from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
from urllib.parse import quote, urlencode

import requests

from Apollo.apollo_tools import aegis_vm_client


EBAY_PRODUCTION_API = "https://api.ebay.com"
EBAY_SANDBOX_API = "https://api.sandbox.ebay.com"
EBAY_PRODUCTION_AUTH = "https://auth.ebay.com/oauth2/authorize"
EBAY_SANDBOX_AUTH = "https://auth.sandbox.ebay.com/oauth2/authorize"
PUBLISH_CONFIRM_PHRASE = "I approve Apollo listing this item on eBay"
APOLLO_ROOT = Path(__file__).resolve().parents[1]
EBAY_APPROVAL_DRAFT_DIR = APOLLO_ROOT / "artifacts" / "ebay_listing_drafts"
APOLLO_ENV_PATH = APOLLO_ROOT / ".env"
_LOCAL_EBAY_ENV_LOADED = False

DEFAULT_SELLER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
]


@dataclass(frozen=True)
class EbayConfig:
    api_base_url: str
    marketplace_id: str
    access_token: str
    mutation_enabled: bool
    publish_enabled: bool


_CATEGORY_TREE_BY_MARKETPLACE = {
    "EBAY_US": "0",
    "EBAY_GB": "3",
    "EBAY_AU": "15",
    "EBAY_DE": "77",
    "EBAY_CA": "2",
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ensure_local_ebay_env_loaded() -> None:
    global _LOCAL_EBAY_ENV_LOADED
    if _LOCAL_EBAY_ENV_LOADED:
        return
    _LOCAL_EBAY_ENV_LOADED = True
    if not APOLLO_ENV_PATH.exists():
        return
    try:
        lines = APOLLO_ENV_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    allowed_prefixes = ("APOLLO_EBAY_", "EBAY_")
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith(allowed_prefixes) or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _clean_text(value: Any, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len]


_REQUEST_TITLE_RE = re.compile(
    r"\b(?:can you|could you|would you|please|list|draft|push|pipeline|ebay|add this|item on ebay)\b",
    flags=re.I,
)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item, 500) for item in value if _clean_text(item, 500)]
    if isinstance(value, str) and value.strip():
        return [_clean_text(value, 500)]
    return []


def _as_price_float(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        parsed = float(text)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _format_price_value(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


def _infer_price(payload: Dict[str, Any]) -> Dict[str, Any]:
    explicit_price = _as_price_float(payload.get("price") if not isinstance(payload.get("price"), dict) else payload.get("price", {}).get("value"))
    currency = _clean_text((payload.get("price") if isinstance(payload.get("price"), dict) else {}).get("currency") if isinstance(payload.get("price"), dict) else payload.get("currency") or "USD", 8).upper() or "USD"
    if explicit_price is not None:
        return {"value": _format_price_value(explicit_price), "currency": currency, "source": "explicit", "inferred": False}

    title = _clean_text(payload.get("title"), 160).lower()
    description = _clean_text(payload.get("description"), 800).lower()
    text = f"{title} {description}".strip()
    has_image_context = bool(
        _as_list(payload.get("image_urls") or payload.get("images"))
        or _as_list(payload.get("source_attachment_urls") or payload.get("source_image_urls"))
        or _as_list(payload.get("source_image_paths") or payload.get("source_images"))
    )
    if re.search(r"\b(song|playlist|music|audio|track|album|youtube)\b", text) and not has_image_context:
        return {"value": "", "currency": currency, "source": "not_inferred_non_listing_media", "inferred": False}

    comps = _comparable_prices(payload)
    sold_prices = [item["price"] for item in comps if item.get("sold")]
    all_prices = [item["price"] for item in comps]
    reference = sold_prices or all_prices
    if reference:
        avg_price = round(sum(reference) / len(reference), 2)
        return {
            "value": _format_price_value(avg_price),
            "currency": currency,
            "source": "sold_comparable_average" if sold_prices else "comparable_average",
            "inferred": True,
        }

    heuristics: list[tuple[re.Pattern[str], float, str]] = [
        (re.compile(r"\b(mug|cup|ceramic|drinkware|glass|tumblr|tumbler)\b", re.I), 12.99, "heuristic_drinkware"),
        (re.compile(r"\b(camera|garmin|watch|gps|electronics|device|tablet|phone)\b", re.I), 39.99, "heuristic_electronics"),
        (re.compile(r"\b(shirt|jacket|jeans|pants|hat|hoodie|clothing|apparel)\b", re.I), 18.99, "heuristic_clothing"),
        (re.compile(r"\b(book|manual|magazine|novel|textbook)\b", re.I), 9.99, "heuristic_books"),
        (re.compile(r"\b(tool|hardware|drill|wrench|socket|saw)\b", re.I), 24.99, "heuristic_tools"),
        (re.compile(r"\b(vintage|antique|collectible|collectable)\b", re.I), 29.99, "heuristic_collectible"),
    ]
    for pattern, guess, source in heuristics:
        if pattern.search(text):
            return {"value": _format_price_value(guess), "currency": currency, "source": source, "inferred": True}
    if title or description or has_image_context:
        return {"value": "19.99", "currency": currency, "source": "heuristic_default", "inferred": True}
    return {"value": "", "currency": currency, "source": "not_inferred_missing_context", "inferred": False}


def _comparable_prices(payload: Dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("comparable_prices") or payload.get("comps") or []
    if not isinstance(raw, list):
        return []
    comps: list[dict[str, Any]] = []
    for item in raw[:20]:
        if isinstance(item, dict):
            price = _as_price_float(item.get("price") or item.get("value"))
            if price is None:
                continue
            comps.append(
                {
                    "price": round(price, 2),
                    "currency": _clean_text(item.get("currency") or payload.get("currency") or "USD", 8).upper(),
                    "source": _clean_text(item.get("source") or item.get("title") or "operator-provided comparable", 180),
                    "url": _clean_text(item.get("url") or "", 500),
                    "sold": bool(item.get("sold") or item.get("completed")),
                }
            )
        else:
            price = _as_price_float(item)
            if price is not None:
                comps.append({"price": round(price, 2), "currency": _clean_text(payload.get("currency") or "USD", 8).upper(), "source": "operator-provided comparable", "url": "", "sold": False})
    return comps


def _explicit_false(value: Any) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off", "disabled"}


def _online_comps_enabled(payload: Dict[str, Any]) -> bool:
    if _explicit_false(payload.get("online_comps")) or _explicit_false(payload.get("enable_online_comps")):
        return False
    if _truthy(payload.get("online_comps")) or _truthy(payload.get("enable_online_comps")):
        return True
    return _truthy(os.getenv("APOLLO_EBAY_ENABLE_ONLINE_COMPS"))


def _comparable_query(payload: Dict[str, Any]) -> str:
    explicit = _clean_text(payload.get("comparable_query") or payload.get("comp_query") or payload.get("query") or payload.get("keywords"), 160)
    if explicit:
        return explicit
    title = _clean_text(payload.get("title"), 120)
    description = _clean_text(payload.get("description"), 500)
    if title and not _REQUEST_TITLE_RE.search(title):
        return title
    candidates: list[str] = []
    for text in (title, description):
        for match in re.finditer(r"\b[A-Z][A-Za-z0-9][A-Za-z0-9 &+./'-]{2,60}\b", text):
            phrase = _clean_text(match.group(0), 80)
            if not phrase or re.search(r"\b(?:Draft|Operator|Sky|Apollo|Generated|Request)\b", phrase):
                continue
            candidates.append(phrase)
    return (candidates[0] if candidates else title)[:120]


def _normalized_title(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(text, 160).lower()).strip()


def _looks_like_request_title(title: Any) -> bool:
    return bool(_REQUEST_TITLE_RE.search(_clean_text(title, 160)))


def _title_specificity_score(title: Any) -> int:
    clean = _clean_text(title, 160)
    if not clean:
        return -100
    if _looks_like_request_title(clean):
        return -50
    tokens = re.findall(r"[A-Za-z0-9]+", clean)
    if not tokens:
        return -100
    score = min(len(tokens), 8)
    score += min(sum(1 for token in tokens if len(token) >= 5), 4)
    if re.search(r"\b[A-Za-z]*\d+[A-Za-z0-9-]*\b", clean):
        score += 2
    if 12 <= len(clean) <= 80:
        score += 1
    if re.search(r"\b(?:black|white|silver|gray|grey|red|blue|green|wood|metal|ceramic|plastic|curved|wireless|charging|stand|dock|holder)\b", clean, flags=re.I):
        score += 1
    if re.search(r"\b(?:item|product|thing|photo|image|picture|attachment|screenshot)\b", clean, flags=re.I):
        score -= 2
    return score


def _should_replace_draft_title(existing_title: Any, incoming_title: Any) -> bool:
    existing = _clean_text(existing_title, 160)
    incoming = _clean_text(incoming_title, 160)
    if not incoming:
        return False
    if _normalized_title(existing) == _normalized_title(incoming):
        return False
    if _looks_like_request_title(incoming):
        return False
    if not existing:
        return True
    if _looks_like_request_title(existing):
        return True
    existing_tokens = set(_normalized_title(existing).split())
    incoming_tokens = set(_normalized_title(incoming).split())
    if existing_tokens and incoming_tokens.issuperset(existing_tokens) and len(incoming_tokens) > len(existing_tokens):
        return True
    existing_score = _title_specificity_score(existing)
    incoming_score = _title_specificity_score(incoming)
    return incoming_score >= existing_score + 2


def _price_from_ebay_amount(value: Any) -> tuple[float | None, str]:
    if isinstance(value, dict):
        return _as_price_float(value.get("value")), _clean_text(value.get("currency") or "USD", 8).upper() or "USD"
    return _as_price_float(value), "USD"


def _comps_from_ebay_items(items: Any, *, sold: bool) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    comps: list[dict[str, Any]] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        price, currency = _price_from_ebay_amount(item.get("price") or item.get("currentBidPrice"))
        if price is None:
            continue
        source = _clean_text(item.get("title") or item.get("itemId") or "eBay comparable", 180)
        url = _clean_text(item.get("itemWebUrl") or item.get("itemHref") or "", 500)
        comps.append({"price": round(price, 2), "currency": currency, "source": source, "url": url, "sold": sold})
    return comps


def online_comparable_prices(payload: Dict[str, Any], cfg: EbayConfig | None = None) -> Dict[str, Any]:
    cfg = cfg or _config(payload)
    query = _comparable_query(payload)
    if not query:
        return {"ok": False, "action": "online_comparable_prices", "error": "comparable_query_required", "comparables": [], "warnings": ["No usable comparable search query could be built."]}
    if not cfg.access_token:
        return {
            "ok": False,
            "action": "online_comparable_prices",
            "query": query,
            "error": "ebay_access_token_required",
            "comparables": [],
            "warnings": ["Online comparable lookup skipped because Apollo has no eBay access token."],
        }

    limit = max(3, min(int(payload.get("comparable_limit") or 8), 20))
    params = urlencode({"q": query, "limit": str(limit), "filter": "buyingOptions:{FIXED_PRICE}"})
    active = _ebay_request("GET", f"/buy/browse/v1/item_summary/search?{params}", None, cfg, timeout_s=float(payload.get("comparable_timeout_s") or 12.0))
    active_response = active.get("response") if isinstance(active.get("response"), dict) else {}
    comparables = _comps_from_ebay_items(active_response.get("itemSummaries"), sold=False)
    warnings: list[str] = []
    if not active.get("ok"):
        warnings.append("Active eBay comparable lookup failed; review price manually.")
    if not comparables:
        warnings.append("No active eBay comparable prices were found; Apollo will fall back to heuristic pricing.")

    sold_lookup: Dict[str, Any] = {"ok": False, "skipped": True, "reason": "restricted_endpoint_not_enabled"}
    if _truthy(payload.get("include_sold_comps")) or _truthy(os.getenv("APOLLO_EBAY_ENABLE_SOLD_COMPS")):
        sold_params = urlencode({"q": query, "limit": str(limit)})
        sold_lookup = _ebay_request("GET", f"/buy/marketplace_insights/v1_beta/item_sales/search?{sold_params}", None, cfg, timeout_s=float(payload.get("comparable_timeout_s") or 12.0))
        sold_response = sold_lookup.get("response") if isinstance(sold_lookup.get("response"), dict) else {}
        sold_comps = _comps_from_ebay_items(sold_response.get("itemSales"), sold=True)
        if sold_comps:
            comparables = sold_comps + comparables
        elif not sold_lookup.get("ok"):
            warnings.append("Sold eBay comparable lookup was unavailable or rejected; using active listings only.")

    return {
        "ok": bool(comparables),
        "action": "online_comparable_prices",
        "query": query,
        "provider": "ebay_browse_api",
        "comparables": comparables[:20],
        "warnings": warnings,
        "active_lookup": active,
        "sold_lookup": sold_lookup,
    }


def _with_online_comparable_prices(payload: Dict[str, Any], cfg: EbayConfig) -> Dict[str, Any]:
    enriched = dict(payload)
    if _comparable_prices(enriched) or not _online_comps_enabled(enriched):
        return enriched
    lookup = online_comparable_prices(enriched, cfg)
    enriched["_online_comparable_lookup"] = lookup
    comps = lookup.get("comparables") if isinstance(lookup.get("comparables"), list) else []
    if comps:
        enriched["comparable_prices"] = comps
    return enriched


def build_pricing_review(payload: Dict[str, Any]) -> Dict[str, Any]:
    asking = _as_price_float(payload.get("price") if not isinstance(payload.get("price"), dict) else payload.get("price", {}).get("value"))
    currency = _clean_text((payload.get("price") if isinstance(payload.get("price"), dict) else {}).get("currency") if isinstance(payload.get("price"), dict) else payload.get("currency") or "USD", 8).upper() or "USD"
    inferred_source = ""
    if asking is None:
        inferred = _infer_price(payload)
        asking = _as_price_float(inferred.get("value"))
        currency = str(inferred.get("currency") or currency).upper()
        inferred_source = str(inferred.get("source") or "")
    comps = _comparable_prices(payload)
    sold_prices = [item["price"] for item in comps if item.get("sold")]
    all_prices = [item["price"] for item in comps]
    reference = sold_prices or all_prices
    suggested = asking
    method = inferred_source or "operator_price"
    warnings: list[str] = []
    if reference:
        avg_price = round(sum(reference) / len(reference), 2)
        low_price = round(min(reference), 2)
        high_price = round(max(reference), 2)
        suggested = asking if asking is not None else avg_price
        method = "sold_comparable_average" if sold_prices else "comparable_average"
    else:
        avg_price = None
        low_price = None
        high_price = None
        warnings.append("No comparable sale data was provided; review price manually before publishing.")
    if asking is None:
        warnings.append("No asking price is set; the draft cannot be ready for review.")
    elif reference:
        if asking > high_price * 1.35:
            warnings.append("Asking price is materially above provided comparable range.")
        if asking < low_price * 0.65:
            warnings.append("Asking price is materially below provided comparable range.")
    return {
        "asking_price": asking,
        "suggested_price": suggested,
        "currency": currency,
        "method": method,
        "comparable_count": len(comps),
        "sold_comparable_count": len(sold_prices),
        "comparable_low": low_price,
        "comparable_high": high_price,
        "comparable_average": avg_price,
        "comparables": comps,
        "warnings": warnings,
        "review_required": True,
    }


def build_image_review(payload: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    product = draft.get("inventory_item", {}).get("product", {}) if isinstance(draft.get("inventory_item"), dict) else {}
    listing_urls = _as_list(product.get("imageUrls"))
    source_paths = _as_list(payload.get("source_image_paths") or payload.get("source_images"))
    source_urls = _as_list(payload.get("source_attachment_urls") or payload.get("source_image_urls"))
    warnings: list[str] = []
    if not listing_urls:
        warnings.append("No listing image URLs are present; add hosted images before publish.")
    if not source_paths and not source_urls:
        warnings.append("No source image evidence is attached to the review packet.")
    return {
        "listing_image_urls": listing_urls,
        "source_image_paths": source_paths,
        "source_attachment_urls": source_urls,
        "listing_image_count": len(listing_urls),
        "source_image_count": len(source_paths) + len(source_urls),
        "ready_for_ui_fill": bool(listing_urls),
        "warnings": warnings,
    }


def _config(payload: Dict[str, Any] | None = None) -> EbayConfig:
    _ensure_local_ebay_env_loaded()
    payload = dict(payload or {})
    env = str(payload.get("environment") or os.getenv("APOLLO_EBAY_ENV") or "sandbox").strip().lower()
    default_base = EBAY_PRODUCTION_API if env in {"prod", "production", "live"} else EBAY_SANDBOX_API
    api_base = str(payload.get("api_base_url") or os.getenv("APOLLO_EBAY_API_BASE_URL") or default_base).strip().rstrip("/")
    token = str(
        payload.get("access_token")
        or os.getenv("APOLLO_EBAY_ACCESS_TOKEN")
        or os.getenv("EBAY_ACCESS_TOKEN")
        or ""
    ).strip()
    marketplace = str(payload.get("marketplace_id") or os.getenv("APOLLO_EBAY_MARKETPLACE_ID") or "EBAY_US").strip()
    return EbayConfig(
        api_base_url=api_base,
        marketplace_id=marketplace,
        access_token=token,
        mutation_enabled=_truthy(payload.get("mutation_enabled")) or _truthy(os.getenv("APOLLO_EBAY_ENABLE_MUTATIONS")),
        publish_enabled=_truthy(payload.get("publish_enabled")) or _truthy(os.getenv("APOLLO_EBAY_ENABLE_PUBLISH")),
    )


def _env_or_payload(payload: Dict[str, Any], *keys: str) -> str:
    _ensure_local_ebay_env_loaded()
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
        value = os.getenv(key)
        if value:
            return str(value).strip()
    return ""


def _oauth_client_id(payload: Dict[str, Any]) -> str:
    return _env_or_payload(payload, "client_id", "APOLLO_EBAY_CLIENT_ID", "APOLLO_EBAY_APP_ID", "EBAY_CLIENT_ID")


def _oauth_client_secret(payload: Dict[str, Any]) -> str:
    return _env_or_payload(payload, "client_secret", "APOLLO_EBAY_CLIENT_SECRET", "APOLLO_EBAY_CERT_ID", "EBAY_CLIENT_SECRET")


def _oauth_redirect_uri(payload: Dict[str, Any]) -> str:
    return _env_or_payload(payload, "redirect_uri", "runame", "APOLLO_EBAY_RUNAME", "APOLLO_EBAY_REDIRECT_URI", "EBAY_RUNAME")


def _oauth_refresh_token(payload: Dict[str, Any]) -> str:
    return _env_or_payload(payload, "refresh_token", "APOLLO_EBAY_REFRESH_TOKEN", "EBAY_REFRESH_TOKEN")


def _oauth_scopes(payload: Dict[str, Any]) -> list[str]:
    raw = payload.get("scopes") or payload.get("scope") or os.getenv("APOLLO_EBAY_SCOPES") or ""
    if isinstance(raw, list):
        scopes = [_clean_text(item, 200) for item in raw if _clean_text(item, 200)]
    else:
        scopes = [part.strip() for part in re.split(r"[\s,]+", str(raw or "")) if part.strip()]
    return scopes or list(DEFAULT_SELLER_SCOPES)


def _oauth_auth_url(cfg: EbayConfig) -> str:
    return EBAY_PRODUCTION_AUTH if cfg.api_base_url == EBAY_PRODUCTION_API else EBAY_SANDBOX_AUTH


def _oauth_basic_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _env_file_path(payload: Dict[str, Any]) -> Path:
    raw = str(payload.get("env_file") or os.getenv("APOLLO_EBAY_ENV_FILE") or APOLLO_ENV_PATH).strip()
    return Path(raw)


def _write_env_values(path: Path, values: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        match = re.match(r"^(\s*[A-Za-z_][A-Za-z0-9_]*\s*)=(.*)$", line)
        if not match:
            output.append(line)
            continue
        key = match.group(1).strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _sku(payload: Dict[str, Any]) -> str:
    raw = str(payload.get("sku") or payload.get("custom_sku") or "").strip()
    if not raw:
        title = _clean_text(payload.get("title"), 60).lower()
        raw = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
    raw = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip(".:-_")
    return raw[:50]


def build_inventory_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    title = _clean_text(payload.get("title"), 80)
    description = str(payload.get("description") or "").strip()
    aspects = payload.get("aspects") if isinstance(payload.get("aspects"), dict) else {}
    brand = _clean_text(payload.get("brand") or aspects.get("Brand"), 65)
    mpn = _clean_text(payload.get("mpn") or aspects.get("MPN"), 65)
    image_urls = _as_list(payload.get("image_urls") or payload.get("images"))
    condition = str(payload.get("condition") or "USED_EXCELLENT").strip().upper()
    quantity = int(payload.get("quantity") or payload.get("available_quantity") or 1)
    inventory_item: Dict[str, Any] = {
        "availability": {
            "shipToLocationAvailability": {
                "quantity": max(0, quantity),
            }
        },
        "condition": condition,
        "product": {
            "title": title,
            "description": description,
        },
    }
    if image_urls:
        inventory_item["product"]["imageUrls"] = image_urls
    if brand or mpn:
        inventory_item["product"]["aspects"] = {}
        if brand:
            inventory_item["product"]["aspects"]["Brand"] = [brand]
        if mpn:
            inventory_item["product"]["aspects"]["MPN"] = [mpn]
    if aspects:
        product_aspects = inventory_item["product"].setdefault("aspects", {})
        for key, value in aspects.items():
            clean_key = _clean_text(key, 65)
            if not clean_key:
                continue
            values = _as_list(value)
            if values:
                product_aspects[clean_key] = values
    return inventory_item


def build_offer(payload: Dict[str, Any], cfg: EbayConfig | None = None) -> Dict[str, Any]:
    cfg = cfg or _config(payload)
    inferred_price = _infer_price(payload)
    value = str(inferred_price.get("value") or "").strip()
    currency = str(inferred_price.get("currency") or payload.get("currency") or "USD").strip().upper()
    offer: Dict[str, Any] = {
        "sku": _sku(payload),
        "marketplaceId": str(payload.get("marketplace_id") or cfg.marketplace_id),
        "format": "FIXED_PRICE",
        "availableQuantity": int(payload.get("quantity") or payload.get("available_quantity") or 1),
        "categoryId": str(payload.get("category_id") or "").strip(),
        "listingDescription": str(payload.get("listing_description") or payload.get("description") or "").strip(),
        "pricingSummary": {
            "price": {
                "value": value,
                "currency": currency,
            }
        },
        "merchantLocationKey": str(payload.get("merchant_location_key") or os.getenv("APOLLO_EBAY_MERCHANT_LOCATION_KEY") or "").strip(),
        "listingPolicies": {
            "fulfillmentPolicyId": str(payload.get("fulfillment_policy_id") or os.getenv("APOLLO_EBAY_FULFILLMENT_POLICY_ID") or "").strip(),
            "paymentPolicyId": str(payload.get("payment_policy_id") or os.getenv("APOLLO_EBAY_PAYMENT_POLICY_ID") or "").strip(),
            "returnPolicyId": str(payload.get("return_policy_id") or os.getenv("APOLLO_EBAY_RETURN_POLICY_ID") or "").strip(),
        },
    }
    if value:
        offer["pricingSummary"]["price"]["value"] = value
    offer["pricingSummary"]["price"]["currency"] = currency
    offer["pricingSummary"]["priceSource"] = str(inferred_price.get("source") or "explicit")
    offer["pricingSummary"]["priceInferred"] = bool(inferred_price.get("inferred"))
    if payload.get("listing_duration"):
        offer["listingDuration"] = str(payload.get("listing_duration")).strip()
    return offer


def _estimated_package(payload: Dict[str, Any]) -> Dict[str, Any]:
    package = payload.get("package") if isinstance(payload.get("package"), dict) else {}
    title = _clean_text(payload.get("title"), 120).lower()
    description = _clean_text(payload.get("description"), 500).lower()
    text = f"{title} {description}"
    if re.search(r"\b(mug|cup|ceramic|glass|dish|bowl|plate)\b", text):
        default = {"length": 10, "width": 8, "height": 6, "unit": "INCH", "weight_value": 3, "weight_unit": "POUND"}
    elif re.search(r"\b(shirt|pants|jacket|clothes|clothing|fabric|hat)\b", text):
        default = {"length": 12, "width": 10, "height": 3, "unit": "INCH", "weight_value": 1, "weight_unit": "POUND"}
    elif re.search(r"\b(book|manual|paper|document|magazine)\b", text):
        default = {"length": 12, "width": 9, "height": 2, "unit": "INCH", "weight_value": 2, "weight_unit": "POUND"}
    else:
        default = {"length": 12, "width": 9, "height": 6, "unit": "INCH", "weight_value": 2, "weight_unit": "POUND"}
    merged = {**default, **package}
    return {
        "length": merged["length"],
        "width": merged["width"],
        "height": merged["height"],
        "unit": str(merged.get("unit") or "INCH").upper(),
        "weight_value": merged["weight_value"],
        "weight_unit": str(merged.get("weight_unit") or "POUND").upper(),
        "estimated": True,
        "estimate_basis": "Apollo default estimate from item title/description; review before live listing.",
    }


def build_shipping_defaults(payload: Dict[str, Any]) -> Dict[str, Any]:
    service = _clean_text(payload.get("shipping_service") or os.getenv("APOLLO_EBAY_DEFAULT_SHIPPING_SERVICE") or "USPS Ground Advantage", 80)
    payer = _clean_text(payload.get("shipping_payer") or os.getenv("APOLLO_EBAY_DEFAULT_SHIPPING_PAYER") or "buyer", 20).lower()
    shipping_type = _clean_text(payload.get("shipping_type") or os.getenv("APOLLO_EBAY_DEFAULT_SHIPPING_TYPE") or "calculated", 40).lower()
    return {
        "payer": "buyer" if payer not in {"seller", "free"} else "seller",
        "service": service or "USPS Ground Advantage",
        "type": "calculated" if shipping_type not in {"flat", "free"} else shipping_type,
        "package": _estimated_package(payload),
        "review_required": True,
        "note": "Default listing posture: buyer pays shipping, USPS Ground Advantage, package size estimated by Apollo.",
    }


def _policy_option(label: str, policy_id: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "label": _clean_text(label, 160),
        "id": _clean_text(policy_id, 80),
        "details": details or {},
    }


def _fulfillment_policy_summary(policy: Dict[str, Any]) -> Dict[str, Any]:
    options = policy.get("shippingOptions") if isinstance(policy.get("shippingOptions"), list) else []
    services: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        for service in option.get("shippingServices") or []:
            if not isinstance(service, dict):
                continue
            cost = service.get("shippingCost") if isinstance(service.get("shippingCost"), dict) else {}
            services.append(
                {
                    "carrier": service.get("shippingCarrierCode") or "",
                    "service_code": service.get("shippingServiceCode") or "",
                    "cost": cost,
                    "free_shipping": bool(service.get("freeShipping")),
                    "option_type": option.get("optionType") or "",
                    "cost_type": option.get("costType") or "",
                }
            )
    first = services[0] if services else {}
    cost = first.get("cost") if isinstance(first.get("cost"), dict) else {}
    cost_value = str(cost.get("value") or "").strip()
    cost_currency = str(cost.get("currency") or "").strip()
    cost_text = "free" if first.get("free_shipping") else (f"{cost_value} {cost_currency}".strip() if cost_value else "")
    parts = [
        str(first.get("carrier") or "").strip(),
        str(first.get("service_code") or "").strip(),
        str(first.get("cost_type") or "").replace("_", " ").title().strip(),
        cost_text,
    ]
    summary = " - ".join(part for part in parts if part)
    return {
        "name": policy.get("name") or "",
        "id": policy.get("fulfillmentPolicyId") or "",
        "summary": summary or str(policy.get("description") or ""),
        "services": services,
    }


def _seller_policy_options(prereqs: Dict[str, Any]) -> Dict[str, Any]:
    checks = prereqs.get("checks") if isinstance(prereqs.get("checks"), dict) else {}

    def _response_list(check_name: str, list_key: str) -> list[dict[str, Any]]:
        check = checks.get(check_name) if isinstance(checks.get(check_name), dict) else {}
        response = check.get("response") if isinstance(check.get("response"), dict) else {}
        values = response.get(list_key) if isinstance(response.get(list_key), list) else []
        return [item for item in values if isinstance(item, dict)]

    fulfillment = []
    for policy in _response_list("fulfillment_policies", "fulfillmentPolicies"):
        summary = _fulfillment_policy_summary(policy)
        fulfillment.append(_policy_option(str(policy.get("name") or summary.get("id") or "Fulfillment policy"), str(summary.get("id") or ""), summary))

    payment = [
        _policy_option(str(policy.get("name") or policy.get("paymentPolicyId") or "Payment policy"), str(policy.get("paymentPolicyId") or ""), {"description": policy.get("description") or ""})
        for policy in _response_list("payment_policies", "paymentPolicies")
    ]
    returns = [
        _policy_option(str(policy.get("name") or policy.get("returnPolicyId") or "Return policy"), str(policy.get("returnPolicyId") or ""), {"description": policy.get("description") or ""})
        for policy in _response_list("return_policies", "returnPolicies")
    ]
    locations = [
        _policy_option(str(item.get("name") or item.get("merchantLocationKey") or "Inventory location"), str(item.get("merchantLocationKey") or ""), {"status": item.get("merchantLocationStatus") or ""})
        for item in _response_list("inventory_locations", "locations")
    ]
    return {
        "fulfillment": [item for item in fulfillment if item.get("id")],
        "payment": [item for item in payment if item.get("id")],
        "returns": [item for item in returns if item.get("id")],
        "locations": [item for item in locations if item.get("id")],
    }


def _selected_option(options: list[dict[str, Any]], selected_id: str) -> Dict[str, Any]:
    selected = str(selected_id or "").strip()
    for option in options:
        if str(option.get("id") or "") == selected:
            return option
    return {}


def _service_tokens(value: str) -> set[str]:
    tokens = {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(token) >= 3}
    aliases = {
        "ground": {"ground", "advantage"},
        "advantage": {"ground", "advantage"},
        "priority": {"priority"},
        "flatrate": {"flat", "rate"},
        "flat": {"flat", "rate"},
    }
    expanded = set(tokens)
    for token in list(tokens):
        expanded.update(aliases.get(token, set()))
    return expanded


def build_publish_readiness(payload: Dict[str, Any]) -> Dict[str, Any]:
    record = payload.get("record") if isinstance(payload.get("record"), dict) else payload
    draft = record.get("draft") if isinstance(record.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    offer = draft.get("offer") if isinstance(draft.get("offer"), dict) else {}
    policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
    shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    if not shipping:
        shipping = build_shipping_defaults(
            {
                "title": product.get("title") or "",
                "description": product.get("description") or offer.get("listingDescription") or "",
            }
        )
    prereqs = seller_prerequisites(payload)
    warnings: list[str] = []
    blockers: list[str] = []
    policy_options = _seller_policy_options(prereqs) if prereqs.get("ok") else {"fulfillment": [], "payment": [], "returns": [], "locations": []}

    if not prereqs.get("ok"):
        blockers.append("Apollo could not read the eBay account policies yet. Check the eBay token/login before publishing.")

    selected_fulfillment = _selected_option(policy_options["fulfillment"], str(policies.get("fulfillmentPolicyId") or ""))
    selected_payment = _selected_option(policy_options["payment"], str(policies.get("paymentPolicyId") or ""))
    selected_return = _selected_option(policy_options["returns"], str(policies.get("returnPolicyId") or ""))
    selected_location = _selected_option(policy_options["locations"], str(offer.get("merchantLocationKey") or ""))

    for label, selected, key in (
        ("fulfillment policy", selected_fulfillment, "fulfillmentPolicyId"),
        ("payment policy", selected_payment, "paymentPolicyId"),
        ("return policy", selected_return, "returnPolicyId"),
    ):
        if policies.get(key) and not selected and prereqs.get("ok"):
            blockers.append(f"The selected {label} ID is not in the current eBay account policy list.")

    if offer.get("merchantLocationKey") and not selected_location and prereqs.get("ok"):
        blockers.append("The selected inventory location is not in the current eBay account location list.")

    desired_service = str(shipping.get("service") or "").strip()
    desired_type = str(shipping.get("type") or "").strip().lower()
    desired_payer = str(shipping.get("payer") or "").strip().lower()
    fulfillment_details = selected_fulfillment.get("details") if isinstance(selected_fulfillment.get("details"), dict) else {}
    services = fulfillment_details.get("services") if isinstance(fulfillment_details.get("services"), list) else []
    first_service = services[0] if services and isinstance(services[0], dict) else {}
    actual_service = str(first_service.get("service_code") or fulfillment_details.get("summary") or "").strip()
    actual_cost_type = str(first_service.get("cost_type") or "").strip().lower()
    actual_free = bool(first_service.get("free_shipping"))
    cost = first_service.get("cost") if isinstance(first_service.get("cost"), dict) else {}
    has_shipping_cost = bool(str(cost.get("value") or "").strip()) and not actual_free
    if desired_service and actual_service:
        desired_tokens = _service_tokens(desired_service)
        actual_tokens = _service_tokens(actual_service)
        if desired_tokens and actual_tokens and not desired_tokens.intersection(actual_tokens):
            warnings.append(f"Draft shipping says {desired_service}, but the selected eBay fulfillment policy uses {actual_service}.")
    if desired_type == "calculated" and actual_cost_type == "flat_rate":
        warnings.append("Draft shipping says calculated, but the selected eBay fulfillment policy is flat-rate.")
    if desired_type == "flat" and actual_cost_type and actual_cost_type != "flat_rate":
        warnings.append(f"Draft shipping says flat, but the selected eBay fulfillment policy is {actual_cost_type.replace('_', ' ')}.")
    if desired_payer == "buyer" and actual_free:
        warnings.append("Draft shipping says buyer pays, but the selected eBay fulfillment policy appears to offer free shipping.")
    if desired_payer == "seller" and has_shipping_cost:
        warnings.append("Draft shipping says seller pays, but the selected eBay fulfillment policy has a buyer-visible shipping cost.")

    if not draft.get("ready_to_publish"):
        blockers.append("The draft still has missing required listing fields.")

    return {
        "ok": not blockers,
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "warnings": list(dict.fromkeys(warnings)),
        "blockers": list(dict.fromkeys(blockers)),
        "policy_options": policy_options,
        "selected": {
            "fulfillment": selected_fulfillment,
            "payment": selected_payment,
            "return": selected_return,
            "location": selected_location,
        },
        "prerequisites_ok": bool(prereqs.get("ok")),
        "prerequisites": prereqs,
    }


def _required_missing(payload: Dict[str, Any]) -> list[str]:
    inventory = build_inventory_item(payload)
    offer = build_offer(payload)
    required = {
        "sku": _sku(payload),
        "title": inventory.get("product", {}).get("title"),
        "description": inventory.get("product", {}).get("description"),
        "condition": inventory.get("condition"),
        "quantity": inventory.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity"),
        "category_id": offer.get("categoryId"),
        "price": offer.get("pricingSummary", {}).get("price", {}).get("value"),
        "merchant_location_key": offer.get("merchantLocationKey"),
        "fulfillment_policy_id": offer.get("listingPolicies", {}).get("fulfillmentPolicyId"),
        "payment_policy_id": offer.get("listingPolicies", {}).get("paymentPolicyId"),
        "return_policy_id": offer.get("listingPolicies", {}).get("returnPolicyId"),
    }
    missing = [name for name, value in required.items() if value in (None, "", [])]
    if not _as_list(payload.get("image_urls") or payload.get("images")):
        missing.append("image_urls")
    return missing


def build_listing_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    payload = _with_online_comparable_prices(payload, cfg)
    payload = _with_autofilled_category(payload, cfg)
    sku = _sku(payload)
    inventory = build_inventory_item({**payload, "sku": sku})
    offer = build_offer({**payload, "sku": sku}, cfg)
    missing = _required_missing({**payload, "sku": sku})
    pricing_review = build_pricing_review(payload)
    return {
        "ok": not bool(missing),
        "action": "draft",
        "sku": sku,
        "environment": "production" if cfg.api_base_url == EBAY_PRODUCTION_API else "sandbox",
        "api_base_url": cfg.api_base_url,
        "marketplace_id": cfg.marketplace_id,
        "ready_to_create_offer": not bool([name for name in missing if name not in {"image_urls"}]),
        "ready_to_publish": not bool(missing),
        "missing": missing,
        "category_lookup": payload.get("_category_lookup") or {},
        "online_comparable_lookup": payload.get("_online_comparable_lookup") or {},
        "pricing_review": pricing_review,
        "pricing_decision": {
            "price_source": str(offer.get("pricingSummary", {}).get("priceSource") or ""),
            "price_inferred": bool(offer.get("pricingSummary", {}).get("priceInferred")),
            "comparable_count": int(pricing_review.get("comparable_count") or 0),
            "sold_comparable_count": int(pricing_review.get("sold_comparable_count") or 0),
            "pricing_method": str(pricing_review.get("method") or ""),
        },
        "inventory_item": inventory,
        "offer": offer,
        "shipping": build_shipping_defaults(payload),
        "publish_requires": {
            "mutation_enabled": "APOLLO_EBAY_ENABLE_MUTATIONS=1",
            "publish_enabled": "APOLLO_EBAY_ENABLE_PUBLISH=1",
            "confirm_phrase": PUBLISH_CONFIRM_PHRASE,
        },
    }


def _with_autofilled_category(payload: Dict[str, Any], cfg: EbayConfig) -> Dict[str, Any]:
    if str(payload.get("category_id") or "").strip() or not cfg.access_token:
        return dict(payload)
    query = _clean_text(payload.get("query") or payload.get("keywords") or payload.get("title"), 120)
    if not query:
        return dict(payload)
    result = category_suggestions({**payload, "query": query})
    suggestions = result.get("ebay", {}).get("response", {}).get("categorySuggestions") or []
    if not suggestions:
        return {**payload, "_category_lookup": {"ok": bool(result.get("ok")), "query": query, "selected": False}}
    selected = suggestions[0].get("category") if isinstance(suggestions[0], dict) else {}
    category_id = str((selected or {}).get("categoryId") or "").strip()
    if not category_id:
        return {**payload, "_category_lookup": {"ok": bool(result.get("ok")), "query": query, "selected": False}}
    return {
        **payload,
        "category_id": category_id,
        "category_name": (selected or {}).get("categoryName") or payload.get("category_name") or "",
        "_category_lookup": {
            "ok": True,
            "query": query,
            "selected": True,
            "category_id": category_id,
            "category_name": (selected or {}).get("categoryName") or "",
        },
    }


def _safe_draft_id(value: Any, fallback: str = "listing") -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip()).strip(".:-_")
    return (text or fallback)[:90]


def _normalize_draft_title(value: Any) -> str:
    raw = _clean_text(value, 240).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", raw)).strip()


def _is_smoke_draft(draft_id: str, title: str) -> bool:
    candidate = f"{str(draft_id)} {title}".lower()
    test_markers = ("smoke", "demo", "example", "test draft", "test item", "human-review-packet")
    return any(marker in candidate for marker in test_markers)


def _offer_published_url(offer_id: str, item_id: str) -> str:
    offer_id = str(offer_id or "").strip()
    item_id = str(item_id or "").strip()
    if item_id:
        return f"https://www.ebay.com/itm/{quote(item_id)}"
    if offer_id:
        # Offer URLs are session dependent; include offer id for quick navigation.
        return f"https://www.ebay.com/ul/v1/offer?offer_id={quote(offer_id)}"
    return ""


def _approval_markdown(draft_id: str, payload: Dict[str, Any], draft: Dict[str, Any]) -> str:
    missing = draft.get("missing") or []
    source_paths = _as_list(payload.get("source_image_paths") or payload.get("source_images"))
    source_urls = _as_list(payload.get("source_attachment_urls") or payload.get("source_image_urls"))
    status = str(payload.get("status") or "needs_approval")
    published_url = str(payload.get("published_url") or "")
    published_at = str(payload.get("published_at") or "")
    offer_id = str(payload.get("offer_id") or "")
    item_id = str(payload.get("item_id") or "")
    online_link_line = ""
    if status == "published" and published_url:
        online_link_line = f"- Published URL: {published_url}"
    elif status in {"approved", "publish_failed", "published", "needs_posting"}:
        if offer_id:
            online_link_line = f"- Offer ID: {offer_id}"
        if item_id:
            online_link_line = f"{online_link_line}\n- Item ID: {item_id}" if online_link_line else f"- Item ID: {item_id}"
    lines = [
        f"# Apollo eBay Draft Approval: {draft_id}",
        "",
        f"- Status: {status}",
        f"- Published: {published_at}" if published_at else "- Published: not yet",
        f"- SKU: {draft.get('sku') or ''}",
        f"- Environment: {draft.get('environment') or ''}",
        f"- Marketplace: {draft.get('marketplace_id') or ''}",
        f"- Ready to publish: {bool(draft.get('ready_to_publish'))}",
        f"- Missing: {', '.join(missing) if missing else 'none'}",
        "",
        "## Listing",
        "",
        f"Title: {draft.get('inventory_item', {}).get('product', {}).get('title') or ''}",
        "",
        f"Price: {draft.get('offer', {}).get('pricingSummary', {}).get('price', {}).get('value') or ''} {draft.get('offer', {}).get('pricingSummary', {}).get('price', {}).get('currency') or ''}".strip(),
        "",
        "Description:",
        "",
        str(draft.get("inventory_item", {}).get("product", {}).get("description") or "").strip() or "(none)",
        "",
        "## Source Images",
        "",
    ]
    if source_paths or source_urls:
        lines.extend([f"- {item}" for item in source_paths + source_urls])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Approval Gate",
            "",
            "This artifact is a local approval draft only. Live eBay publish still requires Apollo's mutation gate, publish gate, operator confirmation, and exact publish phrase.",
            "",
        ]
    )
    if online_link_line:
        lines.insert(-1, online_link_line)
    return "\n".join(lines)


def _prepared_packet_markdown(packet: Dict[str, Any]) -> str:
    draft = packet.get("draft") if isinstance(packet.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    offer = draft.get("offer") if isinstance(draft.get("offer"), dict) else {}
    pricing = packet.get("pricing_review") if isinstance(packet.get("pricing_review"), dict) else {}
    images = packet.get("image_review") if isinstance(packet.get("image_review"), dict) else {}
    fill_steps = packet.get("forgeclaw_fill_steps") if isinstance(packet.get("forgeclaw_fill_steps"), list) else []
    lines = [
        f"# Apollo eBay Prepared Review Packet: {packet.get('draft_id') or ''}",
        "",
        "Status: draft-ready-for-human-review" if packet.get("ready_for_human_review") else "Status: needs-more-fields",
        "",
        "This packet is for supervised draft preparation. ForgeClaw may open eBay, fill fields, and upload/attach prepared images, but it must stop before any final publish/list/submit confirmation.",
        "",
        "## Listing Summary",
        "",
        f"- Title: {product.get('title') or ''}",
        f"- SKU: {draft.get('sku') or ''}",
        f"- Category ID: {offer.get('categoryId') or ''}",
        f"- Condition: {inventory.get('condition') or ''}",
        f"- Quantity: {inventory.get('availability', {}).get('shipToLocationAvailability', {}).get('quantity') or ''}",
        f"- Price: {pricing.get('asking_price') if pricing.get('asking_price') is not None else ''} {pricing.get('currency') or ''}".strip(),
        f"- Ready to publish after human review: {bool(draft.get('ready_to_publish'))}",
        f"- Missing: {', '.join(draft.get('missing') or []) if draft.get('missing') else 'none'}",
        "",
        "## Pricing Review",
        "",
        f"- Method: {pricing.get('method') or ''}",
        f"- Suggested price: {pricing.get('suggested_price') if pricing.get('suggested_price') is not None else ''} {pricing.get('currency') or ''}".strip(),
        f"- Comparable range: {pricing.get('comparable_low')} - {pricing.get('comparable_high')} ({pricing.get('comparable_count') or 0} comps, {pricing.get('sold_comparable_count') or 0} sold)",
        "",
    ]
    warnings = list(pricing.get("warnings") or []) + list(images.get("warnings") or [])
    if warnings:
        lines.extend(["Warnings:"] + [f"- {warning}" for warning in warnings] + [""])
    comparables = pricing.get("comparables") if isinstance(pricing.get("comparables"), list) else []
    if comparables:
        lines.append("Comparables:")
        for item in comparables:
            sold = "sold" if item.get("sold") else "active/unknown"
            suffix = f" - {item.get('url')}" if item.get("url") else ""
            lines.append(f"- {item.get('price')} {item.get('currency')} ({sold}) {item.get('source') or ''}{suffix}")
        lines.append("")
    lines.extend(
        [
            "## Description",
            "",
            str(product.get("description") or "").strip() or "(none)",
            "",
            "## Image Review",
            "",
            f"- Listing image count: {images.get('listing_image_count') or 0}",
            f"- Source image count: {images.get('source_image_count') or 0}",
        ]
    )
    for url in images.get("listing_image_urls") or []:
        lines.append(f"- Listing image: {url}")
    for path in images.get("source_image_paths") or []:
        lines.append(f"- Source path: {path}")
    for url in images.get("source_attachment_urls") or []:
        lines.append(f"- Source URL: {url}")
    lines.extend(["", "## ForgeClaw Fill Steps", ""])
    for idx, step in enumerate(fill_steps, start=1):
        lines.append(f"{idx}. {step.get('label') or step.get('field') or 'step'}: {step.get('value') or step.get('instruction') or ''}")
    lines.extend(
        [
            "",
            "## Human Review Gate",
            "",
            "- Operator must review title, price, category, condition, description, shipping, returns, photos, and all eBay warnings.",
            "- ForgeClaw must not click final publish/list/submit.",
            "- If eBay shows a fee, policy, restricted-item, account, payment, or identity warning, stop and mark the packet `needs-human-review`.",
        ]
    )
    return "\n".join(lines)


def build_prepared_review_packet(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _with_online_comparable_prices(dict(payload), _config(payload))
    approval = create_approval_draft(payload)
    draft = approval.get("draft") if isinstance(approval.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    offer = draft.get("offer") if isinstance(draft.get("offer"), dict) else {}
    price = offer.get("pricingSummary", {}).get("price", {}) if isinstance(offer.get("pricingSummary"), dict) else {}
    pricing_review = build_pricing_review(payload)
    image_review = build_image_review(payload, draft)
    fill_steps = [
        {"field": "title", "label": "Fill title", "value": product.get("title") or ""},
        {"field": "category_id", "label": "Select category", "value": offer.get("categoryId") or "", "instruction": "Use eBay's visible category picker if the numeric ID cannot be pasted."},
        {"field": "condition", "label": "Set condition", "value": inventory.get("condition") or ""},
        {"field": "description", "label": "Fill description", "value": product.get("description") or ""},
        {"field": "quantity", "label": "Set quantity", "value": str(inventory.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity") or "")},
        {"field": "price", "label": "Set price", "value": f"{price.get('value') or ''} {price.get('currency') or ''}".strip()},
        {"field": "images", "label": "Attach images", "value": f"{image_review.get('listing_image_count') or 0} prepared listing image URL(s)"},
        {"field": "shipping", "label": "Review shipping policy", "value": offer.get("listingPolicies", {}).get("fulfillmentPolicyId") or ""},
        {"field": "returns", "label": "Review return policy", "value": offer.get("listingPolicies", {}).get("returnPolicyId") or ""},
        {"field": "payment", "label": "Review payment policy", "value": offer.get("listingPolicies", {}).get("paymentPolicyId") or ""},
        {"field": "stop_before_publish", "label": "Stop before final publish", "instruction": "Do not click final publish/list/submit. Capture screenshot and wait for human review."},
    ]
    hard_warnings = list(pricing_review.get("warnings") or []) + list(image_review.get("warnings") or [])
    packet = {
        **approval,
        "action": "prepared_review_packet",
        "status": "draft-ready-for-human-review" if draft.get("ready_to_publish") and not hard_warnings else "needs-more-fields",
        "ready_for_human_review": bool(draft.get("ready_to_publish")),
        "writes_performed": False,
        "ui_automation_allowed": True,
        "final_publish_allowed": False,
        "pricing_review": pricing_review,
        "image_review": image_review,
        "forgeclaw_fill_steps": fill_steps,
        "human_review_gate": {
            "required": True,
            "must_stop_before_publish": True,
            "operator_must_review": [
                "title",
                "category",
                "condition",
                "description",
                "images",
                "price",
                "shipping",
                "returns",
                "payment",
                "eBay warnings and fees",
            ],
        },
        "next_steps": [
            "Use ForgeClaw in the VM to open the eBay sell flow and fill the draft fields.",
            "Capture screenshots of the completed draft and any eBay warnings.",
            "Stop before final publish/list/submit and wait for human review.",
        ],
    }
    draft_dir = EBAY_APPROVAL_DRAFT_DIR / str(packet.get("draft_id") or "")
    packet_path = draft_dir / "prepared_review_packet.json"
    packet_md_path = draft_dir / "prepared_review_packet.md"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    packet_md_path.write_text(_prepared_packet_markdown(packet), encoding="utf-8")
    packet["prepared_packet_path"] = str(packet_path)
    packet["prepared_packet_markdown_path"] = str(packet_md_path)
    return add_preview_links(packet)


def create_approval_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft = build_listing_draft(payload)
    base = payload.get("draft_id") or payload.get("source_task_id") or draft.get("sku") or payload.get("title") or "listing"
    draft_id = _safe_draft_id(base)
    if not payload.get("draft_id"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        draft_id = f"{draft_id}-{stamp}-{uuid.uuid4().hex[:6]}"
    draft_id = _safe_draft_id(draft_id)
    draft_dir = EBAY_APPROVAL_DRAFT_DIR / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    artifact = {
        "ok": True,
        "action": "approval_draft",
        "draft_id": draft_id,
        "status": "needs_approval",
        "created_at": now,
        "updated_at": now,
        "writes_performed": False,
        "approval_url": f"/admin/ebay/drafts/{draft_id}",
        "artifact_url": f"/admin/ebay/drafts/{draft_id}/artifact",
        "dashboard_url": "/admin/ebay/drafts",
        "publish_gate_required": {
            "mutation_env": "APOLLO_EBAY_ENABLE_MUTATIONS=1",
            "publish_env": "APOLLO_EBAY_ENABLE_PUBLISH=1",
            "confirm_phrase": PUBLISH_CONFIRM_PHRASE,
        },
        "published_url": "",
        "published_at": "",
        "offer_id": "",
        "item_id": "",
        "status": "needs_approval",
        "draft": draft,
        "source": {
            "agent": payload.get("source_agent") or "",
            "task_id": payload.get("source_task_id") or "",
            "conversation_id": payload.get("source_conversation_id") or "",
            "request_text": payload.get("request_text") or payload.get("source_text") or "",
            "image_paths": _as_list(payload.get("source_image_paths") or payload.get("source_images")),
            "attachment_urls": _as_list(payload.get("source_attachment_urls") or payload.get("source_image_urls")),
        },
        "next_steps": [
            "Review the title, description, condition, price, category, seller policies, and images.",
            "Fill missing draft fields before creating inventory or offers.",
            "Use Apollo's separate eBay mutation and publish gates for any live write.",
        ],
    }
    json_path = draft_dir / "draft.json"
    md_path = draft_dir / "approval.md"
    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_approval_markdown(draft_id, payload, draft), encoding="utf-8")
    artifact["artifact_path"] = str(md_path)
    artifact["json_path"] = str(json_path)
    return add_preview_links(artifact)


def _read_draft_entry(draft_id: str) -> Dict[str, Any]:
    safe_id = _safe_draft_id(draft_id)
    if not safe_id:
        return {"ok": False, "error": "invalid_draft_id"}
    draft_dir = EBAY_APPROVAL_DRAFT_DIR / safe_id
    json_path = draft_dir / "draft.json"
    md_path = draft_dir / "approval.md"
    if not json_path.exists():
        return {"ok": False, "error": "draft_not_found", "draft_id": safe_id}
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "draft_read_failed", "detail": str(exc), "draft_id": safe_id}
    return {
        "ok": True,
        "draft_id": safe_id,
        "payload": payload,
        "json_path": json_path,
        "md_path": md_path,
        "draft_dir": draft_dir,
    }


def _write_draft_entry(draft_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    safe_id = _safe_draft_id(draft_id)
    if not safe_id:
        return {"ok": False, "error": "invalid_draft_id"}
    draft_dir = EBAY_APPROVAL_DRAFT_DIR / safe_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    json_path = draft_dir / "draft.json"
    payload["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if "draft_id" not in payload:
        payload["draft_id"] = safe_id
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"ok": True, "draft_id": safe_id, "json_path": json_path, "draft_dir": draft_dir}


def _merge_unique_text(existing: Any, incoming: Any) -> list[str]:
    merged: list[str] = []
    for value in _as_list(existing) + _as_list(incoming):
        clean = str(value or "").strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


def append_images_to_approval_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    record = read["payload"]
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_draft_record"}

    incoming_urls = _as_list(payload.get("image_urls") or payload.get("source_attachment_urls") or payload.get("source_image_urls"))
    incoming_paths = _as_list(payload.get("source_image_paths") or payload.get("source_images"))
    if not incoming_urls and not incoming_paths:
        return {"ok": False, "error": "no_images_to_append", "draft_id": read["draft_id"]}

    draft = record.get("draft") if isinstance(record.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    source = record.get("source") if isinstance(record.get("source"), dict) else {}

    before_urls = _as_list(product.get("imageUrls"))
    before_paths = _as_list(source.get("image_paths"))
    before_source_urls = _as_list(source.get("attachment_urls"))
    before_title = _clean_text(product.get("title"), 160)
    incoming_title = _clean_text(payload.get("title"), 160)
    product["imageUrls"] = _merge_unique_text(before_urls, incoming_urls)
    source["image_paths"] = _merge_unique_text(before_paths, incoming_paths)
    source["attachment_urls"] = _merge_unique_text(before_source_urls, incoming_urls)
    title_updated = False
    if _should_replace_draft_title(before_title, incoming_title):
        product["title"] = incoming_title
        title_updated = True
    inventory["product"] = product
    draft["inventory_item"] = inventory

    missing = [str(name) for name in _as_list(draft.get("missing")) if str(name) != "image_urls"]
    if not product["imageUrls"]:
        missing.append("image_urls")
    draft["missing"] = list(dict.fromkeys(missing))
    draft["ready_to_publish"] = not bool(draft["missing"])
    draft["ready_to_create_offer"] = not bool([name for name in draft["missing"] if name != "image_urls"])

    record["draft"] = draft
    record["source"] = source
    if not isinstance(record.get("image_updates"), list):
        record["image_updates"] = []
    record["image_updates"].append(
        {
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": _clean_text(payload.get("source_agent") or "Sky", 80),
            "request_text": _clean_text(payload.get("request_text") or payload.get("source_text") or "", 500),
            "added_url_count": max(0, len(product["imageUrls"]) - len(before_urls)),
            "added_path_count": max(0, len(source["image_paths"]) - len(before_paths)),
            "title_updated": title_updated,
            "previous_title": before_title if title_updated else "",
            "new_title": incoming_title if title_updated else "",
        }
    )
    if record.get("status") == "approved":
        record["status"] = "needs_approval"
        record["approval_invalidated_reason"] = "New listing images were attached after approval."

    write = _write_draft_entry(read["draft_id"], record)
    md_path = read.get("md_path")
    if isinstance(md_path, Path):
        try:
            md_path.write_text(_approval_markdown(read["draft_id"], record, draft), encoding="utf-8")
        except Exception:
            pass
    return add_preview_links(
        {
            "ok": bool(write.get("ok")),
            "action": "append_images_to_approval_draft",
            "draft_id": read["draft_id"],
            "status": record.get("status") or "",
            "draft": draft,
            "source": source,
            "title_updated": title_updated,
            "appended": {
                "image_urls": product["imageUrls"],
                "source_image_paths": source["image_paths"],
                "source_attachment_urls": source["attachment_urls"],
            },
            "write": {k: str(v) for k, v in write.items()},
        }
    )


def update_approval_draft_text(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    record = read["payload"]
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_draft_record"}

    draft = record.get("draft") if isinstance(record.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    offer = draft.get("offer") if isinstance(draft.get("offer"), dict) else {}

    editable_fields = {
        "title",
        "description",
        "price",
        "currency",
        "condition",
        "quantity",
        "category_id",
        "shipping_payer",
        "shipping_service",
        "shipping_type",
        "package_length",
        "package_width",
        "package_height",
        "package_unit",
        "package_weight_value",
        "package_weight_unit",
        "fulfillment_policy_id",
        "payment_policy_id",
        "return_policy_id",
        "merchant_location_key",
    }
    present_fields = [name for name in editable_fields if name in payload]
    title_present = "title" in payload
    description_present = "description" in payload
    if not present_fields:
        return {"ok": False, "error": "no_text_fields_to_update", "draft_id": read["draft_id"]}

    if title_present:
        product["title"] = _clean_text(payload.get("title"), 80)
    if description_present:
        description = str(payload.get("description") or "").strip()
        product["description"] = description
        offer["listingDescription"] = description
    if "condition" in payload:
        inventory["condition"] = _clean_text(payload.get("condition"), 40).upper() or "USED_GOOD"
    if "quantity" in payload:
        try:
            quantity = max(0, int(str(payload.get("quantity") or "1").strip()))
        except Exception:
            quantity = 1
        availability = inventory.get("availability") if isinstance(inventory.get("availability"), dict) else {}
        ship_availability = availability.get("shipToLocationAvailability") if isinstance(availability.get("shipToLocationAvailability"), dict) else {}
        ship_availability["quantity"] = quantity
        availability["shipToLocationAvailability"] = ship_availability
        inventory["availability"] = availability
        offer["availableQuantity"] = quantity
    if "category_id" in payload:
        offer["categoryId"] = _clean_text(payload.get("category_id"), 40)
    if "price" in payload or "currency" in payload:
        pricing = offer.get("pricingSummary") if isinstance(offer.get("pricingSummary"), dict) else {}
        price = pricing.get("price") if isinstance(pricing.get("price"), dict) else {}
        if "price" in payload:
            price["value"] = _clean_text(payload.get("price"), 40)
        if "currency" in payload:
            price["currency"] = _clean_text(payload.get("currency"), 8).upper() or "USD"
        pricing["price"] = price
        offer["pricingSummary"] = pricing
    if any(name in payload for name in {"fulfillment_policy_id", "payment_policy_id", "return_policy_id"}):
        listing_policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
        if "fulfillment_policy_id" in payload:
            listing_policies["fulfillmentPolicyId"] = _clean_text(payload.get("fulfillment_policy_id"), 80)
        if "payment_policy_id" in payload:
            listing_policies["paymentPolicyId"] = _clean_text(payload.get("payment_policy_id"), 80)
        if "return_policy_id" in payload:
            listing_policies["returnPolicyId"] = _clean_text(payload.get("return_policy_id"), 80)
        offer["listingPolicies"] = listing_policies
    if "merchant_location_key" in payload:
        offer["merchantLocationKey"] = _clean_text(payload.get("merchant_location_key"), 120)
    if any(name in payload for name in {"shipping_payer", "shipping_service", "shipping_type", "package_length", "package_width", "package_height", "package_unit", "package_weight_value", "package_weight_unit"}):
        shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else build_shipping_defaults(
            {"title": product.get("title") or "", "description": product.get("description") or ""}
        )
        package = shipping.get("package") if isinstance(shipping.get("package"), dict) else {}
        if "shipping_payer" in payload:
            shipping["payer"] = _clean_text(payload.get("shipping_payer"), 20).lower() or "buyer"
        if "shipping_service" in payload:
            shipping["service"] = _clean_text(payload.get("shipping_service"), 80) or "USPS Ground Advantage"
        if "shipping_type" in payload:
            shipping["type"] = _clean_text(payload.get("shipping_type"), 40).lower() or "calculated"

        def _num(name: str, fallback: Any) -> Any:
            if name not in payload:
                return fallback
            raw = str(payload.get(name) or "").strip()
            if not raw:
                return fallback
            try:
                value = float(raw)
                return int(value) if value.is_integer() else value
            except Exception:
                return fallback

        package["length"] = _num("package_length", package.get("length") or 12)
        package["width"] = _num("package_width", package.get("width") or 9)
        package["height"] = _num("package_height", package.get("height") or 6)
        package["weight_value"] = _num("package_weight_value", package.get("weight_value") or 2)
        if "package_unit" in payload:
            package["unit"] = _clean_text(payload.get("package_unit"), 20).upper() or "INCH"
        if "package_weight_unit" in payload:
            package["weight_unit"] = _clean_text(payload.get("package_weight_unit"), 20).upper() or "POUND"
        package["estimated"] = True
        shipping["package"] = package
        draft["shipping"] = shipping

    inventory["product"] = product
    draft["inventory_item"] = inventory
    draft["offer"] = offer

    missing = [
        str(name)
        for name in _as_list(draft.get("missing"))
        if str(name)
        not in {
            "title",
            "description",
            "condition",
            "quantity",
            "category_id",
            "price",
            "merchant_location_key",
            "fulfillment_policy_id",
            "payment_policy_id",
            "return_policy_id",
        }
    ]
    if not str(product.get("title") or "").strip():
        missing.append("title")
    if not str(product.get("description") or "").strip():
        missing.append("description")
    if not str(inventory.get("condition") or "").strip():
        missing.append("condition")
    if inventory.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity") in (None, ""):
        missing.append("quantity")
    if not str(offer.get("categoryId") or "").strip():
        missing.append("category_id")
    if not str(offer.get("pricingSummary", {}).get("price", {}).get("value") or "").strip():
        missing.append("price")
    listing_policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
    if not str(offer.get("merchantLocationKey") or "").strip():
        missing.append("merchant_location_key")
    if not str(listing_policies.get("fulfillmentPolicyId") or "").strip():
        missing.append("fulfillment_policy_id")
    if not str(listing_policies.get("paymentPolicyId") or "").strip():
        missing.append("payment_policy_id")
    if not str(listing_policies.get("returnPolicyId") or "").strip():
        missing.append("return_policy_id")
    draft["missing"] = list(dict.fromkeys(missing))
    draft["ready_to_publish"] = not bool(draft["missing"])
    draft["ready_to_create_offer"] = not bool([name for name in draft["missing"] if name != "image_urls"])
    if isinstance(draft.get("shipping"), dict) and not any(
        name in payload
        for name in {"shipping_payer", "shipping_service", "shipping_type", "package_length", "package_width", "package_height", "package_unit", "package_weight_value", "package_weight_unit"}
    ):
        package = draft["shipping"].get("package") if isinstance(draft["shipping"].get("package"), dict) else {}
        if package.get("estimated", True):
            draft["shipping"] = build_shipping_defaults(
                {"title": product.get("title") or "", "description": product.get("description") or ""}
            )

    record["draft"] = draft
    if record.get("status") == "approved":
        record["status"] = "needs_approval"
        record["approval_invalidated_reason"] = "Listing details changed after approval."
    record.pop("publish_readiness", None)
    if not isinstance(record.get("text_updates"), list):
        record["text_updates"] = []
    record["text_updates"].append(
        {
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": _clean_text(payload.get("source") or payload.get("source_agent") or "operator", 80),
            "fields": present_fields,
            "guidance": _clean_text(payload.get("guidance") or "", 500),
        }
    )

    write = _write_draft_entry(read["draft_id"], record)
    md_path = read.get("md_path")
    if isinstance(md_path, Path):
        try:
            md_path.write_text(_approval_markdown(read["draft_id"], record, draft), encoding="utf-8")
        except Exception:
            pass
    return add_preview_links(
        {
            "ok": bool(write.get("ok")),
            "action": "update_approval_draft_text",
            "draft_id": read["draft_id"],
            "status": record.get("status") or "",
            "draft": draft,
            "write": {k: str(v) for k, v in write.items()},
        }
    )


def _build_live_payload_from_draft(draft_record: Dict[str, Any]) -> Dict[str, Any]:
    draft = draft_record.get("draft") if isinstance(draft_record.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    offer = draft.get("offer") if isinstance(draft.get("offer"), dict) else {}
    listing_price = offer.get("pricingSummary", {}).get("price", {}) if isinstance(offer.get("pricingSummary"), dict) else {}
    listing_policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
    payload = {
        "sku": draft.get("sku") or "",
        "title": product.get("title") or "",
        "description": product.get("description") or "",
        "condition": inventory.get("condition") or "",
        "quantity": inventory.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 1),
        "category_id": offer.get("categoryId") or "",
        "price": {
            "value": (listing_price.get("value") or ""),
            "currency": (listing_price.get("currency") or "USD"),
        },
        "merchant_location_key": offer.get("merchantLocationKey") or draft_record.get("merchant_location_key") or "",
        "fulfillment_policy_id": listing_policies.get("fulfillmentPolicyId") or "",
        "payment_policy_id": listing_policies.get("paymentPolicyId") or "",
        "return_policy_id": listing_policies.get("returnPolicyId") or "",
        "image_urls": _as_list(product.get("imageUrls")),
        "environment": draft_record.get("environment") or "sandbox",
    }
    source = draft_record.get("source") if isinstance(draft_record.get("source"), dict) else {}
    if source:
        payload["source_task_id"] = source.get("task_id") or ""
    return payload


def approve_approval_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    record = read["payload"]
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_draft_record"}

    if record.get("status") == "published":
        return {"ok": True, "draft_id": read["draft_id"], "status": "already_published", "payload": record}

    record["status"] = "approved"
    record["approved_by"] = _clean_text(payload.get("approved_by"), 80) or "operator"
    record["approved_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if payload.get("approval_notes"):
        record["approval_notes"] = str(payload.get("approval_notes"))
    if not _truthy(payload.get("skip_publish_readiness")):
        record["publish_readiness"] = build_publish_readiness({"record": record, **payload})
    _write_draft_entry(read["draft_id"], record)
    return {"ok": True, "action": "approve_approval_draft", "draft_id": read["draft_id"], "status": "approved", "payload": record}


def refresh_approval_draft_readiness(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    record = read["payload"]
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_draft_record"}
    record["publish_readiness"] = build_publish_readiness({"record": record, **payload})
    write = _write_draft_entry(read["draft_id"], record)
    md_path = read.get("md_path")
    if isinstance(md_path, Path):
        try:
            md_path.write_text(_approval_markdown(read["draft_id"], record, record.get("draft", {})), encoding="utf-8")
        except Exception:
            pass
    return {
        "ok": bool(write.get("ok")),
        "action": "refresh_approval_draft_readiness",
        "draft_id": read["draft_id"],
        "status": record.get("status") or "",
        "payload": record,
        "publish_readiness": record["publish_readiness"],
    }


def publish_approval_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    record = read["payload"]
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_draft_record"}

    if record.get("status") == "published":
        return {"ok": True, "draft_id": read["draft_id"], "status": "already_published", "payload": record}

    if not _truthy(payload.get("force")) and record.get("status") != "approved":
        return {"ok": False, "error": "draft_not_approved", "status": str(record.get("status") or "needs_approval")}

    run_payload = _build_live_payload_from_draft(record)
    run_payload["action"] = "full"
    run_payload["publish"] = True
    run_payload["operator_confirmed"] = bool(_truthy(payload.get("operator_confirmed")) or _truthy(payload.get("confirmed")))
    run_payload["confirm_phrase"] = str(payload.get("confirm_phrase") or PUBLISH_CONFIRM_PHRASE)
    run_result = full_listing_flow(run_payload)
    if not run_result.get("ok"):
        record["status"] = "publish_failed"
        record["last_error"] = run_result.get("error") or run_result.get("stage") or "publish_failed"
        record["publish_result"] = run_result
        _write_draft_entry(read["draft_id"], record)
        return {
            "ok": False,
            "draft_id": read["draft_id"],
            "status": "publish_failed",
            "payload": record,
            "publish": run_result,
        }

    offer = run_result.get("offer", {}) if isinstance(run_result.get("offer"), dict) else {}
    publish = run_result.get("publish", {}) if isinstance(run_result.get("publish"), dict) else {}
    ebay_response = publish.get("ebay", {})
    item_id = ""
    if isinstance(ebay_response, dict):
        item_id = str(
            ebay_response.get("itemId")
            or ebay_response.get("item_id")
            or ebay_response.get("listingId")
            or ebay_response.get("listing_id")
            or ""
        ).strip()
    offer_id = str(offer.get("offer_id") or "")
    published_url = _offer_published_url(offer_id, item_id)

    record["status"] = "published"
    record["publish"] = {
        "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "operator_confirmed": bool(run_payload.get("operator_confirmed")),
        "confirm_phrase": run_payload.get("confirm_phrase"),
        "offer": offer,
        "publish_api": publish,
        "run_result": run_result,
    }
    record["offer_id"] = offer_id
    record["item_id"] = item_id
    record["published_at"] = record["publish"]["published_at"]
    if published_url:
        record["published_url"] = published_url

    _write_draft_entry(read["draft_id"], record)
    md_path = read.get("md_path")
    if isinstance(md_path, Path):
        try:
            md_path.write_text(_approval_markdown(read["draft_id"], record, record.get("draft", {})), encoding="utf-8")
        except Exception:
            pass
    return {"ok": True, "action": "publish_approval_draft", "draft_id": read["draft_id"], "status": "published", "payload": record, "publish": publish}


def add_preview_links(packet: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(packet, dict):
        return {"ok": False, "error": "invalid_packet"}
    draft_id = packet.get("draft_id") or ""
    if not draft_id:
        return packet
    packet["approval_url"] = f"/admin/ebay/drafts/{draft_id}"
    packet["artifact_url"] = f"/admin/ebay/drafts/{draft_id}/artifact"
    packet["dashboard_url"] = "/admin/ebay/drafts"
    packet["preview_url"] = f"/admin/ebay/drafts/{draft_id}"
    return packet


def load_approval_draft(draft_id: str) -> Dict[str, Any]:
    safe_id = _safe_draft_id(draft_id)
    if not safe_id or safe_id != str(draft_id or "").strip():
        return {"ok": False, "error": "invalid_draft_id"}
    draft_dir = EBAY_APPROVAL_DRAFT_DIR / safe_id
    json_path = draft_dir / "draft.json"
    md_path = draft_dir / "approval.md"
    if not json_path.exists():
        return {"ok": False, "error": "draft_not_found", "draft_id": safe_id}
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "draft_read_failed", "detail": str(exc), "draft_id": safe_id}
    payload["artifact_path"] = str(md_path)
    payload["json_path"] = str(json_path)
    return {"ok": True, "draft_id": safe_id, "payload": payload, "markdown_path": str(md_path), "json_path": str(json_path)}


def list_approval_drafts(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    try:
        limit = int(payload.get("limit", 50))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 500))
    include_all = bool(payload.get("include_all"))
    if include_all:
        limit = max(limit, 500)
    exclude_smoke = not bool(payload.get("include_smoke"))
    term = _normalize_draft_title(payload.get("term"))

    if not EBAY_APPROVAL_DRAFT_DIR.exists():
        return {"ok": True, "drafts": []}

    draft_dirs = [p for p in EBAY_APPROVAL_DRAFT_DIR.iterdir() if p.is_dir()]
    draft_dirs = sorted(draft_dirs, key=lambda p: p.stat().st_mtime, reverse=True)

    drafts: list[Dict[str, Any]] = []
    for draft_dir in draft_dirs:
        if draft_dir.name.startswith("_"):
            continue
        json_path = draft_dir / "draft.json"
        if not json_path.exists():
            continue
        try:
            payload_data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        draft = payload_data.get("draft", {}) if isinstance(payload_data.get("draft"), dict) else {}
        inventory = draft.get("inventory_item", {}) if isinstance(draft.get("inventory_item"), dict) else {}
        product = inventory.get("product", {}) if isinstance(inventory.get("product"), dict) else {}
        offer = draft.get("offer", {}) if isinstance(draft.get("offer"), dict) else {}
        source = payload_data.get("source", {}) if isinstance(payload_data.get("source"), dict) else {}
        title = str(product.get("title") or "")
        sku = str(draft.get("sku") or "")
        draft_id = str(payload_data.get("draft_id") or draft_dir.name)
        normalized_title = _normalize_draft_title(title)
        if exclude_smoke and _is_smoke_draft(draft_id, title):
            continue
        if term:
            haystack = " ".join([normalized_title, _normalize_draft_title(sku), _normalize_draft_title(draft_id)])
            if term not in haystack:
                continue
        drafts.append(
            {
                "draft_id": draft_id,
                "status": str(payload_data.get("status") or ""),
                "action": str(payload_data.get("action") or ""),
                "created_at": str(payload_data.get("created_at") or ""),
                "updated_at": str(payload_data.get("updated_at") or ""),
                "title": title,
                "sku": sku,
                "price": str(offer.get("pricingSummary", {}).get("price", {}).get("value") or ""),
                "currency": str(offer.get("pricingSummary", {}).get("price", {}).get("currency") or ""),
                "price_source": str(offer.get("pricingSummary", {}).get("priceSource") or ""),
                "price_inferred": bool(offer.get("pricingSummary", {}).get("priceInferred")),
                "category_id": str(offer.get("categoryId") or ""),
                "ready_to_publish": bool(draft.get("ready_to_publish")),
                "missing": draft.get("missing") if isinstance(draft.get("missing"), list) else [],
                "approval_url": f"/admin/ebay/drafts/{draft_id}",
                "artifact_url": f"/admin/ebay/drafts/{draft_id}/artifact",
                "preview_images": _merge_unique_text(product.get("imageUrls"), source.get("attachment_urls"))[:3],
                "published_url": str(payload_data.get("published_url") or ""),
                "offer_id": str(payload_data.get("offer_id") or ""),
                "item_id": str(payload_data.get("item_id") or ""),
                "is_smoke": bool(_is_smoke_draft(draft_id, title)),
            }
        )
        if len(drafts) >= limit:
            break
    return {"ok": True, "drafts": drafts, "count": len(drafts)}


def delete_approval_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    draft_dir = read.get("draft_dir")
    if not isinstance(draft_dir, Path) or not draft_dir.exists():
        return {"ok": False, "error": "draft_not_found", "draft_id": read.get("draft_id") or draft_id}
    try:
        shutil.rmtree(draft_dir)
    except Exception as exc:
        return {"ok": False, "error": "draft_delete_failed", "detail": str(exc), "draft_id": read.get("draft_id") or draft_id}
    return {
        "ok": True,
        "action": "delete_approval_draft",
        "draft_id": read.get("draft_id") or draft_id,
        "deleted_dir": str(draft_dir),
        "status": "deleted",
    }


def draft_repeat_summary(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    term = _normalize_draft_title(payload.get("term"))
    include_smoke = bool(payload.get("include_smoke"))
    if not term:
        return {"ok": False, "error": "term_required"}

    if not EBAY_APPROVAL_DRAFT_DIR.exists():
        return {"ok": True, "term": term, "matches": [], "match_count": 0, "repeated_titles": {}}

    title_counts: Dict[str, int] = {}
    matches: list[Dict[str, Any]] = []
    for draft_dir in sorted([p for p in EBAY_APPROVAL_DRAFT_DIR.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
        if draft_dir.name.startswith("_"):
            continue
        json_path = draft_dir / "draft.json"
        if not json_path.exists():
            continue
        try:
            payload_data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        draft = payload_data.get("draft", {}) if isinstance(payload_data.get("draft"), dict) else {}
        inventory = draft.get("inventory_item", {}) if isinstance(draft.get("inventory_item"), dict) else {}
        product = inventory.get("product", {}) if isinstance(inventory.get("product"), dict) else {}
        title = str(product.get("title") or "")
        sku = str(draft.get("sku") or "")
        draft_id = str(payload_data.get("draft_id") or draft_dir.name)
        if not include_smoke and _is_smoke_draft(draft_id, title):
            continue

        normalized_title = _normalize_draft_title(title)
        normalized_sku = _normalize_draft_title(sku)
        normalized_id = _normalize_draft_title(draft_id)
        haystack = " ".join([normalized_title, normalized_sku, normalized_id])
        if term not in haystack:
            continue

        normalized_bucket = normalized_title or normalized_id
        title_counts[normalized_bucket] = title_counts.get(normalized_bucket, 0) + 1
        matches.append(
            {
                "draft_id": draft_id,
                "title": title,
                "normalized_title": normalized_bucket,
                "status": str(payload_data.get("status") or ""),
                "created_at": str(payload_data.get("created_at") or payload_data.get("updated_at") or ""),
                "updated_at": str(payload_data.get("updated_at") or ""),
                "approval_url": f"/admin/ebay/drafts/{draft_id}",
                "artifact_url": f"/admin/ebay/drafts/{draft_id}/artifact",
            }
        )

    repeated_titles = {
        bucket: count
        for bucket, count in sorted(title_counts.items(), key=lambda item: (-item[1], item[0]))
        if count > 1
    }
    return {
        "ok": True,
        "term": term,
        "match_count": len(matches),
        "matches": matches,
        "repeated_titles": repeated_titles,
        "repeated_title_count": len(repeated_titles),
        "repeated_total": sum(repeated_titles.values()),
    }


def _headers(cfg: EbayConfig) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": cfg.marketplace_id,
    }


def _redact_ebay_response(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: _redact_ebay_response(value) for key, value in data.items() if str(key).lower() not in {"access_token", "refresh_token"}}
    if isinstance(data, list):
        return [_redact_ebay_response(item) for item in data]
    return data


def _ebay_request(method: str, path: str, body: Dict[str, Any] | None, cfg: EbayConfig, timeout_s: float = 20.0) -> Dict[str, Any]:
    if not cfg.access_token:
        return {"ok": False, "error": "ebay_access_token_required", "hint": "Set APOLLO_EBAY_ACCESS_TOKEN or EBAY_ACCESS_TOKEN."}
    started = time.perf_counter()
    url = f"{cfg.api_base_url}{path}"
    try:
        response = requests.request(
            method.upper(),
            url,
            headers=_headers(cfg),
            data=json.dumps(body, ensure_ascii=True) if body is not None else None,
            timeout=max(2.0, min(float(timeout_s or 20.0), 60.0)),
        )
        try:
            data = response.json() if response.text else {}
        except Exception:
            data = {"raw": response.text[:2000]}
        return {
            "ok": bool(200 <= response.status_code < 300),
            "http_status": int(response.status_code),
            "url": url,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "response": _redact_ebay_response(data),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "ebay_request_failed",
            "detail": str(exc)[:400],
            "url": url,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }


def oauth_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    scopes = _oauth_scopes(payload)
    return {
        "ok": bool(_oauth_client_id(payload) and _oauth_client_secret(payload) and _oauth_redirect_uri(payload)),
        "action": "oauth_status",
        "environment": "production" if cfg.api_base_url == EBAY_PRODUCTION_API else "sandbox",
        "auth_url": _oauth_auth_url(cfg),
        "token_url": f"{cfg.api_base_url}/identity/v1/oauth2/token",
        "client_id_present": bool(_oauth_client_id(payload)),
        "client_secret_present": bool(_oauth_client_secret(payload)),
        "redirect_uri_present": bool(_oauth_redirect_uri(payload)),
        "refresh_token_present": bool(_oauth_refresh_token(payload)),
        "access_token_present": bool(cfg.access_token),
        "scopes": scopes,
        "missing_env": [
            key
            for key, present in (
                ("APOLLO_EBAY_CLIENT_ID", bool(_oauth_client_id(payload))),
                ("APOLLO_EBAY_CLIENT_SECRET", bool(_oauth_client_secret(payload))),
                ("APOLLO_EBAY_RUNAME", bool(_oauth_redirect_uri(payload))),
            )
            if not present
        ],
    }


def oauth_authorize_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    status = oauth_status(payload)
    if status.get("missing_env"):
        return {"ok": False, "action": "oauth_authorize_url", "missing_env": status["missing_env"], "oauth_status": status}
    state = _clean_text(payload.get("state") or secrets.token_urlsafe(18), 80)
    params = {
        "client_id": _oauth_client_id(payload),
        "redirect_uri": _oauth_redirect_uri(payload),
        "response_type": "code",
        "scope": " ".join(_oauth_scopes(payload)),
        "state": state,
    }
    return {
        "ok": True,
        "action": "oauth_authorize_url",
        "environment": status["environment"],
        "authorize_url": f"{_oauth_auth_url(cfg)}?{urlencode(params)}",
        "state": state,
        "scopes": _oauth_scopes(payload),
        "instructions": [
            "Open authorize_url in the eBay developer browser session.",
            "Approve the seller consent screen.",
            "Copy only the returned code parameter into oauth_exchange_code.",
        ],
    }


def _oauth_token_request(payload: Dict[str, Any], form: Dict[str, str]) -> Dict[str, Any]:
    cfg = _config(payload)
    client_id = _oauth_client_id(payload)
    client_secret = _oauth_client_secret(payload)
    if not client_id or not client_secret:
        return {"ok": False, "error": "ebay_oauth_client_credentials_required", "missing_env": oauth_status(payload).get("missing_env", [])}
    started = time.perf_counter()
    url = f"{cfg.api_base_url}/identity/v1/oauth2/token"
    try:
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": _oauth_basic_header(client_id, client_secret),
            },
            data=urlencode(form),
            timeout=max(2.0, min(float(payload.get("timeout_s") or 20.0), 60.0)),
        )
        try:
            data = response.json() if response.text else {}
        except Exception:
            data = {"raw": response.text[:2000]}
        return {
            "ok": bool(200 <= response.status_code < 300),
            "http_status": int(response.status_code),
            "url": url,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "response": data,
        }
    except Exception as exc:
        return {"ok": False, "error": "ebay_oauth_request_failed", "detail": str(exc)[:400], "url": url, "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def _store_oauth_response(payload: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, str] = {}
    if response.get("access_token"):
        values["APOLLO_EBAY_ACCESS_TOKEN"] = str(response.get("access_token") or "")
    if response.get("refresh_token"):
        values["APOLLO_EBAY_REFRESH_TOKEN"] = str(response.get("refresh_token") or "")
    if not values:
        return {"stored": False, "env_file": str(_env_file_path(payload)), "stored_keys": []}
    path = _env_file_path(payload)
    _write_env_values(path, values)
    for key, value in values.items():
        os.environ[key] = value
    return {"stored": True, "env_file": str(path), "stored_keys": sorted(values)}


def _token_metadata(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "access_token_present": bool(response.get("access_token")),
        "refresh_token_present": bool(response.get("refresh_token")),
        "expires_in": response.get("expires_in"),
        "refresh_token_expires_in": response.get("refresh_token_expires_in"),
        "token_type": response.get("token_type"),
    }


def oauth_exchange_code(payload: Dict[str, Any]) -> Dict[str, Any]:
    code = str(payload.get("code") or "").strip()
    redirect_uri = _oauth_redirect_uri(payload)
    if not code:
        return {"ok": False, "action": "oauth_exchange_code", "error": "authorization_code_required"}
    if not redirect_uri:
        return {"ok": False, "action": "oauth_exchange_code", "error": "redirect_uri_required", "missing_env": ["APOLLO_EBAY_RUNAME"]}
    result = _oauth_token_request(
        payload,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    stored = _store_oauth_response(payload, response) if result.get("ok") and not payload.get("no_store") else {"stored": False, "stored_keys": [], "env_file": str(_env_file_path(payload))}
    return {
        "ok": bool(result.get("ok")),
        "action": "oauth_exchange_code",
        "environment": oauth_status(payload).get("environment"),
        "http_status": result.get("http_status"),
        "elapsed_ms": result.get("elapsed_ms"),
        "token": _token_metadata(response),
        "store": stored,
        "error": result.get("error") or ("" if result.get("ok") else "oauth_exchange_failed"),
        "response_redacted": _redact_ebay_response(response),
    }


def oauth_refresh_access_token(payload: Dict[str, Any]) -> Dict[str, Any]:
    refresh_token = _oauth_refresh_token(payload)
    if not refresh_token:
        return {"ok": False, "action": "oauth_refresh_access_token", "error": "refresh_token_required", "missing_env": ["APOLLO_EBAY_REFRESH_TOKEN"]}
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": " ".join(_oauth_scopes(payload)),
    }
    result = _oauth_token_request(payload, form)
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    stored = _store_oauth_response(payload, response) if result.get("ok") and not payload.get("no_store") else {"stored": False, "stored_keys": [], "env_file": str(_env_file_path(payload))}
    return {
        "ok": bool(result.get("ok")),
        "action": "oauth_refresh_access_token",
        "environment": oauth_status(payload).get("environment"),
        "http_status": result.get("http_status"),
        "elapsed_ms": result.get("elapsed_ms"),
        "token": _token_metadata(response),
        "store": stored,
        "error": result.get("error") or ("" if result.get("ok") else "oauth_refresh_failed"),
        "response_redacted": _redact_ebay_response(response),
    }


def credential_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    oauth = oauth_status(payload)
    return {
        "ok": bool(cfg.access_token),
        "action": "credential_status",
        "environment": "production" if cfg.api_base_url == EBAY_PRODUCTION_API else "sandbox",
        "api_base_url": cfg.api_base_url,
        "marketplace_id": cfg.marketplace_id,
        "access_token_present": bool(cfg.access_token),
        "refresh_token_present": bool(oauth.get("refresh_token_present")),
        "oauth_client_ready": bool(oauth.get("ok")),
        "mutation_enabled": bool(cfg.mutation_enabled),
        "publish_enabled": bool(cfg.publish_enabled),
        "required_env": {
            "access_token": "APOLLO_EBAY_ACCESS_TOKEN",
            "refresh_token": "APOLLO_EBAY_REFRESH_TOKEN",
            "client_id": "APOLLO_EBAY_CLIENT_ID",
            "client_secret": "APOLLO_EBAY_CLIENT_SECRET",
            "redirect_uri": "APOLLO_EBAY_RUNAME",
            "mutations": "APOLLO_EBAY_ENABLE_MUTATIONS=1",
            "publish": "APOLLO_EBAY_ENABLE_PUBLISH=1",
        },
    }


def seller_prerequisites(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    marketplace = quote(cfg.marketplace_id, safe="")
    results = {
        "fulfillment_policies": _ebay_request("GET", f"/sell/account/v1/fulfillment_policy?marketplace_id={marketplace}", None, cfg),
        "payment_policies": _ebay_request("GET", f"/sell/account/v1/payment_policy?marketplace_id={marketplace}", None, cfg),
        "return_policies": _ebay_request("GET", f"/sell/account/v1/return_policy?marketplace_id={marketplace}", None, cfg),
        "inventory_locations": _ebay_request("GET", "/sell/inventory/v1/location?limit=50", None, cfg),
    }
    ready = bool(cfg.access_token) and all(bool(item.get("ok")) for item in results.values())
    return {
        "ok": ready,
        "action": "seller_prerequisites",
        "environment": "production" if cfg.api_base_url == EBAY_PRODUCTION_API else "sandbox",
        "marketplace_id": cfg.marketplace_id,
        "checks": results,
        "ready_for_offer_policy_selection": ready,
        "notes": [
            "Inventory API publishing requires an inventory location and payment, fulfillment, and return business policies.",
            "Use returned policy IDs and merchantLocationKey in listing drafts.",
        ],
    }


def category_suggestions(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    query = _clean_text(payload.get("query") or payload.get("keywords") or payload.get("title"), 120)
    if not query:
        return {"ok": False, "error": "category_query_required"}
    tree_id = str(
        payload.get("category_tree_id")
        or os.getenv("APOLLO_EBAY_CATEGORY_TREE_ID")
        or _CATEGORY_TREE_BY_MARKETPLACE.get(cfg.marketplace_id, "")
    ).strip()
    if not tree_id:
        tree = _ebay_request(
            "GET",
            f"/commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id={quote(cfg.marketplace_id, safe='')}",
            None,
            cfg,
        )
        if not tree.get("ok"):
            return {"ok": False, "action": "category_suggestions", "stage": "category_tree", "category_tree": tree}
        response = tree.get("response") if isinstance(tree.get("response"), dict) else {}
        tree_id = str(response.get("categoryTreeId") or "")
    params = urlencode({"q": query})
    result = _ebay_request("GET", f"/commerce/taxonomy/v1/category_tree/{quote(tree_id, safe='')}/get_category_suggestions?{params}", None, cfg)
    return {
        "ok": bool(result.get("ok")),
        "action": "category_suggestions",
        "query": query,
        "marketplace_id": cfg.marketplace_id,
        "category_tree_id": tree_id,
        "sandbox_caveat": cfg.api_base_url == EBAY_SANDBOX_API,
        "ebay": result,
    }


def setup_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    oauth = oauth_status(payload)
    draft = build_listing_draft(payload) if payload.get("title") or payload.get("sku") else {}
    vm = aegis_vm_client.vm_status(float(payload.get("timeout_s") or 5.0)) if _truthy(payload.get("include_vm")) else {}
    missing_env = []
    missing_env.extend(str(item) for item in oauth.get("missing_env") or [])
    if not cfg.access_token:
        missing_env.append("APOLLO_EBAY_ACCESS_TOKEN")
    if not _oauth_refresh_token(payload):
        missing_env.append("APOLLO_EBAY_REFRESH_TOKEN")
    if not os.getenv("APOLLO_EBAY_MERCHANT_LOCATION_KEY") and not payload.get("merchant_location_key"):
        missing_env.append("APOLLO_EBAY_MERCHANT_LOCATION_KEY")
    for env_key, payload_key in (
        ("APOLLO_EBAY_FULFILLMENT_POLICY_ID", "fulfillment_policy_id"),
        ("APOLLO_EBAY_PAYMENT_POLICY_ID", "payment_policy_id"),
        ("APOLLO_EBAY_RETURN_POLICY_ID", "return_policy_id"),
    ):
        if not os.getenv(env_key) and not payload.get(payload_key):
            missing_env.append(env_key)
    missing_env = list(dict.fromkeys(missing_env))
    listing_missing = list(draft.get("missing") or []) if isinstance(draft, dict) else []
    return {
        "ok": bool(cfg.access_token) and not bool(listing_missing),
        "action": "setup_status",
        "credential_status": credential_status(payload),
        "oauth_status": oauth,
        "missing_env": missing_env,
        "draft": draft,
        "vm_status": vm,
        "next_steps": [
            "Use oauth_authorize_url, then oauth_exchange_code, to mint and store a User access token and refresh token.",
            "Use oauth_refresh_access_token whenever the short-lived User access token expires.",
            "Run seller_prerequisites to read available policy IDs and inventory locations.",
            "Run category_suggestions with the item title or keywords.",
            "Run draft until missing is empty.",
            "Only then enable APOLLO_EBAY_ENABLE_MUTATIONS=1 for sandbox create inventory/offer.",
        ],
    }


def sandbox_pilot(payload: Dict[str, Any]) -> Dict[str, Any]:
    pilot_payload = {**payload, "environment": payload.get("environment") or "sandbox"}
    status = setup_status({**pilot_payload, "include_vm": payload.get("include_vm", True)})
    category = category_suggestions(pilot_payload) if (pilot_payload.get("query") or pilot_payload.get("title") or pilot_payload.get("keywords")) and _config(pilot_payload).access_token else {}
    prereqs = seller_prerequisites(pilot_payload) if _truthy(payload.get("include_prereqs")) else {}
    return {
        "ok": bool(status.get("ok")),
        "action": "sandbox_pilot",
        "stage": "dry_run_plan",
        "setup_status": status,
        "category_suggestions": category,
        "seller_prerequisites": prereqs,
        "create_sequence": [
            "create_or_replace_inventory_item",
            "create_offer",
            "review offer_id and eBay validation response",
            "publish_offer only after separate explicit publish gate",
        ],
        "writes_performed": False,
    }


def _mutation_gate(cfg: EbayConfig, payload: Dict[str, Any], *, publish: bool = False) -> Tuple[bool, Dict[str, Any]]:
    if not cfg.mutation_enabled:
        return False, {"ok": False, "error": "ebay_mutations_disabled", "required_env": "APOLLO_EBAY_ENABLE_MUTATIONS=1"}
    if not bool(payload.get("operator_confirmed")):
        return False, {"ok": False, "error": "operator_confirmation_required"}
    if publish:
        if not cfg.publish_enabled:
            return False, {"ok": False, "error": "ebay_publish_disabled", "required_env": "APOLLO_EBAY_ENABLE_PUBLISH=1"}
        if str(payload.get("confirm_phrase") or "").strip() != PUBLISH_CONFIRM_PHRASE:
            return False, {"ok": False, "error": "publish_confirm_phrase_required", "confirm_phrase": PUBLISH_CONFIRM_PHRASE}
    return True, {}


def create_or_replace_inventory_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    allowed, blocked = _mutation_gate(cfg, payload)
    if not allowed:
        return blocked
    sku = _sku(payload)
    body = build_inventory_item({**payload, "sku": sku})
    result = _ebay_request("PUT", f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}", body, cfg)
    return {"ok": bool(result.get("ok")), "action": "create_or_replace_inventory_item", "sku": sku, "ebay": result}


def create_offer(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    allowed, blocked = _mutation_gate(cfg, payload)
    if not allowed:
        return blocked
    body = build_offer(payload, cfg)
    result = _ebay_request("POST", "/sell/inventory/v1/offer", body, cfg)
    offer_id = ""
    if isinstance(result.get("response"), dict):
        offer_id = str(result["response"].get("offerId") or "")
    return {"ok": bool(result.get("ok")), "action": "create_offer", "sku": body.get("sku"), "offer_id": offer_id, "ebay": result}


def publish_offer(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    allowed, blocked = _mutation_gate(cfg, payload, publish=True)
    if not allowed:
        return blocked
    offer_id = str(payload.get("offer_id") or "").strip()
    if not offer_id:
        return {"ok": False, "error": "offer_id_required"}
    result = _ebay_request("POST", f"/sell/inventory/v1/offer/{quote(offer_id, safe='')}/publish", None, cfg)
    return {"ok": bool(result.get("ok")), "action": "publish_offer", "offer_id": offer_id, "ebay": result}


def withdraw_offer(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _config(payload)
    allowed, blocked = _mutation_gate(cfg, payload)
    if not allowed:
        return blocked
    offer_id = str(payload.get("offer_id") or "").strip()
    if not offer_id:
        return {"ok": False, "error": "offer_id_required"}
    result = _ebay_request("POST", f"/sell/inventory/v1/offer/{quote(offer_id, safe='')}/withdraw", None, cfg)
    return {"ok": bool(result.get("ok")), "action": "withdraw_offer", "offer_id": offer_id, "ebay": result}


def withdraw_approval_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return {"ok": False, "error": "draft_id_required"}
    read = _read_draft_entry(draft_id)
    if not read.get("ok"):
        return read
    record = read["payload"]
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_draft_record"}
    offer_id = str(payload.get("offer_id") or record.get("offer_id") or "").strip()
    result = withdraw_offer({**payload, "offer_id": offer_id})
    record["withdraw_result"] = result
    record["withdraw_checked_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if result.get("ok"):
        record["status"] = "withdrawn"
        record["withdrawn_at"] = record["withdraw_checked_at"]
    else:
        record["last_error"] = result.get("error") or result.get("stage") or "withdraw_failed"
    _write_draft_entry(read["draft_id"], record)
    md_path = read.get("md_path")
    if isinstance(md_path, Path):
        try:
            md_path.write_text(_approval_markdown(read["draft_id"], record, record.get("draft", {})), encoding="utf-8")
        except Exception:
            pass
    return {
        "ok": bool(result.get("ok")),
        "action": "withdraw_approval_draft",
        "draft_id": read["draft_id"],
        "status": record.get("status") or "",
        "payload": record,
        "withdraw": result,
    }


def full_listing_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft = build_listing_draft(payload)
    if draft.get("missing"):
        return {"ok": False, "error": "listing_required_fields_missing", "draft": draft}
    inventory = create_or_replace_inventory_item(payload)
    if not inventory.get("ok"):
        return {"ok": False, "stage": "inventory_item", "inventory": inventory, "draft": draft}
    offer = create_offer(payload)
    if not offer.get("ok"):
        return {"ok": False, "stage": "offer", "inventory": inventory, "offer": offer, "draft": draft}
    if not _truthy(payload.get("publish")):
        return {"ok": True, "stage": "offer_created_unpublished", "inventory": inventory, "offer": offer, "draft": draft}
    publish_payload = {**payload, "offer_id": offer.get("offer_id")}
    publish = publish_offer(publish_payload)
    return {"ok": bool(publish.get("ok")), "stage": "published" if publish.get("ok") else "publish", "inventory": inventory, "offer": offer, "publish": publish, "draft": draft}


def run_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload or {})
    action = str(payload.get("action") or "draft").strip().lower()
    if action in {"oauth_status", "oauth_readiness"}:
        return oauth_status(payload)
    if action in {"oauth_authorize_url", "authorization_url", "auth_url"}:
        return oauth_authorize_url(payload)
    if action in {"oauth_exchange_code", "exchange_code", "authorization_code"}:
        return oauth_exchange_code(payload)
    if action in {"oauth_refresh_access_token", "refresh_access_token", "refresh_token"}:
        return oauth_refresh_access_token(payload)
    if action in {"credential_status", "credentials", "readiness"}:
        return credential_status(payload)
    if action in {"setup_status", "setup", "readiness_report"}:
        return setup_status(payload)
    if action in {"seller_prerequisites", "seller_setup", "list_policies", "list_locations"}:
        return seller_prerequisites(payload)
    if action in {"publish_readiness", "approval_readiness", "check_publish_readiness"}:
        if payload.get("draft_id"):
            read = _read_draft_entry(str(payload.get("draft_id") or ""))
            if not read.get("ok"):
                return read
            return build_publish_readiness({"record": read.get("payload") or {}, **payload})
        return build_publish_readiness(payload)
    if action in {"category_suggestions", "suggest_category", "category_lookup"}:
        return category_suggestions(payload)
    if action in {"online_comparable_prices", "online_comps", "comparable_prices", "price_comps"}:
        return online_comparable_prices(payload)
    if action in {"sandbox_pilot", "pilot"}:
        return sandbox_pilot(payload)
    if action in {"draft", "validate", "build"}:
        return build_listing_draft(payload)
    if action in {"approval", "approval_draft", "create_approval_draft"}:
        return create_approval_draft(payload)
    if action in {"append_images", "append_draft_images", "append_images_to_approval_draft"}:
        return append_images_to_approval_draft(payload)
    if action in {"update_draft_text", "update_approval_draft_text"}:
        return update_approval_draft_text(payload)
    if action in {"delete_draft", "delete_approval_draft"}:
        return delete_approval_draft(payload)
    if action in {"prepared_review_packet", "review_packet", "ui_draft_packet", "forgeclaw_packet"}:
        return build_prepared_review_packet(payload)
    if action in {"approve_draft", "approve_approval_draft"}:
        return approve_approval_draft(payload)
    if action in {"refresh_approval_draft_readiness", "refresh_readiness", "check_draft_readiness"}:
        return refresh_approval_draft_readiness(payload)
    if action in {"publish_draft", "publish_approval_draft"}:
        return publish_approval_draft(payload)
    if action in {"draft_repeats", "draft_repeat_summary", "repeat_summary", "ebay_draft_stats"}:
        return draft_repeat_summary(payload)
    if action == "vm_status":
        return aegis_vm_client.vm_status(float(payload.get("timeout_s") or 8.0))
    if action in {"vm_probe", "vm_exec_probe"}:
        return aegis_vm_client.vm_probe(float(payload.get("timeout_s") or 15.0))
    if action == "vm_exec":
        command = str(payload.get("command") or "").strip()
        if not command:
            return {"ok": False, "error": "command_required"}
        return aegis_vm_client.vm_exec(command, title=str(payload.get("title") or "Apollo eBay VM exec"), timeout_s=float(payload.get("timeout_s") or 15.0))
    if action in {"create_inventory_item", "create_or_replace_inventory_item"}:
        return create_or_replace_inventory_item(payload)
    if action == "create_offer":
        return create_offer(payload)
    if action == "publish_offer":
        return publish_offer(payload)
    if action in {"withdraw_offer", "unpublish_offer", "end_offer"}:
        return withdraw_offer(payload)
    if action in {"withdraw_draft", "withdraw_approval_draft", "end_approval_draft"}:
        return withdraw_approval_draft(payload)
    if action in {"full", "full_listing_flow", "list_item"}:
        return full_listing_flow(payload)
    return {"ok": False, "error": "unknown_action", "action": action}
