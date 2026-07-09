from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent
OPS_ROOT = ENGINEERING_ROOT / "_ops"
REPORT_ROOT = APOLLO_ROOT / "reports" / "ocr97_real_docs"
TITLE = "OCR97 real-document capability benchmark"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat().replace("+00:00", "Z")


def _safe_id(raw: str) -> str:
    digest = hashlib.sha1(str(raw or TITLE).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"aegis_calendar_review_{digest}"


def _load_sky_calendar() -> tuple[Any, Any, Any]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Sky.core.calendar_store import list_events, update_event, CalendarNotFoundError

    return list_events, update_event, CalendarNotFoundError


def find_target_event(event_id: str = "") -> Dict[str, Any]:
    list_events, _, _ = _load_sky_calendar()
    now = _utc_now()
    events = list_events(start=_iso(now - timedelta(days=1)), end=_iso(now + timedelta(days=2)))
    if event_id:
        for event in events:
            if str(event.get("id") or "") == event_id:
                return event
    candidates = [
        event
        for event in events
        if str(event.get("title") or "") == TITLE
        and str(event.get("status") or "").lower() in {"pending", "in_work", "partial", "failed"}
    ]
    if not candidates:
        for event in events:
            title = str(event.get("title") or "").lower()
            status = str(event.get("status") or "").lower()
            if "ocr97" not in title or status not in {"pending", "in_work", "partial", "failed"}:
                continue
            try:
                start = datetime.fromisoformat(str(event.get("start") or "").replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
            try:
                end = datetime.fromisoformat(str(event.get("end") or "").replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                end = start + timedelta(hours=3)
            if start - timedelta(minutes=15) <= now <= end + timedelta(hours=8):
                candidates.append(event)
    candidates.sort(key=lambda event: str(event.get("start") or ""), reverse=True)
    if not candidates:
        raise RuntimeError("ocr97_calendar_event_not_found")
    return candidates[0]


def _append_note(notes: str, line: str) -> str:
    if line in notes:
        return notes
    next_notes = (notes.rstrip() + ("\n\n" if notes.strip() else "") + line).strip()
    if len(next_notes) <= 2000:
        return next_notes
    suffix = "\n\n" + line
    return (notes[: max(0, 2000 - len(suffix))].rstrip() + suffix).strip()


def mark_event_failed(event: Dict[str, Any], contract_id: str, *, exit_code: int, task_name: str) -> Dict[str, Any]:
    _, update_event, _ = _load_sky_calendar()
    timestamp = _iso()
    notes = str(event.get("notes") or "")
    notes = _append_note(notes, f"[aegis_review:{contract_id}]")
    notes = _append_note(
        notes,
        (
            f"[aegis_auto_review:{contract_id}] OCR97 real-document benchmark failed at {timestamp}; "
            "queued for Forge-first Aegis review with VM Codex fallback, evidence.md, and retry/requeue only after validation."
        ),
    )
    patch = {
        "status": "failed",
        "trigger": f"OCR97 real-document benchmark failed with exit code {exit_code}; Aegis review queued.",
        "notes": notes,
    }
    return update_event(str(event.get("id") or ""), patch)


def write_contract(event: Dict[str, Any], contract_id: str, *, exit_code: int, task_name: str) -> Dict[str, Path]:
    now = _utc_now()
    run_dir = OPS_ROOT / "runs" / contract_id
    contract_path = OPS_ROOT / "contracts" / f"{contract_id}.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    report_hint = str(REPORT_ROOT)
    contract = {
        "contract_id": contract_id,
        "title": f"Aegis calendar review: {TITLE}",
        "objective": (
            "The OCR97 real-document capability benchmark failed. Forge must investigate and attempt a verified repair first. "
            "If Forge cannot prove the repair, use VM Codex as fallback, apply fixes natively only after tests pass, "
            "write evidence.md, then retry/requeue the benchmark. Do not mark ready_for_rotation without validation."
        ),
        "owner": "Aegis",
        "created_by": "Apollo scheduled OCR97 fallback",
        "systems": ["Apollo", "Sky", "Aegis", "Forge", "Codex", "VM"],
        "calendar_event": event,
        "review_reason": f"ocr97_real_doc_benchmark_failed_exit_{exit_code}",
        "dispatch_policy": {
            "mode": "night_deferred",
            "manual_click": True,
            "start_immediately_if_compute_available": True,
            "night_fallback": True,
            "night_window": {"start_hour": 20, "end_hour": 6, "timezone": "America/Chicago"},
        },
        "test_plan": {
            "commands": [
                "python -m py_compile Apollo/tools/run_ocr97_real_doc_benchmark.py Apollo/tools/ocr97_real_doc_failure_fallback.py",
                "python -m pytest Apollo/tests/test_ocr97_real_doc_benchmark.py -q",
                "python Apollo/tools/run_ocr97_real_doc_benchmark.py --manifest Apollo/config/ocr97_real_documents_manifest.json --update-readme",
            ],
            "expected_artifacts": [
                "Apollo/reports/ocr97_real_docs/<run_id>/summary.json",
                "Apollo/reports/ocr97_real_docs/<run_id>/report.md",
                "Apollo/README_OCR97.md Real Document Capability Evidence block",
            ],
        },
        "schedule": {
            "start": now.isoformat(),
            "end": (now + timedelta(minutes=90)).isoformat(),
            "timezone": "America/Chicago",
            "recurrence": "one_time",
            "expected_duration_minutes": 90,
        },
        "risk": {"level": "medium", "live_trade_execution": False, "notes": "OCR/reporting code only. No trading."},
        "execution": [
            {
                "step_id": "forge_first_calendar_review",
                "kind": "command",
                "command": f"python Aegis/tools/calendar_review_pipeline.py --contract-id {contract_id}",
                "cwd": ".",
                "timeout_minutes": 90,
                "allowed_outputs": [f"_ops/runs/{contract_id}"],
            }
        ],
        "gates": [
            {"gate_id": "forge_first", "description": "Forge must inspect failure artifacts and attempt the fix first."},
            {"gate_id": "codex_review", "description": "Codex fallback must document why Forge missed and how Forge should improve."},
            {"gate_id": "vm_native_apply", "description": "Fallback fixes must pass native tests before retry/requeue."},
            {"gate_id": "operator_help", "description": "Only request human help for credentials, approvals, or irreducible missing context."},
        ],
        "artifacts": {
            "status_path": f"_ops/runs/{contract_id}/status.json",
            "trace_path": f"_ops/runs/{contract_id}/trace.jsonl",
            "evidence_path": f"_ops/runs/{contract_id}/evidence.md",
            "benchmark_report_root": report_hint,
        },
        "reporting": {
            "calendar_title": TITLE,
            "morning_summary": "OCR97 real-document proof failed and was queued for Forge/Codex Aegis review.",
            "next_decision": "Return the run to upcoming rotation only after the benchmark rerun updates README_OCR97.md.",
        },
        "trace_policy": {"verbose_artifacts": True, "compact_ui": True},
    }
    status = {
        "ok": True,
        "contract_id": contract_id,
        "title": contract["title"],
        "state": "scheduled",
        "result": "",
        "needs_operator": False,
        "updated_at": _iso(),
        "message": "Scheduled for Aegis review after OCR97 real-document benchmark failure.",
        "dispatch_policy": contract["dispatch_policy"],
        "auto_night_scheduled": True,
        "artifacts": {"contract_path": str(contract_path), **contract["artifacts"]},
        "live_update": "OCR97 proof failed; Aegis review queued for Forge-first repair and Codex fallback.",
        "progress": {"percent": 0, "label": "queued for Aegis review"},
    }
    contract_path.write_text(json.dumps(contract, ensure_ascii=True, indent=2), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps(status, ensure_ascii=True, indent=2), encoding="utf-8")
    (run_dir / "evidence.md").write_text(
        "\n".join(
            [
                f"# OCR97 Real Document Benchmark Failure - {contract_id}",
                "",
                f"- Task Scheduler: `{task_name or 'unknown'}`",
                f"- Exit code: `{exit_code}`",
                f"- Calendar event: `{event.get('id')}`",
                f"- Report root: `{report_hint}`",
                "",
                "Aegis should inspect the latest summary/report in the report root, run Forge first, then VM Codex fallback if needed.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"contract_path": contract_path, "status_path": run_dir / "status.json"}


def run_fallback(event_id: str = "", exit_code: int = 1, task_name: str = "") -> Dict[str, Any]:
    event = find_target_event(event_id)
    contract_id = _safe_id(str(event.get("id") or task_name or TITLE))
    paths = write_contract(event, contract_id, exit_code=exit_code, task_name=task_name)
    updated = mark_event_failed(event, contract_id, exit_code=exit_code, task_name=task_name)
    payload = {
        "ok": True,
        "event_id": updated.get("id"),
        "contract_id": contract_id,
        "contract_path": str(paths["contract_path"]),
        "status_path": str(paths["status_path"]),
    }
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / f"fallback_{contract_id}.json").write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Queue Aegis review after OCR97 real-document benchmark failure.")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--exit-code", type=int, default=1)
    parser.add_argument("--task-name", default="")
    args = parser.parse_args(argv)
    payload = run_fallback(args.event_id, exit_code=args.exit_code, task_name=args.task_name)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
