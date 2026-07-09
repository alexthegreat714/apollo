from __future__ import annotations

import os
from flask import Flask

import common.gb10_ocr_gateway as gw
import pytest
from common.gb10_ocr_gateway import register_gb10_ocr_gateway_routes


def _make_app(monkeypatch, tmp_path):
    os.environ["AEGIS_OCR_GATEWAY_PREWARM_ENABLED"] = "0"
    os.environ["AEGIS_OCR_GATEWAY_PREWARM_ON_STARTUP"] = "0"
    os.environ["AEGIS_OCR_SMOKE_REQUIRED"] = "0"
    os.environ["AEGIS_PADDLEOCR_VL_MODEL_DIR"] = str(tmp_path / "paddle_models")
    os.environ["AEGIS_MINERU2_5_MODEL_DIR"] = str(tmp_path / "mineru_models")
    os.environ["AEGIS_OLMOCR2_MODEL_DIR"] = str(tmp_path / "olmocr_models")
    os.environ["AEGIS_OCR_ENGINE_PADDLEOCR_VL_READY"] = "1"
    os.environ["AEGIS_OCR_ENGINE_MINERU2_5_READY"] = "1"
    os.environ["AEGIS_OCR_ENGINE_OLMOCR2_READY"] = "1"
    for folder in ("paddle_models", "mineru_models", "olmocr_models"):
        model_dir = tmp_path / folder
        model_dir.mkdir(parents=True, exist_ok=True)
        marker = model_dir / ".ready"
        marker.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(gw, "PADDLE_OCR_AVAILABLE", False)
    monkeypatch.setattr(gw, "_module_available", lambda module_name: False)
    monkeypatch.setattr(gw, "_hash_dir_metadata", lambda root, limit=3000: "test-hash")
    monkeypatch.setattr(
        gw,
        "_paddle_backend_status",
        lambda: {"ready": False, "reason": "disabled_for_tests", "worker_callable": False, "model_assets_present": False},
    )
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


def test_finbert_eval_route_returns_service_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway.ocr_local_inference.finbert_eval",
        lambda text: {"ok": True, "mode": "finbert_service", "label": "positive", "score": 0.93, "model": "ProsusAI/finbert"},
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.post("/ocr/finbert/eval", json={"text": "Strong earnings and balance-sheet improvement."})
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["mode"] == "finbert_service"
    assert payload["provider"] == "aegis_finbert_service"


def test_table_reconstruct_route_returns_structured_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway.ocr_local_inference.tableformer_reconstruct",
        lambda source_path, text="", normalize_text=None: {
            "ok": True,
            "mode": "tableformer",
            "model": "microsoft/table-transformer-structure-recognition-v1.1-all",
            "tables": [{"id": 0, "rows": [{"cells": ["Assets", "100"]}]}],
            "source_path": source_path,
        },
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.post("/ocr/table/reconstruct", json={"source_path": "C:\\tmp\\table.pdf", "text": "| Assets | 100 |"})
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["mode"] == "tableformer"
    assert payload["tables"][0]["rows"][0]["cells"] == ["Assets", "100"]
    assert payload["provider"] == "aegis_tableformer_service"


def test_extract_route_delegates_got_to_shared_local_inference(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway.ocr_local_inference.got_extract_transformers",
        lambda path, goal, max_chars, max_pages, normalize_markdown=None, normalize_text=None: {
            "ok": True,
            "engine": "gb10_got_ocr2",
            "model": "stepfun-ai/GOT-OCR-2.0-hf",
            "text": "Pattern day trader margin 25000",
            "markdown": "Pattern day trader margin 25000",
            "confidence": 0.88,
            "pages": 1,
            "route": "local_got_transformers",
        },
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    file_path = tmp_path / "fixture.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    with file_path.open("rb") as handle:
        response = client.post(
            "/ocr/extract",
            data={"file": handle, "model": "gb10_got_ocr2", "goal": "Read dense scan"},
            content_type="multipart/form-data",
        )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["engine"] == "gb10_got_ocr2"
    assert payload["confidence"] == 0.88
    assert payload["model"] == "stepfun-ai/GOT-OCR-2.0-hf"


def test_ocr_health_exposes_phase2_service_routes(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.get("/ocr/capabilities")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["routes"]["finbert_eval"] == "/ocr/finbert/eval"
    assert payload["routes"]["table_reconstruct"] == "/ocr/table/reconstruct"
    assert payload["routes"]["docunet_rectify"] == "/ocr/docunet/rectify"
    assert payload["routes"]["realesrgan_upscale"] == "/ocr/realesrgan/upscale"
    assert payload["routes"]["prewarm"] == "/ocr/prewarm"
    assert payload["routes"]["handwriting_extract"] == "/ocr/handwriting/extract"
    assert payload["routes"]["chart_extract"] == "/ocr/chart/extract"


def test_capabilities_exposes_handwriting_and_chart_lanes(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.get("/ocr/capabilities")
    payload = response.get_json()
    assert response.status_code == 200
    engines = {row["name"]: row for row in payload["engines"]}
    assert "gb10_trocr_handwriting" in engines
    assert "gb10_deplot_chart" in engines
    assert engines["gb10_trocr_handwriting"]["ready"] is False
    assert engines["gb10_deplot_chart"]["ready"] is False
    assert "chart_or_figure" in payload["doc_classes"]
    assert "forms_or_checkboxes" in payload["doc_classes"]


def test_new_lane_health_and_disabled_extract_routes_are_structured(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_TROCR_ENABLE", raising=False)
    monkeypatch.delenv("AEGIS_DEPLOT_ENABLE", raising=False)
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    fixture = tmp_path / "figure.png"
    fixture.write_bytes(b"\x89PNG\r\n\x1a\n")

    handwriting_health = client.get("/ocr/handwriting/health").get_json()
    chart_health = client.get("/ocr/chart/health").get_json()
    assert handwriting_health["runtime_loaded"] is False
    assert chart_health["runtime_loaded"] is False

    handwriting = client.post("/ocr/handwriting/extract", json={"path": str(fixture), "goal": "Read handwriting"})
    chart = client.post("/ocr/chart/extract", json={"path": str(fixture), "goal": "Extract chart"})
    assert handwriting.status_code == 422
    assert handwriting.get_json()["engine"] == "gb10_trocr_handwriting"
    assert "disabled" in handwriting.get_json()["error"]
    assert chart.status_code == 422
    assert chart.get_json()["engine"] == "gb10_deplot_chart"
    assert "disabled" in chart.get_json()["error"]


def test_docunet_rectify_route_returns_image_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._docunet_service_rectify",
        lambda source_path="", image_b64="": {
            "ok": True,
            "mode": "docunet",
            "backend": "docunet_paddle_uvdoc",
            "model": "UVDoc",
            "image_b64": "ZmFrZQ==",
        },
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.post("/ocr/docunet/rectify", json={"image_b64": "ZmFrZQ=="})
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["mode"] == "docunet"
    assert payload["provider"] == "aegis_docunet_service"


def test_realesrgan_upscale_route_returns_image_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._realesrgan_service_upscale",
        lambda source_path="", image_b64="", outscale=None: {
            "ok": True,
            "mode": "realesrgan",
            "backend": "realesrgan_cuda",
            "model": "RealESRGAN_x4plus",
            "image_b64": "ZmFrZQ==",
            "outscale": outscale or 2.0,
        },
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.post("/ocr/realesrgan/upscale", json={"image_b64": "ZmFrZQ==", "outscale": 2.0})
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["mode"] == "realesrgan"
    assert payload["provider"] == "aegis_realesrgan_service"


def test_capabilities_exposes_advanced_lane_readiness_reasons(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._paddle_backend_status",
        lambda: {"ready": True, "reason": "worker_ready", "worker_callable": True, "model_assets_present": True},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._mineru_backend_status",
        lambda: {"ready": False, "reason": "model_assets_missing", "worker_callable": True, "model_assets_present": False},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._olmocr_backend_status",
        lambda: {"ready": False, "reason": "engine_not_installed", "worker_callable": False, "model_assets_present": False},
    )
    client = app.test_client()
    response = client.get("/ocr/capabilities")
    payload = response.get_json()
    assert response.status_code == 200
    engines = {row["name"]: row for row in payload["engines"]}
    assert engines["gb10_paddleocr_vl"]["ready"] is True
    assert engines["gb10_paddleocr_vl"]["reason"] == "worker_ready"
    assert engines["mineru2_5"]["ready"] is False
    assert engines["mineru2_5"]["reason"] == "model_assets_missing"
    assert engines["olmocr2"]["reason"] == "engine_not_installed"


def test_prewarm_route_updates_health_warm_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._load_got_runtime",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._load_finbert_runtime",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._load_tableformer_runtime",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._infer_ollama_model",
        lambda url, preferred="": preferred or "qwen2.5vl:7b",
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._prewarm_model",
        lambda base_url, model, timeout_sec=120: {"ok": True, "model": model or "qwen2.5vl:7b"},
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    prewarm_response = client.post("/ocr/prewarm", json={"warm_model": "qwen2.5vl:7b"})
    prewarm_payload = prewarm_response.get_json()
    assert prewarm_response.status_code == 200
    assert prewarm_payload["ok"] is True

    health_response = client.get("/ocr/health")
    health_payload = health_response.get_json()
    assert health_response.status_code == 200
    assert "warm_state" in health_payload
    assert health_payload["warm_state"]["last_run"]
    assert "gb10_qwen_ocr" in health_payload["warm_state"]["warmed_engines"]


def test_health_payload_exposes_engine_details(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.get("/ocr/health")
    payload = response.get_json()
    assert response.status_code == 200
    assert "engine_details" in payload
    assert "gb10_paddleocr_vl" in payload["engine_details"]
    row = payload["engine_details"]["gb10_paddleocr_vl"]
    assert "runtime_loaded" in row
    assert "cold_start_estimate_ms" in row
    assert "slo" in row
    assert "ready_gate" in row
    assert "smoke" in row


def test_gateway_runtime_dicts_alias_shared_local_runtime():
    assert gw._GOT_RUNTIME is gw.ocr_local_inference._GOT_RUNTIME
    assert gw._FINBERT_RUNTIME is gw.ocr_local_inference._FINBERT_RUNTIME
    assert gw._TABLEFORMER_RUNTIME is gw.ocr_local_inference._TABLE_RUNTIME


def test_capabilities_exposes_lane_signature_support(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.get("/ocr/capabilities")
    payload = response.get_json()
    assert response.status_code == 200
    engines = {row["name"]: row for row in payload["engines"]}
    assert engines["mineru2_5"]["native_api_supported"] in {True, False}
    assert engines["mineru2_5"]["cli_supported"] in {True, False}
    assert "mineru_cli_fallback" in engines["mineru2_5"]["lane_signature_modes"]
    assert "olmocr_cli_fallback" in engines["olmocr2"]["lane_signature_modes"]
    assert payload["routes"]["smoke"] == "/ocr/smoke"


def test_smoke_route_exposes_report_contract(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    response = client.get("/ocr/smoke")
    payload = response.get_json()
    assert response.status_code in {200, 404}
    assert "smoke" in payload
    smoke = payload["smoke"]
    assert "required" in smoke
    assert "path" in smoke


def test_extract_explicit_lane_strict_avoids_silent_reroute(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._extract_mineru2_5",
        lambda path, goal, max_chars, max_pages: {
            "ok": False,
            "engine": "mineru2_5",
            "error": "worker_unhealthy:forced_failure",
            "lane_signature": "mineru_native_api",
        },
    )
    app = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    file_path = tmp_path / "fixture.pdf"
    file_path.write_bytes(b"%PDF-1.4\n%fixture")
    with file_path.open("rb") as handle:
        response = client.post(
            "/ocr/extract",
            data={
                "file": handle,
                "model": "mineru2_5",
                "requested_lane_strict": "1",
                "goal": "Extract structure",
            },
            content_type="multipart/form-data",
        )
    payload = response.get_json()
    assert response.status_code == 422
    attempts = list(payload.get("attempts") or [])
    assert len(attempts) == 1
    assert attempts[0]["engine"] == "mineru2_5"
    assert payload["fallback_reason"] == "worker_unhealthy:forced_failure"
    assert payload["requested_lane_strict"] is True


def test_mineru_lane_uses_cli_fallback_signature(monkeypatch, tmp_path):
    pytest.importorskip("mineru.cli.client")

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _fake_run(command, capture_output=True, text=True, timeout=None):
        out_dir = tmp_path / "mineru_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.md").write_text("# MinerU\n| A | B |\n| 1 | 2 |", encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(
        "common.gb10_ocr_gateway._mineru_backend_status",
        lambda: {"ready": True, "worker_callable": True, "model_assets_present": True},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway.subprocess.run",
        _fake_run,
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._collect_text_artifacts",
        lambda workspace: {"markdown": "# MinerU\n| A | B |\n| 1 | 2 |", "pages_detected": 1, "tables": [], "bbox": []},
    )

    def _raise_api(*args, **kwargs):
        raise RuntimeError("api_down")

    import mineru.cli.client as mineru_client

    monkeypatch.setattr(mineru_client, "run_orchestrated_cli", _raise_api)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fixture")
    result = gw._extract_mineru2_5(pdf_path, "goal", 4000, 2)
    assert result["ok"] is True
    assert result["lane_signature"] == "mineru_cli_fallback"
    assert "mineru_native_api_failed" in str(result.get("fallback_reason") or "")


def test_olmocr_lane_uses_cli_fallback_signature(monkeypatch, tmp_path):
    pytest.importorskip("olmocr.pipeline")

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _fake_run(command, capture_output=True, text=True, timeout=None):
        out_dir = tmp_path / "olmocr_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.md").write_text("# olmOCR\n| Rule | Value |\n| PDT | 25000 |", encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(
        "common.gb10_ocr_gateway._olmocr_backend_status",
        lambda: {"ready": True, "worker_callable": True, "model_assets_present": True},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway.subprocess.run",
        _fake_run,
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway._collect_text_artifacts",
        lambda workspace: {"markdown": "# olmOCR\n| Rule | Value |\n| PDT | 25000 |", "pages_detected": 1, "tables": [], "bbox": []},
    )
    monkeypatch.setattr(
        "common.gb10_ocr_gateway.asyncio.run",
        lambda coro: (_ for _ in ()).throw(RuntimeError("api_down")),
    )
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fixture")
    result = gw._extract_olmocr2(pdf_path, "goal", 4000, 2)
    assert result["ok"] is True
    assert result["lane_signature"] == "olmocr_cli_fallback"
    assert "olmocr_native_api_failed" in str(result.get("fallback_reason") or "")
