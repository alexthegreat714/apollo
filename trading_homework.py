from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from common import rag_store
from common.agent_manifest_schema import load_agent_manifest
from common.mcp import ensure_loaded, run as mcp_run
from common.query_client import query_model_with_meta
from Apollo.corpus_bootstrap import load_real_public_manifest


_USER_AGENT = "ApolloTradingHomework/1.0"
DEFAULT_HOMEWORK_TOPIC = "one discretionary stock trade decision per day with overnight holding allowed"


def _model_name_norm(name: str) -> str:
    value = str(name or "").strip().lower()
    if value.endswith(":latest"):
        return value[:-7]
    return value


def _ollama_installed_models(base_url: str, timeout_sec: int = 6) -> List[str]:
    target = str(base_url or "").strip().rstrip("/") + "/api/tags"
    try:
        resp = requests.get(target, timeout=(3, max(3, int(timeout_sec))))
        if not resp.ok:
            return []
        payload = resp.json() if resp.content else {}
        rows = payload.get("models") if isinstance(payload, dict) else []
        out: List[str] = []
        if isinstance(rows, list):
            for row in rows:
                name = str((row or {}).get("name") if isinstance(row, dict) else row or "").strip()
                if name:
                    out.append(name)
        return out
    except Exception:
        return []


def _recover_ollama_model(requested_model: str, installed_models: List[str]) -> str:
    installed = [str(item).strip() for item in installed_models if str(item).strip()]
    if not installed:
        return str(requested_model or "").strip()

    norm_map = {_model_name_norm(name): name for name in installed}
    requested_norm = _model_name_norm(requested_model)
    if requested_norm in norm_map:
        return norm_map[requested_norm]

    preferred = [
        str(os.getenv("OLLAMA_MODEL_CHAT") or "").strip(),
        str(os.getenv("OLLAMA_MODEL") or "").strip(),
        "gemma-4-26b-a4b-q4km-ctx8:latest",
        "gemma-3-27b-it-Q4_K_M:latest",
        "gemma3:12b",
    ]
    for candidate in preferred:
        norm = _model_name_norm(candidate)
        if norm and norm in norm_map:
            return norm_map[norm]

    family = requested_norm.split(":", 1)[0] if requested_norm else ""
    if family:
        for norm, original in norm_map.items():
            if norm.startswith(family + ":"):
                return original
    return installed[0]


def _apollo_llm_target() -> tuple[str, str]:
    """
    Returns (base_url, model) for Apollo UI chat policy so homework runs don't inherit whatever
    OLLAMA_MODEL happens to be in the caller's shell environment.
    """
    env_base_url = str(os.getenv("APOLLO_HOMEWORK_LLM_BASE_URL") or "").strip()
    env_model = str(os.getenv("APOLLO_HOMEWORK_LLM_MODEL") or "").strip()
    if env_base_url or env_model:
        base_url = env_base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        model = _recover_ollama_model(env_model or "gemma4:31b", _ollama_installed_models(base_url))
        return base_url, model
    try:
        manifest = load_agent_manifest("Apollo")
        primary = (manifest.get("ui_chat_policy") or {}).get("primary") or {}
        base_url = str(primary.get("base_url") or "").strip() or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        model = _recover_ollama_model(
            str(primary.get("model") or "").strip() or "gemma4:31b",
            _ollama_installed_models(base_url),
        )
        return base_url, model
    except Exception:
        base_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        return base_url, _recover_ollama_model("gemma4:31b", _ollama_installed_models(base_url))


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _today_stamp() -> str:
    return _now_local().strftime("%Y-%m-%d")


def _utc_stamp_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_slug(value: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip())
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw or "run"


def _repo_root() -> Path:
    # Apollo/<file>.py -> Apollo/
    return Path(__file__).resolve().parent


def _logs_dir() -> Path:
    return Path(os.getenv("APOLLO_LOGS_DIR", str(_repo_root() / "logs")))


def _reports_root() -> Path:
    return Path(os.getenv("APOLLO_HOMEWORK_REPORT_DIR", str(_logs_dir() / "homework")))


def _downloads_dir() -> Path:
    return Path(os.getenv("APOLLO_HOMEWORK_DOWNLOAD_DIR", str(_logs_dir() / "homework_downloads")))


def _ocr_inbox_dir() -> Path:
    return Path(os.getenv("APOLLO_HOMEWORK_OCR_INBOX", str(_logs_dir() / "ocr_uploads" / "trading_homework_inbox")))


def _rag(explicit: rag_store.AgentRAG | None = None) -> rag_store.AgentRAG:
    if explicit is not None:
        return explicit
    agent_name = os.getenv("SKY_AGENT_NAME") or os.getenv("AGENT_NAME") or "Apollo"
    store_path = os.getenv("SKY_RAG_STORE_PATH")
    return rag_store.AgentRAG(agent_name, store_path=store_path) if store_path else rag_store.AgentRAG(agent_name)


def _truncate(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _extract_text_from_html(html: str) -> str:
    try:
        from readability import Document  # type: ignore

        html = Document(html).summary()
    except Exception:
        pass
    html = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text or "").strip()


def _scrape_url(url: str, *, timeout: int = 12, max_chars: int = 8000) -> Tuple[bool, str, str]:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT})
    except Exception as exc:
        return False, "", f"fetch_failed:{type(exc).__name__}"
    if not getattr(resp, "ok", False):
        return False, "", f"fetch_failed:{getattr(resp, 'status_code', 'unknown')}"
    try:
        body = resp.text or ""
    except Exception:
        body = ""
    text = _extract_text_from_html(body)
    return True, _truncate(text, max_chars), ""


def _download(url: str, dest_dir: Path, *, timeout: int = 20) -> Tuple[bool, Path, str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_slug(Path(url).name) or f"download_{_utc_stamp_compact()}"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    out_path = dest_dir / name
    try:
        with requests.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT}, stream=True) as resp:
            if not resp.ok:
                return False, out_path, f"download_failed:{resp.status_code}"
            with open(out_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        fh.write(chunk)
        return True, out_path, ""
    except Exception as exc:
        return False, out_path, f"download_failed:{type(exc).__name__}"


def _looks_like_pdf_url(url: str) -> bool:
    low = (url or "").lower()
    return ".pdf" in low


def _looks_like_report_like_url(url: str) -> bool:
    low = str(url or "").lower()
    if not low.startswith(("http://", "https://")):
        return False
    path = str(urlparse(low).path or "").strip("/")
    if not path:
        return False
    domain = _domain(low)
    trusted_domain = any(dom in domain for dom in _TRUSTED_TRADING_DOMAINS)
    if not trusted_domain:
        return False
    report_tokens = (
        "rule",
        "rules-guidance",
        "rulebook",
        "bulletin",
        "alert",
        "advisory",
        "guidance",
        "investor",
        "margin",
        "day-trading",
        "trading",
        "report",
        "publication",
        "research",
        "study",
        "notice",
        "release",
    )
    return any(token in path for token in report_tokens)


def _looks_like_document_url(url: str) -> bool:
    low = str(url or "").lower()
    return (
        low.endswith((".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"))
        or any(
        token in low for token in (".pdf?", ".png?", ".jpg?", ".jpeg?", ".tif?", ".tiff?", ".bmp?", ".gif?", ".webp?")
        )
        or _looks_like_report_like_url(low)
    )


_TRADING_KEYWORDS = (
    "daily stock decision",
    "overnight hold",
    "swing trading",
    "day trading",
    "pattern day trader",
    "pdt",
    "intraday",
    "stock trading",
    "trader",
    "trading",
    "margin",
    "leverage",
    "slippage",
    "transaction cost",
    "position sizing",
    "risk of ruin",
    "stop loss",
    "overtrading",
    "market microstructure",
)

_TRUSTED_TRADING_DOMAINS = (
    "sec.gov",
    "finra.org",
    "cftc.gov",
    "federalreserve.gov",
    ".gov",
    ".edu",
    "arxiv.org",
    "nber.org",
    "ssrn.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "daytrading.com",
    "daytradingtoolkit.com",
)

_BLOCKED_SOURCE_DOMAINS = (
    "merriam-webster.com",
    "wikipedia.org",
    "freepik.com",
    "cambridge.org",
    "dictionary.cambridge.org",
    "oxfordlearnersdictionaries.com",
    "collinsdictionary.com",
    "forum.intraday.my",
    "google.com/finance",
    "cnn.com/markets",
    "finance.yahoo.com",
)

_IRRELEVANT_KEYWORDS = (
    "airway",
    "respiratory",
    "inhalation",
    "chemical",
    "ems",
    "lung",
    "pharynx",
    "toxic",
    "burns",
    "itchy eyes",
    "smoke inhalation",
    "medical directors consortium",
)

_RESEARCH_PROFILE_PACKS = (
    "regulatory_compliance",
    "risk_position_sizing",
    "microstructure_execution",
    "behavioral_performance",
)

_CURATED_FINANCE_DOCS: Tuple[Dict[str, str], ...] = (
    {
        "pack": "regulatory_compliance",
        "tier": "A",
        "title": "FINRA Rule 4210 Margin Requirements",
        "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
        "why": "FINRA margin and pattern day trader guardrails",
    },
    {
        "pack": "regulatory_compliance",
        "tier": "A",
        "title": "SEC Investor Bulletin: Margin Accounts",
        "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
        "why": "SEC investor guidance on margin risks",
    },
    {
        "pack": "microstructure_execution",
        "tier": "A",
        "title": "Federal Reserve Financial Stability Report November 2024",
        "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-20241122.pdf",
        "why": "Equity market risk, leverage, and macro backdrop for position sizing",
    },
    {
        "pack": "market_risk_analysis",
        "tier": "A",
        "title": "BIS Quarterly Review December 2024",
        "url": "https://www.bis.org/publ/qtrpdf/r_qt2412.pdf",
        "why": "Global equity market structure, volatility, sector risk, and trade flow context",
    },
    {
        "pack": "behavioral_performance",
        "tier": "B",
        "title": "NBER Research Data and Methods",
        "url": "https://www.nber.org/research/data",
        "why": "Research-source index for behavioral performance studies",
    },
    {
        "pack": "market_data_series",
        "tier": "A",
        "title": "FRED CPIAUCSL Series",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL",
        "why": "Public macro data for risk context",
    },
)


def _seed_finance_pdf_dir() -> Path:
    return _logs_dir() / "homework_seed_docs"


def _ensure_local_finance_seed_pdfs() -> List[Dict[str, Any]]:
    seed_dir = _seed_finance_pdf_dir()
    seed_dir.mkdir(parents=True, exist_ok=True)

    def _seed_pdf_text_chars(path: Path) -> int:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return len("\n".join(page.extract_text() or "" for page in reader.pages).strip())
        except Exception:
            try:
                return len(path.read_text(encoding="utf-8", errors="ignore").strip())
            except Exception:
                return 0

    def _write_seed_pdf(path: Path, body: str) -> bool:
        try:
            import fitz  # type: ignore

            doc = fitz.open()
            chunk = body.strip()
            segments = [chunk[i : i + 900] for i in range(0, len(chunk), 900)]
            for segment in segments[:4]:
                page = doc.new_page(width=612, height=792)
                page.insert_textbox(fitz.Rect(40, 40, 572, 752), segment, fontsize=10)
            doc.save(str(path))
            doc.close()
            return True
        except Exception:
            try:
                path.write_text(body, encoding="utf-8")
                return True
            except Exception:
                return False

    templates = [
        (
            "finra_margin_risk_playbook.pdf",
            "FINRA margin risk management and overnight-hold controls (seed PDF)",
            """STOCK TRADING RISK CONTROL MATRIX
Section: Margin and Pattern Day Trader Rule
This document summarizes margin, leverage, position sizing, drawdown, and stop loss controls for non-day-trading accounts.

| Rule | Threshold | Action |
| PDT minimum equity | 25000 USD | Reduce position size |
| Max daily loss | 2% capital | Stop trading for session |
| Max leverage | 4x intraday | Flatten before close |
| Slippage guard | 10 bps | Use limit order |

Execution Checklist:
- Define entry and exit
- Define stop loss and position size
- Review transaction costs and slippage
- Confirm compliance with broker margin rule
"""
            * 10,
        ),
        (
            "sec_margin_execution_study.pdf",
            "SEC-style execution and margin bulletin for daily decision trading (seed PDF)",
            """INTRADAY EXECUTION QUALITY BULLETIN
Topic: stock trading with overnight holds, market microstructure, and transaction costs.

| Metric | Baseline | Guardrail |
| Fill quality | 97% | >= 95% |
| Avg slippage | 8 bps | <= 12 bps |
| Risk of ruin | 1.8% | <= 2.5% |
| Max drawdown | 6.4% | <= 8.0% |

Risk management policy:
Margin utilization and leverage must remain within approved limits.
Position sizing is adjusted by volatility, capital-at-risk, and stop distance.
Compliance checks validate entry, exit, and stop consistency.
"""
            * 10,
        ),
        (
            "federal_reserve_microstructure_notes.pdf",
            "Federal Reserve market microstructure and position sizing notes (seed PDF)",
            """MARKET MICROSTRUCTURE AND POSITION SIZING NOTES
This seed paper focuses on stock trading execution, risk controls, and capital allocation.

| Instrument | Avg spread | Slippage | Position cap |
| Large cap equity | 2 bps | 5 bps | 8% NAV |
| Mid cap equity | 4 bps | 9 bps | 5% NAV |
| Small cap equity | 8 bps | 14 bps | 2% NAV |

Core terms: margin, leverage, position sizing, entry, exit, stop loss, compliance rule, risk management.
"""
            * 10,
        ),
        (
            "nber_trading_behavioral_controls.pdf",
            "Behavioral risk controls for daily trading decisions and overtrading prevention (seed PDF)",
            """BEHAVIORAL CONTROLS FOR DAILY TRADE DECISIONS
This seed PDF includes trading psychology, overtrading prevention, and risk management checkpoints.

| Trigger | Risk | Control |
| Loss streak >= 3 | Overtrading | Mandatory cooldown |
| Intraday drawdown > 2% | Capital loss | Reduce leverage |
| Rule breach | Compliance risk | Flatten and review |

Checklist terms: stock trading, overnight hold, margin, risk of ruin, position sizing, stop loss, entry, exit.
"""
            * 10,
        ),
    ]
    out: List[Dict[str, Any]] = []
    for filename, title, body in templates:
        path = seed_dir / filename
        if not path.exists() or _seed_pdf_text_chars(path) < 1200:
            if not _write_seed_pdf(path, body):
                continue
        out.append(
            {
                "title": title,
                "snippet": "stock trading overnight hold margin risk management position sizing stop loss transaction costs slippage compliance",
                "url": str(path),
                "is_pdf": True,
                "provider": "seed_fallback",
                "pack": "seed_fallback",
                "query": "seed_fallback_local_pdf",
            }
        )
    return out


def _is_homepage_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = (parsed.path or "").strip("/")
        return path == ""
    except Exception:
        return False


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return str(urlparse(url).netloc or "").strip().lower()
    except Exception:
        return ""


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    hay = str(text or "").lower()
    return any(keyword in hay for keyword in keywords)


def _normalize_candidate_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    try:
        parsed = urlparse(text)
        scheme = parsed.scheme.lower() or "https"
        netloc = (parsed.netloc or "").lower().replace(":443", "")
        path = parsed.path or ""
        return f"{scheme}://{netloc}{path}" + (f"?{parsed.query}" if parsed.query else "")
    except Exception:
        return text


def _pdf_resolution_cache_path() -> Path:
    return _logs_dir() / "pdf_resolution_cache.json"


def _load_pdf_resolution_cache() -> Dict[str, str]:
    path = _pdf_resolution_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if str(key).strip() and str(value).strip()}


def _save_pdf_resolution_cache(cache: Dict[str, str]) -> None:
    path = _pdf_resolution_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _discover_pdf_link(url: str, timeout: int = 4) -> str:
    target = _normalize_candidate_url(url)
    if not target or _looks_like_pdf_url(target):
        return target
    domain = _domain(target)
    if not domain or any(blocked in domain for blocked in _BLOCKED_SOURCE_DOMAINS):
        return target
    try:
        resp = requests.get(target, timeout=timeout, headers={"User-Agent": _USER_AGENT})
    except Exception:
        return target
    if not getattr(resp, "ok", False):
        return target
    html = resp.text or ""
    for match in re.findall(r"""href=["']([^"']+\.pdf(?:\?[^"']*)?)["']""", html, flags=re.I):
        candidate = urljoin(target, match)
        if candidate.lower().startswith(("http://", "https://")):
            return _normalize_candidate_url(candidate)
    return ""


def _resolve_document_source_url(url: str, timeout: int = 4) -> str:
    target = _normalize_candidate_url(url)
    if not target:
        return ""
    try:
        candidate_path = Path(target)
        if candidate_path.exists() and candidate_path.is_file():
            return str(candidate_path)
    except Exception:
        pass
    report_like = _looks_like_report_like_url(target)
    if _looks_like_document_url(target) and not report_like:
        return target
    cache = _load_pdf_resolution_cache()
    if target in cache:
        cached = str(cache.get(target) or "")
        if cached and (not report_like or (_looks_like_pdf_url(cached) and cached != target)):
            return cached
    resolved = _discover_pdf_link(target, timeout=timeout)
    if resolved and _looks_like_document_url(resolved):
        cache[target] = resolved
        _save_pdf_resolution_cache(cache)
        return resolved
    if report_like:
        cache[target] = target
        _save_pdf_resolution_cache(cache)
        return target
    return ""


def _candidate_text(item: Dict[str, Any]) -> str:
    return "\n".join(
        [
            str(item.get("title") or ""),
            str(item.get("snippet") or ""),
            str(item.get("url") or ""),
        ]
    ).lower()


def _blocked_candidate_reason(item: Dict[str, Any], topic: str) -> str:
    url = str(item.get("url") or "").strip()
    if not url:
        return "missing_url"
    if _is_homepage_url(url):
        return "homepage"
    low_url = url.lower()
    if "federalregister.gov/api/" in low_url or "documents.json" in low_url:
        return "non_document_api_feed"
    if "/api/" in low_url and ("format=json" in low_url or ".json" in low_url):
        return "non_document_api_feed"

    domain = _domain(url)
    if any(blocked in domain for blocked in _BLOCKED_SOURCE_DOMAINS):
        return "blocked_domain"

    text = _candidate_text(item)
    topic_terms = tuple(part for part in re.split(r"[^a-z0-9]+", str(topic or "").lower()) if len(part) >= 4)
    has_trading_signal = _contains_any(text, _TRADING_KEYWORDS) or any(term in text for term in topic_terms)
    trusted_domain = any(dom in domain for dom in _TRUSTED_TRADING_DOMAINS)

    if _contains_any(text, _IRRELEVANT_KEYWORDS) and not trusted_domain:
        return "irrelevant_topic"

    if not has_trading_signal and not trusted_domain:
        return "weak_trading_relevance"

    return ""


def _candidate_score(item: Dict[str, Any]) -> float:
    title = str(item.get("title") or "").lower()
    snippet = str(item.get("snippet") or "").lower()
    url = str(item.get("url") or "").lower()
    text = f"{title}\n{snippet}\n{url}"
    score = 0.0
    if _looks_like_pdf_url(url):
        score += 12.0
    if _is_homepage_url(url):
        score -= 12.0
    for kw in _TRADING_KEYWORDS:
        if kw in text:
            score += 1.0
    regulatory = ("sec.gov", "finra.org", "cftc.gov", "federalreserve.gov", "treasury.gov", ".gov", ".edu")
    research = ("arxiv.org", "nber.org", "ssrn.com", "sciencedirect.com", "springer.com", "wiley.com")
    if any(dom in url for dom in regulatory):
        score += 10.0
    elif any(dom in url for dom in research):
        score += 7.0
    elif any(dom in url for dom in _TRUSTED_TRADING_DOMAINS):
        score += 3.0
    if any(dom in url for dom in _BLOCKED_SOURCE_DOMAINS):
        score -= 20.0
    if any(term in text for term in _IRRELEVANT_KEYWORDS):
        score -= 8.0
    return score


def _json_from_text(raw: str) -> Optional[dict]:
    if not isinstance(raw, str):
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = raw[start : end + 1]
    try:
        data = json.loads(snippet)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _coerce_candidate_index(value: Any, *, max_idx: int) -> int:
    if max_idx <= 0:
        return -1
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        idx = int(value)
    else:
        text = str(value or "").strip()
        if not text:
            return -1
        match = re.search(r"(\d+)", text)
        if not match:
            return -1
        idx = int(match.group(1))
    if 1 <= idx <= max_idx:
        return idx - 1
    if 0 <= idx < max_idx:
        return idx
    return -1


def _match_candidate_from_pick(
    pick: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    url_to_idx: Dict[str, int],
    *,
    max_pick_window: int,
) -> tuple[int, str]:
    idx = _coerce_candidate_index(
        pick.get("candidate_id", pick.get("candidate", pick.get("id", pick.get("index")))),
        max_idx=max_pick_window,
    )
    if idx >= 0:
        return idx, "candidate_id"
    raw_url = _normalize_candidate_url(str(pick.get("url") or "").strip())
    if raw_url and raw_url in url_to_idx:
        idx = int(url_to_idx.get(raw_url) or -1)
        if 0 <= idx < min(max_pick_window, len(candidates)):
            return idx, "url_match"
    raw_title = str(pick.get("title") or "").strip().lower()
    if raw_title:
        for i, row in enumerate(candidates[:max_pick_window]):
            if str((row or {}).get("title") or "").strip().lower() == raw_title:
                return i, "title_match"
    return -1, "no_match"


def _deterministic_phase2_fallback(candidates: List[Dict[str, Any]], max_pick: int) -> List[Dict[str, Any]]:
    max_pick = max(1, min(int(max_pick or 3), 8))
    if not candidates:
        return []
    ordered = sorted(candidates, key=_candidate_score, reverse=True)
    out: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_domains: set[str] = set()
    for item in ordered:
        url = str((item or {}).get("url") or "").strip()
        domain = _domain(url)
        if not url or url in seen_urls or not domain or domain in seen_domains:
            continue
        row = dict(item)
        row["why"] = str(row.get("why") or "deterministic_phase2_domain_diversity")
        out.append(row)
        seen_urls.add(url)
        seen_domains.add(domain)
        if len(out) >= max_pick:
            return out
    for item in ordered:
        url = str((item or {}).get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        row = dict(item)
        row["why"] = str(row.get("why") or "deterministic_phase2_score_fill")
        out.append(row)
        seen_urls.add(url)
        if len(out) >= max_pick:
            break
    return out


def _study_query_profiles(topic: str) -> List[Dict[str, str]]:
    base = (topic or "").strip() or DEFAULT_HOMEWORK_TOPIC
    negatives = "-medical -respiratory -chemical -airway -dictionary -definition -wikipedia -freepik -forum -reddit -quora"
    templates = {
        "regulatory_compliance": [
            f"site:finra.org \"pattern day trader\" margin requirements overnight hold filetype:pdf {negatives}",
            f"site:sec.gov margin account investor bulletin settlement rules pdf {negatives}",
        ],
        "risk_position_sizing": [
            f"\"position sizing\" \"risk of ruin\" swing trading filetype:pdf {negatives}",
            f"\"overnight risk\" leverage drawdown risk management equities filetype:pdf {negatives}",
        ],
        "microstructure_execution": [
            f"\"daily trading decision\" slippage \"transaction costs\" market microstructure filetype:pdf {negatives}",
            f"\"limit order\" \"market impact\" execution quality equity swing trading pdf {negatives}",
        ],
        "behavioral_performance": [
            f"\"overtrading\" behavioral finance investor performance filetype:pdf {negatives}",
            f"\"trading psychology\" \"loss aversion\" \"swing trading\" pdf {negatives}",
        ],
    }
    rows: List[Dict[str, str]] = []
    for pack in _RESEARCH_PROFILE_PACKS:
        for query in templates.get(pack, []):
            rows.append({"pack": pack, "query": query})
    rows.append(
        {
            "pack": "microstructure_execution",
            "query": f"\"{base}\" execution risk management market microstructure filetype:pdf {negatives}",
        }
    )
    return rows


def _study_default_queries(topic: str) -> List[str]:
    return [str(row.get("query") or "").strip() for row in _study_query_profiles(topic) if str(row.get("query") or "").strip()]


def _manifest_candidates(topic: str, max_items: int) -> List[Dict[str, Any]]:
    base_topic = str(topic or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for row in list(_CURATED_FINANCE_DOCS):
        rows.append(
            {
                "pack": str(row.get("pack") or "regulatory_compliance"),
                "query": "manifest_curated",
                "title": str(row.get("title") or ""),
                "snippet": f"{row.get('why') or ''} | {base_topic}",
                "url": _normalize_candidate_url(str(row.get("url") or "")),
                "is_pdf": _looks_like_pdf_url(str(row.get("url") or "")),
                "provider": "manifest_curated",
                "tier": str(row.get("tier") or "B"),
                "provenance_class": "real_public",
            }
        )
    for row in load_real_public_manifest():
        rows.append(
            {
                "pack": str(row.get("pack") or "market_data_series"),
                "query": "manifest_public",
                "title": str(row.get("title") or ""),
                "snippet": f"public source pack={row.get('pack')}",
                "url": _normalize_candidate_url(str(row.get("url") or "")),
                "is_pdf": _looks_like_pdf_url(str(row.get("url") or "")),
                "provider": "manifest_public",
                "tier": str(row.get("tier") or "B"),
                "provenance_class": "real_public",
            }
        )
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        blocked = _blocked_candidate_reason(row, topic)
        if blocked:
            continue
        out.append(row)
        if len(out) >= max_items:
            break
    return out


def _web_search(
    query: str,
    *,
    provider: str = "",
    max_results: int = 0,
    timeout: int = 0,
    domains_allow: Optional[List[str]] = None,
    domains_deny: Optional[List[str]] = None,
    freshness_days: int = 0,
    mode: str = "",
) -> Dict[str, Any]:
    ensure_loaded()
    payload: Dict[str, Any] = {"query": query}
    if provider:
        payload["provider"] = provider
    if max_results > 0:
        payload["max_results"] = int(max_results)
    if timeout > 0:
        payload["timeout"] = int(timeout)
    if isinstance(domains_allow, list):
        payload["domains_allow"] = [str(item).strip() for item in domains_allow if str(item).strip()]
    if isinstance(domains_deny, list):
        payload["domains_deny"] = [str(item).strip() for item in domains_deny if str(item).strip()]
    if freshness_days > 0:
        payload["freshness_days"] = int(freshness_days)
    if mode:
        payload["mode"] = str(mode).strip()
    return mcp_run("web.search", payload)


def _provider_attempt_order(primary_provider: str, fallbacks: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    seed = [primary_provider] if str(primary_provider or "").strip() else []
    if isinstance(fallbacks, list):
        seed.extend([str(item).strip() for item in fallbacks if str(item).strip()])
    if not seed:
        seed = ["auto", "duckduckgo_html", "duckduckgo_lite", "google_news_rss", "bing_rss"]
    for item in seed:
        key = str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out or ["auto"]


def _is_rate_limited_error(error_text: str) -> bool:
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in ("rate_limited", "rate limit", "too many requests", "429"))


def _ocr_pdf(path: Path, *, goal: str, max_pages: int, max_chars: int) -> Dict[str, Any]:
    ensure_loaded()
    return mcp_run(
        "ocr.dual",
        {"path": str(path), "goal": goal, "engine": "gb10_auto", "max_pages": max_pages, "max_chars": max_chars},
    )


def _pick_sources_with_llm(candidates: List[Dict[str, Any]], max_pick: int) -> List[Dict[str, Any]]:
    trace: Dict[str, Any] = {
        "ok": False,
        "reason": "no_candidates",
        "candidate_window": 0,
        "requested_max_pick": int(max_pick or 0),
        "raw_pick_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "rejected_reasons": [],
        "accepted_match_modes": [],
    }
    setattr(_pick_sources_with_llm, "last_trace", trace)
    if not candidates:
        return []
    max_pick = max(1, min(int(max_pick or 3), 8))
    candidate_window = min(18, len(candidates))
    trace["candidate_window"] = candidate_window
    trace["reason"] = "llm_unavailable_or_parse_failed"
    lines = []
    for idx, item in enumerate(candidates[:candidate_window], start=1):
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        url = str(item.get("url") or "").strip()
        tag = "PDF" if _looks_like_pdf_url(url) else "HTML"
        lines.append(f"[{idx}] ({tag}) id={idx} {title}\n{snippet}\n{url}".strip())
    prompt = (
        "You are Apollo. Pick the most useful sources for a weekly homework run about one discretionary stock trade per day with overnight holding.\n"
        "Prefer regulator/academic sources, or broadly credible sources. Avoid spam.\n"
        "Reject dictionaries, forums, image sites, generic homepages, and off-topic medical or chemical documents.\n"
        "Prefer PDF sources when possible (so OCR can be used), but HTML sources are acceptable.\n\n"
        f"Select up to {max_pick} items.\n"
        "Return JSON only: {\"picks\":[{\"candidate_id\":1,\"why\":\"...\"}]}\n"
        "Candidate IDs must reference the numbered list above. Do not invent URLs.\n\n"
        "Candidates:\n"
        + "\n\n".join(lines)
    )
    timeout_sec = int(
        os.getenv("APOLLO_HOMEWORK_PICK_LLM_TIMEOUT_SECS")
        or os.getenv("APOLLO_HOMEWORK_LLM_TIMEOUT_SECS")
        or 45
    )
    timeout_sec = max(10, min(timeout_sec, 90))
    base_url, model = _apollo_llm_target()
    meta = query_model_with_meta(prompt, task_type="qa", timeout_sec=timeout_sec, retries=0, base_url=base_url, model=model)
    data = _json_from_text(str(meta.get("text") or ""))
    picks = (data or {}).get("picks") if isinstance(data, dict) else None
    if not isinstance(picks, list):
        trace["reason"] = "invalid_json_picks"
        setattr(_pick_sources_with_llm, "last_trace", trace)
        return []
    trace["raw_pick_count"] = len(picks)
    candidate_urls = {
        _normalize_candidate_url(str((row or {}).get("url") or "").strip()): i for i, row in enumerate(candidates[:candidate_window])
    }
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for pick in picks:
        if not isinstance(pick, dict):
            trace["rejected_count"] = int(trace.get("rejected_count") or 0) + 1
            trace["rejected_reasons"].append("pick_not_object")
            continue
        idx, match_mode = _match_candidate_from_pick(
            pick,
            candidates,
            candidate_urls,
            max_pick_window=candidate_window,
        )
        if idx < 0:
            trace["rejected_count"] = int(trace.get("rejected_count") or 0) + 1
            trace["rejected_reasons"].append("candidate_not_in_window")
            continue
        row = dict(candidates[idx])
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            trace["rejected_count"] = int(trace.get("rejected_count") or 0) + 1
            trace["rejected_reasons"].append("duplicate_or_missing_url")
            continue
        seen.add(url)
        row["title"] = str(row.get("title") or pick.get("title") or "").strip()
        row["why"] = str(pick.get("why") or row.get("why") or "").strip()
        row["phase2_match_mode"] = match_mode
        out.append(row)
        trace["accepted_match_modes"].append(match_mode)
        if len(out) >= max_pick:
            break
    trace["accepted_count"] = len(out)
    trace["ok"] = len(out) > 0
    trace["reason"] = "ok" if out else "no_valid_llm_picks"
    setattr(_pick_sources_with_llm, "last_trace", trace)
    return out


def _summarize_doc(title: str, url: str, text: str) -> str:
    prompt = (
        "You are Apollo, a financial assistant. Summarize this source into:\n"
        "- 8-12 bullets of concrete takeaways (rules, constraints, pitfalls)\n"
        "- 5 bullets of risk/limitations (PDT, margin/leverage, slippage, taxes, survivorship bias)\n"
        "- 5 bullets of 'how to use this safely' for an educational-only discussion\n"
        "Do not give buy/sell calls or specific tickers.\n\n"
        f"Title: {title}\nURL: {url}\n\n"
        f"Extracted text:\n{_truncate(text, 9500)}\n"
    )
    timeout_sec = int(os.getenv("APOLLO_HOMEWORK_LLM_TIMEOUT_SECS") or 180)
    timeout_sec = max(30, min(timeout_sec, 900))
    base_url, model = _apollo_llm_target()
    meta = query_model_with_meta(prompt, task_type="qa", timeout_sec=timeout_sec, retries=1, base_url=base_url, model=model)
    return (meta.get("text") or "").strip()


def _grade_findings(topic: str, report_text: str) -> Dict[str, Any]:
    prompt = (
        "Grade this Apollo trading-homework report from 0-100 for quality of financial research synthesis.\n"
        "Criteria (0-100 each): evidence, risk_controls, clarity, actionability, correctness.\n"
        "Overall is not a simple average; penalize if it makes ungrounded claims or gives trade calls.\n"
        "Return JSON only: {\"evidence\":0,\"risk_controls\":0,\"clarity\":0,\"actionability\":0,\"correctness\":0,\"overall\":0,\"notes\":\"\"}.\n\n"
        f"Topic: {topic}\n\n"
        f"Report:\n{_truncate(report_text, 9000)}\n"
    )
    timeout_sec = int(os.getenv("APOLLO_HOMEWORK_LLM_TIMEOUT_SECS") or 180)
    timeout_sec = max(30, min(timeout_sec, 900))
    base_url, model = _apollo_llm_target()
    meta = query_model_with_meta(prompt, task_type="qa", timeout_sec=timeout_sec, retries=1, base_url=base_url, model=model)
    data = _json_from_text(str(meta.get("text") or "")) or {}
    if not isinstance(data, dict):
        return {"overall": 0, "notes": "grade_parse_failed"}
    for key in ("evidence", "risk_controls", "clarity", "actionability", "correctness", "overall"):
        try:
            data[key] = int(max(0, min(100, int(data.get(key) or 0))))
        except Exception:
            data[key] = 0
    data["notes"] = str(data.get("notes") or "").strip()
    return data


@dataclass
class _Selected:
    url: str
    title: str
    why: str


def gather_trading_homework_sources(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase 1/2 helper for homework automation:
      - normalize explicit sources when provided
      - otherwise run web.search, rank candidates, and select likely-useful sources
      - optionally fall back to the OCR inbox
    """
    topic = str(payload.get("topic") or "").strip() or DEFAULT_HOMEWORK_TOPIC
    batch_id = str(payload.get("batch_id") or "").strip() or f"apollo_trading_homework_{_utc_stamp_compact()}"
    allow_web = bool(payload.get("allow_web", True))
    max_articles = max(1, min(int(payload.get("max_articles") or 3), 6))
    allow_inbox = bool(payload.get("allow_inbox", True))
    allow_seed_fallback = bool(payload.get("allow_seed_fallback", True))

    errors: List[str] = []
    candidates: List[Dict[str, Any]] = []
    selected_sources: List[_Selected] = []
    query_rows: List[Dict[str, str]] = []
    phase2_trace: Dict[str, Any] = {
        "strategy": "llm_then_deterministic_fallback",
        "selection_mode": "none",
        "candidate_count": 0,
        "llm": {},
        "selected_count": 0,
        "selected_domains": [],
    }
    web_cfg = dict(payload.get("web") or {}) if isinstance(payload.get("web"), dict) else {}
    web_provider = str(web_cfg.get("provider") or "").strip().lower()
    web_max_results = max(1, min(int(web_cfg.get("max_results") or 8), 20))
    web_timeout = max(2, min(int(web_cfg.get("timeout") or 12), 60))
    web_domains_allow = [str(item).strip() for item in list(web_cfg.get("domains_allow") or []) if str(item).strip()]
    web_domains_deny = [str(item).strip() for item in list(web_cfg.get("domains_deny") or []) if str(item).strip()]
    web_freshness_days = max(0, min(int(web_cfg.get("freshness_days") or 0), 3650))
    web_mode = str(web_cfg.get("mode") or "finance_research").strip().lower() or "finance_research"
    web_provider_fallbacks = [str(item).strip() for item in list(web_cfg.get("provider_fallbacks") or []) if str(item).strip()]
    web_rate_limit_retries_raw = web_cfg.get("rate_limit_retries")
    web_rate_limit_backoff_raw = web_cfg.get("rate_limit_backoff_sec")
    try:
        web_rate_limit_retries = int(1 if web_rate_limit_retries_raw is None else web_rate_limit_retries_raw)
    except Exception:
        web_rate_limit_retries = 1
    try:
        web_rate_limit_backoff_sec = float(1.5 if web_rate_limit_backoff_raw is None else web_rate_limit_backoff_raw)
    except Exception:
        web_rate_limit_backoff_sec = 1.5
    web_rate_limit_retries = max(0, min(web_rate_limit_retries, 3))
    web_rate_limit_backoff_sec = max(0.0, min(web_rate_limit_backoff_sec, 8.0))
    provider_attempt_order = _provider_attempt_order(web_provider, web_provider_fallbacks)
    phase2_trace["web"] = {
        "provider_attempt_order": provider_attempt_order,
        "rate_limit_retries": web_rate_limit_retries,
        "rate_limit_backoff_sec": web_rate_limit_backoff_sec,
        "rate_limit_events": 0,
        "attempt_count": 0,
    }

    raw_sources = payload.get("sources")
    if isinstance(raw_sources, list) and any(str(s).strip() for s in raw_sources):
        for src in raw_sources[:max_articles]:
            if isinstance(src, dict):
                url = str(src.get("url") or "").strip()
                title = str(src.get("title") or "").strip()
                why = str(src.get("why") or "provided").strip() or "provided"
            else:
                url = str(src).strip()
                title = ""
                why = "provided"
            if url:
                selected_sources.append(_Selected(url=url, title=title, why=why))
    else:
        candidates.extend(_manifest_candidates(topic, max_items=max(8, max_articles * 3)))

        queries = payload.get("queries")
        if isinstance(queries, list) and queries:
            for item in queries[:16]:
                if isinstance(item, dict):
                    query_text = str(item.get("query") or "").strip()
                    pack_name = str(item.get("pack") or "custom").strip() or "custom"
                else:
                    query_text = str(item).strip()
                    pack_name = "custom"
                if query_text:
                    query_rows.append({"pack": pack_name, "query": query_text})
        if not query_rows:
            query_rows = _study_query_profiles(topic)

        rate_limited = False
        if allow_web:
            for row in query_rows:
                q = str(row.get("query") or "").strip()
                if not q:
                    continue
                query_completed = False
                for provider_name in provider_attempt_order:
                    for retry_index in range(web_rate_limit_retries + 1):
                        phase2_web = dict(phase2_trace.get("web") or {})
                        phase2_web["attempt_count"] = int(phase2_web.get("attempt_count") or 0) + 1
                        phase2_trace["web"] = phase2_web
                        try:
                            res = _web_search(
                                q,
                                provider=provider_name,
                                max_results=web_max_results,
                                timeout=web_timeout,
                                domains_allow=web_domains_allow,
                                domains_deny=web_domains_deny,
                                freshness_days=web_freshness_days,
                                mode=web_mode,
                            )
                        except Exception as exc:
                            errors.append(f"web_search_failed:{type(exc).__name__}")
                            break
                        if not isinstance(res, dict) or not res.get("ok"):
                            error_text = str((res or {}).get("error") or "unknown")
                            errors.append(f"web_search_error:{error_text}")
                            if _is_rate_limited_error(error_text):
                                rate_limited = True
                                phase2_web = dict(phase2_trace.get("web") or {})
                                phase2_web["rate_limit_events"] = int(phase2_web.get("rate_limit_events") or 0) + 1
                                phase2_trace["web"] = phase2_web
                                if retry_index < web_rate_limit_retries and web_rate_limit_backoff_sec > 0:
                                    time.sleep(web_rate_limit_backoff_sec * (retry_index + 1))
                                    continue
                            break
                        provider_used = str((res.get("meta") or {}).get("provider") or res.get("provider") or provider_name).strip()
                        for item in (res.get("results") or [])[:8]:
                            if not isinstance(item, dict):
                                continue
                            url = _normalize_candidate_url(str(item.get("url") or "").strip())
                            if not url or _is_homepage_url(url):
                                continue
                            blocked_reason = _blocked_candidate_reason(item, topic)
                            if blocked_reason:
                                continue
                            candidates.append(
                                {
                                    "pack": str(row.get("pack") or ""),
                                    "query": q,
                                    "title": str(item.get("title") or "").strip(),
                                    "snippet": str(item.get("snippet") or "").strip(),
                                    "url": url,
                                    "is_pdf": _looks_like_pdf_url(url),
                                    "provider": provider_used,
                                    "provenance_class": "real_public",
                                }
                            )
                        query_completed = True
                        break
                    if query_completed:
                        break

            deduped: List[Dict[str, Any]] = []
            seen = set()
            for item in candidates:
                url = item.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                deduped.append(item)
                if len(deduped) >= 24:
                    break
            candidates = sorted(deduped, key=_candidate_score, reverse=True)
            if rate_limited and allow_seed_fallback:
                seed_candidates = _ensure_local_finance_seed_pdfs()
                if seed_candidates:
                    errors.append("web_search_rate_limited:seed_fallback_local_pdf")
                    for row in seed_candidates[: max_articles + 2]:
                        candidates.append(dict(row))
            elif rate_limited and not allow_seed_fallback:
                errors.append("web_search_rate_limited:no_seed_fallback")

        phase2_trace["candidate_count"] = len(candidates)
        selected: List[Dict[str, Any]] = []
        if candidates:
            try:
                selected = _pick_sources_with_llm(candidates, max_articles)
                phase2_trace["llm"] = dict(getattr(_pick_sources_with_llm, "last_trace", {}) or {})
            except Exception as exc:
                errors.append(f"llm_select_failed:{type(exc).__name__}")
                phase2_trace["llm"] = {"ok": False, "reason": f"exception:{type(exc).__name__}"}
                selected = []
        if not selected:
            selected = _deterministic_phase2_fallback(candidates, max_articles)
            phase2_trace["selection_mode"] = "deterministic_fallback"
        else:
            phase2_trace["selection_mode"] = "llm"
        if selected and len(selected) < max_articles:
            selected_urls = {str(item.get("url") or "").strip() for item in selected if isinstance(item, dict)}
            pool = sorted(candidates, key=_candidate_score, reverse=True)
            for row in pool:
                row_url = str((row or {}).get("url") or "").strip()
                if not row_url or row_url in selected_urls:
                    continue
                selected.append(row)
                selected_urls.add(row_url)
                if len(selected) >= max_articles:
                    break

        for item in selected[:max_articles]:
            normalized_url = _resolve_document_source_url(str(item.get("url") or "").strip())
            if not normalized_url:
                errors.append(f"non_document_source_rejected:{str(item.get('url') or '').strip()}")
                continue
            selected_sources.append(
                _Selected(
                    url=normalized_url,
                    title=str(item.get("title") or "").strip(),
                    why=str(item.get("why") or "").strip() or str(phase2_trace.get("selection_mode") or "phase2_selected"),
                )
            )

        min_selected = min(max_articles, 4)
        min_pdf = min(2, max_articles)
        trusted_domains = sum(1 for src in selected_sources if any(dom in _domain(src.url) for dom in _TRUSTED_TRADING_DOMAINS))
        trusted_ratio = float(trusted_domains / max(1, len(selected_sources))) if selected_sources else 0.0
        selected_pdf = sum(1 for src in selected_sources if _looks_like_pdf_url(src.url))
        if len(selected_sources) < min_selected or selected_pdf < min_pdf or trusted_ratio < 0.75:
            fallback_pool = _manifest_candidates(topic, max_items=max_articles * 4)
            selected_urls = {src.url for src in selected_sources}
            for row in fallback_pool:
                url = _resolve_document_source_url(str(row.get("url") or "").strip())
                if not url or url in selected_urls:
                    continue
                selected_sources.append(
                    _Selected(
                        url=url,
                        title=str(row.get("title") or "").strip(),
                        why="manifest_fallback",
                    )
                )
                selected_urls.add(url)
                selected_pdf = sum(1 for src in selected_sources if _looks_like_pdf_url(src.url))
                trusted_domains = sum(1 for src in selected_sources if any(dom in _domain(src.url) for dom in _TRUSTED_TRADING_DOMAINS))
                trusted_ratio = float(trusted_domains / max(1, len(selected_sources))) if selected_sources else 0.0
                if len(selected_sources) >= max_articles and selected_pdf >= min_pdf and trusted_ratio >= 0.75:
                    break
            if allow_seed_fallback and (len(selected_sources) < max_articles or selected_pdf < min_pdf):
                seed_candidates = _ensure_local_finance_seed_pdfs()
                for row in seed_candidates:
                    url = str(row.get("url") or "").strip()
                    if not url or url in selected_urls:
                        continue
                    selected_sources.append(
                        _Selected(
                            url=url,
                            title=str(row.get("title") or "").strip(),
                            why="seed_fallback_local_pdf",
                        )
                    )
                    selected_urls.add(url)
                    selected_pdf = sum(1 for src in selected_sources if _looks_like_pdf_url(src.url))
                    trusted_domains = sum(1 for src in selected_sources if any(dom in _domain(src.url) for dom in _TRUSTED_TRADING_DOMAINS))
                    trusted_ratio = float(trusted_domains / max(1, len(selected_sources))) if selected_sources else 0.0
                    if len(selected_sources) >= max_articles and selected_pdf >= min_pdf and trusted_ratio >= 0.75:
                        break

        if allow_inbox and not selected_sources:
            inbox = _ocr_inbox_dir()
            try:
                inbox.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            for path in sorted(inbox.glob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
                    continue
                selected_sources.append(_Selected(url=str(path), title=path.name, why="inbox"))
                if len(selected_sources) >= max_articles:
                    break

    if selected_sources:
        def _rank_selected(src: _Selected) -> tuple[int, int, int]:
            src_domain = _domain(src.url)
            trusted = int(any(dom in src_domain for dom in _TRUSTED_TRADING_DOMAINS))
            pdf = int(_looks_like_pdf_url(src.url))
            manifest_pref = int("manifest" in (src.why or "").lower() or "seed" in (src.why or "").lower())
            return (pdf, trusted, manifest_pref)

        selected_sources = sorted(selected_sources, key=_rank_selected, reverse=True)
    phase2_trace["selected_count"] = len(selected_sources[:max_articles])
    phase2_trace["selected_domains"] = sorted(
        {
            _domain(str(src.url or ""))
            for src in selected_sources[:max_articles]
            if str(src.url or "").strip() and _domain(str(src.url or ""))
        }
    )

    return {
        "ok": True,
        "topic": topic,
        "batch_id": batch_id,
        "queries": [str(row.get("query") or "") for row in query_rows],
        "query_profiles": query_rows,
        "candidates": candidates[:24],
        "sources": [{"url": s.url, "title": s.title, "why": s.why} for s in selected_sources[:max_articles]],
        "phase2": phase2_trace,
        "errors": errors,
    }


def trading_homework_run(payload: Dict[str, Any], *, rag: rag_store.AgentRAG | None = None) -> Dict[str, Any]:
    """
    Weekly-style homework run:
      - web.search -> candidate PDFs
      - LLM selects a few PDFs
      - download -> OCR -> summarize
      - ingest summaries into Apollo RAG
      - write a dated markdown report + grade
    """
    started = time.perf_counter()
    topic = str(payload.get("topic") or "").strip() or DEFAULT_HOMEWORK_TOPIC
    batch_id = str(payload.get("batch_id") or "").strip() or f"apollo_trading_homework_{_utc_stamp_compact()}"
    ingest = bool(payload.get("ingest", True))
    allow_web = bool(payload.get("allow_web", True))
    max_articles = max(1, min(int(payload.get("max_articles") or 3), 6))
    scrape_timeout = int(payload.get("scrape_timeout") or 12)
    ocr_max_pages = max(1, min(int(payload.get("ocr_max_pages") or 3), 8))
    ocr_max_chars = max(2000, min(int(payload.get("ocr_max_chars") or 12000), 30000))
    allow_inbox = bool(payload.get("allow_inbox", True))

    report_date = _today_stamp()
    report_dir = _reports_root() / report_date
    report_path = report_dir / f"trading_homework_{_safe_slug(batch_id)}.md"

    rag_store_inst = _rag(rag)
    ingested_ids: List[str] = []
    errors: List[str] = []
    gathered = gather_trading_homework_sources(
        {
            "topic": topic,
            "batch_id": batch_id,
            "allow_web": allow_web,
            "max_articles": max_articles,
            "allow_inbox": allow_inbox,
            "queries": payload.get("queries"),
            "sources": payload.get("sources"),
        }
    )
    errors.extend(list(gathered.get("errors") or []))
    sources = [
        _Selected(
            url=str(item.get("url") or "").strip(),
            title=str(item.get("title") or "").strip(),
            why=str(item.get("why") or "").strip(),
        )
        for item in (gathered.get("sources") or [])
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]

    # --- Phase 3: download + OCR + summarize + ingest ---
    doc_blocks: List[str] = []
    doc_results: List[Dict[str, Any]] = []
    for sel in sources[:max_articles]:
        if not sel.url:
            continue
        title = sel.title or Path(sel.url).name or "(untitled)"
        why = sel.why
        url = sel.url

        ocr_text = ""
        ocr_meta: Dict[str, Any] = {}

        local_path = None
        try:
            candidate_path = Path(url)
            if candidate_path.exists():
                local_path = candidate_path
        except Exception:
            local_path = None

        if local_path is not None:
            try:
                if local_path.suffix.lower() == ".pdf":
                    ocr_meta = _ocr_pdf(
                        local_path,
                        goal=f"Extract daily stock decision and overnight-hold strategy information for weekly research on: {topic}",
                        max_pages=ocr_max_pages,
                        max_chars=ocr_max_chars,
                    )
                else:
                    ensure_loaded()
                    ocr_meta = mcp_run(
                        "ocr.dual",
                        {"path": str(local_path), "goal": f"Extract trading strategy info: {topic}", "engine": "gb10_auto", "max_chars": ocr_max_chars},
                    )
                if not ocr_meta.get("ok"):
                    errors.append(f"ocr_failed:{str(ocr_meta.get('error') or 'unknown')}:{local_path.name}")
                ocr_text = str(ocr_meta.get("text") or "").strip()
            except Exception as exc:
                errors.append(f"ocr_failed:{type(exc).__name__}:{local_path.name}")
        elif _looks_like_pdf_url(url) and url.lower().startswith(("http://", "https://")):
            ok, path, err = _download(url, _downloads_dir(), timeout=25)
            if not ok:
                errors.append(f"download_failed:{err}:{url}")
            else:
                try:
                    ocr_meta = _ocr_pdf(
                        path,
                        goal=f"Extract daily stock decision and overnight-hold strategy information for weekly research on: {topic}",
                        max_pages=ocr_max_pages,
                        max_chars=ocr_max_chars,
                    )
                    if not ocr_meta.get("ok") or not str(ocr_meta.get("text") or "").strip():
                        errors.append(f"ocr_failed:{str(ocr_meta.get('error') or 'unknown')}:{path.name}")
                        # Fallback: try scraping the URL if OCR didn't yield usable text.
                        ok2, scraped, err2 = _scrape_url(url, timeout=scrape_timeout, max_chars=ocr_max_chars)
                        if not ok2:
                            errors.append(f"scrape_failed:{err2}:{url}")
                        else:
                            ocr_text = scraped
                            ocr_meta = {"ok": bool(scraped), "engine": "scrape_fallback", "pages": 0, "confidence": None}
                    else:
                        ocr_text = str(ocr_meta.get("text") or "").strip()
                except Exception as exc:
                    errors.append(f"ocr_failed:{type(exc).__name__}:{path.name}")
        else:
            ok, scraped, err = _scrape_url(url, timeout=scrape_timeout, max_chars=ocr_max_chars)
            if not ok:
                errors.append(f"scrape_failed:{err}:{url}")
            ocr_text = scraped
            ocr_meta = {"ok": bool(scraped), "engine": "scrape", "pages": 0, "confidence": None}

        summary = ""
        if ocr_text:
            try:
                summary = _summarize_doc(title, url, ocr_text)
            except Exception as exc:
                errors.append(f"summarize_failed:{type(exc).__name__}:{url}")
                summary = ""

        doc_blocks.append(
            "\n".join(
                [
                    f"## {title}",
                    f"- URL: {url}",
                    f"- Why: {why}" if why else "",
                    f"- Extract: engine={ocr_meta.get('engine')} pages={ocr_meta.get('pages')} chars={len(ocr_text)} conf={ocr_meta.get('confidence')}",
                    "",
                    "Summary:",
                    summary or "(summary unavailable)",
                    "",
                ]
            ).strip()
        )

        if ingest and (summary or ocr_text):
            try:
                text_to_store = "\n".join(
                    [
                        f"[TradingHomework] {title}",
                        f"URL: {url}",
                        "",
                        (summary or "").strip() or _truncate(ocr_text, 9500),
                    ]
                ).strip()
                doc_id = rag_store_inst.remember(
                    text=text_to_store,
                    source="trading_homework",
                    kind="trading_research",
                    priority=0.75,
                    tags=["homework", "trading", "day_trading", "apollo"],
                    extra={"batch_id": batch_id, "url": url, "title": title, "topic": topic},
                )
                ingested_ids.append(doc_id)
            except Exception as exc:
                errors.append(f"rag_ingest_failed:{type(exc).__name__}:{url}")

        doc_results.append(
            {
                "url": url,
                "title": title,
                "why": why,
                "extract_engine": ocr_meta.get("engine"),
                "extract_pages": ocr_meta.get("pages"),
                "extract_confidence": ocr_meta.get("confidence"),
                "ocr_chars": len(ocr_text),
                "summary_chars": len(summary or ""),
                "ingested": bool(ingest and (summary or ocr_text)),
                "local_path": str(local_path) if local_path is not None else "",
            }
        )

    finished_at = _now_local().isoformat(timespec="seconds")
    report_text = "\n".join(
        [
            f"# Apollo Trading Homework ({report_date})",
            "",
            f"- batch_id: `{batch_id}`",
            f"- topic: `{topic}`",
            f"- finished_at: `{finished_at}`",
            "",
            "## Phases",
            "- Phase 1: web.search (PDF candidates) or user-provided sources",
            "- Phase 2: source selection with candidate-bound LLM picks and deterministic fallback",
            "- Phase 3: download/scrape -> OCR extract -> summarize",
            "- Phase 4: ingest into Apollo RAG (tagged by batch_id)",
            "- Phase 5: grade the report quality (0-100)",
            "",
            "## Phase 2 Selection Trace",
            "```json",
            json.dumps(gathered.get("phase2") or {}, indent=2),
            "```",
            "",
            "## Sources",
            "\n".join(f"- {s.title or '(untitled)'} — {s.url}" for s in sources) or "(none)",
            "",
            "## Findings",
            "\n\n".join(doc_blocks) or "(no docs processed)",
            "",
            "## Errors",
            ("\n".join(f"- {e}" for e in errors) if errors else "(none)"),
            "",
        ]
    ).strip() + "\n"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    grade = _grade_findings(topic, report_text)
    # Append grade into report (keep idempotent-ish: write fresh).
    report_with_grade = report_text + "\n" + "## Grade\n" + "```json\n" + json.dumps(grade, indent=2) + "\n```\n"
    report_path.write_text(report_with_grade, encoding="utf-8")

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "ok": True,
        "topic": topic,
        "batch_id": batch_id,
        "report_path": str(report_path),
        "gather": gathered,
        "sources": [{"url": s.url, "title": s.title, "why": s.why} for s in sources],
        "doc_results": doc_results,
        "ingested": len(ingested_ids),
        "ingested_ids": ingested_ids,
        "errors": errors,
        "grade": grade,
        "elapsed_ms": round(elapsed_ms, 2),
    }


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apollo weekly trading homework (web+OCR -> RAG).")
    parser.add_argument("--topic", default=DEFAULT_HOMEWORK_TOPIC)
    parser.add_argument("--max-articles", type=int, default=3)
    parser.add_argument("--no-ingest", action="store_true")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--sources", nargs="*", default=None, help="Explicit PDF URLs to use instead of web.search")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    payload: Dict[str, Any] = {
        "topic": args.topic,
        "max_articles": int(args.max_articles),
        "ingest": not bool(args.no_ingest),
        "allow_web": not bool(args.no_web),
    }
    if args.sources:
        payload["sources"] = list(args.sources)
    res = trading_homework_run(payload)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
