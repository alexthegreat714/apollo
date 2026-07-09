"""Shared news provenance, deduplication, and standard-candidate gates."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


DOMAIN_TIER_A = (
    "sec.gov",
    "finra.org",
    "cftc.gov",
    "federalreserve.gov",
    "treasury.gov",
    "federalregister.gov",
    "investor.gov",
    "irs.gov",
    "nyse.com",
    "nasdaq.com",
    ".gov",
    ".edu",
)

DOMAIN_TIER_B = (
    "nber.org",
    "ssrn.com",
    "arxiv.org",
    "researchgate.net",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "jstor.org",
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "apnews.com",
)

DOMAIN_TIER_C = (
    "cnbc.com",
    "marketwatch.com",
    "yahoo.com",
    "google.com",
    "cnn.com",
    "investopedia.com",
)

DOMAIN_DENYLIST = (
    "merriam-webster.com",
    "dictionary.cambridge.org",
    "oxfordlearnersdictionaries.com",
    "collinsdictionary.com",
    "wikipedia.org",
    "freepik.com",
    "forum.intraday.my",
    "quora.com",
    "reddit.com/r/",
)

AGGREGATOR_DOMAINS = {
    "feeds.finance.yahoo.com",
    "finance.yahoo.com",
    "news.google.com",
    "google.com",
    "yahoo.com",
}

_PUBLISHER_DOMAIN_HINTS = {
    "reuters": "reuters.com",
    "associated press": "apnews.com",
    "ap": "apnews.com",
    "bloomberg": "bloomberg.com",
    "wall street journal": "wsj.com",
    "financial times": "ft.com",
    "cnbc": "cnbc.com",
    "marketwatch": "marketwatch.com",
}

_TRACKING_QUERY_KEYS = {
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "output",
    "ocid",
    "cmpid",
    "cid",
    "ref",
    "referrer",
    "source",
}

_TITLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}


def normalize_domain(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    try:
        host = str(urlparse(value).hostname or "").strip().lower()
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_matches(domain: str, tokens: Iterable[str]) -> bool:
    value = normalize_domain(domain)
    for raw in tokens:
        token = normalize_domain(raw.lstrip("."))
        if token and (value == token or value.endswith(f".{token}")):
            return True
    return False


def domain_tier(domain: str) -> str:
    if _domain_matches(domain, DOMAIN_DENYLIST):
        return "D"
    if _domain_matches(domain, DOMAIN_TIER_A):
        return "A"
    if _domain_matches(domain, DOMAIN_TIER_B):
        return "B"
    if _domain_matches(domain, DOMAIN_TIER_C):
        return "C"
    return "D"


def source_evidence_lane(row: Dict[str, Any]) -> str:
    domain = normalize_domain(row.get("origin_domain") or row.get("domain") or row.get("url"))
    tier = str(row.get("source_tier") or row.get("tier") or domain_tier(domain)).upper()
    text = f"{row.get('url') or ''} {row.get('title') or row.get('headline') or ''}".lower()
    if tier in {"A", "B"} and (
        "sec.gov" in domain
        or "investor" in domain
        or domain.startswith("ir.")
        or any(token in text for token in ("earnings", "press-release", "8-k", "10-q", "10-k"))
    ):
        return "primary_evidence"
    if tier in {"A", "B"}:
        return "trusted_market_news"
    if tier == "C" or domain in AGGREGATOR_DOMAINS:
        return "market_news_evidence"
    return "other"


def canonicalize_url(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        host = normalize_domain(value)
        query = []
        for key, val in parse_qsl(parsed.query, keep_blank_values=False):
            low = key.lower()
            if low.startswith("utm_") or low in _TRACKING_QUERY_KEYS:
                continue
            query.append((key, val))
        path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
        return urlunparse(("https", host, path, "", urlencode(sorted(query)), ""))
    except Exception:
        return value


def _parse_published(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def freshness_weight(value: Any, *, now: datetime | None = None) -> float:
    published = _parse_published(value)
    if published is None:
        return 0.55
    current = now or datetime.now(timezone.utc)
    age_hours = max(0.0, (current - published).total_seconds() / 3600.0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 48:
        return 0.85
    if age_hours <= 72:
        return 0.65
    if age_hours <= 14 * 24:
        return 0.3
    return 0.1


def _publisher_hint(row: Dict[str, Any]) -> str:
    for key in ("origin_domain", "publisher_domain", "source_domain", "domain"):
        domain = normalize_domain(row.get(key))
        if domain:
            return domain
    source = row.get("source")
    if isinstance(source, dict):
        for key in ("href", "url"):
            domain = normalize_domain(source.get(key))
            if domain:
                return domain
        source = source.get("title") or source.get("name")
    source_name = str(source or row.get("publisher") or "").strip().lower()
    return _PUBLISHER_DOMAIN_HINTS.get(source_name, "")


def _title_tokens(title: Any) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", str(title or "").lower())
    return [token for token in tokens if token not in _TITLE_STOPWORDS and len(token) > 1]


def _title_similarity(left: Any, right: Any) -> float:
    a = set(_title_tokens(left))
    b = set(_title_tokens(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize_news_item(row: Dict[str, Any], *, ticker: str = "") -> Dict[str, Any]:
    item = dict(row or {})
    title = str(item.get("title") or item.get("headline") or "").strip()
    url = canonicalize_url(item.get("url") or item.get("link"))
    publisher_domain = normalize_domain(url)
    origin_domain = _publisher_hint(item) or publisher_domain
    source_tier = str(item.get("source_tier") or item.get("tier") or domain_tier(origin_domain)).upper()
    if source_tier not in {"A", "B", "C", "D"}:
        source_tier = domain_tier(origin_domain)
    published = str(item.get("published_at") or item.get("published") or item.get("date") or "")
    normalized = {
        **item,
        "ticker": str(item.get("ticker") or ticker or "").strip().upper(),
        "title": title,
        "url": url,
        "link": url,
        "published": published,
        "published_at": published,
        "publisher_domain": publisher_domain,
        "origin_domain": origin_domain,
        "source_tier": source_tier,
    }
    normalized["evidence_lane"] = str(item.get("evidence_lane") or source_evidence_lane(normalized))
    fingerprint_text = " ".join(_title_tokens(title)) or url
    normalized["story_fingerprint"] = hashlib.sha256(
        f"{normalized['ticker']}|{fingerprint_text}".encode("utf-8")
    ).hexdigest()[:20]
    return normalized


def cluster_news_items(rows: Iterable[Dict[str, Any]], *, ticker: str = "") -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for raw in rows:
        item = normalize_news_item(raw, ticker=ticker)
        if not item.get("title") and not item.get("url"):
            continue
        published = _parse_published(item.get("published_at"))
        match = None
        for cluster in clusters:
            representative = cluster["representative"]
            other_published = _parse_published(representative.get("published_at"))
            within_window = True
            if published and other_published:
                within_window = abs((published - other_published).total_seconds()) <= 48 * 3600
            same_url = bool(item.get("url") and item.get("url") == representative.get("url"))
            if within_window and (same_url or _title_similarity(item.get("title"), representative.get("title")) >= 0.82):
                match = cluster
                break
        if match is None:
            match = {"representative": item, "items": [], "domains": set()}
            clusters.append(match)
        match["items"].append(item)
        domain = normalize_domain(item.get("origin_domain"))
        if domain:
            match["domains"].add(domain)
        current = match["representative"]
        rank = {"A": 4, "B": 3, "C": 2, "D": 1}
        if rank.get(item.get("source_tier"), 0) > rank.get(current.get("source_tier"), 0):
            match["representative"] = item
    output: List[Dict[str, Any]] = []
    for cluster in clusters:
        representative = dict(cluster["representative"])
        members = list(cluster["items"])
        scores = [float(item.get("sentiment") or item.get("signed_score") or 0.0) for item in members]
        confidences = [float(item.get("sentiment_confidence") or item.get("confidence") or 0.0) for item in members]
        representative["sentiment"] = round(sum(scores) / len(scores), 3) if scores else 0.0
        representative["sentiment_confidence"] = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        representative["cluster_size"] = len(members)
        representative["cluster_domains"] = sorted(cluster["domains"])
        output.append(representative)
    return output


def aggregate_news_evidence(rows: Iterable[Dict[str, Any]], *, ticker: str = "") -> Dict[str, Any]:
    raw_items = [dict(row) for row in rows if isinstance(row, dict)]
    clusters = cluster_news_items(raw_items, ticker=ticker)
    empty = {
        "raw_sentiment": 0.0,
        "weighted_sentiment": 0.0,
        "sentiment_backend": "unknown",
        "sentiment_label": "neutral",
        "sentiment_confidence": 0.0,
        "headline_count": len(raw_items),
        "directional_headline_count": 0,
        "sentiment_consensus_ratio": 0.0,
        "standard_sentiment_eligible": False,
        "unique_story_count": 0,
        "independent_domain_count": 0,
        "source_domains": [],
        "tier_counts": {"A": 0, "B": 0, "C": 0, "D": 0},
        "deduplicated_count": len(raw_items),
        "eligibility_path": "none",
        "standard_cap_reason": "news_evidence_missing",
        "items": [],
    }
    if not clusters:
        return empty

    scores = [float(item.get("sentiment") or 0.0) for item in clusters]
    weighted = [score * freshness_weight(item.get("published_at")) for score, item in zip(scores, clusters)]
    raw_sentiment = round(sum(scores) / len(scores), 3)
    weighted_sentiment = round(sum(weighted) / len(weighted), 3)
    positive = sum(1 for score in scores if score > 0.05)
    negative = sum(1 for score in scores if score < -0.05)
    directional = positive + negative
    consensus = round(max(positive, negative) / directional, 3) if directional else 0.0
    label = "positive" if weighted_sentiment > 0.08 else ("negative" if weighted_sentiment < -0.08 else "neutral")

    backends = {str(item.get("sentiment_backend") or "keyword_fallback") for item in clusters}
    backend = next(iter(backends)) if len(backends) == 1 else "mixed"
    observed_confidence = [float(item.get("sentiment_confidence") or 0.0) for item in clusters]
    derived_confidence = min(
        0.95,
        0.35 + min(abs(weighted_sentiment), 1.0) * 0.35 + consensus * 0.2 + min(len(clusters), 4) * 0.025,
    )
    confidence = round(max((sum(observed_confidence) / len(observed_confidence)) if observed_confidence else 0.0, derived_confidence), 3)

    domain_values = set()
    for item in clusters:
        candidates = list(item.get("cluster_domains") or []) or [item.get("origin_domain")]
        for candidate in candidates:
            domain = normalize_domain(candidate)
            if domain and domain not in AGGREGATOR_DOMAINS:
                domain_values.add(domain)
    domains = sorted(domain_values)
    tiers = {"A": 0, "B": 0, "C": 0, "D": 0}
    for item in clusters:
        tier = str(item.get("source_tier") or "D").upper()
        tiers[tier if tier in tiers else "D"] += 1
    fresh_48 = sum(1 for item in clusters if freshness_weight(item.get("published_at")) >= 0.85)
    fresh_72 = sum(1 for item in clusters if freshness_weight(item.get("published_at")) >= 0.65)

    trusted_path = (
        tiers["A"] + tiers["B"] >= 1
        and len(domains) >= 2
        and len(clusters) >= 2
        and fresh_72 >= 2
        and consensus >= 0.70
        and label in {"positive", "negative"}
    )
    diverse_c_path = (
        tiers["C"] >= 2
        and len(domains) >= 3
        and len(clusters) >= 2
        and fresh_48 >= 2
        and consensus >= 0.75
        and confidence >= 0.72
        and label in {"positive", "negative"}
    )
    eligible = trusted_path or diverse_c_path
    eligibility_path = "trusted_ab" if trusted_path else ("diverse_c" if diverse_c_path else "none")
    if eligible:
        cap_reason = ""
    elif not domains:
        cap_reason = "aggregator_only_news"
    elif len(clusters) < 2:
        cap_reason = "duplicate_heavy_news"
    elif len(domains) < 2:
        cap_reason = "insufficient_independent_domains"
    elif fresh_72 < 2:
        cap_reason = "stale_news_evidence"
    elif consensus < 0.70 or label == "neutral":
        cap_reason = "mixed_or_neutral_news"
    else:
        cap_reason = "source_diversity_below_standard"

    return {
        **empty,
        "raw_sentiment": raw_sentiment,
        "weighted_sentiment": weighted_sentiment,
        "sentiment_backend": backend,
        "sentiment_label": label,
        "sentiment_confidence": confidence,
        "headline_count": len(raw_items),
        "directional_headline_count": directional,
        "sentiment_consensus_ratio": consensus,
        "standard_sentiment_eligible": eligible,
        "unique_story_count": len(clusters),
        "independent_domain_count": len(domains),
        "source_domains": domains,
        "tier_counts": tiers,
        "deduplicated_count": max(0, len(raw_items) - len(clusters)),
        "eligibility_path": eligibility_path,
        "standard_cap_reason": cap_reason,
        "items": clusters[:8],
    }
