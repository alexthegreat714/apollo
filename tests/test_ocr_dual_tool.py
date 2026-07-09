from pathlib import Path

import common.ocr_dual_tool as ocr_dual_tool
import pytest
from PIL import Image
from common.ocr_dual_tool import _classify_doc_type, _gb10_plan, _looks_like_generic_finance_template, _needs_layout_engine, _normalize_markdown_layout, _normalize_ocr_payload, _policy_engine_chain, _quality_bundle, _select_engine, gb10_ocr_backend_readiness


_TMP_DIR = Path("Apollo/tests/_tmp_ocr")
_TMP_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _disable_ocr97_challengers_by_default(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CHALLENGER_ENABLE", False)


def test_needs_layout_engine_does_not_match_information():
    goal = "Extract day trading / stock trading strategy information"
    assert _needs_layout_engine(goal) is False


def test_needs_layout_engine_matches_real_layout_terms():
    assert _needs_layout_engine("Extract table layout from invoice form") is True


def test_select_engine_defaults_pdf_to_tesseract_for_plain_text_goal():
    pdf_path = _TMP_DIR / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    engine, reason = _select_engine("Extract trading strategy information", pdf_path)
    assert engine == "tesseract"
    assert reason == "default"


def test_gb10_plan_prefers_paddleocr_vl_for_finance_pdf():
    pdf_path = _TMP_DIR / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    plan = _gb10_plan(pdf_path, "Extract stock trading strategy headings and tables from this PDF")
    assert plan["primary"] == "gb10_paddleocr_vl"
    assert plan["cleanup"] == "gb10_qwen_ocr"


def test_gb10_plan_prefers_trocr_for_handwriting_goal():
    img_path = _TMP_DIR / "scan.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    plan = _gb10_plan(img_path, "Read this blurry handwriting and tiny text from a dense scan")
    assert plan["primary"] == "gb10_trocr_handwriting"


def test_policy_engine_chain_quality_first_for_table_dense_doc():
    chain = _policy_engine_chain("table_dense", "quality_first")
    assert chain[0] == "native_pdf_text"
    assert chain[1] == "gb10_paddleocr_vl"
    assert "gb10_qwen_ocr" in chain


def test_policy_engine_chain_balanced_for_scanned_pdf():
    chain = _policy_engine_chain("scanned_pdf", "balanced")
    assert chain[:2] == ["gb10_got_ocr2", "gb10_qwen_ocr"]


def test_generic_finance_template_detector_flags_placeholder_markdown():
    raw = """
    # Financial Research Document
    ## Market Analysis
    ## Key Indicators
    ## Sector Performance
    ## Company Performance
    - ABC Corp
    - XYZ Inc
    """
    assert _looks_like_generic_finance_template(raw) is True


def test_classify_doc_type_detects_scanned_pdf_goal():
    pdf_path = _TMP_DIR / "scan_goal.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    assert _classify_doc_type(pdf_path, "scanned and warped page for day trading statement") == "scanned_pdf"


def test_normalize_payload_emits_hybrid_schema_fields():
    raw = {
        "ok": True,
        "engine": "gb10_paddleocr_vl",
        "text": "## Margin table\n| Rule | Value |\n| PDT | 25000 |",
        "confidence": 0.88,
    }
    normalized = _normalize_ocr_payload(
        raw,
        route_mode="quality_first",
        doc_class="digital_pdf",
        engine_chain=["gb10_paddleocr_vl", "gb10_qwen_ocr"],
        attempts=[{"engine": "gb10_paddleocr_vl", "ok": True}],
    )
    assert normalized["ok"] is True
    assert normalized["engine_chain"] == ["gb10_paddleocr_vl", "gb10_qwen_ocr"]
    assert isinstance(normalized["blocks"], list)
    assert isinstance(normalized["tables"], list)
    assert isinstance(normalized["reading_order"], list)
    assert isinstance(normalized["bbox"], list)
    assert normalized["quality"]["route_mode"] == "quality_first"
    assert normalized["quality"]["doc_class"] == "digital_pdf"


def test_normalize_markdown_layout_repairs_flattened_table_rows():
    raw = "| Trading Strategy | Description | | --- | --- | | Overnight Trading | Buy low, sell high overnight |"
    normalized = _normalize_markdown_layout(raw)
    assert "\n|" in normalized
    assert normalized.count("\n") >= 2


def test_normalize_markdown_layout_repairs_flattened_headings_and_bullets():
    raw = "``` ### Day Trading Strategies - **Entry Point:** Identify trend - **Exit Point:** Use stop ```"
    normalized = _normalize_markdown_layout(raw)
    assert "\n### Day Trading Strategies" in normalized
    assert "\n- **Entry Point:**" in normalized
    assert "\n- **Exit Point:**" in normalized


def test_normalize_markdown_layout_strips_explanatory_tail_after_ocr_block():
    raw = """```markdown
# Balance Sheet
| Assets | 100 |
```

Please note that the image shows only a portion of the document.
To perform OCR on the image provided, I'll need to use OCR software."""
    normalized = _normalize_markdown_layout(raw)
    assert "Please note" not in normalized
    assert "To perform OCR" not in normalized
    assert "# Balance Sheet" in normalized


def test_render_pdf_pages_uses_unique_temp_paths(monkeypatch, tmp_path):
    pdf_path = _TMP_DIR / "collision.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    class _Doc:
        page_count = 2

        def load_page(self, idx):
            class _Page:
                def get_pixmap(self, matrix=None):
                    class _Pix:
                        alpha = False
                        width = 10
                        height = 10
                        samples = b"\x00" * 300

                    return _Pix()

            return _Page()

        def close(self):
            return None

    saved_paths = []

    class _ImageObj:
        def save(self, target):
            path = Path(target)
            path.write_bytes(b"png")
            saved_paths.append(path)

    class _FakeUuid:
        def __init__(self, value):
            self.hex = value

        def __str__(self):
            return self.hex

    uuid_counter = {"value": 0}

    def fake_uuid4():
        uuid_counter["value"] += 1
        return _FakeUuid(f"testuuid{uuid_counter['value']:04d}")

    monkeypatch.setattr(ocr_dual_tool, "PDF_AVAILABLE", True)
    monkeypatch.setattr(ocr_dual_tool.fitz, "open", lambda _: _Doc())
    monkeypatch.setattr(ocr_dual_tool.fitz, "Matrix", lambda x, y: (x, y))
    monkeypatch.setattr(ocr_dual_tool.Image, "frombytes", lambda mode, size, samples: _ImageObj())
    monkeypatch.setattr(ocr_dual_tool.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ocr_dual_tool.uuid, "uuid4", fake_uuid4)

    paths = ocr_dual_tool._render_pdf_pages(pdf_path, 2)
    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert all(path.exists() for path in paths)
    assert all("apollo_ocr_pdf_collision_" in path.name for path in paths)


def test_doc_anchor_terms_and_hits():
    path = Path(r"C:\tmp\sec_margin_execution_study.pdf")
    terms = ocr_dual_tool._doc_anchor_terms(path)
    assert "margin" in terms
    assert "execution" in terms
    assert "pdf" not in terms
    hits = ocr_dual_tool._anchor_hits(terms, "This section covers SEC margin execution controls.")
    assert hits >= 2


def test_gb10_qwen_ocr_prefers_anchor_matching_model(monkeypatch, tmp_path):
    pdf_path = tmp_path / "sec_margin_execution_study.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    page_png = tmp_path / "page_1.png"
    page_png.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen-model")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "llava-model")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://fake")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
    monkeypatch.setattr(ocr_dual_tool, "_native_pdf_text_extract", lambda *a, **k: {"ok": True, "markdown": "margin execution", "text": "margin execution"})
    monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *a, **k: [page_png])

    def fake_quality(markdown, confidence, tables=None, finance_checks=None, finbert_eval=None, **kwargs):
        text = str(markdown or "").lower()
        if "margin execution study" in text:
            return {
                "score": 0.32,
                "chars": 600,
                "structure_score": 0.09,
                "table_rows": 3,
                "numeric_fidelity_score": 0.75,
            }
        return {
            "score": 0.36,
            "chars": 600,
            "structure_score": 0.05,
            "table_rows": 1,
            "numeric_fidelity_score": 0.72,
        }

    monkeypatch.setattr(ocr_dual_tool, "_quality_bundle", fake_quality)

    def fake_ollama(path, prompt, model, base_url="", lane=""):
        if model == "qwen-model":
            return {
                "ok": True,
                "engine": "gb10_qwen_ocr",
                "model": model,
                "markdown": "# Trading Portfolio Summary\n| Asset | Value |\n| AAPL | 100 |",
                "text": "Trading Portfolio Summary AAPL 100",
                "confidence": None,
                "route": lane,
            }
        return {
            "ok": True,
            "engine": "gb10_qwen_ocr",
            "model": model,
            "markdown": "# SEC Margin Execution Study\n| Rule | Threshold |\n| Margin | 25k |",
            "text": "SEC Margin Execution Study Margin 25k",
            "confidence": None,
            "route": lane,
        }

    monkeypatch.setattr(ocr_dual_tool, "_ollama_generate_with_image", fake_ollama)
    result = ocr_dual_tool._gb10_qwen_ocr(pdf_path, "Extract margin execution controls", max_chars=6000, max_pages=2)
    assert result["ok"] is True
    assert result["model"] == "llava-model"
    assert "margin execution study" in result["markdown"].lower()


def test_gateway_ocr_sends_local_auth_headers(monkeypatch, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    seen = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "ok": True,
                "engine": "gb10_qwen_ocr",
                "model": "qwen3-vl:32b",
                "markdown": "Visible OCR text",
                "text": "Visible OCR text",
                "pages": 1,
                "confidence": 0.9,
                "quality": {"score": 0.9},
            }

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = dict(headers or {})
        return FakeResponse()

    monkeypatch.setenv("SKY_ALLOW_ANON", "0")
    monkeypatch.setenv("APOLLO_LOCAL_TOKEN", "local-secret")
    monkeypatch.setattr(ocr_dual_tool.requests, "post", fake_post)

    result = ocr_dual_tool._gateway_ocr_via_http(
        gateway_url="http://127.0.0.1:5221",
        path=pdf_path,
        model_name="qwen3-vl:32b",
        goal="extract",
        max_chars=4000,
        max_pages=1,
        route_mode="quality_first",
    )

    assert result["ok"] is True
    assert seen["url"].endswith("/ocr/extract")
    assert seen["headers"]["X-Apollo-Token"] == "local-secret"
    assert seen["headers"]["X-Sky-Token"] == "local-secret"


def test_gb10_qwen_ocr_accepts_short_image_text(monkeypatch, tmp_path):
    img_path = tmp_path / "short.png"
    Image.new("RGB", (240, 80), "white").save(img_path)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen3-vl:32b")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_ollama_generate_with_image",
        lambda *args, **kwargs: {
            "ok": True,
            "engine": "gb10_qwen_ocr",
            "model": "qwen3-vl:32b",
            "markdown": "OK 123.45",
            "text": "OK 123.45",
            "route": "gb10_ollama",
        },
    )

    result = ocr_dual_tool._gb10_qwen_ocr(img_path, "extract visible text", max_chars=2000, max_pages=1)

    assert result["ok"] is True
    assert result["text"] == "OK 123.45"


def test_ollama_image_payload_disables_qwen_thinking(monkeypatch, tmp_path):
    img_path = tmp_path / "sample.png"
    Image.new("RGB", (40, 20), "white").save(img_path)
    seen = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def json(self):
            return {"response": "OK 123.45"}

    def fake_post(url, json=None, timeout=None):
        seen["payload"] = dict(json or {})
        return FakeResponse()

    monkeypatch.setattr(ocr_dual_tool.requests, "post", fake_post)

    result = ocr_dual_tool._ollama_generate_with_image(img_path, "extract", "qwen3-vl:32b")

    assert result["ok"] is True
    assert seen["payload"]["think"] is False


def test_quality_bundle_rewards_table_dense_finance_markdown():
    text = """| Stock Trading Strategies | Day Trading Strategies |
|-------------------------|------------------------|
| Long-term investing | High-frequency trading |
| Value investing | Scalping |
| Dividend stocks | Swing trading |
| Growth stocks | Position trading |
| Market timing | Day trading |
| Fundamental analysis | Trend following |
| Technical analysis | News-driven trading |
"""
    quality = _quality_bundle(text, None)
    assert quality["structure_score"] >= 0.55
    assert quality["numeric_fidelity_score"] >= 0.30


@pytest.mark.skipif(not ocr_dual_tool.CV2_AVAILABLE, reason="OpenCV/numpy required for preprocessing tests")
def test_sauvola_binarize_is_deterministic_and_binary():
    gray = ocr_dual_tool.np.tile(ocr_dual_tool.np.arange(0, 255, dtype="uint8"), (40, 1))
    first = ocr_dual_tool._sauvola_binarize(gray)
    second = ocr_dual_tool._sauvola_binarize(gray)
    assert first.shape == gray.shape
    assert ocr_dual_tool.np.array_equal(first, second)
    assert set(int(value) for value in ocr_dual_tool.np.unique(first)).issubset({0, 255})


@pytest.mark.skipif(not ocr_dual_tool.CV2_AVAILABLE, reason="OpenCV/numpy required for preprocessing tests")
def test_preprocess_variants_stack_columns_in_reading_order(monkeypatch):
    image = ocr_dual_tool.Image.new("L", (10, 10), 255)
    left = ocr_dual_tool.np.full((3, 4), 10, dtype="uint8")
    right = ocr_dual_tool.np.full((2, 4), 200, dtype="uint8")

    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_COLUMN_SPLIT_ENABLED", True)
    monkeypatch.setattr(ocr_dual_tool, "_split_columns", lambda gray: [left, right])

    variants = ocr_dual_tool._preprocess_variants(image)
    split_variant = next(item for item in variants if item["name"] == "column_detection_page_split_reading_order")
    stacked = ocr_dual_tool.np.array(split_variant["image"])
    assert stacked.shape[0] == 3 + 18 + 2
    assert int(stacked[0, 0]) == 10
    assert int(stacked[-1, 0]) == 200


def test_merge_region_retry_fragments_replaces_only_targeted_span():
    merged = ocr_dual_tool._merge_region_retry_fragments(
        "Assets 100\nLiabilities ???\nEquity 40",
        [
            {
                "bbox": {"x": 1, "y": 2, "w": 3, "h": 4},
                "source_text": "Liabilities ???",
                "replacement_text": "Liabilities 60",
            }
        ],
        max_chars=4000,
    )
    assert "Liabilities 60" in merged["markdown"]
    assert "Liabilities ???" not in merged["markdown"]
    assert merged["replaced_regions"][0]["applied"] == "replace"


def test_extract_tesseract_regions_preserves_text_and_normalizes_confidence():
    ocr_data = {
        "text": ["Margin", "Rule"],
        "conf": [35, 90],
        "left": [10, 20],
        "top": [15, 25],
        "width": [40, 55],
        "height": [12, 14],
    }
    regions = ocr_dual_tool._extract_tesseract_regions(ocr_data, max_regions=4, conf_threshold=0.45)
    assert len(regions) == 1
    row = regions[0]
    assert row["text"] == "Margin"
    assert row["source_engine"] == "tesseract"
    assert 0.0 <= float(row["conf"]) <= 1.0


def test_extract_rapidocr_regions_emits_normalized_bbox_rows():
    rows = [
        (
            [[0, 0], [50, 0], [50, 10], [0, 10]],
            "Low confidence row",
            0.21,
        ),
        (
            [[0, 20], [50, 20], [50, 35], [0, 35]],
            "High confidence row",
            0.92,
        ),
    ]
    regions = ocr_dual_tool._extract_rapidocr_regions(rows, max_regions=4, conf_threshold=0.45)
    assert len(regions) == 1
    row = regions[0]
    assert row["source_engine"] == "rapidocr"
    assert row["text"] == "Low confidence row"
    assert row["w"] > 0 and row["h"] > 0
    assert 0.0 <= float(row["conf"]) <= 1.0


def test_parse_region_retry_policy_defaults_and_override():
    defaults = ocr_dual_tool._parse_region_retry_policy({})
    assert defaults["retry_order"] == ["gb10_qwen_ocr", "rapidocr"]
    assert defaults["max_regions"] == 4
    assert abs(defaults["conf_threshold"] - 0.45) < 0.001

    overridden = ocr_dual_tool._parse_region_retry_policy(
        {"max_regions": 2, "threshold": 0.33, "retry_order": ["rapidocr", "gb10_qwen_ocr"]}
    )
    assert overridden["max_regions"] == 2
    assert abs(overridden["conf_threshold"] - 0.33) < 0.001
    assert overridden["retry_order"] == ["rapidocr", "gb10_qwen_ocr"]


def test_finbert_service_mode_is_distinguishable_from_heuristic(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_URL", "http://finbert.local/eval")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "finbert_eval",
        lambda text: {"ok": False, "error": "local_finbert_unavailable"},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_http_json_post",
        lambda url, payload, timeout_sec=5: {"ok": True, "data": {"label": "finance", "score": 0.91}},
    )
    result = ocr_dual_tool._finbert_eval_signal("Balance sheet margin liquidity")
    assert result["mode"] == "finbert_service"
    assert result["status"] == "ok"
    assert result["label"] == "finance"


def test_finbert_heuristic_mode_is_neutralized(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_VERIFY", True)
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "finbert_eval",
        lambda text: {"ok": False, "error": "local_finbert_unavailable"},
    )
    result = ocr_dual_tool._finbert_eval_signal("balance sheet margin liquidity")
    assert result["mode"] == "heuristic_proxy"
    assert result["status"] == "degraded"
    assert result["score_neutralized"] is True
    assert "local_finbert_unavailable" in str(result.get("error") or "")


def test_finbert_local_inference_is_used_when_url_unconfigured(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_VERIFY", True)
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "finbert_eval",
        lambda text: {
            "ok": True,
            "mode": "finbert_service",
            "label": "positive",
            "score": 0.97,
            "model": "ProsusAI/finbert",
        },
    )
    result = ocr_dual_tool._finbert_eval_signal("Balance sheet margin liquidity")
    assert result["mode"] == "finbert_service"
    assert result["status"] == "ok"


def test_apply_ocr97_challenger_routes_promotes_better_result(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CHALLENGER_ENABLE", True)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_OLLAMA_OCR_ENABLE", True)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CLIENT_OCR_ENABLE", False)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_run_ocr97_challenger_route",
        lambda route_id, path, max_chars, timeout_sec: {
            "ok": True,
            "engine": route_id,
            "markdown": "Invoice Total 1382.40 Payment due 2026-05-01",
            "text": "Invoice Total 1382.40 Payment due 2026-05-01",
            "quality": {"score": 0.91, "numeric_fidelity_score": 0.89, "structure_score": 0.72, "chars": 44},
        },
    )
    best = {
        "ok": True,
        "engine": "gb10_qwen_ocr",
        "markdown": "Invoice maybe total",
        "text": "Invoice maybe total",
        "quality": {"score": 0.61, "numeric_fidelity_score": 0.55, "structure_score": 0.40, "chars": 18},
        "phase2": {"passes": 2},
    }
    attempts = []

    result = ocr_dual_tool._apply_ocr97_challenger_routes(
        best,
        path=image_path,
        goal="extract invoice totals",
        max_chars=4000,
        doc_class="image",
        route_mode="quality_first",
        attempts=attempts,
    )

    assert result["engine"] == "ollama_ocr"
    assert result["route"] == "ocr97_challenger_promoted"
    assert result["fallback_reason"] == "ocr97_challenger_promoted:ollama_ocr"
    assert attempts and attempts[0]["engine"] == "ocr97_challenger:ollama_ocr"


def test_apply_ocr97_challenger_routes_keeps_stronger_base_result(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CHALLENGER_ENABLE", True)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_OLLAMA_OCR_ENABLE", True)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CLIENT_OCR_ENABLE", False)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_run_ocr97_challenger_route",
        lambda route_id, path, max_chars, timeout_sec: {
            "ok": True,
            "engine": route_id,
            "markdown": "weaker challenger",
            "text": "weaker challenger",
            "quality": {"score": 0.74, "numeric_fidelity_score": 0.70, "structure_score": 0.50, "chars": 16},
        },
    )
    best = {
        "ok": True,
        "engine": "gb10_paddleocr_vl",
        "markdown": "strong base",
        "text": "strong base",
        "quality": {"score": 0.84, "numeric_fidelity_score": 0.82, "structure_score": 0.68, "chars": 11},
    }

    result = ocr_dual_tool._apply_ocr97_challenger_routes(
        best,
        path=image_path,
        goal="extract invoice totals",
        max_chars=4000,
        doc_class="image",
        route_mode="quality_first",
        attempts=[],
    )

    assert result["engine"] == "gb10_paddleocr_vl"
    assert "challenger_routes" in result


def test_ocr97_readiness_exposes_challenger_paths(monkeypatch):
    monkeypatch.setattr(
        ocr_dual_tool,
        "gb10_ocr_backend_readiness",
        lambda payload: {
            "ready": True,
            "mode": "gateway",
            "checks": {
                "gateway_enabled": True,
                "gateway_health": {"ok": True, "reason": "ok"},
                "direct_paddle": {"ok": True, "reason": "ok"},
                "direct_got": {"ok": True, "reason": "ok"},
                "qwen_check": {"ok": True, "available_model": "qwen3-vl:32b", "reason": "ok"},
                "surya_layout": {"available": True, "enabled": True, "device": "cuda", "last_error": ""},
            },
        },
    )
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CHALLENGER_ENABLE", True)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_OLLAMA_OCR_ENABLE", True)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR97_CLIENT_OCR_ENABLE", False)

    readiness = ocr_dual_tool.ocr97_readiness({})

    paths = readiness["extraction_paths"]
    assert "ollama_ocr_challenger" in paths
    assert paths["ollama_ocr_challenger"]["role"] == "challenger_fallback"
    assert "challenger_order" in readiness["policy"]


def test_finbert_local_inference_is_preferred_when_url_is_configured(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_URL", "http://finbert.local/eval")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_VERIFY", True)
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "finbert_eval",
        lambda text: {
            "ok": True,
            "mode": "finbert_service",
            "label": "positive",
            "score": 0.97,
            "model": "ProsusAI/finbert",
        },
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_http_json_post",
        lambda url, payload, timeout_sec=5: {"ok": True, "data": {"label": "finance", "score": 0.11}},
    )
    result = ocr_dual_tool._finbert_eval_signal("Balance sheet margin liquidity")
    assert result["mode"] == "finbert_service"
    assert result["status"] == "ok"
    assert result["provider"] == "local_finbert_runtime"
    assert result["score"] == 0.97


def test_table_reconstruction_uses_real_backend_when_available(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_TABLEFORMER_URL", "http://tableformer.local/extract")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_LGPMA_URL", "")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "tableformer_reconstruct",
        lambda source_path, text="", normalize_text=None: {"ok": False, "error": "tableformer_runtime_unavailable"},
    )
    captured = {}

    def fake_post(url, payload, timeout_sec=5):
        captured["url"] = url
        captured["payload"] = dict(payload)
        return {"ok": True, "data": {"tables": [{"rows": [{"cells": ["Assets", "100"]}]}]}}

    monkeypatch.setattr(
        ocr_dual_tool,
        "_http_json_post",
        fake_post,
    )
    result = ocr_dual_tool._tableformer_or_lgpma_reconstruct("| Assets | 100 |", source_path="C:\\tmp\\table.pdf")
    assert result["mode"] == "tableformer"
    assert result["ok"] is True
    assert result["tables"][0]["rows"][0]["cells"] == ["Assets", "100"]
    assert captured["payload"]["source_path"] == "C:\\tmp\\table.pdf"


def test_table_reconstruction_uses_local_backend_when_direct_unset(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_TABLEFORMER_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_LGPMA_URL", "")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "tableformer_reconstruct",
        lambda source_path, text="", normalize_text=None: {
            "ok": True,
            "mode": "tableformer",
            "model": "microsoft/table-transformer-structure-recognition-v1.1-all",
            "tables": [{"rows": [{"cells": ["Rule", "4210"]}]}],
            "source_path": source_path,
        },
    )
    result = ocr_dual_tool._tableformer_or_lgpma_reconstruct("Rule 4210", source_path="C:\\tmp\\rule.pdf")
    assert result["mode"] == "tableformer"
    assert result["ok"] is True
    assert result["tables"][0]["rows"][0]["cells"] == ["Rule", "4210"]
    assert result["backends"]["tableformer_local"]["ok"] is True


def test_table_reconstruction_prefers_local_backend_when_service_is_configured(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_TABLEFORMER_URL", "http://tableformer.local/extract")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_LGPMA_URL", "")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "tableformer_reconstruct",
        lambda source_path, text="", normalize_text=None: {
            "ok": True,
            "mode": "tableformer",
            "model": "microsoft/table-transformer-structure-recognition-v1.1-all",
            "tables": [{"rows": [{"cells": ["Rule", "4210"]}]}],
            "source_path": source_path,
        },
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_http_json_post",
        lambda url, payload, timeout_sec=5: {"ok": True, "data": {"tables": [{"rows": [{"cells": ["Wrong", "9999"]}]}]}},
    )
    result = ocr_dual_tool._tableformer_or_lgpma_reconstruct("Rule 4210", source_path="C:\\tmp\\rule.pdf")
    assert result["mode"] == "tableformer"
    assert result["tables"][0]["rows"][0]["cells"] == ["Rule", "4210"]
    assert result["backends"]["tableformer"]["reason"] == "deferred_local_first"
    assert result["backends"]["tableformer_local"]["ok"] is True


def test_table_reconstruction_uses_heuristic_only_after_local_failure(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_TABLEFORMER_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_LGPMA_URL", "")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "tableformer_reconstruct",
        lambda source_path, text="", normalize_text=None: {"ok": False, "error": "tableformer_runtime_unavailable"},
    )
    result = ocr_dual_tool._tableformer_or_lgpma_reconstruct("| Rule | 4210 |", source_path="C:\\tmp\\rule.pdf")
    assert result["mode"] == "heuristic"
    assert result["ok"] is True
    assert result["backends"]["tableformer_local"]["reason"] == "tableformer_runtime_unavailable"


def test_semantic_diff_tracks_first_seen_and_change(monkeypatch, tmp_path):
    fp_path = tmp_path / "ocr_fingerprints.json"
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINGERPRINT_PATH", fp_path)
    first = ocr_dual_tool._semantic_diff_check("C:\\tmp\\doc.pdf", "Assets 100 Liabilities 60 Equity 40")
    unchanged = ocr_dual_tool._semantic_diff_check("C:\\tmp\\doc.pdf", "Assets 100 Liabilities 60 Equity 40")
    second = ocr_dual_tool._semantic_diff_check("C:\\tmp\\doc.pdf", "Assets 200 Liabilities 100 Equity 100")
    assert first["first_seen"] is True
    assert unchanged["first_seen"] is False
    assert unchanged["changed"] is False
    assert second["first_seen"] is False
    assert second["changed"] is True
    assert second["change_pct"] >= 0.0
    assert second["risk_band"] in {"low", "medium", "high"}


def test_run_engine_once_attaches_semantic_diff(monkeypatch, tmp_path):
    doc_path = tmp_path / "scan.png"
    doc_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        ocr_dual_tool,
        "_run_gb10_engine",
        lambda *args, **kwargs: {
            "ok": True,
            "engine": "gb10_got_ocr2",
            "text": "Assets 100 Liabilities 60 Equity 40",
            "markdown": "Assets 100 Liabilities 60 Equity 40",
            "source_path": str(doc_path),
        },
    )
    seen = {}

    def fake_semantic_diff(source_path, text):
        seen["source_path"] = source_path
        seen["text"] = text
        return {"first_seen": True, "changed": False}

    monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", fake_semantic_diff)
    out = ocr_dual_tool._run_engine_once(doc_path, "gb10_got_ocr2", "Read dense scan", max_chars=4000, max_pages=1, use_gateway=False)
    assert out["semantic_diff"]["first_seen"] is True
    assert seen["source_path"] == str(doc_path)
    assert "Assets 100" in seen["text"]


def test_finance_consistency_checks_use_reconstructed_tables():
    result = ocr_dual_tool._finance_consistency_checks(
        "Assets 100\nLiabilities 60\nEquity 40",
        tables=[
            {
                "rows": [
                    {"cells": ["Assets", "100"]},
                    {"cells": ["Liabilities", "60"]},
                    {"cells": ["Equity", "40"]},
                ]
            }
        ],
    )
    assert "table_numeric_values_detected" in result["hints"]
    assert "balance_sheet_balanced" in result["hints"]


def test_normalize_payload_exposes_phase2_preprocessing_ensemble_and_verification():
    raw = {
        "ok": True,
        "engine": "phase2_ensemble_vote",
        "text": "Assets 100\nLiabilities 60\nEquity 40",
        "confidence": 0.91,
        "phase2": {
            "passes": 2,
            "vote_mode": "line_majority_vote",
            "self_consistency_used": True,
            "region_retry_count": 1,
            "stop_reason": "stopping_criteria_small_improvement",
            "contributing_engines": ["gb10_qwen_ocr:base", "gb10_qwen_ocr:strict_literal"],
        },
    }
    normalized = _normalize_ocr_payload(
        raw,
        route_mode="quality_first",
        doc_class="digital_pdf",
        engine_chain=["native_pdf_text", "gb10_qwen_ocr"],
        attempts=[{"pass": 1, "engine": "gb10_qwen_ocr", "ok": True, "score": 0.88}],
    )
    assert normalized["phase2"]["preprocessing"]["sauvola"]["classification"] in {"implemented_literal", "implemented_approximation"}
    assert normalized["phase2"]["ensemble"]["vote_mode"] == "line_majority_vote"
    assert normalized["phase2"]["ensemble"]["self_consistency_used"] is True
    assert isinstance(normalized["phase2"]["verification"]["math_checks"], list)
    assert normalized["phase2"]["verification"]["verification_score"] >= 0.0


def test_phase2_majority_vote_prefers_consensus_lines():
    voted = ocr_dual_tool._line_vote_majority(
        [
            {
                "markdown": "Assets: 100\nLiabilities: 70\nEquity: 30",
                "quality": {"score": 0.80},
            },
            {
                "markdown": "Assets: 100\nLiabilities: 70\nEquity: 30",
                "quality": {"score": 0.75},
            },
            {
                "markdown": "Assets: 110\nLiabilities: 80\nEquity: 30",
                "quality": {"score": 0.70},
            },
        ],
        max_chars=2000,
    )
    assert voted["ok"] is True
    assert "Assets: 100" in str(voted.get("markdown") or "")
    assert str(voted.get("engine") or "") == "phase2_ensemble_vote"


def test_phase2_finance_consistency_flags_percent_overflow():
    checks = ocr_dual_tool._finance_consistency_checks("A: 70%\nB: 40%\nC: 30%")
    assert any("percent_group_exceeds_120" in issue for issue in checks.get("issues") or [])


def test_quality_bundle_contains_phase2_verification_fields():
    quality = _quality_bundle("Balance Sheet\nAssets: 100\nLiabilities: 70\nEquity: 30", 0.9)
    assert "finance_consistency" in quality
    assert "finbert_eval" in quality
    assert isinstance(quality.get("finance_consistency"), dict)
    assert isinstance(quality.get("finbert_eval"), dict)


def test_normalize_payload_keeps_phase2_metadata():
    raw = {
        "ok": True,
        "engine": "gb10_qwen_ocr",
        "text": "Assets: 100\nLiabilities: 70\nEquity: 30",
        "phase2": {"enabled": True, "passes": 3, "ensemble_used": True},
    }
    normalized = _normalize_ocr_payload(
        raw,
        route_mode="quality_first",
        doc_class="digital_pdf",
        engine_chain=["gb10_qwen_ocr"],
    )
    assert normalized["ok"] is True
    assert dict(normalized.get("phase2") or {}).get("enabled") is True


def test_normalize_payload_only_calls_finbert_once(monkeypatch):
    calls = {"count": 0}

    monkeypatch.setattr(
        ocr_dual_tool,
        "_tableformer_or_lgpma_reconstruct",
        lambda markdown, source_path="": {"ok": True, "mode": "heuristic", "tables": []},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_finance_consistency_checks",
        lambda markdown, tables=None: {"score": 0.7, "issues": [], "hints": ["balanced"]},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_semantic_diff_check",
        lambda source_path, text: {"enabled": True, "status": "ok"},
    )

    def fake_finbert(text):
        calls["count"] += 1
        return {"enabled": True, "mode": "local_finbert", "status": "ok", "score": 0.81}

    monkeypatch.setattr(ocr_dual_tool, "_finbert_eval_signal", fake_finbert)
    out = _normalize_ocr_payload(
        {
            "ok": True,
            "engine": "gb10_got_ocr2",
            "text": "Assets 100\nLiabilities 60\nEquity 40",
            "source_path": "C:/tmp/test.pdf",
        },
        route_mode="quality_first",
        doc_class="scanned_pdf",
        engine_chain=["gb10_got_ocr2"],
    )
    assert out["quality"]["finbert_eval"]["mode"] == "local_finbert"
    assert calls["count"] == 1


def test_quality_bundle_respects_supplied_empty_phase2_signals(monkeypatch):
    calls = {"finance": 0, "finbert": 0}

    def fake_finance(text, tables=None):
        calls["finance"] += 1
        return {"score": 0.5}

    def fake_finbert(text):
        calls["finbert"] += 1
        return {"mode": "local_finbert"}

    monkeypatch.setattr(ocr_dual_tool, "_finance_consistency_checks", fake_finance)
    monkeypatch.setattr(ocr_dual_tool, "_finbert_eval_signal", fake_finbert)
    quality = _quality_bundle("Assets 100\nLiabilities 60\nEquity 40", 0.9, finance_checks={}, finbert_eval={})
    assert quality["finance_consistency"] == {}
    assert quality["finbert_eval"] == {}
    assert calls["finance"] == 0
    assert calls["finbert"] == 0


def test_normalize_payload_reuses_quality_signals_and_skips_tableformer_when_tables_exist(monkeypatch):
    calls = {"tableformer": 0, "finance": 0, "finbert": 0}

    def fail_tableformer(markdown, source_path=""):
        calls["tableformer"] += 1
        return {"ok": True, "mode": "tableformer", "tables": [{"rows": []}]}

    def fail_finance(markdown, tables=None):
        calls["finance"] += 1
        return {"score": 0.99, "issues": ["unexpected"], "hints": ["unexpected"]}

    def fail_finbert(text):
        calls["finbert"] += 1
        return {"enabled": True, "mode": "unexpected"}

    monkeypatch.setattr(ocr_dual_tool, "_tableformer_or_lgpma_reconstruct", fail_tableformer)
    monkeypatch.setattr(ocr_dual_tool, "_finance_consistency_checks", fail_finance)
    monkeypatch.setattr(ocr_dual_tool, "_finbert_eval_signal", fail_finbert)

    normalized = _normalize_ocr_payload(
        {
            "ok": True,
            "engine": "rapidocr",
            "text": "Assets 100\nLiabilities 60\nEquity 40",
            "tables": [{"rows": [{"cells": ["Assets", "100"]}]}],
            "quality": {
                "score": 0.82,
                "chars": 36,
                "confidence": 0.91,
                "structure_score": 0.58,
                "numeric_fidelity_score": 0.74,
                "table_rows": 1,
                "finance_consistency": {"score": 0.66, "issues": [], "hints": ["reused"]},
                "finbert_eval": {"enabled": True, "mode": "local_finbert", "status": "ok"},
            },
        },
        route_mode="quality_first",
        doc_class="digital_pdf",
        engine_chain=["rapidocr"],
    )
    assert calls == {"tableformer": 0, "finance": 0, "finbert": 0}
    assert normalized["quality"]["finance_consistency"]["hints"] == ["reused"]
    assert normalized["quality"]["finbert_eval"]["mode"] == "local_finbert"
    assert normalized["quality"]["table_reconstruction"]["mode"] == "upstream_tables"


def test_phase2_pdf_dpi_ensemble_votes_across_variants(monkeypatch, tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    def fake_render(path, max_pages, *, dpi=200, tag=""):
        page = tmp_path / f"{tag}_{dpi}.png"
        page.write_bytes(b"\x89PNG\r\n\x1a\n")
        return [page]

    monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", fake_render)
    monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda source_path, text: {"enabled": True, "status": "ok"})

    def fake_quality(text, confidence, tables=None, finance_checks=None, finbert_eval=None, **kwargs):
        clean = str(text or "")
        score = 0.86 if "Assets: 100" in clean else 0.71
        return {
            "score": score,
            "chars": len(clean),
            "confidence": confidence,
            "structure_score": 0.6,
            "numeric_fidelity_score": 0.8,
            "table_rows": 0,
            "finance_consistency": finance_checks or {"score": 0.5, "issues": [], "hints": []},
            "finbert_eval": finbert_eval or {"enabled": True, "mode": "heuristic_proxy", "status": "ok"},
        }

    monkeypatch.setattr(ocr_dual_tool, "_quality_bundle", fake_quality)

    def fake_run_rendered(source_path, page_paths, engine, goal, *, max_chars, route_mode, use_gateway, variant_label=""):
        if variant_label.endswith("dpi_200"):
            markdown = "Assets: 110\nLiabilities: 80\nEquity: 30"
            score = 0.71
        else:
            markdown = "Assets: 100\nLiabilities: 70\nEquity: 30"
            score = 0.86
        return {
            "ok": True,
            "engine": variant_label,
            "text": markdown,
            "markdown": markdown,
            "confidence": 0.9,
            "quality": {
                "score": score,
                "chars": len(markdown),
                "confidence": 0.9,
                "structure_score": 0.6,
                "numeric_fidelity_score": 0.8,
                "table_rows": 0,
            },
        }

    monkeypatch.setattr(ocr_dual_tool, "_run_engine_on_rendered_pages", fake_run_rendered)
    ensemble = ocr_dual_tool._phase2_pdf_dpi_ensemble(
        pdf_path,
        "gb10_got_ocr2",
        "Read statement",
        max_chars=4000,
        max_pages=1,
        route_mode="quality_first",
        use_gateway=False,
    )
    assert ensemble["ok"] is True
    assert len(ensemble["candidates"]) == 4
    voted = ensemble["voted"]
    assert voted["phase2"]["self_consistency_used"] is True
    assert voted["phase2"]["dpi_samples"] == [150, 200, 300, 400]
    assert "Assets: 100" in str(voted.get("markdown") or "")


def test_run_policy_route_exposes_multi_scale_self_consistency_for_image(monkeypatch, tmp_path):
    img_path = tmp_path / "scan.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(ocr_dual_tool, "_classify_doc_type", lambda path, goal: "photo")
    monkeypatch.setattr(
        ocr_dual_tool,
        "_semantic_diff_check",
        lambda source_path, text: {"enabled": True, "status": "ok"},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_tableformer_or_lgpma_reconstruct",
        lambda markdown, source_path="": {"ok": True, "mode": "heuristic", "tables": []},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_finance_consistency_checks",
        lambda markdown, tables=None: {"score": 0.55, "issues": [], "hints": ["balanced"]},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_finbert_eval_signal",
        lambda text: {"enabled": True, "mode": "heuristic_proxy", "status": "ok"},
    )

    def fake_run_engine_once(path, engine, goal, *, max_chars, max_pages, route_mode="balanced", use_gateway=False):
        if engine == "rapidocr":
            markdown = "Assets: 110\nLiabilities: 80\nEquity: 30"
            return {
                "ok": True,
                "engine": engine,
                "text": markdown,
                "markdown": markdown,
                "confidence": 0.71,
                "quality": {
                    "score": 0.61,
                    "chars": len(markdown),
                    "confidence": 0.71,
                    "structure_score": 0.45,
                    "numeric_fidelity_score": 0.64,
                    "table_rows": 0,
                },
                "source_path": str(path),
            }
        return {"ok": False, "engine": engine, "error": f"{engine}_unavailable", "quality": {"score": 0.0}}

    monkeypatch.setattr(ocr_dual_tool, "_run_engine_once", fake_run_engine_once)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_phase2_image_scale_ensemble",
        lambda path, engine, goal, **kwargs: {
            "ok": True,
            "engine": engine,
            "candidates": [
                {"engine": f"{engine}:scale_1_0", "quality": {"score": 0.79}, "phase2": {"self_consistency_variant": "scale_1.0", "scale": 1.0}},
                {"engine": f"{engine}:scale_1_5", "quality": {"score": 0.83}, "phase2": {"self_consistency_variant": "scale_1.5", "scale": 1.5}},
                {"engine": f"{engine}:scale_2_0", "quality": {"score": 0.82}, "phase2": {"self_consistency_variant": "scale_2.0", "scale": 2.0}},
            ],
            "attempts": [],
            "voted": {
                "ok": True,
                "engine": "phase2_ensemble_vote",
                "text": "Assets: 100\nLiabilities: 70\nEquity: 30",
                "markdown": "Assets: 100\nLiabilities: 70\nEquity: 30",
                "confidence": 0.83,
                "quality": {
                    "score": 0.83,
                    "chars": 39,
                    "confidence": 0.83,
                    "structure_score": 0.6,
                    "numeric_fidelity_score": 0.8,
                    "table_rows": 0,
                },
                "phase2": {
                    "self_consistency_used": True,
                    "vote_mode": "multi_scale_line_vote",
                    "scale_samples": [1.0, 1.5, 2.0],
                    "variant_count": 3,
                    "contributing_engines": [f"{engine}:scale_1_0", f"{engine}:scale_1_5", f"{engine}:scale_2_0"],
                },
                "source_path": str(path),
                "semantic_diff": {"enabled": True, "status": "ok"},
            },
        },
    )

    out = ocr_dual_tool._run_policy_route(
        img_path,
        goal="Read scan",
        route_mode="quality_first",
        max_chars=4000,
        max_pages=1,
        forced_engine="rapidocr",
        consensus=True,
        use_gateway=False,
    )
    ensemble = out["phase2"]["ensemble"]
    assert ensemble["self_consistency_used"] is True
    assert ensemble["scale_samples"] == [1.0, 1.5, 2.0]
    assert ensemble["variant_count"] == 3
    assert out["quality"]["score"] >= 0.83


def test_document_feature_classifier_routes_handwriting_chart_and_forms(tmp_path):
    img_path = tmp_path / "note.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    handwriting = ocr_dual_tool.classify_document_features(img_path, "Read handwritten notes")
    assert handwriting["has_handwriting"] is True
    assert handwriting["layout_class"] == "handwritten"
    assert ocr_dual_tool._policy_engine_chain("handwritten", "quality_first")[0] == "gb10_trocr_handwriting"

    typed = ocr_dual_tool.classify_document_features(img_path, "Read typed text")
    assert typed["has_handwriting"] is False
    assert "gb10_trocr_handwriting" not in ocr_dual_tool._policy_engine_chain(typed["layout_class"], "quality_first")

    chart = ocr_dual_tool.classify_document_features(img_path, "Extract the line chart values")
    assert chart["has_chart"] is True
    assert chart["layout_class"] == "chart_or_figure"
    assert ocr_dual_tool._policy_engine_chain("chart_or_figure", "quality_first")[0] == "gb10_deplot_chart"

    form = ocr_dual_tool.classify_document_features(img_path, "Read this form with checkboxes")
    assert form["layout_class"] == "forms_or_checkboxes"
    assert form["unsupported_visual_control_detection"] is True


def test_handwriting_lane_output_merges_into_payload(monkeypatch, tmp_path):
    img_path = tmp_path / "handwritten.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    def fake_run_engine_once(path, engine, goal, *, max_chars, max_pages, route_mode="balanced", use_gateway=False):
        if engine == "gb10_trocr_handwriting":
            return {
                "ok": True,
                "engine": "gb10_trocr_handwriting",
                "text": "Buy milk",
                "markdown": "Buy milk",
                "confidence": 0.82,
                "handwriting": {"ok": True, "engine": "gb10_trocr_handwriting", "text": "Buy milk", "confidence": 0.82, "regions": [], "reason": "test"},
                "quality": {"score": 0.8, "chars": 8, "structure_score": 0.4, "numeric_fidelity_score": 0.0, "table_rows": 0},
                "source_path": str(path),
            }
        return {"ok": False, "engine": engine, "error": "disabled", "quality": {"score": 0.0}}

    monkeypatch.setattr(ocr_dual_tool, "_run_engine_once", fake_run_engine_once)
    monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda source_path, text: {"enabled": True, "status": "ok"})
    out = ocr_dual_tool._run_policy_route(
        img_path,
        goal="Read handwritten note",
        route_mode="quality_first",
        max_chars=4000,
        max_pages=1,
        consensus=False,
        use_gateway=False,
    )
    assert out["engine"] == "gb10_trocr_handwriting"
    assert out["document_features"]["has_handwriting"] is True
    assert out["handwriting"]["ok"] is True
    assert "Buy milk" in out["markdown"]


def test_chart_lane_output_records_charts(monkeypatch, tmp_path):
    img_path = tmp_path / "chart.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    def fake_run_engine_once(path, engine, goal, *, max_chars, max_pages, route_mode="balanced", use_gateway=False):
        if engine == "gb10_deplot_chart":
            return {
                "ok": True,
                "engine": "gb10_deplot_chart",
                "text": "Year | Sales\n2025 | 10",
                "markdown": "Year | Sales\n2025 | 10",
                "charts": [{"ok": True, "engine": "gb10_deplot_chart", "table": "Year | Sales\n2025 | 10", "source_region": {}, "confidence": None, "reason": "test"}],
                "quality": {"score": 0.82, "chars": 22, "structure_score": 0.7, "numeric_fidelity_score": 0.7, "table_rows": 1},
                "source_path": str(path),
            }
        return {"ok": False, "engine": engine, "error": "disabled", "quality": {"score": 0.0}}

    monkeypatch.setattr(ocr_dual_tool, "_run_engine_once", fake_run_engine_once)
    monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda source_path, text: {"enabled": True, "status": "ok"})
    out = ocr_dual_tool._run_policy_route(
        img_path,
        goal="Extract chart data",
        route_mode="quality_first",
        max_chars=4000,
        max_pages=1,
        consensus=False,
        use_gateway=False,
    )
    assert out["engine"] == "gb10_deplot_chart"
    assert out["document_features"]["has_chart"] is True
    assert out["charts"][0]["table"].startswith("Year")


def test_figure_description_prompt_variant_records_figures(monkeypatch, tmp_path):
    img_path = tmp_path / "figure.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []

    def fake_run_gb10_engine(path, engine, goal, max_chars, max_pages, route_mode="quality_first", use_gateway=False):
        calls.append(goal)
        return {
            "ok": True,
            "engine": "gb10_qwen_ocr",
            "text": "The figure shows rising revenue.",
            "markdown": "The figure shows rising revenue.",
            "quality": {"score": 0.7, "chars": 32, "structure_score": 0.4, "numeric_fidelity_score": 0.0, "table_rows": 0},
        }

    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", fake_run_gb10_engine)
    augmented = ocr_dual_tool._augment_visual_lanes(
        {"ok": True, "engine": "gb10_qwen_ocr", "text": "Base", "markdown": "Base", "quality": {"score": 0.7}},
        img_path,
        "Describe this figure",
        {"has_figure": True, "has_chart": False, "has_handwriting": False, "layout_class": "chart_or_figure"},
        max_chars=4000,
        max_pages=1,
        route_mode="quality_first",
        use_gateway=False,
    )
    assert augmented["figures"][0]["description"].startswith("The figure")
    assert calls and calls[0].startswith("figure_description:")


def test_table_dense_chain_keeps_deplot_out_of_table_route():
    chain = ocr_dual_tool._policy_engine_chain("table_dense", "quality_first")
    assert "gb10_deplot_chart" not in chain
    assert "gb10_paddleocr_vl" in chain


def test_image_scale_variant_uses_realesrgan_for_two_x(monkeypatch, tmp_path):
    img_path = tmp_path / "scan.png"
    image = Image.new("RGB", (20, 20), "white")
    image.save(img_path)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_REALESRGAN_URL", "http://127.0.0.1:5221/ocr/realesrgan/upscale")
    monkeypatch.setattr(
        ocr_dual_tool,
        "_preprocess_service_image",
        lambda image, url, extra_payload=None, timeout_sec=60: Image.new("RGB", (25, 25), "white"),
    )
    variants = ocr_dual_tool._render_image_scale_variants(img_path, scales=[1.0, 2.0], tag="test")
    try:
        two_x = next(row for row in variants if row["scale"] == 2.0)
        with Image.open(two_x["path"]) as rendered:
            assert rendered.size == (25, 25)
    finally:
        for row in variants:
            try:
                row["path"].unlink()
            except Exception:
                pass


def test_image_scale_variant_falls_back_to_pil_resize_when_realesrgan_unavailable(monkeypatch, tmp_path):
    img_path = tmp_path / "scan.png"
    image = Image.new("RGB", (20, 20), "white")
    image.save(img_path)
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_REALESRGAN_URL", "http://127.0.0.1:5221/ocr/realesrgan/upscale")
    monkeypatch.setattr(ocr_dual_tool, "_preprocess_service_image", lambda *args, **kwargs: None)
    variants = ocr_dual_tool._render_image_scale_variants(img_path, scales=[2.0], tag="test")
    try:
        with Image.open(variants[0]["path"]) as rendered:
            assert rendered.size == (40, 40)
    finally:
        for row in variants:
            try:
                row["path"].unlink()
            except Exception:
                pass


def test_run_policy_route_exposes_multi_dpi_self_consistency_for_forced_engine(monkeypatch, tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(ocr_dual_tool, "_classify_doc_type", lambda path, goal: "scanned_pdf")
    monkeypatch.setattr(
        ocr_dual_tool,
        "_semantic_diff_check",
        lambda source_path, text: {"enabled": True, "status": "ok"},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_tableformer_or_lgpma_reconstruct",
        lambda markdown, source_path="": {"ok": True, "mode": "heuristic", "tables": []},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_finance_consistency_checks",
        lambda markdown, tables=None: {"score": 0.75, "issues": [], "hints": ["balanced"]},
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_finbert_eval_signal",
        lambda text: {"enabled": True, "mode": "heuristic_proxy", "status": "ok"},
    )

    def fake_run_engine_once(path, engine, goal, *, max_chars, max_pages, route_mode="balanced", use_gateway=False):
        if engine == "gb10_got_ocr2":
            markdown = "Assets: 110\nLiabilities: 80\nEquity: 30"
            return {
                "ok": True,
                "engine": engine,
                "text": markdown,
                "markdown": markdown,
                "confidence": 0.77,
                "quality": {
                    "score": 0.62,
                    "chars": len(markdown),
                    "confidence": 0.77,
                    "structure_score": 0.48,
                    "numeric_fidelity_score": 0.67,
                    "table_rows": 0,
                },
                "source_path": str(path),
            }
        return {"ok": False, "engine": engine, "error": f"{engine}_unavailable", "quality": {"score": 0.0}}

    monkeypatch.setattr(ocr_dual_tool, "_run_engine_once", fake_run_engine_once)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_phase2_pdf_dpi_ensemble",
        lambda path, engine, goal, **kwargs: {
            "ok": True,
            "engine": engine,
            "candidates": [
                {"engine": f"{engine}:dpi_150", "quality": {"score": 0.83}, "phase2": {"self_consistency_variant": "dpi_150", "dpi": 150}},
                {"engine": f"{engine}:dpi_200", "quality": {"score": 0.81}, "phase2": {"self_consistency_variant": "dpi_200", "dpi": 200}},
                {"engine": f"{engine}:dpi_300", "quality": {"score": 0.84}, "phase2": {"self_consistency_variant": "dpi_300", "dpi": 300}},
                {"engine": f"{engine}:dpi_400", "quality": {"score": 0.85}, "phase2": {"self_consistency_variant": "dpi_400", "dpi": 400}},
            ],
            "attempts": [],
            "voted": {
                "ok": True,
                "engine": "phase2_ensemble_vote",
                "text": "Assets: 100\nLiabilities: 70\nEquity: 30",
                "markdown": "Assets: 100\nLiabilities: 70\nEquity: 30",
                "confidence": 0.84,
                "quality": {
                    "score": 0.84,
                    "chars": 39,
                    "confidence": 0.84,
                    "structure_score": 0.61,
                    "numeric_fidelity_score": 0.82,
                    "table_rows": 0,
                },
                "phase2": {
                    "self_consistency_used": True,
                    "vote_mode": "multi_dpi_line_vote",
                    "dpi_samples": [150, 200, 300, 400],
                    "variant_count": 4,
                    "contributing_engines": [f"{engine}:dpi_150", f"{engine}:dpi_200", f"{engine}:dpi_300", f"{engine}:dpi_400"],
                },
                "source_path": str(path),
                "semantic_diff": {"enabled": True, "status": "ok"},
            },
        },
    )

    out = ocr_dual_tool._run_policy_route(
        pdf_path,
        goal="Read scan",
        route_mode="quality_first",
        max_chars=4000,
        max_pages=1,
        forced_engine="gb10_got_ocr2",
        consensus=True,
        use_gateway=False,
    )
    ensemble = out["phase2"]["ensemble"]
    assert ensemble["self_consistency_used"] is True
    assert ensemble["dpi_samples"] == [150, 200, 300, 400]
    assert ensemble["variant_count"] == 4
    assert out["quality"]["score"] >= 0.84


def test_table_reconstruction_reports_backend_configuration(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_TABLEFORMER_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_LGPMA_URL", "")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "tableformer_reconstruct",
        lambda source_path, text="", normalize_text=None: {"ok": False, "error": "tableformer_runtime_unavailable"},
    )
    out = ocr_dual_tool._tableformer_or_lgpma_reconstruct("| A | B |\n| 1 | 2 |")
    assert out["mode"] == "heuristic"
    assert out["ok"] is True
    backends = dict(out.get("backends") or {})
    assert backends.get("tableformer", {}).get("configured") is False
    assert backends.get("lgpma", {}).get("configured") is False
    assert backends.get("tableformer_local", {}).get("configured") is False


def test_phase2_literal_service_state_requires_runtime_loaded(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "_http_endpoint_reachable", lambda url, timeout_sec=3: {"ok": True, "reason": "http_200", "url": url})

    class _Resp:
        ok = True

        def json(self):
            return {"runtime_loaded": True, "backend": "docunet_paddle_uvdoc"}

    monkeypatch.setattr(ocr_dual_tool.requests, "get", lambda url, timeout=5: _Resp())
    status = ocr_dual_tool._phase2_literal_service_state("http://127.0.0.1:5221/ocr/docunet/rectify", mode_name="docunet")
    assert status["classification"] == "implemented_literal"
    assert status["runtime_loaded"] is True


def test_preprocess_variants_use_docunet_and_realesrgan_services(monkeypatch):
    image = ocr_dual_tool.Image.new("RGB", (16, 16), "white")
    calls = []

    def fake_service(img, url, *, extra_payload=None, timeout_sec=30):
        calls.append((url, dict(extra_payload or {})))
        return img

    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_DOCUNET_URL", "http://127.0.0.1:5221/ocr/docunet/rectify")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_REALESRGAN_URL", "http://127.0.0.1:5221/ocr/realesrgan/upscale")
    monkeypatch.setattr(ocr_dual_tool, "_preprocess_service_image", fake_service)
    variants = ocr_dual_tool._preprocess_variants(image)
    assert variants
    assert calls[0][0].endswith("/ocr/docunet/rectify")
    assert calls[1][0].endswith("/ocr/realesrgan/upscale")
    assert calls[1][1]["outscale"] == 2.0


def test_finbert_eval_signal_uses_heuristic_proxy_when_unconfigured(monkeypatch):
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_FINBERT_URL", "")
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "finbert_eval",
        lambda text: {"ok": False, "error": "local_finbert_unavailable"},
    )
    out = ocr_dual_tool._finbert_eval_signal("Balance sheet margin and liquidity risk.")
    assert out["enabled"] is True
    assert out["mode"] == "heuristic_proxy"


def test_got_ocr2_uses_local_inference_when_url_unset(monkeypatch, tmp_path):
    doc_path = tmp_path / "scan.png"
    doc_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_GOT_OCR_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "got_extract_texts",
        lambda path, goal, max_chars, max_pages, normalize_markdown=None, normalize_text=None: {
            "ok": True,
            "engine": "gb10_got_ocr2",
            "model": "stepfun-ai/GOT-OCR-2.0-hf",
            "text": "Margin requirement 25000",
            "markdown": "Margin requirement 25000",
            "confidence": 0.873,
            "pages": 1,
            "route": "local_got_transformers",
        },
    )
    result = ocr_dual_tool._run_gb10_engine(
        doc_path,
        "gb10_got_ocr2",
        "Read dense scan",
        max_chars=4000,
        max_pages=1,
        use_gateway=False,
    )
    assert result["ok"] is True
    assert result["route"] == "local_got_transformers"
    assert result["confidence"] == 0.873


def test_got_ocr2_returns_explicit_failure_when_local_fallback_fails(monkeypatch, tmp_path):
    doc_path = tmp_path / "scan.png"
    doc_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_GOT_OCR_URL", "")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "got_extract_texts",
        lambda path, goal, max_chars, max_pages, normalize_markdown=None, normalize_text=None: {
            "ok": False,
            "error": "got_runtime_unavailable",
        },
    )
    result = ocr_dual_tool._run_gb10_engine(
        doc_path,
        "gb10_got_ocr2",
        "Read dense scan",
        max_chars=4000,
        max_pages=1,
        use_gateway=False,
    )
    assert result["ok"] is False
    assert "got_runtime_unavailable" in result["error"]


def test_got_ocr2_prefers_local_inference_when_remote_is_configured(monkeypatch, tmp_path):
    doc_path = tmp_path / "scan.png"
    doc_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_GOT_OCR_URL", "http://got.local/extract")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
    monkeypatch.setattr(
        ocr_dual_tool.ocr_local_inference,
        "got_extract_texts",
        lambda path, goal, max_chars, max_pages, normalize_markdown=None, normalize_text=None: {
            "ok": True,
            "engine": "gb10_got_ocr2",
            "model": "stepfun-ai/GOT-OCR-2.0-hf",
            "text": "Margin requirement 25000",
            "markdown": "Margin requirement 25000",
            "confidence": 0.873,
            "pages": 1,
            "route": "local_got_transformers",
        },
    )
    monkeypatch.setattr(
        ocr_dual_tool,
        "_remote_ocr_via_http",
        lambda **kwargs: {
            "ok": True,
            "engine": "gb10_got_ocr2",
            "text": "wrong",
            "markdown": "wrong",
            "route": "gb10_remote",
        },
    )
    result = ocr_dual_tool._run_gb10_engine(
        doc_path,
        "gb10_got_ocr2",
        "Read dense scan",
        max_chars=4000,
        max_pages=1,
        use_gateway=False,
    )
    assert result["ok"] is True
    assert result["route"] == "local_got_transformers"


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.ok = 200 <= status_code < 300
        self.text = ""

    def json(self):
        return self._payload


def test_backend_readiness_prefers_gateway_over_direct_and_qwen(monkeypatch):
    def fake_get(url, timeout=0):
        if str(url).endswith("/ocr/health"):
            return _Resp(200, {"ok": True})
        if str(url).endswith("/api/tags"):
            return _Resp(200, {"models": []})
        return _Resp(404, {})

    monkeypatch.setattr("common.ocr_dual_tool.requests.get", fake_get)
    monkeypatch.setattr("common.ocr_dual_tool.requests.head", lambda url, timeout=0: _Resp(405, {}))
    readiness = gb10_ocr_backend_readiness(
        {
            "use_gateway": True,
            "gateway_url": "http://127.0.0.1:9999",
            "paddle_url": "http://127.0.0.1:9101/ocr",
            "got_url": "http://127.0.0.1:9102/ocr",
            "qwen_primary": True,
            "qwen_ollama_url": "http://127.0.0.1:11434",
        }
    )
    assert readiness["ready"] is True
    assert readiness["mode"] == "gateway"


def test_backend_readiness_prefers_direct_when_gateway_unavailable(monkeypatch):
    def fake_get(url, timeout=0):
        text = str(url)
        if text.endswith("/ocr/health"):
            return _Resp(503, {})
        if "9101" in text or "9102" in text:
            return _Resp(200, {"ok": True})
        if text.endswith("/api/tags"):
            return _Resp(200, {"models": []})
        return _Resp(404, {})

    monkeypatch.setattr("common.ocr_dual_tool.requests.get", fake_get)
    def fake_head(url, timeout=0):
        text = str(url)
        if "9999" in text:
            return _Resp(503, {})
        if "9101" in text or "9102" in text:
            return _Resp(405, {})
        return _Resp(503, {})

    monkeypatch.setattr("common.ocr_dual_tool.requests.head", fake_head)
    readiness = gb10_ocr_backend_readiness(
        {
            "use_gateway": True,
            "gateway_url": "http://127.0.0.1:9999",
            "paddle_url": "http://127.0.0.1:9101/ocr",
            "got_url": "http://127.0.0.1:9102/ocr",
            "qwen_primary": True,
            "qwen_ollama_url": "http://127.0.0.1:11434",
        }
    )
    assert readiness["ready"] is True
    assert readiness["mode"] == "direct"


def test_backend_readiness_uses_qwen_primary_when_explicit(monkeypatch):
    def fake_get(url, timeout=0):
        text = str(url)
        if text.endswith("/api/tags"):
            return _Resp(200, {"models": [{"name": "qwen2.5vl:7b"}]})
        return _Resp(503, {})

    monkeypatch.setattr("common.ocr_dual_tool.requests.get", fake_get)
    monkeypatch.setattr("common.ocr_dual_tool.requests.head", lambda url, timeout=0: _Resp(503, {}))
    readiness = gb10_ocr_backend_readiness(
        {
            "use_gateway": True,
            "gateway_url": "http://127.0.0.1:9999",
            "paddle_url": "",
            "got_url": "",
            "qwen_primary": True,
            "qwen_model": "qwen2.5vl:7b",
            "qwen_ollama_url": "http://127.0.0.1:11434",
        }
    )
    assert readiness["ready"] is True
    assert readiness["mode"] == "qwen_primary"


def test_backend_readiness_rejects_non_vision_qwen_model(monkeypatch):
    def fake_get(url, timeout=0):
        text = str(url)
        if text.endswith("/api/tags"):
            return _Resp(200, {"models": [{"name": "gemma3:12b"}]})
        return _Resp(503, {})

    monkeypatch.setattr("common.ocr_dual_tool.requests.get", fake_get)
    monkeypatch.setattr("common.ocr_dual_tool.requests.head", lambda url, timeout=0: _Resp(503, {}))
    readiness = gb10_ocr_backend_readiness(
        {
            "use_gateway": False,
            "gateway_url": "",
            "paddle_url": "",
            "got_url": "",
            "qwen_primary": True,
            "qwen_model": "gemma3:12b",
            "qwen_fallback_model": "gemma3:12b",
            "qwen_ollama_url": "http://127.0.0.1:11434",
        }
    )
    assert readiness["ready"] is False
    assert readiness["mode"] == "unavailable"
    assert "non_vision_model" in str(readiness["fail_reason"])


def test_split_columns_prefers_surya_result_when_present(monkeypatch):
    if not getattr(ocr_dual_tool, "CV2_AVAILABLE", False):
        pytest.skip("cv2/numpy unavailable")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_COLUMN_SPLIT_ENABLED", True)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_split_columns_surya",
        lambda gray: [gray[:, : 600], gray[:, 600:]],
    )
    gray = ocr_dual_tool.np.zeros((1200, 1600), dtype="uint8")
    cols = ocr_dual_tool._split_columns(gray)
    assert len(cols) == 2
    assert cols[0].shape[1] == 600


def test_backend_readiness_includes_surya_layout_block(monkeypatch):
    monkeypatch.setattr("common.ocr_dual_tool.requests.get", lambda url, timeout=0: _Resp(503, {}))
    monkeypatch.setattr("common.ocr_dual_tool.requests.head", lambda url, timeout=0: _Resp(503, {}))
    readiness = gb10_ocr_backend_readiness(
        {
            "use_gateway": False,
            "gateway_url": "",
            "paddle_url": "",
            "got_url": "",
            "qwen_primary": False,
            "qwen_ollama_url": "http://127.0.0.1:11434",
        }
    )
    surya = dict((readiness.get("checks") or {}).get("surya_layout") or {})
    assert "available" in surya
    assert "enabled" in surya


def test_qwen_ocr_can_skip_self_consistency(monkeypatch):
    img_path = _TMP_DIR / "qwen_no_self_consistency.png"
    Image.new("RGB", (80, 32), "white").save(img_path)
    lanes = []

    def fake_ollama_generate(image_path, prompt, model, *, base_url="", lane=""):
        lanes.append(lane)
        return {
            "ok": True,
            "engine": "gb10_qwen_ocr",
            "model": model,
            "text": "OK 12345",
            "markdown": "OK 12345",
            "route": lane,
        }

    monkeypatch.setattr(ocr_dual_tool, "_ollama_generate_with_image", fake_ollama_generate)
    result = ocr_dual_tool._gb10_qwen_ocr(
        img_path,
        "Extract all visible text exactly.",
        max_chars=200,
        max_pages=1,
        self_consistency=False,
    )
    assert result["ok"] is True
    assert lanes
    assert all("self_consistency" not in lane for lane in lanes)
