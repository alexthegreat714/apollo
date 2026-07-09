from __future__ import annotations

from Apollo import app as app_mod
from Apollo import swing_simulation


def _latest_study():
    return {
        "run_id": "swing_unit",
        "created_at": "2026-05-14T21:14:42Z",
        "best_proposal": {"ticker": "AVGO"},
        "proposals": [
            {
                "ticker": "CAT",
                "setup_type": "trend_continuation",
                "score": 85,
                "risk_reward_estimate": 0.45,
                "entry_zone": "888.71-909.85",
                "stop_zone": "754.45",
                "target_zone": "968.76",
                "relative_strength_rank": 87.34,
                "status": "new",
            },
            {
                "ticker": "AVGO",
                "setup_type": "pullback_to_trend",
                "score": 77,
                "risk_reward_estimate": 1.4,
                "entry_zone": "220.00-224.00",
                "stop_zone": "210.00",
                "target_zone": "242.00",
                "relative_strength_rank": 78.96,
                "status": "new",
            },
        ],
    }


def test_simulation_account_defaults_to_100_and_plans_next_cycle(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_SIM_MAIN_LANE", "conservative")
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")

    payload = swing_simulation.get_simulation_account(latest_study=_latest_study())

    assert payload["ok"] is True
    assert payload["account"]["starting_balance"] == 100.0
    assert payload["account"]["cash_balance"] == 100.0
    assert payload["account"]["invested_amount"] == 0.0
    assert payload["next_cycle_plan"]["planned_investment"] == 40.0
    assert payload["next_cycle_plan"]["planned_cash_reserve"] == 60.0
    assert payload["next_cycle_plan"]["allocations"][0]["ticker"] == "AVGO"
    assert "Starting balance: $100.00" in payload["report"]["markdown"]
    assert "Planned investment: $40.00" in payload["report"]["markdown"]
    assert payload["next_cycle_plan"]["risk_tier"] == "tier_1_baseline"
    assert "## Risk Ladder" in payload["report"]["markdown"]


def test_default_main_lane_uses_aggressive_news_cycle(monkeypatch, tmp_path):
    monkeypatch.delenv("APOLLO_SIM_MAIN_LANE", raising=False)
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    study = {
        "run_id": "unit_aggressive",
        "proposals": [
            {
                "ticker": "TSM",
                "setup_type": "pullback_to_trend",
                "score": 80.28,
                "risk_reward_estimate": 2.64,
                "entry_zone": "429.96-449.78",
                "stop_zone": "416.99",
                "target_zone": "500.21",
                "relative_strength_rank": 84.0,
                "status": "new",
                "catalyst_quality_score": 95.66,
                "catalyst_summary": ["TSMC beat estimates and raised guidance on AI demand."],
                "source_links": ["https://example.test/tsm"],
            }
        ],
    }

    payload = swing_simulation.get_simulation_account(latest_study=study)
    plan = payload["next_cycle_plan"]
    allocation = plan["allocations"][0]

    assert plan["main_lane"] == "aggressive_news_cycle"
    assert plan["status"] == "queued_for_next_cycle"
    assert plan["planned_investment"] == 100.0
    assert allocation["ticker"] == "TSM"
    assert allocation["lane_profile"] == "pressed"
    assert allocation["pressed_aggressive"] is True
    assert allocation["paper_only"] is True
    assert allocation["live_trade_execution"] is False
    assert allocation["human_approval_required"] is True
    assert allocation["production_readiness_credit"] is False
    assert "Main simulation lane: `aggressive_news_cycle`" in payload["report"]["markdown"]
    assert "## Aggressive News-Cycle Lane" in payload["report"]["markdown"]


def test_aggressive_news_cycle_ignores_expired_setups(monkeypatch, tmp_path):
    monkeypatch.delenv("APOLLO_SIM_MAIN_LANE", raising=False)
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    study = {
        "run_id": "unit_expired_filter",
        "proposals": [
            {
                "ticker": "OLD",
                "setup_type": "trend_continuation",
                "score": 99,
                "risk_reward_estimate": 8.0,
                "entry_zone": "10.00-11.00",
                "stop_zone": "8.00",
                "target_zone": "20.00",
                "status": "expired",
                "catalyst_quality_score": 100,
                "catalyst_summary": ["Expired but otherwise tempting catalyst."],
                "source_links": ["https://example.test/old"],
            },
            {
                "ticker": "FRESH",
                "setup_type": "pullback_to_trend",
                "score": 78,
                "risk_reward_estimate": 2.1,
                "entry_zone": "20.00-22.00",
                "stop_zone": "18.00",
                "target_zone": "30.00",
                "status": "new",
                "catalyst_quality_score": 90,
                "catalyst_summary": ["Fresh catalyst with confirmed setup."],
                "source_links": ["https://example.test/fresh"],
            },
        ],
    }

    payload = swing_simulation.get_simulation_account(latest_study=study)
    plan = payload["next_cycle_plan"]

    assert plan["status"] == "queued_for_next_cycle"
    assert [item["ticker"] for item in plan["allocations"]] == ["FRESH"]


def test_api_simulation_account_route_returns_report(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_SIM_MAIN_LANE", "conservative")
    monkeypatch.setattr(app_mod, "_require_local_token", lambda: None)
    monkeypatch.setattr(app_mod.apollo_swing_study, "latest_swing_study", _latest_study)
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")

    client = app_mod.app.test_client()
    response = client.get("/api/simulation/account")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["account"]["market_value"] == 100.0
    assert payload["next_cycle_plan"]["allocations"][0]["ticker"] == "AVGO"
    assert "Apollo Paper Swing Simulation Report" in payload["report"]["markdown"]


def test_account_report_uses_previous_close_as_day_start(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    monkeypatch.setattr(swing_simulation, "_today_date", lambda: "2026-07-05")
    account = {
        "account_id": "unit",
        "mode": "paper_sim",
        "starting_balance": 100.0,
        "cash_balance": 101.09,
        "invested_amount": 0.0,
        "market_value": 101.09,
        "status": "paper_closed",
        "open_positions": [],
        "closed_positions": [],
        "equity_curve": [
            {"date": "2026-07-03", "value": 100.72},
            {"date": "2026-07-04", "value": 101.09},
        ],
    }
    swing_simulation.SIMULATION_ACCOUNT_PATH.write_text(__import__("json").dumps(account), encoding="utf-8")

    payload = swing_simulation.get_simulation_account(latest_study={"proposals": []})

    assert payload["account"]["starting_balance"] == 100.0
    assert payload["account"]["day_starting_balance"] == 101.09
    assert payload["account"]["previous_close_balance"] == 101.09
    assert "Starting balance: $101.09 (previous close 2026-07-04)" in payload["report"]["markdown"]
    assert "Original paper seed: $100.00" in payload["report"]["markdown"]


def test_due_pending_plan_opens_paper_position(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    monkeypatch.setattr(swing_simulation, "_today_date", lambda: "2026-05-20")
    monkeypatch.setattr(swing_simulation, "_tomorrow_date", lambda: "2026-05-21")
    account = {
        "account_id": "unit",
        "mode": "paper_sim",
        "starting_balance": 100.0,
        "cash_balance": 100.0,
        "invested_amount": 0.0,
        "market_value": 100.0,
        "status": "not_started",
        "open_positions": [],
        "closed_positions": [],
        "pending_plan": {
            "status": "queued_for_next_cycle",
            "cycle_date": "2026-05-20",
            "plan_id": "unit-plan",
            "source_run_id": "swing_unit",
            "allocations": [
                {
                    "ticker": "AVGO",
                    "amount": 40.0,
                    "entry_mid": 222.0,
                    "entry_zone": "220.00-224.00",
                    "stop_zone": "210.00",
                    "target_zone": "242.00",
                    "setup_type": "pullback_to_trend",
                    "score": 77,
                    "risk_reward": 1.4,
                }
            ],
        },
    }
    swing_simulation.SIMULATION_ACCOUNT_PATH.write_text(__import__("json").dumps(account), encoding="utf-8")
    study = _latest_study()
    study["proposals"][1]["swing_snapshot"] = {
        "close": 230.0,
        "chart": {"candles": [{"date": "2026-05-20", "open": 221.0, "high": 230.0, "low": 219.0, "close": 230.0}]},
    }

    payload = swing_simulation.get_simulation_account(latest_study=study)

    assert payload["account"]["status"] == "paper_open"
    assert payload["account"]["cash_balance"] == 60.0
    assert payload["account"]["open_positions"][0]["ticker"] == "AVGO"
    assert payload["account"]["open_positions"][0]["fill_source"] == "daily_candle_entry_touch"
    assert payload["account"]["unrealized_return"] > 0


def test_risk_tier_caps_control_next_cycle_allocation(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_SIM_MAIN_LANE", "conservative")
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    study = _latest_study()
    account = {
        "cash_balance": 100.0,
        "open_positions": [],
    }

    measured = swing_simulation._build_next_cycle_plan(
        account,
        study,
        risk_policy={
            "risk_tier": "tier_2_measured",
            "risk_tier_label": "measured",
            "allocation_caps": {"deploy_cap_pct": 80.0, "max_position_pct": 45.0},
        },
    )
    confident = swing_simulation._build_next_cycle_plan(
        account,
        study,
        risk_policy={
            "risk_tier": "tier_3_confident",
            "risk_tier_label": "confident",
            "allocation_caps": {"deploy_cap_pct": 85.0, "max_position_pct": 50.0},
        },
    )
    hold = swing_simulation._build_next_cycle_plan(
        account,
        study,
        risk_policy={
            "risk_tier": "tier_0_hold",
            "risk_tier_label": "hold",
            "allocation_caps": {"deploy_cap_pct": 0.0, "max_position_pct": 0.0},
        },
    )

    assert measured["planned_investment"] == 45.0
    assert measured["deploy_cap_pct"] == 80.0
    assert measured["max_position_pct"] == 45.0
    assert confident["planned_investment"] == 50.0
    assert hold["planned_investment"] == 0.0
    assert hold["status"] == "risk_hold"


def test_due_pending_plan_waits_for_real_daily_candle(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    monkeypatch.setattr(swing_simulation, "_today_date", lambda: "2026-05-20")
    account = {
        "account_id": "unit",
        "mode": "paper_sim",
        "starting_balance": 100.0,
        "cash_balance": 100.0,
        "invested_amount": 0.0,
        "market_value": 100.0,
        "status": "not_started",
        "open_positions": [],
        "closed_positions": [],
        "pending_plan": {
            "status": "queued_for_next_cycle",
            "cycle_date": "2026-05-20",
            "plan_id": "unit-plan",
            "source_run_id": "swing_unit",
            "allocations": [
                {
                    "ticker": "AVGO",
                    "amount": 40.0,
                    "entry_mid": 222.0,
                    "entry_zone": "220.00-224.00",
                    "stop_zone": "210.00",
                    "target_zone": "242.00",
                }
            ],
        },
    }
    swing_simulation.SIMULATION_ACCOUNT_PATH.write_text(__import__("json").dumps(account), encoding="utf-8")
    study = _latest_study()
    study["proposals"][1]["swing_snapshot"] = {"chart": {"candles": [{"date": "2026-05-19", "open": 230.0, "high": 231.0, "low": 229.0, "close": 230.0}]}}

    payload = swing_simulation.get_simulation_account(latest_study=study)

    assert payload["account"]["open_positions"] == []
    assert payload["account"]["cash_balance"] == 100.0
    assert payload["account"]["pending_plan"]["defer_reason"] == "waiting_for_real_daily_entry_touch"
    assert payload["account"]["pending_plan"]["allocations"][0]["defer_reason"] == "waiting_for_cycle_daily_candle"


def test_simulation_validator_blocks_unvalidated_profit_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    monkeypatch.setattr(swing_simulation, "OUTCOMES_PATH", tmp_path / "outcomes.json")
    monkeypatch.setattr(swing_simulation, "SIMULATIONS_PATH", tmp_path / "simulations")
    monkeypatch.setattr(
        swing_simulation,
        "_fetch_history",
        lambda ticker, days=90: [
            {"date": "2026-05-20", "open": 100.0, "high": 106.0, "low": 99.0, "close": 104.0, "volume": 1000}
        ],
    )

    result = swing_simulation.run_simulation(
        {
            "ticker": "GOOGL",
            "entry_zone": "100.00-101.00",
            "stop_zone": "102.00",
            "target_zone": "110.00",
            "created_at": "2026-05-20",
        },
        save=False,
    )

    assert result["status"] == "invalid_setup"
    assert result["simulation_math_warning"] is True
    assert any(warning.get("warning_class") == "invalid_long_stop_above_entry" for warning in result["simulation_warnings"])


def test_aggressive_probe_simulation_is_labeled_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    monkeypatch.setattr(swing_simulation, "OUTCOMES_PATH", tmp_path / "outcomes.json")
    monkeypatch.setattr(swing_simulation, "SIMULATIONS_PATH", tmp_path / "simulations")
    monkeypatch.setattr(
        swing_simulation,
        "_fetch_history",
        lambda ticker, days=90: [
            {"date": "2026-05-20", "open": 100.0, "high": 112.5, "low": 99.0, "close": 110.0, "volume": 2_000_000}
        ],
    )

    result = swing_simulation.run_simulation(
        {
            "ticker": "DDOG",
            "entry_zone": "100.00-101.00",
            "stop_zone": "95.00",
            "target_zone": "112.00",
            "risk_tier": "aggressive_probe",
            "paper_only": True,
            "paper_risk_pct": 0.005,
            "max_notional_pct": 0.05,
            "aggressive_probe_ready": True,
            "aggressive_reason": "rr_asymmetric; event_catalyst",
            "aggressive_next_action": "paper_aggressive_ready",
            "avg_volume_30d": 2_000_000,
            "created_at": "2026-05-20",
        },
        save=False,
    )

    assert result["risk_tier"] == "aggressive_probe"
    assert result["paper_only"] is True
    assert result["aggressive_probe_ready"] is True
    assert result["live_trade_execution"] is False
    assert result["paper_risk_pct"] == 0.005
    assert result["max_notional_pct"] == 0.05
    assert isinstance(result["execution_realism"], dict)
    assert result["execution_realism"]["execution_realism_score"] <= 100


def test_simulation_validator_allows_explicit_profit_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_simulation, "SIMULATION_ACCOUNT_PATH", tmp_path / "account_state.json")
    monkeypatch.setattr(swing_simulation, "SIMULATION_REPORTS_PATH", tmp_path / "reports")
    monkeypatch.setattr(swing_simulation, "OUTCOMES_PATH", tmp_path / "outcomes.json")
    monkeypatch.setattr(swing_simulation, "SIMULATIONS_PATH", tmp_path / "simulations")
    monkeypatch.setattr(
        swing_simulation,
        "_fetch_history",
        lambda ticker, days=90: [
            {"date": "2026-05-20", "open": 100.0, "high": 106.0, "low": 99.0, "close": 104.0, "volume": 1000}
        ],
    )

    result = swing_simulation.run_simulation(
        {
            "ticker": "GOOGL",
            "entry_zone": "100.00-101.00",
            "stop_zone": "102.00",
            "target_zone": "110.00",
            "created_at": "2026-05-20",
            "profit_stop_validated": True,
        },
        save=False,
    )

    assert result["status"] == "stopped_out"
    assert result["simulation_math_warning"] is True
    assert any(warning.get("warning_class") == "valid_profit_stop" for warning in result["simulation_warnings"])
