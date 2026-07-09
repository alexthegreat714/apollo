from __future__ import annotations

from pathlib import Path

from Apollo import day_trade_cycle, simulation_execution


def _set_execution_root(monkeypatch, tmp_path: Path) -> None:
    execution_root = tmp_path / "execution"
    plans_dir = execution_root / "plans"
    queue_path = execution_root / "plan_queue.json"
    submit_window = execution_root / "submission_rate_windows.json"
    plans_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_ROOT", execution_root)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_PLANS", plans_dir)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_QUEUE", queue_path)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_SUBMIT_WINDOW", submit_window)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_RECONCILE_LOCK", execution_root / "reconcile.lock")


def _swing_study_payload():
    return {
        "ok": True,
        "run_id": "swing_day_trade_unit",
        "created_at": "2026-05-15T13:15:00Z",
        "source_health": {"daily_candles": {"ok": True}},
        "provider_health": {"NVDA": {"ok": True}},
        "proposals": [
            {
                "ticker": "NVDA",
                "direction_bias": "long_bias",
                "setup_type": "base_breakout",
                "score": 88.5,
                "confidence": 88.5,
                "confidence_label": "high",
                "entry_zone": "100.00-102.00",
                "stop_zone": "95.00",
                "target_zone": "112.00",
                "no_trade_reason": "",
                "human_approval_required": True,
                "live_trade_execution": False,
            }
        ],
        "best_proposal": {
            "ticker": "NVDA",
            "direction_bias": "long_bias",
            "setup_type": "base_breakout",
            "score": 88.5,
            "confidence": 88.5,
            "confidence_label": "high",
            "entry_zone": "100.00-102.00",
            "stop_zone": "95.00",
            "target_zone": "112.00",
            "no_trade_reason": "",
            "human_approval_required": True,
            "live_trade_execution": False,
        },
    }


def test_day_trade_cycle_proves_paper_lifecycle_and_live_guard(tmp_path, monkeypatch):
    _set_execution_root(monkeypatch, tmp_path)
    monkeypatch.setattr(day_trade_cycle, "RUN_ROOT", tmp_path / "day_trade")
    monkeypatch.setattr(day_trade_cycle.swing_study, "build_swing_study", lambda payload: _swing_study_payload())
    monkeypatch.setattr(day_trade_cycle, "_alpaca_paper_credentials_present", lambda: False)
    monkeypatch.setenv("APOLLO_SIM_LIVE_ENABLED", "0")
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "500")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "10")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_MAX_OPEN_POSITIONS", "5")
    monkeypatch.setenv("APOLLO_SIM_SIMULATED_BUYING_POWER", "50000")

    result = day_trade_cycle.run_day_trade_cycle(run_id="unit_day_trade", root=tmp_path / "day_trade")

    assert result["ok"] is True
    assert result["status"] == "passed"
    assert result["candidate"]["ticker"] == "NVDA"
    assert result["paper_sim"]["ok"] is True
    assert result["paper_sim"]["provider"] == "paper_sim"
    assert result["paper_sim"]["order_ids"]
    assert result["live_guard"]["ok"] is True
    assert result["live_guard"]["detail"] == "live_execution_disabled"
    assert result["alpaca_paper"]["status"] == "alpaca_paper_skipped_missing_credentials"
    assert result["score"] >= 88
    assert (tmp_path / "day_trade" / "unit_day_trade" / "status.json").exists()
    assert (tmp_path / "day_trade" / "unit_day_trade" / "report.md").exists()

    plans = simulation_execution.list_simulation_execution_plans(limit=10)
    providers = [((row.get("broker") or {}).get("provider")) for row in plans["plans"]]
    assert "paper_sim" in providers
    assert all((row.get("execution_type") == "paper_only") for row in plans["plans"])
