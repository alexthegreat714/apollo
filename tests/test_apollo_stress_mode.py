from __future__ import annotations

from Apollo import simulation_execution, stress_mode, swing_simulation, trade_cycle


def test_stress_mode_defaults_enabled(monkeypatch):
    monkeypatch.delenv("APOLLO_SIM_STRESS_MODE", raising=False)
    monkeypatch.delenv("APOLLO_SIM_STRESS_RISK_PCT", raising=False)

    cfg = stress_mode.resolve_stress_mode(100)

    assert cfg["enabled"] is True
    assert cfg["risk_pct"] == 0.25
    assert cfg["max_notional_pct"] == 1.0
    assert cfg["paper_only"] is True
    assert cfg["live_trade_execution"] is False
    assert cfg["production_readiness_credit"] is False


def test_stress_sizing_supports_fractional_high_priced_ticker(monkeypatch):
    monkeypatch.setenv("APOLLO_SIM_STRESS_MODE", "1")

    sizing = stress_mode.stress_position_sizing(entry_price=452.75, stop=399.51, portfolio_size=100)

    assert sizing is not None
    assert sizing["risk_pct"] == 0.25
    assert 0 < sizing["fractional_shares"] < 1
    assert sizing["planned_notional"] > 0
    assert sizing["max_loss_dollars"] <= 25.01
    assert sizing["live_trade_execution"] is False


def test_tier_hold_blocks_production_but_not_stress_sandbox(monkeypatch):
    monkeypatch.setenv("APOLLO_SIM_STRESS_MODE", "1")
    monkeypatch.setenv("APOLLO_SIM_MAIN_LANE", "conservative")
    account = {"cash_balance": 100.0, "open_positions": []}
    study = {
        "run_id": "unit_stress",
        "proposals": [
            {
                "ticker": "TSM",
                "setup_type": "trend_continuation",
                "score": 88,
                "risk_reward_estimate": 1.1,
                "entry_zone": "452.00-454.00",
                "stop_zone": "399.00",
                "target_zone": "524.00",
                "status": "new",
            }
        ],
    }

    production = swing_simulation._build_next_cycle_plan(
        account,
        study,
        risk_policy={
            "risk_tier": "tier_0_hold",
            "risk_tier_label": "hold",
            "allocation_caps": {"deploy_cap_pct": 0.0, "max_position_pct": 0.0},
        },
    )
    stress = swing_simulation._build_stress_sandbox_plan(account, study)

    assert production["status"] == "risk_hold"
    assert production["planned_investment"] == 0.0
    assert stress["status"] == "stress_sandbox_ready"
    assert stress["planned_investment"] > 0
    assert stress["production_readiness_credit"] is False


def test_trade_cycle_marks_stress_candidate_without_standard_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_SIM_STRESS_MODE", "1")
    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    monkeypatch.setattr(
        trade_cycle,
        "_liquidity_ok",
        lambda ticker: {"ticker": ticker, "avg_volume_30d": 1_000_000, "min_required": 500_000, "ok": True},
    )
    monkeypatch.setattr(
        trade_cycle,
        "_entry_valid",
        lambda ticker, low, high: {"ok": False, "reason": "production_price_gate_blocked"},
    )
    proposal = {
        "ticker": "TSM",
        "entry_zone": "452.00-454.00",
        "stop_zone": "399.00",
        "target_zone": "524.00",
        "setup_type": "trend_continuation",
        "score": 88,
        "news_blended_score": 88,
        "risk_reward_estimate": 1.1,
        "direction_bias": "long_bias",
        "price_action_confirmation": {"ok": False},
        "volume_confirmation": {"ok": False},
    }

    result = trade_cycle._stage_risk_checks(
        tmp_path,
        {"news_evidence": "none", "news_by_ticker": {"TSM": {"headline_count": 0}}, "blended_proposals": [proposal]},
        {"market_regime": {"label": "risk_on"}, "proposals": [proposal]},
    )

    assert result["ok"] is False
    assert result["stress_probe_ok"] is True
    assert result["top_stress_probe_candidate"]["ticker"] == "TSM"
    assert result["top_stress_probe_candidate"]["stress_position_sizing"]["fractional_shares"] > 0
    assert result["top_stress_probe_candidate"]["live_trade_execution"] is False


def test_simulation_execution_forces_stress_to_paper_fractional(monkeypatch, tmp_path):
    execution_root = tmp_path / "execution"
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_ROOT", execution_root)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_PLANS", execution_root / "plans")
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_QUEUE", execution_root / "plan_queue.json")
    monkeypatch.setenv("APOLLO_SIM_STRESS_MODE", "1")
    monkeypatch.setenv("APOLLO_PAPER_PORTFOLIO_SIZE", "100")
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "alpaca")

    result = simulation_execution.create_simulation_execution_plan(
        {
            "simulation": {
                "sim_id": "stress_exec",
                "ticker": "TSM",
                "entry_zone": "452.00-454.00",
                "stop_zone": "399.00",
                "target_zone": "524.00",
                "status": "ready",
                "setup_type": "trend_continuation",
                "simulation_stress_mode": True,
            }
        }
    )

    assert result["ok"] is True
    plan = result["execution_plan"]
    assert plan["risk"]["simulation_stress_mode"] is True
    assert 0 < plan["risk"]["proposed_qty"] < 1
    order_plan = simulation_execution._build_broker_order_plan(
        execution_id=plan["execution_id"],
        ticker=plan["ticker"],
        execution=plan["execution"],
        risk=plan["risk"],
        provider="alpaca",
        simulation=plan["simulation"],
    )
    assert order_plan["provider"] == "paper_sim"
    assert 0 < order_plan["orders"][0]["qty"] < 1
