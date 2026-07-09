"""
apollo.budget_query — MCP tool for querying Apollo's SQLite budget store.

Registers as: apollo.budget_query

Input:
    {
        "month": "YYYY-MM",          # optional; defaults to current month
        "include_transactions": true, # optional; include recent transactions
        "top_categories": 5           # optional; how many categories to surface
    }

Output:
    {
        "ok": true,
        "month": "YYYY-MM",
        "summary": "<text budget context>",
        "raw": {
            "assigned_total": float,
            "activity_total": float,
            "available_total": float,
            "overspent_count": int,
            "income_total": float,
            "expense_total": float,
            "net": float,
            "overspent_categories": [...],
            "top_spending": [...],
            "recent_transactions": [...]  # if include_transactions=true
        }
    }
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from common.mcp import register


def _current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def budget_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query Apollo's budget store for a given month's spending summary.

    Returns a text context block (same content injected into chat prompts) plus
    a structured 'raw' object for programmatic use by other agents.
    """
    try:
        from apollo_budget.store import get_store
        from apollo_budget.context import build_budget_context
    except ImportError as exc:
        return {"ok": False, "error": f"budget module unavailable: {exc}"}

    month = str(payload.get("month") or "").strip() or _current_month()
    include_tx = bool(payload.get("include_transactions", True))
    top_n = max(1, min(int(payload.get("top_categories") or 5), 20))

    try:
        store = get_store()
        summary_text = build_budget_context(store, month)

        # Also build structured raw data
        raw_summary = store.get_summary(month)
        raw: Dict[str, Any] = {}

        if raw_summary and raw_summary.get("ok"):
            raw = {
                "assigned_total": raw_summary.get("assigned_total", 0),
                "activity_total": raw_summary.get("activity_total", 0),
                "available_total": raw_summary.get("available_total", 0),
                "overspent_count": raw_summary.get("overspent_count", 0),
                "income_total": raw_summary.get("income_total", 0),
                "expense_total": raw_summary.get("expense_total", 0),
                "net": raw_summary.get("net", 0),
            }

        # Overspent categories
        try:
            groups = store.get_categories(month).get("groups", [])
            flat: List[Dict[str, Any]] = [cat for g in groups for cat in g.get("categories", [])]
            overspent = sorted(
                [c for c in flat if (c.get("available") or 0) < 0],
                key=lambda c: c.get("available") or 0,
            )
            raw["overspent_categories"] = [
                {"name": c.get("name"), "available": c.get("available"), "activity": c.get("activity")}
                for c in overspent[:top_n]
            ]
            top_spending = sorted(
                [c for c in flat if (c.get("activity") or 0) < 0],
                key=lambda c: c.get("activity") or 0,
            )
            raw["top_spending"] = [
                {"name": c.get("name"), "activity": c.get("activity"), "assigned": c.get("assigned")}
                for c in top_spending[:top_n]
            ]
        except Exception:
            raw.setdefault("overspent_categories", [])
            raw.setdefault("top_spending", [])

        # Recent transactions
        if include_tx:
            try:
                txs = store.get_transactions(month).get("transactions", [])[:12]
                raw["recent_transactions"] = [
                    {
                        "date": t.get("date"),
                        "payee": t.get("payee"),
                        "amount": t.get("amount"),
                        "category": t.get("category"),
                    }
                    for t in txs
                ]
            except Exception:
                raw["recent_transactions"] = []

        return {
            "ok": True,
            "month": month,
            "summary": summary_text,
            "raw": raw,
        }

    except Exception as exc:
        return {"ok": False, "error": str(exc), "month": month}


register(
    "apollo.budget_query",
    budget_query,
    {
        "name": "apollo.budget_query",
        "title": "Apollo Budget Query",
        "summary": (
            "Query Apollo's budget store for a given month: spending totals, "
            "overspent categories, top expenses, and recent transactions. "
            "Returns both a text summary block and structured raw data."
        ),
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)
