"""OCR97 spark — fast, no ML model downloads required.

Validates:
  1. All 4 service items classify as implemented_literal when Aegis health
     endpoints return 200.
  2. All 4 classify as service_hook_only when endpoints are unreachable.
  3. _finbert_eval_signal / _tableformer_or_lgpma_reconstruct integration with
     the verification status builder.
  4. OCR pipeline → quality gate end-to-end (monkeypatched engines).
  5. Semantic vocabulary expansion covers equity-market terms.

Run: pytest Apollo/tests/test_ocr_spark.py -q
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

import common.ocr_dual_tool as odt
from common.ocr_dual_tool import (
    _finance_consistency_checks,
    _finbert_eval_signal,
    _phase2_literal_service_state,
    _phase2_preprocessing_status,
    _phase2_verification_status,
    _structure_score,
    _tableformer_or_lgpma_reconstruct,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EQUITY_TEXT = (
    "Equity market risk rose sharply this quarter. Stock valuations in the "
    "technology sector reflected elevated volatility. Trade flows and market "
    "microstructure indicators suggest elevated short-term volatility. "
    "Risk-adjusted returns across equity sectors remain below historical norms."
)

_BALANCE_SHEET_TEXT = (
    "Assets 1000\nLiabilities 600\nEquity 400\n"
    "Risk: 12%\nMargin: 25000\nPosition: long\n"
    "| Asset | Value |\n| Cash | 400 |\n| Bonds | 600 |\n"
)


def _stub_health_ok(url, timeout=5):
    """Simulates a reachable Aegis health endpoint."""
    m = MagicMock()
    m.ok = True
    m.json.return_value = {
        "ok": True,
        "runtime_loaded": False,
        "backend": "",
        "provider": "aegis_stub_service",
    }
    return m


def _stub_health_fail(url, timeout=5):
    raise ConnectionRefusedError("stub: service unreachable")


# ---------------------------------------------------------------------------
# 1. _phase2_literal_service_state: implemented_literal when service is up
# ---------------------------------------------------------------------------

def test_literal_service_state_implemented_literal_when_service_reachable():
    with patch("common.ocr_dual_tool.requests.get", side_effect=_stub_health_ok):
        with patch("common.ocr_dual_tool._http_endpoint_reachable", return_value={"ok": True}):
            result = _phase2_literal_service_state(
                "http://127.0.0.1:5221/ocr/docunet/rectify",
                mode_name="docunet",
            )
    assert result["classification"] == "implemented_literal"
    assert result["healthy"] is True
    assert "model_warm" in result


def test_literal_service_state_service_hook_only_when_unreachable():
    with patch("common.ocr_dual_tool.requests.get", side_effect=_stub_health_fail):
        with patch("common.ocr_dual_tool._http_endpoint_reachable", return_value={"ok": False, "error": "connect_failed"}):
            result = _phase2_literal_service_state(
                "http://127.0.0.1:5221/ocr/realesrgan/upscale",
                mode_name="realesrgan",
            )
    assert result["classification"] == "service_hook_only"
    assert result["healthy"] is False


def test_literal_service_state_service_hook_only_when_url_unset():
    result = _phase2_literal_service_state("", mode_name="docunet")
    assert result["classification"] == "service_hook_only"
    assert result["configured"] is False


# ---------------------------------------------------------------------------
# 2. _phase2_verification_status: finbert and tableformer via service probe
# ---------------------------------------------------------------------------

def test_verification_finbert_implemented_literal_when_service_reachable(monkeypatch):
    monkeypatch.setattr(odt, "DEFAULT_OCR_FINBERT_URL", "http://127.0.0.1:5221/ocr/finbert/eval")
    with patch("common.ocr_dual_tool.requests.get", side_effect=_stub_health_ok):
        with patch("common.ocr_dual_tool._http_endpoint_reachable", return_value={"ok": True}):
            finance = _finance_consistency_checks(_BALANCE_SHEET_TEXT)
            finbert = {"enabled": True, "status": "degraded", "mode": "heuristic_proxy"}
            table_r = {"mode": "heuristic", "ok": False, "tables": []}
            status = _phase2_verification_status(finance, finbert, table_r)
    assert status["finbert"]["classification"] == "implemented_literal"
    assert status["finbert"]["live"] is False
    assert status["finbert"]["service_reachable"] is True


def test_verification_finbert_service_hook_when_url_unset(monkeypatch):
    monkeypatch.setattr(odt, "DEFAULT_OCR_FINBERT_URL", "")
    finance = _finance_consistency_checks(_BALANCE_SHEET_TEXT)
    finbert = {"enabled": True, "status": "degraded", "mode": "heuristic_proxy"}
    table_r = {"mode": "heuristic", "ok": False, "tables": []}
    status = _phase2_verification_status(finance, finbert, table_r)
    assert status["finbert"]["classification"] == "service_hook_only"


def test_verification_tableformer_implemented_literal_when_service_reachable(monkeypatch):
    monkeypatch.setattr(odt, "DEFAULT_OCR_TABLEFORMER_URL", "http://127.0.0.1:5221/ocr/table/reconstruct")
    with patch("common.ocr_dual_tool.requests.get", side_effect=_stub_health_ok):
        with patch("common.ocr_dual_tool._http_endpoint_reachable", return_value={"ok": True}):
            finance = _finance_consistency_checks(_BALANCE_SHEET_TEXT)
            finbert = {"enabled": True, "status": "degraded", "mode": "heuristic_proxy"}
            table_r = {"mode": "heuristic", "ok": False, "tables": []}
            status = _phase2_verification_status(finance, finbert, table_r)
    assert status["table_reconstruction"]["classification"] == "implemented_literal"
    assert status["table_reconstruction"]["live"] is False
    assert status["table_reconstruction"]["service_reachable"] is True


def test_verification_all_four_implemented_literal_when_all_services_reachable(monkeypatch):
    monkeypatch.setattr(odt, "DEFAULT_OCR_FINBERT_URL", "http://127.0.0.1:5221/ocr/finbert/eval")
    monkeypatch.setattr(odt, "DEFAULT_OCR_TABLEFORMER_URL", "http://127.0.0.1:5221/ocr/table/reconstruct")
    monkeypatch.setattr(odt, "DEFAULT_OCR_DOCUNET_URL", "http://127.0.0.1:5221/ocr/docunet/rectify")
    monkeypatch.setattr(odt, "DEFAULT_OCR_REALESRGAN_URL", "http://127.0.0.1:5221/ocr/realesrgan/upscale")
    with patch("common.ocr_dual_tool.requests.get", side_effect=_stub_health_ok):
        with patch("common.ocr_dual_tool._http_endpoint_reachable", return_value={"ok": True}):
            preprocessing = _phase2_preprocessing_status()
            finance = _finance_consistency_checks(_BALANCE_SHEET_TEXT)
            finbert = {"enabled": True, "status": "degraded", "mode": "heuristic_proxy"}
            table_r = {"mode": "heuristic", "ok": False, "tables": []}
            verification = _phase2_verification_status(finance, finbert, table_r)
    assert preprocessing["docunet"]["classification"] == "implemented_literal", "docunet should be implemented_literal"
    assert preprocessing["realesrgan"]["classification"] == "implemented_literal", "realesrgan should be implemented_literal"
    assert verification["finbert"]["classification"] == "implemented_literal", "finbert should be implemented_literal"
    assert verification["table_reconstruction"]["classification"] == "implemented_literal", "tableformer should be implemented_literal"


# ---------------------------------------------------------------------------
# 3. Semantic vocabulary — equity market terms score semantic hits
# ---------------------------------------------------------------------------

def test_equity_terms_in_nightly_pipeline_semantic_vocab():
    """New terms added to _OCR_SEMANTIC_TERMS must cover equity-market vocabulary."""
    try:
        from Apollo.nightly_pipeline import _semantic_section_hits, _OCR_SEMANTIC_TERMS
    except ImportError:
        pytest.skip("nightly_pipeline not importable in this context")
    hits = _semantic_section_hits(_EQUITY_TEXT)
    assert hits >= 4, f"Expected >=4 semantic hits on equity text, got {hits}. Terms: {_OCR_SEMANTIC_TERMS}"


def test_equity_terms_present_in_semantic_vocab():
    try:
        from Apollo.nightly_pipeline import _OCR_SEMANTIC_TERMS
    except ImportError:
        pytest.skip("nightly_pipeline not importable in this context")
    required = {"stock", "equity", "market", "trade", "volatility", "sector"}
    missing = required - set(_OCR_SEMANTIC_TERMS)
    assert not missing, f"Missing equity-market terms from _OCR_SEMANTIC_TERMS: {missing}"


# ---------------------------------------------------------------------------
# 4. _structure_score handles equity-market document structure
# ---------------------------------------------------------------------------

def test_structure_score_equity_report_style():
    text = (
        "## Financial Stability Report 2024\n"
        "### Equity Market Risk\n"
        "- Stock valuations elevated: YES\n"
        "- Sector volatility: HIGH\n"
        "| Metric | Value | Flag |\n"
        "| VIX | 28.5 | elevated |\n"
        "| Equity beta | 1.2 | above_neutral |\n"
        "| Sector spread | 85bps | wide |\n"
        "Market risk remains elevated across sectors.\n"
    )
    score = _structure_score(text)
    assert score >= 0.4, f"Expected structure_score >= 0.4 for equity report text, got {score}"


# ---------------------------------------------------------------------------
# 5. Finance consistency checks on balance-sheet text
# ---------------------------------------------------------------------------

def test_finance_consistency_detects_balance_sheet():
    result = _finance_consistency_checks(_BALANCE_SHEET_TEXT)
    assert result["score"] >= 0.0
    assert "balance_sheet_sections_detected" in result["hints"] or "value_seen:assets" in " ".join(result["hints"])


def test_finance_consistency_perfect_balance_sheet():
    text = "Assets 100\nLiabilities 60\nEquity 40\nAllocation 50%\nCash 50%"
    tables = [{"rows": [{"cells": ["Assets", "100"]}, {"cells": ["Liabilities", "60"]}, {"cells": ["Equity", "40"]}]}]
    result = _finance_consistency_checks(text, tables=tables)
    assert "balance_sheet_balanced" in result["hints"]
    assert result["score"] == 1.0


# ---------------------------------------------------------------------------
# 6. Pipeline OCR → quality gate (fully monkeypatched, no real models)
# ---------------------------------------------------------------------------

def test_ocr_dual_returns_ok_with_monkeypatched_engine(monkeypatch, tmp_path):
    """Full ocr_dual() call with a fake PDF and monkeypatched Qwen engine."""
    pdf = tmp_path / "test_equity_report.pdf"
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Equity market risk. Stock volatility. Sector rotation.", fontsize=11)
        doc.save(str(pdf))
        doc.close()
    except Exception:
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")

    fake_response = {
        "ok": True,
        "text": _EQUITY_TEXT,
        "markdown": _EQUITY_TEXT,
        "engine": "gb10_qwen_ocr",
        "model": "qwen2.5vl:7b",
        "confidence": 0.88,
        "pages": 1,
        "route": "gb10_qwen_vlm_ocr",
        "reason": "qwen_ocr_stub",
        "blocks": [],
        "tables": [],
        "reading_order": [],
        "bbox": [],
        "quality": {
            "score": 0.75,
            "chars": len(_EQUITY_TEXT),
            "structure_score": 0.55,
            "numeric_fidelity_score": 0.45,
            "table_rows": 0,
        },
    }

    monkeypatch.setattr(odt, "_run_gb10_engine", lambda *a, **kw: fake_response)
    monkeypatch.setattr(odt, "_native_pdf_text_extract", lambda *a, **kw: {"ok": False, "error": "no_native_text"})

    result = odt.ocr_dual({"path": str(pdf), "goal": "Extract equity risk content", "engine": "gb10_qwen_ocr", "max_chars": 4000, "max_pages": 1})
    assert result.get("ok") is True, f"ocr_dual failed: {result.get('error')}"
    assert result.get("engine") == "gb10_qwen_ocr"


# ---------------------------------------------------------------------------
# 7. Truth gate build_truth_matrix produces expected shape
# ---------------------------------------------------------------------------

def test_build_truth_matrix_shape_and_symbol_presence():
    from Apollo.tools.ocr_phase2_truth_gate import build_truth_matrix
    matrix = build_truth_matrix({"command": "pytest -q", "passed": 87, "returncode": 0})
    assert "items" in matrix
    assert "ocr_tool" in matrix
    items = matrix["items"]
    assert "preprocessing.deskew" in items
    assert "preprocessing.sauvola" in items
    assert "ensemble.majority_vote" in items
    assert "verification.finance_consistency" in items
    assert items["verification.finance_consistency"]["classification"] == "implemented_literal"
    assert matrix["ocr_tool"]["symbols"]["_line_vote_majority"] is True
    assert matrix["ocr_tool"]["symbols"]["_finance_consistency_checks"] is True


def test_build_truth_matrix_all_service_items_present():
    from Apollo.tools.ocr_phase2_truth_gate import build_truth_matrix
    matrix = build_truth_matrix({"command": "pytest -q", "passed": 87, "returncode": 0})
    items = matrix["items"]
    service_items = [
        "preprocessing.docunet",
        "preprocessing.realesrgan",
        "verification.finbert_verifier",
        "verification.table_reconstruction",
    ]
    for name in service_items:
        assert name in items, f"Expected item '{name}' in truth matrix"
        assert items[name]["classification"] in {"implemented_literal", "service_hook_only"}, \
            f"Item '{name}' has unexpected classification: {items[name]['classification']}"


def test_build_truth_matrix_service_items_implemented_when_services_up(monkeypatch):
    """With all Aegis service URLs configured and health endpoints responding,
    all 4 service items must classify as implemented_literal."""
    monkeypatch.setattr(odt, "DEFAULT_OCR_DOCUNET_URL", "http://127.0.0.1:5221/ocr/docunet/rectify")
    monkeypatch.setattr(odt, "DEFAULT_OCR_REALESRGAN_URL", "http://127.0.0.1:5221/ocr/realesrgan/upscale")
    monkeypatch.setattr(odt, "DEFAULT_OCR_FINBERT_URL", "http://127.0.0.1:5221/ocr/finbert/eval")
    monkeypatch.setattr(odt, "DEFAULT_OCR_TABLEFORMER_URL", "http://127.0.0.1:5221/ocr/table/reconstruct")

    with patch("common.ocr_dual_tool.requests.get", side_effect=_stub_health_ok):
        with patch("common.ocr_dual_tool._http_endpoint_reachable", return_value={"ok": True}):
            from Apollo.tools.ocr_phase2_truth_gate import build_truth_matrix
            matrix = build_truth_matrix({"command": "pytest -q", "passed": 87, "returncode": 0})

    items = matrix["items"]
    for name in ("preprocessing.docunet", "preprocessing.realesrgan", "verification.finbert_verifier", "verification.table_reconstruction"):
        assert items[name]["classification"] == "implemented_literal", \
            f"Expected implemented_literal for {name}, got {items[name]['classification']}"
