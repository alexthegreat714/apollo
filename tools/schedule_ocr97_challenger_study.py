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
REPORT_ROOT = APOLLO_ROOT / "reports" / "ocr97_challenger_study"
LAUNCHER_ROOT = APOLLO_ROOT / "scheduled_launchers"
TASK_PREFIX = "OCR97ChallengerStudy"
PROJECT = "ocr97"
TEST_TYPE = "ocr97_challenger_study"
CAPABILITY = "ocr97_challenger_local_routes"


def _next_tonight_run(now: datetime) -> datetime:
    candidate = datetime.combine(now.date(), time(hour=23, minute=45), tzinfo=now.tzinfo)
    if candidate <= now + timedelta(minutes=5):
        candidate += timedelta(days=1)
    return candidate


def _task_name(start_local: datetime) -> str:
    return f"{TASK_PREFIX}{start_local.strftime('%Y%m%d_%H%M')}"


def _event_id(start_local: datetime) -> str:
    return f"apollo-ocr97-challenger-{start_local.strftime('%Y%m%d-%H%M')}"


def _run_command(start_local: datetime) -> str:
    task_name = _task_name(start_local)
    launcher = LAUNCHER_ROOT / f"{task_name}.cmd"
    script = APOLLO_ROOT / "tools" / "run_ocr97_challenger_study_scheduled.ps1"
    LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "\r\n".join(
            [
                "@echo off",
                f'cd /d "{APOLLO_ROOT}"',
                (
                    "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
                    f'-File "{script}" -TaskName "{task_name}" -EventId "{_event_id(start_local)}"'
                ),
                "",
            ]
        ),
        encoding="ascii",
    )
    return str(launcher)


def _report_path(start_local: datetime) -> str:
    return str(REPORT_ROOT / "latest_report.md")


def install_windows_task(start_local: datetime, *, dry_run: bool = False) -> Dict[str, Any]:
    task = _task_name(start_local)
    cmd = [
        "schtasks", "/Create", "/SC", "ONCE", "/TN", task, "/TR", _run_command(start_local),
        "/SD", start_local.strftime("%m/%d/%Y"), "/ST", start_local.strftime("%H:%M"), "/F",
    ]
    if dry_run:
        return {"ok": True, "dry_run": True, "task_name": task, "command": cmd}
    proc = subprocess.run(cmd, cwd=str(APOLLO_ROOT), capture_output=True, text=True, timeout=60, check=False)
    return {"ok": proc.returncode == 0, "task_name": task, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip(), "command": cmd}


def _calendar_payload(start_local: datetime, task_name: str) -> Dict[str, Any]:
    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc = (start_local + timedelta(minutes=90)).astimezone(ZoneInfo("UTC"))
    local_script = APOLLO_ROOT / "tools" / "run_ocr97_challenger_study_scheduled.ps1"
    command = _run_command(start_local)
    report_path = _report_path(start_local)
    notes = "\n".join(
        [
            "Run the OCR97 challenger study against local challenger routes and write stable latest report pointers.",
            f"Task Scheduler: {task_name}.",
            f"[local_command:{local_script}]",
            f"Verification command: {command}",
            "Outputs: Apollo/reports/ocr97_challenger_study/latest_report.md and latest_summary.json.",
            "Routes under test: OCR97 baseline, client-ocr, ollama-ocr.",
            "This benchmark should remain local/offline-friendly and avoid paid cloud OCR services.",
        ]
    )
    return {
        "id": _event_id(start_local),
        "title": "OCR97 challenger study overnight",
        "start": start_utc.isoformat().replace("+00:00", "Z"),
        "end": end_utc.isoformat().replace("+00:00", "Z"),
        "status": "pending",
        "source": "apollo",
        "trigger": "Night proof run to compare local OCR challengers against OCR97.",
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


def create_sky_task_card(start_local: datetime, task_name: str, *, dry_run: bool = False) -> Dict[str, Any]:
    event_id = _event_id(start_local)
    start_hook = APOLLO_ROOT / "tools" / "run_ocr97_challenger_study_scheduled.ps1"
    text = (
        f"OCR97 challenger study is queued for {start_local.strftime('%Y-%m-%d %H:%M')} CT. "
        f"Calendar event: {event_id}. Task Scheduler: {task_name}. Start hook: {start_hook}. "
        "Expected latest report path: Apollo/reports/ocr97_challenger_study/latest_report.md."
    )
    payload = {
        "text": text,
        "source": "apollo",
        "conversation_id": f"ocr97-challenger-study-{start_local.strftime('%Y%m%d')}",
        "dispatch": False,
        "metadata": {
            "input_type": "task",
            "bucket": "ops",
            "priority": "high",
            "title": "OCR97 challenger study queued",
            "calendar_event_id": event_id,
            "task_scheduler_name": task_name,
            "start_hook": str(start_hook),
            "project": PROJECT,
            "test_type": TEST_TYPE,
            "capability": CAPABILITY,
            "verification_command": _run_command(start_local),
            "report_path": _report_path(start_local),
        },
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "payload": payload}
    base_url = str(__import__("os").environ.get("SKY_BASE_URL") or "http://127.0.0.1:5011").rstrip("/")
    request = urllib.request.Request(f"{base_url}/sky/tasks", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
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
    parser = argparse.ArgumentParser(description="Schedule OCR97 challenger study in Task Scheduler and Sky calendar.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", help="Local date override, YYYY-MM-DD")
    parser.add_argument("--time", default="23:45", help="Local time, HH:MM")
    args = parser.parse_args(argv)

    tz = ZoneInfo("America/Chicago")
    now = datetime.now(tz)
    if args.date:
        hh, mm = [int(part) for part in args.time.split(":", 1)]
        start_local = datetime.combine(datetime.fromisoformat(args.date).date(), time(hour=hh, minute=mm), tzinfo=tz)
    else:
        start_local = _next_tonight_run(now)

    start_local, card = find_calendar_slot(start_local, dry_run=args.dry_run)
    task = install_windows_task(start_local, dry_run=args.dry_run) if card.get("ok") else {"ok": False, "error": "calendar_card_failed"}
    task_card = create_sky_task_card(start_local, str(task.get("task_name") or _task_name(start_local)), dry_run=args.dry_run) if task.get("ok") else {"ok": False, "error": "task_scheduler_failed"}
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"ok": bool(task.get("ok")) and bool(card.get("ok")), "scheduled_for_local": start_local.isoformat(), "task": task, "calendar": card, "sky_task": task_card}
    out = REPORT_ROOT / f"scheduled_{start_local.strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
