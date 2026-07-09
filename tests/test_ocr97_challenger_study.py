from __future__ import annotations

import importlib.util
import json
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_ocr97_challenger_study.py"
SCHED_PATH = Path(__file__).resolve().parents[1] / "tools" / "schedule_ocr97_challenger_study.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_module(RUNNER_PATH, "ocr97_challenger_runner")
schedule = _load_module(SCHED_PATH, "ocr97_challenger_schedule")


def test_run_challenger_study_writes_latest_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "REPORT_ROOT", tmp_path / "reports")
    monkeypatch.setattr(runner, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(runner, "PAPER_INDEX_PATH", tmp_path / "papers.json")
    runner.MANIFEST_PATH.write_text(
        json.dumps({"name": "study", "baseline_route": "ocr97", "documents": [], "routes": []}),
        encoding="utf-8",
    )
    runner.PAPER_INDEX_PATH.write_text(json.dumps({"papers": []}), encoding="utf-8")
    monkeypatch.setattr(runner, "ensure_fixtures", lambda: [])
    monkeypatch.setattr(
        runner.harness,
        "run_study",
        lambda manifest, paper_index, timeout_sec: {
            "baseline_route": "ocr97",
            "route_findings": [{"route_id": "client_ocr", "recommended_use_mode": "fallback"}],
            "baseline_vs_challenger": [],
            "paper_index": {"papers": []},
            "failure_exemplars": [],
        },
    )
    monkeypatch.setattr(runner.harness, "render_study_markdown", lambda summary: "# OCR97 challenger report\n")

    payload = runner.run_challenger_study(run_id="study_a", timeout_sec=30)

    assert payload["ok"] is True
    assert Path(payload["report_path"]).exists()
    assert Path(payload["latest_report_path"]).exists()
    index_payload = json.loads(Path(payload["latest_index_path"]).read_text(encoding="utf-8"))
    assert index_payload["run_id"] == "study_a"
    assert index_payload["recommended_modes"]["client_ocr"] == "fallback"


def test_schedule_payload_marks_report_path_and_routes() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start = datetime(2026, 6, 1, 23, 45, tzinfo=ZoneInfo("America/Chicago"))
    payload = schedule._calendar_payload(start, "OCR97ChallengerStudy20260601_2345")

    assert payload["title"] == "OCR97 challenger study overnight"
    assert payload["test_queue"] is True
    assert payload["test_type"] == "ocr97_challenger_study"
    assert "client-ocr" in payload["notes"]
    assert "ollama-ocr" in payload["notes"]
    assert payload["report_path"].endswith("Apollo\\reports\\ocr97_challenger_study\\latest_report.md")
    assert payload["metadata"]["report_path"].endswith("Apollo\\reports\\ocr97_challenger_study\\latest_report.md")
