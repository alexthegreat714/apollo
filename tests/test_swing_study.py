from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from Apollo import market_study, swing_data, swing_study


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _disable_heavy_corpus_paths(monkeypatch):
    monkeypatch.setattr(swing_study, "_rag_context_for_ticker", lambda ticker, setup_type="": [])
    monkeypatch.setattr(swing_study, "_load_earnings_dates_from_corpus", lambda tickers: {})
    monkeypatch.setattr(swing_study, "_load_fundamentals_from_corpus", lambda tickers: {})


def _candles(count=260, start=100.0, step=0.35, volume=1_000_000):
    rows = []
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = start
    for idx in range(count):
        price += step
        rows.append(
            {
                "date": (base + timedelta(days=idx)).date().isoformat(),
                "open": price - 0.4,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": volume + (idx * 1000),
            }
        )
    return rows


def test_swing_snapshot_indicators_with_fixed_candles():
    row = swing_data.build_swing_snapshot_from_candles("NVDA", _candles(), spy_candles=_candles(step=0.1), qqq_candles=_candles(step=0.12))
    assert row["ok"] is True
    assert row["sma20"] is not None
    assert row["ema8"] is not None
    assert row["rsi14"] is not None
    assert row["atr14"] is not None
    assert row["relative_strength"]["SPY"]["relative_20d"] is not None


def test_swing_snapshot_rejects_insufficient_candles():
    row = swing_data.build_swing_snapshot_from_candles("AMD", _candles(count=10))
    assert row["ok"] is False
    assert row["error"] == "insufficient_candles"


def test_provider_comparison_uses_last_good_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_data, "CANDLE_CACHE_PATH", tmp_path / "candle_cache.json")
    monkeypatch.setattr(swing_data, "PROVIDER_HEALTH_PATH", tmp_path / "provider_health.json")
    monkeypatch.setattr(swing_data, "fetch_yahoo_daily_candles", lambda ticker: {"ok": True, "ticker": ticker, "candles": _candles(), "source": "yahoo_chart_daily"})
    monkeypatch.setattr(swing_data, "fetch_stooq_daily_candles", lambda ticker: {"ok": True, "ticker": ticker, "candles": _candles(start=101.0), "source": "stooq_daily"})
    first = swing_data.fetch_provider_candles("NVDA")
    assert first["ok"] is True
    assert first["provider_health"]["confidence"] <= 100

    monkeypatch.setattr(swing_data, "fetch_yahoo_daily_candles", lambda ticker: {"ok": False, "ticker": ticker, "candles": [], "source": "yahoo_chart_daily", "error": "down"})
    monkeypatch.setattr(swing_data, "fetch_stooq_daily_candles", lambda ticker: {"ok": False, "ticker": ticker, "candles": [], "source": "stooq_daily", "error": "down"})
    cached = swing_data.fetch_provider_candles("NVDA")
    assert cached["ok"] is True
    assert str(cached["source"]).startswith("cache:")
    assert "using last-good candle cache" in cached["provider_health"]["disagreements"]


def test_classify_pullback_and_broken_trend():
    pullback = swing_data.build_swing_snapshot_from_candles("NVDA", _candles(step=0.18))
    pullback["close"] = pullback["sma20"]
    pullback["rsi14"] = 58
    result = swing_study.classify_setup(pullback)
    assert result["setup_type"] in {"pullback_to_trend", "trend_continuation"}

    broken = dict(pullback)
    broken["close"] = float(broken["sma50"]) * 0.9
    result = swing_study.classify_setup(broken)
    assert result["setup_type"] == "broken_trend"


def test_strong_swing_study_yields_long_bias(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_study, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(swing_study, "STATE_PATH", tmp_path / "state.json")
    snapshot = swing_data.build_swing_snapshot_from_candles("NVDA", _candles(step=0.5), spy_candles=_candles(step=0.1), qqq_candles=_candles(step=0.1))
    snapshot["close"] = snapshot["high20"]
    snapshot["rsi14"] = 63
    snapshot["atr14"] = 3.0
    snapshot["volume_trend"]["ratio"] = 1.8
    snapshot["market_regime"] = {"label": "risk_on", "score": 90, "risk_notes": []}
    monkeypatch.setattr(
        swing_data,
        "swing_snapshot",
        lambda payload: {"ok": True, "snapshots": [snapshot], "market_regime": snapshot["market_regime"], "source_health": {"daily_candles": {"ok": True}}},
    )
    monkeypatch.setattr(
        market_study,
        "build_market_study",
        lambda payload: {
            "ok": True,
            "blocked_session": False,
            "source_health": {},
            "proposals": [
                {
                    "ticker": "NVDA",
                    "catalyst_summary": ["NVDA raises guidance", "NVDA demand strong"],
                    "source_links": ["https://news/a", "https://news/b"],
                }
            ],
        },
    )
    study = swing_study.build_swing_study({"ticker": "NVDA", "write_artifacts": True, "state_path": str(tmp_path / "state.json")})
    assert study["ok"] is True
    assert study["best_proposal"]["direction_bias"] == "long_bias"
    assert study["best_proposal"]["human_approval_required"] is True
    assert study["best_proposal"]["live_trade_execution"] is False
    assert study["best_proposal"]["catalyst_quality_score"] > 0
    assert "outcome_status" in study["best_proposal"]


def test_broken_trend_does_not_pass_even_with_catalyst(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_study, "RUNS_ROOT", tmp_path)
    snapshot = swing_data.build_swing_snapshot_from_candles("AMD", _candles(step=0.2))
    snapshot["close"] = float(snapshot["sma50"]) * 0.85
    snapshot["market_regime"] = {"label": "risk_on", "score": 90, "risk_notes": []}
    monkeypatch.setattr(swing_data, "swing_snapshot", lambda payload: {"ok": True, "snapshots": [snapshot], "market_regime": snapshot["market_regime"], "source_health": {}})
    monkeypatch.setattr(
        market_study,
        "build_market_study",
        lambda payload: {"ok": True, "blocked_session": False, "source_health": {}, "proposals": [{"ticker": "AMD", "catalyst_summary": ["AMD beats"], "source_links": ["x"]}]},
    )
    study = swing_study.build_swing_study({"ticker": "AMD", "write_artifacts": False, "state_path": str(tmp_path / "state.json")})
    assert study["best_proposal"]["direction_bias"] == "no_trade"
    assert study["best_proposal"]["setup_type"] == "broken_trend"


def test_catalyst_normalization_dedupes_and_scores():
    rows = [
        {"title": "NVDA raises guidance", "url": "https://news/a", "kind": "news"},
        {"title": "NVDA raises guidance duplicate", "url": "https://news/a", "kind": "news"},
        {"title": "NVDA 8-K filed", "url": "https://www.sec.gov/x", "kind": "sec_filing"},
    ]
    records = swing_study.normalize_catalysts("NVDA", rows, "base_breakout")
    assert len(records) == 2
    assert records[0]["quality_score"] >= records[-1]["quality_score"]
    assert swing_study._catalyst_quality(records) > 0


def test_catalyst_normalization_rejects_false_cat_matches():
    rows = [
        {"title": "Red Cat Holdings Q1 misses forecasts", "url": "https://news/rcat", "kind": "news"},
        {"title": "Red Cat Stock: Big Quarter Ahead (NASDAQ:RCAT)", "url": "https://news/red-cat-stock-rcat", "kind": "news"},
        {"title": "Caterpillar CAT shares rise after equipment demand improves", "url": "https://news/cat", "kind": "news"},
    ]
    records = swing_study.normalize_catalysts("CAT", rows, "pullback_to_trend")
    assert len(records) == 1
    assert records[0]["title"].startswith("Caterpillar")
    assert records[0]["ticker_relevance"] == "company_alias"


def test_kg_ticker_match_uses_boundaries_not_substrings():
    assert swing_study._kg_endpoint_mentions_ticker("CAT", "Caterpillar related_to industrial equipment")
    assert swing_study._kg_endpoint_mentions_ticker("CAT", "CAT related_to NYSE")
    assert not swing_study._kg_endpoint_mentions_ticker("CAT", "ai applications span clinical decision support")


def test_graph_asymmetry_extends_aggressive_paper_target():
    snapshot = {
        "close": 100.0,
        "atr14": 4.0,
        "chart": {"candles": _candles(count=60, start=80.0, step=0.3)},
    }
    zones = {"entry_zone": "99.00-101.00", "stop_zone": "94.00", "target_zone": "108.00", "risk_reward_estimate": 1.33}
    profile = swing_study._graph_asymmetric_profile(
        kg_triples=[
            {"head": "DDOG", "relation": "reported", "tail": "record growth and demand acceleration"},
            {"head": "DDOG", "relation": "benefits_from", "tail": "sector rotation and upgrade momentum"},
        ],
        rag_chunks=[{"pack": "earnings", "text": "guidance raise with margin expansion and strong contract demand"}],
        catalysts=[{"title": "DDOG beats and raises guidance", "quality_score": 80, "url": "https://news/a"}],
        event_summary={"impact_score": 76},
    )

    updated, extension = swing_study._apply_aggressive_reward_extension(zones, snapshot, "long_bias", profile)

    assert profile["use_for_aggressive_reward"] is True
    assert extension["applied"] is True
    assert updated["target_zone"] == "112.00"
    assert updated["risk_reward_estimate"] > zones["risk_reward_estimate"]
    assert extension["reason"] == "graph_backed_asymmetric_target_extension"


def test_outcome_tracking_marks_target_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(swing_study, "OUTCOMES_PATH", tmp_path / "outcomes.json")
    proposal = {
        "ticker": "NVDA",
        "setup_type": "base_breakout",
        "score": 85,
        "direction_bias": "long_bias",
        "entry_zone": "100.00-101.00",
        "stop_zone": "95.00",
        "target_zone": "105.00",
        "swing_snapshot": {"chart": {"candles": _candles(count=30, start=100.0, step=1.0)}},
        "lifecycle": {"first_seen": "2025-01-01T00:00:00Z"},
    }
    result = swing_study.recompute_outcomes({"latest": {"proposals": [proposal], "created_at": "2025-01-01T00:00:00Z"}})
    assert result["ok"] is True
    assert result["outcomes"]["entries"][0]["outcome"]["status"] == "target_hit"


def test_proposal_decision_mutates_only_local_state(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("APOLLO_SWING_STATE_PATH", str(state_path))
    result = swing_study.proposal_decision({"ticker": "NVDA", "action": "watching"})
    assert result["ok"] is True
    assert result["record"]["status"] == "watching"
    result = swing_study.proposal_decision({"ticker": "NVDA", "action": "reject"})
    assert result["record"]["status"] == "rejected"
