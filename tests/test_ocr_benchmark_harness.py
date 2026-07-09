from __future__ import annotations

import json
import subprocess
from pathlib import Path

from Apollo.tools import ocr_benchmark_harness as harness


def test_run_study_computes_slice_delta_and_recommendation(monkeypatch):
    manifest = {
        "name": "study",
        "baseline_route": "ocr97",
        "documents": [
            {"id": "doc_a", "slice": "tiny_text_high_density_pages", "path": __file__, "capability_weights": {"tiny_text_recovery": 1.0}},
            {"id": "doc_b", "slice": "digital_finance_pdfs", "path": __file__, "capability_weights": {"literal_transcription_fidelity": 1.0}},
        ],
        "routes": [
            {"id": "ocr97", "label": "OCR97 baseline", "route_type": "baseline", "execution": {"kind": "literature_only"}},
            {"id": "got_ocr2", "label": "GOT", "route_type": "challenger", "execution": {"kind": "literature_only"}},
        ],
    }

    canned = {
        ("ocr97", "doc_a"): {
            "route_id": "ocr97",
            "route_label": "OCR97 baseline",
            "route_type": "baseline",
            "document_id": "doc_a",
            "slice": "tiny_text_high_density_pages",
            "status": "measured",
            "ok": True,
            "metrics": {"latency_ms": 100},
            "capability_cards": {"tiny_text_recovery": 70, "literal_transcription_fidelity": 70},
            "study_score": 70,
            "checks": {"required_missing": []},
            "error": "",
        },
        ("ocr97", "doc_b"): {
            "route_id": "ocr97",
            "route_label": "OCR97 baseline",
            "route_type": "baseline",
            "document_id": "doc_b",
            "slice": "digital_finance_pdfs",
            "status": "measured",
            "ok": True,
            "metrics": {"latency_ms": 100},
            "capability_cards": {"literal_transcription_fidelity": 80},
            "study_score": 80,
            "checks": {"required_missing": []},
            "error": "",
        },
        ("got_ocr2", "doc_a"): {
            "route_id": "got_ocr2",
            "route_label": "GOT",
            "route_type": "challenger",
            "document_id": "doc_a",
            "slice": "tiny_text_high_density_pages",
            "status": "measured",
            "ok": True,
            "metrics": {"latency_ms": 140},
            "capability_cards": {"tiny_text_recovery": 84, "literal_transcription_fidelity": 84},
            "study_score": 84,
            "checks": {"required_missing": []},
            "error": "",
        },
        ("got_ocr2", "doc_b"): {
            "route_id": "got_ocr2",
            "route_label": "GOT",
            "route_type": "challenger",
            "document_id": "doc_b",
            "slice": "digital_finance_pdfs",
            "status": "measured",
            "ok": True,
            "metrics": {"latency_ms": 140},
            "capability_cards": {"literal_transcription_fidelity": 76},
            "study_score": 76,
            "checks": {"required_missing": []},
            "error": "",
        },
    }

    def _fake_eval(doc, route, *, timeout_sec, paper_index):
        row = dict(canned[(route["id"], doc["id"])])
        row["research"] = []
        return row

    monkeypatch.setattr(harness, "_eval_document_route", _fake_eval)
    summary = harness.run_study(manifest, paper_index={"papers": []}, timeout_sec=5)

    delta_rows = {(item["route_id"], item["slice"]): item for item in summary["baseline_vs_challenger"]}
    assert delta_rows[("got_ocr2", "tiny_text_high_density_pages")]["judgment"] == "win"
    assert delta_rows[("got_ocr2", "tiny_text_high_density_pages")]["delta_vs_ocr97"] == 14
    assert summary["route_summaries"]["got_ocr2"]["recommended_use_mode"] == "routed_specialist"


def test_download_papers_writes_index(tmp_path: Path):
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    paper_manifest = {
        "papers": [
            {
                "id": "paper_a",
                "title": "Paper A",
                "pdf_url": source_pdf.as_uri(),
                "local_pdf": "papers/paper_a.pdf",
                "source_url": "https://example.test/paper_a",
                "publish_date": "2026-01-01",
                "relevance_note": "test",
            }
        ]
    }
    output_root = tmp_path / "cache"
    index_output = output_root / "papers_index.json"
    result = harness.download_papers(
        paper_manifest,
        output_root=output_root,
        index_output=index_output,
        force=False,
        timeout_sec=10,
    )
    assert index_output.exists()
    payload = json.loads(index_output.read_text(encoding="utf-8"))
    assert payload["papers"][0]["download_status"] in {"downloaded", "cached"}
    assert (output_root / "papers" / "paper_a.pdf").exists()
    assert result["papers"][0]["sha256"]


def test_render_study_markdown_includes_local_pdf():
    report = harness.render_study_markdown(
        {
            "baseline_route": "ocr97",
            "baseline_label": "OCR97 baseline",
            "route_findings": [],
            "baseline_vs_challenger": [],
            "paper_index": {
                "papers": [
                    {
                        "id": "ocrbench_2023",
                        "title": "OCRBench",
                        "publish_date": "2023-05-13",
                        "source_url": "https://arxiv.org/abs/2305.07895",
                        "local_pdf": "papers/ocrbench_2023.pdf",
                        "relevance_note": "benchmark",
                    }
                ]
            },
            "failure_exemplars": [],
        }
    )
    assert "papers/ocrbench_2023.pdf" in report
    assert "https://arxiv.org/abs/2305.07895" in report


def test_eval_document_route_supports_node_cli_json(monkeypatch, tmp_path: Path):
    doc_path = tmp_path / "fixture.png"
    doc_path.write_bytes(b"fake")
    route = {
        "id": "client_ocr",
        "label": "Client OCR",
        "route_type": "challenger",
        "execution": {
            "kind": "node_cli_json",
            "command": ["node", "Apollo/tools/ocr_challenger_routes/runtime/client_ocr_route.mjs"],
        },
    }
    doc = {
        "id": "fixture",
        "label": "Fixture",
        "slice": "tiny_text_high_density_pages",
        "path": str(doc_path),
        "must_contain": ["Invoice"],
        "capability_weights": {"tiny_text_recovery": 1.0},
    }

    def _fake_run(command, **kwargs):
        assert command[0] == "node"
        assert command[-2] == "--path"
        assert command[-1] == str(doc_path.resolve())
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "engine": "client_ocr",
                    "route": "client_ocr_browser",
                    "text": "Invoice Total 99",
                    "markdown": "Invoice Total 99",
                    "quality": {
                        "score": 0.91,
                        "confidence": 0.88,
                        "chars": 16,
                        "structure_score": 0.84,
                        "numeric_fidelity_score": 0.79,
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(harness.subprocess, "run", _fake_run)
    row = harness._eval_document_route(doc, route, timeout_sec=30, paper_index={"papers": []})

    assert row["status"] == "measured"
    assert row["ok"] is True
    assert row["metrics"]["quality_lane"] == 91
    assert row["checks"]["required_missing"] == []
