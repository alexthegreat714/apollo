"""
apollo.financial_news — Finance-tuned web news search MCP tool.

Wraps web.search with:
  - Financial abbreviation expansion (CPI, GDP, FOMC, etc.)
  - Year anchoring for time-sensitive queries
  - News-preferred provider chain (google_news_rss → auto fallback)
  - Structured output with domain attribution

Registers as: apollo.financial_news

Input:
    {
        "query":       str,   # required — raw user question or search term
        "intent":      str,   # optional — "markets"|"macro"|"investing"|"risk"|"tax"
        "max_results": int,   # optional — default 4, max 8
    }

Output: standard web.search response with enhanced_query and original_query added.
    {
        "ok": bool,
        "results": [{"title", "snippet", "url", "source"}, ...],
        "enhanced_query": str,
        "original_query": str,
        "meta": {...}
    }
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict

from common.mcp import register

_YEAR = "2026"

# Known financial abbreviations that search engines handle poorly as acronyms alone.
_EXPANSIONS = (
    (r"\bCPI\b", "CPI consumer price index inflation"),
    (r"\bPPI\b", "PPI producer price index"),
    (r"\bGDP\b", "GDP economic growth"),
    (r"\bFOMC\b", "FOMC Federal Reserve meeting decision"),
    (r"\bFed\b(?! funds| rate)", "Federal Reserve"),
    (r"\bFed funds\b", "Federal Reserve federal funds rate"),
    (r"\bTreasury\b", "US Treasury"),
    (r"\bVIX\b", "VIX volatility index"),
    (r"\bDXY\b", "US Dollar index DXY"),
)

# Terms that make a query time-sensitive — we append the year when these appear.
_TIME_SENSITIVE_RE = re.compile(
    r"\b(current|latest|today|recent|now|rate|price|yield|forecast|outlook|data"
    r"|report|release|reading|figure|number|print)\b",
    re.I,
)

# Terms that suggest the user wants news specifically (affects provider selection).
_NEWS_INTENT_RE = re.compile(
    r"\b(news|headline|headlines|breaking|what happened|announcement|report"
    r"|earnings|result|statement|release)\b",
    re.I,
)


def _enhance_query(query: str, intent: str) -> str:
    """Return a search-optimised query for financial content."""
    q = re.sub(
        r"\b(please|can you|what is|what are|tell me|i want to know|explain|do you know|give me)\b",
        "",
        query,
        flags=re.I,
    )
    for pattern, replacement in _EXPANSIONS:
        q = re.sub(pattern, replacement, q)
    q = re.sub(r"\s+", " ", q).strip()

    # Anchor time-sensitive queries to the current year so stale results rank lower.
    if _TIME_SENSITIVE_RE.search(q) and _YEAR not in q:
        q = f"{q} {_YEAR}"

    # Trim to a reasonable search query length.
    return q[:150].strip()


def financial_news(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Finance-tuned news search: enhance query, prefer news feeds, return structured results.
    """
    if os.getenv("SKY_WEB_ENABLED") != "1":
        return {"ok": False, "error": "web_disabled", "results": []}

    raw_query = str(payload.get("query") or "").strip()
    if not raw_query:
        return {"ok": False, "error": "query required", "results": []}

    intent = str(payload.get("intent") or "markets").strip().lower()
    max_results = max(1, min(int(payload.get("max_results") or 4), 8))

    enhanced = _enhance_query(raw_query, intent)

    # Pick best provider: prefer google_news_rss for headline queries, else auto chain.
    provider = "google_news_rss" if _NEWS_INTENT_RE.search(raw_query) else "auto"

    try:
        from common import mcp as _mcp
        result = _mcp.run(
            "web.search",
            {"query": enhanced, "max_results": max_results, "provider": provider},
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "results": []}

    if isinstance(result, dict):
        result["enhanced_query"] = enhanced
        result["original_query"] = raw_query
        result["provider_preference"] = provider

    return result


register(
    "apollo.financial_news",
    financial_news,
    {
        "name": "apollo.financial_news",
        "title": "Apollo Financial News Search",
        "summary": (
            "Finance-tuned web search: auto-expands abbreviations (CPI, GDP, FOMC), "
            "anchors time-sensitive queries to the current year, prefers Google News RSS "
            "for headline queries. Returns structured results with domain attribution. "
            "Requires SKY_WEB_ENABLED=1."
        ),
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)
