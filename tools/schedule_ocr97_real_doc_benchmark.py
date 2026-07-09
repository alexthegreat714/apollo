from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo


APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent
SKY_ROOT = ENGINEERING_ROOT / "Sky"
REPORT_ROOT = APOLLO_ROOT / "reports" / "ocr97_real_docs"
LAUNCHER_ROOT = APOLLO_ROOT / "scheduled_launchers"
TASK_PREFIX = "OCR97RealDocumentBenchmark"
PROJECT = "ocr97"
TEST_TYPE = "ocr97_calendar_run"
CAPABILITY = "real_document_ocr_capability"
GX10_MODEL_REQUIREMENT = {
    "provider": "gx10_ollama",
    "url": "http://127.0.0.1:11434",
    "model": "qwen3-vl:32b",
    "offline_model_only": True,
    "source_under_test": str(APOLLO_ROOT / "tools" / "run_ocr97_real_doc_benchmark.py"),
    "ocr_entrypoint": "common.ocr_dual_tool.ocr_dual",
}


def _next_tonight_run(now: datetime) -> datetime:
    # Nightly proof run is intentionally after normal day work, before morning report.
    candidate = datetime.combine(now.date(), time(hour=23, minute=15), tzinfo=now.tzinfo)
    if candidate <= now + timedelta(minutes=5):
        candidate += timedelta(days=1)
    return candidate


def _task_name(start_local: datetime) -> str:
    return f"{TASK_PREFIX}{start_local.strftime('%Y%m%d_%H%M')}"


def _event_id(start_local: datetime) -> str:
    return f"apollo-ocr97-real-docs-{start_local.strftime('%Y%m%d-%H%M')}"


def _run_command(start_local: datetime) -> str:
    task_name = _task_name(start_local)
    launcher = LAUNCHER_ROOT / f"{task_name}.cmd"
    script = APOLLO_ROOT / "tools" / "run_ocr97_real_doc_benchmark_scheduled.ps1"
    LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "\r\n".join(
            [
                "@echo off",
                f'cd /d "{APOLLO_ROOT}"',
                (
                    "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
                    f'-File "{script}" -TaskName "{task_name}"'
                ),
                "",
            ]
        ),
        encoding="ascii",
    )
    return str(launcher)


def _legacy_run_command(start_local: datetime) -> str:
    script = APOLLO_ROOT / "tools" / "run_ocr97_real_doc_benchmark_scheduled.ps1"
    powershell = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    return (
        f"{powershell} -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        f"-File \"{script}\""
    )


def _report_path(start_local: datetime) -> str:
    return str(REPORT_ROOT / f"scheduled_{start_local.strftime('%Y%m%d_%H%M')}" / "report.md")


def _apply_self_delete(task_name: str, start_local: datetime) -> Dict[str, Any]:
    # schtasks /SC ONCE cannot set an expiry; without one, fired one-shots pile up forever.
    end_boundary = (start_local + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    script = (
        f"$t = Get-ScheduledTask -TaskName '{task_name}'; "
        f"$t.Triggers[0].EndBoundary = '{end_boundary}'; "
        "$t.Settings.DeleteExpiredTaskAfter = 'PT12H'; "
        "Set-ScheduledTask -InputObject $t | Out-Null"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return {"ok": proc.returncode == 0, "end_boundary": end_boundary, "stderr": proc.stderr.strip()}


def install_windows_task(start_local: datetime, *, dry_run: bool = False) -> Dict[str, Any]:
    task = _task_name(start_local)
    cmd = [
        "schtasks",
        "/Create",
        "/SC",
        "ONCE",
        "/TN",
        task,
        "/TR",
        _run_command(start_local),
        "/SD",
        start_local.strftime("%m/%d/%Y"),
        "/ST",
        start_local.strftime("%H:%M"),
        "/F",
    ]
    if dry_run:
        return {"ok": True, "dry_run": True, "task_name": task, "command": cmd}
    proc = subprocess.run(cmd, cwd=str(APOLLO_ROOT), capture_output=True, text=True, timeout=60, check=False)
    result = {
        "ok": proc.returncode == 0,
        "task_name": task,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "command": cmd,
    }
    if result["ok"]:
        try:
            result["self_delete"] = _apply_self_delete(task, start_local)
        except Exception as exc:
            result["self_delete"] = {"ok": False, "error": str(exc)[:200]}
    return result


def _calendar_payload(start_local: datetime, task_name: str) -> Dict[str, Any]:
    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc = (start_local + timedelta(minutes=75)).astimezone(ZoneInfo("UTC"))
    local_script = APOLLO_ROOT / "tools" / "run_ocr97_real_doc_benchmark_scheduled.ps1"
    fallback_script = APOLLO_ROOT / "tools" / "ocr97_real_doc_failure_fallback.py"
    command = _run_command(start_local)
    report_path = _report_path(start_local)
    notes = "\n".join(
        [
            "Run OCR97 against a public real-document corpus, compare it against the old local OCR lane, and update README_OCR97.md with the latest defend/rescore evidence.",
            f"Task Scheduler: {task_name}.",
            f"[local_command:{local_script}]",
            f"Verification command: {command}",
            "Outputs: Apollo/reports/ocr97_real_docs/<run_id>/report.md and summary.json.",
            "The run must update the generated Real Document Capability Evidence block in Apollo/README_OCR97.md.",
            "Model-heavy OCR lane requirement:",
            f"  - provider: {GX10_MODEL_REQUIREMENT['provider']}",
            f"  - url: {GX10_MODEL_REQUIREMENT['url']} (local SSH tunnel to GX10)",
            f"  - model: {GX10_MODEL_REQUIREMENT['model']}",
            "  - offline_model_only: true; do not pull models or require cloud login",
            f"  - source under test: {GX10_MODEL_REQUIREMENT['source_under_test']}",
            "FTP loose timeout gate: timeout-like OCR failures are recoverable/inconclusive and should enter the bounded 3x repair/retry loop instead of being treated as final capability failure.",
            "Failure fallback: mark this card failed, write an Aegis review contract, run Forge first, then VM Codex fallback, then retry/requeue only after validation.",
            f"Fallback helper: {fallback_script}",
        ]
    )
    return {
        "id": _event_id(start_local),
        "title": "OCR97 real-document capability benchmark",
        "start": start_utc.isoformat().replace("+00:00", "Z"),
        "end": end_utc.isoformat().replace("+00:00", "Z"),
        "status": "pending",
        "source": "apollo",
        "trigger": "Night proof run to defend or rescore OCR97 using real public documents.",
        "notes": notes,
        "test_queue": True,
        "project": PROJECT,
        "test_type": TEST_TYPE,
        "run_id": task_name,
        "capability": CAPABILITY,
        "verification_command": command,
        "report_path": report_path,
        "metadata": {
            "test_queue": True,
            "project": PROJECT,
            "test_type": TEST_TYPE,
            "capability": CAPABILITY,
            "runner_command": command,
            "task_scheduler_name": task_name,
            "report_path": report_path,
            "model_requirement": GX10_MODEL_REQUIREMENT,
        },
    }


def create_sky_calendar_card(start_local: datetime, task_name: str, *, dry_run: bool = False) -> Dict[str, Any]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    payload = _calendar_payload(start_local, task_name)
    if dry_run:
        return {"ok": True, "dry_run": True, "event": payload}
    from Sky.core.calendar_store import CalendarConflictError, CalendarNotFoundError, create_event, update_event

    try:
        event = update_event(payload["id"], payload)
    except CalendarNotFoundError:
        try:
            event = create_event(payload)
        except CalendarConflictError:
            return {"ok": False, "error": "event_overlap", "event": payload}
    except CalendarConflictError:
        return {"ok": False, "error": "event_overlap", "event": payload}
    return {"ok": True, "event": event}


def _actual_event_start_local(card: Dict[str, Any], fallback: datetime) -> datetime:
    event = card.get("event") if isinstance(card.get("event"), dict) else {}
    raw = str(event.get("start") or "").strip()
    if not raw:
        return fallback
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago"))
    except Exception:
        return fallback


def sync_calendar_card_task(event: Dict[str, Any], start_local: datetime, task_name: str, *, dry_run: bool = False) -> Dict[str, Any]:
    if not isinstance(event, dict) or not event.get("id"):
        return {"ok": False, "error": "event_required"}
    payload = _calendar_payload(start_local, task_name)
    payload["id"] = str(event.get("id"))
    payload["start"] = str(event.get("start") or payload["start"])
    payload["end"] = str(event.get("end") or payload["end"])
    payload["status"] = str(event.get("status") or payload["status"])
    metadata = dict(event.get("metadata") or {})
    metadata.update(payload.get("metadata") or {})
    payload["metadata"] = metadata
    if dry_run:
        return {"ok": True, "dry_run": True, "event": payload}
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Sky.core.calendar_store import CalendarConflictError, CalendarNotFoundError, update_event

    try:
        updated = update_event(payload["id"], payload)
    except (CalendarConflictError, CalendarNotFoundError) as exc:
        return {"ok": False, "error": type(exc).__name__, "event": payload}
    return {"ok": True, "event": updated}


def create_sky_task_card(start_local: datetime, task_name: str, *, dry_run: bool = False) -> Dict[str, Any]:
    event_id = _event_id(start_local)
    start_hook = APOLLO_ROOT / "tools" / "run_ocr97_real_doc_benchmark_scheduled.ps1"
    fallback_helper = APOLLO_ROOT / "tools" / "ocr97_real_doc_failure_fallback.py"
    text = (
        f"OCR97 real-document capability benchmark is queued for {start_local.strftime('%Y-%m-%d %H:%M')} CT. "
        f"Calendar event: {event_id}. Task Scheduler: {task_name}. Start hook: {start_hook}. "
        f"If it fails, {fallback_helper} marks the calendar card failed and writes an Aegis review contract requiring "
        "Forge-first repair, VM Codex fallback, evidence.md, validation, and retry/requeue only after proof."
    )
    payload = {
        "text": text,
        "source": "apollo",
        "conversation_id": f"ocr97-real-doc-benchmark-{start_local.strftime('%Y%m%d')}",
        "dispatch": False,
        "metadata": {
            "input_type": "task",
            "bucket": "ops",
            "priority": "high",
            "title": "OCR97 real-document benchmark queued",
            "calendar_event_id": event_id,
            "task_scheduler_name": task_name,
            "start_hook": str(start_hook),
            "fallback_helper": str(fallback_helper),
            "project": PROJECT,
            "test_type": TEST_TYPE,
            "capability": CAPABILITY,
            "verification_command": _run_command(start_local),
            "report_path": _report_path(start_local),
            "model_requirement": GX10_MODEL_REQUIREMENT,
        },
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "payload": payload}
    base_url = str(__import__("os").environ.get("SKY_BASE_URL") or "http://127.0.0.1:5011").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/sky/tasks",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"http_{exc.code}", "detail": exc.read().decode("utf-8", errors="ignore")[:300]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    return data if isinstance(data, dict) else {"ok": False, "error": "task_response_invalid"}


def find_calendar_slot(start_local: datetime, *, dry_run: bool = False) -> Tuple[datetime, Dict[str, Any]]:
    candidate = start_local
    last: Dict[str, Any] = {}
    for _ in range(16):
        task = _task_name(candidate)
        card = create_sky_calendar_card(candidate, task, dry_run=dry_run)
        if card.get("ok"):
            return candidate, card
        last = card
        candidate += timedelta(minutes=15)
    return start_local, {"ok": False, "error": "no_open_calendar_slot", "last": last}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule OCR97 real-document benchmark in Task Scheduler and Sky calendar.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", help="Local date override, YYYY-MM-DD")
    parser.add_argument("--time", default="23:15", help="Local time, HH:MM")
    args = parser.parse_args(argv)

    tz = ZoneInfo("America/Chicago")
    now = datetime.now(tz)
    if args.date:
        hh, mm = [int(part) for part in args.time.split(":", 1)]
        start_local = datetime.combine(datetime.fromisoformat(args.date).date(), time(hour=hh, minute=mm), tzinfo=tz)
    else:
        start_local = _next_tonight_run(now)

    start_local, card = find_calendar_slot(start_local, dry_run=args.dry_run)
    if card.get("ok"):
        start_local = _actual_event_start_local(card, start_local)
    task = install_windows_task(start_local, dry_run=args.dry_run) if card.get("ok") else {"ok": False, "error": "calendar_card_failed"}
    if task.get("ok") and card.get("ok"):
        synced = sync_calendar_card_task(card.get("event") or {}, start_local, str(task.get("task_name") or _task_name(start_local)), dry_run=args.dry_run)
        if synced.get("ok"):
            card = synced
    task_card = create_sky_task_card(start_local, str(task.get("task_name") or _task_name(start_local)), dry_run=args.dry_run) if task.get("ok") else {"ok": False, "error": "task_scheduler_failed"}
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": bool(task.get("ok")) and bool(card.get("ok")),
        "scheduled_for_local": start_local.isoformat(),
        "task": task,
        "calendar": card,
        "sky_task": task_card,
    }
    out = REPORT_ROOT / f"scheduled_{start_local.strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
