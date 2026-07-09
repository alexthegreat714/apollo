from __future__ import annotations

from flask import Flask

from Apollo import mcp_routes


def _client():
    app = Flask(__name__)
    app.register_blueprint(mcp_routes.mcp_bp)
    return app.test_client()


def test_mcp_list_returns_only_exposed_tools(monkeypatch):
    monkeypatch.setattr(mcp_routes, "_authorized", lambda headers: True)
    monkeypatch.setattr(mcp_routes, "verify_interagent_request", lambda request: (True, "", {}))
    monkeypatch.setattr(mcp_routes, "ensure_loaded", lambda: None)
    monkeypatch.setattr(
        mcp_routes,
        "_REGISTRY",
        {
            "apollo.calculator": object(),
            "apollo.budget_query": object(),
            "apollo.financial_news": object(),
            "apollo.ebay_listing": object(),
            "apollo.event_impact": object(),
            "apollo.event_swing_study": object(),
            "apollo.event_watchlist": object(),
            "apollo.event_impact_health": object(),
            "apollo.market_forecast_turn": object(),
            "apollo.market_forecast_health": object(),
            "apollo.market_snapshot": object(),
            "apollo.market_catalysts": object(),
            "apollo.market_study": object(),
            "apollo.trade_proposal": object(),
            "apollo.swing_snapshot": object(),
            "apollo.swing_study": object(),
            "apollo.swing_watchlist": object(),
            "apollo.swing_outcomes": object(),
            "apollo.swing_provider_health": object(),
            "web.search": object(),
        },
    )
    monkeypatch.setattr(
        mcp_routes,
        "evaluate_tool_dispatch",
        lambda agent_name, tool_name, mutates, payload: {"allowed": True, "tool_scope": "readonly"},
    )
    client = _client()
    resp = client.get("/mcp/list")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert sorted(body["tools"]) == [
        "apollo.budget_query",
        "apollo.calculator",
        "apollo.ebay_listing",
        "apollo.event_impact",
        "apollo.event_impact_health",
        "apollo.event_swing_study",
        "apollo.event_watchlist",
        "apollo.financial_news",
        "apollo.market_catalysts",
        "apollo.market_forecast_health",
        "apollo.market_forecast_turn",
        "apollo.market_snapshot",
        "apollo.market_study",
        "apollo.swing_outcomes",
        "apollo.swing_provider_health",
        "apollo.swing_snapshot",
        "apollo.swing_study",
        "apollo.swing_watchlist",
        "apollo.trade_proposal",
    ]
    assert "web.search" not in body["tools"]


def test_mcp_run_executes_exposed_tool(monkeypatch):
    monkeypatch.setattr(mcp_routes, "_authorized", lambda headers: True)
    monkeypatch.setattr(mcp_routes, "verify_interagent_request", lambda request: (True, "", {}))
    monkeypatch.setattr(mcp_routes, "ensure_loaded", lambda: None)
    monkeypatch.setattr(mcp_routes, "tool_meta", lambda name: {"readonly": True})
    monkeypatch.setattr(
        mcp_routes,
        "evaluate_tool_dispatch",
        lambda agent_name, tool_name, mutates, payload: {"allowed": True, "tool_scope": "readonly"},
    )
    monkeypatch.setattr(
        mcp_routes,
        "build_mcp_envelope",
        lambda started_at, agent_name, tool_name, source, decision: {"agent": agent_name, "tool": tool_name},
    )
    monkeypatch.setattr(mcp_routes, "run", lambda name, payload: {"answer": 42, "name": name, "payload": payload})
    client = _client()
    resp = client.post("/mcp/run", json={"name": "apollo.calculator", "payload": {"expr": "6*7"}})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["result"]["answer"] == 42


def test_mcp_run_blocks_unexposed_tool(monkeypatch):
    monkeypatch.setattr(mcp_routes, "_authorized", lambda headers: True)
    monkeypatch.setattr(mcp_routes, "verify_interagent_request", lambda request: (True, "", {}))
    client = _client()
    resp = client.post("/mcp/run", json={"name": "web.search", "payload": {"query": "x"}})
    body = resp.get_json()
    assert resp.status_code == 403
    assert body["ok"] is False
    assert body["error"] == "tool_not_exposed"
