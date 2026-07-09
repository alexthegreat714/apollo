from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

ENGINEERING_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINEERING_ROOT))

from Apollo.tools import run_apollo_homework_strategy_benchmark as runner


APOLLO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_TZ = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
TITLE = "Apollo homework/trade strategy benchmark"
PROJECT = runner.PROJECT
TEST_TYPE = runner.TEST_TYPE
CAPABILITY = runner.CAPABILITY
GX10_MODEL_REQUIREMENT = {
    "provider": "gx10_ollama",
    "url": "http://127.0.0.1:11434",
    "model": "gemma3:12b",
    "offline_model_only": True,
    "source_under_test": str(APOLLO_ROOT / "trading_homework.py"),
    "benchmark_entrypoint": str(Path(__file__).resolve().with_name("run_apollo_homework_strategy_benchmark.py")),
}


def _run_id(start_local: datetime) -> str:
    return f"{TEST_TYPE}_{start_local.strftime('%Y%m%d_%H%M')}"


def _event_id(start_local: datetime) -> str:
    return f"apollo-homework-strategy-benchmark-{start_local.strftime('%Y%m%d-%H%M')}"


def _report_path(start_local: datetime) -> str:
    run_id = _run_id(start_local)
    return str(runner.REPORT_ROOT / run_id / "report.md")


def _runner_command(start_local: datetime) -> str:
    script = Path(__file__).resolve().with_name("run_apollo_homework_strategy_benchmark.py")
    return f'{sys.executable or "python"} "{script}" --run-id {_run_id(start_local)}'


def _calendar_payload(start_local: datetime, *, duration_minutes: int = 45) -> Dict[str, Any]:
    run_id = _run_id(start_local)
    command = _runner_command(start_local)
    report_path = _report_path(start_local)
    end_local = start_local + timedelta(minutes=duration_minutes)
    notes = "\n".join(
        [
            "Benchmark Apollo homework runs, trade strategy analysis, risk/no-trade gates, and report quality.",
            f"Runner command: {command}",
            f"[local_command:{Path(__file__).resolve().with_name('run_apollo_homework_strategy_benchmark.py')} --run-id {run_id}]",
            f"[hook_signature:sky.local_command|apollo|{start_local.date().isoformat()}|apollo homework trade strategy benchmark]",
            f"Expected report: {report_path}",
            "The run updates Apollo/README.md between APOLLO_HOMEWORK_STRATEGY_BENCHMARK markers.",
            "This is a paper/simulation benchmark only. Live broker execution must remain disabled.",
            "LLM-heavy Apollo homework helpers must use the GX10 local tunnel when invoked by the tested process:",
            f"  - provider: {GX10_MODEL_REQUIREMENT['provider']}",
            f"  - url: {GX10_MODEL_REQUIREMENT['url']}",
            f"  - model: {GX10_MODEL_REQUIREMENT['model']}",
            "  - offline_model_only: true; do not pull models or require cloud login",
            f"  - source under test: {GX10_MODEL_REQUIREMENT['source_under_test']}",
        ]
    )
    return {
        "id": _event_id(start_local),
        "title": TITLE,
        "start": start_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "end": end_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "status": "pending",
        "source": "apollo",
        "trigger": "Queued Apollo homework/trade strategy benchmark.",
        "notes": notes,
        "test_queue": True,
        "project": PROJECT,
        "test_type": TEST_TYPE,
        "run_id": run_id,
        "capability": CAPABILITY,
        "verification_command": command,
        "report_path": report_path,
        "metadata": {
            "test_queue": True,
            "project": PROJECT,
            "test_type": TEST_TYPE,
            "capability": CAPABILITY,
            "runner_command": command,
            "readme_path": str(runner.README_PATH),
            "readme_section": "APOLLO_HOMEWORK_STRATEGY_BENCHMARK",
            "model_requirement": GX10_MODEL_REQUIREMENT,
        },
    }


def _next_overnight_start(now: Optional[datetime] = None) -> datetime:
    current = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    candidate = datetime.combine(current.date(), time(hour=23, minute=0), tzinfo=LOCAL_TZ)
    if candidate <= current + timedelta(minutes=5):
        candidate += timedelta(days=1)
    return candidate


def create_sky_calendar_card(start_local: datetime, *, dry_run: bool = False) -> Dict[str, Any]:
    payload = _calendar_payload(start_local)
    if dry_run:
        return {"ok": True, "dry_run": True, "event": payload}
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
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


def find_calendar_slot(
    start_local: datetime,
    *,
    dry_run: bool = False,
) -> Tuple[Optional[datetime], Dict[str, Any]]:
    candidate = start_local
    last: Dict[str, Any] = {}
    while True:
        local_time = candidate.time()
        in_window = local_time >= time(hour=23) or local_time < time(hour=6)
        if not in_window:
            return None, {"ok": False, "error": "no_open_overnight_slot", "last": last}
        card = create_sky_calendar_card(candidate, dry_run=dry_run)
        if card.get("ok"):
            event = card.get("event") if isinstance(card.get("event"), dict) else {}
            actual_start = str(event.get("start") or "").strip()
            if actual_start:
                try:
                    return datetime.fromisoformat(actual_start.replace("Z", "+00:00")).astimezone(LOCAL_TZ), card
                except Exception:
                    pass
            return candidate, card
        last = card
        candidate += timedelta(minutes=15)
        if candidate.time() >= time(hour=6) and candidate.date() != start_local.date():
            return None, {"ok": False, "error": "no_open_overnight_slot", "last": last}


def schedule(*, dry_run: bool = False, start_local: Optional[datetime] = None) -> Dict[str, Any]:
    start = start_local or _next_overnight_start()
    selected, card = find_calendar_slot(start, dry_run=dry_run)
    return {
        "ok": bool(card.get("ok")),
        "scheduled_for_local": selected.isoformat() if selected else "",
        "calendar": card,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule Apollo homework/trade strategy benchmark on Sky calendar.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", default="")
    parser.add_argument("--time", default="23:00")
    args = parser.parse_args(argv)

    start = None
    if args.date:
        hh, mm = [int(part) for part in args.time.split(":", 1)]
        start = datetime.combine(datetime.fromisoformat(args.date).date(), time(hour=hh, minute=mm), tzinfo=LOCAL_TZ)
    result = schedule(dry_run=args.dry_run, start_local=start)
    out_dir = APOLLO_ROOT / "reports" / "scheduled_sequences"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (result.get("scheduled_for_local") or _next_overnight_start().isoformat()).replace(":", "").replace("-", "")
    (out_dir / f"{TEST_TYPE}_{stamp}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
