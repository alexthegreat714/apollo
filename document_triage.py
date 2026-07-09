"""
Apollo document triage — pre-OCR/HippoRAG relevance and routing pass.

For each gathered source, decides:
  accept_ocr    — relevant, is a PDF/image, needs OCR processing
  accept_direct — relevant, clean text, goes straight to HippoRAG
  reject        — not relevant to the research goal

Decision is based on URL domain, title, and local path patterns.
Content body is often empty at triage time so we don't rely on it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


# ── Domain classification ─────────────────────────────────────────────────────

# Macro/regulatory domains: reject unless the title explicitly names a ticker
_MACRO_DOMAINS = {
    "federalreserve.gov",
    "federalregister.gov",
    "finra.org",
    "cftc.gov",
    "fred.stlouisfed.org",
    "bls.gov",
    "census.gov",
    "irs.gov",
    "treasury.gov",
    "nber.org",
    "imf.org",
    "worldbank.org",
    "bis.org",
}

# SEC is special: regulatory pages → reject, but EDGAR filings for a ticker → accept
_SEC_REGULATORY_PATHS = (
    "/resources-for-investors/",
    "/litigation/",
    "/enforcement/",
    "/rules/",
    "/news/",
    "/about/",
)

# Generic seed PDF paths — always low-relevance for equity research
_SEED_PATH_MARKER = "homework_seed_docs"

# Equity-specific domains: always accept
_EQUITY_DOMAINS = {
    "finance.yahoo.com",
    "feeds.finance.yahoo.com",
    "seekingalpha.com",
    "fool.com",
    "investors.com",
    "barrons.com",
    "marketwatch.com",
    "benzinga.com",
    "thestreet.com",
    "zacks.com",
    "stockanalysis.com",
    "macrotrends.net",
    "wisesheets.io",
    "simplywall.st",
    "finviz.com",
}

_EQUITY_NEWS_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "wsj.com",
    "ft.com",
    "businessinsider.com",
    "forbes.com",
    "techcrunch.com",
    "theverge.com",  # relevant for AI/tech stocks
}

_EQUITY_TERMS = frozenset({
    "earnings", "revenue", "guidance", "eps", "beat", "miss", "upgrade",
    "downgrade", "analyst", "price target", "quarterly", "annual report",
    "10-k", "10-q", "8-k", "shareholder", "dividend", "buyback",
    "acquisition", "merger", "ipo", "breakout", "pullback", "momentum",
    "resistance", "support", "options", "calls", "puts", "short interest",
    "institutional", "insider", "forecast", "estimate",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.hostname or "").lower()
        return re.sub(r"^www\d*\.", "", host)
    except Exception:
        return ""


def _is_local_path(url: str) -> bool:
    return url.startswith(("C:\\", "D:\\", "/", "./", "../")) or "\\" in url


def _needs_ocr(url: str) -> bool:
    u = url.lower()
    return u.endswith(".pdf") or u.endswith(".png") or u.endswith(".jpg") or u.endswith(".tiff")


# ── Relevance scoring ─────────────────────────────────────────────────────────

def _score_source(
    source: Dict[str, Any],
    tickers: List[str],
) -> Tuple[float, str]:
    """
    Returns (relevance_score 0.0–1.0, reason_string).
    Score >= 0.35 → accept; < 0.35 → reject.
    """
    url = str(source.get("url") or "")
    title = str(source.get("title") or "").lower()
    content = str(source.get("content") or source.get("text") or "")[:600].lower()
    ticker_lower = [t.lower() for t in tickers]

    # Generic seed fallback PDFs — no equity signal, always reject
    if _SEED_PATH_MARKER in url:
        return 0.0, "generic_seed_pdf"

    d = _domain(url)

    # Equity-specific sources — always accept
    if d in _EQUITY_DOMAINS:
        for t in ticker_lower:
            if t in title:
                return 1.0, f"equity_domain+ticker_title:{t}"
        return 0.8, f"equity_domain:{d}"

    # General news sources with ticker in title
    if d in _EQUITY_NEWS_DOMAINS:
        for t in ticker_lower:
            if t in title:
                return 0.95, f"news_domain+ticker_title:{t}"
        term_hits = sum(1 for term in _EQUITY_TERMS if term in title or term in content)
        if term_hits >= 2:
            return 0.65, f"news_domain+equity_terms:{term_hits}"
        return 0.4, f"news_domain_weak:{d}"

    # SEC: EDGAR filings for a specific ticker → accept; regulatory pages → reject
    if d in ("sec.gov", "www.sec.gov"):
        if "/cgi-bin/browse-edgar" in url or "/Archives/edgar/" in url:
            for t in ticker_lower:
                if t in url.lower() or t in title:
                    return 0.9, f"sec_edgar_ticker:{t}"
            return 0.5, "sec_edgar_no_ticker"
        for path in _SEC_REGULATORY_PATHS:
            if path in url:
                return 0.0, f"sec_regulatory_path:{path}"
        return 0.1, "sec_unknown_path"

    # Macro/regulatory domains
    if d in _MACRO_DOMAINS:
        for t in ticker_lower:
            if t in title or t in content:
                return 0.5, f"macro_domain_ticker_mention:{t}"
        return 0.0, f"macro_domain:{d}"

    # Local files not in seed path — could be relevant PDFs the user put there
    if _is_local_path(url):
        for t in ticker_lower:
            if t in title.lower() or t in url.lower():
                return 0.85, f"local_file_ticker:{t}"
        return 0.2, "local_file_no_ticker"

    # Unknown domain: score by ticker and equity term presence
    for t in ticker_lower:
        if t in title:
            return 0.9, f"unknown_domain+ticker_title:{t}"
    for t in ticker_lower:
        if t in content:
            return 0.7, f"unknown_domain+ticker_content:{t}"
    term_hits = sum(1 for term in _EQUITY_TERMS if term in title or term in content)
    if term_hits >= 3:
        return 0.6, f"equity_terms:{term_hits}"
    if term_hits >= 1:
        return 0.4, f"equity_terms_weak:{term_hits}"

    return 0.15, "no_equity_signal"


# ── Public API ────────────────────────────────────────────────────────────────

def triage_sources(
    sources: List[Dict[str, Any]],
    tickers: List[str],
    topic: str = "",
    min_relevance: float = 0.35,
) -> Dict[str, Any]:
    """
    Triage a list of gathered source dicts.

    Returns:
      {
        accepted_direct: [...],   # relevant, clean text → direct HippoRAG
        accepted_ocr:    [...],   # relevant, PDF/image → OCR first
        rejected:        [...],   # not relevant
        summary:         {...},
      }

    Each source dict is annotated with a '_triage' key containing
    {score, reason, decision}.
    """
    accepted_direct: List[Dict] = []
    accepted_ocr: List[Dict] = []
    rejected: List[Dict] = []

    for src in sources:
        url = str(src.get("url") or "")
        score, reason = _score_source(src, tickers)
        annotated = {**src, "_triage": {"score": round(score, 3), "reason": reason}}

        if score < min_relevance:
            annotated["_triage"]["decision"] = "reject"
            rejected.append(annotated)
        elif _needs_ocr(url):
            annotated["_triage"]["decision"] = "accept_ocr"
            accepted_ocr.append(annotated)
        else:
            annotated["_triage"]["decision"] = "accept_direct"
            accepted_direct.append(annotated)

    reject_reasons: Dict[str, int] = {}
    for s in rejected:
        r = s["_triage"]["reason"].split(":")[0]
        reject_reasons[r] = reject_reasons.get(r, 0) + 1

    return {
        "accepted_direct": accepted_direct,
        "accepted_ocr": accepted_ocr,
        "rejected": rejected,
        "summary": {
            "total": len(sources),
            "accepted_direct": len(accepted_direct),
            "accepted_ocr": len(accepted_ocr),
            "rejected": len(rejected),
            "reject_reasons": reject_reasons,
            "tickers": tickers,
        },
    }
