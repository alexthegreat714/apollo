from __future__ import annotations

from pathlib import Path

from Apollo.tools.ocr_phase2_truth_gate import (
    OCR_TOOL_PATH,
    README_PATH,
    build_truth_matrix,
    find_unsupported_readme_claims,
    parse_pytest_output,
    render_readme_section,
)


def test_build_truth_matrix_reports_live_line_count_and_symbols():
    matrix = build_truth_matrix({"command": "pytest -q", "passed": 90, "returncode": 0})
    live_line_count = len(OCR_TOOL_PATH.read_text(encoding="utf-8").splitlines())
    assert matrix["ocr_tool"]["line_count"] == live_line_count
    assert matrix["ocr_tool"]["symbols"]["_preprocess_variants"] is True
    assert matrix["ocr_tool"]["symbols"]["_line_vote_majority"] is True
    assert matrix["items"]["verification.finbert_verifier"]["classification"] in {
        "implemented_literal",
        "service_hook_only",
    }


def test_parse_pytest_output_extracts_exact_pass_count():
    parsed = parse_pytest_output("........................\n90 passed, 1 warning in 12.34s\n")
    assert parsed["passed"] == 90
    assert parsed["warnings"] == 1


def test_find_unsupported_readme_claims_rejects_overclaim():
    matrix = build_truth_matrix({"command": "pytest -q", "passed": 90, "returncode": 0})
    matrix["items"]["verification.finbert_verifier"]["classification"] = "service_hook_only"
    readme_text = """
    ## OCR Phase 2
    Phase 2 complete.
    FinBERT fully implemented.
    Truth matrix: artifacts/ocr_phase2/phase2_truth_matrix.json
    """
    issues = find_unsupported_readme_claims(readme_text, matrix, "artifacts/ocr_phase2/phase2_validation_20260420T000000Z.md")
    assert "phase2_complete_claim_without_literal_backing" in issues
    assert "unsupported_claim:verification.finbert_verifier" in issues
    assert "validation_artifact_path_missing" in issues


def test_render_readme_section_includes_artifact_paths_and_current_result():
    matrix = build_truth_matrix({"command": "pytest -q", "passed": 90, "returncode": 0})
    section = render_readme_section(matrix, "artifacts/ocr_phase2/phase2_validation_20260420T000000Z.md")
    assert "artifacts/ocr_phase2/phase2_truth_matrix.json" in section
    assert "phase2_validation_20260420T000000Z.md" in section
    assert "`90 passed`" in section


def test_current_readme_has_phase2_section_anchor():
    text = README_PATH.read_text(encoding="utf-8")
    assert "OCR Pipeline Phase 2" in text or "OCR Phase 2" in text
