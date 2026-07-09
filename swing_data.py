from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus
import csv

from Apollo import market_data


_APOLLO_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "swing_study"
CANDLE_CACHE_PATH = RUNS_ROOT / "candle_cache.json"
PROVIDER_HEALTH_PATH = RUNS_ROOT / "provider_health.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return None
        num = float(value)
        if not math.isfinite(num):
            return None
        return num
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return None
        return int(float(value))
    except Exception:
        return None


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _pct_change(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or start <= 0:
        return None
    return ((end - start) / start) * 100.0


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / float(period)


def _ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / float(period)
    for value in values[period:]:
        ema = (value * k) + (ema * (1.0 - k))
    return ema


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(0.0, delta))
        losses.append(max(0.0, -delta))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(candles) <= period:
        return None
    trs: List[float] = []
    previous_close: Optional[float] = None
    for candle in candles:
        high = _safe_float(candle.get("high"))
        low = _safe_float(candle.get("low"))
        close = _safe_float(candle.get("close"))
        if high is None or low is None or close is None:
            continue
        if previous_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        trs.append(max(0.0, tr))
        previous_close = close
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / float(period)


def _volume_trend(volumes: List[int]) -> Dict[str, Any]:
    if not volumes:
        return {"latest": None, "avg20": None, "ratio": None, "label": "unknown"}
    latest = volumes[-1]
    avg20 = sum(volumes[-20:]) / float(min(20, len(volumes)))
    ratio = latest / avg20 if avg20 > 0 else None
    label = "normal"
    if ratio is None:
        label = "unknown"
    elif ratio >= 1.5:
        label = "expanding"
    elif ratio <= 0.65:
        label = "drying_up"
    return {"latest": latest, "avg20": int(avg20), "ratio": _round(ratio), "label": label}


def _parse_day(value: Any) -> Optional[datetime]:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _last_close(candles: List[Dict[str, Any]]) -> Optional[float]:
    if not candles:
        return None
    return _safe_float(candles[-1].get("close"))


def _latest_day(candles: List[Dict[str, Any]]) -> str:
    if not candles:
        return ""
    return str(candles[-1].get("date") or "")


def _trim_candles(candles: List[Dict[str, Any]], limit: int = 320) -> List[Dict[str, Any]]:
    clean = [dict(row) for row in candles if _safe_float((row or {}).get("close")) is not None]
    return clean[-limit:]


def _sane_candles(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    missing_volume = sum(1 for row in candles[-30:] if not (_safe_int(row.get("volume")) or 0))
    split_like = False
    closes = _close_values(candles)
    for prev, cur in zip(closes[-90:-1], closes[-89:]):
        if prev > 0 and (cur / prev >= 1.8 or cur / prev <= 0.55):
            split_like = True
            break
    return {"missing_volume_days_30": missing_volume, "split_like_outlier": split_like}


def _provider_health(
    ticker: str,
    yahoo: Dict[str, Any],
    stooq: Dict[str, Any],
    chosen_source: str,
) -> Dict[str, Any]:
    providers = {"yahoo_chart_daily": yahoo, "stooq_daily": stooq}
    details: Dict[str, Any] = {}
    confidence = 100.0
    disagreements: List[str] = []
    now_day = datetime.now(timezone.utc).date()
    for name, payload in providers.items():
        candles = list(payload.get("candles") or [])
        latest = _latest_day(candles)
        latest_dt = _parse_day(latest)
        stale_days = (now_day - latest_dt.date()).days if latest_dt else None
        sanity = _sane_candles(candles)
        ok = bool(payload.get("ok") and candles)
        detail = {
            "ok": ok,
            "latest_date": latest,
            "candle_count": len(candles),
            "stale_days": stale_days,
            "error": str(payload.get("error") or ""),
            **sanity,
        }
        if stale_days is not None and stale_days > 5:
            confidence -= 12.0
            disagreements.append(f"{name} stale by {stale_days} days")
        if sanity.get("missing_volume_days_30", 0) >= 10:
            confidence -= 5.0
            disagreements.append(f"{name} missing recent volume")
        if sanity.get("split_like_outlier"):
            confidence -= 15.0
            disagreements.append(f"{name} split-like price jump")
        details[name] = detail
    y_close = _last_close(list(yahoo.get("candles") or []))
    s_close = _last_close(list(stooq.get("candles") or []))
    close_disagreement_pct = None
    if y_close and s_close:
        close_disagreement_pct = abs(y_close - s_close) / max(y_close, s_close) * 100.0
        if close_disagreement_pct > 1.0:
            confidence -= min(30.0, close_disagreement_pct * 5.0)
            disagreements.append(f"provider close disagreement {close_disagreement_pct:.2f}%")
    if not any((payload.get("ok") and payload.get("candles")) for payload in providers.values()):
        confidence = 0.0
        disagreements.append("no live provider returned candles")
    return {
        "ticker": ticker,
        "ok": confidence >= 45.0,
        "chosen_source": chosen_source,
        "providers": details,
        "close_disagreement_pct": _round(close_disagreement_pct),
        "disagreements": disagreements,
        "confidence": round(max(0.0, min(100.0, confidence)), 2),
        "updated_at": _utc_iso(),
    }


def _load_candle_cache() -> Dict[str, Any]:
    return _read_json(Path(os.getenv("APOLLO_SWING_CANDLE_CACHE", str(CANDLE_CACHE_PATH))), {"tickers": {}})


def _save_candle_cache(cache: Dict[str, Any]) -> None:
    _write_json(Path(os.getenv("APOLLO_SWING_CANDLE_CACHE", str(CANDLE_CACHE_PATH))), cache)


def _write_provider_health(payload: Dict[str, Any]) -> None:
    _write_json(Path(os.getenv("APOLLO_SWING_PROVIDER_HEALTH", str(PROVIDER_HEALTH_PATH))), payload)


def fetch_yahoo_daily_candles(ticker: str, *, range_: str = "1y", interval: str = "1d") -> Dict[str, Any]:
    ticker_norm = market_data.normalize_ticker(ticker)
    if not ticker_norm:
        return {"ok": False, "ticker": "", "candles": [], "error": "ticker_required"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(ticker_norm)}?range={quote_plus(range_)}&interval={quote_plus(interval)}"
    try:
        resp = market_data._http_get(url, timeout=10)
    except Exception as exc:
        return {"ok": False, "ticker": ticker_norm, "candles": [], "error": f"{type(exc).__name__}:{exc}"}
    if not resp.ok:
        return {"ok": False, "ticker": ticker_norm, "candles": [], "error": f"http_{resp.status_code}"}
    payload = resp.json() if resp.content else {}
    result = (((payload.get("chart") or {}).get("result") or []) or [{}])[0]
    timestamps = list(result.get("timestamp") or [])
    quote = (((result.get("indicators") or {}).get("quote") or []) or [{}])[0]
    opens = list((quote or {}).get("open") or [])
    highs = list((quote or {}).get("high") or [])
    lows = list((quote or {}).get("low") or [])
    closes = list((quote or {}).get("close") or [])
    volumes = list((quote or {}).get("volume") or [])
    candles: List[Dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        close = _safe_float(closes[idx] if idx < len(closes) else None)
        high = _safe_float(highs[idx] if idx < len(highs) else None)
        low = _safe_float(lows[idx] if idx < len(lows) else None)
        if close is None or high is None or low is None:
            continue
        candles.append(
            {
                "date": datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d"),
                "open": _safe_float(opens[idx] if idx < len(opens) else close),
                "high": high,
                "low": low,
                "close": close,
                "volume": _safe_int(volumes[idx] if idx < len(volumes) else None) or 0,
            }
        )
    return {"ok": bool(candles), "ticker": ticker_norm, "candles": candles, "source": "yahoo_chart_daily", "error": "" if candles else "no_candles"}


def fetch_stooq_daily_candles(ticker: str) -> Dict[str, Any]:
    ticker_norm = market_data.normalize_ticker(ticker)
    if not ticker_norm:
        return {"ok": False, "ticker": "", "candles": [], "error": "ticker_required", "source": "stooq_daily"}
    api_key = str(os.getenv("APOLLO_STOOQ_APIKEY") or "").strip()
    if not api_key:
        return {"ok": False, "ticker": ticker_norm, "candles": [], "error": "api_key_required", "source": "stooq_daily"}
    symbol = ticker_norm.lower()
    if "." not in symbol:
        symbol = f"{symbol}.us"
    url = f"https://stooq.com/q/d/l/?s={quote_plus(symbol)}&i=d&apikey={quote_plus(api_key)}"
    try:
        resp = market_data._http_get(url, timeout=10)
    except Exception as exc:
        return {"ok": False, "ticker": ticker_norm, "candles": [], "error": f"{type(exc).__name__}:{exc}", "source": "stooq_daily"}
    if not resp.ok:
        return {"ok": False, "ticker": ticker_norm, "candles": [], "error": f"http_{resp.status_code}", "source": "stooq_daily"}
    candles: List[Dict[str, Any]] = []
    if "get your apikey" in resp.text.lower() or "captcha" in resp.text.lower():
        return {"ok": False, "ticker": ticker_norm, "candles": [], "error": "api_key_required_or_invalid", "source": "stooq_daily"}
    for row in csv.DictReader(resp.text.splitlines()):
        close = _safe_float(row.get("Close"))
        high = _safe_float(row.get("High"))
        low = _safe_float(row.get("Low"))
        if close is None or high is None or low is None:
            continue
        candles.append(
            {
                "date": str(row.get("Date") or "").strip(),
                "open": _safe_float(row.get("Open")) or close,
                "high": high,
                "low": low,
                "close": close,
                "volume": _safe_int(row.get("Volume")) or 0,
            }
        )
    candles.sort(key=lambda row: str(row.get("date") or ""))
    return {"ok": bool(candles), "ticker": ticker_norm, "candles": candles, "source": "stooq_daily", "error": "" if candles else "no_candles"}


def fetch_daily_candles(ticker: str, *, range_: str = "1y", interval: str = "1d") -> Dict[str, Any]:
    return fetch_yahoo_daily_candles(ticker, range_=range_, interval=interval)


def fetch_provider_candles(ticker: str) -> Dict[str, Any]:
    ticker_norm = market_data.normalize_ticker(ticker)
    cache = _load_candle_cache()
    ticker_cache = dict((cache.get("tickers") or {}).get(ticker_norm) or {})
    yahoo = fetch_yahoo_daily_candles(ticker_norm)
    stooq = fetch_stooq_daily_candles(ticker_norm)
    candidates = [yahoo, stooq]
    chosen = next((row for row in candidates if row.get("ok") and row.get("candles")), {})
    fallback_used = False
    if not chosen and ticker_cache.get("candles"):
        chosen = {
            "ok": True,
            "ticker": ticker_norm,
            "candles": list(ticker_cache.get("candles") or []),
            "source": f"cache:{ticker_cache.get('source') or 'unknown'}",
            "error": "live_sources_failed_using_last_good",
        }
        fallback_used = True
    source = str(chosen.get("source") or "unavailable")
    health = _provider_health(ticker_norm, yahoo, stooq, source)
    if fallback_used:
        health["confidence"] = min(float(health.get("confidence") or 0.0), 55.0)
        health["disagreements"] = [*list(health.get("disagreements") or []), "using last-good candle cache"]
    if chosen.get("ok") and chosen.get("candles") and not source.startswith("cache:"):
        cache.setdefault("tickers", {})[ticker_norm] = {
            "source": source,
            "updated_at": _utc_iso(),
            "candles": _trim_candles(list(chosen.get("candles") or [])),
        }
        cache["updated_at"] = _utc_iso()
        _save_candle_cache(cache)
    return {
        "ok": bool(chosen.get("ok") and chosen.get("candles")),
        "ticker": ticker_norm,
        "candles": _trim_candles(list(chosen.get("candles") or [])),
        "source": source,
        "provider_health": health,
        "providers": {"yahoo_chart_daily": yahoo, "stooq_daily": stooq},
    }


def _close_values(candles: List[Dict[str, Any]]) -> List[float]:
    return [float(candle.get("close")) for candle in candles if _safe_float(candle.get("close")) is not None]


def _relative_strength(candles: List[Dict[str, Any]], benchmark_candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    closes = _close_values(candles)
    bench = _close_values(benchmark_candles)
    out: Dict[str, Any] = {}
    for period in (20, 50):
        own = _pct_change(closes[-period], closes[-1]) if len(closes) >= period else None
        ref = _pct_change(bench[-period], bench[-1]) if len(bench) >= period else None
        out[f"{period}d_pct"] = _round(own)
        out[f"benchmark_{period}d_pct"] = _round(ref)
        out[f"relative_{period}d"] = _round((own - ref) if own is not None and ref is not None else None)
    return out


def _chart_payload(candles: List[Dict[str, Any]], limit: int = 120) -> Dict[str, Any]:
    clean = _trim_candles(candles, limit=max(220, limit))
    rows: List[Dict[str, Any]] = []
    closes: List[float] = []
    for row in clean:
        close = _safe_float(row.get("close"))
        if close is None:
            continue
        closes.append(close)
        rows.append(
            {
                "date": str(row.get("date") or ""),
                "open": _round(_safe_float(row.get("open"))),
                "high": _round(_safe_float(row.get("high"))),
                "low": _round(_safe_float(row.get("low"))),
                "close": _round(close),
                "volume": _safe_int(row.get("volume")) or 0,
                "sma20": _round(_sma(closes, 20)),
                "sma50": _round(_sma(closes, 50)),
                "sma200": _round(_sma(closes, 200)),
                "ema8": _round(_ema(closes, 8)),
                "ema21": _round(_ema(closes, 21)),
            }
        )
    return {"candles": rows[-limit:]}


def build_swing_snapshot_from_candles(
    ticker: str,
    candles: List[Dict[str, Any]],
    *,
    spy_candles: Optional[List[Dict[str, Any]]] = None,
    qqq_candles: Optional[List[Dict[str, Any]]] = None,
    source: str = "unit",
) -> Dict[str, Any]:
    ticker_norm = market_data.normalize_ticker(ticker)
    clean = [dict(row) for row in candles if _safe_float((row or {}).get("close")) is not None]
    closes = _close_values(clean)
    volumes = [_safe_int(row.get("volume")) or 0 for row in clean]
    if not ticker_norm or len(clean) < 21:
        return {"ok": False, "ticker": ticker_norm, "error": "insufficient_candles", "candle_count": len(clean), "source": source}
    latest = clean[-1]
    close = float(latest.get("close"))
    high20 = max(float(row.get("high")) for row in clean[-20:] if _safe_float(row.get("high")) is not None)
    low20 = min(float(row.get("low")) for row in clean[-20:] if _safe_float(row.get("low")) is not None)
    high52 = max(float(row.get("high")) for row in clean[-252:] if _safe_float(row.get("high")) is not None)
    low52 = min(float(row.get("low")) for row in clean[-252:] if _safe_float(row.get("low")) is not None)
    atr14 = _atr(clean, 14)
    atr_pct = (atr14 / close * 100.0) if atr14 is not None and close > 0 else None
    range_pos = ((close - low52) / (high52 - low52) * 100.0) if high52 > low52 else None
    spy_rs = _relative_strength(clean, spy_candles or []) if spy_candles else {}
    qqq_rs = _relative_strength(clean, qqq_candles or []) if qqq_candles else {}
    snapshot = {
        "ok": True,
        "ticker": ticker_norm,
        "source": source,
        "as_of": str(latest.get("date") or ""),
        "updated_at": _utc_iso(),
        "candle_count": len(clean),
        "close": _round(close),
        "sma20": _round(_sma(closes, 20)),
        "sma50": _round(_sma(closes, 50)),
        "sma200": _round(_sma(closes, 200)),
        "ema8": _round(_ema(closes, 8)),
        "ema21": _round(_ema(closes, 21)),
        "rsi14": _round(_rsi(closes, 14)),
        "atr14": _round(atr14),
        "atr_pct": _round(atr_pct),
        "high20": _round(high20),
        "low20": _round(low20),
        "high52": _round(high52),
        "low52": _round(low52),
        "range52_position": _round(range_pos),
        "volume_trend": _volume_trend(volumes),
        "pct_change_5d": _round(_pct_change(closes[-5], closes[-1]) if len(closes) >= 5 else None),
        "pct_change_20d": _round(_pct_change(closes[-20], closes[-1]) if len(closes) >= 20 else None),
        "pct_change_50d": _round(_pct_change(closes[-50], closes[-1]) if len(closes) >= 50 else None),
        "relative_strength": {"SPY": spy_rs, "QQQ": qqq_rs},
        "chart": _chart_payload(clean),
    }
    return snapshot


def classify_market_regime(spy_snapshot: Dict[str, Any], qqq_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    rows = [row for row in (spy_snapshot, qqq_snapshot) if row.get("ok")]
    if not rows:
        return {"label": "unknown", "score": 45.0, "risk_notes": ["Regime source unavailable."], "sma200_gate": False}
    positive = 0
    notes: List[str] = []
    sma200_breach = False
    for row in rows:
        close = _safe_float(row.get("close"))
        sma50 = _safe_float(row.get("sma50"))
        sma200 = _safe_float(row.get("sma200"))
        ema8 = _safe_float(row.get("ema8"))
        ema21 = _safe_float(row.get("ema21"))
        ticker = row.get("ticker")
        if close is not None and sma50 is not None and close > sma50:
            positive += 1
        else:
            notes.append(f"{ticker} below SMA50")
        if ema8 is not None and ema21 is not None and ema8 > ema21:
            positive += 1
        else:
            notes.append(f"{ticker} short trend not confirmed")
        if close is not None and sma200 is not None and close < sma200:
            sma200_breach = True
            notes.append(f"{ticker} below SMA200 — hard risk_off trigger")
    max_score = max(1, len(rows) * 2)
    score = round(positive / max_score * 100.0, 2)
    if sma200_breach:
        label = "risk_off"
        score = min(score, 25.0)
    elif score >= 75:
        label = "risk_on"
    elif score >= 45:
        label = "mixed"
    else:
        label = "risk_off"
    return {"label": label, "score": score, "risk_notes": notes[:4], "sma200_gate": sma200_breach}


def swing_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    tickers = market_data.normalize_tickers(payload)
    if not tickers:
        return {"ok": False, "error": "ticker_required", "snapshots": []}
    spy_payload = fetch_provider_candles("SPY")
    qqq_payload = fetch_provider_candles("QQQ")
    spy_candles = list(spy_payload.get("candles") or [])
    qqq_candles = list(qqq_payload.get("candles") or [])
    spy_snapshot = build_swing_snapshot_from_candles("SPY", spy_candles, source="yahoo_chart_daily") if spy_candles else {"ok": False, "ticker": "SPY"}
    qqq_snapshot = build_swing_snapshot_from_candles("QQQ", qqq_candles, source="yahoo_chart_daily") if qqq_candles else {"ok": False, "ticker": "QQQ"}
    regime = classify_market_regime(spy_snapshot, qqq_snapshot)
    snapshots: List[Dict[str, Any]] = []
    provider_rows: Dict[str, Any] = {"SPY": spy_payload.get("provider_health") or {}, "QQQ": qqq_payload.get("provider_health") or {}}
    for ticker in tickers:
        data = fetch_provider_candles(ticker)
        candles = list(data.get("candles") or [])
        if candles:
            row = build_swing_snapshot_from_candles(ticker, candles, spy_candles=spy_candles, qqq_candles=qqq_candles, source=str(data.get("source") or "yahoo_chart_daily"))
        else:
            row = {"ok": False, "ticker": ticker, "error": str(data.get("error") or "no_candles"), "source": str(data.get("source") or "yahoo_chart_daily")}
        row["market_regime"] = regime
        row["provider_confidence"] = (data.get("provider_health") or {}).get("confidence")
        row["data_disagreements"] = list((data.get("provider_health") or {}).get("disagreements") or [])
        row["provider_health"] = data.get("provider_health") or {}
        provider_rows[ticker] = data.get("provider_health") or {}
        snapshots.append(row)
    provider_health = {"updated_at": _utc_iso(), "tickers": provider_rows}
    _write_provider_health(provider_health)
    return {
        "ok": any(row.get("ok") for row in snapshots),
        "snapshots": snapshots,
        "market_regime": regime,
        "benchmarks": {"SPY": spy_snapshot, "QQQ": qqq_snapshot},
        "source_health": {
            "daily_candles": {"ok": any(row.get("ok") for row in snapshots), "source": "yahoo_chart_daily"},
            "regime": {"ok": bool(spy_snapshot.get("ok") or qqq_snapshot.get("ok"))},
            "providers": provider_health,
        },
    }


__all__ = [
    "build_swing_snapshot_from_candles",
    "classify_market_regime",
    "fetch_daily_candles",
    "fetch_provider_candles",
    "fetch_stooq_daily_candles",
    "fetch_yahoo_daily_candles",
    "swing_snapshot",
]
