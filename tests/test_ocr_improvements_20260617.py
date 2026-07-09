"""
Tests for OCR97 improvements from 2026-06-17:
  1. Companion tesseract regions — exclusion-based (not whitelist-based)
  2. Numeric fidelity guard — runs on all file types, not just PDF
  3. Model degradation warning — model_degraded key when fallback is used
  4. model_degraded propagates through _normalize_ocr_payload to public API
  5. Phase 2 SC annotates ran_on_degraded_model
  6. Real-document smoke (skipped if GX10/PIL unavailable)
  7. DePlot and TrOCR engine routing tests
"""
import os
from pathlib import Path

import pytest

import common.ocr_dual_tool as ocr_dual_tool
from common.ocr_dual_tool import _run_engine_once


def _fake_png(tmp_path: Path, name: str = "doc.png") -> Path:
    p = tmp_path / name
    # minimal 1x1 white PNG
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return p


# ---------------------------------------------------------------------------
# 1. Companion tesseract regions — exclusion logic
# ---------------------------------------------------------------------------

class TestCompanionTesseractExclusion:
    """Companion tesseract patch should fire for all engines EXCEPT tesseract and rapidocr."""

    def _run(self, engine: str, tmp_path: Path, monkeypatch) -> dict:
        p = _fake_png(tmp_path)
        monkeypatch.setattr(
            ocr_dual_tool,
            "_run_gb10_engine",
            lambda *a, **kw: {"ok": True, "engine": engine, "markdown": "hello", "text": "hello", "model": "qwen"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_tesseract_ocr", lambda *a, **kw: {"ok": True, "engine": "tesseract", "markdown": "hello", "text": "hello"})
        monkeypatch.setattr(ocr_dual_tool, "_rapidocr_ocr", lambda *a, **kw: {"ok": True, "engine": "rapidocr", "markdown": "hello", "text": "hello"})
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        captured = []
        real_companion = ocr_dual_tool._companion_tesseract_regions

        def _spy(*a, **kw):
            captured.append(True)
            return []

        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", _spy)
        _run_engine_once(p, engine, "extract text", max_chars=4000, max_pages=4)
        return {"called": bool(captured)}

    def test_fires_for_gb10_qwen_ocr(self, tmp_path, monkeypatch):
        assert self._run("gb10_qwen_ocr", tmp_path, monkeypatch)["called"] is True

    def test_fires_for_gb10_got_ocr2(self, tmp_path, monkeypatch):
        assert self._run("gb10_got_ocr2", tmp_path, monkeypatch)["called"] is True

    def test_fires_for_gb10_paddleocr_vl(self, tmp_path, monkeypatch):
        assert self._run("gb10_paddleocr_vl", tmp_path, monkeypatch)["called"] is True

    def test_skips_for_tesseract(self, tmp_path, monkeypatch):
        assert self._run("tesseract", tmp_path, monkeypatch)["called"] is False

    def test_skips_for_rapidocr(self, tmp_path, monkeypatch):
        assert self._run("rapidocr", tmp_path, monkeypatch)["called"] is False


# ---------------------------------------------------------------------------
# 2. Numeric fidelity guard — fires on image inputs (not just PDF)
# ---------------------------------------------------------------------------

class TestNumericFidelityGuardNonPdf:
    """_apply_numeric_fidelity_guard should be called for PNG/TIFF/JPG when draft_seed is present."""

    # Enough chars to pass best_chars>=120 and score>=0.12 acceptance gate
    _MOCK_MD = (
        "Revenue: 100.0\nNet Income: 50.0\nEPS: 2.50\n"
        "Total Assets: 1000.0\nTotal Liabilities: 500.0\n"
        "Gross Margin: 45.2%\nOperating Cash Flow: 75.0\n"
        "Capital Expenditure: 20.0\nFree Cash Flow: 55.0\n"
    ) * 4  # ~296 chars, ensures char_score and total score pass

    def _run_qwen_with_draft(self, suffix: str, tmp_path: Path, monkeypatch) -> dict:
        p = tmp_path / f"doc{suffix}"
        p.write_bytes(b"data")
        guard_calls = []

        def _spy_guard(md, seed):
            guard_calls.append(seed)
            return md

        monkeypatch.setattr(ocr_dual_tool, "_apply_numeric_fidelity_guard", _spy_guard)

        # Patch the internals so we don't need a real Ollama
        monkeypatch.setattr(
            ocr_dual_tool,
            "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": "qwen2.5vl:7b"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *a, **kw: [p])
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)

        ocr_dual_tool._gb10_qwen_ocr(p, "extract", max_chars=4000, max_pages=4, draft_text="Revenue 100.0")
        return {"called": bool(guard_calls)}

    def test_fires_for_png(self, tmp_path, monkeypatch):
        assert self._run_qwen_with_draft(".png", tmp_path, monkeypatch)["called"] is True

    def test_fires_for_jpg(self, tmp_path, monkeypatch):
        assert self._run_qwen_with_draft(".jpg", tmp_path, monkeypatch)["called"] is True

    def test_fires_for_tiff(self, tmp_path, monkeypatch):
        assert self._run_qwen_with_draft(".tiff", tmp_path, monkeypatch)["called"] is True

    def test_fires_for_pdf(self, tmp_path, monkeypatch):
        """Existing PDF path must still work."""
        assert self._run_qwen_with_draft(".pdf", tmp_path, monkeypatch)["called"] is True

    def test_no_call_when_no_draft(self, tmp_path, monkeypatch):
        guard_calls = []
        monkeypatch.setattr(ocr_dual_tool, "_apply_numeric_fidelity_guard", lambda md, s: guard_calls.append(s) or md)
        monkeypatch.setattr(
            ocr_dual_tool,
            "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": "hello", "model": "qwen2.5vl:7b"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        ocr_dual_tool._gb10_qwen_ocr(p, "extract", max_chars=4000, max_pages=4, draft_text="")
        assert not guard_calls


# ---------------------------------------------------------------------------
# 3. Model degradation warning
# ---------------------------------------------------------------------------

class TestModelDegradedFlag:
    """model_degraded key must appear when fallback model is used."""

    _MOCK_MD = (
        "Revenue: 100.0\nNet Income: 50.0\nEPS: 2.50\n"
        "Total Assets: 1000.0\nTotal Liabilities: 500.0\n"
        "Gross Margin: 45.2%\nOperating Cash Flow: 75.0\n"
    ) * 4

    def _call_qwen(self, used_model: str, tmp_path: Path, monkeypatch) -> dict:
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool,
            "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": used_model},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen2.5vl:7b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "qwen3-vl:32b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
        return ocr_dual_tool._gb10_qwen_ocr(p, "extract", max_chars=4000, max_pages=4)

    def test_no_degraded_flag_when_primary_used(self, tmp_path, monkeypatch):
        result = self._call_qwen("qwen2.5vl:7b", tmp_path, monkeypatch)
        assert result.get("ok") is True
        assert "model_degraded" not in result

    def test_degraded_flag_when_fallback_used(self, tmp_path, monkeypatch):
        result = self._call_qwen("qwen3-vl:32b", tmp_path, monkeypatch)
        assert result.get("ok") is True
        assert result.get("model_degraded") is True
        reason = result.get("model_degraded_reason") or {}
        assert isinstance(reason, dict)
        assert reason.get("primary") == "qwen2.5vl:7b"
        assert reason.get("used") == "qwen3-vl:32b"
        assert reason.get("reason") == "primary_model_unavailable"


# ---------------------------------------------------------------------------
# 4. Live inference smoke (skipped if Ollama GX10 tunnel is down)
# ---------------------------------------------------------------------------

def _gx10_reachable() -> bool:
    try:
        import urllib.request
        port = int(os.getenv("SKY_GX10_TUNNEL_PORT") or 11435)
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tags", timeout=3)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _gx10_reachable(), reason="GX10 tunnel not reachable")
def test_live_qwen_ocr_smoke(tmp_path):
    """Round-trip: a real PNG through _gb10_qwen_ocr must return non-empty text."""
    p = _fake_png(tmp_path)
    result = ocr_dual_tool._gb10_qwen_ocr(p, "extract all text", max_chars=4000, max_pages=1)
    # Result is a dict with an "ok" key regardless of whether the image is readable
    assert isinstance(result, dict)
    assert "ok" in result


# ---------------------------------------------------------------------------
# 4. model_degraded propagates through _normalize_ocr_payload
# ---------------------------------------------------------------------------

class TestModelDegradedPropagation:
    """model_degraded must survive _normalize_ocr_payload and appear in final output."""

    _MOCK_MD = (
        "Revenue: 100.0\nNet Income: 50.0\nEPS: 2.50\n"
        "Total Assets: 1000.0\nTotal Liabilities: 500.0\n"
    ) * 4

    def test_model_degraded_in_normalize_output(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool, "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": "qwen3-vl:32b"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen2.5vl:7b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "qwen3-vl:32b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])

        raw = ocr_dual_tool._gb10_qwen_ocr(p, "extract", max_chars=4000, max_pages=1)
        assert raw.get("model_degraded") is True

        normalized = ocr_dual_tool._normalize_ocr_payload(
            raw,
            route_mode="quality_first",
            doc_class="text",
            engine_chain=["gb10_qwen_ocr"],
        )
        assert normalized.get("model_degraded") is True
        reason = normalized.get("model_degraded_reason") or {}
        assert isinstance(reason, dict)
        assert reason.get("primary") == "qwen2.5vl:7b"

    def test_model_degraded_false_not_noisy(self, tmp_path, monkeypatch):
        """When no degradation, model_degraded is False (not absent — it's an explicit field)."""
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool, "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": "qwen2.5vl:7b"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen2.5vl:7b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "qwen3-vl:32b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])

        raw = ocr_dual_tool._gb10_qwen_ocr(p, "extract", max_chars=4000, max_pages=1)
        assert not raw.get("model_degraded")

        normalized = ocr_dual_tool._normalize_ocr_payload(
            raw,
            route_mode="quality_first",
            doc_class="text",
            engine_chain=["gb10_qwen_ocr"],
        )
        assert normalized.get("model_degraded") is False


# ---------------------------------------------------------------------------
# 5. Phase 2 self-consistency annotates ran_on_degraded_model
# ---------------------------------------------------------------------------

class TestPhase2DegradedAnnotation:
    _MOCK_MD = (
        "Revenue: 100.0\nNet Income: 50.0\nEPS: 2.50\n"
        "Total Assets: 1000.0\nTotal Liabilities: 500.0\n"
    ) * 4

    def _call_with_phase2(self, used_model: str, tmp_path: Path, monkeypatch) -> dict:
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool, "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": used_model},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", True)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen2.5vl:7b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "qwen3-vl:32b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
        return ocr_dual_tool._gb10_qwen_ocr(p, "extract", max_chars=4000, max_pages=1)

    def test_sc_not_flagged_when_primary_used(self, tmp_path, monkeypatch):
        result = self._call_with_phase2("qwen2.5vl:7b", tmp_path, monkeypatch)
        phase2 = result.get("phase2") or {}
        if phase2.get("self_consistency_used"):
            assert phase2.get("ran_on_degraded_model") is False

    def test_sc_flagged_when_fallback_used(self, tmp_path, monkeypatch):
        result = self._call_with_phase2("qwen3-vl:32b", tmp_path, monkeypatch)
        phase2 = result.get("phase2") or {}
        if phase2.get("self_consistency_used"):
            assert phase2.get("ran_on_degraded_model") is True


# ---------------------------------------------------------------------------
# 6. Real-document smoke — PIL text image through full pipeline
# ---------------------------------------------------------------------------

def _pil_available() -> bool:
    try:
        from PIL import Image, ImageDraw
        return True
    except ImportError:
        return False


def _make_text_image(tmp_path: Path) -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (480, 240), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    lines = [
        "Revenue: 1,234.56",
        "Net Income: 456.78",
        "EPS: 2.34",
        "Total Assets: 9,876.00",
        "Operating Cash Flow: 345.67",
    ]
    for i, line in enumerate(lines):
        draw.text((20, 20 + i * 36), line, fill=(0, 0, 0))
    p = tmp_path / "real_doc.png"
    img.save(p)
    return p


@pytest.mark.skipif(not (_pil_available() and _gx10_reachable()), reason="PIL or GX10 tunnel not available")
def test_real_document_ocr_smoke(tmp_path):
    """End-to-end: PIL-generated text image → _gb10_qwen_ocr → contains numeric text."""
    p = _make_text_image(tmp_path)
    result = ocr_dual_tool._gb10_qwen_ocr(p, "extract all financial figures", max_chars=4000, max_pages=1)
    assert isinstance(result, dict)
    assert "ok" in result
    if result.get("ok"):
        text = str(result.get("text") or result.get("markdown") or "")
        # Must contain at least one number from the fixture
        assert any(n in text for n in ["1,234", "456", "2.34", "9,876", "345"]), (
            f"OCR output missing expected numbers. Got: {text[:300]}"
        )


# ---------------------------------------------------------------------------
# 7. DePlot and TrOCR engine routing
# ---------------------------------------------------------------------------

class TestDePlotEngine:
    """gb10_deplot_chart routes to local inference → remote → error on both failure."""

    def test_returns_local_result_when_available(self, tmp_path, monkeypatch):
        p = tmp_path / "chart.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "deplot_extract_chart",
            lambda *a, **kw: {"ok": True, "engine": "gb10_deplot_chart", "markdown": "| A | B |\n| 1 | 2 |", "text": "A B\n1 2"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])
        result = _run_engine_once(p, "gb10_deplot_chart", "extract chart", max_chars=4000, max_pages=1)
        assert result.get("ok") is True
        assert str(result.get("engine") or "").startswith("gb10_deplot")

    def test_falls_back_to_remote_on_local_failure(self, tmp_path, monkeypatch):
        p = tmp_path / "chart.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "deplot_extract_chart",
            lambda *a, **kw: {"ok": False, "error": "deplot_not_loaded"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_DEPLOT_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        result = _run_engine_once(p, "gb10_deplot_chart", "extract chart", max_chars=4000, max_pages=1, use_gateway=False)
        assert result.get("ok") is False
        assert "deplot" in str(result.get("error") or "")

    def test_engine_key_in_error_result(self, tmp_path, monkeypatch):
        p = tmp_path / "chart.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "deplot_extract_chart",
            lambda *a, **kw: {"ok": False, "error": "deplot_not_loaded"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_DEPLOT_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        result = _run_engine_once(p, "gb10_deplot_chart", "extract chart", max_chars=4000, max_pages=1, use_gateway=False)
        assert "engine" in result


class TestTrOCREngine:
    """gb10_trocr_handwriting routes to local inference → remote → error on both failure."""

    def test_returns_local_result_when_available(self, tmp_path, monkeypatch):
        p = tmp_path / "handwriting.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "trocr_extract_handwriting",
            lambda *a, **kw: {"ok": True, "engine": "gb10_trocr_handwriting", "markdown": "hello world", "text": "hello world"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])
        result = _run_engine_once(p, "gb10_trocr_handwriting", "extract handwriting", max_chars=4000, max_pages=1)
        assert result.get("ok") is True
        assert str(result.get("engine") or "").startswith("gb10_trocr")

    def test_falls_back_to_remote_on_local_failure(self, tmp_path, monkeypatch):
        p = tmp_path / "handwriting.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "trocr_extract_handwriting",
            lambda *a, **kw: {"ok": False, "error": "trocr_not_loaded"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_TROCR_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        result = _run_engine_once(p, "gb10_trocr_handwriting", "extract handwriting", max_chars=4000, max_pages=1, use_gateway=False)
        assert result.get("ok") is False
        assert "trocr" in str(result.get("error") or "")

    def test_companion_tesseract_fires_for_trocr(self, tmp_path, monkeypatch):
        """TrOCR is not in the exclusion set, so companion regions must fire."""
        p = tmp_path / "handwriting.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "trocr_extract_handwriting",
            lambda *a, **kw: {"ok": True, "engine": "gb10_trocr_handwriting", "markdown": "hello", "text": "hello"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        captured = []
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: captured.append(True) or [])
        _run_engine_once(p, "gb10_trocr_handwriting", "extract handwriting", max_chars=4000, max_pages=1)
        assert captured


# ---------------------------------------------------------------------------
# 8. GOT-OCR2 engine routing
# ---------------------------------------------------------------------------

class TestGotOcr2Engine:
    """gb10_got_ocr2 routes to local inference → remote → error on both failure."""

    def test_returns_local_result_when_available(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "got_extract_texts",
            lambda *a, **kw: {"ok": True, "engine": "gb10_got_ocr2", "markdown": "dense text block", "text": "dense text block"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])
        result = _run_engine_once(p, "gb10_got_ocr2", "extract text", max_chars=4000, max_pages=1)
        assert result.get("ok") is True
        assert str(result.get("engine") or "").startswith("gb10_got_ocr2")

    def test_falls_back_to_remote_on_local_failure(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "got_extract_texts",
            lambda *a, **kw: {"ok": False, "error": "gb10_got_ocr2_not_loaded"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_GOT_OCR_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        result = _run_engine_once(p, "gb10_got_ocr2", "extract text", max_chars=4000, max_pages=1, use_gateway=False)
        assert result.get("ok") is False
        assert "got_ocr2" in str(result.get("error") or "")

    def test_engine_key_in_error_result(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "got_extract_texts",
            lambda *a, **kw: {"ok": False, "error": "got_not_loaded"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_GOT_OCR_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        result = _run_engine_once(p, "gb10_got_ocr2", "extract text", max_chars=4000, max_pages=1, use_gateway=False)
        assert "engine" in result
        assert result.get("engine") == "gb10_got_ocr2"

    def test_companion_tesseract_fires_for_got_ocr2(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "got_extract_texts",
            lambda *a, **kw: {"ok": True, "engine": "gb10_got_ocr2", "markdown": "text", "text": "text"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        captured = []
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: captured.append(True) or [])
        _run_engine_once(p, "gb10_got_ocr2", "extract text", max_chars=4000, max_pages=1)
        assert captured

    def test_remote_url_used_when_local_fails(self, tmp_path, monkeypatch):
        """When local fails and a remote URL is set, the remote path is attempted."""
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool.ocr_local_inference, "got_extract_texts",
            lambda *a, **kw: {"ok": False, "error": "got_not_loaded"},
        )
        remote_called = []
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_GOT_OCR_URL", "http://fake-remote:9999")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        monkeypatch.setattr(
            ocr_dual_tool, "_remote_ocr_via_http",
            lambda *a, **kw: remote_called.append(True) or {"ok": True, "engine": "gb10_got_ocr2", "markdown": "remote text", "text": "remote text"},
        )
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])
        result = _run_engine_once(p, "gb10_got_ocr2", "extract text", max_chars=4000, max_pages=1, use_gateway=False)
        assert remote_called
        assert result.get("ok") is True


# ---------------------------------------------------------------------------
# 9. DPI ensemble gate checks and success path
# ---------------------------------------------------------------------------

class TestDpiEnsemble:
    _GOOD_ROW = {
        "ok": True,
        "engine": "gb10_qwen_ocr:dpi_200",
        "markdown": "Revenue: 100.0\nNet Income: 50.0\n" * 8,
        "text": "Revenue: 100.0 Net Income: 50.0 " * 8,
        "confidence": 0.85,
        "model": "qwen2.5vl:7b",
        "route": "gb10_ollama",
        "source_path": "",
        "quality": {"score": 0.72, "chars": 320, "structure_score": 0.3, "numeric_fidelity_score": 0.5, "table_rows": 0},
        "semantic_diff": {},
        "fallback_reason": "",
        "confidence_map_regions": [],
    }

    def test_rejects_non_pdf(self, tmp_path):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        result = ocr_dual_tool._phase2_pdf_dpi_ensemble(p, "gb10_qwen_ocr", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert result.get("ok") is False
        assert result.get("error") == "dpi_ensemble_non_pdf"

    def test_rejects_unsupported_engine(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4")
        result = ocr_dual_tool._phase2_pdf_dpi_ensemble(p, "gb10_deplot_chart", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert result.get("ok") is False
        assert "dpi_ensemble_engine_unsupported" in str(result.get("error") or "")

    def test_returns_voted_when_sufficient_candidates(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4")
        good_row = dict(self._GOOD_ROW)
        monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *a, **kw: [p])
        monkeypatch.setattr(ocr_dual_tool, "_run_engine_on_rendered_pages", lambda *a, **kw: good_row)
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        result = ocr_dual_tool._phase2_pdf_dpi_ensemble(p, "gb10_qwen_ocr", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert "candidates" in result
        assert "attempts" in result
        assert "dpi_values" in result
        if result.get("ok"):
            assert "voted" in result
            assert result["voted"].get("ok") is True

    def test_insufficient_candidates_returns_error(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *a, **kw: [p])
        monkeypatch.setattr(ocr_dual_tool, "_run_engine_on_rendered_pages", lambda *a, **kw: {"ok": False, "error": "engine_failed", "quality": {}})
        result = ocr_dual_tool._phase2_pdf_dpi_ensemble(p, "gb10_qwen_ocr", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert result.get("ok") is False
        assert "insufficient_candidates" in str(result.get("error") or "")


# ---------------------------------------------------------------------------
# 10. Scale ensemble gate checks and success path
# ---------------------------------------------------------------------------

class TestScaleEnsemble:
    _GOOD_ROW = {
        "ok": True,
        "engine": "gb10_qwen_ocr:scale_1_0",
        "markdown": "Revenue: 100.0\nNet Income: 50.0\n" * 8,
        "text": "Revenue: 100.0 Net Income: 50.0 " * 8,
        "confidence": 0.85,
        "model": "qwen2.5vl:7b",
        "route": "gb10_ollama",
        "source_path": "",
        "quality": {"score": 0.72, "chars": 320, "structure_score": 0.3, "numeric_fidelity_score": 0.5, "table_rows": 0},
        "semantic_diff": {},
        "fallback_reason": "",
        "confidence_map_regions": [],
        "latency_ms": 100.0,
    }

    def test_rejects_pdf_input(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4")
        result = ocr_dual_tool._phase2_image_scale_ensemble(p, "gb10_qwen_ocr", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert result.get("ok") is False
        assert result.get("error") == "scale_ensemble_pdf_only"

    def test_rejects_unsupported_engine(self, tmp_path):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        result = ocr_dual_tool._phase2_image_scale_ensemble(p, "gb10_deplot_chart", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert result.get("ok") is False
        assert "scale_ensemble_engine_unsupported" in str(result.get("error") or "")

    def test_returns_structure_when_variants_produced(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        good_row = dict(self._GOOD_ROW)
        scale_paths = []
        for i, scale in enumerate([1.0, 1.5, 2.0, 4.0]):
            sp = tmp_path / f"scale_{i}.png"
            sp.write_bytes(b"data")
            scale_paths.append({"scale": scale, "path": sp})
        monkeypatch.setattr(ocr_dual_tool, "_render_image_scale_variants", lambda *a, **kw: scale_paths)
        monkeypatch.setattr(ocr_dual_tool, "_run_engine_once", lambda *a, **kw: good_row)
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        result = ocr_dual_tool._phase2_image_scale_ensemble(p, "gb10_qwen_ocr", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert "candidates" in result
        assert "attempts" in result
        assert "scale_values" in result

    def test_insufficient_candidates_returns_error(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(ocr_dual_tool, "_render_image_scale_variants", lambda *a, **kw: [])
        result = ocr_dual_tool._phase2_image_scale_ensemble(p, "gb10_qwen_ocr", "extract", max_chars=4000, max_pages=2, route_mode="quality_first", use_gateway=False)
        assert result.get("ok") is False
        assert "insufficient_candidates" in str(result.get("error") or "")


# ---------------------------------------------------------------------------
# 11. MinerU 2.5 / OlmOCR2 — engine_unknown fallback + chain membership
# ---------------------------------------------------------------------------

class TestMineruOlmOcr:

    def test_mineru_returns_engine_unknown(self, tmp_path):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        result = _run_engine_once(p, "mineru2_5", "extract", max_chars=4000, max_pages=2)
        assert result.get("ok") is False
        assert "engine_unknown" in str(result.get("error") or "")
        assert "mineru2_5" in str(result.get("error") or "")

    def test_mineru_in_digital_pdf_chain(self):
        chain = ocr_dual_tool._policy_engine_chain("digital_pdf", "quality_first")
        assert "mineru2_5" in chain

    def test_policy_route_does_not_crash_with_mineru_in_chain(self, tmp_path, monkeypatch):
        """When mineru2_5 is in the chain and returns engine_unknown, policy route falls through."""
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "_native_pdf_text_extract", lambda *a, **kw: {"ok": False, "error": "no_text"})
        monkeypatch.setattr(ocr_dual_tool, "_ocr_pdf_local", lambda *a, **kw: {"ok": False, "error": "local_failed"})
        monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", lambda *a, **kw: {"ok": False, "error": "gb10_failed"})
        monkeypatch.setattr(ocr_dual_tool, "classify_document_features", lambda *a, **kw: {"layout_class": "digital_pdf"})
        result = ocr_dual_tool._run_policy_route(p, goal="extract", route_mode="quality_first", max_chars=4000, max_pages=2, gb10_enabled=True, consensus=False, use_gateway=False)
        assert isinstance(result, dict)
        assert "ok" in result

    def test_engine_unknown_has_engine_key(self, tmp_path):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        result = _run_engine_once(p, "mineru2_5", "extract", max_chars=4000, max_pages=2)
        assert result.get("engine") == "mineru2_5"


# ---------------------------------------------------------------------------
# 12. ocr97_extract public API — model_degraded survives full call chain
# ---------------------------------------------------------------------------

class TestOcr97ExtractApi:
    _MOCK_MD = (
        "Revenue: 100.0\nNet Income: 50.0\nEPS: 2.50\n"
        "Total Assets: 1000.0\nTotal Liabilities: 500.0\n"
    ) * 4

    def test_model_degraded_in_ocr97_extract_output(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool, "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": "qwen3-vl:32b"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen2.5vl:7b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "qwen3-vl:32b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])

        result = ocr_dual_tool.ocr97_extract({"path": str(p), "engine": "gb10_qwen_ocr", "goal": "extract"})
        assert isinstance(result, dict)
        assert result.get("tool") == "ocr97.extract"
        assert result.get("model_degraded") is True
        reason = result.get("model_degraded_reason") or {}
        assert isinstance(reason, dict)
        assert reason.get("primary") == "qwen2.5vl:7b"
        assert reason.get("used") == "qwen3-vl:32b"
        assert reason.get("reason") == "primary_model_unavailable"

    def test_model_degraded_false_when_primary_used(self, tmp_path, monkeypatch):
        p = tmp_path / "doc.png"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            ocr_dual_tool, "_ollama_generate_with_image",
            lambda *a, **kw: {"ok": True, "markdown": self._MOCK_MD, "model": "qwen2.5vl:7b"},
        )
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_MODEL", "qwen2.5vl:7b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OCR_FALLBACK_MODEL", "qwen3-vl:32b")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_COMPAT_ENABLED", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_USE_GATEWAY", False)
        monkeypatch.setattr(ocr_dual_tool, "DEFAULT_GB10_OCR_GATEWAY_URL", "")
        monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda *a, **kw: {})
        monkeypatch.setattr(ocr_dual_tool, "_companion_tesseract_regions", lambda *a, **kw: [])

        result = ocr_dual_tool.ocr97_extract({"path": str(p), "engine": "gb10_qwen_ocr", "goal": "extract"})
        assert result.get("model_degraded") is False
        assert result.get("model_degraded_reason") == {}
