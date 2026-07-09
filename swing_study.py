from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

_THESIS_ENABLED = os.getenv("APOLLO_THESIS_LLM_ENABLED", "1") == "1"
_THESIS_MODEL = os.getenv("APOLLO_THESIS_MODEL", "").strip() or None
_THESIS_TIMEOUT = int(os.getenv("APOLLO_THESIS_TIMEOUT_S", "45"))

try:
    from .config import get_watchlist
except ImportError:
    from config import get_watchlist
from Apollo import event_impact, focus_trading, market_data, market_study, swing_data


_APOLLO_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "swing_study"
STATE_PATH = RUNS_ROOT / "swing_state.json"
OUTCOMES_PATH = RUNS_ROOT / "swing_outcomes.json"
CATALYST_RECORDS_PATH = RUNS_ROOT / "catalyst_records.json"

_CONFIDENCE_TIERS = ["low", "medium", "high"]


def _default_kg_path() -> Path:
    for env_var in ("APOLLO_NIGHTLY_HIPPORAG_RAG_DIR", "APOLLO_RAG_DIR"):
        raw = os.getenv(env_var, "").strip()
        if raw:
            candidate = (Path(raw) if Path(raw).is_absolute() else (_APOLLO_ROOT / raw)).resolve()
            if (candidate / "financial_kg.json").exists():
                return candidate / "financial_kg.json"
    # RAG_DIR may be set relative to Engineering root, not Apollo root
    raw_rag = os.getenv("RAG_DIR", "").strip()
    if raw_rag:
        for base_dir in (_APOLLO_ROOT, _APOLLO_ROOT.parent):
            candidate = (Path(raw_rag) if Path(raw_rag).is_absolute() else (base_dir / raw_rag)).resolve()
            if (candidate / "financial_kg.json").exists():
                return candidate / "financial_kg.json"
    return (_APOLLO_ROOT / "chroma_db" / "financial_kg.json").resolve()


def _kg_endpoint_mentions_ticker(ticker: str, text: str) -> bool:
    ticker_norm = market_data.normalize_ticker(ticker)
    hay = f" {text} ".lower()
    if not ticker_norm:
        return False
    for false_alias in _TICKER_FALSE_ALIASES.get(ticker_norm, ()):
        if false_alias in hay:
            return False
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(ticker_norm)}(?![A-Za-z0-9])", text, re.I):
        return True
    return any(alias in hay for alias in _TICKER_ALIASES.get(ticker_norm, ()))


def _kg_triples_for_ticker(ticker: str, kg_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = kg_path or _default_kg_path()
    if not path.exists():
        return []
    try:
        from Apollo.apollo_hipporag.graph_store import FinancialKG
        kg = FinancialKG(str(path))
        view = kg.subgraph_view(query=ticker.lower(), limit_nodes=30, hops=1)
        ticker_lower = ticker.lower()
        edges = []
        for edge in list(view.get("edges") or [])[:40]:
            head = str(edge.get("source") or edge.get("head") or "")
            rel = str(edge.get("relation") or edge.get("label") or "")
            tail = str(edge.get("target") or edge.get("tail") or "")
            if not head or not tail:
                continue
            # Drop edges where neither endpoint references the ticker by symbol boundary
            # or company alias. Substring matching made CAT match words like applications.
            if not _kg_endpoint_mentions_ticker(ticker_lower, f"{head} {tail}"):
                continue
            # Financial fact triples encode relation into tail as "fact_type:period"
            if rel == "has_fact" and ":" in tail:
                fact_rel, _, period = tail.partition(":")
                edges.append({"head": head, "relation": fact_rel, "tail": period, "raw_tail": tail})
            else:
                edges.append({"head": head, "relation": rel, "tail": tail})
            if len(edges) >= 20:
                break
        return edges
    except Exception:
        return []


def _apply_data_quality_override(
    proposals: List[Dict[str, Any]],
    confidence_band: str,
) -> List[Dict[str, Any]]:
    if confidence_band not in {"low", "medium"}:
        return proposals
    downgrade = confidence_band == "low"
    out = []
    for proposal in proposals:
        p = dict(proposal)
        label = str(p.get("confidence_label") or "low")
        tier = _CONFIDENCE_TIERS.index(label) if label in _CONFIDENCE_TIERS else 0
        if downgrade and tier > 0:
            new_label = _CONFIDENCE_TIERS[tier - 1]
            p["confidence_label"] = new_label
            p["data_quality_warning"] = f"pipeline_confidence_band:low:label_downgraded:{label}->{new_label}"
            if new_label != "high":
                p["paper_order_candidate"] = None
        elif confidence_band == "medium" and label == "high":
            p["data_quality_warning"] = "pipeline_confidence_band:medium→high_label_flagged"
        out.append(p)
    return out
PROVIDER_HEALTH_PATH = RUNS_ROOT / "provider_health.json"
VALID_STATUSES = {"new", "watching", "confirmed", "invalidated", "expired", "paper_open", "paper_closed", "rejected"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _safe_float_optional(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return None
        return float(value)
    except Exception:
        return None


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_list_artifact(path: Path, key: str) -> List[Dict[str, Any]]:
    payload = _read_json(path, {})
    return [dict(row) for row in list(payload.get(key) or []) if isinstance(row, dict)]


def _write_list_artifact(path: Path, key: str, rows: List[Dict[str, Any]]) -> None:
    _write_json(path, {"updated_at": _utc_iso(), key: rows})


def _tickers_from_payload(payload: Dict[str, Any]) -> List[str]:
    tickers = market_data.normalize_tickers(payload)
    if tickers:
        return tickers[:24]
    try:
        state = focus_trading.load_state()
        focus = dict(state.get("focus") or {})
        tickers = [market_data.normalize_ticker(str(item)) for item in list(focus.get("selected_tickers") or [])]
        tickers = [ticker for ticker in tickers if ticker]
        if tickers:
            return tickers[:24]
    except Exception:
        pass
    return get_watchlist()


def load_swing_state(path: Optional[str] = None) -> Dict[str, Any]:
    state_path = Path(path or os.getenv("APOLLO_SWING_STATE_PATH", str(STATE_PATH)))
    state = _read_json(state_path, {})
    state.setdefault("positions", {})
    state.setdefault("events", [])
    state.setdefault("last_run_id", "")
    state["state_path"] = str(state_path)
    return state


def save_swing_state(state: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    state_path = Path(path or os.getenv("APOLLO_SWING_STATE_PATH", str(STATE_PATH)))
    payload = dict(state or {})
    payload.setdefault("positions", {})
    payload.setdefault("events", [])
    payload["events"] = list(payload.get("events") or [])[-200:]
    payload["updated_at"] = _utc_iso()
    payload["state_path"] = str(state_path)
    _write_json(state_path, payload)
    return payload


def _rs_score(snapshot: Dict[str, Any], sector_rs_adj: float = 0.0) -> float:
    rs = dict(snapshot.get("relative_strength") or {})
    values: List[float] = []
    for bench in ("SPY", "QQQ"):
        data = dict(rs.get(bench) or {})
        for key in ("relative_20d", "relative_50d"):
            if data.get(key) is not None:
                values.append(_safe_float(data.get(key)))
    if not values:
        base = 45.0
    else:
        avg = sum(values) / len(values)
        base = max(0.0, min(100.0, 50.0 + (avg * 4.0)))
    # Sector ETF RS adjustment: cap at ±8 points
    sector_boost = max(-8.0, min(8.0, sector_rs_adj * 2.0))
    return max(0.0, min(100.0, base + sector_boost))


def classify_setup(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if not snapshot.get("ok"):
        return {"setup_type": "no_trade", "no_trade_reason": str(snapshot.get("error") or "missing swing snapshot"), "risk_notes": []}
    close = _safe_float(snapshot.get("close"))
    sma20 = _safe_float(snapshot.get("sma20"))
    sma50 = _safe_float(snapshot.get("sma50"))
    sma200 = _safe_float(snapshot.get("sma200"))
    ema8 = _safe_float(snapshot.get("ema8"))
    ema21 = _safe_float(snapshot.get("ema21"))
    rsi = _safe_float(snapshot.get("rsi14"))
    high20 = _safe_float(snapshot.get("high20"))
    low20 = _safe_float(snapshot.get("low20"))
    atr = _safe_float(snapshot.get("atr14"))
    atr_pct = _safe_float(snapshot.get("atr_pct"))
    volume_ratio = _safe_float((snapshot.get("volume_trend") or {}).get("ratio"))
    risk_notes: List[str] = []
    if not close or not sma20 or not sma50 or not ema8 or not ema21 or not high20 or not low20:
        return {"setup_type": "no_trade", "no_trade_reason": "insufficient indicators", "risk_notes": risk_notes}
    trend_up = close > sma50 and ema8 > ema21 and (not sma200 or close > sma200)
    near_20 = abs(close - sma20) / close <= 0.035 if close else False
    near_50 = abs(close - sma50) / close <= 0.045 if close else False
    breakout = close >= high20 * 0.995 and trend_up and volume_ratio >= 1.05
    extended = bool(atr and close > sma20 + (2.8 * atr)) or rsi >= 76
    broken = close < sma50 or ema8 < ema21
    if broken:
        setup = "broken_trend"
        reason = "trend support broken"
    elif extended:
        setup = "extended_no_chase"
        reason = "extended from trend support"
    elif breakout:
        setup = "base_breakout"
        reason = ""
    elif trend_up and (near_20 or near_50):
        setup = "pullback_to_trend"
        reason = ""
    elif trend_up:
        setup = "trend_continuation"
        reason = ""
    else:
        setup = "no_trade"
        reason = "long trend not confirmed"
    if atr_pct and atr_pct > 8:
        risk_notes.append("ATR is high for a clean swing entry.")
    if rsi >= 70:
        risk_notes.append("RSI is elevated; avoid chasing without a controlled entry.")
    return {"setup_type": setup, "no_trade_reason": reason, "risk_notes": risk_notes}


def _swing_pivots(candles: List[Dict[str, Any]], close: float, lookback: int = 40) -> Dict[str, Any]:
    """Find nearest structural support/resistance from pivot highs/lows in candle data."""
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    n = len(recent)
    swing_highs: List[float] = []
    swing_lows: List[float] = []
    for i in range(2, n - 2):
        h = _safe_float(recent[i].get("high"))
        lo = _safe_float(recent[i].get("low"))
        if h is None or lo is None:
            continue
        neighbor_h = [_safe_float(recent[j].get("high")) for j in (i - 2, i - 1, i + 1, i + 2)]
        neighbor_l = [_safe_float(recent[j].get("low")) for j in (i - 2, i - 1, i + 1, i + 2)]
        if all(nb is not None and h > nb for nb in neighbor_h):
            swing_highs.append(h)
        if all(nb is not None and lo < nb for nb in neighbor_l):
            swing_lows.append(lo)
    resistance = min((h for h in swing_highs if h > close * 1.001), default=None)
    support = max((lo for lo in swing_lows if lo < close * 0.999), default=None)
    return {
        "resistance": resistance,
        "support": support,
        "all_highs": sorted(swing_highs, reverse=True)[:5],
        "all_lows": sorted(swing_lows)[:5],
    }


def _zones(snapshot: Dict[str, Any], setup_type: str) -> Dict[str, Any]:
    close = _safe_float(snapshot.get("close"))
    sma20 = _safe_float(snapshot.get("sma20"))
    sma50 = _safe_float(snapshot.get("sma50"))
    high20 = _safe_float(snapshot.get("high20"))
    low20 = _safe_float(snapshot.get("low20"))
    atr = _safe_float(snapshot.get("atr14"))
    if not close or not atr:
        return {"entry_zone": "", "stop_zone": "", "target_zone": "", "risk_reward_estimate": None, "risk_distance_pct": None, "pivot_support": None, "pivot_resistance": None}

    chart_candles = list((snapshot.get("chart") or {}).get("candles") or [])
    pivots = _swing_pivots(chart_candles, close) if len(chart_candles) >= 10 else {}
    support = _safe_float(pivots.get("support"))
    resistance = _safe_float(pivots.get("resistance"))

    if setup_type == "base_breakout":
        if resistance:
            entry_low = resistance - 0.15 * atr
            entry_high = resistance + 0.20 * atr
        else:
            entry_low = max(close - 0.35 * atr, (high20 or close) - 0.25 * atr)
            entry_high = close + 0.25 * atr
        if support and (close - support) / close <= 0.07:
            stop = support - 0.10 * atr
        else:
            stop = max(0.01, min(low20 or (close - 2 * atr), close - 1.35 * atr))
        higher = [h for h in (pivots.get("all_highs") or []) if h > entry_high * 1.005]
        if higher:
            base_h = (resistance or close) - (support or (close - 2 * atr))
            target = max(min(higher), entry_high + max(base_h, atr))
        else:
            target = close + 2.2 * atr

    elif setup_type == "pullback_to_trend":
        anchor = sma20 if (sma20 and sma50 and abs(close - sma20) <= abs(close - sma50)) else (sma50 or close)
        entry_low = anchor - 0.35 * atr
        entry_high = anchor + 0.55 * atr
        if support and support > anchor - 2 * atr and (close - support) / close <= 0.06:
            stop = support - 0.10 * atr
        else:
            stop = max(0.01, close - 1.35 * atr)
        target = resistance if resistance and resistance > entry_high * 1.01 else close + 2.2 * atr

    else:
        entry_low = close - 0.45 * atr
        entry_high = close + 0.25 * atr
        stop = max(0.01, min(low20 or (close - 2 * atr), close - 1.35 * atr))
        target = resistance if resistance and resistance > close * 1.02 else close + 2.2 * atr

    stop = max(0.01, stop)
    risk = max(0.01, close - stop)
    reward = max(0.0, target - close)
    return {
        "entry_zone": f"{entry_low:.2f}-{entry_high:.2f}",
        "stop_zone": f"{stop:.2f}",
        "target_zone": f"{target:.2f}",
        "risk_reward_estimate": round(reward / risk, 2) if risk > 0 else None,
        "risk_distance_pct": round(risk / close * 100.0, 2),
        "pivot_support": round(support, 2) if support else None,
        "pivot_resistance": round(resistance, 2) if resistance else None,
    }


def _event_dislocation_zones(snapshot: Dict[str, Any], direction_bias: str) -> Dict[str, Any]:
    close = _safe_float(snapshot.get("close"))
    atr = _safe_float(snapshot.get("atr14"))
    if not close or not atr:
        return {"entry_zone": "", "stop_zone": "", "target_zone": "", "risk_reward_estimate": None, "risk_distance_pct": None, "pivot_support": None, "pivot_resistance": None}
    chart_candles = list((snapshot.get("chart") or {}).get("candles") or [])
    pivots = _swing_pivots(chart_candles, close) if len(chart_candles) >= 10 else {}
    support = _safe_float(pivots.get("support"))
    resistance = _safe_float(pivots.get("resistance"))
    entry_low = close - 0.30 * atr
    entry_high = close + 0.30 * atr
    if direction_bias == "short_bias":
        stop = resistance + 0.10 * atr if resistance and resistance < close + 2.5 * atr else close + 1.25 * atr
        target = support if support and support < close * 0.99 else close - 2.10 * atr
        risk = max(0.01, stop - close)
        reward = max(0.0, close - target)
    else:
        stop = support - 0.10 * atr if support and support > close - 2.5 * atr else max(0.01, close - 1.25 * atr)
        target = resistance if resistance and resistance > close * 1.01 else close + 2.10 * atr
        risk = max(0.01, close - stop)
        reward = max(0.0, target - close)
    stop = max(0.01, stop)
    target = max(0.01, target)
    return {
        "entry_zone": f"{entry_low:.2f}-{entry_high:.2f}",
        "stop_zone": f"{stop:.2f}",
        "target_zone": f"{target:.2f}",
        "risk_reward_estimate": round(reward / risk, 2) if risk > 0 else None,
        "risk_distance_pct": round(risk / close * 100.0, 2),
        "pivot_support": round(support, 2) if support else None,
        "pivot_resistance": round(resistance, 2) if resistance else None,
    }


_GRAPH_UPSIDE_TERMS = {
    "beat", "beats", "raise", "raises", "raised", "guidance", "upgrade", "upgraded",
    "accelerat", "growth", "demand", "margin", "expansion", "record", "surge",
    "breakout", "momentum", "rotation", "leader", "contract", "partnership",
    "approval", "launch", "pricing power", "short squeeze", "squeeze",
}
_GRAPH_DOWNSIDE_TERMS = {
    "miss", "misses", "cut", "cuts", "downgrade", "downgraded", "lawsuit",
    "probe", "investigation", "dilution", "debt", "default", "recall",
    "weak", "slowdown", "margin pressure", "regulatory", "ban",
}


def _graph_asymmetric_profile(
    *,
    kg_triples: List[Dict[str, Any]],
    rag_chunks: List[Dict[str, Any]],
    catalysts: List[Dict[str, Any]],
    event_summary: Dict[str, Any],
) -> Dict[str, Any]:
    rows: List[str] = []
    for triple in kg_triples[:20]:
        rows.append(" ".join(str(triple.get(key) or "") for key in ("head", "relation", "tail")))
    for chunk in rag_chunks[:8]:
        rows.append(" ".join(str(chunk.get(key) or "") for key in ("title", "text", "summary", "pack")))
    for catalyst in catalysts[:8]:
        rows.append(" ".join(str(catalyst.get(key) or "") for key in ("title", "summary", "kind")))
    text = " ".join(rows).lower()

    upside_hits = sorted({term for term in _GRAPH_UPSIDE_TERMS if term in text})
    downside_hits = sorted({term for term in _GRAPH_DOWNSIDE_TERMS if term in text})
    event_score = _safe_float(event_summary.get("impact_score"))
    catalyst_quality = _safe_float(sum(float(row.get("quality_score") or 0.0) for row in catalysts[:6]))
    source_breadth = len({str(row.get("url") or row.get("source") or row.get("kind") or "") for row in catalysts if row})
    graph_breadth = len(kg_triples[:10]) + len(rag_chunks[:6])
    score = (
        min(36.0, len(upside_hits) * 7.0)
        + min(20.0, graph_breadth * 2.0)
        + min(20.0, event_score * 0.25)
        + min(14.0, catalyst_quality * 0.08)
        + min(10.0, source_breadth * 2.0)
        - min(24.0, len(downside_hits) * 6.0)
    )
    score = round(max(0.0, min(100.0, score)), 2)
    conviction = "high" if score >= 70 else ("medium" if score >= 50 else "low")
    return {
        "score": score,
        "conviction": conviction,
        "upside_terms": upside_hits[:10],
        "downside_terms": downside_hits[:8],
        "kg_triple_count": len(kg_triples),
        "rag_chunk_count": len(rag_chunks),
        "catalyst_count": len(catalysts),
        "source_breadth": source_breadth,
        "event_score": event_score,
        "use_for_aggressive_reward": bool(score >= 65 and len(upside_hits) >= 2 and len(downside_hits) <= 2),
    }


def _apply_aggressive_reward_extension(
    zones: Dict[str, Any],
    snapshot: Dict[str, Any],
    direction_bias: str,
    graph_profile: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if direction_bias not in {"long_bias", "short_bias"}:
        return zones, {"applied": False, "reason": "no_directional_trade_bias"}
    if not graph_profile.get("use_for_aggressive_reward"):
        return zones, {"applied": False, "reason": "graph_asymmetry_below_threshold"}
    close = _safe_float(snapshot.get("close"))
    atr = _safe_float(snapshot.get("atr14"))
    if not close or not atr:
        return zones, {"applied": False, "reason": "missing_close_or_atr"}
    entry_low, entry_high = _parse_zone_text(str(zones.get("entry_zone") or ""))
    stop = _safe_float(str(zones.get("stop_zone") or "").replace("$", ""))
    target = _safe_float(str(zones.get("target_zone") or "").replace("$", ""))
    entry_price = (entry_low + entry_high) / 2.0 if entry_low and entry_high else close
    if not entry_price or not stop or not target:
        return zones, {"applied": False, "reason": "missing_entry_stop_target"}

    updated = dict(zones)
    original_target = target
    if direction_bias == "short_bias":
        stretched = max(0.01, min(target, close - 3.0 * atr))
        risk = max(0.01, stop - entry_price)
        reward = max(0.0, entry_price - stretched)
    else:
        stretched = max(target, close + 3.0 * atr)
        risk = max(0.01, entry_price - stop)
        reward = max(0.0, stretched - entry_price)
    updated["target_zone"] = f"{stretched:.2f}"
    updated["risk_reward_estimate"] = round(reward / risk, 2) if risk > 0 else zones.get("risk_reward_estimate")
    return updated, {
        "applied": bool(stretched != original_target),
        "reason": "graph_backed_asymmetric_target_extension",
        "original_target": round(original_target, 2),
        "stretched_target": round(stretched, 2),
        "target_extension_atr": 3.0,
        "graph_asymmetry_score": graph_profile.get("score"),
    }


def _parse_zone_text(value: Any) -> tuple[float, float]:
    text = str(value or "").strip().replace("$", "")
    if not text:
        return 0.0, 0.0
    parts = [part.strip() for part in text.split("-", 1)]
    try:
        first = float(parts[0])
        second = float(parts[1]) if len(parts) > 1 else first
        return min(first, second), max(first, second)
    except Exception:
        return 0.0, 0.0


def _confirmation_profile(snapshot: Dict[str, Any], setup_type: str, zones: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    candles = list((snapshot.get("chart") or {}).get("candles") or [])
    last = dict(candles[-1]) if candles else {}
    prev = dict(candles[-2]) if len(candles) >= 2 else {}
    close = _safe_float(last.get("close") if last else snapshot.get("close"))
    low = _safe_float(last.get("low"))
    high = _safe_float(last.get("high"))
    entry_low, entry_high = _parse_zone_text(zones.get("entry_zone"))
    touched = bool(entry_low and entry_high and low and high and low <= entry_high and high >= entry_low)
    prior_mid = 0.0
    if prev:
        po = _safe_float(prev.get("open"))
        pc = _safe_float(prev.get("close"))
        ph = _safe_float(prev.get("high"))
        pl = _safe_float(prev.get("low"))
        if po and pc:
            prior_mid = (po + pc) / 2.0
        elif ph and pl:
            prior_mid = (ph + pl) / 2.0
    close_above_zone = bool(close and entry_high and close >= entry_high)
    close_above_prior_mid = bool(close and prior_mid and close >= prior_mid)
    price_required = setup_type == "pullback_to_trend"
    price_ok = (not price_required) or bool(touched and (close_above_zone or close_above_prior_mid))
    volume_ratio = _safe_float((snapshot.get("volume_trend") or {}).get("ratio"))
    min_volume_ratio = _safe_float(os.getenv("APOLLO_BREAKOUT_VOLUME_RATIO_MIN", "1.20"))
    volume_required = setup_type == "base_breakout"
    volume_ok = (not volume_required) or bool(volume_ratio >= min_volume_ratio)
    return {
        "price_action_confirmation": {
            "ok": price_ok,
            "required": price_required,
            "reason": (
                "confirmed_recovery"
                if price_ok and price_required
                else ("blocked_price_action_confirmation" if price_required else "not_pullback_setup")
            ),
            "touched_entry_zone": touched,
            "close_above_entry_zone": close_above_zone,
            "close_above_prior_midpoint": close_above_prior_mid,
            "last_close": round(close, 4) if close else None,
            "entry_low": round(entry_low, 4) if entry_low else None,
            "entry_high": round(entry_high, 4) if entry_high else None,
            "prior_midpoint": round(prior_mid, 4) if prior_mid else None,
        },
        "volume_confirmation": {
            "ok": volume_ok,
            "required": volume_required,
            "reason": (
                "confirmed_breakout_volume"
                if volume_ok and volume_required
                else ("blocked_breakout_volume_confirmation" if volume_required else "not_breakout_setup")
            ),
            "volume_ratio": round(volume_ratio, 3),
            "min_volume_ratio": round(min_volume_ratio, 3),
        },
    }


def _event_risk(catalysts: List[Dict[str, Any]], earnings_date: Optional[str] = None) -> Dict[str, Any]:
    titles = " ".join(str(row.get("title") or "") for row in catalysts).lower()
    flags = []
    for word in ("earnings", "guidance", "lawsuit", "probe", "downgrade", "sec"):
        if word in titles:
            flags.append(word)
    result: Dict[str, Any] = {"flags": sorted(set(flags)), "label": "elevated" if flags else "normal"}
    if earnings_date:
        try:
            days = (datetime.strptime(earnings_date, "%Y-%m-%d").date() - datetime.now(timezone.utc).date()).days
            result["days_to_earnings"] = days
            result["earnings_date"] = earnings_date
            if 0 <= days <= 7:
                result["label"] = "elevated"
                if "earnings" not in result["flags"]:
                    result["flags"] = sorted(result["flags"] + ["earnings"])
        except Exception:
            pass
    return result


_SECTOR_ETF_MAP: Dict[str, str] = {
    "ai_compute": "XLK",
    "enterprise_platforms": "XLK",
    "money_center_financials": "XLF",
    "energy_infrastructure": "XLE",
    "healthcare_quality": "XLV",
    "industrial_automation": "XLI",
}


def _load_earnings_dates_from_corpus(tickers: List[str]) -> Dict[str, str]:
    """Query ChromaDB for pack=earnings_calendar docs, return {TICKER: 'YYYY-MM-DD'}."""
    try:
        from corpus_live_ingest import _DEFAULT_RAG_DIR, _DEFAULT_COLLECTION, _get_client
    except ImportError:
        try:
            from Apollo.corpus_live_ingest import _DEFAULT_RAG_DIR, _DEFAULT_COLLECTION, _get_client
        except ImportError:
            return {}
    try:
        client = _get_client(_DEFAULT_RAG_DIR)
        col = client.get_or_create_collection(_DEFAULT_COLLECTION)
        results = col.get(
            where={"pack": "earnings_calendar"},
            include=["metadatas"],
            limit=500,
        )
        out: Dict[str, str] = {}
        for meta in list(results.get("metadatas") or []):
            t = str((meta or {}).get("ticker") or "").strip().upper()
            ed = str((meta or {}).get("event_date") or "").strip()
            if t and ed and t not in out:
                out[t] = ed
        return out
    except Exception:
        return {}


def _rag_context_for_ticker(ticker: str, setup_type: str = "") -> List[Dict[str, Any]]:
    """
    Query ChromaDB for the most relevant corpus chunks for this ticker.
    Returns up to 8 results ranked by semantic similarity to a context query.
    Used to surface earnings press releases, analyst targets, and fundamentals
    that are in the corpus but not yet in the KG.
    """
    try:
        from Apollo.corpus_live_ingest import _DEFAULT_RAG_DIR, _DEFAULT_COLLECTION, _get_client
    except ImportError:
        try:
            from corpus_live_ingest import _DEFAULT_RAG_DIR, _DEFAULT_COLLECTION, _get_client
        except ImportError:
            return []
    def _rows_from_results(results: Dict[str, Any], *, retrieval_mode: str, error: str = "") -> List[Dict[str, Any]]:
        docs_raw = results.get("documents") or []
        metas_raw = results.get("metadatas") or []
        if docs_raw and isinstance(docs_raw[0], list):
            docs = list(docs_raw[0])
            metas = list((metas_raw or [[]])[0])
        else:
            docs = list(docs_raw)
            metas = list(metas_raw)
        out: List[Dict[str, Any]] = []
        for doc, meta in zip(docs, metas):
            meta = meta or {}
            out.append({
                "text": str(doc or "")[:400],
                "pack": str(meta.get("pack") or ""),
                "source": str(meta.get("source") or ""),
                "filed_at": str(meta.get("filed_at") or meta.get("retrieved_at") or meta.get("timestamp") or "")[:10],
                "title": str(meta.get("title") or "")[:120],
                "retrieval_mode": retrieval_mode,
                "retrieval_error": error[:160],
            })
        return out

    try:
        client = _get_client(_DEFAULT_RAG_DIR)
        col = client.get_or_create_collection(_DEFAULT_COLLECTION)
        ticker_upper = ticker.upper()
        name = (_TICKER_ALIASES.get(ticker_upper) or ("",))[0].title() or ticker_upper
        setup_words = setup_type.replace("_", " ")
        query = f"{ticker_upper} {name} revenue earnings guidance analyst {setup_words}"
        try:
            results = col.query(
                query_texts=[query],
                n_results=8,
                where={"ticker": ticker_upper},
                include=["documents", "metadatas"],
            )
            return _rows_from_results(results, retrieval_mode="dense_query")
        except Exception as query_exc:
            # Chroma can return InternalError("Error finding id") on older stores
            # while metadata get() still works. Do not silently lose RAG context.
            results = col.get(
                where={"ticker": ticker_upper},
                include=["documents", "metadatas"],
                limit=24,
            )
            rows = _rows_from_results(results, retrieval_mode="metadata_fallback", error=f"{type(query_exc).__name__}:{query_exc}")
            terms = [ticker_upper.lower(), name.lower(), "earnings", "guidance", "revenue", "analyst", setup_words.lower()]

            def _rank(row: Dict[str, Any]) -> int:
                hay = " ".join(str(row.get(key) or "") for key in ("title", "text", "pack", "source")).lower()
                return sum(1 for term in terms if term and term in hay)

            return sorted(rows, key=_rank, reverse=True)[:8]
    except Exception:
        return []


def _load_fundamentals_from_corpus(tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Read the latest fundamentals pack metadata from ChromaDB.
    Returns {TICKER: {short_ratio, short_pct_float}} without a vector search.
    """
    try:
        from Apollo.corpus_live_ingest import _DEFAULT_RAG_DIR, _DEFAULT_COLLECTION, _get_client
    except ImportError:
        try:
            from corpus_live_ingest import _DEFAULT_RAG_DIR, _DEFAULT_COLLECTION, _get_client
        except ImportError:
            return {}
    try:
        client = _get_client(_DEFAULT_RAG_DIR)
        col = client.get_or_create_collection(_DEFAULT_COLLECTION)
        results = col.get(
            where={"pack": "fundamentals"},
            include=["metadatas"],
            limit=500,
        )
        out: Dict[str, Dict[str, Any]] = {}
        for meta in list(results.get("metadatas") or []):
            t = str((meta or {}).get("ticker") or "").strip().upper()
            if t and t not in out:
                sr_raw = str(meta.get("short_ratio") or "").strip()
                spf_raw = str(meta.get("short_pct_float") or "").strip()
                out[t] = {
                    "short_ratio": float(sr_raw) if sr_raw else None,
                    "short_pct_float": float(spf_raw) if spf_raw else None,
                }
        return out
    except Exception:
        return {}


def _fetch_sector_etf_rs(themes: Dict[str, str]) -> Dict[str, float]:
    """
    Fetch 20-day relative strength of sector ETFs vs SPY.
    themes: {ticker: theme_id}
    Returns {ticker: sector_rs_pct_adjustment} where positive means sector is outperforming.
    """
    etfs_needed = set(_SECTOR_ETF_MAP.get(theme, "") for theme in themes.values() if theme)
    etfs_needed.discard("")
    if not etfs_needed:
        return {}
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY").history(period="3mo", interval="1d")
        if spy.empty:
            return {}
        spy_ret = float(spy["Close"].iloc[-1] / spy["Close"].iloc[-20] - 1) if len(spy) >= 20 else 0.0
        etf_rs: Dict[str, float] = {}
        for etf in etfs_needed:
            try:
                hist = yf.Ticker(etf).history(period="3mo", interval="1d")
                if len(hist) >= 20:
                    etf_ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-20] - 1)
                    etf_rs[etf] = round((etf_ret - spy_ret) * 100.0, 2)
            except Exception:
                pass
        # Map back to tickers
        ticker_adj: Dict[str, float] = {}
        for ticker, theme in themes.items():
            etf = _SECTOR_ETF_MAP.get(theme, "")
            if etf and etf in etf_rs:
                ticker_adj[ticker] = etf_rs[etf]
        return ticker_adj
    except Exception:
        return {}


_TICKER_ALIASES = {
    "ABBV": ("abbvie", "nyse:abbv", "abbv stock", "abbv shares"),
    "AMD": ("advanced micro devices", "nasdaq:amd", "amd stock", "amd shares"),
    "AMZN": ("amazon", "amazon.com", "nasdaq:amzn", "amzn stock", "amzn shares"),
    "ANET": ("arista networks", "nasdaq:anet", "anet stock", "anet shares"),
    "AVGO": ("broadcom", "nasdaq:avgo", "avgo stock", "avgo shares"),
    "BAC": ("bank of america", "nyse:bac", "bac stock", "bac shares"),
    "BMY": ("bristol myers", "bristol-myers squibb", "nyse:bmy", "bmy stock", "bmy shares"),
    "C": ("citigroup", "citi", "nyse:c", "citigroup stock", "citigroup shares"),
    "CAT": ("caterpillar", "caterpillar inc", "nyse:cat", "cat stock", "cat shares"),
    "COP": ("conocophillips", "conoco phillips", "nyse:cop", "cop stock", "cop shares"),
    "CRM": ("salesforce", "nasdaq:crm", "crm stock", "crm shares"),
    "CVX": ("chevron", "nyse:cvx", "cvx stock", "cvx shares"),
    "EMR": ("emerson electric", "emerson", "nyse:emr", "emr stock", "emr shares"),
    "ETN": ("eaton", "eaton corporation", "nyse:etn", "etn stock", "etn shares"),
    "GE": ("ge aerospace", "general electric", "nyse:ge", "ge stock", "ge shares"),
    "GOOGL": ("alphabet", "google", "nasdaq:googl", "googl stock", "googl shares"),
    "GS": ("goldman", "goldman sachs", "nyse:gs", "gs stock", "gs shares"),
    "HAL": ("halliburton", "nyse:hal", "hal stock", "hal shares"),
    "HON": ("honeywell", "nasdaq:hon", "hon stock", "hon shares"),
    "JNJ": ("johnson & johnson", "johnson and johnson", "nyse:jnj", "jnj stock", "jnj shares"),
    "JPM": ("jpmorgan", "jpmorgan chase", "nyse:jpm", "jpm stock", "jpm shares"),
    "LLY": ("eli lilly", "nyse:lly", "lly stock", "lly shares"),
    "MRK": ("merck", "merck & co", "nyse:mrk", "mrk stock", "mrk shares"),
    "MRVL": ("marvell technology", "marvell", "nasdaq:mrvl", "mrvl stock", "mrvl shares"),
    "MS": ("morgan stanley", "nyse:ms", "ms stock", "ms shares"),
    "MSFT": ("microsoft", "nasdaq:msft", "msft stock", "msft shares"),
    "META": ("meta platforms", "facebook parent", "nasdaq:meta", "meta stock", "meta shares"),
    "NVDA": ("nvidia", "nasdaq:nvda", "nvda stock", "nvda shares"),
    "ORCL": ("oracle", "nyse:orcl", "orcl stock", "orcl shares"),
    "OXY": ("occidental petroleum", "occidental", "nyse:oxy", "oxy stock", "oxy shares"),
    "PH": ("parker hannifin", "parker-hannifin", "nyse:ph", "ph stock", "ph shares"),
    "ROK": ("rockwell automation", "nyse:rok", "rok stock", "rok shares"),
    "SCHW": ("charles schwab", "schwab", "nyse:schw", "schw stock", "schw shares"),
    "SLB": ("schlumberger", "slb", "nyse:slb", "slb stock", "slb shares"),
    "TSM": ("taiwan semiconductor", "tsmc", "nyse:tsm", "tsm stock", "tsm shares"),
    "UNH": ("unitedhealth", "unitedhealth group", "nyse:unh", "unh stock", "unh shares"),
    "XOM": ("exxon", "exxon mobil", "exxonmobil", "nyse:xom", "xom stock", "xom shares"),
}

_TICKER_FALSE_ALIASES = {
    "CAT": ("red cat", "red cat holdings", "nasdaq:rcat", " rcat ", "(rcat)", ":rcat"),
    "META": ("metallurgical",),
}


def _catalyst_ticker_relevance(ticker: str, title: str, url: str = "") -> Dict[str, Any]:
    ticker_norm = market_data.normalize_ticker(ticker)
    hay = f" {title} {url} ".lower()
    false_hits = [alias for alias in _TICKER_FALSE_ALIASES.get(ticker_norm, ()) if alias in hay]
    aliases = _TICKER_ALIASES.get(ticker_norm, ())
    alias_hits = [alias for alias in aliases if alias in hay]
    strong_alias_hits = [
        alias
        for alias in alias_hits
        if alias not in {f"{ticker_norm.lower()} stock", f"{ticker_norm.lower()} shares"}
    ]
    ticker_pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(ticker_norm)}(?![A-Za-z0-9])", re.I) if ticker_norm else None
    ticker_hit = bool(ticker_pattern and ticker_pattern.search(f"{title} {url}"))
    if false_hits and not strong_alias_hits:
        return {"keep": False, "score": 0.0, "reason": f"false_alias:{false_hits[0]}", "matched": alias_hits}
    if alias_hits:
        return {"keep": True, "score": 1.0, "reason": "company_alias", "matched": alias_hits[:3]}
    if ticker_hit:
        return {"keep": True, "score": 0.88, "reason": "ticker_symbol", "matched": [ticker_norm]}
    return {"keep": True, "score": 0.54, "reason": "weak_ticker_context", "matched": []}


def normalize_catalysts(ticker: str, catalysts: List[Dict[str, Any]], setup_type: str = "") -> List[Dict[str, Any]]:
    ticker_norm = market_data.normalize_ticker(ticker)
    seen: set[str] = set()
    records: List[Dict[str, Any]] = []
    positive = re.compile(
        r"\b(beat|beats|raise|raises|raised|growth|surge|jumps|upgrade|record|strong|higher|"
        r"approval|contract|launch|rebound|recovery|accelerat|outperform|exceed|partnership|"
        r"dividend|buyback|patent|cleared|profit|gain|rally|momentum|expanding|milestone|"
        r"win|award|deal|positive|ahead|surprise|beat\s+estimate|raised\s+guidance|"
        r"record\s+revenue|record\s+earnings|strong\s+demand|margin\s+expansion)\b",
        re.I,
    )
    negative = re.compile(
        r"\b(miss|misses|cut|cuts|lower|falls|drop|downgrade|probe|lawsuit|weak|warning|"
        r"delay|recall|bankrupt|fraud|investigation|subpoena|breach|hack|layoff|resign|"
        r"crash|plunge|loss|decline|concern|risk|fail|liability|fine|penalty|charge|"
        r"restructur|guidance\s+cut|missed\s+estimate|below\s+expect|revenue\s+miss|"
        r"margin\s+pressure|headwind|slowdown|shortfall|impairment|write.?off)\b",
        re.I,
    )
    for row in catalysts:
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        key = (url or title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        source = str(row.get("source") or row.get("kind") or ("sec" if "sec.gov" in url else "news")).strip() or "news"
        kind = str(row.get("kind") or ("sec_filing" if "sec" in source.lower() or "sec.gov" in url else "news")).strip()
        relevance_check = _catalyst_ticker_relevance(ticker_norm, title, url)
        if not bool(relevance_check.get("keep")):
            continue
        relevance = float(relevance_check.get("score") or 0.0)
        recency = 0.68
        text = f"{title} {source}"
        support = 0.0
        if positive.search(text):
            support += 0.18
        if negative.search(text):
            support -= 0.22
        if kind == "sec_filing":
            recency += 0.08
            relevance += 0.08
        if setup_type in {"base_breakout", "trend_continuation"} and support > 0:
            support += 0.06
        quality = max(0.0, min(100.0, (relevance * 45.0) + (recency * 25.0) + (abs(support) * 100.0) + (12.0 if url else 0.0)))
        event_flags = []
        for word in ("earnings", "guidance", "lawsuit", "probe", "downgrade", "sec"):
            if word in text.lower():
                event_flags.append(word)
        records.append(
            {
                "id": re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:80],
                "ticker": ticker_norm,
                "kind": kind,
                "title": title,
                "url": url,
                "published_at": str(row.get("published_at") or ""),
                "source": source,
                "quality_score": round(quality, 2),
                "support_bias": round(support, 3),
                "event_flags": sorted(set(event_flags)),
                "ticker_relevance": relevance_check.get("reason"),
                "ticker_relevance_matches": list(relevance_check.get("matched") or []),
                "created_at": _utc_iso(),
            }
        )
    records.sort(key=lambda row: (float(row.get("quality_score") or 0.0), str(row.get("published_at") or "")), reverse=True)
    return records


def _catalyst_quality(records: List[Dict[str, Any]]) -> float:
    if not records:
        return 0.0
    top = [float(row.get("quality_score") or 0.0) for row in records[:5]]
    return round(min(100.0, (sum(top) / len(top)) + min(18.0, len(records) * 2.5)), 2)


def _score_proposal(snapshot: Dict[str, Any], catalysts: List[Dict[str, Any]], setup: Dict[str, Any], sector_rs_adj: float = 0.0) -> Dict[str, Any]:
    setup_type = str(setup.get("setup_type") or "no_trade")
    if setup_type in {"no_trade", "broken_trend", "extended_no_chase"}:
        base = {"no_trade": 30.0, "broken_trend": 25.0, "extended_no_chase": 48.0}.get(setup_type, 30.0)
    else:
        base = {"base_breakout": 72.0, "pullback_to_trend": 68.0, "trend_continuation": 62.0, "event_dislocation": 74.0}.get(setup_type, 55.0)
    close = _safe_float(snapshot.get("close"))
    sma20 = _safe_float(snapshot.get("sma20"))
    sma50 = _safe_float(snapshot.get("sma50"))
    rsi = _safe_float(snapshot.get("rsi14"))
    volume_ratio = _safe_float((snapshot.get("volume_trend") or {}).get("ratio"))
    regime = dict(snapshot.get("market_regime") or {})
    trend_score = 50.0
    if close and sma20 and sma50:
        trend_score = 50.0 + min(20.0, max(-20.0, ((close - sma50) / close) * 250.0))
        if sma20 > sma50:
            trend_score += 10.0
    momentum_score = 50.0
    if rsi:
        momentum_score = 72.0 if 50 <= rsi <= 68 else (58.0 if 40 <= rsi < 76 else 35.0)
    rs_score = _rs_score(snapshot, sector_rs_adj=sector_rs_adj)
    volume_score = 50.0 + min(25.0, max(-20.0, (volume_ratio - 1.0) * 35.0)) if volume_ratio else 45.0
    catalyst_score = _catalyst_quality(catalysts)
    regime_score = _safe_float(regime.get("score") or 45.0)
    risk_penalty = max(0.0, (_safe_float(snapshot.get("atr_pct")) - 5.0) * 2.5)
    provider_confidence = _safe_float(snapshot.get("provider_confidence") or 70.0)
    provider_penalty = max(0.0, (75.0 - provider_confidence) * 0.18)
    score = (
        base * 0.20
        + trend_score * 0.18
        + momentum_score * 0.12
        + rs_score * 0.17
        + volume_score * 0.10
        + catalyst_score * 0.10
        + regime_score * 0.13
        - risk_penalty
        - provider_penalty
    )
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "components": {
            "base": round(base, 2),
            "trend": round(trend_score, 2),
            "momentum": round(momentum_score, 2),
            "relative_strength": round(rs_score, 2),
            "volume": round(volume_score, 2),
            "catalyst": round(catalyst_score, 2),
            "market_regime": round(regime_score, 2),
            "risk_penalty": round(risk_penalty, 2),
            "provider_confidence": round(provider_confidence, 2),
            "provider_penalty": round(provider_penalty, 2),
        },
    }


def _level_float(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    first = text.split("-")[0].strip()
    return _safe_float_optional(first)


def load_outcomes(path: Optional[str] = None) -> Dict[str, Any]:
    out_path = Path(path or os.getenv("APOLLO_SWING_OUTCOMES_PATH", str(OUTCOMES_PATH)))
    payload = _read_json(out_path, {})
    payload.setdefault("entries", [])
    payload["path"] = str(out_path)
    return payload


def save_outcomes(payload: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    out_path = Path(path or os.getenv("APOLLO_SWING_OUTCOMES_PATH", str(OUTCOMES_PATH)))
    data = dict(payload or {})
    data.setdefault("entries", [])
    data["entries"] = list(data.get("entries") or [])[-1000:]
    data["updated_at"] = _utc_iso()
    data["path"] = str(out_path)
    _write_json(out_path, data)
    return data


def _followup_returns(entry_price: Optional[float], candles: List[Dict[str, Any]], direction_bias: str = "long_bias") -> Dict[str, Any]:
    if not entry_price or entry_price <= 0:
        return {}
    out: Dict[str, Any] = {}
    for days in (2, 5, 10, 20):
        window = candles[:days]
        if len(window) < days:
            out[f"{days}d"] = {"available": False}
            continue
        closes = [_safe_float(row.get("close")) for row in window]
        highs = [_safe_float(row.get("high")) for row in window]
        lows = [_safe_float(row.get("low")) for row in window]
        closes = [val for val in closes if val]
        highs = [val for val in highs if val]
        lows = [val for val in lows if val]
        if direction_bias == "short_bias":
            close_return = ((entry_price - closes[-1]) / entry_price) * 100.0 if closes else None
            favorable = ((entry_price - min(lows)) / entry_price) * 100.0 if lows else None
            adverse = ((entry_price - max(highs)) / entry_price) * 100.0 if highs else None
        else:
            close_return = ((closes[-1] - entry_price) / entry_price) * 100.0 if closes else None
            favorable = ((max(highs) - entry_price) / entry_price) * 100.0 if highs else None
            adverse = ((min(lows) - entry_price) / entry_price) * 100.0 if lows else None
        out[f"{days}d"] = {
            "available": bool(closes),
            "close_return_pct": round(close_return, 2) if close_return is not None else None,
            "max_favorable_pct": round(favorable, 2) if favorable is not None else None,
            "max_adverse_pct": round(adverse, 2) if adverse is not None else None,
        }
    return out


def _candles_after(first_seen: Any, candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    first_dt = _parse_iso(first_seen)
    out: List[Dict[str, Any]] = []
    for row in candles:
        try:
            day = datetime.fromisoformat(str(row.get("date") or "")[:10]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if day.date() > first_dt.date():
            out.append(row)
    return out


def _outcome_status(proposal: Dict[str, Any], candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    entry = _level_float(proposal.get("entry_zone")) or _safe_float_optional((proposal.get("swing_snapshot") or {}).get("close"))
    stop = _level_float(proposal.get("stop_zone"))
    target = _level_float(proposal.get("target_zone"))
    first_seen = (proposal.get("lifecycle") or {}).get("first_seen") or proposal.get("created_at") or _utc_iso()
    future_candles = _candles_after(first_seen, candles)
    direction_bias = str(proposal.get("direction_bias") or "long_bias")
    status = "still_valid"
    if proposal.get("no_trade_reason"):
        status = "no_trade"
    elif direction_bias == "short_bias" and future_candles and stop and any((_safe_float(row.get("high")) or 0.0) >= stop for row in future_candles[:20]):
        status = "stop_hit"
    elif direction_bias == "short_bias" and future_candles and target and any((_safe_float(row.get("low")) or 0.0) <= target for row in future_candles[:20]):
        status = "target_hit"
    elif future_candles and stop and any((_safe_float(row.get("low")) or 0.0) <= stop for row in future_candles[:20]):
        status = "stop_hit"
    elif future_candles and target and any((_safe_float(row.get("high")) or 0.0) >= target for row in future_candles[:20]):
        status = "target_hit"
    elif len(future_candles) >= 20:
        status = "expired"
    return {
        "status": status,
        "entry_price": entry,
        "stop": stop,
        "target": target,
        "followup_days_available": len(future_candles),
        "followup_returns": _followup_returns(entry, future_candles, direction_bias),
    }


def recompute_outcomes(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    outcomes = load_outcomes(str(payload.get("path") or "") or None)
    entries = [dict(row) for row in list(outcomes.get("entries") or []) if isinstance(row, dict)]
    latest = dict(payload.get("latest") or latest_swing_study())
    proposals = [dict(row) for row in list(latest.get("proposals") or []) if isinstance(row, dict)]
    by_key = {str(row.get("key") or ""): row for row in entries}
    for proposal in proposals:
        ticker = market_data.normalize_ticker(str(proposal.get("ticker") or ""))
        if not ticker:
            continue
        first_seen = str((proposal.get("lifecycle") or {}).get("first_seen") or proposal.get("created_at") or latest.get("created_at") or _utc_iso())
        key = f"{ticker}:{first_seen[:10]}:{proposal.get('setup_type')}"
        candles = list(((proposal.get("swing_snapshot") or {}).get("chart") or {}).get("candles") or [])
        entry = dict(by_key.get(key) or {"key": key, "ticker": ticker, "first_seen": first_seen, "setup_type": proposal.get("setup_type")})
        entry.update(
            {
                "last_score": proposal.get("score"),
                "last_status": proposal.get("status"),
                "direction_bias": proposal.get("direction_bias"),
                "market_regime": dict(proposal.get("market_regime") or {}),
                "risk_tier": proposal.get("risk_tier") or proposal.get("paper_risk_tier") or "observation",
                "outcome": _outcome_status(proposal, candles),
                "updated_at": _utc_iso(),
            }
        )
        by_key[key] = entry
    saved = save_outcomes({"entries": list(by_key.values())}, str(payload.get("path") or "") or None)
    return {"ok": True, "outcomes": saved, "entry_count": len(saved.get("entries") or [])}


def _outcome_for_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    candles = list(((proposal.get("swing_snapshot") or {}).get("chart") or {}).get("candles") or [])
    return _outcome_status(proposal, candles)


def _setup_history(ticker: str, setup_type: str, score: float, state: Dict[str, Any]) -> Dict[str, Any]:
    positions = dict(state.get("positions") or {})
    existing = dict(positions.get(ticker) or {})
    previous_score = _safe_float(existing.get("last_score"))
    invalidations = _safe_int(existing.get("invalidation_count"))
    stale_repeat = bool(existing.get("last_setup_type") == setup_type and previous_score and abs(previous_score - score) < 2)
    best_prior = max(previous_score, _safe_float(existing.get("best_score")))
    return {
        "first_seen": str(existing.get("first_seen") or ""),
        "previous_score": previous_score,
        "best_prior_score": round(best_prior, 2) if best_prior else 0.0,
        "prior_setup_type": str(existing.get("last_setup_type") or ""),
        "prior_invalidations": invalidations,
        "stale_repeat": stale_repeat,
    }


def _learning_notes(proposal: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    lifecycle = dict(proposal.get("lifecycle") or {})
    setup_history = dict(proposal.get("setup_history") or {})
    if lifecycle.get("change") == "improving":
        notes.append("Score is improving versus the prior run.")
    elif lifecycle.get("change") == "deteriorating":
        notes.append("Score is deteriorating versus the prior run.")
    elif setup_history.get("stale_repeat"):
        notes.append("This is a stale repeat; avoid re-alerting unless confirmation improves.")
    if proposal.get("provider_confidence") and _safe_float(proposal.get("provider_confidence")) < 70:
        notes.append("Provider confidence is degraded; verify chart manually before action.")
    if proposal.get("outcome_status", {}).get("status") in {"stop_hit", "expired"}:
        notes.append("Historical follow-up marks this setup as no longer clean.")
    learned = dict(proposal.get("learned_adjustment") or {})
    if _safe_float(learned.get("adjustment")) > 0:
        notes.append("Learning loop boosted this setup from prior paper/outcome performance.")
    elif _safe_float(learned.get("adjustment")) < 0:
        notes.append("Learning loop penalized this setup from prior paper/outcome performance.")
    return notes


def _learning_adjustment_for_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from Apollo import learning_loop

        return learning_loop.learned_adjustment_for_proposal(proposal)
    except Exception as exc:
        return {"adjustment": 0.0, "reasons": [f"learning_unavailable:{type(exc).__name__}"], "confidence": 0.0}


def _status_for(ticker: str, proposal: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    positions = dict(state.get("positions") or {})
    existing = dict(positions.get(ticker) or {})
    now = _utc_iso()
    score = _safe_float(proposal.get("score"))
    setup_type = str(proposal.get("setup_type") or "")
    no_trade = bool(proposal.get("no_trade_reason"))
    status = str(existing.get("status") or "").strip().lower()
    if status not in VALID_STATUSES:
        status = "new"
    first_seen = str(existing.get("first_seen") or now)
    previous_score = _safe_float(existing.get("last_score"))
    previous_setup = str(existing.get("last_setup_type") or "")
    invalidated_at = str(existing.get("invalidated_at") or "")
    already_notified = bool(existing.get("notified"))
    invalidation_count = _safe_int(existing.get("invalidation_count"))
    if no_trade and status in {"watching", "confirmed", "paper_open"}:
        status = "invalidated"
        invalidated_at = invalidated_at or now
        if not existing.get("invalidated_at"):
            invalidation_count += 1
    elif no_trade:
        status = "expired" if status in {"new", "rejected"} else status
    elif status in {"new", "watching"} and score >= 82:
        status = "confirmed" if status == "watching" else "new"
    change = "new"
    if previous_score:
        if score >= previous_score + 5:
            change = "improving"
        elif score <= previous_score - 5:
            change = "deteriorating"
        else:
            change = "still_valid"
    record = {
        **existing,
        "ticker": ticker,
        "status": status,
        "first_seen": first_seen,
        "last_seen": now,
        "last_score": score,
        "last_setup_type": setup_type,
        "previous_score": previous_score,
        "previous_setup_type": previous_setup,
        "change": change,
        "invalidated_at": invalidated_at,
        "invalidation_count": invalidation_count,
        "best_score": round(max(score, _safe_float(existing.get("best_score"))), 2),
        "notified": already_notified,
    }
    positions[ticker] = record
    state["positions"] = positions
    return record


def _synthesize_trade_thesis(
    ticker: str,
    setup_type: str,
    kg_triples: List[Dict[str, Any]],
    catalysts: List[Dict[str, Any]],
    swing_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Call Ollama to synthesize a directional trade thesis from KG and catalyst context.
    Returns {} on any failure so callers can treat it as a no-op."""
    if not _THESIS_ENABLED:
        return {}
    if not kg_triples and not catalysts:
        return {}
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model = _THESIS_MODEL or os.getenv("MODEL_NAME", "gemma4:31b")
    triple_lines = "\n".join(
        f"- {t.get('head', '')} {t.get('relation', '')} {t.get('tail', '')}"
        for t in kg_triples[:15]
    ) or "(none)"
    catalyst_lines = "\n".join(
        f"- {str(c.get('title') or '')[:140]}"
        for c in catalysts[:6]
    ) or "(none)"
    close = swing_snapshot.get("close", "?")
    sma20 = swing_snapshot.get("sma20", "?")
    rsi = swing_snapshot.get("rsi14", "?")
    atr_pct = swing_snapshot.get("atr_pct", "?")
    prompt = (
        f"You are a systematic swing trade analyst. Evaluate this setup and return JSON only — no prose outside the JSON.\n\n"
        f"Ticker: {ticker}\n"
        f"Setup: {setup_type}\n"
        f"Price: {close}  SMA20: {sma20}  RSI14: {rsi}  ATR%: {atr_pct}\n\n"
        f"Knowledge graph context:\n{triple_lines}\n\n"
        f"Recent catalysts:\n{catalyst_lines}\n\n"
        f"Return ONLY this JSON (fill every field):\n"
        f'{{\n'
        f'  "direction": "long" or "short" or "no_trade",\n'
        f'  "confidence_adjustment": <integer from -15 to 10>,\n'
        f'  "thesis": "<one sentence — core reason this setup has edge>",\n'
        f'  "primary_risk": "<one sentence — biggest thing that invalidates the thesis>",\n'
        f'  "catalyst_quality": "strong" or "moderate" or "weak" or "none"\n'
        f'}}'
    )
    try:
        resp = requests.post(
            f"{ollama_host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json", "options": {"num_predict": 250}},
            timeout=(4, _THESIS_TIMEOUT),  # 4s connect, full timeout for read
        )
        if not resp.ok:
            return {}
        raw = resp.json().get("response", "")
        if not raw:
            return {}
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            return {}
        direction = str(parsed.get("direction") or "").strip().lower()
        if direction not in {"long", "short", "no_trade"}:
            direction = ""
        try:
            adj = max(-15, min(10, int(parsed.get("confidence_adjustment") or 0)))
        except (TypeError, ValueError):
            adj = 0
        cq = str(parsed.get("catalyst_quality") or "").strip().lower()
        if cq not in {"strong", "moderate", "weak", "none"}:
            cq = ""
        return {
            "direction": direction,
            "confidence_adjustment": adj,
            "thesis": str(parsed.get("thesis") or "")[:300],
            "primary_risk": str(parsed.get("primary_risk") or "")[:300],
            "catalyst_quality": cq,
        }
    except Exception:
        return {}


def _proposal_for_ticker(
    ticker: str,
    swing_snapshot: Dict[str, Any],
    market_proposal: Dict[str, Any],
    catalysts: List[Dict[str, Any]],
    state: Dict[str, Any],
    event_summary: Optional[Dict[str, Any]] = None,
    kg_context: Optional[List[Dict[str, Any]]] = None,
    earnings_date: Optional[str] = None,
    sector_rs_adj: float = 0.0,
    short_interest: Optional[Dict[str, Any]] = None,
    rag_context: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    setup = classify_setup(swing_snapshot)
    setup_type = str(setup.get("setup_type") or "no_trade")
    regime = dict(swing_snapshot.get("market_regime") or {})
    if regime.get("label") == "risk_off" and setup_type in {"base_breakout", "trend_continuation"}:
        risk_notes = list(setup.get("risk_notes") or [])
        risk_notes.append("Market regime is risk_off — aggressive long setups suppressed.")
        setup = {"setup_type": "no_trade", "no_trade_reason": "market_regime_risk_off", "risk_notes": risk_notes}
        setup_type = "no_trade"
    catalyst_records = normalize_catalysts(ticker, catalysts, setup_type)
    score_payload = _score_proposal(swing_snapshot, catalyst_records, setup, sector_rs_adj=sector_rs_adj)
    kg_triples = list(kg_context or [])
    rag_chunks = list(rag_context or [])
    if kg_triples:
        kg_boost = min(8.0, len(kg_triples) * 1.5)
        components = dict(score_payload.get("components") or {})
        components["kg_grounding"] = round(kg_boost, 2)
        score_payload["components"] = components
        score_payload["score"] = round(min(100.0, float(score_payload.get("score") or 0.0) + kg_boost), 2)
    # RAG context boost: each unique pack type that surfaces adds a small signal bonus
    if rag_chunks:
        unique_packs = {str(c.get("pack") or "") for c in rag_chunks} - {""}
        rag_boost = min(5.0, len(unique_packs) * 1.2)
        components = dict(score_payload.get("components") or {})
        components["rag_context"] = round(rag_boost, 2)
        score_payload["components"] = components
        score_payload["score"] = round(min(100.0, float(score_payload.get("score") or 0.0) + rag_boost), 2)
    _thesis = _synthesize_trade_thesis(ticker, setup_type, kg_triples, catalyst_records, swing_snapshot)
    if _thesis:
        _adj = _thesis.get("confidence_adjustment") or 0
        if _adj:
            score_payload["score"] = round(min(100.0, max(0.0, float(score_payload.get("score") or 0.0) + _adj)), 2)
            _tc = dict(score_payload.get("components") or {})
            _tc["llm_thesis_adj"] = _adj
            score_payload["components"] = _tc
    event_risk = _event_risk(catalyst_records, earnings_date=earnings_date)
    no_trade_reason = str(setup.get("no_trade_reason") or "")
    score = _safe_float(score_payload.get("score"))
    event_summary = dict(event_summary or {})
    event_delta = _safe_float(event_summary.get("event_score_component"))
    event_bias = str(event_summary.get("directional_bias") or "").strip().lower()
    event_top = dict(event_summary.get("top_event") or {})
    event_ready = bool(event_summary.get("ok"))
    event_impact_score = _safe_float(event_summary.get("impact_score"))
    event_dislocation_min = _safe_float(os.getenv("APOLLO_EVENT_DISLOCATION_MIN_IMPACT", "62"))
    event_dislocation = bool(
        event_ready
        and event_bias in {"positive", "negative"}
        and event_impact_score >= event_dislocation_min
    )
    if event_dislocation:
        setup_type = "event_dislocation"
        setup["setup_type"] = setup_type
        no_trade_reason = ""
        event_floor = min(92.0, 58.0 + max(0.0, event_impact_score - event_dislocation_min) * 1.15)
        score = max(score, event_floor)
    if event_dislocation and event_bias == "negative":
        score = max(0.0, min(100.0, score + abs(event_delta)))
    elif event_ready and setup_type not in {"no_trade", "broken_trend", "extended_no_chase"}:
        score = max(0.0, min(100.0, score + event_delta))
    elif event_bias == "negative" and _safe_float(event_summary.get("impact_score")) >= 58:
        score = max(0.0, score + min(0.0, event_delta))
    score_payload["score"] = round(score, 2)
    components = dict(score_payload.get("components") or {})
    components["event_impact"] = round(event_delta, 2)
    score_payload["components"] = components
    # Apply short squeeze boost before direction/no_trade_reason decisions
    _squeeze_note: str = ""
    if short_interest and setup_type == "base_breakout":
        _sr = short_interest.get("short_ratio")
        _spf = short_interest.get("short_pct_float")
        _sr_v = float(_sr) if _sr is not None else 0.0
        _spf_v = float(_spf) if _spf is not None else 0.0
        if _sr_v > 5 or _spf_v > 15:
            _sq_boost = min(6.0, (_sr_v - 5) * 0.8) if _sr_v > 5 else min(4.0, (_spf_v - 15) * 0.15)
            score = round(min(100.0, score + _sq_boost), 2)
            score_payload["score"] = score
            _comp = dict(score_payload.get("components") or {})
            _comp["short_squeeze_fuel"] = round(_sq_boost, 2)
            score_payload["components"] = _comp
            _squeeze_note = f"High short interest ({_sr_v:.1f}d to cover) on breakout — squeeze catalyst potential."
    setup_history = _setup_history(ticker, setup_type, score, state)
    learned_score_before = round(score, 2)
    learned_probe = {
        "ticker": ticker,
        "score": score,
        "setup_type": setup_type,
        "market_regime": dict(swing_snapshot.get("market_regime") or {}),
        "catalyst_records": catalyst_records[:8],
        "event_impact": event_summary,
        "setup_history": setup_history,
    }
    learned_adjustment = _learning_adjustment_for_proposal(learned_probe)
    learned_delta = _safe_float(learned_adjustment.get("adjustment"))
    if learned_delta:
        score = round(max(0.0, min(100.0, score + learned_delta)), 2)
        score_payload["score"] = score
        learned_components = dict(score_payload.get("components") or {})
        learned_components["learned_adjustment"] = learned_delta
        score_payload["components"] = learned_components
    if score < 65 and not no_trade_reason:
        no_trade_reason = "score below swing threshold"
    if event_bias == "negative" and _safe_float(event_summary.get("impact_score")) >= 62 and not event_dislocation and not no_trade_reason:
        no_trade_reason = "negative event impact"
    if setup_type in {"broken_trend", "extended_no_chase"} and not no_trade_reason:
        no_trade_reason = setup_type
    if _thesis.get("direction") == "no_trade" and not no_trade_reason:
        no_trade_reason = "llm_thesis_no_trade"
    if no_trade_reason or score < 65:
        direction = "no_trade"
    elif event_dislocation and event_bias == "negative":
        direction = "short_bias"
    elif _thesis.get("direction") == "short":
        direction = "short_bias"
    else:
        direction = "long_bias"
    zones = _event_dislocation_zones(swing_snapshot, "short_bias" if event_bias == "negative" else "long_bias") if event_dislocation else _zones(swing_snapshot, setup_type)
    graph_asymmetry = _graph_asymmetric_profile(
        kg_triples=kg_triples,
        rag_chunks=rag_chunks,
        catalysts=catalyst_records,
        event_summary=event_summary,
    )
    zones, aggressive_reward_extension = _apply_aggressive_reward_extension(zones, swing_snapshot, direction, graph_asymmetry)
    confirmations = _confirmation_profile(swing_snapshot, setup_type, zones)
    status_record = _status_for(
        ticker,
        {"score": score, "setup_type": setup_type, "no_trade_reason": no_trade_reason},
        state,
    )
    review_by = (_utc_now() + timedelta(days=5)).date().isoformat()
    risk_notes = list(setup.get("risk_notes") or [])
    risk_notes.extend(list((swing_snapshot.get("market_regime") or {}).get("risk_notes") or [])[:2])
    if _squeeze_note:
        risk_notes.append(_squeeze_note)
    elif short_interest:
        _sr2 = short_interest.get("short_ratio")
        _spf2 = short_interest.get("short_pct_float")
        _sr2_v = float(_sr2) if _sr2 is not None else 0.0
        _spf2_v = float(_spf2) if _spf2 is not None else 0.0
        if _sr2_v > 5:
            risk_notes.append(f"High short interest: {_sr2_v:.1f} days to cover — elevated gap risk.")
        elif _spf2_v > 15:
            risk_notes.append(f"Short interest {_spf2_v:.1f}% of float — monitor for volatility.")
    provider_confidence = _safe_float(swing_snapshot.get("provider_confidence") or 0.0)
    proposal = {
        "ticker": ticker,
        "direction_bias": direction,
        "confidence": score,
        "score": score,
        "score_components": dict(score_payload.get("components") or {}),
        "learned_adjustment": learned_adjustment,
        "learned_adjustment_reasons": list(learned_adjustment.get("reasons") or []),
        "learned_score_before": learned_score_before,
        "learned_score_after": score,
        "learning_confidence": learned_adjustment.get("confidence", 0.0),
        "event_impact": event_summary,
        "event_score_component": round(event_delta, 2),
        "confidence_label": "high" if score >= 82 and provider_confidence >= 75 else ("medium" if score >= 65 and provider_confidence >= 60 else "low"),
        "setup_type": setup_type,
        "status": status_record.get("status"),
        "lifecycle": status_record,
        "entry_zone": zones.get("entry_zone"),
        "stop_zone": zones.get("stop_zone"),
        "target_zone": zones.get("target_zone"),
        "risk_reward_estimate": zones.get("risk_reward_estimate"),
        "risk_distance_pct": zones.get("risk_distance_pct"),
        "price_action_confirmation": confirmations.get("price_action_confirmation") or {},
        "volume_confirmation": confirmations.get("volume_confirmation") or {},
        "atr_pct": swing_snapshot.get("atr_pct"),
        "days_in_setup": max(1, (_utc_now() - _parse_iso(status_record.get("first_seen"))).days + 1),
        "market_regime": dict(swing_snapshot.get("market_regime") or {}),
        "relative_strength_rank": round(_rs_score(swing_snapshot, sector_rs_adj=sector_rs_adj), 2),
        "earnings_or_event_risk": event_risk,
        "invalidation_trigger": (
            f"Close above {zones.get('stop_zone') or 'stop zone'} or catalyst fades."
            if direction == "short_bias"
            else f"Close below {zones.get('stop_zone') or 'stop zone'} or setup changes to broken_trend."
        ),
        "review_by_date": review_by,
        "human_approval_required": True,
        "live_trade_execution": False,
        "time_horizon": "2_to_20_trading_days",
        "swing_snapshot": swing_snapshot,
        "market_proposal": market_proposal,
        "provider_confidence": provider_confidence,
        "data_disagreements": list(swing_snapshot.get("data_disagreements") or []),
        "catalyst_count": len(catalyst_records),
        "catalyst_quality_score": _catalyst_quality(catalyst_records),
        "catalyst_records": catalyst_records[:8],
        "catalyst_summary": [str(row.get("title") or "") for row in catalyst_records[:5]],
        "source_links": [str(row.get("url") or "") for row in catalyst_records[:8] if str(row.get("url") or "").strip()],
        "setup_history": setup_history,
        "risk_notes": risk_notes,
        "no_trade_reason": no_trade_reason,
        "kg_grounding": {
            "triples": kg_triples[:10],
            "triple_count": len(kg_triples),
            "kg_boost": round(min(8.0, len(kg_triples) * 1.5), 2) if kg_triples else 0.0,
        },
        "graph_risk_appetite": graph_asymmetry,
        "aggressive_reward_profile": {
            "pressed_candidate": bool(graph_asymmetry.get("use_for_aggressive_reward")),
            "extension": aggressive_reward_extension,
            "paper_only": True,
            "human_approval_required": True,
            "live_trade_execution": False,
        },
        "rag_context": {
            "chunks": rag_chunks[:6],
            "chunk_count": len(rag_chunks),
            "packs": sorted({str(c.get("pack") or "") for c in rag_chunks} - {""}),
        },
        "trade_thesis": {
            "direction": _thesis.get("direction", ""),
            "thesis": _thesis.get("thesis", ""),
            "primary_risk": _thesis.get("primary_risk", ""),
            "catalyst_quality": _thesis.get("catalyst_quality", ""),
            "confidence_adjustment": _thesis.get("confidence_adjustment", 0),
        },
    }
    proposal["paper_order_candidate"] = None
    if (
        event_summary.get("paper_order_candidate")
        and proposal.get("direction_bias") in {"long_bias", "short_bias"}
        and _safe_float(proposal.get("score")) >= 82.0
        and not proposal.get("no_trade_reason")
    ):
        proposal["paper_order_candidate"] = {
            **dict(event_summary.get("paper_order_candidate") or {}),
            "swing_score": proposal.get("score"),
            "setup_type": proposal.get("setup_type"),
            "direction_bias": proposal.get("direction_bias"),
            "entry_zone": proposal.get("entry_zone"),
            "stop_zone": proposal.get("stop_zone"),
            "target_zone": proposal.get("target_zone"),
            "event_title": event_top.get("title") or "",
            "human_approval_required": True,
            "live_trade_execution": False,
            "broker_order_created": False,
        }
    proposal["outcome_status"] = _outcome_for_proposal(proposal)
    proposal["followup_returns"] = dict((proposal.get("outcome_status") or {}).get("followup_returns") or {})
    proposal["learning_notes"] = _learning_notes(proposal)
    proposal["short_interest"] = short_interest or {}
    proposal["chart_levels"] = {
        "entry_zone": proposal.get("entry_zone"),
        "stop": _level_float(proposal.get("stop_zone")),
        "target": _level_float(proposal.get("target_zone")),
        "current": _safe_float_optional(swing_snapshot.get("close")),
        "pivot_support": zones.get("pivot_support"),
        "pivot_resistance": zones.get("pivot_resistance"),
    }
    return proposal


def _parse_iso(value: Any) -> datetime:
    try:
        text = str(value or "").replace("Z", "+00:00")
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except Exception:
        return _utc_now()


def build_swing_study(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    tickers = _tickers_from_payload(payload)
    state = load_swing_state(str(payload.get("state_path") or "") or None)
    market = market_study.build_market_study({"tickers": tickers, "write_artifacts": False, "refresh": payload.get("refresh", True)})
    swing = swing_data.swing_snapshot({"tickers": tickers})
    include_event_impact = bool(payload.get("include_event_impact")) or os.getenv("APOLLO_EVENT_IMPACT_ENABLED", "0") == "1"
    event_payload: Dict[str, Any] = {}
    event_by_ticker: Dict[str, Dict[str, Any]] = {}
    if include_event_impact:
        try:
            event_payload = event_impact.build_event_impact(
                {
                    "tickers": tickers,
                    "refresh": payload.get("refresh", True),
                    "max_results": payload.get("event_max_results", payload.get("max_results", 6)),
                    "write_artifacts": False,
                }
            )
            event_by_ticker = {
                str(row.get("ticker") or ""): dict(row)
                for row in list(event_payload.get("summaries") or [])
                if isinstance(row, dict)
            }
        except Exception as exc:
            event_payload = {"ok": False, "error": f"event_impact_failed:{type(exc).__name__}:{exc}", "summaries": []}
    swing_by_ticker = {str(row.get("ticker") or ""): row for row in list(swing.get("snapshots") or []) if isinstance(row, dict)}
    market_by_ticker = {str(row.get("ticker") or ""): row for row in list(market.get("proposals") or []) if isinstance(row, dict)}
    market_state = market_data.load_market_state()
    raw_catalysts = dict(market_state.get("catalysts") or {})
    catalysts_by_ticker = {}
    for row in list(market.get("proposals") or []):
        if isinstance(row, dict):
            ticker = str(row.get("ticker") or "")
            catalysts_by_ticker[ticker] = list(raw_catalysts.get(ticker) or []) or [
                {"title": title, "url": url}
                for title, url in zip(list(row.get("catalyst_summary") or []), list(row.get("source_links") or []))
            ]
    kg_path = _default_kg_path()
    kg_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    if kg_path.exists():
        for _t in tickers:
            kg_by_ticker[_t] = _kg_triples_for_ticker(_t, kg_path)

    rag_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for _t in tickers:
        _setup = str((swing_by_ticker.get(_t) or {}).get("setup_type") or "")
        rag_by_ticker[_t] = _rag_context_for_ticker(_t, _setup)

    earnings_by_ticker = _load_earnings_dates_from_corpus(tickers)
    fundamentals_by_ticker = _load_fundamentals_from_corpus(tickers)

    ticker_themes = {}
    for row in list(market.get("proposals") or []):
        if isinstance(row, dict):
            t = str(row.get("ticker") or "")
            if t:
                ticker_themes[t] = str(swing_by_ticker.get(t, {}).get("theme") or "")
    # Fall back to theme from swing snapshot if market doesn't have it
    for row in list(swing.get("snapshots") or []):
        if isinstance(row, dict):
            t = str(row.get("ticker") or "")
            if t and not ticker_themes.get(t):
                ticker_themes[t] = str(row.get("theme") or "")
    sector_rs_by_ticker = _fetch_sector_etf_rs(ticker_themes)

    proposals = [
        _proposal_for_ticker(
            ticker,
            dict(swing_by_ticker.get(ticker) or {"ok": False, "ticker": ticker, "error": "missing_swing_snapshot"}),
            dict(market_by_ticker.get(ticker) or {}),
            list(catalysts_by_ticker.get(ticker) or []),
            state,
            dict(event_by_ticker.get(ticker) or {}),
            kg_context=list(kg_by_ticker.get(ticker) or []),
            earnings_date=earnings_by_ticker.get(ticker),
            sector_rs_adj=sector_rs_by_ticker.get(ticker, 0.0),
            short_interest=fundamentals_by_ticker.get(ticker),
            rag_context=rag_by_ticker.get(ticker),
        )
        for ticker in tickers
    ]
    data_quality_band = str(payload.get("data_quality_band") or "").strip().lower()
    if data_quality_band in {"low", "medium"}:
        proposals = _apply_data_quality_override(proposals, data_quality_band)
    watched_statuses = {"watching", "confirmed", "paper_open"}
    proposals.sort(key=lambda row: (1 if str(row.get("status") or "") in watched_statuses else 0, float(row.get("score") or 0.0)), reverse=True)
    best = proposals[0] if proposals else {}
    high_confidence = bool(best and best.get("direction_bias") in {"long_bias", "short_bias"} and _safe_float(best.get("score")) >= 82.0)
    blocked = bool(market.get("blocked_session"))
    invalidated_watched = any(str(row.get("status") or "") == "invalidated" for row in proposals)
    notify_reason = ""
    if high_confidence and not bool(((best.get("lifecycle") or {}).get("notified"))):
        notify_reason = "high_confidence_swing_proposal"
        _mark_notified(state, str(best.get("ticker") or ""))
    elif invalidated_watched:
        notify_reason = "swing_setup_invalidated"
    elif blocked:
        notify_reason = "browser_session_blocked"
    run_id = str(payload.get("run_id") or f"apollo_swing_study_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    state["last_run_id"] = run_id
    state["last_proposals"] = proposals
    save_swing_state(state, str(payload.get("state_path") or "") or None)
    catalyst_records = [record for row in proposals for record in list(row.get("catalyst_records") or [])]
    _write_list_artifact(CATALYST_RECORDS_PATH, "catalyst_records", catalyst_records[-500:])
    study = {
        "ok": bool(proposals),
        "run_id": run_id,
        "created_at": _utc_iso(),
        "tickers": tickers,
        "source_health": {"market": market.get("source_health") or {}, "swing": swing.get("source_health") or {}},
        "event_impact": event_payload,
        "market_regime": swing.get("market_regime") or {},
        "provider_health": ((swing.get("source_health") or {}).get("providers") or {}),
        "catalyst_record_count": len(catalyst_records),
        "proposals": proposals,
        "best_proposal": best,
        "high_confidence": high_confidence,
        "blocked_session": blocked,
        "notify_reason": notify_reason,
        "summary": _format_summary(proposals),
    }
    outcome_result = recompute_outcomes({"latest": study}) if proposals else {"ok": True, "outcomes": load_outcomes()}
    study["outcomes"] = outcome_result.get("outcomes") or {}
    if bool(payload.get("write_artifacts", True)):
        study["artifacts"] = write_swing_study_artifacts(study)
    if bool(payload.get("notify", False)):
        study["notify"] = maybe_notify(study)
    return study


def _mark_notified(state: Dict[str, Any], ticker: str) -> None:
    positions = dict(state.get("positions") or {})
    row = dict(positions.get(ticker) or {})
    row["notified"] = True
    positions[ticker] = row
    state["positions"] = positions


def _format_summary(proposals: List[Dict[str, Any]]) -> str:
    if not proposals:
        return "Apollo found no swing setups."
    lines: List[str] = []
    watched = [row for row in proposals if str(row.get("status") or "") in {"watching", "confirmed", "paper_open"}]
    improving = [row for row in proposals if (row.get("lifecycle") or {}).get("change") == "improving"]
    if watched:
        lines.append("Watched changes:")
        for row in watched[:4]:
            lines.append(f"- {row.get('ticker')}: {row.get('setup_type')} score={row.get('score')} status={row.get('status')} change={(row.get('lifecycle') or {}).get('change')}")
    new_rows = [row for row in proposals if str(row.get("status") or "") == "new" and not row.get("no_trade_reason")]
    if new_rows:
        lines.append("New candidates:")
        for row in new_rows[:4]:
            lines.append(f"- {row.get('ticker')}: {row.get('setup_type')} score={row.get('score')}")
    invalidated = [row for row in proposals if str(row.get("status") or "") in {"invalidated", "expired"}]
    if invalidated:
        lines.append("Invalidated/expired:")
        for row in invalidated[:4]:
            lines.append(f"- {row.get('ticker')}: {row.get('setup_type')} {row.get('no_trade_reason') or row.get('status')}")
    no_trade_improving = [row for row in improving if row.get("no_trade_reason")]
    if no_trade_improving:
        lines.append("No-trade but improving:")
        for row in no_trade_improving[:4]:
            lines.append(f"- {row.get('ticker')}: {row.get('setup_type')} score={row.get('score')} reason={row.get('no_trade_reason')}")
    if lines:
        return "\n".join(lines)
    for row in proposals[:8]:
        reason = str(row.get("no_trade_reason") or "").strip()
        suffix = f" ({reason})" if reason else ""
        lines.append(f"{row.get('ticker')}: {row.get('setup_type')} score={row.get('score')} status={row.get('status')}{suffix}")
    return "\n".join(lines)


def write_swing_study_artifacts(study: Dict[str, Any]) -> Dict[str, str]:
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(study.get("run_id") or "swing_study")).strip("_")
    out_dir = RUNS_ROOT / _local_day() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    study_path = out_dir / "swing_study.json"
    proposal_path = out_dir / "swing_proposals.json"
    outcome_path = out_dir / "swing_outcomes.json"
    provider_path = out_dir / "provider_health.json"
    catalyst_path = out_dir / "catalyst_records.json"
    event_path = out_dir / "event_impact.json"
    analog_path = out_dir / "event_analog_report.md"
    report_path = out_dir / "swing_study_report.md"
    _write_json(study_path, study)
    _write_json(proposal_path, {"proposals": list(study.get("proposals") or []), "best_proposal": dict(study.get("best_proposal") or {})})
    _write_json(outcome_path, dict(study.get("outcomes") or load_outcomes()))
    _write_json(provider_path, dict(study.get("provider_health") or {}))
    _write_json(catalyst_path, {"catalyst_records": [record for row in list(study.get("proposals") or []) for record in list((row or {}).get("catalyst_records") or [])]})
    event_payload = dict(study.get("event_impact") or {})
    if event_payload:
        _write_json(event_path, event_payload)
        analog_lines = ["# Apollo Swing Event Analog Report", ""]
        for row in list(event_payload.get("summaries") or []):
            top = dict((row or {}).get("top_event") or {})
            analogs = dict(top.get("historical_analogs") or {})
            analog_lines.extend(
                [
                    f"## {row.get('ticker')}",
                    "",
                    f"- event: `{top.get('title') or ''}`",
                    f"- impact_score: `{row.get('impact_score')}`",
                    f"- swing_score_delta: `{row.get('event_score_component')}`",
                ]
            )
            for window, payload in dict(analogs.get("windows") or {}).items():
                analog_lines.append(f"- {window}: samples `{payload.get('sample_count')}`, avg `{payload.get('avg_return_pct')}`, positive_rate `{payload.get('positive_rate_pct')}`")
            analog_lines.append("")
        analog_path.write_text("\n".join(analog_lines).strip() + "\n", encoding="utf-8")
    lines = [
        "# Apollo Swing Study",
        "",
        f"- created_at: `{study.get('created_at')}`",
        f"- run_id: `{study.get('run_id')}`",
        f"- high_confidence: `{study.get('high_confidence')}`",
        f"- market_regime: `{(study.get('market_regime') or {}).get('label')}`",
        "",
        "## Summary",
        "",
        str(study.get("summary") or ""),
        "",
        "## Source Health",
        "",
        f"- provider_health_count: `{len(((study.get('provider_health') or {}).get('tickers') or {}))}`",
        f"- catalyst_record_count: `{study.get('catalyst_record_count')}`",
        f"- event_impact_enabled: `{bool(event_payload)}`",
        "",
        "## Proposals",
    ]
    for row in list(study.get("proposals") or []):
        lines.extend(
            [
                "",
                f"### {row.get('ticker')}",
                f"- setup_type: `{row.get('setup_type')}`",
                f"- direction_bias: `{row.get('direction_bias')}`",
                f"- score: `{row.get('score')}`",
                f"- status: `{row.get('status')}`",
                f"- entry_zone: `{row.get('entry_zone')}`",
                f"- stop_zone: `{row.get('stop_zone')}`",
                f"- target_zone: `{row.get('target_zone')}`",
                f"- provider_confidence: `{row.get('provider_confidence')}`",
                f"- catalyst_quality_score: `{row.get('catalyst_quality_score')}`",
                f"- event_score_component: `{row.get('event_score_component')}`",
                f"- event_impact: `{((row.get('event_impact') or {}).get('directional_bias') or '')} {((row.get('event_impact') or {}).get('impact_score') or '')}`",
                f"- paper_order_candidate: `{bool(row.get('paper_order_candidate'))}`",
                f"- outcome_status: `{(row.get('outcome_status') or {}).get('status')}`",
                f"- no_trade_reason: `{row.get('no_trade_reason') or ''}`",
            ]
        )
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    latest_path = RUNS_ROOT / "latest_swing_study.json"
    latest_payload = {
        "study_path": str(study_path),
        "proposal_path": str(proposal_path),
        "outcome_path": str(outcome_path),
        "provider_path": str(provider_path),
        "catalyst_path": str(catalyst_path),
        "event_path": str(event_path) if event_payload else "",
        "analog_report_path": str(analog_path) if event_payload else "",
        "report_path": str(report_path),
        **study,
    }
    _write_json(latest_path, latest_payload)
    return {"study_path": str(study_path), "proposal_path": str(proposal_path), "outcome_path": str(outcome_path), "provider_path": str(provider_path), "catalyst_path": str(catalyst_path), "event_path": str(event_path) if event_payload else "", "analog_report_path": str(analog_path) if event_payload else "", "report_path": str(report_path), "latest_path": str(latest_path)}


def latest_swing_study() -> Dict[str, Any]:
    latest_path = RUNS_ROOT / "latest_swing_study.json"
    payload = _read_json(latest_path, {})
    if payload:
        return payload
    state = load_swing_state()
    return {"ok": True, "proposals": list(state.get("last_proposals") or []), "state": state, "run_id": state.get("last_run_id") or ""}


def swing_watchlist(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    status_filter = str(payload.get("status") or "").strip().lower()
    latest = latest_swing_study()
    rows = [row for row in list(latest.get("proposals") or []) if isinstance(row, dict)]
    if status_filter:
        rows = [row for row in rows if str(row.get("status") or "").lower() == status_filter]
    return {"ok": True, "run_id": latest.get("run_id") or "", "watchlist": rows, "state": load_swing_state()}


def swing_outcomes(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    limit = max(1, min(_safe_int(payload.get("limit") or 100), 1000))
    outcomes = load_outcomes()
    entries = [dict(row) for row in list(outcomes.get("entries") or []) if isinstance(row, dict)]
    ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
    if ticker:
        entries = [row for row in entries if str(row.get("ticker") or "") == ticker]
    return {"ok": True, "entries": entries[-limit:], "outcomes": outcomes}


def swing_provider_health(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    health = _read_json(Path(os.getenv("APOLLO_SWING_PROVIDER_HEALTH", str(PROVIDER_HEALTH_PATH))), {})
    ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
    if ticker:
        tickers = dict(health.get("tickers") or {})
        return {"ok": True, "provider_health": dict(tickers.get(ticker) or {}), "ticker": ticker}
    return {"ok": True, "provider_health": health}


def proposal_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
    action = str(payload.get("action") or "").strip().lower()
    if not ticker:
        return {"ok": False, "error": "ticker_required"}
    if action not in {"watching", "reject", "paper_trade", "paper_closed"}:
        return {"ok": False, "error": "invalid_action", "allowed": ["watching", "reject", "paper_trade", "paper_closed"]}
    state = load_swing_state()
    positions = dict(state.get("positions") or {})
    row = dict(positions.get(ticker) or {"ticker": ticker, "first_seen": _utc_iso()})
    if action == "watching":
        row["status"] = "watching"
    elif action == "reject":
        row["status"] = "rejected"
    elif action == "paper_closed":
        row["status"] = "paper_closed"
    else:
        row["status"] = "paper_open"
        try:
            focus_trading.submit_paper_trade(
                action="buy",
                ticker=ticker,
                thesis=str(payload.get("thesis") or "Apollo swing proposal"),
                confidence=min(1.0, max(0.0, _safe_float(payload.get("confidence")) / 100.0)),
                horizon="2_to_20_trading_days",
            )
        except Exception:
            pass
    row["last_decision_at"] = _utc_iso()
    row["note"] = str(payload.get("note") or row.get("note") or "")
    positions[ticker] = row
    state["positions"] = positions
    events = list(state.get("events") or [])
    events.append({"ts": _utc_iso(), "type": "proposal_decision", "ticker": ticker, "action": action, "note": row.get("note") or ""})
    state["events"] = events
    save_swing_state(state)
    outcomes = load_outcomes()
    entries = list(outcomes.get("entries") or [])
    entries.append({"key": f"decision:{ticker}:{_utc_iso()}", "ticker": ticker, "event": "proposal_decision", "action": action, "status": row.get("status"), "created_at": _utc_iso()})
    save_outcomes({"entries": entries})
    return {"ok": True, "ticker": ticker, "action": action, "record": row, "state": state}


def maybe_notify(study: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(study.get("notify_reason") or "")
    if not reason:
        return {"ok": True, "skipped": True, "reason": "routine_swing_update"}
    url = str(os.getenv("ARGUS_ALERTS_EXTERNAL_URL") or "http://127.0.0.1:5216/alerts/external").strip()
    token = str(os.getenv("SKY_LOCAL_TOKEN") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    best = dict(study.get("best_proposal") or {})
    payload = {
        "level": "critical" if reason == "high_confidence_swing_proposal" else "warn",
        "title": "Apollo swing study",
        "message": str(study.get("summary") or "")[:1800],
        "tags": ["chart_with_upwards_trend", "apollo", "swing"],
        "source": "apollo",
        "event": reason,
        "ticker": best.get("ticker"),
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        return {"ok": bool(resp.ok), "status_code": resp.status_code, "reason": reason}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}:{exc}", "reason": reason}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apollo swing study")
    parser.add_argument("cmd", choices=["run"], nargs="?", default="run")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--no-artifacts", action="store_true")
    args = parser.parse_args()
    payload: Dict[str, Any] = {"notify": bool(args.notify), "write_artifacts": not bool(args.no_artifacts)}
    if args.tickers:
        payload["tickers"] = args.tickers
    print(json.dumps(build_swing_study(payload), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "build_swing_study",
    "classify_setup",
    "latest_swing_study",
    "load_swing_state",
    "proposal_decision",
    "recompute_outcomes",
    "save_swing_state",
    "swing_outcomes",
    "swing_provider_health",
    "swing_watchlist",
]
