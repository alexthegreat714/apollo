from __future__ import annotations

import argparse
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

from Apollo import simulation_execution, swing_study


APOLLO_ROOT = Path(__file__).resolve().parent
RUN_ROOT = APOLLO_ROOT / "logs" / "day_trade_cycle"
CAPABILITY = "paper simulated day-trade lifecycle"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: Any, fallback: str = "apollo_day_trade_cycle") -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("._-")
    return text[:96] or fallback


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _boolish(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _http_get_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 8.0) -> Dict[str, Any]:
    try:
        response = requests.get(url, headers=headers or {}, timeout=timeout)
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("status_code", response.status_code)
        payload.setdefault("ok", response.ok)
        return payload
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}


def _alpaca_paper_credentials_present() -> bool:
    key = os.getenv("APOLLO_ALPACA_PAPER_API_KEY") or os.getenv("APOLLO_ALPACA_PAPER_KEY")
    secret = os.getenv("APOLLO_ALPACA_PAPER_API_SECRET") or os.getenv("APOLLO_ALPACA_PAPER_SECRET")
    return bool(str(key or "").strip() and str(secret or "").strip())


@contextmanager
def _temporary_env(overrides: Dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def preflight(
    *,
    apollo_base_url: str = "",
    aegis_base_url: str = "",
    aegis_token: str = "",
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    live_disabled = not _boolish(os.getenv("APOLLO_SIM_LIVE_ENABLED"))
    checks.append(
        {
            "name": "live_execution_disabled",
            "ok": live_disabled,
            "required": True,
            "detail": "APOLLO_SIM_LIVE_ENABLED is disabled" if live_disabled else "APOLLO_SIM_LIVE_ENABLED is enabled",
        }
    )

    if apollo_base_url:
        health = _http_get_json(apollo_base_url.rstrip("/") + "/health")
        checks.append(
            {
                "name": "apollo_health",
                "ok": bool(health.get("ok") or health.get("status") == "ok"),
                "required": True,
                "detail": health.get("status") or health.get("error") or "",
                "evidence": health,
            }
        )
        provider = _http_get_json(apollo_base_url.rstrip("/") + "/admin/swing/provider_health", timeout=12.0)
        checks.append(
            {
                "name": "provider_health",
                "ok": bool(provider.get("ok")),
                "required": False,
                "detail": provider.get("error") or "provider route reachable",
                "evidence": provider,
            }
        )
    else:
        checks.append({"name": "apollo_health", "ok": True, "required": True, "detail": "in_process"})
        checks.append({"name": "provider_health", "ok": True, "required": False, "detail": "checked_after_swing_study"})

    if aegis_base_url:
        headers = {}
        if aegis_token:
            headers = {"X-Aegis-Token": aegis_token, "X-Admin-Token": aegis_token, "X-CSRF-Token": aegis_token}
        readiness = _http_get_json(aegis_base_url.rstrip("/") + "/admin/apollo/swing/readiness", headers=headers, timeout=20.0)
        checks.append(
            {
                "name": "aegis_apollo_readiness",
                "ok": bool(readiness.get("ready") or readiness.get("ok")),
                "required": False,
                "detail": readiness.get("next_decision") or readiness.get("error") or "",
                "evidence": readiness,
            }
        )
    else:
        checks.append({"name": "aegis_apollo_readiness", "ok": True, "required": False, "detail": "not_configured"})

    failed_required = [row["name"] for row in checks if row.get("required") and not row.get("ok")]
    return {"ok": not failed_required, "checks": checks, "failed_required": failed_required}


def _candidate_from_study(study: Dict[str, Any]) -> Dict[str, Any]:
    proposals = [row for row in list(study.get("proposals") or []) if isinstance(row, dict)]
    viable = [
        row
        for row in proposals
        if str(row.get("direction_bias") or "") == "long_bias"
        and not str(row.get("no_trade_reason") or "").strip()
        and str(row.get("entry_zone") or "").strip()
        and str(row.get("stop_zone") or "").strip()
        and str(row.get("target_zone") or "").strip()
    ]
    if viable:
        viable.sort(key=lambda row: _safe_float(row.get("score")), reverse=True)
        return dict(viable[0])
    best = study.get("best_proposal") if isinstance(study.get("best_proposal"), dict) else {}
    return dict(best or (proposals[0] if proposals else {}))


def build_or_load_candidate(*, tickers: str = "", run_id: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "write_artifacts": False,
        "notify": False,
        "refresh": True,
        "run_id": f"{run_id}_swing" if run_id else "",
    }
    if tickers:
        payload["tickers"] = tickers
    study = swing_study.build_swing_study(payload)
    candidate = _candidate_from_study(study)
    ok = bool(study.get("ok") and candidate)
    return {"ok": ok, "study": study, "candidate": candidate}


def _proposal_to_simulation(candidate: Dict[str, Any], *, run_id: str) -> Dict[str, Any]:
    ticker = str(candidate.get("ticker") or "").strip().upper()
    entry_zone = str(candidate.get("entry_zone") or "").strip()
    stop_zone = str(candidate.get("stop_zone") or candidate.get("stop") or "").strip()
    target_zone = str(candidate.get("target_zone") or candidate.get("target") or "").strip()
    if not ticker or not entry_zone or not stop_zone or not target_zone:
        raise ValueError("candidate_missing_execution_levels")
    return {
        "sim_id": f"day_trade_{_slug(run_id)}_{ticker}",
        "ticker": ticker,
        "entry_zone": entry_zone,
        "stop_zone": stop_zone,
        "target_zone": target_zone,
        "status": "ready",
        "setup_type": str(candidate.get("setup_type") or "day_trade_rehearsal"),
        "score": candidate.get("score") or candidate.get("confidence"),
        "confidence_label": str(candidate.get("confidence_label") or ""),
        "run_id": run_id,
        "created_at": _utc_now(),
    }


def _run_execution_stage(
    simulation: Dict[str, Any],
    *,
    executor: str,
    label: str,
    run_id: str,
) -> Dict[str, Any]:
    created = simulation_execution.create_simulation_execution_plan(
        {
            "simulation": simulation,
            "notes": f"Apollo bounded day-trade rehearsal {label} stage {run_id}",
        }
    )
    if not created.get("ok"):
        return {"ok": False, "stage": label, "error": created.get("error"), "create": created}
    execution_id = str((created.get("execution_plan") or {}).get("execution_id") or "")
    approved = simulation_execution.approve_simulation_execution_plan(
        execution_id=execution_id,
        approved_by="apollo_day_trade_cycle",
        approval_notes=f"bounded rehearsal stage={label}",
        approval_source="day_trade_cycle",
    )
    if not approved.get("ok"):
        return {"ok": False, "stage": label, "execution_id": execution_id, "error": approved.get("error"), "approve": approved}
    submitted = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        executor=executor,
        submitted_by="apollo_day_trade_cycle",
        submission_source="day_trade_cycle",
    )
    if not submitted.get("ok"):
        return {
            "ok": False,
            "stage": label,
            "execution_id": execution_id,
            "error": submitted.get("error"),
            "reason": submitted.get("reason"),
            "submit": submitted,
        }
    synced = simulation_execution.sync_simulation_execution_plan_with_broker(execution_id=execution_id)
    reconciled = simulation_execution.reconcile_simulation_execution_orders(
        max_plans=1,
        requested_by="apollo_day_trade_cycle",
        source=f"day_trade_cycle_{label}",
    )
    plan = submitted.get("execution_plan") or {}
    broker = plan.get("broker") if isinstance(plan.get("broker"), dict) else {}
    return {
        "ok": True,
        "stage": label,
        "execution_id": execution_id,
        "order_ids": list(broker.get("order_ids") or []),
        "fill_status": broker.get("fill_status"),
        "provider": broker.get("provider"),
        "mode": broker.get("mode"),
        "submit": submitted,
        "sync": synced,
        "reconcile": reconciled,
    }


def _verify_live_guard(simulation: Dict[str, Any], *, run_id: str) -> Dict[str, Any]:
    with _temporary_env(
        {
            "APOLLO_SIM_EXECUTION_MODE": "live",
            "APOLLO_SIM_LIVE_ENABLED": "0",
            "APOLLO_SIM_BROKER_PROVIDER": "alpaca",
        }
    ):
        created = simulation_execution.create_simulation_execution_plan(
            {
                "simulation": simulation,
                "notes": f"Apollo day-trade live guard proof {run_id}",
            }
        )
        if not created.get("ok"):
            return {"ok": False, "error": created.get("error"), "create": created}
        execution_id = str((created.get("execution_plan") or {}).get("execution_id") or "")
        simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="apollo_day_trade_cycle")
        submitted = simulation_execution.submit_simulation_execution_plan(
            execution_id=execution_id,
            executor="alpaca",
            submitted_by="apollo_day_trade_cycle",
            submission_source="day_trade_live_guard",
        )
    return {
        "ok": bool(not submitted.get("ok") and submitted.get("reason") == "live_execution_disabled"),
        "execution_id": execution_id,
        "submit": submitted,
        "detail": submitted.get("reason") or submitted.get("error"),
    }


def _score(status: Dict[str, Any]) -> int:
    if not status.get("paper_sim", {}).get("ok"):
        return 35
    if not status.get("live_guard", {}).get("ok"):
        return 65
    alpaca = status.get("alpaca_paper", {})
    if alpaca.get("ok"):
        return 96
    if alpaca.get("skipped"):
        return 88
    return 80


def _markdown_report(status: Dict[str, Any]) -> str:
    candidate = status.get("candidate") if isinstance(status.get("candidate"), dict) else {}
    paper = status.get("paper_sim") if isinstance(status.get("paper_sim"), dict) else {}
    alpaca = status.get("alpaca_paper") if isinstance(status.get("alpaca_paper"), dict) else {}
    live_guard = status.get("live_guard") if isinstance(status.get("live_guard"), dict) else {}
    lines = [
        "# Apollo Simulated Day-Trade Cycle",
        "",
        f"- run_id: `{status.get('run_id')}`",
        f"- status: `{status.get('status')}`",
        f"- score: `{status.get('score')}`",
        f"- candidate: `{candidate.get('ticker') or ''}` score `{candidate.get('score') or candidate.get('confidence') or ''}`",
        f"- paper_sim: `{paper.get('ok')}` orders `{', '.join(str(x) for x in paper.get('order_ids') or [])}`",
        f"- alpaca_paper: `{alpaca.get('status') or alpaca.get('error') or alpaca.get('detail') or alpaca.get('ok')}`",
        f"- live_guard: `{live_guard.get('ok')}` detail `{live_guard.get('detail')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = list(status.get("blockers") or [])
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"


def run_day_trade_cycle(
    *,
    run_id: Optional[str] = None,
    tickers: str = "",
    apollo_base_url: str = "",
    aegis_base_url: str = "",
    aegis_token: str = "",
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    safe_run_id = _slug(run_id or f"apollo_day_trade_cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out_root = Path(root) if root is not None else RUN_ROOT
    run_dir = out_root / safe_run_id
    started_at = _utc_now()

    status: Dict[str, Any] = {
        "ok": False,
        "status": "running",
        "run_id": safe_run_id,
        "started_at": started_at,
        "capability": CAPABILITY,
        "live_trade_execution": False,
        "broker_execution_enabled": False,
        "blockers": [],
    }
    _write_json(run_dir / "status.json", status)

    preflight_result = preflight(apollo_base_url=apollo_base_url, aegis_base_url=aegis_base_url, aegis_token=aegis_token)
    status["preflight"] = preflight_result
    if not preflight_result.get("ok"):
        status["blockers"].extend([f"preflight:{item}" for item in preflight_result.get("failed_required") or []])

    candidate_result = build_or_load_candidate(tickers=tickers, run_id=safe_run_id)
    status["study_run_id"] = (candidate_result.get("study") or {}).get("run_id")
    candidate = dict(candidate_result.get("candidate") or {})
    status["candidate"] = candidate
    if not candidate_result.get("ok"):
        status["blockers"].append("candidate_unavailable")

    simulation: Dict[str, Any] = {}
    if not status["blockers"]:
        try:
            simulation = _proposal_to_simulation(candidate, run_id=safe_run_id)
            status["simulation"] = simulation
        except Exception as exc:
            status["blockers"].append(str(exc))

    if not status["blockers"]:
        with _temporary_env({"APOLLO_SIM_EXECUTION_MODE": "paper", "APOLLO_SIM_BROKER_PROVIDER": "paper_sim"}):
            status["paper_sim"] = _run_execution_stage(simulation, executor="paper_sim", label="paper_sim", run_id=safe_run_id)
        if not status["paper_sim"].get("ok"):
            status["blockers"].append(f"paper_sim:{status['paper_sim'].get('error') or 'failed'}")

    if not status["blockers"]:
        status["live_guard"] = _verify_live_guard(simulation, run_id=safe_run_id)
        if not status["live_guard"].get("ok"):
            status["blockers"].append("live_guard_failed")

    if not status["blockers"]:
        if _alpaca_paper_credentials_present():
            with _temporary_env({"APOLLO_SIM_EXECUTION_MODE": "paper", "APOLLO_SIM_BROKER_PROVIDER": "alpaca_paper"}):
                status["alpaca_paper"] = _run_execution_stage(simulation, executor="alpaca_paper", label="alpaca_paper", run_id=safe_run_id)
            if not status["alpaca_paper"].get("ok"):
                status["blockers"].append(f"alpaca_paper:{status['alpaca_paper'].get('error') or 'failed'}")
        else:
            status["alpaca_paper"] = {
                "ok": True,
                "skipped": True,
                "status": "alpaca_paper_skipped_missing_credentials",
                "detail": "APOLLO_ALPACA_PAPER credentials are not configured.",
            }

    status["completed_at"] = _utc_now()
    status["score"] = _score(status)
    status["ok"] = bool(not status["blockers"] and status.get("paper_sim", {}).get("ok") and status.get("live_guard", {}).get("ok"))
    status["status"] = "passed" if status["ok"] else "failed"
    status["json_path"] = str(run_dir / "status.json")
    status["markdown_path"] = str(run_dir / "report.md")

    _write_json(run_dir / "status.json", status)
    (run_dir / "report.md").write_text(_markdown_report(status), encoding="utf-8")
    return status


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Apollo's bounded simulated day-trade cycle.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--apollo-base-url", default=os.getenv("APOLLO_BASE_URL", ""))
    parser.add_argument("--aegis-base-url", default=os.getenv("AEGIS_BASE_URL", ""))
    parser.add_argument("--aegis-token", default=os.getenv("AEGIS_LOCAL_TOKEN", ""))
    args = parser.parse_args(argv)

    result = run_day_trade_cycle(
        run_id=args.run_id or None,
        tickers=args.tickers,
        apollo_base_url=args.apollo_base_url,
        aegis_base_url=args.aegis_base_url,
        aegis_token=args.aegis_token,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
