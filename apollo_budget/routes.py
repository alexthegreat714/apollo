from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request, send_file

from apollo_budget.context import build_budget_context
from apollo_budget.insights import refresh_insights
from apollo_budget.store import get_store, infer_excel_mapping
from apollo_ingest.xlsx_ingest import read_xlsx


bp_budget = Blueprint("budget", __name__)
RAG = None


def _month_from_req() -> str:
    return request.args.get("month") or (request.get_json(silent=True) or {}).get("month") or ""


@bp_budget.route("/budget/summary", methods=["GET"])
def budget_summary():
    month = _month_from_req() or ""
    store = get_store()
    return jsonify(store.get_summary(month))


@bp_budget.route("/budget/categories", methods=["GET"])
def budget_categories():
    month = _month_from_req() or ""
    store = get_store()
    return jsonify(store.get_categories(month))


@bp_budget.route("/budget/accounts", methods=["GET"])
def budget_accounts():
    store = get_store()
    return jsonify(store.get_accounts())


@bp_budget.route("/budget/account", methods=["POST"])
def budget_account():
    data = request.get_json(force=True) or {}
    store = get_store()
    return jsonify(store.add_or_update_account(data))


@bp_budget.route("/budget/transactions", methods=["GET"])
def budget_transactions():
    month = _month_from_req() or ""
    store = get_store()
    return jsonify(store.get_transactions(month))


@bp_budget.route("/budget/transaction", methods=["POST"])
def budget_transaction():
    data = request.get_json(force=True) or {}
    store = get_store()
    return jsonify(store.add_transaction(data))


@bp_budget.route("/budget/category", methods=["POST"])
def budget_category():
    data = request.get_json(force=True) or {}
    month = data.get("month") or ""
    store = get_store()
    return jsonify(store.add_or_update_category(month, data))


@bp_budget.route("/budget/import/everydollar_csv", methods=["POST"])
def budget_import_everydollar():
    store = get_store()
    month = request.form.get("month") or request.args.get("month")
    file = request.files.get("file")
    if not file:
        body = request.get_json(silent=True) or {}
        path = body.get("path")
        if not path:
            return jsonify({"ok": False, "error": "file_required"}), 400
        result = store.import_everydollar_csv(path, month=month)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    tmp = Path("logs") / file.filename
    tmp.parent.mkdir(parents=True, exist_ok=True)
    file.save(tmp)
    result = store.import_everydollar_csv(str(tmp), month=month)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@bp_budget.route("/budget/import/excel", methods=["POST"])
def budget_import_excel():
    store = get_store()
    preview = request.args.get("preview") == "1"
    mapping_raw = request.form.get("mapping") or request.args.get("mapping")
    if mapping_raw:
        try:
            mapping = json.loads(mapping_raw)
        except json.JSONDecodeError:
            return jsonify({"ok": False, "error": "invalid_mapping_json"}), 400
    else:
        mapping = {}
    month = request.form.get("month") or request.args.get("month") or ""
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "file_required"}), 400
    tmp = Path("logs") / file.filename
    tmp.parent.mkdir(parents=True, exist_ok=True)
    file.save(tmp)
    sheets = read_xlsx(str(tmp))
    if not sheets:
        return jsonify({"ok": False, "error": "empty_excel"}), 400
    sheet_name = list(sheets.keys())[0]
    rows = sheets[sheet_name]
    if preview:
        sample = rows[:5]
        columns = list(sample[0].keys()) if sample else []
        inferred = infer_excel_mapping(columns)
        missing_fields = [field for field in ("date", "payee", "amount") if field not in inferred]
        return jsonify(
            {
                "ok": True,
                "columns": columns,
                "sample": sample,
                "inferred_mapping": inferred,
                "mapping_complete": not missing_fields,
                "missing_fields": missing_fields,
            }
        )

    effective_mapping = dict(mapping or {})
    if not effective_mapping:
        effective_mapping = infer_excel_mapping(rows[0].keys())
    missing_fields = [field for field in ("date", "payee", "amount") if not effective_mapping.get(field)]
    if missing_fields:
        return jsonify(
            {
                "ok": False,
                "error": "ambiguous_mapping",
                "missing_fields": missing_fields,
                "inferred_mapping": effective_mapping,
            }
        ), 400

    result = store.import_excel_rows(rows, effective_mapping, month=month)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@bp_budget.route("/budget/export", methods=["GET"])
def budget_export():
    month = _month_from_req() or ""
    store = get_store()
    csv_text = store.export_csv(month)
    out_path = Path("logs") / f"budget_export_{month or 'latest'}.csv"
    out_path.write_text(csv_text, encoding="utf-8")
    return send_file(str(out_path), mimetype="text/csv", as_attachment=True, download_name=out_path.name)


@bp_budget.route("/budget/paychecks", methods=["GET"])
def budget_paychecks():
    month = _month_from_req() or ""
    store = get_store()
    return jsonify(store.get_paychecks(month))


@bp_budget.route("/budget/paycheck", methods=["POST"])
def budget_paycheck():
    data = request.get_json(force=True) or {}
    month = data.get("month") or ""
    store = get_store()
    return jsonify(store.add_paycheck(month, data))


@bp_budget.route("/budget/paycheck/<paycheck_id>", methods=["DELETE"])
def budget_paycheck_delete(paycheck_id: str):
    store = get_store()
    return jsonify(store.delete_paycheck(paycheck_id))


@bp_budget.route("/budget/goals", methods=["GET"])
def budget_goals():
    store = get_store()
    return jsonify(store.get_goals())


@bp_budget.route("/budget/goal", methods=["POST"])
def budget_goal():
    data = request.get_json(force=True) or {}
    store = get_store()
    return jsonify(store.add_goal(data))


@bp_budget.route("/budget/goal/<goal_id>", methods=["PATCH"])
def budget_goal_update(goal_id: str):
    data = request.get_json(force=True) or {}
    store = get_store()
    return jsonify(store.update_goal(goal_id, data))


@bp_budget.route("/budget/insights", methods=["GET"])
def budget_insights():
    month = _month_from_req() or ""
    store = get_store()
    latest = store.latest_insight(month) if month else None
    if latest:
        return jsonify({"ok": True, "month": month, "summary": latest.get("summary"), "sources": json.loads(latest.get("sources_json") or "[]")})
    return jsonify({"ok": True, "month": month, "summary": "", "sources": []})


@bp_budget.route("/budget/insights/refresh", methods=["POST"])
def budget_insights_refresh():
    month = _month_from_req() or ""
    store = get_store()
    if RAG is None:
        return jsonify({"ok": False, "error": "rag_unavailable"}), 503
    result = refresh_insights(RAG, month or "")
    if result.get("ok"):
        store.store_insight(month or "", result.get("summary", ""), result.get("sources", []))
    return jsonify(result)


@bp_budget.route("/budget/context", methods=["GET"])
def budget_context():
    month = _month_from_req() or ""
    store = get_store()
    return jsonify({"ok": True, "context": build_budget_context(store, month or "")})


def register_budget_routes(app, rag) -> None:
    global RAG
    RAG = rag
    app.register_blueprint(bp_budget)
