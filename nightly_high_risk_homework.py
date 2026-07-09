from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


APOLLO_ROOT = Path(__file__).resolve().parent
ENGINEERING_ROOT = APOLLO_ROOT.parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "nightly_high_risk_homework"

DEFAULT_TICKERS = "AAPL,MSFT,NVDA,AMD,AVGO,TSLA,META,GOOGL,AMZN,COIN,UBER,DDOG,NET,SNOW,SHOP,CRWD,PLTR,RTX,BAC,JPM"

PHASE_ORDER = [
    "post_close_market_snapshot",
    "news_catalyst_gather",
    "event_dislocation_study",
    "high_risk_paper_screen",
    "graph_rag_enrichment",
    "technical_confirmation_trade_cycle",
    "pressed_aggressive_projection",
    "deep_daily_report",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    return f"apollo_high_risk_homework_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _confidence_rank(value: Any) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(str(value or "").strip().lower(), 0)


def _graph_usefulness_label(detail: Dict[str, Any]) -> str:
    label = str(detail.get("hipporag_label") or "").strip() or "unknown"
    if label == "supporting_context_only":
        return "supporting_context_only"
    if label == "graph_augmented":
        dense = _safe_float(detail.get("dense_only_avg"))
        graph = _safe_float(detail.get("dense_graph_avg"))
        return "graph_augmented" if graph > dense else "supporting_context_only"
    return label


def _event_source_confidence(detail: Dict[str, Any]) -> str:
    best = dict(detail.get("best_event_impact") or {})
    top = dict(best.get("top_event") or {})
    quality = _safe_float(top.get("source_quality"))
    if quality >= 80:
        return "high"
    if quality >= 65:
        return "medium"
    if quality > 0:
        return "low"
    return "none"


def _load_ocr97_status() -> Dict[str, Any]:
    try:
        if str(ENGINEERING_ROOT) not in sys.path:
            sys.path.insert(0, str(ENGINEERING_ROOT))
        from Sky.services import test_run_reports

        ledger = test_run_reports.load_project_capability_ledger()
        project = dict((ledger.get("projects") or {}).get("ocr97") or {})
        latest = {
            "status": str(project.get("latest_status") or ""),
            "score": project.get("latest_score"),
            "completed_at": str(project.get("latest_completed_at") or ""),
            "report_path": str(project.get("latest_report_path") or ""),
        }
        completed = latest["completed_at"]
        age_hours = None
        if completed:
            try:
                parsed = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_hours = round((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0, 2)
            except Exception:
                age_hours = None
        latest["age_hours"] = age_hours
        latest["fresh"] = bool(age_hours is not None and age_hours <= 168)
        latest["quality_confident"] = bool(
            latest["fresh"] and _safe_float(latest.get("score")) >= 85.0
        )
        return latest
    except Exception as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}:{exc}", "fresh": False, "quality_confident": False}


def _blocker_hierarchy(context: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    trade = dict(context.get("phase_outputs", {}).get("technical_confirmation_trade_cycle") or {})
    graph = dict(context.get("phase_outputs", {}).get("graph_rag_enrichment") or {})
    event = dict(context.get("phase_outputs", {}).get("news_catalyst_gather") or {})
    ocr97 = dict(context.get("ocr97_status") or {})
    source_confidence = _event_source_confidence(event)
    if _confidence_rank(source_confidence) <= 1:
        blockers.append("source_quality")
    if not bool(ocr97.get("fresh")) or not bool(ocr97.get("quality_confident")):
        blockers.append("ocr_document_quality")
    if _graph_usefulness_label(graph) == "supporting_context_only":
        blockers.append("hipporag_usefulness")
    adjudication = dict(trade.get("deep_trade_adjudication") or {})
    if str(adjudication.get("status") or "").strip().lower() == "unavailable":
        blockers.append("model_adjudication_availability")
    evidence = dict(trade.get("evidence_quality") or {})
    if str(evidence.get("news_evidence") or "").strip().lower() not in {"good", "partial"}:
        blockers.append("price_volume_confirmation")
    elif str(evidence.get("news_source_quality") or "").strip().lower() == "c_tier_only_yahoo_rss":
        blockers.append("price_volume_confirmation")
    if list(trade.get("trade_blockers") or []):
        blockers.append("trade_risk_gate")
    ordered: List[str] = []
    for item in blockers:
        if item not in ordered:
            ordered.append(item)
    return ordered


def _confidence_model(context: Dict[str, Any]) -> Dict[str, Any]:
    event = dict(context.get("phase_outputs", {}).get("news_catalyst_gather") or {})
    trade = dict(context.get("phase_outputs", {}).get("technical_confirmation_trade_cycle") or {})
    graph = dict(context.get("phase_outputs", {}).get("graph_rag_enrichment") or {})
    source_confidence = _event_source_confidence(event)
    event_confidence = "high" if bool(event.get("high_confidence")) else ("medium" if _confidence_rank(source_confidence) >= 2 else "low")
    graph_usefulness = _graph_usefulness_label(graph)
    tech_status = str(trade.get("status_label") or "").strip().lower()
    technical_confirmation = "high" if tech_status == "trade_ready" else ("medium" if tech_status in {"aggressive_probe_ready", "probe_ready"} else "low")
    blocker_hierarchy = _blocker_hierarchy(context)
    return {
        "source_confidence": source_confidence,
        "event_confidence": event_confidence,
        "graph_usefulness": graph_usefulness,
        "technical_confirmation": technical_confirmation,
        "top_blocker": blocker_hierarchy[0] if blocker_hierarchy else "",
        "blocker_hierarchy": blocker_hierarchy,
        "ocr97": dict(context.get("ocr97_status") or {}),
    }


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _clip(value: Any, limit: int = 3000) -> str:
    return str(value or "").strip()[:limit].rstrip()


def _tickers(raw: str = "") -> List[str]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Apollo import market_data

    configured = raw or os.getenv("APOLLO_NIGHTLY_HIGH_RISK_TICKERS") or os.getenv("APOLLO_WATCHLIST") or DEFAULT_TICKERS
    base = market_data.normalize_tickers({"tickers": configured})
    extra = market_data.normalize_tickers({"tickers": os.getenv("APOLLO_AGGRESSIVE_EXTRA_WATCHLIST", "")})
    out: List[str] = []
    for ticker in [*base, *extra]:
        if ticker and ticker not in out:
            out.append(ticker)
    return out[:40]


def _phase_status(result: Dict[str, Any], required: bool = True) -> str:
    if result.get("skipped"):
        return "skipped"
    if result.get("ok") is True:
        return "passed"
    if required:
        return "failed"
    return "partial"


def _phase_markdown(phase: Dict[str, Any]) -> str:
    detail = phase.get("detail") if isinstance(phase.get("detail"), dict) else {}
    lines = [
        f"# Apollo High-Risk Homework Phase - {phase.get('name')}",
        "",
        f"- Status: `{phase.get('status')}`",
        f"- Started: `{phase.get('started_at')}`",
        f"- Finished: `{phase.get('finished_at')}`",
        f"- Duration sec: `{phase.get('duration_sec')}`",
        "",
        "## Summary",
        "",
        str(phase.get("summary") or ""),
        "",
        "## Detail",
        "",
        "```json",
        json.dumps(detail, indent=2, ensure_ascii=False)[:12000],
        "```",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _write_phase(run_dir: Path, index: int, phase: Dict[str, Any]) -> Dict[str, Any]:
    safe_name = str(phase.get("name") or f"phase_{index}").replace(" ", "_")
    phase_dir = run_dir / "phase_reports"
    json_path = phase_dir / f"{index:02d}_{safe_name}.json"
    md_path = phase_dir / f"{index:02d}_{safe_name}.md"
    phase["json_path"] = str(json_path)
    phase["markdown_path"] = str(md_path)
    _write_json(json_path, phase)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_phase_markdown(phase), encoding="utf-8")
    return phase


def _run_phase(
    run_dir: Path,
    index: int,
    name: str,
    fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    context: Dict[str, Any],
    *,
    required: bool = True,
) -> Dict[str, Any]:
    started = _utc_iso()
    t0 = time.monotonic()
    try:
        detail = fn(context)
    except Exception as exc:
        detail = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
    finished = _utc_iso()
    status = _phase_status(detail, required=required)
    phase = {
        "name": name,
        "status": status,
        "ok": status == "passed",
        "required": required,
        "started_at": started,
        "finished_at": finished,
        "duration_sec": round(time.monotonic() - t0, 2),
        "summary": _summarize_phase(name, detail),
        "detail": detail,
    }
    phase = _write_phase(run_dir, index, phase)
    context.setdefault("phases", []).append(phase)
    context["phase_outputs"][name] = detail
    return phase


def _summarize_phase(name: str, detail: Dict[str, Any]) -> str:
    if detail.get("skipped"):
        return str(detail.get("reason") or "Skipped.")
    if name == "post_close_market_snapshot":
        return f"{detail.get('ok_count', 0)} of {detail.get('ticker_count', 0)} market snapshots were usable."
    if name == "news_catalyst_gather":
        return f"{detail.get('summary_count', 0)} event-impact summaries gathered; high_confidence={detail.get('high_confidence')}."
    if name == "event_dislocation_study":
        return f"{detail.get('proposal_count', 0)} swing proposals built; best={detail.get('best_ticker') or 'none'}."
    if name == "high_risk_paper_screen":
        return f"{detail.get('aggressive_count', 0)} aggressive paper candidates, {detail.get('pressed_count', 0)} pressed candidates."
    if name == "graph_rag_enrichment":
        return f"HippoRAG label={detail.get('hipporag_label')}; dense+graph={detail.get('dense_graph_avg')} dense={detail.get('dense_only_avg')}."
    if name == "technical_confirmation_trade_cycle":
        return f"Trade cycle status={detail.get('status_label')}; top={detail.get('top_ticker') or 'none'}."
    if name == "pressed_aggressive_projection":
        return f"Projection action={detail.get('next_day_projection', {}).get('primary_action') or 'none'}."
    if name == "deep_daily_report":
        return f"Daily report path={detail.get('markdown_path') or 'unavailable'}."
    return "Phase complete." if detail.get("ok") else f"Phase issue: {detail.get('error') or 'unknown'}"


def _phase_market_snapshot(context: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo import market_data

    tickers = context["tickers"]
    result = market_data.market_snapshot({"tickers": tickers, "refresh": True})
    snapshots = [row for row in list(result.get("snapshots") or []) if isinstance(row, dict)]
    ok_rows = [row for row in snapshots if row.get("ok")]
    high_rel = [
        {
            "ticker": row.get("ticker"),
            "price": row.get("price"),
            "gap_pct": row.get("gap_pct"),
            "relative_volume": row.get("relative_volume"),
            "freshness": row.get("freshness"),
        }
        for row in snapshots
        if _safe_float(row.get("relative_volume")) >= 1.25 or abs(_safe_float(row.get("gap_pct"))) >= 2.0
    ]
    return {
        "ok": bool(result.get("ok")),
        "ticker_count": len(tickers),
        "ok_count": len(ok_rows),
        "high_motion_count": len(high_rel),
        "high_motion": high_rel[:12],
        "source_health": result.get("source_health") or {},
    }


def _phase_event_impact(context: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo import event_impact

    result = event_impact.build_event_impact(
        {
            "tickers": context["tickers"],
            "max_results": int(context.get("event_max_results") or 8),
            "write_artifacts": True,
        }
    )
    summaries = [row for row in list(result.get("summaries") or []) if isinstance(row, dict)]
    return {
        "ok": bool(result.get("ok", True)),
        "run_id": result.get("run_id"),
        "summary_count": len(summaries),
        "high_confidence": bool(result.get("high_confidence")),
        "source_confidence": _event_source_confidence({"best_event_impact": result.get("best_event_impact") or {}}),
        "best_event_impact": result.get("best_event_impact") or {},
        "top_events": summaries[:10],
        "artifacts": result.get("artifacts") or {},
    }


def _phase_swing_study(context: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo import swing_study

    old_event = os.environ.get("APOLLO_EVENT_IMPACT_ENABLED")
    old_allow = os.environ.get("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES")
    os.environ["APOLLO_EVENT_IMPACT_ENABLED"] = "1"
    os.environ["APOLLO_EVENT_ALLOW_PAPER_CANDIDATES"] = "1"
    try:
        result = swing_study.build_swing_study(
            {
                "tickers": context["tickers"],
                "include_event_impact": True,
                "event_max_results": int(context.get("event_max_results") or 8),
                "write_artifacts": True,
                "refresh": True,
                "data_quality_band": "nightly_high_risk_homework",
            }
        )
    finally:
        if old_event is None:
            os.environ.pop("APOLLO_EVENT_IMPACT_ENABLED", None)
        else:
            os.environ["APOLLO_EVENT_IMPACT_ENABLED"] = old_event
        if old_allow is None:
            os.environ.pop("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES", None)
        else:
            os.environ["APOLLO_EVENT_ALLOW_PAPER_CANDIDATES"] = old_allow
    proposals = [row for row in list(result.get("proposals") or []) if isinstance(row, dict)]
    best = dict(result.get("best_proposal") or {})
    return {
        "ok": bool(result.get("ok")),
        "run_id": result.get("run_id"),
        "proposal_count": len(proposals),
        "best_ticker": best.get("ticker"),
        "best_score": best.get("score"),
        "best_risk_reward": best.get("risk_reward_estimate"),
        "best_proposal": best,
        "top_proposals": proposals[:12],
        "artifacts": result.get("artifacts") or {},
    }


def _phase_high_risk_screen(context: Dict[str, Any]) -> Dict[str, Any]:
    study = dict(context.get("phase_outputs", {}).get("event_dislocation_study") or {})
    proposals = [row for row in list(study.get("top_proposals") or []) if isinstance(row, dict)]
    aggressive: List[Dict[str, Any]] = []
    pressed: List[Dict[str, Any]] = []
    entry_reset: List[Dict[str, Any]] = []
    for row in proposals:
        rr = _safe_float(row.get("risk_reward_estimate"))
        graph = dict(row.get("graph_risk_appetite") or {})
        reward = dict(row.get("aggressive_reward_profile") or {})
        event_score = _safe_float((row.get("event_impact") or {}).get("impact_score"))
        volume_ratio = _safe_float((row.get("volume_confirmation") or {}).get("volume_ratio"))
        setup = {
            "ticker": row.get("ticker"),
            "score": row.get("score"),
            "rr": rr,
            "event_score": event_score,
            "volume_ratio": volume_ratio,
            "graph_asymmetry_score": graph.get("score"),
            "direction_bias": row.get("direction_bias"),
            "entry_zone": row.get("entry_zone"),
            "stop_zone": row.get("stop_zone"),
            "target_zone": row.get("target_zone"),
            "pressed_graph_reward": bool(reward.get("pressed_candidate")),
        }
        if rr >= 2.0 and _safe_float(graph.get("score")) >= 65 and bool(reward.get("pressed_candidate")):
            pressed.append({**setup, "next_action": "paper_pressed_aggressive_ready"})
        elif rr >= 1.5 and (event_score >= 65 or volume_ratio >= 1.25 or _safe_float(graph.get("score")) >= 65):
            aggressive.append({**setup, "next_action": "paper_aggressive_ready"})
        elif _safe_float(row.get("score")) >= 70:
            entry_reset.append({**setup, "next_action": "wait_for_entry_reset"})
    return {
        "ok": True,
        "aggressive_count": len(aggressive),
        "pressed_count": len(pressed),
        "entry_reset_count": len(entry_reset),
        "pressed_candidates": pressed[:10],
        "aggressive_candidates": aggressive[:10],
        "wait_for_entry_reset": entry_reset[:10],
        "paper_only": True,
        "human_approval_required": True,
        "live_trade_execution": False,
    }


def _phase_graph_rag(context: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo import hipporag_benchmark

    result = hipporag_benchmark.run_live_benchmark()
    result["ok"] = True
    return result


def _phase_trade_cycle(context: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo import trade_cycle

    old_event = os.environ.get("APOLLO_EVENT_IMPACT_ENABLED")
    old_allow = os.environ.get("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES")
    old_aggr = os.environ.get("APOLLO_AGGRESSIVE_PAPER_ENABLED")
    old_pressed = os.environ.get("APOLLO_PRESSED_AGGRESSIVE_PAPER_ENABLED")
    os.environ["APOLLO_EVENT_IMPACT_ENABLED"] = "1"
    os.environ["APOLLO_EVENT_ALLOW_PAPER_CANDIDATES"] = "1"
    os.environ["APOLLO_AGGRESSIVE_PAPER_ENABLED"] = "1"
    os.environ["APOLLO_PRESSED_AGGRESSIVE_PAPER_ENABLED"] = "1"
    captured_stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_stdout):
            result = trade_cycle.run_cycle()
    finally:
        for key, value in {
            "APOLLO_EVENT_IMPACT_ENABLED": old_event,
            "APOLLO_EVENT_ALLOW_PAPER_CANDIDATES": old_allow,
            "APOLLO_AGGRESSIVE_PAPER_ENABLED": old_aggr,
            "APOLLO_PRESSED_AGGRESSIVE_PAPER_ENABLED": old_pressed,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {
        "ok": bool(result.get("ok")),
        "run_id": result.get("run_id"),
        "status_label": result.get("status_label"),
        "top_ticker": result.get("top_ticker"),
        "risk_tier": result.get("risk_tier"),
        "trade_ready": bool(result.get("trade_ready")),
        "probe_ready": bool(result.get("probe_ready")),
        "aggressive_probe_ready": bool(result.get("aggressive_probe_ready")),
        "stress_probe_ready": bool(result.get("stress_probe_ready")),
        "simulation_stress_mode": bool(result.get("simulation_stress_mode")),
        "standard_candidate_count": result.get("standard_candidate_count"),
        "probe_candidate_count": result.get("probe_candidate_count"),
        "aggressive_probe_candidate_count": result.get("aggressive_probe_candidate_count"),
        "stress_probe_candidate_count": result.get("stress_probe_candidate_count"),
        "top_stress_ticker": result.get("top_stress_ticker"),
        "stress_position_sizing": result.get("stress_position_sizing") or {},
        "stress_report": result.get("stress_report") or {},
        "pressed_aggressive_candidate_count": result.get("pressed_aggressive_candidate_count"),
        "pressed_aggressive": bool(result.get("pressed_aggressive")),
        "graph_asymmetry_score": result.get("graph_asymmetry_score"),
        "trade_blockers": result.get("trade_blockers") or [],
        "evidence_quality": result.get("evidence_quality") or {},
        "deep_trade_adjudication": result.get("deep_trade_adjudication") or {},
        "captured_log": _clip(captured_stdout.getvalue(), 5000),
    }


def _build_projection(context: Dict[str, Any]) -> Dict[str, Any]:
    screen = dict(context.get("phase_outputs", {}).get("high_risk_paper_screen") or {})
    trade = dict(context.get("phase_outputs", {}).get("technical_confirmation_trade_cycle") or {})
    graph = dict(context.get("phase_outputs", {}).get("graph_rag_enrichment") or {})
    pressed = list(screen.get("pressed_candidates") or [])
    aggressive = list(screen.get("aggressive_candidates") or [])
    reset = list(screen.get("wait_for_entry_reset") or [])
    blockers = list(trade.get("trade_blockers") or [])
    confidence = _confidence_model(context)
    strong_source = _confidence_rank(confidence.get("source_confidence")) >= 2
    graph_adds_lift = str(confidence.get("graph_usefulness") or "") == "graph_augmented"
    weak_ocr = not bool((confidence.get("ocr97") or {}).get("fresh")) or not bool((confidence.get("ocr97") or {}).get("quality_confident"))
    if pressed:
        primary_action = "paper_pressed_aggressive_probe"
        primary = pressed[0]
    elif trade.get("aggressive_probe_ready"):
        primary_action = "paper_aggressive_probe"
        primary = aggressive[0] if aggressive else {"ticker": trade.get("top_ticker")}
    elif trade.get("probe_ready"):
        primary_action = "paper_probe"
        primary = {"ticker": trade.get("top_ticker"), "risk_tier": trade.get("risk_tier")}
    elif trade.get("stress_probe_ready"):
        primary_action = "stress_sandbox_probe"
        primary = {
            "ticker": trade.get("top_stress_ticker") or trade.get("top_ticker"),
            "risk_tier": "stress_sandbox",
            "stress_position_sizing": trade.get("stress_position_sizing") or {},
        }
    elif reset:
        primary_action = "wait_for_entry_reset"
        primary = reset[0]
    else:
        primary_action = "observe_until_confirmation"
        primary = {"ticker": trade.get("top_ticker") or "", "blockers": blockers}
    if primary_action in {"paper_pressed_aggressive_probe", "paper_aggressive_probe"}:
        if not strong_source:
            primary_action = "watch_for_volume_confirmation"
        elif not graph_adds_lift:
            primary_action = "wait_for_entry_reset"
        elif weak_ocr:
            primary_action = "watch_for_volume_confirmation"
    return {
        "ok": True,
        "generated_at": _utc_iso(),
        "primary_action": primary_action,
        "primary_candidate": primary,
        "morning_refresh_required": True,
        "open_behavior": "wait_15_to_30_min_for_price_volume_confirmation",
        "paper_only": True,
        "human_approval_required": True,
        "live_trade_execution": False,
        "risk_posture": "pressed_simulation_only" if primary_action == "paper_pressed_aggressive_probe" else "measured_simulation_only",
        "stress_sandbox": {
            "enabled": bool(trade.get("simulation_stress_mode")),
            "ready": bool(trade.get("stress_probe_ready")),
            "candidate_count": trade.get("stress_probe_candidate_count"),
            "top_ticker": trade.get("top_stress_ticker"),
            "position_sizing": trade.get("stress_position_sizing") or {},
            "report": trade.get("stress_report") or {},
            "production_readiness_credit": False,
        },
        "graph_rag_mode": graph.get("hipporag_label") or "unknown",
        "graph_fail_soft": bool(graph.get("fail_soft")),
        "blockers": blockers,
        "confidence_model": confidence,
        "candidate_counts": {
            "pressed": len(pressed),
            "aggressive": len(aggressive),
            "wait_for_entry_reset": len(reset),
            "trade_cycle_aggressive": trade.get("aggressive_probe_candidate_count"),
            "trade_cycle_stress": trade.get("stress_probe_candidate_count"),
            "trade_cycle_pressed": trade.get("pressed_aggressive_candidate_count"),
        },
    }


def _phase_projection(context: Dict[str, Any]) -> Dict[str, Any]:
    projection = _build_projection(context)
    return {"ok": True, "next_day_projection": projection}


def _phase_daily_report(context: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo import daily_report

    result = daily_report.ensure_daily_report()
    paths = {
        "markdown": result.get("markdown_path") or str((daily_report.REPORT_ROOT / "latest_apollo_daily_report.md")),
        "json": result.get("json_path") or str((daily_report.REPORT_ROOT / "latest_apollo_daily_report.json")),
    }
    return {
        "ok": True,
        "date": result.get("date"),
        "markdown_path": paths["markdown"],
        "json_path": paths["json"],
    }


def _run_subprocess(command: List[str], *, timeout_sec: int) -> Dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(ENGINEERING_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    parsed: Dict[str, Any] = {}
    try:
        parsed = json.loads(proc.stdout)
    except Exception:
        parsed = {}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": subprocess.list2cmdline(command),
        "stdout": _clip(proc.stdout),
        "stderr": _clip(proc.stderr),
        "parsed": parsed,
    }


def _phase_nightly_pipeline(context: Dict[str, Any]) -> Dict[str, Any]:
    if context.get("mode") != "full":
        return {"ok": True, "skipped": True, "reason": "quick_mode_skips_heavy_nightly_pipeline"}
    payload = {
        "run_mode": "nightly_high_risk_homework",
        "tickers": context["tickers"],
        "include_event_impact": True,
        "source_priority": "a_b_tier_first",
        "write_reports": True,
    }
    payload_path = context["run_dir"] / "nightly_pipeline_payload.json"
    _write_json(payload_path, payload)
    return _run_subprocess(
        [sys.executable, "-m", "Apollo.nightly_pipeline", "run", "--payload-file", str(payload_path)],
        timeout_sec=int(context.get("nightly_timeout_sec") or 7200),
    )


PHASE_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "post_close_market_snapshot": _phase_market_snapshot,
    "news_catalyst_gather": _phase_event_impact,
    "event_dislocation_study": _phase_swing_study,
    "high_risk_paper_screen": _phase_high_risk_screen,
    "graph_rag_enrichment": _phase_graph_rag,
    "technical_confirmation_trade_cycle": _phase_trade_cycle,
    "pressed_aggressive_projection": _phase_projection,
    "deep_daily_report": _phase_daily_report,
    "nightly_pipeline_full_gather_ocr_hipporag": _phase_nightly_pipeline,
}


def _master_markdown(status: Dict[str, Any]) -> str:
    projection = dict(status.get("next_day_projection") or {})
    confidence = dict(status.get("confidence_model") or projection.get("confidence_model") or {})
    ocr97 = dict(status.get("ocr97_status") or confidence.get("ocr97") or {})
    lines = [
        f"# Apollo Nightly High-Risk Homework - {status.get('run_id')}",
        "",
        f"- Status: `{status.get('status')}`",
        f"- Score: `{status.get('score')}`",
        f"- Started: `{status.get('started_at')}`",
        f"- Completed: `{status.get('completed_at')}`",
        "- Live execution submitted: `False`",
        "- Human approval required: `True`",
        "",
        "## Next-Day Projection",
        "",
        f"- Primary action: `{projection.get('primary_action') or 'none'}`",
        f"- Risk posture: `{projection.get('risk_posture') or 'unknown'}`",
        f"- Stress sandbox ready: `{(projection.get('stress_sandbox') or {}).get('ready')}`",
        f"- Stress sandbox top: `{(projection.get('stress_sandbox') or {}).get('top_ticker') or 'none'}`",
        f"- Stress sandbox report: `{((projection.get('stress_sandbox') or {}).get('report') or {}).get('markdown_path') or ''}`",
        "- Stress sandbox warning: high-variance simulation-only evidence; no production readiness credit.",
        f"- Morning refresh required: `{projection.get('morning_refresh_required')}`",
        f"- Open behavior: `{projection.get('open_behavior') or ''}`",
        f"- Graph RAG mode: `{projection.get('graph_rag_mode') or 'unknown'}`",
        "",
        "## Confidence Model",
        "",
        f"- source_confidence: `{confidence.get('source_confidence') or 'unknown'}`",
        f"- event_confidence: `{confidence.get('event_confidence') or 'unknown'}`",
        f"- graph_usefulness: `{confidence.get('graph_usefulness') or 'unknown'}`",
        f"- technical_confirmation: `{confidence.get('technical_confirmation') or 'unknown'}`",
        f"- top_blocker: `{confidence.get('top_blocker') or 'none'}`",
        f"- blocker_hierarchy: `{', '.join(list(confidence.get('blocker_hierarchy') or [])) or 'none'}`",
        f"- ocr97_status: `{ocr97.get('status') or 'unknown'}` fresh=`{ocr97.get('fresh')}` quality_confident=`{ocr97.get('quality_confident')}` score=`{ocr97.get('score')}`",
        "",
        "## Phases",
    ]
    for phase in status.get("phases") or []:
        lines.append(f"- `{phase.get('name')}`: `{phase.get('status')}` - {phase.get('summary')}")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
        ]
    )
    for phase in status.get("phases") or []:
        lines.append(f"- `{phase.get('name')}`: `{phase.get('markdown_path')}`")
    return "\n".join(lines).rstrip() + "\n"


def run_homework(
    *,
    run_id: Optional[str] = None,
    mode: str = "quick",
    tickers: str = "",
    include_heavy_nightly: bool = False,
    write_reports: bool = True,
    nightly_timeout_sec: int = 7200,
) -> Dict[str, Any]:
    safe_run_id = run_id or _default_run_id()
    run_dir = REPORT_ROOT / safe_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    phase_names = list(PHASE_ORDER)
    if include_heavy_nightly:
        phase_names.insert(3, "nightly_pipeline_full_gather_ocr_hipporag")
    context: Dict[str, Any] = {
        "run_id": safe_run_id,
        "run_dir": run_dir,
        "mode": mode,
        "tickers": _tickers(tickers),
        "phases": [],
        "phase_outputs": {},
        "event_max_results": int(os.getenv("APOLLO_HIGH_RISK_EVENT_MAX_RESULTS", "8")),
        "nightly_timeout_sec": nightly_timeout_sec,
        "ocr97_status": _load_ocr97_status(),
    }
    started = _utc_iso()
    for index, name in enumerate(phase_names, start=1):
        required = name not in {"graph_rag_enrichment", "nightly_pipeline_full_gather_ocr_hipporag", "deep_daily_report"}
        _run_phase(run_dir, index, name, PHASE_HANDLERS[name], context, required=required)

    projection = _build_projection(context)
    failed = [phase for phase in context["phases"] if phase.get("status") == "failed"]
    partial = [phase for phase in context["phases"] if phase.get("status") == "partial"]
    passed = [phase for phase in context["phases"] if phase.get("status") == "passed"]
    total = len(context["phases"])
    score = round(((len(passed) + 0.5 * len(partial)) / max(1, total)) * 100.0, 2)
    status = {
        "ok": not failed,
        "status": "passed" if not failed and not partial else ("partial" if not failed else "failed"),
        "run_id": safe_run_id,
        "mode": mode,
        "started_at": started,
        "completed_at": _utc_iso(),
        "passed": len(passed),
        "partial": len(partial),
        "failed": len(failed),
        "total": total,
        "score": score,
        "tickers": context["tickers"],
        "phases": context["phases"],
        "next_day_projection": projection,
        "confidence_model": _confidence_model(context),
        "ocr97_status": dict(context.get("ocr97_status") or {}),
        "paper_only": True,
        "human_approval_required": True,
        "live_trade_execution": False,
    }
    if write_reports:
        json_path = run_dir / "status.json"
        md_path = run_dir / "report.md"
        _write_json(json_path, status)
        md_path.write_text(_master_markdown(status), encoding="utf-8")
        latest = REPORT_ROOT / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        _write_json(latest / "status.json", status)
        (latest / "report.md").write_text(_master_markdown(status), encoding="utf-8")
        status["json_path"] = str(json_path)
        status["markdown_path"] = str(md_path)
    return status


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Apollo nightly high-risk paper homework and next-day projection.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--include-heavy-nightly", action="store_true")
    parser.add_argument("--nightly-timeout-sec", type=int, default=7200)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run_homework(
        run_id=args.run_id or None,
        mode=args.mode,
        tickers=args.tickers,
        include_heavy_nightly=bool(args.include_heavy_nightly),
        write_reports=not bool(args.no_write),
        nightly_timeout_sec=max(60, int(args.nightly_timeout_sec)),
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
