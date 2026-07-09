from __future__ import annotations

import importlib.util
import json
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_ocr97_real_doc_benchmark.py"
SCHED_PATH = Path(__file__).resolve().parents[1] / "tools" / "schedule_ocr97_real_doc_benchmark.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_module(RUNNER_PATH, "ocr97_real_doc_runner")
schedule = _load_module(SCHED_PATH, "ocr97_real_doc_schedule")


def test_manifest_has_real_public_documents() -> None:
    manifest = runner.load_manifest(Path(__file__).resolve().parents[1] / "config" / "ocr97_real_documents_manifest.json")

    assert manifest["documents"]
    assert len(manifest["documents"]) >= 3
    assert all(str(doc["url"]).startswith("https://") for doc in manifest["documents"])
    assert any("forms" in doc.get("capability_focus", []) for doc in manifest["documents"])


def test_score_document_defends_when_terms_quality_and_size_are_present() -> None:
    doc = {
        "id": "sample",
        "title": "Sample",
        "min_chars": 100,
        "expected_terms": ["Federal Reserve", "asset valuations"],
        "expected_regex": ["November\\s+2024"],
    }
    result = {
        "ok": True,
        "engine": "native_pdf_text",
        "quality": {"score": 0.95},
        "markdown": "Federal Reserve November 2024 asset valuations 12 percent " * 8,
    }

    row = runner.score_document(doc, result, {"ok": True, "path": "sample.pdf"})

    assert row["score"] >= 90
    assert row["missing_terms"] == []
    assert row["missing_regex"] == []


def test_summary_requires_minimum_document_score_to_defend_97() -> None:
    rows = [
        {"ok": True, "score": 96},
        {"ok": True, "score": 79},
    ]

    summary = runner.summarize_scores(rows, score_floor=90)

    assert summary["average_score"] >= 85
    assert summary["verdict"] != "defends_current_97_with_real_document_evidence"
    assert summary["recommended_score"] < 97


def test_readme_update_replaces_generated_block(tmp_path, monkeypatch) -> None:
    readme = tmp_path / "README_OCR97.md"
    readme.write_text("head\n\nold\n", encoding="utf-8")
    monkeypatch.setattr(runner, "README_PATH", readme)

    runner.update_readme("report", {"average_score": 91, "minimum_score": 86, "verdict": "defends", "recommended_score": 97}, "run1", tmp_path / "report.md")
    runner.update_readme("report", {"average_score": 83, "minimum_score": 70, "verdict": "rescore", "recommended_score": 86}, "run2", tmp_path / "report2.md")

    text = readme.read_text(encoding="utf-8")
    assert text.count(runner.README_START) == 1
    assert "run2" in text
    assert "run1" not in text


def test_run_benchmark_uses_mocks_and_writes_artifacts(tmp_path, monkeypatch) -> None:
    manifest_path = tmp_path / "manifest.json"
    source_path = tmp_path / "doc.pdf"
    source_path.write_bytes(b"%PDF fake")
    manifest_path.write_text(
        json.dumps(
            {
                "name": "mock",
                "score_floor_to_defend_97": 90,
                "documents": [
                    {
                        "id": "doc",
                        "title": "Doc",
                        "url": "https://example.test/doc.pdf",
                        "min_chars": 50,
                        "expected_terms": ["Form W-4"],
                        "expected_regex": ["Step\\s+1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "download_document", lambda doc, cache, force=False: (source_path, {"ok": True, "path": str(source_path), "bytes": 9}))
    monkeypatch.setattr(
        runner,
        "run_ocr97_on_document",
        lambda path, doc, max_chars, route_mode: {"ok": True, "engine": "mock", "quality": {"score": 1}, "markdown": "Form W-4 Step 1 2026 " * 8},
    )

    payload = runner.run_benchmark(
        manifest_path=manifest_path,
        report_root=tmp_path / "reports",
        cache_root=tmp_path / "cache",
        update_readme_flag=False,
        download_only=False,
        force_download=False,
        route_mode="quality_first",
        max_chars=1000,
        compare_baseline=False,
    )

    assert payload["summary"]["recommended_score"] == 97
    assert Path(payload["artifacts"]["summary"]).exists()
    assert Path(payload["artifacts"]["report"]).exists()


def test_run_benchmark_writes_old_ocr_comparison(tmp_path, monkeypatch) -> None:
    manifest_path = tmp_path / "manifest.json"
    source_path = tmp_path / "doc.pdf"
    source_path.write_bytes(b"%PDF fake")
    manifest_path.write_text(
        json.dumps(
            {
                "name": "mock",
                "score_floor_to_defend_97": 90,
                "documents": [
                    {
                        "id": "doc",
                        "title": "Doc",
                        "url": "https://example.test/doc.pdf",
                        "min_chars": 50,
                        "expected_terms": ["Form W-4"],
                        "expected_regex": ["Step\\s+1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "download_document", lambda doc, cache, force=False: (source_path, {"ok": True, "path": str(source_path), "bytes": 9}))
    monkeypatch.setattr(
        runner,
        "run_ocr97_on_document",
        lambda path, doc, max_chars, route_mode: {
            "ok": True,
            "engine": "ocr97",
            "quality": {"score": 1},
            "markdown": "Form W-4 Step 1 2026 " * 8,
            "latency_ms": 100,
        },
    )
    monkeypatch.setattr(
        runner,
        "run_baseline_on_document",
        lambda path, doc, max_chars: {
            "ok": True,
            "engine": "tesseract",
            "quality": {"score": 0.2},
            "markdown": "Form W-4 only",
            "latency_ms": 10,
        },
    )

    payload = runner.run_benchmark(
        manifest_path=manifest_path,
        report_root=tmp_path / "reports",
        cache_root=tmp_path / "cache",
        update_readme_flag=False,
        download_only=False,
        force_download=False,
        route_mode="quality_first",
        max_chars=1000,
        compare_baseline=True,
    )

    assert payload["comparison_summary"]["verdict"] == "ocr97_better_on_decisive_docs"
    assert payload["comparisons"][0]["judgment"] == "ocr97_better"
    assert payload["baseline_documents"][0]["engine"] == "tesseract"


def test_schedule_dry_run_includes_task_and_calendar_hook() -> None:
    payload = schedule.main(["--dry-run", "--date", "2026-05-13", "--time", "23:15"])

    assert payload == 0


def test_schedule_payload_has_ps1_start_hook_and_forge_fallback() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start = datetime(2026, 5, 13, 0, 45, tzinfo=ZoneInfo("America/Chicago"))
    payload = schedule._calendar_payload(start, "OCR97RealDocumentBenchmark20260513_0045")
    command = schedule._run_command(start)

    assert "run_ocr97_real_doc_benchmark_scheduled.ps1" in payload["notes"]
    assert "[local_command:" in payload["notes"]
    assert "Forge first" in payload["notes"] or "Forge-first" in payload["notes"]
    assert "VM Codex fallback" in payload["notes"]
    assert "ocr97_real_doc_failure_fallback.py" in payload["notes"]
    assert payload["test_queue"] is True
    assert payload["project"] == "ocr97"
    assert payload["test_type"] == "ocr97_calendar_run"
    assert payload["capability"] == "real_document_ocr_capability"
    assert payload["metadata"]["model_requirement"]["provider"] == "gx10_ollama"
    assert payload["metadata"]["model_requirement"]["model"] == "qwen3-vl:32b"
    assert payload["metadata"]["model_requirement"]["offline_model_only"] is True
    assert len(command) <= 261


def test_runner_defaults_to_gx10_qwen3_vl_for_ocr() -> None:
    assert runner.GX10_OLLAMA_URL == "http://127.0.0.1:11434"
    assert runner.GX10_OCR_MODEL == "qwen3-vl:32b"
    assert runner.os.environ["APOLLO_GB10_QWEN_OCR_MODEL"] == "qwen3-vl:32b"


def test_schedule_task_card_points_to_calendar_and_fallback() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start = datetime(2026, 5, 13, 0, 45, tzinfo=ZoneInfo("America/Chicago"))
    payload = schedule.create_sky_task_card(start, "OCR97RealDocumentBenchmark20260513_0045", dry_run=True)

    assert payload["ok"]
    task_payload = payload["payload"]
    assert task_payload["dispatch"] is False
    assert task_payload["metadata"]["calendar_event_id"] == "apollo-ocr97-real-docs-20260513-0045"
    assert "Forge-first repair" in task_payload["text"]
    assert "VM Codex fallback" in task_payload["text"]
