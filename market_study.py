from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from Apollo import market_data


_APOLLO_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "market_study"
DEFAULT_TICKERS = ("NVDA", "AMD", "AVGO")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


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
    if tickers:
        return tickers
    focus = payload.get("focus_universe")
    if isinstance(focus, dict):
        rows = list(focus.get("selected_tickers") or [])
        tickers = []
        for row in rows:
            if isinstance(row, dict):
                tickers.append(market_data.normalize_ticker(str(row.get("ticker") or "")))
            else:
                tickers.append(market_data.normalize_ticker(str(row)))
        tickers = [ticker for ticker in tickers if ticker]
    return tickers[:12] or list(DEFAULT_TICKERS)


def _load_latest_focus_tickers() -> List[str]:
    candidates = [
        _APOLLO_ROOT / "logs" / "nightly" / "background" / "focus_trading_state.json",
        _APOLLO_ROOT / "logs" / "nightly" / "latest_status.json",
    ]
    for path in candidates:
        data = _read_json(path, {})
        focus = dict(data.get("focus") or data.get("focus_universe") or {})
        tickers = [market_data.normalize_ticker(str(item)) for item in list(focus.get("selected_tickers") or [])]
        tickers = [ticker for ticker in tickers if ticker]
        if tickers:
            return tickers[:12]
        stage_focus = dict((data.get("stage_details") or {}).get("focus_universe") or {})
        tickers = [market_data.normalize_ticker(str(item)) for item in list(stage_focus.get("selected_tickers") or [])]
        tickers = [ticker for ticker in tickers if ticker]
        if tickers:
            return tickers[:12]
    return list(DEFAULT_TICKERS)


def _headline_bias(catalysts: List[Dict[str, Any]]) -> float:
    positive = re.compile(r"\b(beat|beats|raise|raises|raised|growth|surge|jumps|upgrade|record|strong|higher)\b", re.I)
    negative = re.compile(r"\b(miss|misses|cut|cuts|lower|falls|drop|downgrade|probe|lawsuit|weak|warning)\b", re.I)
    score = 0.0
    for row in catalysts:
        title = str(row.get("title") or "")
        if positive.search(title):
            score += 1.0
        if negative.search(title):
            score -= 1.0
    return score


def _dislocation_profile(snapshot: Dict[str, Any], catalysts: List[Dict[str, Any]], headline_bias: float) -> Dict[str, Any]:
    gap = _safe_float(snapshot.get("gap_pct"))
    rel_volume = _safe_float(snapshot.get("relative_volume"))
    catalyst_count = len(catalysts)
    fresh = snapshot.get("freshness") == "fresh"
    magnitude = min(abs(gap) * 4.0, 28.0)
    if rel_volume:
        magnitude += min(max(rel_volume - 1.0, 0.0) * 12.0, 22.0)
    magnitude += min(catalyst_count * 5.0, 25.0)
    magnitude += min(abs(headline_bias) * 8.0, 20.0)
    if not fresh:
        magnitude -= 20.0
    score = max(0.0, min(100.0, magnitude))
    if gap > 0.5 or headline_bias > 0:
        direction = "long_bias"
    elif gap < -0.5 or headline_bias < 0:
        direction = "short_bias"
    else:
        direction = "watch_only"
    return {
        "is_event_dislocation": bool(fresh and score >= _safe_float(os.getenv("APOLLO_MARKET_DISLOCATION_MIN_SCORE", "45")) and direction in {"long_bias", "short_bias"}),
        "score": round(score, 2),
        "direction_bias": direction,
        "gap_pct": gap,
        "relative_volume": rel_volume,
        "catalyst_count": catalyst_count,
        "headline_bias": headline_bias,
    }


def _proposal_for_ticker(ticker: str, snapshot: Dict[str, Any], catalysts: List[Dict[str, Any]]) -> Dict[str, Any]:
    gap = _safe_float(snapshot.get("gap_pct"))
    confidence = _safe_float(snapshot.get("confidence"))
    catalyst_count = len(catalysts)
    catalyst_score = min(25.0, catalyst_count * 5.0)
    volume_bonus = 0.0
    rel_volume = snapshot.get("relative_volume")
    if rel_volume is not None:
        volume_bonus = min(12.0, max(0.0, (_safe_float(rel_volume) - 1.0) * 8.0))
    freshness_penalty = 0.0 if snapshot.get("freshness") == "fresh" else 12.0
    score = max(0.0, min(100.0, confidence * 0.55 + catalyst_score + min(abs(gap) * 2.0, 12.0) + volume_bonus - freshness_penalty))
    headline_bias = _headline_bias(catalysts)
    dislocation = _dislocation_profile(snapshot, catalysts, headline_bias)
    if score < 60.0 and not dislocation.get("is_event_dislocation"):
        direction = "no_trade"
        no_trade_reason = "insufficient fresh market/catalyst confidence"
    elif dislocation.get("direction_bias") == "long_bias" or gap > 0.5 or headline_bias > 0:
        direction = "long_bias"
        no_trade_reason = ""
    elif dislocation.get("direction_bias") == "short_bias" or gap < -0.5 or headline_bias < 0:
        direction = "short_bias"
        no_trade_reason = ""
    else:
        direction = "watch_only"
        no_trade_reason = "no clear directional catalyst"
    return {
        "ticker": ticker,
        "direction_bias": direction,
        "confidence": round(score, 2),
        "setup_type": "event_dislocation" if dislocation.get("is_event_dislocation") else "market_catalyst_watch",
        "event_dislocation": dislocation,
        "human_approval_required": True,
        "live_trade_execution": False,
        "time_horizon": "intraday_or_next_session",
        "snapshot": snapshot,
        "catalyst_count": catalyst_count,
        "catalyst_summary": [str(row.get("title") or "") for row in catalysts[:4]],
        "source_links": [str(row.get("url") or "") for row in catalysts[:6] if str(row.get("url") or "").strip()],
        "risk_notes": [
            "Use this as research only; no order should be placed without explicit human approval.",
            "Reject stale quotes, thin volume, and ambiguous headlines before considering execution.",
        ],
        "invalidation_notes": [
            "No trade if snapshot is stale or source health is degraded.",
            "No trade if catalyst cannot be verified from source links.",
        ],
        "no_trade_reason": no_trade_reason,
    }


def build_market_study(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    tickers = _tickers_from_payload(payload) if payload else _load_latest_focus_tickers()
    snapshot_payload = market_data.market_snapshot({"tickers": tickers, "refresh": payload.get("refresh", True)})
    catalysts_payload = market_data.market_catalysts({"tickers": tickers, "max_results": payload.get("max_results", 5)})
    snapshots = {str(row.get("ticker") or ""): row for row in list(snapshot_payload.get("snapshots") or []) if isinstance(row, dict)}
    catalysts_by_ticker = dict(catalysts_payload.get("catalysts") or {})
    proposals = [
        _proposal_for_ticker(ticker, dict(snapshots.get(ticker) or {"ticker": ticker, "ok": False}), list(catalysts_by_ticker.get(ticker) or []))
        for ticker in tickers
    ]
    proposals.sort(key=lambda row: float(row.get("confidence") or 0.0), reverse=True)
    best = proposals[0] if proposals else {}
    high_confidence = bool(best and best.get("direction_bias") in {"long_bias", "short_bias"} and _safe_float(best.get("confidence")) >= 75.0)
    source_health = {
        "snapshot": snapshot_payload.get("source_health") or {},
        "catalysts": catalysts_payload.get("source_health") or {},
    }
    blocked = bool(((source_health.get("snapshot") or {}).get("browser") or {}).get("blocked") or ((source_health.get("catalysts") or {}).get("browser") or {}).get("blocked"))
    study = {
        "ok": bool(proposals),
        "run_id": str(payload.get("run_id") or f"apollo_market_study_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        "created_at": _utc_iso(),
        "tickers": tickers,
        "source_health": source_health,
        "proposals": proposals,
        "best_proposal": best,
        "high_confidence": high_confidence,
        "blocked_session": blocked,
        "notify_reason": "high_confidence_proposal" if high_confidence else ("browser_session_blocked" if blocked else ""),
        "summary": _format_summary(proposals, source_health),
    }
    if bool(payload.get("write_artifacts", True)):
        study["artifacts"] = write_market_study_artifacts(study)
    if bool(payload.get("notify", False)):
        study["notify"] = maybe_notify(study)
    return study


def _format_summary(proposals: List[Dict[str, Any]], source_health: Dict[str, Any]) -> str:
    if not proposals:
        return "Apollo market study found no usable tickers."
    lines = []
    for row in proposals[:5]:
        reason = str(row.get("no_trade_reason") or "").strip()
        suffix = f" ({reason})" if reason else ""
        lines.append(f"{row.get('ticker')}: {row.get('direction_bias')} confidence={row.get('confidence')}{suffix}")
    browser = ((source_health.get("snapshot") or {}).get("browser") or {})
    if browser.get("blocked"):
        lines.append("Browser fallback is blocked; manual session refresh needed.")
    return "\n".join(lines)


def write_market_study_artifacts(study: Dict[str, Any]) -> Dict[str, str]:
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(study.get("run_id") or "market_study")).strip("_")
    out_dir = RUNS_ROOT / _local_day() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    study_path = out_dir / "market_study.json"
    proposal_path = out_dir / "trade_proposals.json"
    report_path = out_dir / "market_study_report.md"
    _write_json(study_path, study)
    _write_json(proposal_path, {"proposals": list(study.get("proposals") or []), "best_proposal": dict(study.get("best_proposal") or {})})
    lines = [
        "# Apollo Market Study",
        "",
        f"- created_at: `{study.get('created_at')}`",
        f"- run_id: `{study.get('run_id')}`",
        f"- high_confidence: `{study.get('high_confidence')}`",
        f"- blocked_session: `{study.get('blocked_session')}`",
        "",
        "## Summary",
        "",
        str(study.get("summary") or ""),
        "",
        "## Proposals",
    ]
    for row in list(study.get("proposals") or []):
        lines.extend(
            [
                "",
                f"### {row.get('ticker')}",
                f"- direction_bias: `{row.get('direction_bias')}`",
                f"- confidence: `{row.get('confidence')}`",
                f"- human_approval_required: `{row.get('human_approval_required')}`",
                f"- no_trade_reason: `{row.get('no_trade_reason') or ''}`",
            ]
        )
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    latest_path = RUNS_ROOT / "latest_market_study.json"
    _write_json(latest_path, {"study_path": str(study_path), "proposal_path": str(proposal_path), "report_path": str(report_path), **study})
    return {"study_path": str(study_path), "proposal_path": str(proposal_path), "report_path": str(report_path), "latest_path": str(latest_path)}


def maybe_notify(study: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(study.get("notify_reason") or "")
    if not reason:
        return {"ok": True, "skipped": True, "reason": "routine_low_confidence"}
    url = str(os.getenv("ARGUS_ALERTS_EXTERNAL_URL") or "http://127.0.0.1:5216/alerts/external").strip()
    token = str(os.getenv("SKY_LOCAL_TOKEN") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    best = dict(study.get("best_proposal") or {})
    payload = {
        "level": "critical" if reason == "high_confidence_proposal" else "warn",
        "title": "Apollo market study",
        "message": str(study.get("summary") or "")[:1800],
        "tags": ["chart_with_upwards_trend", "apollo", "market"],
        "source": "apollo",
        "event": reason,
        "ticker": best.get("ticker"),
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        return {"ok": bool(resp.ok), "status_code": resp.status_code, "reason": reason}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}:{exc}", "reason": reason}


def trade_proposal(payload: Dict[str, Any]) -> Dict[str, Any]:
    study = build_market_study({**dict(payload or {}), "write_artifacts": bool(payload.get("write_artifacts", True))})
    ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
    if ticker:
        for row in list(study.get("proposals") or []):
            if str(row.get("ticker") or "") == ticker:
                return {"ok": True, "proposal": row, "study": study}
        return {"ok": False, "error": "ticker_not_in_study", "study": study}
    return {"ok": bool(study.get("best_proposal")), "proposal": dict(study.get("best_proposal") or {}), "study": study}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apollo market study")
    parser.add_argument("cmd", choices=["run"], nargs="?", default="run")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--no-artifacts", action="store_true")
    args = parser.parse_args()
    payload: Dict[str, Any] = {"notify": bool(args.notify), "write_artifacts": not bool(args.no_artifacts)}
    if args.tickers:
        payload["tickers"] = args.tickers
    print(json.dumps(build_market_study(payload), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
