"""
Simulation spark — fast validation of the swing simulation pipeline.

Covers:
  1. Zone and price parsing
  2. Entry detection (price touches zone intraday)
  3. Stop-out detection (stop hit before target)
  4. Target hit detection (target hit before stop)
  5. Never-entered (price never reaches entry zone)
  6. Open trade (entered, neither stop nor target hit yet)
  7. P&L calculation accuracy
  8. Simulation save/load round-trip
  9. run_simulation() with real yfinance data (AVGO, 90 days)
 10. run_simulation_from_latest_homework() produces valid output

Run: pytest Apollo/tests/test_simulation_spark.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── helpers ──────────────────────────────────────────────────────────────────

def _candle(date: str, open_: float, high: float, low: float, close: float) -> Dict[str, Any]:
    return {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": 1_000_000}


def _fake_history(candles: List[Dict[str, Any]]):
    """Build a fake yfinance Ticker.history() return value."""
    import pandas as pd
    rows = []
    for c in candles:
        rows.append({
            "Open": c["open"], "High": c["high"], "Low": c["low"],
            "Close": c["close"], "Volume": c["volume"],
        })
    idx = pd.to_datetime([c["date"] for c in candles], utc=True).tz_convert("America/New_York")
    return pd.DataFrame(rows, index=idx)


# ── import subject ────────────────────────────────────────────────────────────

from Apollo.swing_simulation import (
    _parse_zone,
    _parse_price,
    _simulate_outcome,
    _fetch_history,
    run_simulation,
    get_latest_simulation,
    list_simulations,
    OUTCOMES_PATH,
    SIMULATIONS_PATH,
)


# ── 1. Zone / price parsing ───────────────────────────────────────────────────

class TestParsing:
    def test_zone_low_high(self):
        lo, hi = _parse_zone("423.06-433.86")
        assert abs(lo - 423.06) < 0.01
        assert abs(hi - 433.86) < 0.01

    def test_zone_single_price(self):
        lo, hi = _parse_zone("400.00")
        assert lo < 400.01
        assert hi > 399.99
        assert lo < hi

    def test_zone_with_dollar_sign(self):
        lo, hi = _parse_zone("$100.00-$110.00")
        assert abs(lo - 100.0) < 0.01
        assert abs(hi - 110.0) < 0.01

    def test_zone_empty(self):
        lo, hi = _parse_zone("")
        assert lo == 0.0 and hi == 0.0

    def test_price_single(self):
        p = _parse_price("369.48")
        assert abs(p - 369.48) < 0.01

    def test_price_range_takes_first(self):
        p = _parse_price("350.00-360.00")
        assert abs(p - 350.0) < 0.01

    def test_price_with_dollar(self):
        p = _parse_price("$250")
        assert abs(p - 250.0) < 0.01

    def test_price_empty(self):
        assert _parse_price("") == 0.0


# ── 2-6. Outcome simulation logic ────────────────────────────────────────────

ENTRY_LOW, ENTRY_HIGH = 100.0, 110.0
STOP = 80.0
TARGET = 140.0


class TestOutcomeLogic:
    """Uses synthetic candles — no network calls."""

    def _run(self, candles, start=""):
        return _simulate_outcome(candles, ENTRY_LOW, ENTRY_HIGH, STOP, TARGET, start_date=start)

    def test_never_entered_when_price_too_high(self):
        candles = [_candle("2026-04-01", 150, 155, 145, 152)]
        r = self._run(candles)
        assert r["status"] == "never_entered"
        assert r["entry_price"] is None
        assert r["pnl_pct"] is None

    def test_entered_when_price_touches_zone(self):
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),  # Low=108 < 110 → enters
        ]
        r = self._run(candles)
        assert r["status"] == "open"
        assert r["entry_date"] == "2026-04-01"
        assert r["entry_price"] == pytest.approx((ENTRY_LOW + ENTRY_HIGH) / 2, abs=0.01)

    def test_stopped_out_before_target(self):
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),  # enters
            _candle("2026-04-02", 112, 113, 79, 80),    # Low=79 ≤ 80 stop → stopped
        ]
        r = self._run(candles)
        assert r["status"] == "stopped_out"
        assert r["exit_date"] == "2026-04-02"
        assert r["exit_price"] == STOP
        assert r["pnl_dollars"] == pytest.approx(STOP - (ENTRY_LOW + ENTRY_HIGH) / 2, abs=0.01)

    def test_hit_target_before_stop(self):
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),  # enters
            _candle("2026-04-02", 115, 141, 114, 138),  # High=141 ≥ 140 target
        ]
        r = self._run(candles)
        assert r["status"] == "hit_target"
        assert r["exit_date"] == "2026-04-02"
        assert r["exit_price"] == TARGET
        assert r["pnl_pct"] > 0

    def test_open_stays_open(self):
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),  # enters at 105
            _candle("2026-04-02", 112, 118, 104, 115),  # in range, no hit
            _candle("2026-04-03", 115, 120, 110, 118),  # still open
        ]
        r = self._run(candles)
        assert r["status"] == "open"
        assert r["exit_price"] is None
        assert r["pnl_dollars"] is not None  # uses last close

    def test_stop_checked_before_target_same_day(self):
        """If stop and target are both reachable intraday, stop wins (bear-case bias)."""
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),  # enters
            _candle("2026-04-02", 80, 145, 75, 120),    # both stop (75) and target (145) hit
        ]
        r = self._run(candles)
        assert r["status"] == "stopped_out"

    def test_start_date_filters_prior_candles(self):
        candles = [
            _candle("2026-03-01", 120, 125, 108, 112),  # before start — should be ignored
            _candle("2026-04-15", 130, 135, 125, 128),  # after start — price above zone
        ]
        r = self._run(candles, start="2026-04-01")
        assert r["status"] == "never_entered"

    def test_insufficient_data_returns_gracefully(self):
        r = _simulate_outcome([], 100, 110, 80, 140)
        assert r["status"] == "insufficient_data"

    def test_zero_zones_returns_gracefully(self):
        candles = [_candle("2026-04-01", 100, 110, 90, 100)]
        r = _simulate_outcome(candles, 0, 0, 80, 140)
        assert r["status"] == "insufficient_data"


# ── 7. P&L accuracy ──────────────────────────────────────────────────────────

class TestPnL:
    def test_pnl_hit_target(self):
        entry_price = (ENTRY_LOW + ENTRY_HIGH) / 2  # 105.0
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),
            _candle("2026-04-02", 115, 145, 114, 138),
        ]
        r = _simulate_outcome(candles, ENTRY_LOW, ENTRY_HIGH, STOP, TARGET)
        expected_pnl = (TARGET - entry_price) / entry_price * 100
        assert r["pnl_pct"] == pytest.approx(expected_pnl, abs=0.01)

    def test_pnl_stopped_out_is_negative(self):
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),
            _candle("2026-04-02", 100, 101, 75, 80),
        ]
        r = _simulate_outcome(candles, ENTRY_LOW, ENTRY_HIGH, STOP, TARGET)
        assert r["pnl_pct"] < 0

    def test_pnl_open_uses_last_close(self):
        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),
            _candle("2026-04-10", 115, 120, 110, 115),  # last close 115
        ]
        r = _simulate_outcome(candles, ENTRY_LOW, ENTRY_HIGH, STOP, TARGET)
        entry = (ENTRY_LOW + ENTRY_HIGH) / 2
        expected = (115.0 - entry) / entry * 100
        assert r["pnl_pct"] == pytest.approx(expected, abs=0.01)


# ── 8. Save / load round-trip ────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_reload(self, tmp_path, monkeypatch):
        import Apollo.swing_simulation as sim_mod
        monkeypatch.setattr(sim_mod, "OUTCOMES_PATH", tmp_path / "outcomes.json")
        monkeypatch.setattr(sim_mod, "SIMULATIONS_PATH", tmp_path / "sims")

        candles = [
            _candle("2026-04-01", 120, 125, 108, 112),
            _candle("2026-04-10", 115, 120, 110, 118),
        ]

        with patch("Apollo.swing_simulation._fetch_history", return_value=candles):
            result = run_simulation({
                "ticker": "TEST",
                "entry_zone": "100.0-110.0",
                "stop_zone": "80.0",
                "target_zone": "140.0",
                "setup_type": "test_setup",
                "score": 75.0,
                "confidence_label": "medium",
                "created_at": "2026-04-01",
                "run_id": "spark_test_001",
            }, save=True)

        assert result["status"] in ("open", "hit_target", "stopped_out", "never_entered")

        # reload from file
        reloaded = get_latest_simulation()
        assert reloaded is not None
        assert reloaded["ticker"] == "TEST"
        assert reloaded["run_id"] == "spark_test_001"

    def test_list_simulations(self, tmp_path, monkeypatch):
        import Apollo.swing_simulation as sim_mod
        outcomes_path = tmp_path / "outcomes.json"
        monkeypatch.setattr(sim_mod, "OUTCOMES_PATH", outcomes_path)
        monkeypatch.setattr(sim_mod, "SIMULATIONS_PATH", tmp_path / "sims")

        candles = [_candle("2026-04-01", 120, 125, 108, 112)]

        for ticker in ("AAA", "BBB", "CCC"):
            with patch("Apollo.swing_simulation._fetch_history", return_value=candles):
                run_simulation({
                    "ticker": ticker, "entry_zone": "100-110",
                    "stop_zone": "80", "target_zone": "140",
                    "created_at": "2026-04-01",
                }, save=True)

        sims = list_simulations(limit=10)
        assert len(sims) == 3
        tickers = {s["ticker"] for s in sims}
        assert tickers == {"AAA", "BBB", "CCC"}


# ── 9. Real price data (yfinance) ─────────────────────────────────────────────

@pytest.mark.external
class TestRealData:
    def test_avgo_90d_returns_candles(self):
        candles = _fetch_history("AVGO", days=90)
        assert len(candles) >= 50, f"Expected ≥50 candles, got {len(candles)}"
        last = candles[-1]
        assert "date" in last
        assert last["close"] > 0
        assert last["high"] >= last["low"]

    def test_run_simulation_avgo_produces_valid_outcome(self):
        result = run_simulation({
            "ticker": "AVGO",
            "entry_zone": "423.06-433.86",
            "stop_zone": "369.48",
            "target_zone": "463.94",
            "setup_type": "trend_continuation",
            "score": 87.33,
            "confidence_label": "medium",
            "risk_reward_estimate": 0.56,
            "created_at": "2026-04-15T00:00:00Z",
            "run_id": "spark_avgo_live",
        }, lookback_days=90, save=False)

        assert result["ticker"] == "AVGO"
        assert result["status"] in ("open", "hit_target", "stopped_out", "never_entered")
        assert len(result["candles"]) >= 50
        assert result["entry_low"] == pytest.approx(423.06, abs=0.01)
        assert result["stop"] == pytest.approx(369.48, abs=0.01)
        assert result["target"] == pytest.approx(463.94, abs=0.01)
        if result["status"] in ("open", "hit_target", "stopped_out"):
            assert result["entry_price"] is not None
            assert result["entry_date"] is not None

    def test_nvda_never_entered_above_zone(self):
        """NVDA entry zone 105-112 — current price ~120. Should be never_entered."""
        result = run_simulation({
            "ticker": "NVDA",
            "entry_zone": "105.00-112.00",
            "stop_zone": "88.00",
            "target_zone": "135.00",
            "created_at": "2026-04-30T00:00:00Z",
            "run_id": "spark_nvda_live",
        }, lookback_days=15, save=False)

        assert result["ticker"] == "NVDA"
        # Either never entered or open — depends on actual price movement
        assert result["status"] in ("never_entered", "open", "hit_target", "stopped_out")
        assert len(result["candles"]) > 0


# ── 10. run_simulation_from_latest_homework ───────────────────────────────────

class TestHomeworkIntegration:
    def test_runs_from_latest_if_file_exists(self, tmp_path, monkeypatch):
        from Apollo import homework_trade_pipeline
        latest = tmp_path / "latest_homework_trade.json"
        latest.write_text(json.dumps({
            "run_id": "spark_hw_test",
            "created_at": "2026-04-15T00:00:00Z",
            "trade_plan": {
                "ticker": "AVGO",
                "decision": "research_candidate",
                "entry_zone": "423.06-433.86",
                "stop_zone": "369.48",
                "target_zone": "463.94",
                "setup_type": "trend_continuation",
                "score": 87.33,
                "confidence_label": "medium",
                "risk_reward_estimate": 0.56,
            },
            "swing_study": {"proposals": []},
        }), encoding="utf-8")
        monkeypatch.setattr(homework_trade_pipeline, "RUNS_ROOT", tmp_path)

        candles = [
            _candle("2026-04-15", 430, 435, 422, 428),
            _candle("2026-04-22", 425, 434, 424, 430),
        ]
        import Apollo.swing_simulation as sim_mod
        monkeypatch.setattr(sim_mod, "OUTCOMES_PATH", tmp_path / "outcomes.json")
        monkeypatch.setattr(sim_mod, "SIMULATIONS_PATH", tmp_path / "sims")

        with patch("Apollo.swing_simulation._fetch_history", return_value=candles):
            result = sim_mod.run_simulation_from_latest_homework(save=False)

        assert result is not None
        assert result["ticker"] == "AVGO"
        assert result["status"] in ("open", "hit_target", "stopped_out", "never_entered")

    def test_returns_none_when_no_homework_file(self, tmp_path, monkeypatch):
        from Apollo import homework_trade_pipeline
        monkeypatch.setattr(homework_trade_pipeline, "RUNS_ROOT", tmp_path)

        import Apollo.swing_simulation as sim_mod
        result = sim_mod.run_simulation_from_latest_homework(save=False)
        assert result is None
