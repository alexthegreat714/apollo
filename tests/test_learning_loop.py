from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from Apollo import learning_loop, market_study, risk_scaling, swing_data, swing_study


def _write_study(root: Path, run_id: str, proposals: list[dict]) -> None:
    out_dir = root / "2026-06-10" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "swing_study.json").write_text(
        json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "created_at": "2026-06-10T12:00:00Z",
                "market_regime": {"label": "risk_on"},
                "proposals": proposals,
            }
        ),
        encoding="utf-8",
    )


def _proposal(ticker: str, setup_type: str, score: float, target_hit: bool = True) -> dict:
    candles = []
    close = 100.0
    start = datetime(2026, 6, 11, tzinfo=timezone.utc)
    for idx in range(25):
        close += 1.0 if target_hit else -1.0
        candles.append(
            {
                "date": (start + timedelta(days=idx)).date().isoformat(),
                "open": close,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 1000000,
            }
        )
    return {
        "ticker": ticker,
        "setup_type": setup_type,
        "score": score,
        "status": "new",
        "direction_bias": "long_bias",
        "entry_zone": "100.00-101.00",
        "stop_zone": "95.00",
        "target_zone": "105.00" if target_hit else "115.00",
        "market_regime": {"label": "risk_on"},
        "swing_snapshot": {"chart": {"candles": candles}},
        "lifecycle": {"first_seen": "2026-06-10T00:00:00Z"},
    }


def test_learning_loop_recomputes_historical_proposals_and_closed_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(swing_study, "RUNS_ROOT", tmp_path / "swing_study")
    monkeypatch.setattr(swing_study, "OUTCOMES_PATH", tmp_path / "swing_outcomes.json")
    monkeypatch.setattr(learning_loop, "LEARNING_ROOT", tmp_path / "learning")
    monkeypatch.setattr(learning_loop, "LATEST_LEARNING_PATH", tmp_path / "learning" / "latest_learning_state.json")
    _write_study(swing_study.RUNS_ROOT, "run_a", [_proposal("NVDA", "base_breakout", 86, target_hit=True)])
    account = {
        "closed_positions": [
            {
                "ticker": "AMD",
                "setup_type": "pullback_to_trend",
                "risk_tier": "standard",
                "score": 82,
                "realized_pnl_pct": -2.5,
                "opened_at": "2026-06-12T00:00:00Z",
                "closed_at": "2026-06-14T00:00:00Z",
                "exit_reason": "stop_hit",
            }
        ]
    }

    state = learning_loop.build_learning_state(account=account)

    assert state["ok"] is True
    assert state["proposal_count"] == 1
    assert state["closed_trade_count"] == 1
    assert state["groups"]["ticker:nvda"]["expectancy_pct"] > 0
    assert state["groups"]["ticker:amd"]["expectancy_pct"] < 0
    assert learning_loop.LATEST_LEARNING_PATH.exists()


def test_learned_adjustment_boosts_and_penalizes_from_learning_state(tmp_path, monkeypatch):
    monkeypatch.setattr(learning_loop, "LATEST_LEARNING_PATH", tmp_path / "latest_learning_state.json")
    state = {
        "run_id": "learning_unit",
        "groups": {
            "setup_type:base_breakout": {
                "sample_count": 5,
                "expectancy_pct": 2.0,
                "confidence_weight": 1.0,
                "recommended_score_adjustment": 2.7,
            },
            "ticker:amd": {
                "sample_count": 5,
                "expectancy_pct": -2.0,
                "confidence_weight": 1.0,
                "recommended_score_adjustment": -2.7,
            },
        },
    }
    learning_loop.LATEST_LEARNING_PATH.write_text(json.dumps(state), encoding="utf-8")

    boosted = learning_loop.learned_adjustment_for_proposal({"ticker": "NVDA", "setup_type": "base_breakout", "score": 80})
    penalized = learning_loop.learned_adjustment_for_proposal({"ticker": "AMD", "setup_type": "base_breakout", "score": 80})

    assert boosted["adjustment"] > 0
    assert penalized["adjustment"] < boosted["adjustment"]


def test_risk_tier_demotes_for_negative_learning_but_truth_gate_still_holds():
    account = {
        "max_drawdown_pct": 1.0,
        "closed_positions": [
            {"ticker": "AMD", "realized_pnl_pct": 1.0},
            {"ticker": "AMD", "realized_pnl_pct": 1.0},
            {"ticker": "AMD", "realized_pnl_pct": 1.0},
        ],
        "performance_stats": {"expectancy_pct": 1.0},
    }
    study = {"proposals": [{"ticker": "AMD", "setup_type": "base_breakout", "score": 84}]}
    learning_state = {
        "run_id": "learning_unit",
        "groups": {
            "ticker:amd": {"sample_count": 5, "expectancy_pct": -1.5, "confidence_weight": 1.0},
            "setup_type:base_breakout": {"sample_count": 5, "expectancy_pct": -1.0, "confidence_weight": 1.0},
        },
    }

    demoted = risk_scaling.evaluate_risk_tier(
        account=account,
        latest_study=study,
        next_cycle_plan={"allocations": [{"ticker": "AMD"}]},
        learning_state=learning_state,
    )
    held = risk_scaling.evaluate_risk_tier(
        account=account,
        latest_study=study,
        next_cycle_plan={"allocations": [{"ticker": "NVDA"}]},
        learning_state=learning_state,
    )

    assert demoted["risk_tier"] == "tier_0_hold" or "ticker:amd_negative_expectancy" in demoted["rationale"]
    assert held["risk_tier"] == "tier_0_hold"


def test_swing_study_surfaces_learned_adjustment(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_study, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(swing_study, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(learning_loop, "LATEST_LEARNING_PATH", tmp_path / "latest_learning_state.json")
    monkeypatch.setattr(swing_study, "classify_setup", lambda snapshot: {"setup_type": "base_breakout", "risk_notes": []})
    learning_loop.LATEST_LEARNING_PATH.write_text(
        json.dumps(
            {
                "run_id": "learning_unit",
                "groups": {
                    "setup_type:base_breakout": {
                        "sample_count": 5,
                        "expectancy_pct": 2.0,
                        "confidence_weight": 1.0,
                        "recommended_score_adjustment": 2.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    candles = []
    for idx in range(260):
        price = 100 + idx
        candles.append({"date": f"2025-01-{(idx % 28) + 1:02d}", "open": price, "high": price + 2, "low": price - 1, "close": price, "volume": 1000000 + idx})
    snapshot = swing_data.build_swing_snapshot_from_candles("NVDA", candles)
    snapshot["close"] = snapshot["high20"]
    snapshot["rsi14"] = 63
    snapshot["atr14"] = 3.0
    snapshot["volume_trend"]["ratio"] = 1.8
    snapshot["market_regime"] = {"label": "risk_on", "score": 90, "risk_notes": []}
    monkeypatch.setattr(
        swing_data,
        "swing_snapshot",
        lambda payload: {"ok": True, "snapshots": [snapshot], "market_regime": snapshot["market_regime"], "source_health": {}},
    )
    monkeypatch.setattr(
        market_study,
        "build_market_study",
        lambda payload: {"ok": True, "blocked_session": False, "source_health": {}, "proposals": [{"ticker": "NVDA", "catalyst_summary": ["NVDA raises guidance"], "source_links": ["https://news/a"]}]},
    )

    study = swing_study.build_swing_study({"ticker": "NVDA", "write_artifacts": False, "state_path": str(tmp_path / "state.json")})

    assert study["best_proposal"]["learned_adjustment"]["adjustment"] > 0
    assert study["best_proposal"]["learned_score_after"] >= study["best_proposal"]["learned_score_before"]
