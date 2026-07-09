from __future__ import annotations

import importlib


def test_apollo_chat_runs_market_study_from_prompt(monkeypatch):
    monkeypatch.setenv("SKY_ALLOW_ANON", "1")
    monkeypatch.setenv("APOLLO_ALLOW_CHAT_PROOFS", "1")
    app_mod = importlib.import_module("Apollo.app")

    captured = {}

    def _fake_market_study(payload):
        captured.update(payload)
        return {
            "ok": True,
            "run_id": "market_prompt_123",
            "high_confidence": False,
            "best_proposal": {"ticker": "NVDA", "direction_bias": "watch_only", "confidence": 64},
            "artifacts": {"report_path": "logs/market_study/report.md"},
        }

    monkeypatch.setattr(app_mod.apollo_market_study, "build_market_study", _fake_market_study)

    client = app_mod.app.test_client()
    response = client.post("/chat", json={"message": "run market research tickers: NVDA, AMD", "conversation_id": "market-proof"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["reasoning"] == "(apollo-market_study)"
    assert captured["tickers"] == "NVDA,AMD"
    assert "market research proof complete" in payload["reply"].lower()
    assert "Research-only" in payload["reply"]


def test_apollo_chat_runs_stock_trade_proof_from_prompt(monkeypatch):
    monkeypatch.setenv("SKY_ALLOW_ANON", "1")
    monkeypatch.setenv("APOLLO_ALLOW_CHAT_PROOFS", "1")
    app_mod = importlib.import_module("Apollo.app")

    captured = {}

    def _fake_trade_plan(payload):
        captured.update(payload)
        return {
            "ok": True,
            "run_id": "trade_prompt_123",
            "trade_plan": {"decision": "research_candidate", "ticker": "AMD", "score": 71},
            "artifacts": {"report_path": "logs/homework_trade/report.md"},
        }

    monkeypatch.setattr(app_mod.apollo_homework_trade_pipeline, "build_homework_trade_plan", _fake_trade_plan)

    client = app_mod.app.test_client()
    response = client.post("/chat", json={"message": "stock trade proof tickers: AMD", "conversation_id": "trade-proof"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["reasoning"] == "(apollo-trade_proof)"
    assert captured["tickers"] == "AMD"
    assert captured["upstream_ocr_status"] == "prompt_proof"
    assert "live_trade_execution=false" in payload["reply"]
