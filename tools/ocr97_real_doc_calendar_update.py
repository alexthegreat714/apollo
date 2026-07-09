from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _load_sky_calendar() -> tuple[Any, Any]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Sky.core.calendar_store import list_events, update_event

    return list_events, update_event


def _read_json_from_stdout_log(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return {}
    return {}


def _event_task_name(event: Dict[str, Any]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    contract = metadata.get("run_contract") if isinstance(metadata.get("run_contract"), dict) else {}
    for value in (
        contract.get("start_ref"),
        metadata.get("task_scheduler_name"),
        event.get("run_id"),
    ):
        if value:
            return str(value)
    notes = str(event.get("notes") or "")
    match = re.search(r"Task Scheduler:\s*([A-Za-z0-9_.-]+)", notes)
    return match.group(1) if match else ""


def find_target_event(event_id: str, task_name: str) -> Dict[str, Any]:
    list_events, _ = _load_sky_calendar()
    now = _utc_now()
    events = list_events(start=_iso(now - timedelta(days=2)), end=_iso(now + timedelta(days=7)))
    if event_id:
        for event in events:
            if str(event.get("id") or "") == event_id:
                return event
    if task_name:
        candidates = [event for event in events if _event_task_name(event) == task_name]
        candidates.sort(key=lambda item: str(item.get("start") or ""))
        if candidates:
            return candidates[0]
    timed_candidates = []
    for event in events:
        title = str(event.get("title") or "").lower()
        status = str(event.get("status") or "").lower()
        if "ocr97" not in title or status not in {"pending", "in_work", "partial"}:
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if isinstance(metadata.get("ocr97_report"), dict) and metadata["ocr97_report"].get("report_path"):
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
            timed_candidates.append((abs((now - start).total_seconds()), event))
    if timed_candidates:
        timed_candidates.sort(key=lambda item: item[0])
        return timed_candidates[0][1]
    raise RuntimeError("ocr97_calendar_event_not_found")


def _append_note(notes: str, line: str) -> str:
    if line in notes:
        return notes
    next_notes = (notes.rstrip() + ("\n\n" if notes.strip() else "") + line).strip()
    if len(next_notes) <= 4000:
        return next_notes
    suffix = "\n\n" + line
    return (notes[: max(0, 4000 - len(suffix))].rstrip() + suffix).strip()


def _register_capability_ledger(
    summary: Dict[str, Any],
    report_path: str,
    summary_path: str,
    raw_path: str,
    task_name: str,
    exit_code: int,
) -> Dict[str, Any]:
    try:
        if str(ENGINEERING_ROOT) not in sys.path:
            sys.path.insert(0, str(ENGINEERING_ROOT))
        from Sky.services.test_run_reports import record_test_run_report

        recommended = summary.get("recommended_score")
        score = float(recommended) if recommended is not None else (100.0 if exit_code == 0 else 0.0)
        artifact_paths = [p for p in [report_path, summary_path, raw_path] if p]
        payload = {
            "project": "ocr97",
            "project_label": "OCR97",
            "test_type": "ocr97_real_doc_benchmark",
            "capability": "real_document_ocr_capability",
            "status": "complete" if exit_code == 0 else "failed",
            "score": score,
            "summary": (
                f"OCR97 real-doc benchmark: avg={summary.get('average_score')} "
                f"min={summary.get('minimum_score')} recommended={recommended} "
                f"verdict={summary.get('verdict')}"
            ),
            "completed_at": _iso(),
            "artifact_paths": artifact_paths,
            "run_id": task_name or f"ocr97_real_doc_{_iso().replace(':', '').replace('-', '')[:15]}",
            "tests_improved": ["ocr97_real_doc"] if exit_code == 0 else [],
            "tests_confirmed": ["ocr97_real_doc"] if exit_code == 0 else [],
            "capabilities_confirmed": ["real_document_ocr_capability"] if exit_code == 0 else [],
        }
        result = record_test_run_report(payload)
        return {"ok": True, "ledger_run_id": result.get("run_id"), "score": score}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def update_calendar(event_id: str, task_name: str, stdout_log: Path, exit_code: int) -> Dict[str, Any]:
    event = find_target_event(event_id, task_name)
    _, update_event = _load_sky_calendar()
    payload = _read_json_from_stdout_log(stdout_log)
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    report_path = str(artifacts.get("report") or "")
    summary_path = str(artifacts.get("summary") or "")
    raw_path = str(artifacts.get("raw") or "")
    recommended = summary.get("recommended_score")
    average = summary.get("average_score")
    minimum = summary.get("minimum_score")
    verdict = str(summary.get("verdict") or "")
    status = "understood" if exit_code == 0 else "failed"
    timestamp = _iso()
    note = (
        f"[ocr97_report:{timestamp}] Task `{task_name}` completed with exit `{exit_code}`. "
        f"Report: `{report_path or 'unavailable'}`. Summary: `{summary_path or 'unavailable'}`. "
        f"Average: `{average}`. Minimum: `{minimum}`. Recommended score: `{recommended}`. Verdict: `{verdict}`."
    )
    notes = _append_note(str(event.get("notes") or ""), note)
    patch: Dict[str, Any] = {
        "status": status,
        "trigger": (
            f"OCR97 report written: {report_path}"
            if exit_code == 0 and report_path
            else f"OCR97 scheduled run exited with {exit_code}; see logs."
        ),
        "notes": notes,
    }
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    metadata = dict(metadata)
    ledger_result = _register_capability_ledger(summary, report_path, summary_path, raw_path, task_name, exit_code)
    metadata["ocr97_report"] = {
        "updated_at": timestamp,
        "task_name": task_name,
        "exit_code": exit_code,
        "stdout_log": str(stdout_log),
        "report_path": report_path,
        "summary_path": summary_path,
        "raw_path": raw_path,
        "summary": summary,
        "ledger_registration": ledger_result,
    }
    patch["metadata"] = metadata
    if report_path:
        patch["report_path"] = report_path
    updated = update_event(str(event.get("id") or ""), patch)
    return {"ok": True, "event_id": updated.get("id"), "status": status, "report_path": report_path, "summary_path": summary_path, "ledger": ledger_result}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attach OCR97 real-document report artifacts to the Sky calendar card.")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--task-name", default="")
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args(argv)
    result = update_calendar(args.event_id, args.task_name, args.stdout_log, args.exit_code)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
