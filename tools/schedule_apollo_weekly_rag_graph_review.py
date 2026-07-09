from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


ENGINEERING_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINEERING_ROOT))

from Apollo.tools import run_apollo_weekly_rag_graph_review as runner


APOLLO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_TZ = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
TITLE = "Apollo weekly RAG + graph evolution review"
PROJECT = runner.PROJECT
TEST_TYPE = runner.TEST_TYPE
CAPABILITY = runner.CAPABILITY


def _run_id(start_local: datetime) -> str:
    return f"{TEST_TYPE}_{start_local.strftime('%Y%m%d_%H%M')}"


def _event_id(start_local: datetime) -> str:
    return f"apollo-weekly-rag-graph-review-{start_local.strftime('%Y%m%d-%H%M')}"


def _report_path(start_local: datetime) -> str:
    run_id = _run_id(start_local)
    return str(runner.REPORT_ROOT / run_id / "report.md")


def _runner_command(start_local: datetime) -> str:
    script = Path(__file__).resolve().with_name("run_apollo_weekly_rag_graph_review.py")
    week_end = start_local.date().isoformat()
    return f'{sys.executable or "python"} "{script}" --run-id {_run_id(start_local)} --week-end-date {week_end}'


def _calendar_payload(start_local: datetime, *, duration_minutes: int = 75) -> Dict[str, Any]:
    run_id = _run_id(start_local)
    command = _runner_command(start_local)
    report_path = _report_path(start_local)
    end_local = start_local + timedelta(minutes=duration_minutes)
    notes = "\n".join(
        [
            "Weekly governance loop for Apollo RAG and graph evolution.",
            "Produce keep/purge/quarantine recommendations from the last 7 days of nightly, graph, and simulation evidence.",
            f"Runner command: {command}",
            f"[local_command:{Path(__file__).resolve().with_name('run_apollo_weekly_rag_graph_review.py')} --run-id {run_id} --week-end-date {start_local.date().isoformat()}]",
            f"[hook_signature:sky.local_command|apollo|{start_local.date().isoformat()}|apollo weekly rag graph review]",
            f"Expected report: {report_path}",
            "This is review/planning only. It does not auto-delete corpus documents or graph nodes.",
            "After completion, apply purge/quarantine/keep decisions in the following week."
        ]
    )
    return {
        "id": _event_id(start_local),
        "title": TITLE,
        "start": start_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "end": end_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "status": "pending",
        "source": "apollo",
        "trigger": "Queued Apollo weekly RAG/graph evolution review.",
        "notes": notes,
        "test_queue": True,
        "project": PROJECT,
        "test_type": TEST_TYPE,
        "run_id": run_id,
        "capability": CAPABILITY,
        "verification_command": command,
        "report_path": report_path,
        "report_required": True,
        "metadata": {
            "test_queue": True,
            "project": PROJECT,
            "test_type": TEST_TYPE,
            "capability": CAPABILITY,
            "runner_command": command,
            "review_window_days": 7,
            "execution_lane": "weekend_overnight",
        },
    }


def _next_weekend_start(now: Optional[datetime] = None, *, preferred_time: time = time(hour=2, minute=30)) -> datetime:
    current = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    days_ahead = (6 - current.weekday()) % 7  # Sunday
    candidate_date = current.date() + timedelta(days=days_ahead)
    candidate = datetime.combine(candidate_date, preferred_time, tzinfo=LOCAL_TZ)
    if candidate <= current + timedelta(minutes=5):
        candidate += timedelta(days=7)
    return candidate


def create_sky_calendar_card(start_local: datetime, *, dry_run: bool = False) -> Dict[str, Any]:
    payload = _calendar_payload(start_local)
    if dry_run:
        return {"ok": True, "dry_run": True, "event": payload}
    from Sky.core.calendar_store import CalendarConflictError, CalendarNotFoundError, create_event, update_event

    try:
        event = update_event(payload["id"], payload)
    except CalendarNotFoundError:
        try:
            event = create_event(payload)
        except CalendarConflictError as exc:
            return {"ok": False, "error": "event_overlap", "event": payload, "conflict": exc.conflict_event}
    except CalendarConflictError as exc:
        return {"ok": False, "error": "event_overlap", "event": payload, "conflict": exc.conflict_event}
    return {"ok": True, "event": event}


def find_calendar_slot(start_local: datetime, *, dry_run: bool = False) -> Dict[str, Any]:
    candidate = start_local
    last: Dict[str, Any] = {}
    for _ in range(24):  # up to 6 hours in 15-minute increments
        local_time = candidate.time()
        in_window = local_time >= time(hour=23) or local_time < time(hour=6)
        if not in_window:
            return {"ok": False, "error": "no_open_overnight_slot", "last": last}
        card = create_sky_calendar_card(candidate, dry_run=dry_run)
        if card.get("ok"):
            event = card.get("event") if isinstance(card.get("event"), dict) else {}
            actual_start = str(event.get("start") or "").strip()
            if actual_start:
                try:
                    selected = datetime.fromisoformat(actual_start.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
                except Exception:
                    selected = candidate
            else:
                selected = candidate
            return {"ok": True, "scheduled_for_local": selected.isoformat(), "calendar": card}
        last = card
        candidate += timedelta(minutes=15)
    return {"ok": False, "error": "no_open_overnight_slot", "last": last}


def schedule(*, dry_run: bool = False, weeks: int = 1, start_local: Optional[datetime] = None) -> Dict[str, Any]:
    scheduled: List[Dict[str, Any]] = []
    first = start_local or _next_weekend_start()
    for index in range(max(1, int(weeks))):
        candidate = first + timedelta(days=7 * index)
        row = find_calendar_slot(candidate, dry_run=dry_run)
        row["requested_start_local"] = candidate.isoformat()
        scheduled.append(row)
    return {
        "ok": all(item.get("ok") for item in scheduled),
        "count": len(scheduled),
        "events": scheduled,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule Apollo weekly RAG/graph review on Sky calendar.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--date", default="")
    parser.add_argument("--time", default="02:30")
    args = parser.parse_args(argv)

    start = None
    if args.date:
        hh, mm = [int(part) for part in args.time.split(":", 1)]
        start = datetime.combine(date.fromisoformat(args.date), time(hour=hh, minute=mm), tzinfo=LOCAL_TZ)
    result = schedule(dry_run=args.dry_run, weeks=max(1, args.weeks), start_local=start)
    out_dir = APOLLO_ROOT / "reports" / "scheduled_sequences"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"{TEST_TYPE}_{stamp}.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

