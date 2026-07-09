from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from Apollo import event_impact, market_data, swing_study


_APOLLO_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "market_forecast_turn"
DEFAULT_TICKERS = ("SPY", "QQQ", "NVDA", "AMD", "AVGO", "MSFT", "AMZN")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
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


def _parse_dt(value: Any, default: Optional[datetime] = None) -> datetime:
    text = str(value or "").strip()
    if not text:
        return default or _utc_now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0)
    except Exception:
        return default or _utc_now()


def _tickers_from_payload(payload: Dict[str, Any]) -> List[str]:
    tickers = market_data.normalize_tickers(payload)
    return tickers[:24] or list(DEFAULT_TICKERS)


def next_trading_session(requested: date) -> date:
    target = requested
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def _direction_value(label: Any) -> float:
    text = str(label or "").lower()
    if text in {"positive", "long_bias", "risk_on"}:
        return 1.0
    if text in {"negative", "short_bias", "risk_off"}:
        return -1.0
    if text in {"mixed"}:
        return -0.25
    return 0.0


def _event_by_ticker(event_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("ticker") or ""): dict(row)
        for row in list(event_result.get("summaries") or [])
        if isinstance(row, dict)
    }


def _proposal_by_ticker(swing_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("ticker") or ""): dict(row)
        for row in list(swing_result.get("proposals") or [])
        if isinstance(row, dict)
    }


def _build_forecast(tickers: List[str], event_result: Dict[str, Any], swing_result: Dict[str, Any]) -> Dict[str, Any]:
    events = _event_by_ticker(event_result)
    proposals = _proposal_by_ticker(swing_result)
    ticker_rows: List[Dict[str, Any]] = []
    aggregate = 0.0
    broad = 0.0
    broad_count = 0
    for ticker in tickers:
        event_row = dict(events.get(ticker) or {})
        proposal = dict(proposals.get(ticker) or {})
        event_score = _safe_float(event_row.get("impact_score"))
        event_component = _safe_float(event_row.get("event_score_component"))
        event_direction = _direction_value(event_row.get("directional_bias"))
        swing_score = _safe_float(proposal.get("score"))
        swing_direction = _direction_value(proposal.get("direction_bias"))
        no_trade = bool(proposal.get("no_trade_reason"))
        expired = str(proposal.get("status") or "").lower() == "expired"
        ticker_signal = (event_direction * min(event_score, 100.0) * 0.35) + (swing_direction * swing_score * 0.25) + event_component
        if no_trade or expired:
            ticker_signal *= 0.45
        aggregate += ticker_signal
        if ticker in {"SPY", "QQQ"}:
            broad += ticker_signal
            broad_count += 1
        ticker_rows.append(
            {
                "ticker": ticker,
                "signal_score": round(ticker_signal, 2),
                "event_impact": event_row,
                "swing_proposal": proposal,
                "watch_state": _watch_state(proposal, event_row),
            }
        )
    aggregate = aggregate / max(1, len(tickers))
    broad_score = broad / max(1, broad_count)
    if aggregate >= 18 and broad_score >= 5:
        bias = "risk_on"
    elif aggregate >= 6:
        bias = "cautious_positive"
    elif aggregate <= -18 or broad_score <= -12:
        bias = "risk_off"
    elif aggregate <= -6:
        bias = "cautious_negative"
    else:
        bias = "mixed_flat"
    confidence = "high" if abs(aggregate) >= 24 and abs(broad_score) >= 12 else ("medium" if abs(aggregate) >= 10 else "low")
    candidates = [
        row
        for row in ticker_rows
        if row.get("watch_state") in {"action_candidate", "watch_candidate"}
    ]
    candidates.sort(key=lambda row: float(row.get("signal_score") or 0.0), reverse=True)
    return {
        "overall_bias": bias,
        "confidence": confidence,
        "aggregate_signal_score": round(aggregate, 2),
        "broad_market_signal_score": round(broad_score, 2),
        "watch_candidates": candidates[:8],
        "ticker_rows": ticker_rows,
        "summary": _forecast_summary(bias, confidence, candidates, ticker_rows),
    }


def _watch_state(proposal: Dict[str, Any], event_row: Dict[str, Any]) -> str:
    score = _safe_float(proposal.get("score"))
    no_trade = bool(proposal.get("no_trade_reason"))
    expired = str(proposal.get("status") or "").lower() == "expired"
    event_positive = str(event_row.get("directional_bias") or "").lower() == "positive"
    event_score = _safe_float(event_row.get("impact_score"))
    if no_trade:
        return "no_trade"
    if expired:
        return "stale_watch"
    if score >= 82 and event_positive and event_score >= 70:
        return "action_candidate"
    if score >= 70 or event_score >= 65:
        return "watch_candidate"
    return "monitor"


def _forecast_summary(bias: str, confidence: str, candidates: List[Dict[str, Any]], ticker_rows: List[Dict[str, Any]]) -> str:
    lines = [f"Apollo next-session estimate: {bias} ({confidence} confidence)."]
    if candidates:
        tickers = ", ".join(str(row.get("ticker") or "") for row in candidates[:4])
        lines.append(f"Watch candidates: {tickers}.")
    else:
        lines.append("No action candidates; monitor news and re-score before the next open.")
    stale = [str(row.get("ticker") or "") for row in ticker_rows if row.get("watch_state") == "stale_watch"]
    if stale:
        lines.append(f"Stale/expired lifecycle state present for: {', '.join(stale[:6])}.")
    return " ".join(lines)


def _run_ocr97_documents(payload: Dict[str, Any]) -> Dict[str, Any]:
    docs = payload.get("ocr_documents") or payload.get("documents") or []
    if isinstance(docs, str):
        docs = [docs]
    docs = [str(item).strip() for item in list(docs or []) if str(item).strip()]
    if not docs:
        return {
            "used": False,
            "mode": "skipped_no_documents",
            "reason": "News/RSS text does not require OCR97; provide ocr_documents for PDFs or images.",
            "documents": [],
        }
    if os.getenv("APOLLO_MARKET_FORECAST_OCR97_ENABLED", "0") != "1":
        return {"used": False, "mode": "disabled", "reason": "Set APOLLO_MARKET_FORECAST_OCR97_ENABLED=1 to OCR supplied documents.", "documents": docs}
    rows: List[Dict[str, Any]] = []
    try:
        from common.mcp import ensure_loaded, run
    except Exception as exc:
        return {"used": False, "mode": "unavailable", "reason": f"mcp_unavailable:{type(exc).__name__}", "documents": docs}
    ensure_loaded()
    for raw in docs[:6]:
        path = Path(raw)
        if not path.exists() or not path.is_file():
            rows.append({"path": raw, "ok": False, "error": "file_not_found"})
            continue
        try:
            result = dict(
                run(
                    "ocr.dual",
                    {
                        "path": str(path),
                        "goal": "Extract market-moving facts, earnings guidance, macro data, chart labels, and risk warnings.",
                        "engine": "gb10_auto",
                        "route_mode": "quality_first",
                        "max_pages": int(payload.get("ocr_max_pages") or 4),
                        "max_chars": int(payload.get("ocr_max_chars") or 12000),
                        "consensus": True,
                        "use_gateway": True,
                    },
                )
                or {}
            )
            rows.append({"path": str(path), "ok": bool(result.get("ok")), "engine": result.get("engine"), "quality": result.get("quality") or {}, "text_preview": str(result.get("markdown") or result.get("text") or "")[:1200], "error": result.get("error") or ""})
        except Exception as exc:
            rows.append({"path": str(path), "ok": False, "error": f"{type(exc).__name__}:{exc}"})
    return {"used": True, "mode": "ocr97", "documents": rows}


def _simulation(as_of: datetime, requested_turn_at: datetime, requested_date: date, session_date: date, forecast: Dict[str, Any]) -> Dict[str, Any]:
    weekend_gap = requested_date != session_date
    if weekend_gap:
        status = "weekend_monitoring"
        message = "No normal U.S. equity session on the requested date; carry forward event watch and re-score before next open."
    else:
        status = "next_session_watch"
        message = "Re-score before the next open with fresh candles, provider health, and overnight headlines."
    return {
        "as_of": _utc_iso(as_of),
        "simulated_turn_at": _utc_iso(requested_turn_at),
        "requested_date": requested_date.isoformat(),
        "next_trading_session": session_date.isoformat(),
        "status": status,
        "message": message,
        "expected_apollo_turn": {
            "market_action": "no_broker_action",
            "live_trade_execution": False,
            "broker_order_created": False,
            "forecast_bias": forecast.get("overall_bias"),
            "confidence": forecast.get("confidence"),
            "operator_note": forecast.get("summary"),
        },
    }


def build_market_forecast_turn(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    tickers = _tickers_from_payload(payload)
    as_of = _parse_dt(payload.get("as_of"))
    requested_turn_at = _parse_dt(payload.get("simulate_at"), as_of + timedelta(days=int(payload.get("days_ahead") or 1)))
    requested_date = requested_turn_at.date()
    session_date = next_trading_session(requested_date)
    ocr97 = _run_ocr97_documents(payload)
    event_result = event_impact.build_event_impact(
        {
            "tickers": tickers,
            "max_results": payload.get("max_results", 4),
            "refresh": payload.get("refresh", True),
            "write_artifacts": bool(payload.get("write_child_artifacts", False)),
        }
    )
    swing_result = swing_study.build_swing_study(
        {
            "tickers": tickers,
            "include_event_impact": True,
            "max_results": payload.get("max_results", 4),
            "event_max_results": payload.get("max_results", 4),
            "refresh": payload.get("refresh", True),
            "write_artifacts": bool(payload.get("write_child_artifacts", False)),
        }
    )
    forecast = _build_forecast(tickers, event_result, swing_result)
    result = {
        "ok": True,
        "run_id": str(payload.get("run_id") or f"apollo_market_forecast_turn_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        "created_at": _utc_iso(),
        "tickers": tickers,
        "as_of": _utc_iso(as_of),
        "requested_turn_at": _utc_iso(requested_turn_at),
        "requested_date": requested_date.isoformat(),
        "next_trading_session": session_date.isoformat(),
        "session_adjustment": "weekend_to_next_trading_session" if requested_date != session_date else "none",
        "ocr97": ocr97,
        "event_impact": event_result,
        "swing_study": swing_result,
        "forecast": forecast,
        "simulation": _simulation(as_of, requested_turn_at, requested_date, session_date, forecast),
        "broker_readiness": {
            "human_approval_required": True,
            "live_trade_execution": False,
            "broker_order_created": False,
        },
    }
    if bool(payload.get("write_artifacts", True)):
        result["artifacts"] = write_market_forecast_artifacts(result)
    return result


def write_market_forecast_artifacts(result: Dict[str, Any]) -> Dict[str, str]:
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(result.get("run_id") or "market_forecast_turn")).strip("_")
    out_dir = RUNS_ROOT / _local_day() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "market_forecast_turn.json"
    report_path = out_dir / "market_forecast_turn_report.md"
    latest_path = RUNS_ROOT / "latest_market_forecast_turn.json"
    _write_json(result_path, result)
    forecast = dict(result.get("forecast") or {})
    simulation = dict(result.get("simulation") or {})
    lines = [
        "# Apollo Market Forecast Turn",
        "",
        f"- created_at: `{result.get('created_at')}`",
        f"- requested_date: `{result.get('requested_date')}`",
        f"- next_trading_session: `{result.get('next_trading_session')}`",
        f"- session_adjustment: `{result.get('session_adjustment')}`",
        f"- overall_bias: `{forecast.get('overall_bias')}`",
        f"- confidence: `{forecast.get('confidence')}`",
        f"- live_trade_execution: `{(result.get('broker_readiness') or {}).get('live_trade_execution')}`",
        "",
        "## Summary",
        "",
        str(forecast.get("summary") or ""),
        "",
        "## Simulated Future Turn",
        "",
        str(simulation.get("message") or ""),
    ]
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    _write_json(latest_path, {"result_path": str(result_path), "report_path": str(report_path), **result})
    return {"result_path": str(result_path), "report_path": str(report_path), "latest_path": str(latest_path)}


def latest_market_forecast_turn() -> Dict[str, Any]:
    return _read_json(RUNS_ROOT / "latest_market_forecast_turn.json", {"ok": True, "forecast": {}, "simulation": {}})


def market_forecast_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "enabled": os.getenv("APOLLO_MARKET_FORECAST_ENABLED", "1") == "1",
        "event_impact": event_impact.event_impact_health(),
        "ocr97": {
            "enabled_for_documents": os.getenv("APOLLO_MARKET_FORECAST_OCR97_ENABLED", "0") == "1",
            "mode": "only_when_ocr_documents_are_supplied",
        },
        "broker_readiness": {"human_approval_required": True, "live_trade_execution": False, "broker_order_created": False},
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Apollo next-session market forecast turn")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--as-of", default="")
    parser.add_argument("--simulate-at", default="")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_market_forecast_turn(
                {
                    "tickers": args.tickers,
                    "as_of": args.as_of,
                    "simulate_at": args.simulate_at,
                    "write_artifacts": not args.no_write,
                }
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    _main()


__all__ = [
    "build_market_forecast_turn",
    "latest_market_forecast_turn",
    "market_forecast_health",
    "next_trading_session",
    "write_market_forecast_artifacts",
]
