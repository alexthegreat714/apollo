from __future__ import annotations

from Apollo import risk_scaling


def _study(ticker: str = "NVDA"):
    return {
        "run_id": "swing_unit",
        "best_proposal": {"ticker": ticker, "score": 82},
        "proposals": [
            {
                "ticker": ticker,
                "setup_type": "pullback_to_trend",
                "status": "new",
                "score": 82,
                "risk_reward_estimate": 1.8,
                "entry_zone": "100-102",
                "stop_zone": "94",
                "target_zone": "116",
                "provider_health": {"ok": True, "stale_days": 0},
            }
        ],
    }


def _account(closed_count: int = 0, *, warning: bool = False, max_drawdown: float = 0.5):
    closed = []
    for idx in range(closed_count):
        closed.append(
            {
                "ticker": "NVDA",
                "opened_at": f"2026-05-{idx + 1:02d}T00:00:00Z",
                "closed_at": f"2026-05-{idx + 2:02d}T00:00:00Z",
                "exit_reason": "target_hit",
                "fill_price": 100.0,
                "exit_price": 110.0,
                "stop": 94.0,
                "realized_pnl_pct": 10.0,
                "realized_pnl": 1.0,
            }
        )
    account = {
        "cash_balance": 100.0,
        "market_value": 110.0,
        "max_drawdown_pct": max_drawdown,
        "performance_stats": {"expectancy_pct": 2.0},
        "closed_positions": closed,
        "simulation_warnings": [],
    }
    if warning:
        account["simulation_warnings"] = [
            {
                "ticker": "NVDA",
                "warning_class": "mislabeled_exit_reason",
                "reason": "unit warning",
            }
        ]
    return account


def _plan(ticker: str = "NVDA"):
    return {
        "status": "queued_for_next_cycle",
        "allocations": [{"ticker": ticker, "amount": 40.0}],
    }


def _clean_external():
    return {
        "nightly_truth": {"quality_gate_pass": True},
        "homework_trade": {"missing_today": False},
        "latest_gate": {
            "decision": "clear_for_paper_sim",
            "candidate": {"ticker": "NVDA"},
            "live_guard": {"live_trade_execution": False},
        },
    }


def test_math_warning_forces_hold():
    ext = _clean_external()
    result = risk_scaling.evaluate_risk_tier(
        account=_account(10, warning=True),
        latest_study=_study(),
        next_cycle_plan=_plan(),
        enforce_external_gates=True,
        **ext,
    )

    assert result["risk_tier"] == "tier_0_hold"
    assert result["allocation_caps"]["deploy_cap_pct"] == 0.0
    assert "simulation_math_clean" in result["rationale"]


def test_clean_five_decision_window_reaches_measured():
    ext = _clean_external()
    result = risk_scaling.evaluate_risk_tier(
        account=_account(5),
        latest_study=_study(),
        next_cycle_plan=_plan(),
        enforce_external_gates=True,
        **ext,
    )

    assert result["risk_tier"] == "tier_2_measured"
    assert result["allocation_caps"] == {"deploy_cap_pct": 80.0, "max_position_pct": 45.0}


def test_clean_ten_decision_window_reaches_confident():
    ext = _clean_external()
    result = risk_scaling.evaluate_risk_tier(
        account=_account(10, max_drawdown=2.5),
        latest_study=_study(),
        next_cycle_plan=_plan(),
        enforce_external_gates=True,
        **ext,
    )

    assert result["risk_tier"] == "tier_3_confident"
    assert result["allocation_caps"] == {"deploy_cap_pct": 85.0, "max_position_pct": 50.0}


def test_plan_truth_mismatch_forces_hold():
    ext = _clean_external()
    result = risk_scaling.evaluate_risk_tier(
        account=_account(10),
        latest_study=_study("ANET"),
        next_cycle_plan=_plan("MRVL"),
        enforce_external_gates=True,
        **ext,
    )

    assert result["risk_tier"] == "tier_0_hold"
    assert result["active_paper_intent"]["mismatch"] is True
    assert "plan_truth_consistent" in result["rationale"]


def test_blocked_gate_demotes_one_tier():
    ext = _clean_external()
    ext["latest_gate"] = {
        "decision": "blocked_by_news",
        "candidate": {"ticker": "NVDA"},
        "live_guard": {"live_trade_execution": False},
    }
    result = risk_scaling.evaluate_risk_tier(
        account=_account(10, max_drawdown=2.5),
        latest_study=_study(),
        next_cycle_plan=_plan(),
        enforce_external_gates=True,
        **ext,
    )

    assert result["risk_tier"] == "tier_2_measured"
    assert "latest_gate_blocked_by_news" in result["rationale"]
