from __future__ import annotations

from typing import Any, Dict, Optional

from apollo_budget.context import build_budget_context
from apollo_budget.store import get_store, infer_excel_mapping
from apollo_ingest.xlsx_ingest import read_xlsx


def budget_import_csv(path: str, month: Optional[str] = None) -> Dict[str, Any]:
    store = get_store()
    return store.import_everydollar_csv(path, month=month)


def budget_import_excel(path: str, mapping: Optional[Dict[str, str]] = None, month: Optional[str] = None) -> Dict[str, Any]:
    store = get_store()
    sheets = read_xlsx(path)
    if not sheets:
        return {"ok": False, "error": "empty_excel"}
    sheet_name = list(sheets.keys())[0]
    rows = sheets[sheet_name]
    if not rows:
        return {"ok": False, "error": "empty_rows"}
    effective_mapping = mapping or infer_excel_mapping(rows[0].keys())
    return store.import_excel_rows(rows, effective_mapping, month=month)


def budget_get_summary(month: str = "") -> Dict[str, Any]:
    store = get_store()
    return store.get_summary(month)


def budget_get_category_status(month: str = "") -> Dict[str, Any]:
    store = get_store()
    return store.get_categories(month)


def budget_get_transactions(month: str = "") -> Dict[str, Any]:
    store = get_store()
    return store.get_transactions(month)


def budget_get_insights(month: str = "") -> Dict[str, Any]:
    store = get_store()
    latest = store.latest_insight(month) if month else None
    if not latest:
        return {"ok": False, "error": "no_insights"}
    return {
        "ok": True,
        "month": month,
        "summary": latest.get("summary"),
        "sources": latest.get("sources_json"),
    }


def budget_get_context(month: str = "") -> Dict[str, Any]:
    store = get_store()
    return {"ok": True, "context": build_budget_context(store, month)}


def budget_export(month: str = "") -> Dict[str, Any]:
    store = get_store()
    return {"ok": True, "csv": store.export_csv(month)}
