from __future__ import annotations

from Apollo import premarket_sim_readiness as gate


def test_stress_sandbox_config_blocks_non_paper_sim_env(monkeypatch):
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "alpaca")
    result = gate.check_stress_sandbox_config()

    assert result["ok"] is False
    assert "simulation_broker_env_not_paper_sim:alpaca" in result["detail"]["blockers"]


def test_stress_sandbox_config_passes_default_paper_sim(monkeypatch):
    monkeypatch.delenv("APOLLO_SIM_BROKER_PROVIDER", raising=False)
    monkeypatch.delenv("APOLLO_SIM_EXECUTION_MODE", raising=False)
    result = gate.check_stress_sandbox_config()

    assert result["ok"] is True
    assert result["detail"]["config"]["provider"] == "paper_sim"
    assert result["detail"]["config"]["live_trade_execution"] is False
    assert result["detail"]["config"]["human_approval_required"] is True


def test_provider_freshness_fails_on_stale_ticker(monkeypatch):
    def fake_http_json(url, *, timeout=8.0):
        return {
            "ok": True,
            "status_code": 200,
            "body": {
                "ok": True,
                "provider_health": {
                    "tickers": {
                        "SPY": {"ok": True, "stale_days": 1, "latest_date": "2026-06-29"},
                        "QQQ": {"ok": True, "stale_days": 9, "latest_date": "2026-06-20"},
                    }
                },
            },
        }

    monkeypatch.setattr(gate, "_http_json", fake_http_json)
    result = gate.check_provider_freshness(max_stale_days=4)

    assert result["ok"] is False
    assert result["detail"]["stale_rows"][0]["ticker"] == "QQQ"


def test_run_readiness_gate_aggregates_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "REPORT_ROOT", tmp_path)
    monkeypatch.setattr(gate, "check_apollo_health", lambda base_url: gate._phase("apollo_health", True, {}))
    monkeypatch.setattr(gate, "check_provider_freshness", lambda base_url, max_stale_days: gate._phase("provider_freshness", True, {}))
    monkeypatch.setattr(gate, "check_daily_report_open", lambda report_date: gate._phase("daily_report_open", True, {}))
    monkeypatch.setattr(gate, "check_stress_sandbox_config", lambda: gate._phase("stress_sandbox_config", False, {"blockers": ["stress_provider_not_paper_sim"]}))
    monkeypatch.setattr(gate, "check_aggressive_probe_suite", lambda run_id: gate._phase("aggressive_probe_suite", True, {}))

    result = gate.run_readiness_gate(run_id="unit_gate", write_reports=True)

    assert result["ok"] is False
    assert result["decision"] == "blocked"
    assert result["failed_required"] == ["stress_sandbox_config"]
    assert (tmp_path / "unit_gate" / "status.json").exists()
    assert (tmp_path / "latest" / "report.md").exists()
