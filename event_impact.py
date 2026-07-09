from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from Apollo import market_data, swing_data


_APOLLO_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "event_impact"
DEFAULT_CACHE_ROOT = Path(os.getenv("APOLLO_EVENT_CACHE_ROOT", "D:/AI/apollo_market_event_engine"))
DEFAULT_MODEL_ID = os.getenv("APOLLO_EVENT_MODEL_ID", "D:/AI/models/finbert-financial-sentiment")
DEFAULT_TICKERS = tuple(
    t.strip().upper()
    for t in os.getenv(
        "APOLLO_WATCHLIST",
        "NVDA,AMD,AVGO,TSM,MSFT,AMZN,GOOGL,META,JPM,GS,BAC,XOM,CVX,LLY,UNH,JNJ,CAT,HON",
    ).split(",")
    if t.strip()
)
LOOKBACK_WINDOWS = (1, 5, 20, 60)

POSITIVE_RE = re.compile(
    r"\b(beat|beats|raise|raises|raised|growth|surge|jump|jumps|upgrade|record|strong|higher|approval|contract|launch|wins|expands|guidance up|buyback)\b",
    re.I,
)
NEGATIVE_RE = re.compile(
    r"\b(miss|misses|cut|cuts|lower|falls|drop|downgrade|probe|lawsuit|weak|warning|delay|recall|investigation|slows|halt|risk)\b",
    re.I,
)
STALE_RE = re.compile(r"\b(recap|preview|what to know|explainer|why .* moved|last week|last month)\b", re.I)
EVENT_TYPES = (
    ("earnings", re.compile(r"\b(earnings|revenue|eps|profit|quarter|q[1-4])\b", re.I)),
    ("guidance", re.compile(r"\b(guidance|forecast|outlook|raise|cut|warn)\b", re.I)),
    ("analyst", re.compile(r"\b(upgrade|downgrade|price target|rating|analyst)\b", re.I)),
    ("sec_filing", re.compile(r"\b(8-k|10-q|10-k|sec|filing)\b", re.I)),
    ("legal_regulatory", re.compile(r"\b(lawsuit|probe|investigation|regulator|ftc|doj|sec charges)\b", re.I)),
    ("product_contract", re.compile(r"\b(launch|contract|partnership|deal|order|ship|customer)\b", re.I)),
    ("macro_sector", re.compile(r"\b(fed|inflation|rates|tariff|export|china|sector|semiconductor|ai)\b", re.I)),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _safe_float(value: Any) -> float:
    try:
        num = float(value)
        return num if math.isfinite(num) else 0.0
    except Exception:
        return 0.0


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _tickers_from_payload(payload: Dict[str, Any]) -> List[str]:
    tickers = market_data.normalize_tickers(payload)
    return tickers[:24] or list(DEFAULT_TICKERS)


def _parse_published_at(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = parsedate_to_datetime(text)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def configure_cache_env() -> Dict[str, Any]:
    cache_root = Path(os.getenv("APOLLO_EVENT_CACHE_ROOT", str(DEFAULT_CACHE_ROOT)))
    hf_home = Path(os.getenv("HF_HOME", "D:/AI/hf_cache"))
    transformers_cache = Path(os.getenv("TRANSFORMERS_CACHE", str(hf_home / "transformers")))
    torch_home = Path(os.getenv("TORCH_HOME", "D:/AI/torch_cache"))
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(transformers_cache))
    os.environ.setdefault("TORCH_HOME", str(torch_home))
    for path in (cache_root, hf_home, transformers_cache, torch_home):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    paths = {
        "cache_root": str(cache_root),
        "hf_home": str(Path(os.environ.get("HF_HOME", str(hf_home)))),
        "transformers_cache": str(Path(os.environ.get("TRANSFORMERS_CACHE", str(transformers_cache)))),
        "torch_home": str(Path(os.environ.get("TORCH_HOME", str(torch_home)))),
    }
    paths["d_storage_only"] = all(str(value).replace("\\", "/").lower().startswith("d:/") for value in paths.values())
    return paths


def _event_type(text: str, kind: str) -> str:
    if "sec" in kind.lower():
        return "sec_filing"
    for label, pattern in EVENT_TYPES:
        if pattern.search(text):
            return label
    return "general_news"


def _heuristic_sentiment(text: str) -> Dict[str, Any]:
    positive = len(POSITIVE_RE.findall(text))
    negative = len(NEGATIVE_RE.findall(text))
    if positive > negative:
        label = "positive"
        score = min(0.95, 0.58 + (positive - negative) * 0.12)
    elif negative > positive:
        label = "negative"
        score = min(0.95, 0.58 + (negative - positive) * 0.12)
    elif positive and negative:
        label = "mixed"
        score = 0.56
    else:
        label = "neutral"
        score = 0.5
    return {"label": label, "score": round(score, 3), "mode": "heuristic"}


def _model_sentiment(text: str) -> Dict[str, Any]:
    if os.getenv("APOLLO_EVENT_MODEL_ENABLED", "0") != "1":
        return _heuristic_sentiment(text)
    model_id = str(os.getenv("APOLLO_EVENT_MODEL_ID", DEFAULT_MODEL_ID)).strip()
    model_path = Path(model_id) if model_id else Path("")
    if not model_id or (model_path.drive.upper() != "D:" and model_path.is_absolute()):
        row = _heuristic_sentiment(text)
        row["fallback_reason"] = "model_path_not_d_storage"
        return row
    if model_path.is_absolute() and not model_path.exists():
        row = _heuristic_sentiment(text)
        row["fallback_reason"] = "local_model_missing"
        return row
    try:
        configure_cache_env()
        from transformers import pipeline  # type: ignore

        classifier = pipeline("sentiment-analysis", model=model_id, tokenizer=model_id, local_files_only=model_path.is_absolute())
        result = (classifier(text[:1200]) or [{}])[0]
        label_raw = str(result.get("label") or "").lower()
        label = "positive" if "pos" in label_raw else ("negative" if "neg" in label_raw else "neutral")
        return {"label": label, "score": round(_safe_float(result.get("score")), 3), "mode": "model", "model_id": model_id}
    except Exception as exc:
        row = _heuristic_sentiment(text)
        row["fallback_reason"] = f"model_unavailable:{type(exc).__name__}"
        return row


def _source_quality(row: Dict[str, Any]) -> float:
    source = str(row.get("source") or row.get("kind") or "").lower()
    url = str(row.get("url") or "").lower()
    quality = 58.0
    if "sec" in source or "sec.gov" in url:
        quality = 88.0
    elif "google_news" in source:
        quality = 66.0
    elif source.startswith("cache:"):
        quality = 52.0
    if not row.get("url"):
        quality -= 10.0
    return max(0.0, min(100.0, quality))


def _ticker_relevance(ticker: str, text: str) -> float:
    ticker_norm = market_data.normalize_ticker(ticker)
    if not ticker_norm:
        return 35.0
    low = text.lower()
    if ticker_norm.lower() in low:
        return 94.0
    return 68.0


def _novelty_score(row: Dict[str, Any], seen_titles: set[str]) -> Dict[str, Any]:
    title = re.sub(r"\s+", " ", str(row.get("title") or "").strip().lower())
    duplicate = bool(title and title in seen_titles)
    if title:
        seen_titles.add(title)
    published = _parse_published_at(row.get("published_at"))
    age_hours: Optional[float] = None
    novelty = 72.0
    if duplicate:
        novelty -= 38.0
    if published:
        age_hours = max(0.0, (_utc_now() - published).total_seconds() / 3600.0)
        if age_hours <= 24:
            novelty += 12.0
        elif age_hours <= 72:
            novelty += 3.0
        elif age_hours <= 168:
            novelty -= 18.0
        else:
            novelty -= 38.0
    else:
        novelty -= 10.0
    if STALE_RE.search(title):
        novelty -= 18.0
    novelty = max(0.0, min(100.0, novelty))
    return {"score": round(novelty, 2), "duplicate": duplicate, "published_at": _utc_iso(published) if published else "", "age_hours": round(age_hours, 2) if age_hours is not None else None}


def _priced_in_risk(snapshot: Dict[str, Any], novelty: Dict[str, Any], sentiment: Dict[str, Any]) -> Dict[str, Any]:
    gap_abs = abs(_safe_float(snapshot.get("gap_pct")))
    rel_volume = _safe_float(snapshot.get("relative_volume"))
    risk = 18.0
    reasons: List[str] = []
    if _safe_float(novelty.get("score")) < 55:
        risk += 30.0
        reasons.append("event appears stale or duplicate")
    if gap_abs >= 5:
        risk += 18.0
        reasons.append("large price gap may already reflect headline")
    if rel_volume >= 1.8:
        risk += 8.0
        reasons.append("elevated volume suggests market is already repricing")
    if sentiment.get("label") == "neutral":
        risk += 8.0
        reasons.append("headline lacks directional evidence")
    risk = max(0.0, min(100.0, risk))
    return {"score": round(risk, 2), "label": "high" if risk >= 65 else ("medium" if risk >= 40 else "low"), "reasons": reasons}


def _return_pct(candles: List[Dict[str, Any]], idx: int, window: int) -> Optional[float]:
    if idx + window >= len(candles):
        return None
    start = _safe_float(candles[idx].get("close"))
    end = _safe_float(candles[idx + window].get("close"))
    if start <= 0 or end <= 0:
        return None
    return ((end - start) / start) * 100.0


def _historical_analogs(ticker: str, sentiment: str) -> Dict[str, Any]:
    try:
        provider = swing_data.fetch_provider_candles(ticker)
    except Exception as exc:
        return {"ok": False, "error": f"candles_unavailable:{type(exc).__name__}", "windows": {}}
    candles = [dict(row) for row in list(provider.get("candles") or []) if isinstance(row, dict)]
    if len(candles) < 80:
        return {"ok": False, "error": "insufficient_candles", "windows": {}, "provider_health": provider.get("provider_health") or {}}
    signed_positive = sentiment != "negative"
    event_indices: List[int] = []
    volumes = [_safe_float(row.get("volume")) for row in candles]
    for idx in range(1, len(candles) - max(LOOKBACK_WINDOWS)):
        prev = _safe_float(candles[idx - 1].get("close"))
        cur = _safe_float(candles[idx].get("close"))
        if prev <= 0 or cur <= 0:
            continue
        day_ret = ((cur - prev) / prev) * 100.0
        avg_volume = sum(volumes[max(0, idx - 20) : idx] or [0.0]) / max(1, len(volumes[max(0, idx - 20) : idx]))
        vol_ratio = (_safe_float(candles[idx].get("volume")) / avg_volume) if avg_volume > 0 else 0.0
        event_like = abs(day_ret) >= 2.0 or vol_ratio >= 1.45
        if not event_like:
            continue
        if signed_positive and day_ret >= -0.25:
            event_indices.append(idx)
        elif not signed_positive and day_ret <= 0.25:
            event_indices.append(idx)
    event_indices = event_indices[-80:]
    windows: Dict[str, Any] = {}
    for window in LOOKBACK_WINDOWS:
        returns = [ret for idx in event_indices if (ret := _return_pct(candles, idx, window)) is not None]
        if not returns:
            windows[f"{window}d"] = {"sample_count": 0, "available": False}
            continue
        positive_rate = sum(1 for value in returns if value > 0) / len(returns) * 100.0
        windows[f"{window}d"] = {
            "available": True,
            "sample_count": len(returns),
            "avg_return_pct": round(sum(returns) / len(returns), 3),
            "median_return_pct": round(median(returns), 3),
            "positive_rate_pct": round(positive_rate, 2),
            "best_return_pct": round(max(returns), 3),
            "worst_return_pct": round(min(returns), 3),
        }
    return {"ok": bool(event_indices), "sample_count": len(event_indices), "windows": windows, "provider_health": provider.get("provider_health") or {}}


def _analog_direction_score(analogs: Dict[str, Any], sentiment: str) -> float:
    window = dict((analogs.get("windows") or {}).get("5d") or {})
    if not window.get("available"):
        return 50.0
    positive_rate = _safe_float(window.get("positive_rate_pct"))
    avg_ret = _safe_float(window.get("avg_return_pct"))
    if sentiment == "negative":
        positive_rate = 100.0 - positive_rate
        avg_ret = -avg_ret
    return max(0.0, min(100.0, (positive_rate * 0.65) + (50.0 + max(-20.0, min(20.0, avg_ret * 8.0))) * 0.35))


def classify_event(ticker: str, row: Dict[str, Any], snapshot: Dict[str, Any], seen_titles: set[str]) -> Dict[str, Any]:
    title = str(row.get("title") or "").strip()
    text = f"{title} {row.get('source') or ''} {row.get('kind') or ''}"
    sentiment = _model_sentiment(text)
    novelty = _novelty_score(row, seen_titles)
    source_quality = _source_quality(row)
    relevance = _ticker_relevance(ticker, text)
    event_type = _event_type(text, str(row.get("kind") or row.get("source") or ""))
    priced_in = _priced_in_risk(snapshot, novelty, sentiment)
    analogs = _historical_analogs(ticker, str(sentiment.get("label") or "neutral"))
    analog_score = _analog_direction_score(analogs, str(sentiment.get("label") or "neutral"))
    # Impact is magnitude, not long-only bullishness. A strong negative catalyst
    # should rank as high-impact and then flow through as short_bias research.
    sentiment_score = {"positive": 82.0, "negative": 82.0, "mixed": 55.0, "neutral": 50.0}.get(str(sentiment.get("label")), 50.0)
    impact_score = (
        sentiment_score * 0.24
        + _safe_float(novelty.get("score")) * 0.18
        + source_quality * 0.16
        + relevance * 0.14
        + analog_score * 0.20
        - _safe_float(priced_in.get("score")) * 0.12
    )
    impact_score = max(0.0, min(100.0, impact_score))
    label = str(sentiment.get("label") or "neutral")
    if _safe_float(novelty.get("score")) < _safe_float(os.getenv("APOLLO_EVENT_MIN_NOVELTY", "55")):
        swing_delta = 0.0
    elif label == "positive":
        swing_delta = max(0.0, min(8.0, (impact_score - 58.0) * 0.18))
    elif label == "negative":
        swing_delta = -max(3.0, min(15.0, (impact_score - 35.0) * 0.22))
    else:
        swing_delta = 0.0
    return {
        "id": re.sub(r"[^a-z0-9]+", "_", f"{ticker}_{title}".lower()).strip("_")[:96],
        "ticker": market_data.normalize_ticker(ticker),
        "title": title,
        "url": str(row.get("url") or ""),
        "source": str(row.get("source") or row.get("kind") or ""),
        "kind": str(row.get("kind") or ""),
        "published_at": novelty.get("published_at") or str(row.get("published_at") or ""),
        "event_type": event_type,
        "sentiment": sentiment,
        "novelty": novelty,
        "source_quality": round(source_quality, 2),
        "ticker_relevance": round(relevance, 2),
        "priced_in_risk": priced_in,
        "historical_analogs": analogs,
        "impact_score": round(impact_score, 2),
        "directional_bias": label,
        "swing_score_delta": round(swing_delta, 2),
        "confidence_reason": _confidence_reason(label, novelty, source_quality, priced_in, analogs),
        "created_at": _utc_iso(),
    }


def _confidence_reason(label: str, novelty: Dict[str, Any], source_quality: float, priced_in: Dict[str, Any], analogs: Dict[str, Any]) -> str:
    parts = [f"{label} event", f"novelty {novelty.get('score')}", f"source {round(source_quality, 1)}"]
    if priced_in.get("label") != "low":
        parts.append(f"priced-in risk {priced_in.get('label')}")
    if analogs.get("ok"):
        parts.append(f"{analogs.get('sample_count')} analogs")
    else:
        parts.append(str(analogs.get("error") or "no analogs"))
    return "; ".join(parts)


def _ticker_summary(ticker: str, events: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if not events:
        return {
            "ticker": ticker,
            "ok": False,
            "impact_score": 0.0,
            "event_score_component": 0.0,
            "directional_bias": "none",
            "top_event": {},
            "paper_order_candidate": None,
            "reason": "no catalysts/events found",
        }
    top = sorted(events, key=lambda row: float(row.get("impact_score") or 0.0), reverse=True)[0]
    min_source = _safe_float(os.getenv("APOLLO_EVENT_MIN_SOURCE_CONFIDENCE", "60"))
    min_novelty = _safe_float(os.getenv("APOLLO_EVENT_MIN_NOVELTY", "55"))
    ready = bool(_safe_float(top.get("source_quality")) >= min_source and _safe_float((top.get("novelty") or {}).get("score")) >= min_novelty)
    paper_candidate = None
    min_dislocation_impact = _safe_float(os.getenv("APOLLO_EVENT_DISLOCATION_MIN_IMPACT", "62"))
    if (
        os.getenv("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES", "0") == "1"
        and ready
        and top.get("directional_bias") in {"positive", "negative"}
        and _safe_float(top.get("impact_score")) >= min_dislocation_impact
    ):
        side = "long" if top.get("directional_bias") == "positive" else "short"
        paper_candidate = {
            "ticker": ticker,
            "candidate_type": "event_dislocation_paper_prep",
            "setup_type": "event_dislocation",
            "direction_bias": "long_bias" if side == "long" else "short_bias",
            "side": side,
            "requires_swing_confirmation": False,
            "human_approval_required": True,
            "live_trade_execution": False,
            "broker_order_created": False,
            "reason": top.get("confidence_reason") or "",
            "event_title": top.get("title") or "",
            "event_type": top.get("event_type") or "",
            "impact_score": top.get("impact_score"),
        }
    return {
        "ticker": ticker,
        "ok": ready,
        "impact_score": top.get("impact_score"),
        "event_score_component": top.get("swing_score_delta"),
        "directional_bias": top.get("directional_bias"),
        "top_event": top,
        "event_count": len(events),
        "paper_order_candidate": paper_candidate,
        "snapshot": snapshot,
    }


def build_event_impact(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    configure_cache_env()
    tickers = _tickers_from_payload(payload)
    refresh = bool(payload.get("refresh", True))
    max_results = max(1, min(int(payload.get("max_results") or 6), 12))
    snapshot_payload = market_data.market_snapshot({"tickers": tickers, "refresh": refresh})
    catalysts_payload = market_data.market_catalysts({"tickers": tickers, "max_results": max_results})
    snapshots = {str(row.get("ticker") or ""): row for row in list(snapshot_payload.get("snapshots") or []) if isinstance(row, dict)}
    catalysts_by_ticker = dict(catalysts_payload.get("catalysts") or {})
    seen_titles: set[str] = set()
    events_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    summaries: List[Dict[str, Any]] = []
    for ticker in tickers:
        snapshot = dict(snapshots.get(ticker) or {"ticker": ticker, "ok": False})
        rows = [dict(row) for row in list(catalysts_by_ticker.get(ticker) or []) if isinstance(row, dict)]
        events = [classify_event(ticker, row, snapshot, seen_titles) for row in rows]
        events.sort(key=lambda row: float(row.get("impact_score") or 0.0), reverse=True)
        events_by_ticker[ticker] = events[:max_results]
        summaries.append(_ticker_summary(ticker, events[:max_results], snapshot))
    summaries.sort(key=lambda row: float(row.get("impact_score") or 0.0), reverse=True)
    best = summaries[0] if summaries else {}
    result = {
        "ok": bool(summaries),
        "run_id": str(payload.get("run_id") or f"apollo_event_impact_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        "created_at": _utc_iso(),
        "tickers": tickers,
        "source_health": {"snapshot": snapshot_payload.get("source_health") or {}, "catalysts": catalysts_payload.get("source_health") or {}},
        "events_by_ticker": events_by_ticker,
        "summaries": summaries,
        "best_event_impact": best,
        "high_confidence": bool(best and best.get("ok") and _safe_float(best.get("impact_score")) >= 78.0),
        "broker_readiness": {
            "broker_prep_enabled": True,
            "paper_order_candidates_enabled": os.getenv("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES", "0") == "1",
            "human_approval_required": True,
            "live_trade_execution": False,
            "broker_order_created": False,
        },
        "model_health": event_impact_health().get("model") or {},
        "summary": _format_summary(summaries),
    }
    if bool(payload.get("write_artifacts", True)):
        result["artifacts"] = write_event_impact_artifacts(result)
    return result


def _format_summary(summaries: List[Dict[str, Any]]) -> str:
    if not summaries:
        return "Apollo event impact found no usable event evidence."
    lines = []
    for row in summaries[:6]:
        top = dict(row.get("top_event") or {})
        title = str(top.get("title") or row.get("reason") or "")[:90]
        lines.append(f"{row.get('ticker')}: {row.get('directional_bias')} impact={row.get('impact_score')} delta={row.get('event_score_component')} - {title}")
    return "\n".join(lines)


def _analog_markdown(event: Dict[str, Any]) -> List[str]:
    analogs = dict(event.get("historical_analogs") or {})
    lines = [f"### {event.get('ticker')} - {event.get('event_type')}", "", f"- headline: `{event.get('title')}`", f"- impact_score: `{event.get('impact_score')}`", f"- confidence_reason: `{event.get('confidence_reason')}`"]
    for window, row in dict(analogs.get("windows") or {}).items():
        lines.append(f"- {window}: samples `{row.get('sample_count')}`, avg `{row.get('avg_return_pct')}`, positive_rate `{row.get('positive_rate_pct')}`")
    return lines


def write_event_impact_artifacts(result: Dict[str, Any]) -> Dict[str, str]:
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(result.get("run_id") or "event_impact")).strip("_")
    out_dir = RUNS_ROOT / _local_day() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    event_path = out_dir / "event_impact.json"
    analog_path = out_dir / "event_analog_report.md"
    report_path = out_dir / "event_impact_report.md"
    latest_path = RUNS_ROOT / "latest_event_impact.json"
    _write_json(event_path, result)
    lines = ["# Apollo Event Impact", "", f"- created_at: `{result.get('created_at')}`", f"- run_id: `{result.get('run_id')}`", f"- high_confidence: `{result.get('high_confidence')}`", "", "## Summary", "", str(result.get("summary") or "")]
    for row in list(result.get("summaries") or []):
        top = dict(row.get("top_event") or {})
        lines.extend(["", f"### {row.get('ticker')}", f"- directional_bias: `{row.get('directional_bias')}`", f"- impact_score: `{row.get('impact_score')}`", f"- swing_score_delta: `{row.get('event_score_component')}`", f"- paper_order_candidate: `{bool(row.get('paper_order_candidate'))}`", f"- top_event: `{top.get('title') or ''}`"])
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    analog_lines = ["# Apollo Event Analog Report", ""]
    for row in list(result.get("summaries") or []):
        top = dict(row.get("top_event") or {})
        if top:
            analog_lines.extend(_analog_markdown(top))
            analog_lines.append("")
    analog_path.write_text("\n".join(analog_lines).strip() + "\n", encoding="utf-8")
    latest_payload = {"event_path": str(event_path), "report_path": str(report_path), "analog_report_path": str(analog_path), **result}
    _write_json(latest_path, latest_payload)
    return {"event_path": str(event_path), "report_path": str(report_path), "analog_report_path": str(analog_path), "latest_path": str(latest_path)}


def latest_event_impact() -> Dict[str, Any]:
    return _read_json(RUNS_ROOT / "latest_event_impact.json", {"ok": True, "summaries": [], "events_by_ticker": {}})


def event_watchlist(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    latest = latest_event_impact()
    ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
    rows = [dict(row) for row in list(latest.get("summaries") or []) if isinstance(row, dict)]
    if ticker:
        rows = [row for row in rows if str(row.get("ticker") or "") == ticker]
    return {"ok": True, "run_id": latest.get("run_id") or "", "watchlist": rows}


def event_impact_health() -> Dict[str, Any]:
    paths = configure_cache_env()
    model_id = str(os.getenv("APOLLO_EVENT_MODEL_ID", DEFAULT_MODEL_ID)).strip()
    model_path = Path(model_id) if model_id else Path("")
    model_enabled = os.getenv("APOLLO_EVENT_MODEL_ENABLED", "0") == "1"
    model_ready = bool(model_enabled and model_id and (not model_path.is_absolute() or model_path.exists()) and (not model_path.is_absolute() or model_path.drive.upper() == "D:"))
    return {
        "ok": True,
        "enabled": os.getenv("APOLLO_EVENT_IMPACT_ENABLED", "0") == "1",
        "cache": paths,
        "model": {
            "enabled": model_enabled,
            "model_id": model_id,
            "runtime_loaded": False,
            "ready": model_ready,
            "mode": "model_if_local_available_else_heuristic",
            "last_error": "" if model_ready or not model_enabled else "model not present on D storage or transformers unavailable until first run",
        },
        "broker_readiness": {"human_approval_required": True, "live_trade_execution": False, "broker_order_created": False},
    }


def event_impact_history(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    limit = max(1, min(int(payload.get("limit") or 20), 100))
    rows: List[Dict[str, Any]] = []
    try:
        paths = sorted(RUNS_ROOT.glob("*/*/event_impact.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        for path in paths:
            data = _read_json(path, {})
            rows.append(
                {
                    "run_id": str(data.get("run_id") or path.parent.name),
                    "created_at": str(data.get("created_at") or ""),
                    "high_confidence": bool(data.get("high_confidence")),
                    "best_ticker": str((data.get("best_event_impact") or {}).get("ticker") or ""),
                    "summary_count": len(list(data.get("summaries") or [])),
                    "path": str(path),
                }
            )
    except Exception:
        rows = []
    return {"ok": True, "runs": rows}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Apollo news event impact engine")
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("--tickers", "--ticker", dest="tickers", default="")
    run.add_argument("--max-results", type=int, default=6)
    run.add_argument("--no-write", action="store_true")
    sub.add_parser("health")
    args = parser.parse_args()
    if args.cmd == "health":
        print(json.dumps(event_impact_health(), indent=2))
    else:
        print(json.dumps(build_event_impact({"tickers": args.tickers, "max_results": args.max_results, "write_artifacts": not args.no_write}), indent=2))


if __name__ == "__main__":
    _main()


__all__ = [
    "build_event_impact",
    "classify_event",
    "configure_cache_env",
    "event_impact_health",
    "event_impact_history",
    "event_watchlist",
    "latest_event_impact",
    "write_event_impact_artifacts",
]
