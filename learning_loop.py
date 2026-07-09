from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_APOLLO_ROOT = Path(__file__).resolve().parent
LEARNING_ROOT = _APOLLO_ROOT / "logs" / "learning"
LATEST_LEARNING_PATH = LEARNING_ROOT / "latest_learning_state.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().date().isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return default


def _ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _score_bucket(value: Any) -> str:
    score = _safe_float(value, 0.0)
    if score >= 85:
        return "85_plus"
    if score >= 75:
        return "75_84"
    if score >= 65:
        return "65_74"
    return "under_65"


def _group_key(kind: str, value: Any) -> str:
    raw = str(value or "unknown").strip().lower() or "unknown"
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("_") or "unknown"
    return f"{kind}:{cleaned}"


def _iter_swing_study_paths() -> List[Path]:
    from Apollo import swing_study

    root = Path(os.getenv("APOLLO_SWING_STUDY_ROOT", str(swing_study.RUNS_ROOT)))
    if not root.exists():
        return []
    paths = []
    for path in root.glob("**/swing_study.json"):
        if path.name == "latest_swing_study.json":
            continue
        paths.append(path)
    return sorted(paths, key=lambda p: p.stat().st_mtime)


def _proposal_key(proposal: Dict[str, Any], fallback_created_at: str = "") -> str:
    ticker = _ticker(proposal.get("ticker"))
    first_seen = str((proposal.get("lifecycle") or {}).get("first_seen") or proposal.get("created_at") or fallback_created_at or "")[:10]
    setup = str(proposal.get("setup_type") or "unknown")
    return f"{ticker}:{first_seen}:{setup}"


def collect_historical_proposals(limit: int = 1200) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for path in _iter_swing_study_paths():
        study = _read_json(path, {})
        if not isinstance(study, dict):
            continue
        created_at = str(study.get("created_at") or "")
        regime = dict(study.get("market_regime") or {})
        for row in list(study.get("proposals") or []):
            if not isinstance(row, dict) or not _ticker(row.get("ticker")):
                continue
            proposal = dict(row)
            proposal.setdefault("created_at", created_at)
            proposal.setdefault("source_study_run_id", study.get("run_id"))
            proposal.setdefault("market_regime", regime or proposal.get("market_regime") or {})
            by_key[_proposal_key(proposal, created_at)] = proposal
    proposals = list(by_key.values())
    proposals.sort(key=lambda row: str(row.get("created_at") or (row.get("lifecycle") or {}).get("first_seen") or ""))
    return proposals[-limit:]


def recompute_all_outcomes(proposals: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    from Apollo import swing_study

    rows = list(proposals if proposals is not None else collect_historical_proposals())
    latest = {"created_at": _utc_iso(), "proposals": rows}
    return swing_study.recompute_outcomes({"latest": latest})


def _outcome_return_pct(entry: Dict[str, Any]) -> float:
    outcome = dict(entry.get("outcome") or {})
    returns = dict(outcome.get("followup_returns") or {})
    for window in ("20d", "10d", "5d", "2d"):
        row = dict(returns.get(window) or {})
        if row.get("available") and row.get("close_return_pct") is not None:
            return _safe_float(row.get("close_return_pct"))
    status = str(outcome.get("status") or "").lower()
    entry_price = _safe_float(outcome.get("entry_price"), 0.0)
    stop = _safe_float(outcome.get("stop"), 0.0)
    target = _safe_float(outcome.get("target"), 0.0)
    direction = str(entry.get("direction_bias") or "long_bias")
    if entry_price > 0 and status == "target_hit" and target > 0:
        return ((entry_price - target) / entry_price * 100.0) if direction == "short_bias" else ((target - entry_price) / entry_price * 100.0)
    if entry_price > 0 and status == "stop_hit" and stop > 0:
        return ((entry_price - stop) / entry_price * 100.0) if direction == "short_bias" else ((stop - entry_price) / entry_price * 100.0)
    return 0.0


def _favorable_adverse(entry: Dict[str, Any]) -> Tuple[float, float]:
    outcome = dict(entry.get("outcome") or {})
    returns = dict(outcome.get("followup_returns") or {})
    favorable: List[float] = []
    adverse: List[float] = []
    for row in returns.values():
        if not isinstance(row, dict) or not row.get("available"):
            continue
        favorable.append(_safe_float(row.get("max_favorable_pct"), 0.0))
        adverse.append(_safe_float(row.get("max_adverse_pct"), 0.0))
    return (max(favorable) if favorable else 0.0, min(adverse) if adverse else 0.0)


def _records_from_outcomes(outcomes: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for entry in list(outcomes.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        outcome = dict(entry.get("outcome") or {})
        status = str(outcome.get("status") or entry.get("last_status") or "unknown")
        if status in {"still_valid", "open", "no_trade"}:
            continue
        ret = _outcome_return_pct(entry)
        fav, adv = _favorable_adverse(entry)
        records.append(
            {
                "source": "proposal_outcome",
                "key": entry.get("key") or "",
                "ticker": _ticker(entry.get("ticker")),
                "setup_type": str(entry.get("setup_type") or "unknown"),
                "market_regime": str((entry.get("market_regime") or {}).get("label") or "unknown"),
                "risk_tier": str(entry.get("risk_tier") or "observation"),
                "score_bucket": _score_bucket(entry.get("last_score")),
                "return_pct": round(ret, 3),
                "max_favorable_pct": round(fav, 3),
                "max_adverse_pct": round(adv, 3),
                "win": ret > 0,
                "outcome_status": status,
            }
        )
    return records


def _load_account(account_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(account_path) if account_path else (_APOLLO_ROOT / "logs" / "swing_simulation" / "account_state.json")
    payload = _read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _records_from_closed_trades(account: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for row in list(account.get("closed_positions") or []):
        if not isinstance(row, dict):
            continue
        ret = _safe_float(row.get("realized_pnl_pct"), 0.0)
        records.append(
            {
                "source": "closed_paper_trade",
                "key": f"{_ticker(row.get('ticker'))}:{row.get('opened_at') or ''}:{row.get('closed_at') or ''}",
                "ticker": _ticker(row.get("ticker")),
                "setup_type": str(row.get("setup_type") or "unknown"),
                "market_regime": str(row.get("market_regime") or "unknown"),
                "risk_tier": str(row.get("risk_tier") or "standard"),
                "score_bucket": _score_bucket(row.get("score") or row.get("confidence")),
                "return_pct": round(ret, 3),
                "max_favorable_pct": _safe_float(row.get("max_favorable_pct"), max(0.0, ret)),
                "max_adverse_pct": _safe_float(row.get("max_adverse_pct"), min(0.0, ret)),
                "win": ret > 0,
                "outcome_status": str(row.get("exit_reason") or "closed"),
            }
        )
    return records


def _stats_for(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [_safe_float(row.get("return_pct"), 0.0) for row in records]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    n = len(returns)
    win_rate = round(len(wins) / n * 100.0, 1) if n else 0.0
    avg_win = round(sum(wins) / len(wins), 3) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 3) if losses else 0.0
    expectancy = round((sum(returns) / n), 3) if n else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss else (999.0 if gross_profit else 0.0)
    confidence = round(min(1.0, n / 5.0), 3)
    raw_adjustment = max(-4.0, min(4.0, expectancy * 1.35))
    if n < 2:
        raw_adjustment = max(-1.0, min(1.0, raw_adjustment))
    adjustment = round(raw_adjustment * confidence, 2)
    return {
        "sample_count": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy,
        "profit_factor": profit_factor,
        "avg_max_adverse_pct": round(sum(_safe_float(row.get("max_adverse_pct"), 0.0) for row in records) / n, 3) if n else 0.0,
        "avg_max_favorable_pct": round(sum(_safe_float(row.get("max_favorable_pct"), 0.0) for row in records) / n, 3) if n else 0.0,
        "confidence_weight": confidence,
        "recommended_score_adjustment": adjustment,
    }


def _group_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in records:
        dimensions = {
            "ticker": row.get("ticker"),
            "setup_type": row.get("setup_type"),
            "market_regime": row.get("market_regime"),
            "risk_tier": row.get("risk_tier"),
            "score_bucket": row.get("score_bucket"),
        }
        for kind, value in dimensions.items():
            grouped.setdefault(_group_key(kind, value), []).append(row)
    return {key: {"dimension": key.split(":", 1)[0], "value": key.split(":", 1)[1], **_stats_for(rows)} for key, rows in grouped.items()}


def _top_bottom(groups: Dict[str, Dict[str, Any]], dimension: str, limit: int = 3) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows = [dict(row) for row in groups.values() if row.get("dimension") == dimension and int(row.get("sample_count") or 0) > 0]
    rows.sort(key=lambda row: (_safe_float(row.get("expectancy_pct"), 0.0), int(row.get("sample_count") or 0)), reverse=True)
    best = rows[:limit]
    worst = list(reversed(rows[-limit:])) if rows else []
    return best, worst


def _summary(groups: Dict[str, Dict[str, Any]], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    best_setups, worst_setups = _top_bottom(groups, "setup_type")
    best_tickers, worst_tickers = _top_bottom(groups, "ticker")
    adjustments = [
        {"key": key, **value}
        for key, value in groups.items()
        if abs(_safe_float(value.get("recommended_score_adjustment"), 0.0)) > 0
    ]
    adjustments.sort(key=lambda row: abs(_safe_float(row.get("recommended_score_adjustment"), 0.0)), reverse=True)
    return {
        "record_count": len(records),
        "best_setup_types": best_setups,
        "worst_setup_types": worst_setups,
        "best_tickers": best_tickers,
        "worst_tickers": worst_tickers,
        "score_adjustments_applied": adjustments[:12],
    }


def build_learning_state(
    *,
    proposals: Optional[List[Dict[str, Any]]] = None,
    account: Optional[Dict[str, Any]] = None,
    write: bool = True,
) -> Dict[str, Any]:
    proposal_rows = list(proposals if proposals is not None else collect_historical_proposals())
    outcome_result = recompute_all_outcomes(proposal_rows)
    outcomes = dict(outcome_result.get("outcomes") or {})
    account_payload = dict(account if account is not None else _load_account())
    records = _records_from_outcomes(outcomes) + _records_from_closed_trades(account_payload)
    groups = _group_records(records)
    payload = {
        "ok": True,
        "run_id": f"apollo_learning_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "created_at": _utc_iso(),
        "paper_only": True,
        "live_trade_execution": False,
        "proposal_count": len(proposal_rows),
        "outcome_entry_count": len(list(outcomes.get("entries") or [])),
        "closed_trade_count": len(list(account_payload.get("closed_positions") or [])),
        "records": records[-1000:],
        "groups": groups,
        "summary": _summary(groups, records),
    }
    if write:
        run_path = LEARNING_ROOT / _local_day() / f"{payload['run_id']}.json"
        payload["path"] = str(run_path)
        payload["latest_path"] = str(LATEST_LEARNING_PATH)
        _write_json(run_path, payload)
        _write_json(LATEST_LEARNING_PATH, payload)
    return payload


def latest_learning_state() -> Dict[str, Any]:
    payload = _read_json(LATEST_LEARNING_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _lookup_group(groups: Dict[str, Dict[str, Any]], kind: str, value: Any) -> Dict[str, Any]:
    return dict(groups.get(_group_key(kind, value)) or {})


def learned_adjustment_for_proposal(proposal: Dict[str, Any], state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = dict(state or latest_learning_state())
    groups = dict(state.get("groups") or {})
    reasons: List[str] = []
    adjustment = 0.0
    confidence_values: List[float] = []
    weighted_dimensions = [
        ("ticker", proposal.get("ticker"), 0.35),
        ("setup_type", proposal.get("setup_type"), 0.45),
        ("market_regime", (proposal.get("market_regime") or {}).get("label"), 0.12),
        ("score_bucket", _score_bucket(proposal.get("score")), 0.08),
    ]
    for kind, value, weight in weighted_dimensions:
        group = _lookup_group(groups, kind, value)
        if not group:
            continue
        group_adj = _safe_float(group.get("recommended_score_adjustment"), 0.0)
        confidence = _safe_float(group.get("confidence_weight"), 0.0)
        sample_count = int(group.get("sample_count") or 0)
        if sample_count <= 0 or confidence <= 0:
            continue
        adjustment += group_adj * weight
        confidence_values.append(confidence)
        if abs(group_adj) >= 0.25:
            direction = "boost" if group_adj > 0 else "penalty"
            reasons.append(f"{kind}_{direction}:{value}:{group_adj}")

    catalyst_penalty = _stale_catalyst_penalty(proposal)
    if catalyst_penalty:
        adjustment += catalyst_penalty
        reasons.append(f"stale_catalyst_penalty:{catalyst_penalty}")
    if (proposal.get("setup_history") or {}).get("stale_repeat"):
        adjustment -= 0.75
        reasons.append("stale_repeat_penalty:-0.75")
    event_summary = dict(proposal.get("event_impact") or {})
    if event_summary and not event_summary.get("ok") and _safe_float(event_summary.get("impact_score"), 0.0) >= 50:
        adjustment -= 0.5
        reasons.append("weak_event_impact_penalty:-0.5")
    confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0
    bounded = round(max(-5.0, min(5.0, adjustment)), 2)
    return {
        "adjustment": bounded,
        "reasons": reasons,
        "confidence": confidence,
        "state_run_id": state.get("run_id") or "",
    }


def _stale_catalyst_penalty(proposal: Dict[str, Any]) -> float:
    records = [row for row in list(proposal.get("catalyst_records") or []) if isinstance(row, dict)]
    event_top = dict((proposal.get("event_impact") or {}).get("top_event") or {})
    age_values = [_safe_float(row.get("age_days"), 0.0) for row in records if row.get("age_days") is not None]
    if event_top:
        novelty = dict(event_top.get("novelty") or {})
        age_hours = _safe_float(novelty.get("age_hours"), 0.0)
        if age_hours > 0:
            age_values.append(age_hours / 24.0)
    if not age_values:
        return 0.0
    max_age = max(age_values)
    if max_age >= 30:
        return -1.5
    if max_age >= 14:
        return -0.75
    return 0.0


def run(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    state = build_learning_state(write=bool(payload.get("write", True)))
    try:
        from Apollo import daily_report

        daily_report.append_event(
            "learning_loop",
            {
                "run_id": state.get("run_id"),
                "summary": dict(state.get("summary") or {}),
                "path": state.get("path") or "",
                "latest_path": state.get("latest_path") or "",
                "paper_only": True,
                "live_trade_execution": False,
            },
        )
    except Exception as exc:
        state["daily_report_event_error"] = f"{type(exc).__name__}:{exc}"
    return state


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Apollo's paper-only self-improvement learning loop.")
    parser.add_argument("action", nargs="?", default="run", choices=["run", "latest"])
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "latest":
        print(json.dumps(latest_learning_state(), indent=2, ensure_ascii=False))
        return 0
    result = run({"write": not args.no_write})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
