"""
backtest.py - Apollo Walk-Forward Backtester

Downloads 24+ months of daily candles and rolls through month-end snapshots,
generating trade signals from historical indicator state and simulating forward
outcomes. Produces win rate, average-R, expectancy, and equity curve per ticker.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_APOLLO_ROOT = Path(__file__).resolve().parent
BACKTEST_ROOT = _APOLLO_ROOT / "logs" / "backtest"

_NO_TRADE_SETUPS = {"no_trade", "extended_no_chase", "broken_trend"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        if v == v and v not in (float("inf"), float("-inf")):
            return v
    except Exception:
        pass
    return default


def _fetch_candles(ticker: str, months: int = 24) -> List[Dict[str, Any]]:
    """Download daily OHLCV going back `months` months plus a 60-day buffer."""
    try:
        import yfinance as yf
        days = months * 31 + 60
        start = (date.today() - timedelta(days=days)).isoformat()
        end = (date.today() + timedelta(days=1)).isoformat()
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        rows: List[Dict[str, Any]] = []
        for ts, row in hist.iterrows():
            rows.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return rows
    except Exception:
        return []


def _compute_technical_state(candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Compute all indicators needed by classify_setup() from a candle slice."""
    from Apollo.swing_data import _sma, _ema, _rsi, _atr, _volume_trend

    if len(candles) < 22:
        return None

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    close = closes[-1]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    ema8 = _ema(closes, 8)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(candles, 14)
    high20 = max(highs[-20:]) if len(highs) >= 20 else None
    low20 = min(lows[-20:]) if len(lows) >= 20 else None
    vol_trend = _volume_trend(volumes)
    atr_pct = round(atr14 / close * 100.0, 3) if atr14 and close else None

    if not sma20 or not sma50 or not ema8 or not ema21 or not high20 or not low20:
        return None

    return {
        "ok": True,
        "close": close,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "ema8": ema8,
        "ema21": ema21,
        "rsi14": rsi14,
        "high20": high20,
        "low20": low20,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "volume_trend": vol_trend,
        "chart": {"candles": candles},
    }


def _parse_zone(zone_str: str) -> Tuple[float, float]:
    import re
    s = str(zone_str or "").strip().replace("$", "")
    parts = re.split(r"\s*[-–]\s*", s)
    try:
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
        v = float(parts[0])
        return v * 0.995, v * 1.005
    except (ValueError, IndexError):
        return 0.0, 0.0


def _parse_price(price_str: str) -> float:
    import re
    s = str(price_str or "").strip().replace("$", "")
    parts = re.split(r"\s*[-–]\s*", s)
    try:
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _simulate_forward(
    candles: List[Dict[str, Any]],
    entry_low: float,
    entry_high: float,
    stop: float,
    target: float,
    horizon: int = 90,
) -> Dict[str, Any]:
    """Simulate a trade against the next `horizon` candles."""
    relevant = candles[:horizon]
    if not relevant or entry_low <= 0 or entry_high <= 0 or stop <= 0 or target <= 0:
        return {"status": "insufficient_data", "pnl_pct": None}

    in_trade = False
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    status = "open"

    for candle in relevant:
        if not in_trade:
            candle_open = candle.get("open") or candle["high"]
            if float(candle_open) > entry_high * 1.002:
                continue
            if candle["low"] <= entry_high and candle["high"] >= entry_low:
                fill_base = entry_low + (entry_high - entry_low) * 0.3
                entry_price = round(min(fill_base * 1.001, entry_high), 4)
                in_trade = True

        if in_trade:
            if candle["low"] <= stop:
                exit_price = stop
                status = "stopped_out"
                break
            if candle["high"] >= target:
                exit_price = target
                status = "hit_target"
                break

    if not in_trade:
        return {"status": "never_entered", "pnl_pct": None}

    pnl_pct = None
    if entry_price and exit_price:
        pnl_pct = round((exit_price - entry_price) / entry_price * 100.0, 3)
    elif entry_price and status == "open" and relevant:
        last_close = relevant[-1]["close"]
        pnl_pct = round((last_close - entry_price) / entry_price * 100.0, 3)

    return {
        "status": status,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": pnl_pct,
    }


def _month_end_indices(candles: List[Dict[str, Any]], min_lookback: int = 60) -> List[int]:
    """Return candle indices that are the last trading day of each calendar month."""
    indices: List[int] = []
    for i in range(min_lookback, len(candles) - 1):
        this_month = candles[i]["date"][:7]
        next_month = candles[i + 1]["date"][:7]
        if this_month != next_month:
            indices.append(i)
    return indices


def run_walk_forward(ticker: str, months: int = 24, forward_days: int = 90) -> Dict[str, Any]:
    """
    Roll through the last `months` months of history for `ticker`.
    At each month-end: classify the setup, simulate forward `forward_days` candles.
    Returns per-period results and aggregate stats.
    """
    from Apollo.swing_study import classify_setup, _zones

    candles = _fetch_candles(ticker, months=months)
    if len(candles) < 60:
        return {"ok": False, "ticker": ticker, "error": "insufficient_history", "periods": [], "stats": {}}

    month_ends = _month_end_indices(candles, min_lookback=50)
    periods: List[Dict[str, Any]] = []

    for idx in month_ends:
        lookback_slice = candles[:idx + 1]
        state = _compute_technical_state(lookback_slice)
        if state is None:
            continue

        classification = classify_setup(state)
        setup_type = classification.get("setup_type", "no_trade")

        if setup_type in _NO_TRADE_SETUPS:
            periods.append({
                "as_of_date": candles[idx]["date"],
                "setup_type": setup_type,
                "action": "no_trade",
                "pnl_pct": None,
                "status": None,
            })
            continue

        zones = _zones(state, setup_type)
        entry_low, entry_high = _parse_zone(zones.get("entry_zone", ""))
        stop = _parse_price(zones.get("stop_zone", ""))
        target = _parse_price(zones.get("target_zone", ""))
        rr = zones.get("risk_reward_estimate")

        if rr is None or rr < 1.0:
            periods.append({
                "as_of_date": candles[idx]["date"],
                "setup_type": setup_type,
                "action": "skip_low_rr",
                "risk_reward": rr,
                "pnl_pct": None,
                "status": None,
            })
            continue

        forward_candles = candles[idx + 1:]
        outcome = _simulate_forward(forward_candles, entry_low, entry_high, stop, target, horizon=forward_days)

        periods.append({
            "as_of_date": candles[idx]["date"],
            "setup_type": setup_type,
            "action": "traded",
            "risk_reward": rr,
            "entry_zone": zones.get("entry_zone"),
            "stop_zone": zones.get("stop_zone"),
            "target_zone": zones.get("target_zone"),
            **outcome,
        })

    stats = _aggregate_stats(periods)
    result = {
        "ok": True,
        "ticker": ticker,
        "months": months,
        "forward_days": forward_days,
        "run_at": _utc_now(),
        "total_periods": len(month_ends),
        "periods": periods,
        "stats": stats,
    }
    return result


def _aggregate_stats(periods: List[Dict[str, Any]]) -> Dict[str, Any]:
    traded = [p for p in periods if p.get("action") == "traded" and p.get("pnl_pct") is not None]
    if not traded:
        return {"total_signals": 0, "traded": 0, "win_rate_pct": None, "avg_win_pct": None,
                "avg_loss_pct": None, "expectancy_pct": None, "profit_factor": None}

    wins = [p["pnl_pct"] for p in traded if p["pnl_pct"] > 0]
    losses = [p["pnl_pct"] for p in traded if p["pnl_pct"] <= 0]
    n = len(traded)
    win_rate = round(len(wins) / n * 100.0, 1)
    avg_win = round(sum(wins) / len(wins), 3) if wins else None
    avg_loss = round(sum(losses) / len(losses), 3) if losses else None
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
    wr = len(wins) / n
    lr = len(losses) / n
    expectancy = round(wr * (avg_win or 0.0) + lr * (avg_loss or 0.0), 3)

    setup_counts: Dict[str, int] = {}
    for p in traded:
        k = p.get("setup_type", "unknown")
        setup_counts[k] = setup_counts.get(k, 0) + 1

    return {
        "total_periods": len(periods),
        "total_signals": len([p for p in periods if p.get("action") == "traded"]),
        "traded": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy,
        "profit_factor": profit_factor,
        "setup_type_counts": setup_counts,
    }


def run_portfolio_backtest(
    tickers: Optional[List[str]] = None,
    months: int = 24,
    forward_days: int = 90,
) -> Dict[str, Any]:
    """
    Run walk-forward backtest across a list of tickers.
    Aggregates per-ticker stats and computes portfolio-level metrics.
    Saves results to logs/backtest/latest.json.
    """
    if tickers is None:
        from Apollo.config import get_watchlist
        tickers = get_watchlist()

    results: Dict[str, Any] = {}
    all_traded: List[Dict[str, Any]] = []

    for ticker in tickers:
        result = run_walk_forward(ticker, months=months, forward_days=forward_days)
        results[ticker] = result
        if result.get("ok"):
            for p in result.get("periods", []):
                if p.get("action") == "traded" and p.get("pnl_pct") is not None:
                    all_traded.append({**p, "ticker": ticker})

    portfolio_stats = _aggregate_stats(all_traded)

    payload = {
        "ok": True,
        "run_at": _utc_now(),
        "months": months,
        "forward_days": forward_days,
        "tickers": tickers,
        "portfolio_stats": portfolio_stats,
        "by_ticker": {t: r.get("stats", {}) for t, r in results.items() if r.get("ok")},
    }

    BACKTEST_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = BACKTEST_ROOT / "latest.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = BACKTEST_ROOT / f"backtest_{ts}.json"
    archive_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return payload


def load_latest_backtest() -> Optional[Dict[str, Any]]:
    path = BACKTEST_ROOT / "latest.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


__all__ = [
    "run_walk_forward",
    "run_portfolio_backtest",
    "load_latest_backtest",
]
