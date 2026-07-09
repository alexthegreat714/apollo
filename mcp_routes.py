from __future__ import annotations

import os
import time
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from common.agent_auth import verify_interagent_request
from common.auth import require_token
from common.mcp import _REGISTRY, ensure_loaded, run, tool_meta
from common.mcp_policy import MCP_POLICY_SCHEMA_VERSION, build_mcp_envelope, evaluate_tool_dispatch

# Apollo-owned tools are imported here so /mcp/list exposes them even though the
# shared MCP manifest is intentionally conservative about agent-specific modules.
try:  # pragma: no cover - import side effect only
    from Apollo.apollo_tools import mcp_event_impact  # noqa: F401
    from Apollo.apollo_tools import mcp_ebay_listing  # noqa: F401
    from Apollo.apollo_tools import mcp_market_study  # noqa: F401
    from Apollo.apollo_tools import mcp_swing_study  # noqa: F401
except Exception:
    pass


mcp_bp = Blueprint("apollo_mcp", __name__)

_APOLLO_EXPOSED_TOOLS = {
    "apollo.calculator",
    "apollo.budget_query",
    "apollo.financial_news",
    "apollo.market_snapshot",
    "apollo.market_catalysts",
    "apollo.market_study",
    "apollo.trade_proposal",
    "apollo.event_impact",
    "apollo.event_swing_study",
    "apollo.event_watchlist",
    "apollo.event_impact_health",
    "apollo.ebay_listing",
    "apollo.market_forecast_turn",
    "apollo.market_forecast_health",
    "apollo.swing_snapshot",
    "apollo.swing_study",
    "apollo.swing_watchlist",
    "apollo.swing_outcomes",
    "apollo.swing_provider_health",
}


def _authorized(headers) -> bool:
    if os.environ.get("APOLLO_ALLOW_ANON", "1") == "1":
        return True
    expected = str(os.environ.get("APOLLO_LOCAL_TOKEN") or os.environ.get("SKY_LOCAL_TOKEN") or "").strip()
    if expected and (
        headers.get("X-Apollo-Token", "") == expected
        or headers.get("X-Sky-Token", "") == expected
    ):
        return True
    return require_token(headers, allow_anon=False)


def _tool_allowed(name: str) -> bool:
    return str(name or "").strip() in _APOLLO_EXPOSED_TOOLS


@mcp_bp.route("/mcp/list", methods=["GET", "POST"])
def mcp_list():
    if not _authorized(request.headers):
        return jsonify({"ok": False, "error": "auth_required"}), 401
    ok, err, _ = verify_interagent_request(request)
    if not ok:
        return jsonify({"ok": False, "error": "signature_required", "detail": err}), 403
    ensure_loaded()
    tools = [name for name in sorted(list(_REGISTRY.keys())) if _tool_allowed(name)]
    scope_map: Dict[str, str] = {}
    for name in tools:
        decision = evaluate_tool_dispatch(agent_name="apollo", tool_name=name, mutates=False, payload={})
        if bool(decision.get("allowed")):
            scope_map[name] = str(decision.get("tool_scope") or "generic")
        else:
            scope_map[name] = "blocked"
    return jsonify(
        {
            "ok": True,
            "tools": tools,
            "policy_schema": MCP_POLICY_SCHEMA_VERSION,
            "tool_scopes": scope_map,
        }
    )


@mcp_bp.route("/mcp/run", methods=["POST"])
def mcp_run():
    started_at = time.perf_counter()
    if not _authorized(request.headers):
        return jsonify({"ok": False, "error": "auth_required"}), 401
    ok, err, _ = verify_interagent_request(request)
    if not ok:
        return jsonify({"ok": False, "error": "signature_required", "detail": err}), 403

    data = request.get_json(force=True) or {}
    name = str(data.get("name") or "").strip()
    payload = data.get("payload", {})
    if not name:
        return jsonify({"ok": False, "error": "name_required"}), 400
    if not _tool_allowed(name):
        return jsonify({"ok": False, "error": "tool_not_exposed", "name": name}), 403
    if not isinstance(payload, dict):
        payload = {}

    meta = tool_meta(name)
    mutates = not bool(meta.get("readonly", True))
    decision = evaluate_tool_dispatch(
        agent_name="apollo",
        tool_name=name,
        mutates=mutates,
        payload=payload,
    )
    envelope = build_mcp_envelope(
        started_at=started_at,
        agent_name="apollo",
        tool_name=name,
        source="mcp.run",
        decision=decision,
    )
    if not bool(decision.get("allowed")):
        return jsonify({"ok": False, "error": "tool_blocked_by_policy", "name": name, "mcp_envelope": envelope}), 403
    try:
        ensure_loaded()
        result = run(name, payload)
        return jsonify({"ok": True, "name": name, "result": result, "mcp_envelope": envelope}), 200
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc), "mcp_envelope": envelope}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": "mcp_failed", "detail": str(exc)[:400], "mcp_envelope": envelope}), 500
