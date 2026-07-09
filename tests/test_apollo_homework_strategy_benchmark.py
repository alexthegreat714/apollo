from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from Sky.core import calendar_store
from Sky.services import test_run_reports as trr

from Apollo.tools import run_apollo_homework_strategy_benchmark as runner
from Apollo.tools import schedule_apollo_homework_strategy_benchmark as scheduler


def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_store, "STORE_PATH", tmp_path / "calendar_events.json")
    monkeypatch.setattr(calendar_store, "LOG_DIR", tmp_path)
    calendar_store._STORE_CACHE = None
    calendar_store._STORE_MTIME_NS = None


def _completed_process(returncode: int = 0, stdout: str = "ok"):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="" if returncode == 0 else "failed")


def test_runner_dry_run_command_sequence_is_ordered():
    commands = runner.default_commands()

    assert commands[0].startswith(__import__("sys").executable)
    assert "-m py_compile" in commands[0]
    assert "test_homework_trade_pipeline.py" in commands[1]
    assert "test_trade_cycle.py" in commands[2]
    assert "test_apollo_homework_strategy_benchmark.py" in commands[3]


def test_passing_sequence_writes_reports_and_readme(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "README_PATH", tmp_path / "README.md")
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: _completed_process())
    runner.README_PATH.write_text("# Apollo\n", encoding="utf-8")

    result = runner.run_sequence(
        run_id="apollo_homework_strategy_unit",
        report_root=tmp_path / "reports",
        sky_report_root=tmp_path / "sky_reports",
    )

    assert result["ok"] is True
    assert result["score"] >= 80
    assert result["status"] == "passed"
    assert (tmp_path / "reports" / "apollo_homework_strategy_unit" / "report.md").exists()
    assert runner.README_START in runner.README_PATH.read_text(encoding="utf-8")
    assert result["sky_report"]["ok"] is True
    ledger = trr.load_project_capability_ledger(root=tmp_path / "sky_reports")
    assert ledger["projects"]["apollo"]["latest_score"] >= 80


def test_failing_command_still_writes_valid_report_payload(monkeypatch, tmp_path):
    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        return _completed_process(returncode=1 if calls["count"] == 2 else 0)

    monkeypatch.setattr(runner, "README_PATH", tmp_path / "README.md")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.README_PATH.write_text("# Apollo\n", encoding="utf-8")

    result = runner.run_sequence(
        run_id="apollo_homework_strategy_fail",
        report_root=tmp_path / "reports",
        sky_report_root=tmp_path / "sky_reports",
    )
    validation = trr.validate_test_queue_report_payload(result["capability_report"])

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["capability_report"]["diagnostic"]["category"] == "verification"
    assert validation["ok"] is True
    assert "homework candidate selection" in " ".join(result["weak_areas"])


def test_scheduler_dry_run_calendar_payload_contains_required_fields():
    start = datetime(2026, 5, 20, 23, 0, tzinfo=ZoneInfo("America/Chicago"))
    result = scheduler.schedule(dry_run=True, start_local=start)
    event = result["calendar"]["event"]

    assert result["ok"] is True
    assert event["title"] == "Apollo homework/trade strategy benchmark"
    assert event["test_queue"] is True
    assert event["project"] == "apollo"
    assert event["test_type"] == runner.TEST_TYPE
    assert event["capability"] == runner.CAPABILITY
    assert event["verification_command"].startswith(__import__("sys").executable)
    assert event["report_path"].endswith("report.md")
    assert "Apollo/README.md" in event["notes"]
    assert event["metadata"]["model_requirement"]["provider"] == "gx10_ollama"
    assert event["metadata"]["model_requirement"]["model"] == "gemma3:12b"
    assert event["metadata"]["model_requirement"]["offline_model_only"] is True


def test_scheduler_calendar_event_is_accepted_and_conflict_advances(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    calendar_store.create_event(
        {
            "title": "Existing overnight benchmark",
            "start": "2026-05-21T04:00:00Z",
            "end": "2026-05-21T04:45:00Z",
            "status": "pending",
            "source": "sky",
            "trigger": "",
            "notes": "",
            "test_queue": True,
            "project": "apollo",
            "test_type": "existing_benchmark",
            "capability": "existing",
            "verification_command": "pytest",
            "report_path": "report.md",
        }
    )
    start = datetime(2026, 5, 20, 23, 0, tzinfo=ZoneInfo("America/Chicago"))

    selected, result = scheduler.find_calendar_slot(start)

    assert result["ok"] is True
    assert selected == datetime(2026, 5, 20, 23, 45, tzinfo=ZoneInfo("America/Chicago"))
    event = result["event"]
    assert event["metadata"]["test_queue_report_requirement"]["required"] is True
    assert event["report_required"] is True
    assert event["verification_command"]


def test_report_payload_is_json_safe(tmp_path):
    result = {
        "run_id": "apollo_homework_strategy_payload",
        "status": "passed",
        "score": 90,
        "components": {"module_import_health": 20},
        "commands": [{"command": "pytest Apollo/tests/test_homework_trade_pipeline.py -q", "ok": True}],
        "weak_areas": [],
        "completed_at": "2026-05-20T04:00:00Z",
    }

    payload = runner.build_report_payload(result, report_path=tmp_path / "report.md", summary_path=tmp_path / "summary.json")

    assert trr.validate_test_queue_report_payload(payload)["ok"] is True
    json.dumps(payload)
