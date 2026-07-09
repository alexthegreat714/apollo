"""
apollo_memory.extractor — Extract financial facts from user messages.

Strategy: regex-first for common patterns (fast, no LLM), then optionally
call gx10 for complex statements the patterns miss.

Extracted facts are returned as {field: value} dicts that the caller
can persist via MemoryStore.set_profile_field().
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Regex patterns — each yields (field, value) or None
# ---------------------------------------------------------------------------

def _match_income(text: str) -> Optional[Tuple[str, Any]]:
    """$85,000/year | $7,000/month | 85k a year | make $120k"""
    patterns = [
        (r"\$\s*([\d,]+)\s*[kK]\s*(?:a |per |/\s*)?(?:year|yr|annual)", 1000, "annual"),
        (r"\$\s*([\d,]+)\s*(?:a |per |/\s*)?(?:year|yr|annual)", 1, "annual"),
        (r"(?:earn|make|salary|income)[^\d]*\$?\s*([\d,]+)\s*[kK]", 1000, "annual"),
        (r"(?:earn|make|salary|income)[^\d]*\$\s*([\d,]+)(?!\s*[kK])\b", 1, "annual"),
        (r"\$\s*([\d,]+)\s*(?:a |per |/\s*)?month", 1, "monthly"),
        (r"([\d,]+)\s*[kK]\s*(?:a |per |/\s*)?(?:year|yr|annual)", 1000, "annual"),
    ]
    for pattern, multiplier, freq in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw) * multiplier
                if freq == "monthly":
                    val *= 12
                if 10_000 <= val <= 5_000_000:
                    return ("income", val)
            except ValueError:
                continue
    return None


def _match_age(text: str) -> Optional[Tuple[str, Any]]:
    patterns = [
        r"i(?:'m| am)\s+(\d{2})\s+years?\s+old",
        r"\bage[d]?\s+(\d{2})\b",
        r"\b(\d{2})\s+years?\s+old\b",
        r"\bturning\s+(\d{2})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            age = int(m.group(1))
            if 18 <= age <= 90:
                return ("age", age)
    return None


def _match_risk_tolerance(text: str) -> Optional[Tuple[str, Any]]:
    m = re.search(
        r"\b(very\s+)?(conservative|moderate|aggressive|growth[-\s]oriented|income[-\s]focused)\b"
        r"(?:\s+(?:investor|risk|investment|portfolio))?",
        text, re.I,
    )
    if m:
        raw = m.group(2).lower().replace("-", " ").replace(" ", "_")
        if "conservative" in raw:
            return ("risk_tolerance", "conservative")
        if "moderate" in raw:
            return ("risk_tolerance", "moderate")
        if "aggressive" in raw or "growth" in raw:
            return ("risk_tolerance", "aggressive")
    return None


def _match_filing_status(text: str) -> Optional[Tuple[str, Any]]:
    patterns = {
        "single": r"\b(filing\s+)?single\b",
        "mfj": r"\bmarried\s+filing\s+jointly\b|\bmfj\b",
        "mfs": r"\bmarried\s+filing\s+separately\b|\bmfs\b",
        "hoh": r"\bhead\s+of\s+household\b|\bhoh\b",
    }
    for status, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            return ("tax_filing_status", status)
    return None


def _match_retirement_age(text: str) -> Optional[Tuple[str, Any]]:
    patterns = [
        r"retire\s+(?:at|by|around)\s+(\d{2})",
        r"retirement\s+(?:at|by|around|age)\s+(\d{2})",
        r"(?:want|plan|hope)\s+to\s+retire\s+(?:at|by)?\s+(\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            age = int(m.group(1))
            if 45 <= age <= 80:
                return ("retirement_age_target", age)
    return None


def _match_mortgage(text: str) -> Optional[Tuple[str, Any]]:
    """Extract mortgage details → appended to debts list."""
    m = re.search(
        r"\$\s*([\d,]+[kK]?)\s+mortgage|mortgage\s+(?:of\s+)?\$\s*([\d,]+[kK]?)"
        r"(?:.*?([\d.]+)\s*%)?",
        text, re.I,
    )
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").replace(",", "")
    if not raw:
        return None
    multiplier = 1000 if raw.lower().endswith("k") else 1
    balance = float(raw.rstrip("kK")) * multiplier
    rate_m = re.search(r"([\d.]+)\s*%\s+mortgage|mortgage[^\d]*([\d.]+)\s*%", text, re.I)
    rate = float(rate_m.group(1) or rate_m.group(2)) / 100 if rate_m else None
    debt = {"type": "mortgage", "balance": balance}
    if rate:
        debt["rate"] = rate
    return ("debts", [debt])


def _match_state(text: str) -> Optional[Tuple[str, Any]]:
    states = {
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
        "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
        "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
        "VA","WA","WV","WI","WY","DC",
    }
    m = re.search(r"\b(in|live\s+in|from|based\s+in)\s+([A-Z]{2})\b", text)
    if m and m.group(2) in states:
        return ("state", m.group(2))
    # Full state names (partial)
    full = {
        "california": "CA", "texas": "TX", "new york": "NY", "florida": "FL",
        "illinois": "IL", "washington": "WA", "colorado": "CO", "georgia": "GA",
    }
    for name, abbr in full.items():
        if re.search(r"\b" + name + r"\b", text, re.I):
            return ("state", abbr)
    return None


def _match_emergency_fund(text: str) -> Optional[Tuple[str, Any]]:
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*[-–]?\s*month\s+emergency|emergency\s+fund.*?(\d+)\s+month",
        text, re.I,
    )
    if m:
        months = float(m.group(1) or m.group(2))
        if 0 < months < 36:
            return ("emergency_fund_months", months)
    return None


def _match_goal(text: str) -> Optional[Tuple[str, Any]]:
    goal_phrases = [
        r"(retire\s+early|fire\b|financial\s+independence)",
        r"(buy\s+a\s+home|buy\s+a\s+house|purchase\s+a\s+home)",
        r"(pay\s+off\s+(?:my\s+)?(?:student\s+loans?|debt|mortgage))",
        r"(save\s+for\s+college|college\s+savings?|529\s+plan)",
        r"(start\s+a\s+business|small\s+business)",
        r"(build\s+wealth|grow\s+my\s+(?:savings|portfolio|investments?))",
    ]
    found = []
    for pattern in goal_phrases:
        m = re.search(pattern, text, re.I)
        if m:
            found.append(m.group(1).lower())
    return ("goals", found) if found else None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

_EXTRACTORS = [
    _match_income,
    _match_age,
    _match_risk_tolerance,
    _match_filing_status,
    _match_retirement_age,
    _match_mortgage,
    _match_state,
    _match_emergency_fund,
    _match_goal,
]


def extract_facts(text: str) -> Dict[str, Any]:
    """
    Run all regex extractors against a user message.
    Returns a dict of {field: value} for any facts found.

    For list fields (debts, goals) returned values are lists
    that the caller should APPEND to existing stored values,
    not overwrite.
    """
    facts: Dict[str, Any] = {}
    for extractor in _EXTRACTORS:
        result = extractor(text)
        if result is not None:
            field, value = result
            if field in facts and isinstance(value, list):
                # merge list facts
                existing = facts[field] if isinstance(facts[field], list) else [facts[field]]
                facts[field] = existing + value
            else:
                facts[field] = value
    return facts


def has_financial_facts(text: str) -> bool:
    """Quick check — does this message likely contain new financial facts?"""
    triggers = [
        r"\$\s*[\d,]+",
        r"\b\d{2}\s+years?\s+old\b",
        r"\b(income|salary|earn|make|mortgage|retire|risk|invest|savings?|debt|loan)\b",
        r"\b(conservative|moderate|aggressive)\b",
        r"\b(married|single|filing)\b",
    ]
    return any(re.search(t, text, re.I) for t in triggers)


def describe_new_facts(facts: Dict[str, Any]) -> str:
    """Human-readable summary of what was extracted (for logging)."""
    if not facts:
        return "none"
    parts = []
    for field, value in facts.items():
        if isinstance(value, list):
            parts.append(f"{field}=[{len(value)} items]")
        else:
            parts.append(f"{field}={value}")
    return ", ".join(parts)
