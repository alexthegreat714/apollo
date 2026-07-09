from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from Apollo import market_data, swing_study, swing_strategy_kb


_APOLLO_ROOT = Path(__file__).resolve().parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "homework_trade"
DEFAULT_TICKERS = tuple(
    t.strip().upper()
    for t in os.getenv(
        "APOLLO_WATCHLIST",
        "NVDA,AMD,AVGO,TSM,MSFT,AMZN,GOOGL,META,JPM,GS,BAC,XOM,CVX,LLY,UNH,JNJ,CAT,HON",
    ).split(",")
    if t.strip()
)
_NIGHTLY_RUNS_ROOT = _APOLLO_ROOT / "logs" / "nightly"


def _corpus_equity_check() -> Dict[str, Any]:
    """
    Check whether the latest nightly run produced equity-relevant sources.
    Reads the triage result (01b_triage.json) from the most recent run.
    Returns {ok, equity_accepted, rejected, total, reject_reasons} or
    {ok: True, skipped: True} when no triage file is found (non-blocking).
    """
    try:
        latest_path = _NIGHTLY_RUNS_ROOT / "latest_status.json"
        if not latest_path.exists():
            return {"ok": True, "skipped": True, "reason": "no_latest_status"}
        status = json.loads(latest_path.read_text(encoding="utf-8"))
        run_dir_raw = str(status.get("run_dir") or "").strip()
        if not run_dir_raw:
            return {"ok": True, "skipped": True, "reason": "no_run_dir"}
        triage_path = Path(run_dir_raw) / "01b_triage.json"
        if not triage_path.exists():
            return {"ok": True, "skipped": True, "reason": "no_triage_result"}
        triage = json.loads(triage_path.read_text(encoding="utf-8"))
        summary = triage.get("summary") or {}
        equity_accepted = int(summary.get("accepted_direct", 0)) + int(summary.get("accepted_ocr", 0))
        return {
            "ok": equity_accepted > 0,
            "equity_accepted": equity_accepted,
            "rejected": int(summary.get("rejected", 0)),
            "total": int(summary.get("total", 0)),
            "reject_reasons": dict(summary.get("reject_reasons") or {}),
        }
    except Exception:
        return {"ok": True, "skipped": True, "reason": "triage_check_error"}


def _latest_nightly_confidence_band() -> str:
    try:
        # primary: latest_status.json symlink at nightly root
        latest_path = _NIGHTLY_RUNS_ROOT / "latest_status.json"
        if latest_path.exists():
            status = json.loads(latest_path.read_text(encoding="utf-8"))
            label = str((status.get("quality") or {}).get("confidence_band", {}).get("label") or "").strip().lower()
            if label in {"low", "medium", "high"}:
                return label
        # fallback: walk date dirs and check nested run dirs
        date_dirs = sorted(
            [d for d in _NIGHTLY_RUNS_ROOT.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )
        for date_dir in date_dirs[:3]:
            run_dirs = sorted(
                [d for d in date_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name,
                reverse=True,
            )
            for run_dir in run_dirs[:3]:
                status_path = run_dir / "status.json"
                if not status_path.exists():
                    continue
                status = json.loads(status_path.read_text(encoding="utf-8"))
                label = str((status.get("quality") or {}).get("confidence_band", {}).get("label") or "").strip().lower()
                if label in {"low", "medium", "high"}:
                    return label
    except Exception:
        pass
    return ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _tickers(payload: Dict[str, Any]) -> str:
    tickers = market_data.normalize_tickers(payload)
    return ",".join(tickers or list(DEFAULT_TICKERS))


def _candidate_rows(study: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [dict(row) for row in list(study.get("proposals") or []) if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            1 if row.get("direction_bias") == "long_bias" and not row.get("no_trade_reason") else 0,
            _safe_float(row.get("score")),
        ),
        reverse=True,
    )
    return rows


_MIN_RISK_REWARD = 1.0
_MIN_PROVIDER_CONFIDENCE = 60.0
_MIN_STRATEGY_HITS = 2


def _row_status(row: Dict[str, Any]) -> str:
    lifecycle = dict(row.get("lifecycle") or {})
    return str(row.get("status") or lifecycle.get("status") or "").strip().lower()


def _candidate_blockers(candidate: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    if not candidate:
        return ["no_candidate"]
    if candidate.get("direction_bias") != "long_bias":
        blockers.append(f"direction_bias:{candidate.get('direction_bias') or 'unknown'}")
    if candidate.get("no_trade_reason"):
        blockers.append(f"no_trade_reason:{candidate.get('no_trade_reason')}")
    status = _row_status(candidate)
    if status in {"expired", "invalidated"}:
        blockers.append(f"stale_or_expired:{status}")
    rr = _safe_float(candidate.get("risk_reward_estimate"))
    if rr <= 0:
        blockers.append("risk_reward_missing")
    elif rr < _MIN_RISK_REWARD:
        blockers.append(f"rr_below_minimum:{rr:.2f}<{_MIN_RISK_REWARD:.1f}")
    if not str(candidate.get("entry_zone") or "").strip():
        blockers.append("entry_zone_missing")
    if not str(candidate.get("stop_zone") or "").strip():
        blockers.append("stop_zone_missing")
    if not str(candidate.get("target_zone") or "").strip():
        blockers.append("target_zone_missing")
    provider_confidence = _safe_float(candidate.get("provider_confidence"))
    if provider_confidence > 0 and provider_confidence < _MIN_PROVIDER_CONFIDENCE:
        blockers.append(f"provider_confidence_below_minimum:{provider_confidence:.1f}<{_MIN_PROVIDER_CONFIDENCE:.1f}")
    if str(candidate.get("confidence_label") or "").strip().lower() == "low":
        blockers.append("confidence_label_low")
    if len(list(candidate.get("catalyst_summary") or [])) == 0:
        blockers.append("catalysts_missing")
    return blockers


def _quality_score(candidate: Dict[str, Any], strategy_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    strategy_context = dict(strategy_context or {})
    blockers = list(_candidate_blockers(candidate))
    score = _safe_float(candidate.get("score")) or 50.0
    rr = _safe_float(candidate.get("risk_reward_estimate"))
    strategy_hits = len(list(strategy_context.get("hits") or []))
    catalyst_count = len(list(candidate.get("catalyst_summary") or []))
    if blockers:
        score -= min(60.0, 14.0 * len(blockers))
    if rr >= 2.0:
        score += 6.0
    elif rr >= 1.5:
        score += 3.0
    elif rr < _MIN_RISK_REWARD:
        score -= 10.0
    if strategy_hits < _MIN_STRATEGY_HITS:
        blockers.append(f"strategy_context_below_minimum:{strategy_hits}<{_MIN_STRATEGY_HITS}")
        score -= 12.0
    if catalyst_count == 0:
        score -= 8.0
    if _row_status(candidate) == "new":
        score += 4.0
    quality_score = round(max(0.0, min(100.0, score)), 2)
    if blockers:
        label = "blocked"
    elif quality_score >= 85:
        label = "strong"
    elif quality_score >= 70:
        label = "usable"
    else:
        label = "needs_review"
    return {
        "score": quality_score,
        "label": label,
        "blockers": blockers,
        "min_risk_reward": _MIN_RISK_REWARD,
        "min_provider_confidence": _MIN_PROVIDER_CONFIDENCE,
        "strategy_hit_count": strategy_hits,
        "catalyst_count": catalyst_count,
        "candidate_status": _row_status(candidate),
    }


def _select_candidate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    eligible = [row for row in rows if not _candidate_blockers(row)]
    if eligible:
        return eligible[0]
    return rows[0]


def _trade_plan(candidate: Dict[str, Any], upstream: Dict[str, Any], strategy_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    quality = _quality_score(candidate, strategy_context)
    no_trade = bool(quality.get("blockers"))
    no_trade_reason = "; ".join(list(quality.get("blockers") or []))
    if not no_trade_reason:
        no_trade_reason = candidate.get("no_trade_reason") or ""
    return {
        "ticker": candidate.get("ticker") or "",
        "decision": "research_candidate" if not no_trade else "no_trade",
        "setup_type": candidate.get("setup_type") or "",
        "score": candidate.get("score"),
        "confidence_label": candidate.get("confidence_label") or "",
        "entry_zone": candidate.get("entry_zone") if not no_trade else "",
        "stop_zone": candidate.get("stop_zone") if not no_trade else "",
        "target_zone": candidate.get("target_zone") if not no_trade else "",
        "risk_reward_estimate": candidate.get("risk_reward_estimate") if not no_trade else None,
        "invalidation_trigger": candidate.get("invalidation_trigger") or "",
        "review_by_date": candidate.get("review_by_date") or "",
        "market_regime": candidate.get("market_regime") or {},
        "provider_confidence": candidate.get("provider_confidence"),
        "catalyst_quality_score": candidate.get("catalyst_quality_score"),
        "outcome_status": candidate.get("outcome_status") or {},
        "learning_notes": list(candidate.get("learning_notes") or []),
        "catalyst_summary": list(candidate.get("catalyst_summary") or [])[:5],
        "source_links": list(candidate.get("source_links") or [])[:8],
        "no_trade_reason": no_trade_reason,
        "quality": quality,
        "quality_gates": {
            "passed": not no_trade,
            "blockers": list(quality.get("blockers") or []),
            "min_risk_reward": _MIN_RISK_REWARD,
            "min_provider_confidence": _MIN_PROVIDER_CONFIDENCE,
            "min_strategy_hits": _MIN_STRATEGY_HITS,
        },
        "strategy_context": dict(strategy_context or {}),
        "strategy_rubric": list((strategy_context or {}).get("rubric") or []),
        "upstream_ocr": upstream,
        "human_approval_required": True,
        "live_trade_execution": False,
        "broker_order_created": False,
    }


def _report_lines(result: Dict[str, Any]) -> List[str]:
    plan = dict(result.get("trade_plan") or {})
    study = dict(result.get("swing_study") or {})
    rows = [dict(row) for row in list(study.get("proposals") or []) if isinstance(row, dict)]
    quality = dict(plan.get("quality") or {})
    blockers = list((plan.get("quality_gates") or {}).get("blockers") or quality.get("blockers") or [])
    learning_effect = dict(result.get("learning_effect") or {})
    lines = [
        "# Apollo Homework Trade Plan",
        "",
        f"- created_at: `{result.get('created_at')}`",
        f"- run_id: `{result.get('run_id')}`",
        f"- upstream_ocr_status: `{(result.get('upstream_ocr') or {}).get('status')}`",
        f"- strategy_kb_status: `{(result.get('strategy_kb') or {}).get('status')}`",
        f"- market_regime: `{(study.get('market_regime') or {}).get('label')}`",
        f"- live_trade_execution: `false`",
        f"- human_approval_required: `true`",
        "",
        "## Decision",
        "",
        f"- ticker: `{plan.get('ticker')}`",
        f"- decision: `{plan.get('decision')}`",
        f"- setup_type: `{plan.get('setup_type')}`",
        f"- score: `{plan.get('score')}`",
        f"- entry_zone: `{plan.get('entry_zone')}`",
        f"- stop_zone: `{plan.get('stop_zone')}`",
        f"- target_zone: `{plan.get('target_zone')}`",
        f"- risk_reward: `{plan.get('risk_reward_estimate')}`",
        f"- quality: `{quality.get('label') or 'unknown'} ({quality.get('score')})`",
        f"- invalidation: `{plan.get('invalidation_trigger')}`",
        f"- no_trade_reason: `{plan.get('no_trade_reason')}`",
        f"- quality_blockers: `{'; '.join(blockers) if blockers else 'none'}`",
        "",
        "## Homework Quality Gates",
        "",
        f"- passed: `{not bool(blockers)}`",
        f"- minimum_risk_reward: `{_MIN_RISK_REWARD}`",
        f"- strategy_hits: `{quality.get('strategy_hit_count', 0)}`",
        f"- candidate_status: `{quality.get('candidate_status') or ''}`",
        f"- provider_confidence_minimum: `{_MIN_PROVIDER_CONFIDENCE}`",
        "",
        "## Learning Effect",
        "",
        f"- verdict: `{learning_effect.get('verdict') or 'unknown'}`",
        f"- selected_candidate: `{learning_effect.get('selected_candidate') or ''}`",
        f"- blocked_candidates: `{learning_effect.get('blocked_candidate_count', 0)}`",
        f"- actionable_candidates: `{learning_effect.get('actionable_candidate_count', 0)}`",
        f"- note: `{learning_effect.get('note') or ''}`",
        "",
        "## Catalysts",
        "",
    ]
    catalysts = list(plan.get("catalyst_summary") or [])
    lines.extend([f"- {item}" for item in catalysts] or ["- None recorded."])
    lines.extend(["", "## Strategy Rubric", ""])
    rubric = list(plan.get("strategy_rubric") or [])
    lines.extend([f"- {item}" for item in rubric[:6]] or ["- No strategy KB matches were available."])
    lines.extend(["", "## Ranked Watchlist", ""])
    for row in rows[:12]:
        row_blockers = _candidate_blockers(row)
        lines.append(
            f"- {row.get('ticker')}: {row.get('setup_type')} score={row.get('score')} "
            f"status={row.get('status')} decision={row.get('direction_bias')} "
            f"rr={row.get('risk_reward_estimate')} "
            f"quality={'blocked' if row_blockers else 'actionable'} "
            f"reason={'; '.join(row_blockers) or row.get('no_trade_reason') or ''}"
        )
    return lines


def _learning_effect(rows: List[Dict[str, Any]], selected: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    actionable = [row for row in rows if not _candidate_blockers(row)]
    blocked = [row for row in rows if _candidate_blockers(row)]
    blockers = list((plan.get("quality_gates") or {}).get("blockers") or [])
    verdict = "improves_decision_quality" if plan.get("decision") == "research_candidate" else "blocked_weak_candidate"
    if not rows:
        verdict = "no_candidates"
    elif not actionable:
        verdict = "blocked_all_candidates"
    note = "Homework gates kept weak, stale, or incomplete setups out of the trade queue."
    if plan.get("decision") == "research_candidate":
        note = "Homework found an actionable candidate that passed freshness, risk/reward, data, and strategy-context gates."
    return {
        "verdict": verdict,
        "selected_candidate": selected.get("ticker") or "",
        "selected_setup": selected.get("setup_type") or "",
        "selected_blockers": blockers,
        "actionable_candidate_count": len(actionable),
        "blocked_candidate_count": len(blocked),
        "note": note,
    }


def write_artifacts(result: Dict[str, Any]) -> Dict[str, str]:
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(result.get("run_id") or "apollo_homework_trade")).strip("_")
    out_dir = RUNS_ROOT / _local_day() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "homework_trade_plan.json"
    report_path = out_dir / "homework_trade_report.md"
    status_path = out_dir / "status.json"
    latest_path = RUNS_ROOT / "latest_homework_trade.json"
    _write_json(plan_path, result)
    report_path.write_text("\n".join(_report_lines(result)).strip() + "\n", encoding="utf-8")
    _write_json(status_path, {"ok": result.get("ok"), "run_id": result.get("run_id"), "created_at": result.get("created_at"), "plan_path": str(plan_path), "report_path": str(report_path)})
    latest = {"plan_path": str(plan_path), "report_path": str(report_path), "status_path": str(status_path), **result}
    _write_json(latest_path, latest)
    return {"plan_path": str(plan_path), "report_path": str(report_path), "status_path": str(status_path), "latest_path": str(latest_path)}


def build_homework_trade_plan(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    data_quality_band = str(payload.get("data_quality_band") or "").strip().lower()
    if not data_quality_band:
        data_quality_band = _latest_nightly_confidence_band()
    payload["data_quality_band"] = data_quality_band

    corpus_check = _corpus_equity_check()
    if not corpus_check.get("ok") and not corpus_check.get("skipped"):
        return {
            "ok": False,
            "run_id": f"apollo_homework_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "created_at": _utc_iso(),
            "trade_plan": {
                "decision": "no_trade",
                "no_trade_reason": "insufficient_corpus_equity_sources",
                "human_approval_required": True,
                "live_trade_execution": False,
            },
            "swing_study": {"ok": False, "proposals": []},
            "corpus_check": corpus_check,
            "data_quality_band": data_quality_band,
        }

    upstream = {
        "task": str(payload.get("upstream_ocr_task") or ""),
        "status": str(payload.get("upstream_ocr_status") or "unknown"),
        "last_result": str(payload.get("upstream_ocr_result") or ""),
        "completed_at": str(payload.get("upstream_ocr_completed_at") or ""),
    }
    study = swing_study.build_swing_study(
        {
            "tickers": _tickers(payload),
            "notify": bool(payload.get("notify", False)),
            "write_artifacts": True,
            "refresh": True,
            "data_quality_band": str(payload.get("data_quality_band") or ""),
        }
    )
    rows = _candidate_rows(study)
    best = _select_candidate(rows)
    strategy_context: Dict[str, Any] = {}
    if best and bool(payload.get("use_strategy_kb", True)):
        strategy_context = swing_strategy_kb.build_strategy_context(best, top_k=int(payload.get("strategy_top_k") or 5))
    plan = _trade_plan(best, upstream, strategy_context) if best else {"decision": "no_trade", "human_approval_required": True, "live_trade_execution": False}
    result = {
        "ok": bool(study.get("ok")),
        "run_id": str(payload.get("run_id") or f"apollo_homework_trade_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        "created_at": _utc_iso(),
        "tickers": market_data.normalize_tickers({"tickers": _tickers(payload)}),
        "purpose": "homework trade research after OCR pipeline completion",
        "strategy_kb": dict(payload.get("strategy_kb_status") or {}),
        "research_only": True,
        "human_approval_required": True,
        "live_trade_execution": False,
        "upstream_ocr": upstream,
        "trade_plan": plan,
        "swing_study": study,
        "learning_effect": _learning_effect(rows, best, plan),
    }
    if bool(payload.get("write_artifacts", True)):
        result["artifacts"] = write_artifacts(result)
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apollo homework trade research pipeline")
    parser.add_argument("cmd", choices=["run"], nargs="?", default="run")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--upstream-ocr-task", default="")
    parser.add_argument("--upstream-ocr-status", default="")
    parser.add_argument("--upstream-ocr-result", default="")
    parser.add_argument("--upstream-ocr-completed-at", default="")
    parser.add_argument("--strategy-kb-status", default="")
    parser.add_argument("--strategy-top-k", type=int, default=5)
    parser.add_argument("--no-strategy-kb", action="store_true")
    args = parser.parse_args()
    payload: Dict[str, Any] = {
        "tickers": args.tickers,
        "notify": bool(args.notify),
        "upstream_ocr_task": args.upstream_ocr_task,
        "upstream_ocr_status": args.upstream_ocr_status,
        "upstream_ocr_result": args.upstream_ocr_result,
        "upstream_ocr_completed_at": args.upstream_ocr_completed_at,
        "strategy_kb_status": {"status": args.strategy_kb_status} if args.strategy_kb_status else {},
        "strategy_top_k": args.strategy_top_k,
        "use_strategy_kb": not bool(args.no_strategy_kb),
    }
    print(json.dumps(build_homework_trade_plan(payload), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["build_homework_trade_plan", "write_artifacts"]
