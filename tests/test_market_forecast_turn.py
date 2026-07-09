from __future__ import annotations

from datetime import date

from Apollo import market_forecast_turn


def _event_result():
    return {
        "ok": True,
        "summaries": [
            {"ticker": "SPY", "ok": True, "impact_score": 58, "event_score_component": 0, "directional_bias": "neutral"},
            {"ticker": "QQQ", "ok": True, "impact_score": 62, "event_score_component": 1.0, "directional_bias": "positive"},
            {"ticker": "NVDA", "ok": True, "impact_score": 76, "event_score_component": 4.0, "directional_bias": "positive"},
        ],
        "broker_readiness": {"human_approval_required": True, "live_trade_execution": False, "broker_order_created": False},
    }


def _swing_result():
    return {
        "ok": True,
        "proposals": [
            {"ticker": "SPY", "score": 62, "direction_bias": "no_trade", "status": "expired", "no_trade_reason": "extended from trend support"},
            {"ticker": "QQQ", "score": 70, "direction_bias": "long_bias", "status": "new", "no_trade_reason": ""},
            {"ticker": "NVDA", "score": 84, "direction_bias": "long_bias", "status": "new", "no_trade_reason": "", "live_trade_execution": False},
        ],
        "best_proposal": {"ticker": "NVDA", "score": 84, "live_trade_execution": False},
    }


def test_next_trading_session_skips_weekend():
    assert market_forecast_turn.next_trading_session(date(2026, 5, 9)).isoformat() == "2026-05-11"
    assert market_forecast_turn.next_trading_session(date(2026, 5, 11)).isoformat() == "2026-05-11"


def test_forecast_turn_simulates_weekend_to_next_session(monkeypatch, tmp_path):
    monkeypatch.setattr(market_forecast_turn, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(market_forecast_turn.event_impact, "build_event_impact", lambda payload: _event_result())
    monkeypatch.setattr(market_forecast_turn.swing_study, "build_swing_study", lambda payload: _swing_result())

    result = market_forecast_turn.build_market_forecast_turn(
        {
            "tickers": "SPY,QQQ,NVDA",
            "as_of": "2026-05-08T22:00:00-05:00",
            "simulate_at": "2026-05-09T12:00:00-05:00",
            "write_artifacts": True,
        }
    )

    assert result["ok"] is True
    assert result["requested_date"] == "2026-05-09"
    assert result["next_trading_session"] == "2026-05-11"
    assert result["session_adjustment"] == "weekend_to_next_trading_session"
    assert result["simulation"]["status"] == "weekend_monitoring"
    assert result["broker_readiness"]["live_trade_execution"] is False
    assert result["broker_readiness"]["broker_order_created"] is False
    assert result["artifacts"]["report_path"]


def test_forecast_turn_skips_ocr97_without_documents(monkeypatch):
    monkeypatch.setattr(market_forecast_turn.event_impact, "build_event_impact", lambda payload: _event_result())
    monkeypatch.setattr(market_forecast_turn.swing_study, "build_swing_study", lambda payload: _swing_result())

    result = market_forecast_turn.build_market_forecast_turn({"tickers": "NVDA", "write_artifacts": False})

    assert result["ocr97"]["used"] is False
    assert result["ocr97"]["mode"] == "skipped_no_documents"


def test_market_forecast_admin_routes(monkeypatch):
    from Apollo import app as app_mod

    monkeypatch.setattr(app_mod, "_require_local_token", lambda: None)
    monkeypatch.setattr(app_mod.apollo_market_forecast_turn, "build_market_forecast_turn", lambda payload: {"ok": True, "payload": payload, "broker_readiness": {"live_trade_execution": False}})
    monkeypatch.setattr(app_mod.apollo_market_forecast_turn, "latest_market_forecast_turn", lambda: {"ok": True, "forecast": {}})
    monkeypatch.setattr(app_mod.apollo_market_forecast_turn, "market_forecast_health", lambda: {"ok": True, "broker_readiness": {"live_trade_execution": False}})
    client = app_mod.app.test_client()

    assert client.post("/admin/market_forecast_turn/run", json={"tickers": "NVDA"}).get_json()["ok"] is True
    assert client.get("/admin/market_forecast_turn/latest").get_json()["ok"] is True
    health = client.get("/admin/market_forecast_turn/health").get_json()
    assert health["broker_readiness"]["live_trade_execution"] is False
