"""
apollo_hipporag.entity_extractor — Extract (head, relation, tail) triples from text.

Two modes:
  1. Regex / keyword NER  — fast, no LLM, covers common financial entities
  2. LLM extraction       — gx10 via Ollama, richer triples, runs at ingest time

The regex mode is always run first. LLM mode is opt-in and used by the batch
ingestion job to enrich the graph beyond what patterns can catch.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

Triple = Tuple[str, str, str]  # (head, relation, tail)

_ENTITY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "if", "in", "into", "is", "it", "its", "of", "on", "or",
    "that", "the", "their", "this", "to", "was", "were", "with", "you",
}

_SHORT_ENTITY_ALLOWLIST = {
    "ai", "aml", "apr", "apy", "bea", "bps", "cboe", "cdd", "cftc", "cpi",
    "crd", "dti", "ebitda", "eps", "etf", "fed", "finra", "fomc", "frb",
    "fv", "gdp", "hsa", "ipo", "ira", "irs", "ltv", "m2", "npv", "pdt",
    "ppi", "pv", "rmd", "roa", "roe", "roi", "sec", "sofr", "sro", "tips",
    "tvm", "vix", "ytm",
    # Common high-liquidity tickers used by Apollo's focus universe.
    "aapl", "abbv", "amd", "amzn", "anet", "avgo", "bac", "bmy", "c",
    "cop", "crm", "cvx", "emr", "etn", "ge", "gm", "googl", "gs", "hal",
    "hon", "jnj", "jpm", "lly", "meta", "mrk", "mrvl", "ms", "msft",
    "nvda", "orcl", "oxy", "ph", "rok", "schw", "slb", "tsla", "tsm",
    "tsmc", "unh", "xom",
}


def is_valid_entity(entity: str) -> bool:
    """Return True when an entity is useful enough for Apollo's finance KG."""
    value = re.sub(r"\s+", " ", str(entity or "").lower().strip())
    if not value or value in _ENTITY_STOPWORDS:
        return False
    # Check allowlist before length — allows short tickers like single-char "c" (Citigroup)
    if value in _SHORT_ENTITY_ALLOWLIST:
        return True
    if len(value) < 2:
        return False
    if len(value) <= 4 and value not in _ALL_ENTITIES:
        return False
    if not re.search(r"[a-z0-9]", value):
        return False
    alpha_chars = re.sub(r"[^a-z]", "", value)
    if alpha_chars and len(alpha_chars) <= 2 and value not in _SHORT_ENTITY_ALLOWLIST:
        return False
    return True


# ---------------------------------------------------------------------------
# Financial entity patterns for lightweight NER
# ---------------------------------------------------------------------------

_RATE_ENTITIES = [
    "federal funds rate", "fed funds rate", "interest rate", "mortgage rate",
    "inflation rate", "cpi", "ppi", "treasury yield", "10-year yield",
    "prime rate", "libor", "sofr", "discount rate",
]

_INSTRUMENT_ENTITIES = [
    "roth ira", "traditional ira", "401k", "403b", "hsa", "529 plan",
    "s&p 500", "dow jones", "nasdaq", "vix", "etf", "mutual fund",
    "treasury bond", "tips", "i-bond", "municipal bond", "corporate bond",
    "stock", "equity", "dividend", "option", "call option", "put option",
]

_INSTITUTION_ENTITIES = [
    "federal reserve", "fed", "fomc", "sec", "irs", "treasury department",
    "fdic", "cfpb", "social security",
]

_TRADING_RULE_ENTITIES = [
    "day trading",
    "pattern day trader",
    "pdt",
    "margin",
    "initial margin",
    "maintenance margin",
    "margin call",
    "margin requirement",
    "buying power",
    "equity requirement",
    "house requirement",
    "cash account",
    "margin account",
    "regulation t",
    "settlement risk",
    "wash sale rule",
    "short sale restriction",
    "stop loss",
    "position sizing",
    "risk management",
    "risk of ruin",
    "slippage",
    "transaction cost",
    "market microstructure",
    "liquidity",
]

_CONCEPT_ENTITIES = [
    "inflation", "deflation", "recession", "gdp", "unemployment",
    "capital gains", "dividend yield", "price-to-earnings", "p/e ratio",
    "sharpe ratio", "beta", "alpha", "standard deviation", "volatility",
    "diversification", "asset allocation", "rebalancing", "dollar-cost averaging",
    "compound interest", "compound growth", "time value of money",
    "marginal tax rate", "effective tax rate", "tax bracket",
    "required minimum distribution", "rmd", "safe withdrawal rate",
    "sequence of returns", "monte carlo", "4% rule",
    "debt-to-income ratio", "dti", "loan-to-value",
]

_ALL_ENTITIES = set(
    e.lower() for e in _RATE_ENTITIES + _INSTRUMENT_ENTITIES
    + _INSTITUTION_ENTITIES + _TRADING_RULE_ENTITIES + _CONCEPT_ENTITIES
)


def extract_entities_regex(text: str) -> List[str]:
    """Return financial entities mentioned in text (regex / list lookup)."""
    tl = text.lower()
    found = []
    for entity in _ALL_ENTITIES:
        if entity in tl:
            found.append(entity)
    # Also catch ticker-style ALL_CAPS (2-5 chars) e.g. NVDA, AAPL
    for m in re.finditer(r"\b([A-Z]{2,5})\b", text):
        candidate = m.group(1)
        if candidate in {"US", "IRA", "ETF", "GDP", "CPI", "PPI", "SEC", "IRS", "FED", "FOMC", "TIPS", "HSA", "RMD"}:
            continue
        normalized = candidate.lower()
        if is_valid_entity(normalized):
            found.append(normalized)
    return list(dict.fromkeys(found))  # dedupe preserving order


def extract_triples_regex(text: str, doc_id: str) -> List[Triple]:
    """
    Build simple triples from co-occurring financial entities in a passage.
    Uses sliding-window co-occurrence: if two entities appear within 200 chars,
    infer a 'related_to' edge. Not perfect but fast.
    """
    entities = extract_entities_regex(text)
    if len(entities) < 2:
        return []

    triples: List[Triple] = []
    tl = text.lower()

    # Find positions of each entity
    positions = {}
    for ent in entities:
        idx = tl.find(ent)
        if idx >= 0:
            positions[ent] = idx

    # Sliding-window co-occurrence within 300 chars
    items = sorted(positions.items(), key=lambda x: x[1])
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            head, hpos = items[i]
            tail, tpos = items[j]
            if abs(tpos - hpos) <= 300:
                # Try to infer a better relation from context
                relation = _infer_relation(text, head, tail)
                triples.append((head, relation, tail))

    return triples[:8]  # cap per chunk


def _infer_relation(text: str, head: str, tail: str) -> str:
    """Guess a relation label from words between two entities."""
    tl = text.lower()
    h_idx = tl.find(head)
    t_idx = tl.find(tail)
    if h_idx < 0 or t_idx < 0:
        return "related_to"
    between = tl[min(h_idx, t_idx): max(h_idx, t_idx) + len(tail)]

    relation_hints = {
        "affect": "affects", "impact": "impacts", "influence": "influences",
        "increase": "increases", "decrease": "decreases", "raise": "raises",
        "lower": "lowers", "reduce": "reduces", "drive": "drives",
        "measure": "measures", "track": "tracks", "reflect": "reflects",
        "determine": "determines", "control": "controls",
        "hedge": "hedges_against", "protect": "protects_against",
        "correl": "correlated_with", "invers": "inversely_related_to",
        "compound": "compounds", "grow": "grows_with",
        "depend": "depends_on", "calculat": "calculated_from",
    }
    for hint, label in relation_hints.items():
        if hint in between:
            return label
    return "related_to"


# ---------------------------------------------------------------------------
# Financial fact extraction — EDGAR / earnings-text specific
# ---------------------------------------------------------------------------

_RE_REVENUE_GREW = re.compile(
    # Adjacent form: "revenues increased ..."
    r'(?:net\s+)?(?:revenue|revenues|net\s+sales|total\s+revenue|total\s+net\s+revenue)'
    r'(?:\s+\w+){0,6}\s+(?:increased|grew|rose|surged|jumped|climbed|higher)'
    # Proximity form: "revenues ... increase/growth" or "increase in revenues"
    r'|(?:increase|growth|improvement)\s+in\s+(?:net\s+)?(?:revenue|revenues|net\s+sales)',
    re.IGNORECASE,
)
_RE_REVENUE_FELL = re.compile(
    r'(?:net\s+)?(?:revenue|revenues|net\s+sales|total\s+revenue|total\s+net\s+revenue)'
    r'(?:\s+\w+){0,6}\s+(?:decreased|declined|fell|dropped|slipped|contracted|lower)'
    r'|(?:decrease|decline|reduction)\s+in\s+(?:net\s+)?(?:revenue|revenues|net\s+sales)',
    re.IGNORECASE,
)
_RE_EPS_BEAT = re.compile(
    r'(?:beat|exceeded?|surpassed?|topped?)\s+(?:\w+\s+){0,3}(?:estimates?|consensus|expectations?)'
    r'|(?:earnings?\s+per\s+share|eps|diluted\s+eps)\s+(?:of\s+)?\$[\d.]+\s+(?:compared\s+to\s+(?:analyst\s+)?estimates?)',
    re.IGNORECASE,
)
_RE_EPS_MISS = re.compile(
    r'(?:missed?|fell\s+short|below\s+(?:analyst\s+)?estimates?|came\s+in\s+below)',
    re.IGNORECASE,
)
_RE_EPS_GREW = re.compile(
    r'(?:earnings?\s+per\s+(?:diluted\s+)?share|diluted\s+(?:net\s+)?earnings?\s+per\s+share|diluted\s+eps)'
    r'(?:\s+\w+){0,6}\s+'
    r'(?:increased|grew|rose|improved|higher)'
    r'|(?:increase|growth|improvement)\s+in\s+(?:diluted\s+)?earnings?\s+per\s+share',
    re.IGNORECASE,
)
_RE_EPS_FELL = re.compile(
    r'(?:earnings?\s+per\s+(?:diluted\s+)?share|diluted\s+(?:net\s+)?earnings?\s+per\s+share|diluted\s+eps)'
    r'(?:\s+\w+){0,6}\s+'
    r'(?:decreased|declined|fell|dropped|lower)'
    r'|(?:decrease|decline)\s+in\s+(?:diluted\s+)?earnings?\s+per\s+share',
    re.IGNORECASE,
)
_RE_GUIDANCE_RAISED = re.compile(
    r'(?:raised?|increased?|lifted?|boosted?|improved?|raised?\s+(?:its\s+)?(?:full[- ]?year\s+)?)\s*guidance'
    r'|(?:raised?|increased?|lifted?)\s+(?:full[- ]?year|fiscal\s+year|annual)\s+(?:revenue|earnings?|eps)\s+(?:outlook|guidance|forecast)',
    re.IGNORECASE,
)
_RE_GUIDANCE_LOWERED = re.compile(
    r'(?:lowered?|reduced?|cut\s+(?:its\s+)?|withdrew?n?\s+(?:its\s+)?|decreased?)\s*guidance'
    r'|(?:reduced?|lowered?|cut)\s+(?:full[- ]?year|fiscal\s+year|annual)\s+(?:revenue|earnings?|eps)\s+(?:outlook|guidance|forecast)',
    re.IGNORECASE,
)
_RE_GUIDANCE_MAINTAINED = re.compile(
    r'(?:maintained?|reaffirmed?|reiterated?)\s+(?:(?:full[- ]?year|annual|fiscal)\s+)?guidance'
    r'|(?:maintained?|reaffirmed?)\s+(?:full[- ]?year|fiscal\s+year)\s+(?:outlook|forecast)',
    re.IGNORECASE,
)
_RE_QUARTER = re.compile(
    r'\b(Q[1-4])\s+(?:fiscal\s+)?(20\d\d)\b'
    r'|\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?(?:fiscal\s+)?(20\d\d)\b'
    r'|three\s+months\s+ended\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,?\s+(20\d\d)',
    re.IGNORECASE,
)

_MONTH_TO_QUARTER = {
    "january": "Q1", "february": "Q1", "march": "Q1",
    "april": "Q2", "may": "Q2", "june": "Q2",
    "july": "Q3", "august": "Q3", "september": "Q3",
    "october": "Q4", "november": "Q4", "december": "Q4",
}
_RE_SEGMENT_GROWTH = re.compile(
    r'(cloud|data\s+center|advertising|services|product|hardware|software|ai|semiconductor)\s+'
    r'(?:segment\s+)?(?:revenue|revenues?|sales)\s+'
    r'(?:increased|grew|rose|declined|fell|of)\s+(?:by\s+)?(?:[\d.]+\s*(?:%|percent|\$)|[\d.]+\s*(?:billion|million))',
    re.IGNORECASE,
)
_RE_MARGIN_EXPANDED = re.compile(
    r'(?:gross|operating|net)\s+(?:profit\s+)?margin\s+(?:expanded|improved|increased|grew)'
    r'|gross\s+profit\s+(?:margin\s+)?(?:increased|improved|expanded|grew|rose)',
    re.IGNORECASE,
)
_RE_MARGIN_COMPRESSED = re.compile(
    r'(?:gross|operating|net)\s+(?:profit\s+)?margin\s+(?:compressed|declined|decreased|fell|contracted)'
    r'|gross\s+profit\s+(?:margin\s+)?(?:decreased|declined|fell|compressed|contracted)',
    re.IGNORECASE,
)
_RE_SHARE_BUYBACK = re.compile(
    r'(?:repurchased?|bought\s+back|buyback|share\s+repurchase)\s+\$?[\d.]+\s*(?:billion|million|B|M)\b'
    r'|(?:repurchased?|retired)\s+[\d,]+\s+(?:shares?\s+of\s+)?(?:common\s+stock|shares?)',
    re.IGNORECASE,
)
_RE_DIVIDEND_RAISED = re.compile(
    r'(?:increased?|raised?|boosted?)\s+(?:its\s+)?(?:quarterly\s+)?dividend'
    r'|(?:declared|announced)\s+(?:a\s+)?(?:quarterly\s+)?(?:cash\s+)?dividend\s+(?:of\s+\$[\d.]+|increase)',
    re.IGNORECASE,
)
_RE_NET_INCOME_GREW = re.compile(
    r'(?:net\s+income|net\s+earnings?)'
    r'(?:\s+\w+){0,6}\s+'
    r'(?:increased|grew|rose|improved|higher)'
    r'|(?:increase|growth|improvement)\s+in\s+net\s+(?:income|earnings)',
    re.IGNORECASE,
)
_RE_NET_INCOME_FELL = re.compile(
    r'(?:net\s+income|net\s+earnings?)'
    r'(?:\s+\w+){0,6}\s+'
    r'(?:decreased|declined|fell|dropped|lower)'
    r'|(?:decrease|decline)\s+in\s+net\s+(?:income|earnings)',
    re.IGNORECASE,
)

_QUARTER_NAMES = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}


def _detect_period(text: str) -> str:
    m = _RE_QUARTER.search(text)
    if not m:
        return "recent_period"
    if m.group(1):  # "Q1 2026"
        return f"{m.group(1).upper()}_{m.group(2)}".lower()
    if m.group(3):  # "first quarter of 2026"
        name = _QUARTER_NAMES.get((m.group(3) or "").lower(), "Q?")
        return f"{name}_{m.group(4)}".lower()
    # "three months ended March 31, 2026" → group(5) is the year
    # Need to find month from original match to map to quarter
    year = m.group(5)
    month_match = re.search(
        r'three\s+months\s+ended\s+(January|February|March|April|May|June|'
        r'July|August|September|October|November|December)',
        text, re.IGNORECASE,
    )
    if month_match and year:
        qtr = _MONTH_TO_QUARTER.get(month_match.group(1).lower(), "Q?")
        return f"{qtr}_{year}".lower()
    return "recent_period"


def _ticker_from_doc_id(doc_id: str) -> str:
    """Extract lowercase ticker from known doc_id prefixes."""
    patterns = [
        r'^edgar_([A-Za-z]{1,6})_',
        r'^news_yf_([A-Za-z]{1,6})_',
        r'^fund_([A-Za-z]{1,6})_',           # fundamentals snapshot
        r'^earnings_earn_([A-Za-z]{1,6})_',  # earnings calendar
        r'^analyst_([A-Za-z]{1,6})_',        # analyst targets (future)
    ]
    for pat in patterns:
        m = re.match(pat, doc_id, re.IGNORECASE)
        if m:
            cand = m.group(1).lower()
            if cand in _SHORT_ENTITY_ALLOWLIST:
                return cand
    return ""


def _extract_financial_facts(text: str, ticker: str) -> List[Triple]:
    """
    Extract company-specific financial fact triples from EDGAR filing text.
    Returns triples like (googl, eps_beat_estimates, q1_2026).
    """
    if not ticker:
        return []
    period = _detect_period(text)
    triples: List[Triple] = []

    # Encode relation into the tail so each fact type produces a unique (head, tail)
    # pair — the graph_store uses DiGraph which allows only one edge per (head, tail).
    def _fact(rel: str) -> Triple:
        return (ticker, "has_fact", f"{rel}:{period}")

    if _RE_REVENUE_GREW.search(text):
        triples.append(_fact("revenue_grew"))
    elif _RE_REVENUE_FELL.search(text):
        triples.append(_fact("revenue_declined"))

    if _RE_EPS_BEAT.search(text):
        triples.append(_fact("eps_beat_estimates"))
    elif _RE_EPS_MISS.search(text):
        triples.append(_fact("eps_missed_estimates"))
    elif _RE_EPS_GREW.search(text):
        triples.append(_fact("eps_grew"))
    elif _RE_EPS_FELL.search(text):
        triples.append(_fact("eps_declined"))

    if _RE_NET_INCOME_GREW.search(text):
        triples.append(_fact("net_income_grew"))
    elif _RE_NET_INCOME_FELL.search(text):
        triples.append(_fact("net_income_declined"))

    if _RE_GUIDANCE_RAISED.search(text):
        triples.append(_fact("raised_guidance"))
    elif _RE_GUIDANCE_LOWERED.search(text):
        triples.append(_fact("lowered_guidance"))
    elif _RE_GUIDANCE_MAINTAINED.search(text):
        triples.append(_fact("maintained_guidance"))

    seg = _RE_SEGMENT_GROWTH.search(text)
    if seg:
        seg_name = re.sub(r'\s+', '_', seg.group(1).lower().strip())
        triples.append(_fact(f"{seg_name}_segment_growth"))

    if _RE_MARGIN_EXPANDED.search(text):
        triples.append(_fact("margin_expanded"))
    elif _RE_MARGIN_COMPRESSED.search(text):
        triples.append(_fact("margin_compressed"))

    if _RE_SHARE_BUYBACK.search(text):
        triples.append(_fact("share_buyback_active"))

    if _RE_DIVIDEND_RAISED.search(text):
        triples.append(_fact("dividend_raised"))

    return triples[:8]


def _fallback_keyword_triples(text: str) -> List[Triple]:
    """Last-resort triples for margin/day-trading docs with sparse entity matches."""
    tl = text.lower()
    signals = [
        "day trading",
        "pattern day trader",
        "margin",
        "margin requirement",
        "buying power",
        "risk management",
        "risk",
        "position sizing",
        "position",
        "slippage",
        "transaction cost",
        "volatility",
        "liquidity",
        "stop loss",
        "account",
        "margin account",
        "security",
        "securities",
        "market",
        "trade",
        "trading",
        "order",
        "settlement",
        "capital",
        "equity",
        "portfolio",
        "leverage",
        "compliance",
        "regulation",
        "requirement",
        "broker",
        "dealer",
        "customer",
    ]
    found = [signal for signal in signals if signal in tl]
    found = list(dict.fromkeys(found))
    triples: List[Triple] = []
    if "pattern day trader" in found and "margin requirement" in found:
        triples.append(("pattern day trader", "subject_to", "margin requirement"))
    if "day trading" in found and "risk management" in found:
        triples.append(("day trading", "requires", "risk management"))
    if "day trading" in found and "margin" in found:
        triples.append(("day trading", "uses", "margin"))
    if "slippage" in found and "transaction cost" in found:
        triples.append(("slippage", "increases", "transaction cost"))
    if "volatility" in found and "liquidity" in found:
        triples.append(("volatility", "interacts_with", "liquidity"))
    for i in range(len(found)):
        for j in range(i + 1, len(found)):
            if len(triples) >= 8:
                break
            head, tail = found[i], found[j]
            triple = (head, "related_to", tail)
            if head != tail and triple not in triples:
                triples.append(triple)
    return triples[:8]


# ---------------------------------------------------------------------------
# LLM-based extraction (gx10)
# ---------------------------------------------------------------------------

_LLM_EXTRACT_PROMPT = """\
Extract financial knowledge graph triples from the text below.
Return ONLY valid JSON — no explanation, no markdown.
Format: {{"triples": [["head entity", "relation", "tail entity"], ...]}}

Rules:
- Entities: financial instruments, rates, institutions, metrics, strategies (2-5 words max, lowercase)
- Relations: concise verb phrases (1-3 words, lowercase)
- Extract 3-7 triples. Only include clear, factual relationships.
- If no clear relationships exist, return {{"triples": []}}

Text:
{text}"""


def extract_triples_llm(
    text: str,
    doc_id: str,
    model: str = "gx10:latest",
    ollama_url: str = "http://127.0.0.1:11434",
    timeout: int = 30,
    num_predict: int = 300,
) -> List[Triple]:
    """
    Extract triples via LLM (gx10). Falls back to regex on failure.
    Used by the batch ingestion job — not called at query time.
    """
    try:
        import requests as _req
        prompt = _LLM_EXTRACT_PROMPT.format(text=text[:800])
        resp = _req.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": int(max(64, num_predict))}},
            timeout=timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}")
        raw = resp.json().get("response", "").strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
        raw = re.sub(r"\s*```$", "", raw, flags=re.M).strip()

        parsed = json.loads(raw)
        triples_raw = parsed.get("triples", [])
        triples: List[Triple] = []
        for item in triples_raw:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                h, r, t = [str(x).lower().strip() for x in item]
                if h and r and t and h != t and is_valid_entity(h) and is_valid_entity(t):
                    triples.append((h, r, t))
        return triples[:8]

    except Exception as exc:
        log.debug("[hipporag] LLM extraction failed for %s (%s); falling back to regex", doc_id, exc)
        return extract_triples_regex(text, doc_id)


def extract_triples(
    text: str,
    doc_id: str,
    use_llm: bool = False,
    **llm_kwargs,
) -> List[Triple]:
    """
    Main entry point. Regex always runs; LLM runs if use_llm=True.
    Financial-fact extraction runs for EDGAR/news doc_ids.
    Results are merged and deduplicated.
    """
    ticker = _ticker_from_doc_id(doc_id)
    fact_triples = _extract_financial_facts(text, ticker) if ticker else []

    regex_triples = extract_triples_regex(text, doc_id)
    if not use_llm:
        base = fact_triples + regex_triples
        seen: set = set()
        merged: List[Triple] = []
        for h, r, t in base:
            key = (h, r, t)
            if key not in seen:
                seen.add(key)
                merged.append((h, r, t))
        return merged[:12] if merged else _fallback_keyword_triples(text)

    llm_triples = extract_triples_llm(text, doc_id, **llm_kwargs)

    seen = set()
    merged = []
    for h, r, t in fact_triples + llm_triples + regex_triples:
        key = (h, r, t)
        if key not in seen:
            seen.add(key)
            merged.append((h, r, t))
    if merged:
        return merged[:14]
    return _fallback_keyword_triples(text)
