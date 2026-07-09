from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from Apollo import risk_scaling
from Apollo import stress_mode


_APOLLO_ROOT = Path(__file__).resolve().parent
REPORT_ROOT = _APOLLO_ROOT / "logs" / "daily_reports"
README_PATH = _APOLLO_ROOT / "README.md"
README_START = "<!-- APOLLO_DAILY_REPORT_COMPLETION_START -->"
README_END = "<!-- APOLLO_DAILY_REPORT_COMPLETION_END -->"
CT = ZoneInfo("America/Chicago")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now_utc()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ct_now(dt: Optional[datetime] = None) -> datetime:
    return (dt or _now_utc()).astimezone(CT)


def _ct_stamp(dt: Optional[datetime] = None) -> str:
    return _ct_now(dt).strftime("%H:%M CST")


def _ct_date(dt: Optional[datetime] = None) -> str:
    return _ct_now(dt).date().isoformat()


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
            return payload if isinstance(payload, dict) else dict(default or {})
        except Exception:
            continue
    return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except Exception:
        return "$0.00"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    except Exception:
        pass
    return default


def _paths(report_date: Optional[str] = None) -> Dict[str, Path]:
    date_value = str(report_date or _ct_date()).strip()[:10] or _ct_date()
    root = REPORT_ROOT / date_value
    return {
        "date": root,
        "json": root / "apollo_daily_report.json",
        "md": root / "apollo_daily_report.md",
        "latest_json": REPORT_ROOT / "latest_apollo_daily_report.json",
        "latest_md": REPORT_ROOT / "latest_apollo_daily_report.md",
    }


def _parse_report_date(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d")
    except Exception:
        return None


def _load_latest_nightly() -> Dict[str, Any]:
    try:
        from Apollo import nightly_pipeline

        status = nightly_pipeline.latest_status()
        if isinstance(status, dict) and status:
            latest_real = status.get("latest_real_run")
            if isinstance(latest_real, dict) and latest_real.get("run_dir"):
                run_dir = Path(str(latest_real.get("run_dir")))
                real_status = _read_json(run_dir / "status.json", {})
                if real_status:
                    try:
                        return nightly_pipeline._enrich_status_payload(run_dir, real_status)
                    except Exception:
                        return real_status
            return status
    except Exception:
        pass
    return _read_json(_APOLLO_ROOT / "logs" / "nightly" / "latest_status.json", {})


def _load_latest_swing() -> Dict[str, Any]:
    return _read_json(_APOLLO_ROOT / "logs" / "swing_study" / "latest_swing_study.json", {})


def _load_latest_learning() -> Dict[str, Any]:
    return _read_json(_APOLLO_ROOT / "logs" / "learning" / "latest_learning_state.json", {})


def _load_sim_account() -> Dict[str, Any]:
    try:
        from Apollo import swing_simulation

        return swing_simulation.get_simulation_account(save_report=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _previous_daily_report(report_date: str) -> Dict[str, Any]:
    parsed = _parse_report_date(report_date)
    if parsed is None:
        return {}
    previous_date = (parsed.date() - timedelta(days=1)).isoformat()
    return _read_json(_paths(previous_date)["json"], {})


def _ending_balance_from_report(payload: Dict[str, Any]) -> float:
    final_summary = dict(payload.get("final_summary") or {})
    account = dict(payload.get("simulation_account") or {})
    return _safe_float(
        final_summary.get("market_value")
        if final_summary.get("market_value") is not None
        else account.get("market_value"),
        default=0.0,
    )


def _daily_balance_check(payload: Dict[str, Any], account: Dict[str, Any]) -> Dict[str, Any]:
    existing = dict(payload.get("daily_balance_check") or {})
    report_date = str(payload.get("date") or _ct_date()).strip()[:10]
    current_value = _safe_float(account.get("market_value"), _safe_float(account.get("cash_balance"), 0.0))

    previous = _previous_daily_report(report_date)
    previous_date = ""
    previous_end = 0.0
    previous_found = False
    if previous:
        previous_date = str(previous.get("date") or "")
        previous_end = _ending_balance_from_report(previous)
        previous_found = previous_end > 0

    if existing.get("start_balance") is not None:
        start_balance = _safe_float(existing.get("start_balance"), default=current_value)
    else:
        start_balance = previous_end if previous_found else current_value

    delta = round(start_balance - previous_end, 4) if previous_found else None
    reconciled = bool(previous_found and abs(float(delta or 0.0)) < 0.01)
    status = "matched_previous_close" if reconciled else ("mismatch_previous_close" if previous_found else "no_previous_close")
    return {
        **existing,
        "date": report_date,
        "checked_at": existing.get("checked_at") or _utc_iso(),
        "checked_at_ct": existing.get("checked_at_ct") or _ct_stamp(),
        "start_balance": round(start_balance, 2),
        "current_market_value": round(current_value, 2),
        "previous_date": previous_date,
        "previous_end_balance": round(previous_end, 2) if previous_found else None,
        "delta": delta,
        "reconciled": reconciled,
        "status": status,
        "source": "apollo_swing_paper_sim",
    }


def _should_send_daily_balance_notification(payload: Dict[str, Any]) -> bool:
    if os.getenv("APOLLO_DAILY_BALANCE_NOTIFY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if str(payload.get("date") or "") != _ct_date():
        return os.getenv("APOLLO_DAILY_BALANCE_NOTIFY_HISTORICAL", "0").strip().lower() in {"1", "true", "yes", "on"}
    sent = dict(payload.get("daily_balance_notification") or {})
    return not bool(sent.get("sent"))


def _send_daily_balance_notification(payload: Dict[str, Any]) -> Dict[str, Any]:
    check = dict(payload.get("daily_balance_check") or {})
    if not check:
        return {"sent": False, "skipped": True, "reason": "missing_daily_balance_check"}
    url = str(os.getenv("ARGUS_ALERTS_EXTERNAL_URL") or "http://127.0.0.1:5216/alerts/external").strip()
    token = str(os.getenv("SKY_LOCAL_TOKEN") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    previous = check.get("previous_end_balance")
    previous_text = _money(previous) if previous is not None else "unavailable"
    match_text = "matched" if check.get("reconciled") else str(check.get("status") or "not_matched")
    alert = {
        "level": "warn",
        "title": "Apollo daily paper balance",
        "message": (
            f"Apollo swing paper sim starts {check.get('date')} at {_money(check.get('start_balance'))}. "
            f"Yesterday close ({check.get('previous_date') or 'none'}): {previous_text}. "
            f"Continuity: {match_text}. Current value: {_money(check.get('current_market_value'))}."
        ),
        "tags": ["chart_with_upwards_trend", "apollo", "swing", "paper"],
        "source": "apollo",
        "event": "daily_balance_check",
        "date": check.get("date"),
        "start_balance": check.get("start_balance"),
        "previous_end_balance": check.get("previous_end_balance"),
        "reconciled": check.get("reconciled"),
    }
    try:
        resp = requests.post(url, json=alert, headers=headers, timeout=5)
        return {
            "sent": bool(resp.ok),
            "status_code": int(getattr(resp, "status_code", 0)),
            "sent_at": _utc_iso() if resp.ok else "",
            "sent_at_ct": _ct_stamp() if resp.ok else "",
            "reason": "daily_balance_check",
        }
    except Exception as exc:
        return {"sent": False, "error": f"{type(exc).__name__}:{exc}", "reason": "daily_balance_check"}


def _send_nightly_recovery_notification(payload: Dict[str, Any], scheduled: Dict[str, Any]) -> Dict[str, Any]:
    url = str(os.getenv("ARGUS_ALERTS_EXTERNAL_URL") or "http://127.0.0.1:5216/alerts/external").strip()
    token = str(os.getenv("SKY_LOCAL_TOKEN") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    alert = {
        "level": "warn",
        "title": "Apollo nightly recovery needed",
        "message": (
            f"Apollo 1 AM nightly run for {payload.get('date')} appears failed and no later passing recovery is recorded. "
            f"Scheduler log: {scheduled.get('stdout_path') or 'missing'}"
        ),
        "tags": ["warning", "apollo", "nightly"],
        "source": "apollo",
        "event": "nightly_recovery_needed",
        "date": payload.get("date"),
    }
    try:
        resp = requests.post(url, json=alert, headers=headers, timeout=5)
        return {
            "sent": bool(resp.ok),
            "status_code": int(getattr(resp, "status_code", 0)),
            "sent_at": _utc_iso() if resp.ok else "",
            "sent_at_ct": _ct_stamp() if resp.ok else "",
            "reason": "nightly_recovery_needed",
        }
    except Exception as exc:
        return {"sent": False, "error": f"{type(exc).__name__}:{exc}", "reason": "nightly_recovery_needed"}


def _proposal_lookup(swing: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    proposals = [row for row in list(swing.get("proposals") or []) if isinstance(row, dict)]
    return {str(row.get("ticker") or "").upper(): dict(row) for row in proposals if str(row.get("ticker") or "").strip()}


def _swing_trade_summary(swing: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    by_ticker = _proposal_lookup(swing)
    allocations = [row for row in list(plan.get("allocations") or []) if isinstance(row, dict)]
    if not allocations:
        best = dict(swing.get("best_proposal") or {})
        if best:
            allocations = [{"ticker": best.get("ticker"), "amount": 0, "entry_zone": best.get("entry_zone")}]

    rows: List[Dict[str, Any]] = []
    for item in allocations[:8]:
        ticker = str(item.get("ticker") or "").upper()
        proposal = dict(by_ticker.get(ticker) or {})
        snapshot = dict(proposal.get("swing_snapshot") or {})
        event_risk = dict(proposal.get("earnings_or_event_risk") or {})
        kg = dict(proposal.get("kg_grounding") or {})
        row = {
            "ticker": ticker,
            "amount": item.get("amount"),
            "setup_type": proposal.get("setup_type") or item.get("setup_type"),
            "score": proposal.get("score") or item.get("score"),
            "confidence_label": proposal.get("confidence_label") or "",
            "risk_reward": proposal.get("risk_reward_estimate") or item.get("risk_reward"),
            "entry_zone": proposal.get("entry_zone") or item.get("entry_zone"),
            "stop_zone": proposal.get("stop_zone") or item.get("stop_zone"),
            "target_zone": proposal.get("target_zone") or item.get("target_zone"),
            "time_horizon": proposal.get("time_horizon") or "2_to_20_trading_days",
            "relative_strength_rank": proposal.get("relative_strength_rank"),
            "atr_pct": proposal.get("atr_pct"),
            "source": snapshot.get("source") or "",
            "as_of": snapshot.get("as_of") or "",
            "event_risk": event_risk.get("label") or "normal",
            "kg_triple_count": kg.get("triple_count", 0),
            "why_swing": [
                f"{str(proposal.get('setup_type') or item.get('setup_type') or 'setup').replace('_', ' ')} setup",
                f"score {proposal.get('score') or item.get('score')}",
                f"R/R {proposal.get('risk_reward_estimate') or item.get('risk_reward')}",
                f"RS rank {proposal.get('relative_strength_rank')}",
            ],
            "why_not_long_term_low_risk": [
                "Time horizon is 2-20 trading days, not a multi-year underwriting horizon.",
                "The plan depends on an entry zone, stop, target, and invalidation trigger.",
                "Risk is technical/event driven; this is not a low-risk security or buy-and-hold thesis.",
            ],
        }
        rows.append(row)

    return {
        "run_id": swing.get("run_id") or "",
        "created_at": swing.get("created_at") or "",
        "market_regime": (swing.get("market_regime") or {}).get("label") or "",
        "plan_status": plan.get("status") or "",
        "rows": rows,
        "report_rule": "Classify these as paper swing setups only. Do not describe them as long-term low-risk holdings.",
    }


def _initial_payload(report_date: Optional[str] = None) -> Dict[str, Any]:
    date_value = str(report_date or _ct_date()).strip()[:10] or _ct_date()
    nightly = _load_latest_nightly()
    swing = _load_latest_swing()
    account_payload = _load_sim_account()
    account = dict(account_payload.get("account") or {})
    next_plan = dict(account_payload.get("next_cycle_plan") or {})
    best = dict(swing.get("best_proposal") or {})
    return {
        "ok": True,
        "date": date_value,
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "timezone": "America/Chicago",
        "morning_plan": {
            "created_at_ct": _ct_stamp(),
            "nightly_run_id": nightly.get("run_id") or "",
            "nightly_ok": nightly.get("ok"),
            "nightly_stage": nightly.get("current_stage") or "",
            "nightly_truth_label": nightly.get("truth_label") or "",
            "nightly_pipeline_completed": bool(nightly.get("pipeline_completed")),
            "nightly_quality_gate_pass": bool(nightly.get("quality_gate_pass")),
            "nightly_gate_fail_reasons": list((nightly.get("quality") or {}).get("gate_fail_reasons") or []),
            "nightly_stage_truth": dict(nightly.get("stage_truth") or {}),
            "nightly_run_dir": nightly.get("run_dir") or "",
            "swing_run_id": swing.get("run_id") or "",
            "best_ticker": best.get("ticker") or "",
            "best_score": best.get("score") or best.get("confidence"),
            "market_regime": (swing.get("market_regime") or {}).get("label") or "",
            "planned_checks": [
                "07:45 CT open report",
                "08:45-15:15 CT lightweight pre-trade planner every 30 minutes",
                "forced pre-trade gate before paper_sim submit",
                "16:35 CT final daily report",
            ],
        },
        "events": [],
        "latest_gate": {},
        "final_summary": {},
        "simulation_account": account,
        "next_cycle_plan": next_plan,
        "swing_trade_summary": _swing_trade_summary(swing, next_plan),
        "guardrails": {
            "mode": "paper_sim",
            "live_trade_execution": False,
            "alpaca_required": False,
            "heavy_daytime_hipporag_refresh": False,
        },
    }


def _nightly_truth_summary(nightly: Dict[str, Any]) -> Dict[str, Any]:
    quality = dict(nightly.get("quality") or {})
    return {
        "run_id": nightly.get("run_id") or "",
        "run_dir": nightly.get("run_dir") or "",
        "ok": nightly.get("ok"),
        "current_stage": nightly.get("current_stage") or "",
        "truth_label": nightly.get("truth_label") or ("passed" if nightly.get("ok") else "blocked"),
        "pipeline_completed": bool(nightly.get("pipeline_completed")),
        "quality_gate_pass": bool(nightly.get("quality_gate_pass") or quality.get("gate_pass")),
        "stage_truth": dict(nightly.get("stage_truth") or {}),
        "gate_fail_reasons": list(quality.get("gate_fail_reasons") or []),
        "ocr": dict(nightly.get("ocr") or {}),
        "updated_at": nightly.get("updated_at") or "",
        "stale": bool(nightly.get("stale")),
        "stale_reason": nightly.get("stale_reason") or "",
        "latest_real_run": dict(nightly.get("latest_real_run") or {}),
    }


def _homework_trade_status(report_date: str) -> Dict[str, Any]:
    root = _APOLLO_ROOT / "logs" / "homework_trade" / report_date
    runs = []
    if root.exists():
        for status_path in sorted(root.glob("*/status.json")):
            status = _read_json(status_path, {})
            if status:
                runs.append(
                    {
                        "run_id": status.get("run_id") or status_path.parent.name,
                        "status": status.get("status") or ("complete" if status.get("ok") else "unknown"),
                        "ok": status.get("ok"),
                        "status_path": str(status_path),
                        "report_path": str(status_path.parent / "homework_trade_report.md"),
                        "plan_path": str(status_path.parent / "homework_trade_plan.json"),
                    }
                )
    latest = runs[-1] if runs else {}
    return {
        "status": "complete" if runs else "missing_today",
        "missing_today": not bool(runs),
        "run_count": len(runs),
        "latest": latest,
        "root": str(root),
        "live_trade_execution": False,
        "human_approval_required": True,
    }


def _high_risk_homework_status() -> Dict[str, Any]:
    latest = _read_json(_APOLLO_ROOT / "reports" / "nightly_high_risk_homework" / "latest" / "status.json", {})
    projection = dict(latest.get("next_day_projection") or {})
    confidence = dict(latest.get("confidence_model") or projection.get("confidence_model") or {})
    ocr97 = dict(latest.get("ocr97_status") or confidence.get("ocr97") or {})
    return {
        "status": str(latest.get("status") or ("complete" if latest.get("ok") else "missing_today")),
        "run_id": str(latest.get("run_id") or ""),
        "score": latest.get("score"),
        "projection_action": str(projection.get("primary_action") or ""),
        "source_confidence": str(confidence.get("source_confidence") or ""),
        "event_confidence": str(confidence.get("event_confidence") or ""),
        "graph_usefulness": str(confidence.get("graph_usefulness") or ""),
        "technical_confirmation": str(confidence.get("technical_confirmation") or ""),
        "top_blocker": str(confidence.get("top_blocker") or ""),
        "blocker_hierarchy": list(confidence.get("blocker_hierarchy") or []),
        "ocr97_status": ocr97,
        "report_path": str(latest.get("markdown_path") or ""),
        "json_path": str(latest.get("json_path") or ""),
    }


def _graph_plot_status(report_date: str) -> Dict[str, Any]:
    root = _APOLLO_ROOT / "logs" / "plots" / report_date
    summaries = sorted(root.glob("*/graph_summary.md")) if root.exists() else []
    pngs = sorted(root.glob("*/*.png")) if root.exists() else []
    latest = summaries[-1] if summaries else None
    return {
        "status": "available" if summaries else "missing_today",
        "missing_today": not bool(summaries),
        "root": str(root),
        "summary_path": str(latest) if latest else "",
        "png_paths": [str(path) for path in pngs[-12:]],
        "message": "graph images generated today" if pngs else "no graph images generated today",
    }


def _latest_json_status(pattern: str) -> Dict[str, Any]:
    paths = sorted(_APOLLO_ROOT.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        payload = _read_json(path, {})
        if payload:
            payload["status_path"] = str(path)
            payload.setdefault("run_id", path.parent.name)
            return payload
    return {}


def _hipporag_discovery_status(report_date: str, reported: Dict[str, Any]) -> Dict[str, Any]:
    actual = _latest_json_status(f"logs/hipporag_improve/{report_date}/*/status.json")
    actual_status = str(actual.get("state") or ("complete" if actual.get("ok") else ("missing_today" if not actual else "failed")))
    reported_status = str(reported.get("status") or "missing_today")
    return {
        "reported_status": reported_status,
        "actual_latest_status": actual_status,
        "mismatch": bool(actual) and reported_status != actual_status,
        "run_id": actual.get("run_id") or "",
        "status_path": actual.get("status_path") or "",
        "message": actual.get("message") or "",
    }


def _nightly_scheduler_status(report_date: str) -> Dict[str, Any]:
    compact_date = report_date.replace("-", "")
    root = _APOLLO_ROOT / "logs" / "nightly" / "scheduler_runs"
    stdout_logs = sorted(root.glob(f"nightly_{compact_date}_0100*.stdout.log"), key=lambda path: path.stat().st_mtime, reverse=True) if root.exists() else []
    stderr_logs = sorted(root.glob(f"nightly_{compact_date}_0100*.stderr.log"), key=lambda path: path.stat().st_mtime, reverse=True) if root.exists() else []
    stdout_path = stdout_logs[0] if stdout_logs else None
    stderr_path = stderr_logs[0] if stderr_logs else None
    full_text = ""
    if stdout_path:
        try:
            full_text = stdout_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            full_text = ""
    notification_failure = "could not post" in full_text.lower() and "sky" in full_text.lower()
    lower_text = full_text.lower()
    scheduler_failed = bool(stdout_path) and not (
        '"truth_label":  "passed"' in lower_text
        or '"truth_label": "passed"' in lower_text
        or '"quality_gate_pass":  true' in lower_text
        or '"quality_gate_pass": true' in lower_text
    )
    return {
        "status": "found" if stdout_path else "missing_today",
        "stdout_path": str(stdout_path) if stdout_path else "",
        "stderr_path": str(stderr_path) if stderr_path else "",
        "scheduler_failed": scheduler_failed,
        "notification_failure": notification_failure,
        "notification_failure_note": "Sky calendar post timed out" if notification_failure else "",
    }


def _trade_cycle_status(report_date: str) -> Dict[str, Any]:
    compact_date = report_date.replace("-", "")
    status = _latest_json_status(f"logs/trade_cycle/trade_cycle_{compact_date}_*/status.json")
    if not status:
        return {"status": "missing_today"}
    adjudication = dict(status.get("deep_trade_adjudication") or {})
    return {
        "status": "complete" if status.get("completed") else "partial",
        "run_id": status.get("run_id") or "",
        "ok": status.get("ok"),
        "trade_ready": bool(status.get("trade_ready")),
        "probe_ready": bool(status.get("probe_ready")),
        "aggressive_probe_ready": bool(status.get("aggressive_probe_ready")),
        "stress_probe_ready": bool(status.get("stress_probe_ready")),
        "simulation_stress_mode": bool(status.get("simulation_stress_mode")),
        "pressed_aggressive": bool(status.get("pressed_aggressive")),
        "status_label": status.get("status_label") or "",
        "trade_blockers": list(status.get("trade_blockers") or []),
        "top_ticker": status.get("top_ticker") or "",
        "risk_tier": status.get("risk_tier") or "",
        "standard_candidate_count": int(status.get("standard_candidate_count") or 0),
        "aggressive_probe_candidate_count": int(status.get("aggressive_probe_candidate_count") or 0),
        "stress_probe_candidate_count": int(status.get("stress_probe_candidate_count") or 0),
        "top_stress_ticker": status.get("top_stress_ticker") or "",
        "stress_position_sizing": status.get("stress_position_sizing") or {},
        "stress_report": status.get("stress_report") or {},
        "pressed_aggressive_candidate_count": int(status.get("pressed_aggressive_candidate_count") or 0),
        "graph_asymmetry_score": status.get("graph_asymmetry_score"),
        "probe_candidate_count": int(status.get("probe_candidate_count") or 0),
        "wait_for_entry_reset_count": int(status.get("wait_for_entry_reset_count") or 0),
        "adjudication_status": adjudication.get("status") or "",
        "adjudication_decision": adjudication.get("decision") or "",
        "adjudication_fallback": adjudication.get("fallback_policy") or "",
        "status_path": status.get("status_path") or "",
    }


def _overnight_component_status(report_date: str) -> Dict[str, Any]:
    corpus = _read_json(_APOLLO_ROOT / "logs" / "corpus_refresh" / "latest_corpus_refresh.json", {})
    hippo = _latest_json_status(f"logs/hipporag_improve/{report_date}/*/status.json")
    hippo_component = {
        "status": hippo.get("state") or ("complete" if hippo.get("ok") else ("missing_today" if not hippo else "failed")),
        "ok": hippo.get("ok") if "ok" in hippo else bool(hippo.get("build", {}).get("ok") and hippo.get("summary", {}).get("ok")),
        "run_id": hippo.get("run_id") or "",
        "message": hippo.get("message") or "",
        "status_path": hippo.get("status_path") or "",
    }
    return {
        "scheduled_nightly_0100": _nightly_scheduler_status(report_date),
        "embedded_trade_cycle_homework": _trade_cycle_status(report_date),
        "corpus_refresh": {
            "status": "complete" if corpus.get("ok") else ("missing_today" if not corpus else "failed"),
            "ok": corpus.get("ok"),
            "run_id": corpus.get("run_id") or "",
            "finished_at": corpus.get("finished_at") or "",
            "ingest_exit": (corpus.get("ingest") or {}).get("exit_code"),
            "graph_exit": (corpus.get("graph") or {}).get("exit_code"),
            "status_path": str(_APOLLO_ROOT / "logs" / "corpus_refresh" / "latest_corpus_refresh.json") if corpus else "",
        },
        "improved_hipporag": hippo_component,
        "hipporag_discovery": _hipporag_discovery_status(report_date, hippo_component),
    }


def _simulation_truth(account_payload: Dict[str, Any]) -> Dict[str, Any]:
    account = dict(account_payload.get("account") or {})
    warnings = list(account.get("simulation_warnings") or account_payload.get("simulation_warnings") or [])
    return {
        "account_value": account.get("market_value"),
        "cash_balance": account.get("cash_balance"),
        "open_positions": len([row for row in list(account.get("open_positions") or []) if isinstance(row, dict)]),
        "closed_positions": len([row for row in list(account.get("closed_positions") or []) if isinstance(row, dict)]),
        "math_warning_count": len(warnings),
        "warnings": warnings[:10],
        "confidence_summary_eligible": not bool(warnings),
    }


def _latest_homework_strategy_benchmark() -> Dict[str, Any]:
    root = _APOLLO_ROOT / "reports" / "homework_strategy_benchmark"
    if not root.exists():
        return {"status": "missing", "score": None, "report_path": "", "weak_areas": ["No homework/strategy benchmark report found."]}
    reports = sorted(root.glob("*/report.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for report_path in reports:
        try:
            text = report_path.read_text(encoding="utf-8")
        except Exception:
            continue
        score_match = re.search(r"(?im)^-\s*Score:\s*`?([0-9]+(?:\.[0-9]+)?)\s*/\s*100`?", text)
        status_match = re.search(r"(?im)^-\s*Status:\s*`?([^`\r\n]+)`?", text)
        completed_match = re.search(r"(?im)^-\s*Completed:\s*`?([^`\r\n]+)`?", text)
        weak_areas: List[str] = []
        weak_match = re.search(r"(?is)^##\s+Weak Areas\s*(.*?)(?:\n##\s+|\Z)", text)
        if weak_match:
            weak_areas = [
                item.strip("- ").strip()
                for item in weak_match.group(1).splitlines()
                if item.strip().startswith("-")
            ]
        return {
            "status": (status_match.group(1).strip() if status_match else "unknown"),
            "score": _safe_float(score_match.group(1), 0.0) if score_match else None,
            "completed_at": (completed_match.group(1).strip() if completed_match else ""),
            "report_path": str(report_path),
            "weak_areas": weak_areas,
        }
    return {"status": "missing", "score": None, "report_path": "", "weak_areas": ["No readable homework/strategy benchmark report found."]}


def _apollo_trade_score(payload: Dict[str, Any], account_payload: Dict[str, Any]) -> Dict[str, Any]:
    benchmark = _latest_homework_strategy_benchmark()
    simulation = _simulation_truth(account_payload)
    account = dict(account_payload.get("account") or payload.get("simulation_account") or {})
    plan = dict(account_payload.get("next_cycle_plan") or payload.get("next_cycle_plan") or {})
    nightly = dict(payload.get("nightly_truth") or {})
    homework = dict(payload.get("homework_trade") or {})
    score = 72
    gaps: List[str] = []
    benchmark_score = benchmark.get("score")
    if benchmark_score is None:
        score = min(score, 45)
        gaps.append("No current Apollo homework/trade strategy benchmark report was found.")
    elif _safe_float(benchmark_score) < 80:
        score = min(score, max(25, int(_safe_float(benchmark_score))))
        gaps.append(f"Latest homework/trade benchmark score is only {benchmark_score}/100.")
    if not bool(nightly.get("quality_gate_pass")):
        score = min(score, 68)
        reasons = ", ".join(str(item) for item in list(nightly.get("gate_fail_reasons") or []) if str(item).strip())
        gaps.append(f"Nightly quality gate did not pass{': ' + reasons if reasons else ''}.")
    if homework.get("missing_today"):
        score = min(score, 68)
        gaps.append("No same-day homework_trade proof artifact was found.")
    warning_count = int(simulation.get("math_warning_count") or 0)
    if warning_count:
        score = min(score, 64)
        gaps.append(f"Paper simulation has {warning_count} math warning(s), so confidence is capped.")
    if str(plan.get("status") or "") == "waiting_for_trade_ready_setup":
        score = min(score, 68)
        gaps.append("Current plan is waiting for a trade-ready setup; no new paper deployment is queued.")
    risk = dict(payload.get("risk_scaling") or {})
    if str(risk.get("risk_tier") or "") == "tier_0_hold":
        score = min(score, 64)
        reasons = ", ".join(str(item) for item in list(risk.get("rationale") or []) if str(item).strip())
        gaps.append(f"Risk ladder is holding new paper deployment{': ' + reasons if reasons else ''}.")
    if not gaps:
        gaps.append("No daily blockers detected beyond paper-simulation-only guardrails.")
    return {
        "score": int(score),
        "scale": 100,
        "label": "apollo_trade_readiness",
        "simulated_balance": account.get("market_value"),
        "cash_balance": account.get("cash_balance"),
        "invested_amount": account.get("invested_amount"),
        "open_positions": simulation.get("open_positions"),
        "closed_positions": simulation.get("closed_positions"),
        "benchmark": benchmark,
        "gaps": gaps,
        "rule": "Score is capped by the latest scoped benchmark, nightly quality truth, same-day homework proof, paper-sim math warnings, and whether a trade-ready setup exists.",
    }


def _refresh_truth_sections(payload: Dict[str, Any], account_payload: Dict[str, Any]) -> None:
    nightly = _load_latest_nightly()
    nightly_truth = _nightly_truth_summary(nightly)
    morning = dict(payload.get("morning_plan") or {})
    morning.update(
        {
            "nightly_run_id": nightly_truth.get("run_id") or "",
            "nightly_ok": nightly_truth.get("ok"),
            "nightly_stage": nightly_truth.get("current_stage") or "",
            "nightly_truth_label": nightly_truth.get("truth_label") or "",
            "nightly_pipeline_completed": bool(nightly_truth.get("pipeline_completed")),
            "nightly_quality_gate_pass": bool(nightly_truth.get("quality_gate_pass")),
            "nightly_gate_fail_reasons": list(nightly_truth.get("gate_fail_reasons") or []),
            "nightly_stage_truth": dict(nightly_truth.get("stage_truth") or {}),
            "nightly_run_dir": nightly_truth.get("run_dir") or "",
        }
    )
    payload["morning_plan"] = morning
    payload["nightly_truth"] = nightly_truth
    payload["homework_trade"] = _homework_trade_status(str(payload.get("date") or _ct_date())[:10])
    payload["high_risk_homework"] = _high_risk_homework_status()
    payload["graph_artifacts"] = _graph_plot_status(str(payload.get("date") or _ct_date())[:10])
    payload["overnight_components"] = _overnight_component_status(str(payload.get("date") or _ct_date())[:10])
    payload["hipporag_discovery"] = dict((payload.get("overnight_components") or {}).get("hipporag_discovery") or {})
    payload["simulation_truth"] = _simulation_truth(account_payload)
    learning = _load_latest_learning()
    learning_summary = dict(learning.get("summary") or {})
    payload["learning_summary"] = {
        "run_id": learning.get("run_id") or "",
        "created_at": learning.get("created_at") or "",
        "record_count": learning_summary.get("record_count", 0),
        "best_setup_types": list(learning_summary.get("best_setup_types") or [])[:3],
        "worst_setup_types": list(learning_summary.get("worst_setup_types") or [])[:3],
        "best_tickers": list(learning_summary.get("best_tickers") or [])[:3],
        "worst_tickers": list(learning_summary.get("worst_tickers") or [])[:3],
        "path": learning.get("path") or "",
        "latest_path": learning.get("latest_path") or "",
    }
    payload["score_adjustments_applied"] = list(learning_summary.get("score_adjustments_applied") or [])[:12]
    payload["learning_alerts"] = [
        str(item)
        for item in list(learning.get("alerts") or [])
        if str(item).strip()
    ]
    scheduled = dict((payload.get("overnight_components") or {}).get("scheduled_nightly_0100") or {})
    scheduler_failed = bool(scheduled.get("scheduler_failed"))
    recovered = bool(nightly_truth.get("quality_gate_pass") and nightly_truth.get("pipeline_completed"))
    scheduled["recovered_by_latest"] = bool(scheduler_failed and recovered)
    scheduled["recovery_needed"] = bool(scheduler_failed and not recovered)
    payload.setdefault("overnight_components", {})["scheduled_nightly_0100"] = scheduled
    if scheduled.get("recovery_needed"):
        payload["learning_alerts"].append("1 AM nightly failed and no passing recovery is recorded.")
        notice = dict(payload.get("nightly_recovery_notification") or {})
        if not notice.get("sent") and str(payload.get("date") or "") == _ct_date():
            payload["nightly_recovery_notification"] = _send_nightly_recovery_notification(payload, scheduled)
    account = dict(account_payload.get("account") or payload.get("simulation_account") or {})
    swing = _load_latest_swing()
    risk_policy = risk_scaling.evaluate_risk_tier(
        account=account,
        latest_study=swing,
        next_cycle_plan=dict(account_payload.get("next_cycle_plan") or payload.get("next_cycle_plan") or {}),
        latest_gate=dict(payload.get("latest_gate") or {}),
        nightly_truth=dict(payload.get("nightly_truth") or {}),
        homework_trade=dict(payload.get("homework_trade") or {}),
        learning_state=learning,
        enforce_external_gates=True,
    )
    payload["risk_scaling"] = risk_policy
    payload["risk_tier"] = risk_policy.get("risk_tier")
    payload["apollo_trade_score"] = _apollo_trade_score(payload, account_payload)


def ensure_daily_report(report_date: Optional[str] = None) -> Dict[str, Any]:
    paths = _paths(report_date)
    payload = _read_json(paths["json"], {})
    if not payload:
        payload = _initial_payload(report_date)
    payload.setdefault("events", [])
    payload.setdefault("guardrails", {})
    payload.setdefault("latest_gate", {})
    payload["updated_at"] = _utc_iso()
    account_payload = _load_sim_account()
    swing = _load_latest_swing()
    payload["simulation_account"] = dict(account_payload.get("account") or payload.get("simulation_account") or {})
    payload["next_cycle_plan"] = dict(account_payload.get("next_cycle_plan") or payload.get("next_cycle_plan") or {})
    payload["stress_sandbox_plan"] = dict(account_payload.get("stress_sandbox_plan") or payload.get("stress_sandbox_plan") or {})
    payload["swing_trade_summary"] = _swing_trade_summary(swing, dict(payload.get("next_cycle_plan") or {}))
    _refresh_truth_sections(payload, account_payload)
    payload["daily_balance_check"] = _daily_balance_check(payload, dict(payload.get("simulation_account") or {}))
    if _should_send_daily_balance_notification(payload):
        payload["daily_balance_notification"] = _send_daily_balance_notification(payload)
    _save_report(payload)
    return payload


def append_event(kind: str, event: Dict[str, Any], report_date: Optional[str] = None) -> Dict[str, Any]:
    payload = ensure_daily_report(report_date)
    entry = {
        "kind": str(kind or "event"),
        "recorded_at": _utc_iso(),
        "recorded_at_ct": _ct_stamp(),
        **dict(event or {}),
    }
    payload.setdefault("events", []).append(entry)
    if kind == "pretrade_gate":
        payload["latest_gate"] = dict(event or {})
    payload["events"] = list(payload.get("events") or [])[-200:]
    payload["updated_at"] = _utc_iso()
    if kind == "pretrade_gate":
        account_payload = _load_sim_account()
        payload["simulation_account"] = dict(account_payload.get("account") or payload.get("simulation_account") or {})
        payload["next_cycle_plan"] = dict(account_payload.get("next_cycle_plan") or payload.get("next_cycle_plan") or {})
        payload["swing_trade_summary"] = _swing_trade_summary(_load_latest_swing(), dict(payload.get("next_cycle_plan") or {}))
        _refresh_truth_sections(payload, account_payload)
    _save_report(payload)
    return payload


def record_gate_result(result: Dict[str, Any], report_date: Optional[str] = None) -> Dict[str, Any]:
    compact = {
        "run_id": result.get("run_id"),
        "trigger": result.get("trigger"),
        "ticker": (result.get("candidate") or {}).get("ticker"),
        "decision": result.get("decision"),
        "blocker": result.get("blocker"),
        "next_action": result.get("next_action"),
        "score": result.get("score"),
        "headlines_checked": len(list(result.get("headlines") or [])),
        "quote": result.get("quote"),
        "live_guard": result.get("live_guard"),
        "json_path": result.get("json_path"),
        "markdown_path": result.get("markdown_path"),
    }
    return append_event("pretrade_gate", compact, report_date=report_date)


def finalize_daily_report(report_date: Optional[str] = None, note: str = "") -> Dict[str, Any]:
    payload = ensure_daily_report(report_date)
    account = dict(payload.get("simulation_account") or {})
    plan = dict(payload.get("next_cycle_plan") or {})
    payload["final_summary"] = {
        "finalized_at": _utc_iso(),
        "finalized_at_ct": _ct_stamp(),
        "note": note or "Apollo daily report finalized after the market workflow.",
        "starting_balance": account.get("starting_balance"),
        "cash_balance": account.get("cash_balance"),
        "invested_amount": account.get("invested_amount"),
        "market_value": account.get("market_value"),
        "planned_investment": plan.get("planned_investment"),
        "planned_cash_reserve": plan.get("planned_cash_reserve"),
    }
    payload["updated_at"] = _utc_iso()
    _save_report(payload)
    _update_readme_completion(payload)
    return payload


def _event_lines(events: List[Dict[str, Any]]) -> List[str]:
    if not events:
        return ["- No checks or paper-sim actions have been recorded yet."]
    lines: List[str] = []
    for event in events:
        if event.get("kind") == "pretrade_gate":
            lines.append(
                "- "
                f"{event.get('recorded_at_ct', '')} pre-trade gate "
                f"{event.get('ticker') or '-'}: {event.get('decision') or '-'}; "
                f"trigger={event.get('trigger') or '-'}; "
                f"blocker={event.get('blocker') or 'none'}; "
                f"next={event.get('next_action') or '-'}; "
                f"headlines={event.get('headlines_checked', 0)}"
            )
        else:
            lines.append(f"- {event.get('recorded_at_ct', '')} {event.get('kind')}: {event.get('summary') or event}")
    return lines


def _swing_summary_lines(summary: Dict[str, Any]) -> List[str]:
    rows = [row for row in list(summary.get("rows") or []) if isinstance(row, dict)]
    if not rows:
        return [
            "- No swing setups are queued in the current paper plan.",
            "- Classification rule: Apollo must not relabel unqueued candidates as long-term low-risk holdings.",
        ]
    lines = [
        f"- Source swing study: `{summary.get('run_id') or 'none'}`",
        f"- Plan status: `{summary.get('plan_status') or 'unknown'}`",
        f"- Market regime: `{summary.get('market_regime') or 'unknown'}`",
        "- Classification: paper swing setups only; not long-term low-risk securities.",
        "",
    ]
    for row in rows:
        why = "; ".join(str(item) for item in list(row.get("why_swing") or []) if str(item).strip())
        not_long = " ".join(str(item) for item in list(row.get("why_not_long_term_low_risk") or []) if str(item).strip())
        lines.extend(
            [
                f"### {row.get('ticker') or 'Unknown'}",
                "",
                f"- Planned paper allocation: {_money(row.get('amount'))}",
                f"- Setup: `{str(row.get('setup_type') or '').replace('_', ' ') or 'unknown'}`; score `{row.get('score')}`; confidence `{row.get('confidence_label') or 'unknown'}`",
                f"- Trade levels: entry `{row.get('entry_zone') or '-'}`; stop `{row.get('stop_zone') or '-'}`; target `{row.get('target_zone') or '-'}`; R/R `{row.get('risk_reward')}`",
                f"- Horizon: `{row.get('time_horizon') or '2_to_20_trading_days'}`",
                f"- Real daily data: source `{row.get('source') or 'unknown'}` as_of `{row.get('as_of') or 'unknown'}`; ATR `{row.get('atr_pct')}`; RS rank `{row.get('relative_strength_rank')}`",
                f"- Why swing-worthy: {why or 'No swing rationale recorded.'}",
                f"- Why not long-term/low-risk: {not_long}",
                f"- Event/KG context: event risk `{row.get('event_risk') or 'unknown'}`; KG triples `{row.get('kg_triple_count')}`",
                "",
            ]
        )
    return lines


def _nightly_lines(payload: Dict[str, Any]) -> List[str]:
    nightly = dict(payload.get("nightly_truth") or {})
    if not nightly:
        return ["- No nightly status artifact was found."]
    label = str(nightly.get("truth_label") or "unknown")
    reasons = [str(item) for item in list(nightly.get("gate_fail_reasons") or []) if str(item).strip()]
    if label == "quality_failed":
        headline = f"Nightly stages completed, quality gate failed because {', '.join(reasons) or 'no concrete reason was recorded'}."
    elif label == "stale":
        latest = dict(nightly.get("latest_real_run") or {})
        headline = f"Nightly status is stale; latest real run is {latest.get('run_id') or 'unknown'}."
    elif label == "passed":
        headline = "Nightly pipeline passed all stage and quality checks."
    elif label == "partial":
        headline = "Nightly pipeline is partial; at least one stage has not completed."
    else:
        headline = "Nightly pipeline is blocked or missing required evidence."
    lines = [
        f"- {headline}",
        f"- Run: `{nightly.get('run_id') or 'none'}` label=`{label}` completed=`{nightly.get('pipeline_completed')}` quality_gate=`{nightly.get('quality_gate_pass')}`",
    ]
    if nightly.get("run_dir"):
        lines.append(f"- Run folder: `{nightly.get('run_dir')}`")
    ocr = dict(nightly.get("ocr") or {})
    if ocr:
        lines.append(f"- OCR failure class: `{ocr.get('failure_class') or 'unknown'}`; backend_ready=`{ocr.get('backend_ready')}`; evaluated_documents=`{ocr.get('evaluated_documents')}`")
    for name, truth in dict(nightly.get("stage_truth") or {}).items():
        row = dict(truth or {})
        lines.append(f"- {name}: ok=`{row.get('ok')}` completed=`{row.get('completed')}` result=`{row.get('result_path') or ''}`")
    return lines


def _homework_lines(payload: Dict[str, Any]) -> List[str]:
    status = dict(payload.get("homework_trade") or {})
    high_risk = dict(payload.get("high_risk_homework") or {})
    if status.get("missing_today"):
        lines = [
            "- Homework trade proof: `missing_today`.",
            f"- Expected artifact root: `{status.get('root') or ''}`",
            "- Guardrail: live_trade_execution=`false`; human_approval_required=`true`.",
        ]
    else:
        latest = dict(status.get("latest") or {})
        lines = [
            f"- Homework trade proof: `{status.get('status') or 'unknown'}`; runs today `{status.get('run_count') or 0}`.",
            f"- Latest plan: `{latest.get('plan_path') or ''}`",
            f"- Latest report: `{latest.get('report_path') or ''}`",
            "- Guardrail: live_trade_execution=`false`; human_approval_required=`true`.",
        ]
    if high_risk:
        lines.extend(
            [
                f"- High-risk homework: `{high_risk.get('status') or 'unknown'}`; action `{high_risk.get('projection_action') or 'none'}`; score `{high_risk.get('score')}`",
                f"- Confidence: source `{high_risk.get('source_confidence') or 'unknown'}` / event `{high_risk.get('event_confidence') or 'unknown'}` / graph `{high_risk.get('graph_usefulness') or 'unknown'}` / technical `{high_risk.get('technical_confirmation') or 'unknown'}`",
                f"- Top blocker hierarchy: `{', '.join(list(high_risk.get('blocker_hierarchy') or [])) or high_risk.get('top_blocker') or 'none'}`",
                f"- OCR97 backing: `{(high_risk.get('ocr97_status') or {}).get('status') or 'unknown'}` fresh=`{(high_risk.get('ocr97_status') or {}).get('fresh')}` quality_confident=`{(high_risk.get('ocr97_status') or {}).get('quality_confident')}`",
            ]
        )
        if high_risk.get("report_path"):
            lines.append(f"- High-risk homework report: `{high_risk.get('report_path')}`")
    return lines


def _graph_lines(payload: Dict[str, Any]) -> List[str]:
    status = dict(payload.get("graph_artifacts") or {})
    lines = [f"- {status.get('message') or 'no graph images generated today'}."]
    if status.get("summary_path"):
        lines.append(f"- Graph summary: `{status.get('summary_path')}`")
    for path in list(status.get("png_paths") or [])[:8]:
        lines.append(f"- Plot: `{path}`")
    if not status.get("summary_path"):
        lines.append(f"- Expected artifact root: `{status.get('root') or ''}`")
    return lines


def _overnight_component_lines(payload: Dict[str, Any]) -> List[str]:
    components = dict(payload.get("overnight_components") or {})
    scheduled = dict(components.get("scheduled_nightly_0100") or {})
    trade = dict(components.get("embedded_trade_cycle_homework") or {})
    corpus = dict(components.get("corpus_refresh") or {})
    hippo = dict(components.get("improved_hipporag") or {})
    lines = [
        f"- Scheduled 1:00 AM nightly: `{scheduled.get('status') or 'unknown'}`; stdout=`{scheduled.get('stdout_path') or ''}`",
        f"- Scheduled 1:00 AM failure detected: `{bool(scheduled.get('scheduler_failed'))}`; recovered by latest run: `{bool(scheduled.get('recovered_by_latest'))}`",
        f"- Embedded trade-cycle homework: `{trade.get('status') or 'unknown'}`; run=`{trade.get('run_id') or ''}`; label=`{trade.get('status_label') or ''}`; top=`{trade.get('top_ticker') or ''}`",
        f"- Trade adjudication: status=`{trade.get('adjudication_status') or ''}` decision=`{trade.get('adjudication_decision') or ''}` fallback=`{trade.get('adjudication_fallback') or ''}`",
        f"- Corpus refresh: `{corpus.get('status') or 'unknown'}`; ingest_exit=`{corpus.get('ingest_exit')}`; graph_exit=`{corpus.get('graph_exit')}`",
        f"- Improved HippoRAG: `{hippo.get('status') or 'unknown'}`; ok=`{hippo.get('ok')}`; run=`{hippo.get('run_id') or ''}`",
    ]
    discovery = dict(components.get("hipporag_discovery") or payload.get("hipporag_discovery") or {})
    if discovery:
        lines.append(
            f"- HippoRAG discovery: reported=`{discovery.get('reported_status') or ''}` actual=`{discovery.get('actual_latest_status') or ''}` mismatch=`{discovery.get('mismatch')}`"
        )
    blockers = [str(item) for item in list(trade.get("trade_blockers") or []) if str(item).strip()]
    if blockers:
        lines.append(f"- Trade-cycle top blocker: `{blockers[0]}`; all blockers `{', '.join(blockers)}`")
    if scheduled.get("notification_failure"):
        lines.append(f"- Notification issue: `{scheduled.get('notification_failure_note')}`; Apollo pipeline status should be judged from stage artifacts.")
    return lines


def _simulation_truth_lines(payload: Dict[str, Any]) -> List[str]:
    truth = dict(payload.get("simulation_truth") or {})
    warnings = [item for item in list(truth.get("warnings") or []) if isinstance(item, dict)]
    lines = [
        f"- Account value: {_money(truth.get('account_value'))}; cash: {_money(truth.get('cash_balance'))}",
        f"- Open positions: `{truth.get('open_positions', 0)}`; closed positions: `{truth.get('closed_positions', 0)}`",
        f"- Math warnings: `{truth.get('math_warning_count', 0)}`; confidence summaries eligible=`{truth.get('confidence_summary_eligible')}`",
    ]
    for warning in warnings[:5]:
        lines.append(
            f"- Warning: `{warning.get('warning_class') or warning.get('subcode') or warning.get('code') or 'simulation_math_warning'}` "
            f"on `{warning.get('ticker') or warning.get('sim_id') or 'unknown'}` - {warning.get('reason') or ''}"
        )
    return lines


def _apollo_trade_score_lines(payload: Dict[str, Any]) -> List[str]:
    score = dict(payload.get("apollo_trade_score") or {})
    benchmark = dict(score.get("benchmark") or {})
    gaps = [str(item) for item in list(score.get("gaps") or []) if str(item).strip()]
    lines = [
        f"- Daily trade readiness score: `{score.get('score', 0)}/{score.get('scale', 100)}`",
        f"- Simulated balance: {_money(score.get('simulated_balance'))}; cash: {_money(score.get('cash_balance'))}; invested: {_money(score.get('invested_amount'))}",
        f"- Positions: open `{score.get('open_positions', 0)}`; closed `{score.get('closed_positions', 0)}`",
        f"- Scoped homework/strategy benchmark: `{benchmark.get('score') if benchmark.get('score') is not None else 'missing'}/100`; status `{benchmark.get('status') or 'unknown'}`",
    ]
    if benchmark.get("report_path"):
        lines.append(f"- Benchmark report: `{benchmark.get('report_path')}`")
    lines.append(f"- Scoring rule: {score.get('rule') or 'No scoring rule recorded.'}")
    lines.append("- What holds Apollo back:")
    for gap in gaps:
        lines.append(f"- {gap}")
    return lines


def _aggressive_paper_risk_lines(payload: Dict[str, Any]) -> List[str]:
    trade = dict((payload.get("overnight_components") or {}).get("embedded_trade_cycle_homework") or {})
    lines = [
        f"- Aggressive probe ready: `{trade.get('aggressive_probe_ready')}`",
        f"- Aggressive candidate count: `{trade.get('aggressive_probe_candidate_count', 0)}`",
        f"- Pressed aggressive active: `{trade.get('pressed_aggressive')}`",
        f"- Pressed aggressive candidate count: `{trade.get('pressed_aggressive_candidate_count', 0)}`",
        f"- Top graph asymmetry score: `{trade.get('graph_asymmetry_score')}`",
        f"- Wait-for-entry-reset count: `{trade.get('wait_for_entry_reset_count', 0)}`",
        f"- Selected risk tier: `{trade.get('risk_tier') or 'none'}`",
        "- Guardrail: aggressive probes are paper-only; live_trade_execution=`false`; human_approval_required=`true`.",
    ]
    blockers = [str(item) for item in list(trade.get("trade_blockers") or []) if str(item).strip()]
    if blockers:
        lines.append(f"- Standard trade blockers still apply: `{', '.join(blockers)}`")
    return lines


def _risk_scaling_lines(payload: Dict[str, Any]) -> List[str]:
    risk = dict(payload.get("risk_scaling") or {})
    caps = dict(risk.get("allocation_caps") or {})
    truth = dict(risk.get("truth_gates") or {})
    failed = [key for key, value in truth.items() if not value]
    intent = dict(risk.get("active_paper_intent") or {})
    sources = dict(intent.get("sources") or {})
    lines = [
        f"- Current tier: `{risk.get('risk_tier') or 'tier_0_hold'}`",
        f"- Rationale: `{', '.join(str(item) for item in list(risk.get('rationale') or []) if str(item).strip()) or 'clean_current_state'}`",
        f"- Good-decision count: `{risk.get('good_decision_count', 0)}`",
        f"- Recent expectancy: `{risk.get('expectancy_pct', 0.0)}%`",
        f"- Max drawdown: `{risk.get('max_drawdown_pct', 0.0)}%`",
        f"- Next allocation caps: deploy `{caps.get('deploy_cap_pct', 0.0)}%`, max position `{caps.get('max_position_pct', 0.0)}%`",
        f"- Truth gates: `{', '.join(failed) if failed else 'clean'}`",
        f"- Active paper intent: `{intent.get('canonical_ticker') or 'unresolved'}`",
        "- Paper-only guardrail: live_trade_execution=`false`; human_approval_required=`true`.",
    ]
    if sources:
        source_text = "; ".join(f"{name}={','.join(values)}" for name, values in sources.items())
        lines.append(f"- Intent sources: `{source_text}`")
    return lines


def _stress_simulation_lines(payload: Dict[str, Any]) -> List[str]:
    plan = dict(payload.get("stress_sandbox_plan") or {})
    trade = dict(((payload.get("overnight_components") or {}).get("embedded_trade_cycle_homework")) or {})
    cfg = stress_mode.resolve_stress_mode()
    allocations = list(plan.get("allocations") or [])
    first = dict(allocations[0]) if allocations else {}
    sizing = dict(first.get("stress_position_sizing") or trade.get("stress_position_sizing") or {})
    report = dict(trade.get("stress_report") or {})
    return [
        f"- Production paper status: `{(payload.get('next_cycle_plan') or {}).get('status') or 'unknown'}`",
        f"- Stress sandbox status: `{plan.get('status') or ('stress_sandbox_ready' if trade.get('stress_probe_ready') else 'unknown')}`",
        f"- Stress mode: `{bool((plan.get('simulation_stress_mode') or {}).get('enabled') or trade.get('simulation_stress_mode') or cfg.get('enabled'))}`",
        f"- Candidate: `{first.get('ticker') or trade.get('top_stress_ticker') or 'none'}`",
        f"- Stress risk percent: `{plan.get('risk_pct') if plan.get('risk_pct') is not None else (round(_safe_float(sizing.get('risk_pct')) * 100, 2) if sizing else '')}`",
        f"- Planned notional: {_money(plan.get('planned_investment') if plan.get('planned_investment') is not None else sizing.get('planned_notional'))}",
        f"- Estimated fractional shares: `{first.get('estimated_shares') or sizing.get('fractional_shares') or ''}`",
        f"- Max loss at stop: {_money(sizing.get('max_loss_dollars'))}",
        f"- Stress report: `{report.get('markdown_path') or ''}`",
        "- Warning: stress simulation is sandbox evidence only and does not raise production trading readiness.",
    ]


def _learning_lines(payload: Dict[str, Any]) -> List[str]:
    learning = dict(payload.get("learning_summary") or {})
    adjustments = [dict(row) for row in list(payload.get("score_adjustments_applied") or []) if isinstance(row, dict)]

    def _names(rows: List[Dict[str, Any]]) -> str:
        parts = []
        for row in rows[:3]:
            parts.append(
                f"{row.get('value') or 'unknown'} "
                f"exp={row.get('expectancy_pct', 0.0)}% n={row.get('sample_count', 0)}"
            )
        return "; ".join(parts) or "none"

    lines = [
        f"- Learning run: `{learning.get('run_id') or 'none'}`",
        f"- Records evaluated: `{learning.get('record_count', 0)}`",
        f"- Best setup types: `{_names([dict(row) for row in list(learning.get('best_setup_types') or []) if isinstance(row, dict)])}`",
        f"- Worst setup types: `{_names([dict(row) for row in list(learning.get('worst_setup_types') or []) if isinstance(row, dict)])}`",
        f"- Best tickers: `{_names([dict(row) for row in list(learning.get('best_tickers') or []) if isinstance(row, dict)])}`",
        f"- Worst tickers: `{_names([dict(row) for row in list(learning.get('worst_tickers') or []) if isinstance(row, dict)])}`",
    ]
    if adjustments:
        rendered = []
        for row in adjustments[:5]:
            rendered.append(f"{row.get('key')}={row.get('recommended_score_adjustment')}")
        lines.append(f"- Active score adjustments: `{'; '.join(rendered)}`")
    else:
        lines.append("- Active score adjustments: `none`")
    alerts = [str(item) for item in list(payload.get("learning_alerts") or []) if str(item).strip()]
    if alerts:
        lines.append(f"- Learning alerts: `{'; '.join(alerts[:5])}`")
    lines.append("- Guardrail: learning adjusts paper scoring/ranking only; live_trade_execution=`false`.")
    return lines


def render_markdown(payload: Dict[str, Any]) -> str:
    morning = dict(payload.get("morning_plan") or {})
    account = dict(payload.get("simulation_account") or {})
    balance_check = dict(payload.get("daily_balance_check") or {})
    plan = dict(payload.get("next_cycle_plan") or {})
    swing_summary = dict(payload.get("swing_trade_summary") or {})
    latest_gate = dict(payload.get("latest_gate") or {})
    final = dict(payload.get("final_summary") or {})
    previous_balance = balance_check.get("previous_end_balance")
    previous_balance_text = _money(previous_balance) if previous_balance is not None else "unavailable"
    continuity_status = balance_check.get("status") or "unchecked"
    lines = [
        f"# Apollo Daily Report - {payload.get('date')}",
        "",
        f"- Updated: {_ct_stamp()}",
        "- Mode: paper_sim only",
        "- Live broker execution: disabled",
        "- Daytime HippoRAG rebuild: disabled",
        "",
        "## Morning Plan",
        "",
        f"- Nightly run: `{morning.get('nightly_run_id') or 'none'}` ok=`{morning.get('nightly_ok')}` stage=`{morning.get('nightly_stage') or ''}`",
        f"- Nightly truth: `{morning.get('nightly_truth_label') or 'unknown'}`; pipeline_completed=`{morning.get('nightly_pipeline_completed')}`; quality_gate_pass=`{morning.get('nightly_quality_gate_pass')}`",
        f"- Swing study: `{morning.get('swing_run_id') or 'none'}`",
        f"- Best setup: `{morning.get('best_ticker') or 'none'}` score `{morning.get('best_score')}`",
        f"- Market regime: `{morning.get('market_regime') or 'unknown'}`",
        "",
        "## Apollo Trade Score",
        "",
        *_apollo_trade_score_lines(payload),
        "",
        "## Paper Risk Ladder",
        "",
        *_risk_scaling_lines(payload),
        "",
        "## What Apollo Learned Today",
        "",
        *_learning_lines(payload),
        "",
        "## Aggressive Paper Risk",
        "",
        *_aggressive_paper_risk_lines(payload),
        "",
        "## Apollo Stress Simulation",
        "",
        *_stress_simulation_lines(payload),
        "",
        "## Nightly Truth",
        "",
        *_nightly_lines(payload),
        "",
        "## Overnight Components",
        "",
        *_overnight_component_lines(payload),
        "",
        "## Simulated Account",
        "",
        f"- Daily start balance: {_money(balance_check.get('start_balance', account.get('market_value')))}",
        f"- Yesterday ending balance: {previous_balance_text} (`{continuity_status}`)",
        f"- Balance continuity matched: `{bool(balance_check.get('reconciled'))}`",
        f"- Starting balance: {_money(account.get('starting_balance'))}",
        f"- Cash balance: {_money(account.get('cash_balance'))}",
        f"- Invested: {_money(account.get('invested_amount'))}",
        f"- Market value: {_money(account.get('market_value'))}",
        f"- Realized return: {_money(account.get('realized_return'))} ({account.get('realized_return_pct', 0)}%)",
        f"- Unrealized return: {_money(account.get('unrealized_return'))} ({account.get('unrealized_return_pct', 0)}%)",
        "",
        "## Simulation Truth",
        "",
        *_simulation_truth_lines(payload),
        "",
        "## Homework Trade Proof",
        "",
        *_homework_lines(payload),
        "",
        "## Graph/Plot Artifacts",
        "",
        *_graph_lines(payload),
        "",
        "## Current Plan",
        "",
        f"- Status: `{plan.get('status') or 'unknown'}`",
        f"- Planned investment: {_money(plan.get('planned_investment'))}",
        f"- Cash reserve: {_money(plan.get('planned_cash_reserve'))}",
        f"- Note: {plan.get('note') or 'No next-cycle note recorded.'}",
        "",
        "## Swing Trade Rationale",
        "",
        *_swing_summary_lines(swing_summary),
        "",
        "## Latest Pre-Trade Gate",
        "",
        f"- Ticker: `{latest_gate.get('ticker') or 'none'}`",
        f"- Decision: `{latest_gate.get('decision') or 'none'}`",
        f"- Blocker: `{latest_gate.get('blocker') or 'none'}`",
        f"- Next action: {latest_gate.get('next_action') or 'No action recorded.'}",
        "",
        "## Timeline",
        "",
        *_event_lines([dict(row) for row in list(payload.get("events") or []) if isinstance(row, dict)]),
        "",
        "## Final Summary",
        "",
        f"- Finalized: `{final.get('finalized_at_ct') or 'not finalized'}`",
        f"- Note: {final.get('note') or 'Report remains open for today.'}",
        "",
        "This report is informational and paper-simulation only. It does not place live orders.",
    ]
    return "\n".join(lines).strip() + "\n"


def _save_report(payload: Dict[str, Any]) -> None:
    paths = _paths(str(payload.get("date") or _ct_date()))
    payload["markdown_path"] = str(paths["md"])
    payload["json_path"] = str(paths["json"])
    _write_json(paths["json"], payload)
    paths["md"].write_text(render_markdown(payload), encoding="utf-8")
    paths["latest_json"].parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(paths["json"], paths["latest_json"])
    shutil.copyfile(paths["md"], paths["latest_md"])


def _readme_completion_block(payload: Dict[str, Any]) -> str:
    final = dict(payload.get("final_summary") or {})
    gate = dict(payload.get("latest_gate") or {})
    balance = dict(payload.get("daily_balance_check") or {})
    report_date = str(payload.get("date") or _ct_date()).strip()[:10] or _ct_date()
    report_path = str(payload.get("markdown_path") or "")
    return "\n".join(
        [
            README_START,
            "## Apollo Daily Report Completion Log",
            "",
            f"- Date: `{report_date}`",
            f"- Finalized: `{final.get('finalized_at_ct') or ''}`",
            f"- Report: `{report_path}`",
            f"- Latest gate: decision=`{gate.get('decision') or 'none'}` blocker=`{gate.get('blocker') or 'none'}` next=`{gate.get('next_action') or 'n/a'}`",
            f"- Balance check: status=`{balance.get('status') or 'unknown'}` reconciled=`{bool(balance.get('reconciled'))}`",
            f"- Note: `{final.get('note') or ''}`",
            "",
            "This block is replaced on each finalized report so README.md always shows the most recent completion day.",
            README_END,
        ]
    )


def _update_readme_completion(payload: Dict[str, Any]) -> None:
    try:
        block = _readme_completion_block(payload)
        README_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else "# Apollo\n"
        if README_START in existing and README_END in existing:
            before = existing.split(README_START, 1)[0].rstrip()
            after = existing.split(README_END, 1)[1].lstrip()
            updated = f"{before}\n\n{block}\n\n{after}".rstrip() + "\n"
        else:
            updated = existing.rstrip() + "\n\n" + block + "\n"
        README_PATH.write_text(updated, encoding="utf-8")
    except Exception:
        pass


def get_daily_report(report_date: Optional[str] = None) -> Dict[str, Any]:
    paths = _paths(report_date)
    payload = _read_json(paths["json"], {})
    if not payload:
        payload = ensure_daily_report(report_date)
    markdown = ""
    try:
        markdown = paths["md"].read_text(encoding="utf-8")
    except Exception:
        markdown = render_markdown(payload)
    return {"ok": True, "date": payload.get("date"), "report": payload, "markdown": markdown, "paths": {"json": str(paths["json"]), "markdown": str(paths["md"])}}


def latest_daily_report() -> Dict[str, Any]:
    payload = _read_json(REPORT_ROOT / "latest_apollo_daily_report.json", {})
    if not payload:
        return get_daily_report()
    markdown = ""
    try:
        markdown = (REPORT_ROOT / "latest_apollo_daily_report.md").read_text(encoding="utf-8")
    except Exception:
        markdown = render_markdown(payload)
    return {"ok": True, "date": payload.get("date"), "report": payload, "markdown": markdown}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Apollo daily report writer.")
    parser.add_argument("action", nargs="?", default="open", choices=["open", "finalize", "status"])
    parser.add_argument("--date", default="")
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    if args.action == "finalize":
        result = finalize_daily_report(args.date or None, note=args.note)
    elif args.action == "status":
        result = get_daily_report(args.date or None)
    else:
        result = ensure_daily_report(args.date or None)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
