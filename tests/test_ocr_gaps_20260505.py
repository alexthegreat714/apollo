"""Tests for OCR97 gap closures — 2026-05-05.

Gap 1: PIL image signal augmentation in classify_document_features
Gap 2: Checkbox state detection via Qwen prompt variant
Gap 3: DePlot/TrOCR model-not-found error classification
Gap 4: PaddleOCR-VL actionable install path
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

import common.gb10_ocr_gateway as gw
import common.ocr_local_inference as li
import common.ocr_dual_tool as odt
from common.gb10_ocr_gateway import register_gb10_ocr_gateway_routes
from common.ocr_dual_tool import (
    _augment_visual_lanes,
    _image_signals,
    _parse_checkbox_response,
    classify_document_features,
)


# ---------------------------------------------------------------------------
# Gateway test helper
# ---------------------------------------------------------------------------

def _make_base_app(monkeypatch, tmp_path):
    os.environ["AEGIS_OCR_GATEWAY_PREWARM_ENABLED"] = "0"
    os.environ["AEGIS_OCR_GATEWAY_PREWARM_ON_STARTUP"] = "0"
    os.environ["AEGIS_OCR_SMOKE_REQUIRED"] = "0"
    os.environ["AEGIS_PADDLEOCR_VL_MODEL_DIR"] = str(tmp_path / "paddle_models")
    os.environ["AEGIS_MINERU2_5_MODEL_DIR"] = str(tmp_path / "mineru_models")
    os.environ["AEGIS_OLMOCR2_MODEL_DIR"] = str(tmp_path / "olmocr_models")
    for folder in ("paddle_models", "mineru_models", "olmocr_models"):
        model_dir = tmp_path / folder
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / ".ready").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(gw, "_module_available", lambda module_name: False)
    monkeypatch.setattr(gw, "_hash_dir_metadata", lambda root, limit=3000: "test-hash")
    monkeypatch.setattr(
        gw,
        "_mineru_backend_status",
        lambda: {"ready": False, "reason": "disabled_for_tests", "worker_callable": False, "model_assets_present": False},
    )
    monkeypatch.setattr(
        gw,
        "_olmocr_backend_status",
        lambda: {"ready": False, "reason": "disabled_for_tests", "worker_callable": False, "model_assets_present": False},
    )
    app = Flask(__name__)
    register_gb10_ocr_gateway_routes(app, "Aegis", upload_dir=tmp_path)
    return app


# ---------------------------------------------------------------------------
# Gap 1 — PIL image signal augmentation
# ---------------------------------------------------------------------------

def test_image_signals_returns_aspect_ratio_and_whitespace(tmp_path):
    img = Image.new("RGB", (100, 200), color=(255, 255, 255))
    p = tmp_path / "white.png"
    img.save(str(p))
    sigs = _image_signals(p)
    assert sigs["aspect_ratio"] == 0.5
    assert sigs["whitespace_fraction"] >= 0.95


def test_image_signals_returns_empty_dict_when_pil_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(odt, "PIL_AVAILABLE", False)
    p = tmp_path / "img.png"
    p.write_bytes(b"x")
    assert _image_signals(p) == {}


def test_classify_promotes_chart_from_image_signal_on_generic_goal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        odt,
        "_image_signals",
        lambda path, **kw: {
            "edge_density": 0.15,
            "whitespace_fraction": 0.60,
            "dark_pixel_fraction": 0.05,
            "horizontal_line_score": 0.01,
            "aspect_ratio": 1.2,
        },
    )
    p = tmp_path / "img.png"
    p.write_bytes(b"x")
    result = classify_document_features(p, "extract text")
    assert result["layout_class"] == "chart_or_figure"
    assert result["confidence_reason"] == "image_signals"


def test_classify_text_signal_wins_when_strong(tmp_path, monkeypatch):
    monkeypatch.setattr(
        odt,
        "_image_signals",
        lambda path, **kw: {
            "edge_density": 0.03,
            "whitespace_fraction": 0.10,
            "dark_pixel_fraction": 0.01,
            "horizontal_line_score": 0.00,
            "aspect_ratio": 0.8,
        },
    )
    p = tmp_path / "img.png"
    p.write_bytes(b"x")
    result = classify_document_features(p, "Read this handwritten note", draft_text="cursive annotation handwritten")
    assert result["layout_class"] == "handwritten"
    assert result["confidence_reason"] == "text_signals"


def test_classify_confidence_reason_present_on_all_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(odt, "_image_signals", lambda path, **kw: {})
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    result = classify_document_features(p, "generic document")
    assert "confidence_reason" in result
    assert "image_signals" in result


# ---------------------------------------------------------------------------
# Gap 2 — Checkbox detection
# ---------------------------------------------------------------------------

def test_parse_checkbox_response_extracts_checked_and_unchecked():
    items = _parse_checkbox_response("[checked] Married\n[unchecked] Single")
    assert len(items) == 2
    assert items[0] == {"label": "Married", "checked": True}
    assert items[1] == {"label": "Single", "checked": False}


def test_parse_checkbox_response_handles_x_bracket():
    items = _parse_checkbox_response("[x] Accept terms\n[ ] Decline")
    assert len(items) == 2
    assert items[0]["checked"] is True
    assert items[1]["checked"] is False


def test_parse_checkbox_response_handles_unicode_symbols():
    items = _parse_checkbox_response("☑ Accept terms\n☐ Decline")
    assert len(items) == 2
    assert items[0]["checked"] is True
    assert items[0]["label"] == "Accept terms"
    assert items[1]["checked"] is False
    assert items[1]["label"] == "Decline"


def test_parse_checkbox_response_returns_empty_on_no_match():
    items = _parse_checkbox_response("No checkboxes here. Just text.\nAnother line.")
    assert items == []


def test_visual_controls_augmentation_issues_prompt_variant(tmp_path, monkeypatch):
    captured_goals: list[str] = []

    def fake_run_gb10(path, engine, goal, **kw):
        captured_goals.append(goal)
        return {"ok": True, "markdown": "[checked] Married\n[unchecked] Single"}

    monkeypatch.setattr(odt, "_run_gb10_engine", fake_run_gb10)

    p = tmp_path / "form.png"
    p.write_bytes(b"x")
    features = {
        "has_visual_controls": True,
        "unsupported_visual_control_detection": True,
        "has_handwriting": False,
        "has_chart": False,
        "has_figure": False,
        "layout_class": "forms_or_checkboxes",
    }

    result = _augment_visual_lanes(
        {"ok": True, "markdown": "Some form text"},
        p, "process this form", features,
        max_chars=20000, max_pages=4,
        route_mode="quality_first", use_gateway=False,
    )

    assert any("checkbox_detection:" in g for g in captured_goals)
    vc = result.get("visual_controls")
    assert vc is not None
    assert vc["ok"] is True
    assert vc["count_checked"] == 1
    assert vc["count_unchecked"] == 1
    assert vc["detection_method"] == "qwen_prompt_variant"
    assert result["document_features"]["unsupported_visual_control_detection"] is False


def test_visual_controls_augmentation_skipped_when_qwen_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(odt, "_run_gb10_engine", lambda *a, **kw: {"ok": False, "error": "engine_not_ready"})

    p = tmp_path / "form.png"
    p.write_bytes(b"x")
    features = {
        "has_visual_controls": True,
        "unsupported_visual_control_detection": True,
        "has_handwriting": False,
        "has_chart": False,
        "has_figure": False,
        "layout_class": "forms_or_checkboxes",
    }

    result = _augment_visual_lanes(
        {"ok": True, "markdown": "Some form"},
        p, "process form", features,
        max_chars=20000, max_pages=4,
        route_mode="quality_first", use_gateway=False,
    )

    controls = result.get("visual_controls")
    assert controls is None or not controls.get("ok", True)
    assert result["document_features"].get("unsupported_visual_control_detection") is True


# ---------------------------------------------------------------------------
# Gap 3 — Model-not-found error classification
# ---------------------------------------------------------------------------

class _MockProcNotFound:
    @staticmethod
    def from_pretrained(model_id, **kw):
        raise OSError("does not appear to have a file named config.json")


class _MockProcOtherError:
    @staticmethod
    def from_pretrained(model_id, **kw):
        raise RuntimeError("CUDA out of memory")


class _MockModel:
    @staticmethod
    def from_pretrained(model_id, **kw):
        return object()


def _reset_trocr_runtime():
    li._TROCR_RUNTIME["pipeline"] = None
    li._TROCR_RUNTIME["loaded_model_id"] = ""
    li._TROCR_RUNTIME["last_error"] = ""
    li._TROCR_RUNTIME["install_hint"] = ""
    li._TROCR_RUNTIME["backend"] = ""


def _reset_deplot_runtime():
    li._DEPLOT_RUNTIME["model"] = None
    li._DEPLOT_RUNTIME["processor"] = None
    li._DEPLOT_RUNTIME["loaded_model_id"] = ""
    li._DEPLOT_RUNTIME["last_error"] = ""
    li._DEPLOT_RUNTIME["install_hint"] = ""
    li._DEPLOT_RUNTIME["backend"] = ""


def test_load_trocr_runtime_returns_not_found_code_on_oserror(monkeypatch):
    _reset_trocr_runtime()
    monkeypatch.setattr(li, "TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr(li, "PIL_AVAILABLE", True)
    monkeypatch.setattr(li, "_trocr_enabled", lambda: True)
    monkeypatch.setattr(li, "_trocr_device", lambda: "cpu")
    monkeypatch.setattr(li, "_ensure_transformers_loaded", lambda: {"ok": True})
    monkeypatch.setattr(li, "_transformers_device_index", lambda name: -1)

    def raise_not_found(*args, **kwargs):
        raise OSError("does not appear to have a file named config.json")

    monkeypatch.setattr(li, "pipeline", raise_not_found)

    result = li.load_trocr_runtime()

    model_id = li._trocr_model_id()
    assert result["ok"] is False
    assert result["error"] == f"trocr_model_not_found:{model_id}"
    assert result["install_hint"].startswith("huggingface-cli download")


def test_load_trocr_runtime_returns_generic_code_on_other_error(monkeypatch):
    _reset_trocr_runtime()
    monkeypatch.setattr(li, "TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr(li, "PIL_AVAILABLE", True)
    monkeypatch.setattr(li, "_trocr_enabled", lambda: True)
    monkeypatch.setattr(li, "_trocr_device", lambda: "cpu")
    monkeypatch.setattr(li, "_ensure_transformers_loaded", lambda: {"ok": True})
    monkeypatch.setattr(li, "_transformers_device_index", lambda name: -1)

    def raise_other(*args, **kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(li, "pipeline", raise_other)

    result = li.load_trocr_runtime()

    assert result["ok"] is False
    assert result["error"].startswith("trocr_model_load_failed")
    assert result["install_hint"] == ""


def test_load_deplot_runtime_returns_not_found_code_on_oserror(monkeypatch):
    _reset_deplot_runtime()
    monkeypatch.setattr(li, "TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr(li, "PIL_AVAILABLE", True)
    monkeypatch.setattr(li, "_deplot_enabled", lambda: True)
    monkeypatch.setattr(li, "_ensure_transformers_loaded", lambda: {"ok": True})
    monkeypatch.setattr(li, "AutoProcessor", _MockProcNotFound)
    monkeypatch.setattr(li, "AutoModelForSeq2SeqLM", _MockModel)

    result = li.load_deplot_runtime()

    model_id = li._deplot_model_id()
    assert result["ok"] is False
    assert result["error"] == f"deplot_model_not_found:{model_id}"
    assert result["install_hint"].startswith("huggingface-cli download")


def test_load_deplot_runtime_returns_generic_code_on_other_error(monkeypatch):
    _reset_deplot_runtime()
    monkeypatch.setattr(li, "TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr(li, "PIL_AVAILABLE", True)
    monkeypatch.setattr(li, "_deplot_enabled", lambda: True)
    monkeypatch.setattr(li, "_ensure_transformers_loaded", lambda: {"ok": True})
    monkeypatch.setattr(li, "AutoProcessor", _MockProcOtherError)
    monkeypatch.setattr(li, "AutoModelForSeq2SeqLM", _MockModel)

    result = li.load_deplot_runtime()

    assert result["ok"] is False
    assert result["error"].startswith("deplot_model_load_failed")
    assert result["install_hint"] == ""


def test_chart_health_exposes_install_hint_on_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "PADDLE_OCR_AVAILABLE", False)
    monkeypatch.setattr(
        gw,
        "_paddle_backend_status",
        lambda: {"ready": False, "reason": "disabled_for_tests", "worker_callable": False, "model_assets_present": False},
    )
    gw._DEPLOT_RUNTIME["last_error"] = "deplot_model_not_found:google/deplot"
    gw._DEPLOT_RUNTIME["install_hint"] = "huggingface-cli download google/deplot"
    try:
        app = _make_base_app(monkeypatch, tmp_path)
        client = app.test_client()
        resp = client.get("/ocr/chart/health")
        payload = resp.get_json()
        assert resp.status_code == 200
        assert "install_hint" in payload
        assert payload["install_hint"] == "huggingface-cli download google/deplot"
    finally:
        gw._DEPLOT_RUNTIME["last_error"] = ""
        gw._DEPLOT_RUNTIME["install_hint"] = ""


def test_handwriting_health_exposes_install_hint_on_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "PADDLE_OCR_AVAILABLE", False)
    monkeypatch.setattr(
        gw,
        "_paddle_backend_status",
        lambda: {"ready": False, "reason": "disabled_for_tests", "worker_callable": False, "model_assets_present": False},
    )
    gw._TROCR_RUNTIME["last_error"] = "trocr_model_not_found:microsoft/trocr-base-handwritten"
    gw._TROCR_RUNTIME["install_hint"] = "huggingface-cli download microsoft/trocr-base-handwritten"
    try:
        app = _make_base_app(monkeypatch, tmp_path)
        client = app.test_client()
        resp = client.get("/ocr/handwriting/health")
        payload = resp.get_json()
        assert resp.status_code == 200
        assert "install_hint" in payload
        assert payload["install_hint"] == "huggingface-cli download microsoft/trocr-base-handwritten"
    finally:
        gw._TROCR_RUNTIME["last_error"] = ""
        gw._TROCR_RUNTIME["install_hint"] = ""


# ---------------------------------------------------------------------------
# Gap 4 — PaddleOCR install hint
# ---------------------------------------------------------------------------

def test_paddle_health_exposes_install_hint_when_engine_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "PADDLE_OCR_AVAILABLE", False)
    os.environ.pop("AEGIS_OCR_ENGINE_PADDLEOCR_VL_READY", None)
    os.environ.pop("APOLLO_GB10_PADDLEOCR_VL_URL", None)
    app = _make_base_app(monkeypatch, tmp_path)
    client = app.test_client()
    resp = client.get("/ocr/paddle/health")
    payload = resp.get_json()
    assert resp.status_code == 200
    assert "install_hint" in payload
    assert "pip install" in payload["install_hint"]


def test_capabilities_exposes_paddle_install_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "PADDLE_OCR_AVAILABLE", False)
    os.environ.pop("AEGIS_OCR_ENGINE_PADDLEOCR_VL_READY", None)
    os.environ.pop("APOLLO_GB10_PADDLEOCR_VL_URL", None)
    app = _make_base_app(monkeypatch, tmp_path)
    client = app.test_client()
    resp = client.get("/ocr/capabilities")
    payload = resp.get_json()
    assert resp.status_code == 200
    engines = {e["name"]: e for e in payload["engines"]}
    paddle_entry = engines.get("gb10_paddleocr_vl")
    assert paddle_entry is not None
    assert "install_hint" in paddle_entry
    assert "pip install" in paddle_entry["install_hint"]
