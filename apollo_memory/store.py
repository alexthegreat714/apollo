"""
apollo_memory.store — User profile and session memory storage.

Wraps AgentRAG for structured user context. Profile docs float to the top
of gather_hits() because they carry priority 0.85-0.95.

Schema
------
Each profile fact is stored as a plain-text document with structured metadata:

  kind = "user_profile"    — core financial facts (income, debts, holdings)
  kind = "user_preference" — response style, preferred depth, topics
  kind = "financial_event" — timestamped conversation events

Profile docs use a stable deterministic ID so updating a fact
overwrites the old doc rather than creating a duplicate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Priority values — high enough to beat corpus docs in merged gather_hits()
PRIORITY_PROFILE    = 0.95
PRIORITY_PREFERENCE = 0.90
PRIORITY_EVENT      = 0.85

# Profile field registry — canonical keys for the user profile document
PROFILE_FIELDS = {
    "income":                 "Annual income (float, USD)",
    "income_frequency":       "Income frequency: annual | monthly | hourly",
    "tax_filing_status":      "Federal filing status: single | mfj | mfs | hoh | qw",
    "state":                  "US state abbreviation (two-letter)",
    "age":                    "User age (int)",
    "risk_tolerance":         "Risk tolerance: conservative | moderate | aggressive",
    "investment_horizon_yrs": "Years until funds needed (int)",
    "retirement_age_target":  "Target retirement age (int)",
    "monthly_expenses":       "Estimated monthly expenses (float, USD)",
    "emergency_fund_months":  "Emergency fund size in months of expenses (float)",
    "goals":                  "Investment / financial goals (list of strings)",
    "debts":                  "Debt list [{type, balance, rate, monthly_payment}]",
    "assets":                 "Asset list [{type, value, description}]",
    "tax_bracket":            "Estimated federal marginal bracket (float, e.g. 0.22)",
}


def _profile_id(field: str) -> str:
    """Stable deterministic doc ID for a profile field so updates overwrite."""
    return "profile_" + hashlib.md5(f"user_profile:{field}".encode()).hexdigest()[:12]


def _event_id() -> str:
    return f"event_{int(time.time() * 1000)}"


def _format_profile_text(field: str, value: Any) -> str:
    """Human-readable text for a profile fact (what gets embedded + injected)."""
    label = PROFILE_FIELDS.get(field, field)
    if isinstance(value, (list, dict)):
        val_str = json.dumps(value, ensure_ascii=False)
    else:
        val_str = str(value)
    return f"[User Profile] {label}: {val_str}"


class MemoryStore:
    """
    Thin wrapper around AgentRAG providing user-profile CRUD.

    Parameters
    ----------
    rag : AgentRAG
        The live AgentRAG instance (pass APOLLO_RAG from app.py).
    """

    def __init__(self, rag: Any) -> None:
        self._rag = rag

    # ------------------------------------------------------------------
    # Profile field CRUD
    # ------------------------------------------------------------------

    def set_profile_field(self, field: str, value: Any) -> str:
        """
        Write or overwrite a single profile field.
        Returns the doc ID.
        """
        doc_id = _profile_id(field)
        text = _format_profile_text(field, value)
        meta_extra: Dict[str, Any] = {"field": field}
        if isinstance(value, (str, int, float, bool)):
            meta_extra["value"] = value
        else:
            meta_extra["value_json"] = json.dumps(value, ensure_ascii=False)

        # Delete old version then re-add (AgentRAG doesn't support upsert by ID)
        try:
            self._rag.delete(ids=[doc_id])
        except Exception:
            pass

        self._rag.remember(
            text=text,
            source="user_profile",
            kind="user_profile",
            priority=PRIORITY_PROFILE,
            tags=["user", "profile", field],
            extra=meta_extra,
            id_=doc_id,
        )
        log.debug("[memory] profile.%s = %s", field, value)
        return doc_id

    def get_profile_field(self, field: str) -> Optional[Any]:
        """Return the stored value for a profile field, or None."""
        doc_id = _profile_id(field)
        try:
            res = self._rag.get(ids=[doc_id])
            metas = res.get("metadatas") or []
            if not metas:
                return None
            m = metas[0] or {}
            if "value" in m:
                return m["value"]
            if "value_json" in m:
                return json.loads(m["value_json"])
        except Exception:
            pass
        return None

    def get_full_profile(self) -> Dict[str, Any]:
        """Return all stored profile fields as a dict."""
        profile: Dict[str, Any] = {}
        for field in PROFILE_FIELDS:
            val = self.get_profile_field(field)
            if val is not None:
                profile[field] = val
        return profile

    def set_preference(self, key: str, value: str) -> str:
        """Store a user preference (response style, depth, etc.)."""
        doc_id = "pref_" + hashlib.md5(f"user_preference:{key}".encode()).hexdigest()[:12]
        text = f"[User Preference] {key}: {value}"
        try:
            self._rag.delete(ids=[doc_id])
        except Exception:
            pass
        self._rag.remember(
            text=text,
            source="user_preference",
            kind="user_preference",
            priority=PRIORITY_PREFERENCE,
            tags=["user", "preference", key],
            extra={"key": key, "value": value},
            id_=doc_id,
        )
        return doc_id

    # ------------------------------------------------------------------
    # Financial event log
    # ------------------------------------------------------------------

    def log_event(self, summary: str, event_type: str = "conversation",
                  details: Optional[Dict] = None) -> str:
        """
        Log a timestamped financial event (decision, question asked, fact noted).
        Each call creates a new doc (no deduplication — events are append-only).
        """
        doc_id = _event_id()
        ts = time.strftime("%Y-%m-%d %H:%M", time.utc if hasattr(time, "utc") else time.gmtime())
        text = f"[Financial Event {ts}] {event_type}: {summary}"
        extra: Dict[str, Any] = {"event_type": event_type, "ts": time.time()}
        if details:
            extra["details_json"] = json.dumps(details, ensure_ascii=False)
        self._rag.remember(
            text=text,
            source="financial_event",
            kind="financial_event",
            priority=PRIORITY_EVENT,
            tags=["user", "event", event_type],
            extra=extra,
            id_=doc_id,
        )
        return doc_id

    # ------------------------------------------------------------------
    # Retrieval helpers
    # ------------------------------------------------------------------

    def get_user_context_hits(self, query: str, top_k: int = 4) -> List[Dict]:
        """
        Retrieve top profile/preference/event docs relevant to the query.
        Returns AgentRAG-compatible hit dicts.
        """
        res = self._rag.search(
            query=query,
            top_k=top_k,
            kinds=["user_profile", "user_preference", "financial_event"],
        )
        return res.get("results", [])

    def get_profile_hits(self) -> List[Dict]:
        """
        Return ALL profile and preference docs as hits (for prompt injection).
        Used to prepend a structured user context block regardless of query.
        """
        profile = self.get_full_profile()
        if not profile:
            return []
        hits = []
        for field, value in profile.items():
            hits.append({
                "text": _format_profile_text(field, value),
                "score": PRIORITY_PROFILE,
                "meta": {"kind": "user_profile", "field": field},
                "source": "user_memory",
            })
        return hits
