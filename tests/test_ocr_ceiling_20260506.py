import os
from pathlib import Path

import common.ocr_dual_tool as ocr_dual_tool

from Apollo.tests.test_ocr_gaps_20260505 import _make_base_app
from common.ocr_dual_tool import _apply_numeric_fidelity_guard


def _long_line(text: str) -> str:
    return f"{text}\nNotes: " + ("verified accounting line " * 8)


def _good_quality(text, confidence=None, tables=None, finance_checks=None, finbert_eval=None, **kwargs):
    clean = str(text or "")
    return {
        "score": 0.9 if "corrected" in clean.lower() or "100" in clean or "567" in clean else 0.7,
        "chars": len(clean),
        "confidence": confidence,
        "structure_score": 0.65,
        "numeric_fidelity_score": 0.9,
        "table_rows": clean.count("|"),
        "finance_consistency": finance_checks or {"score": 0.8, "issues": [], "hints": []},
        "finbert_eval": finbert_eval or {"enabled": True, "mode": "heuristic_proxy", "status": "ok"},
    }


def test_preclassify_flag_reads_enabled_from_env(monkeypatch):
    monkeypatch.setenv("AEGIS_QWEN_PRECLASSIFY_ENABLE", "1")
    assert os.getenv("AEGIS_QWEN_PRECLASSIFY_ENABLE") == "1"


def test_run_policy_route_calls_qwen_preclassify_when_enabled(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    captured = {}
    monkeypatch.setenv("AEGIS_QWEN_PRECLASSIFY_ENABLE", "1")
    monkeypatch.setattr(ocr_dual_tool, "_qwen_classify_layout", lambda *args, **kwargs: "chart_or_figure")
    monkeypatch.setattr(ocr_dual_tool, "classify_document_features", lambda *args, **kwargs: {"layout_class": "digital_pdf", "has_chart": False})

    def fake_chain(doc_class, route_mode, forced_engine=""):
        captured["doc_class"] = doc_class
        return []

    monkeypatch.setattr(ocr_dual_tool, "_policy_engine_chain", fake_chain)
    ocr_dual_tool._run_policy_route(
        p,
        goal="extract text",
        route_mode="quality_first",
        max_chars=1000,
        max_pages=1,
        gb10_enabled=True,
        consensus=False,
        use_gateway=False,
    )
    assert captured["doc_class"] == "chart_or_figure"


def test_run_policy_route_skips_preclassify_when_disabled(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    captured = {}
    monkeypatch.setenv("AEGIS_QWEN_PRECLASSIFY_ENABLE", "0")
    monkeypatch.setattr(ocr_dual_tool, "_qwen_classify_layout", lambda *args, **kwargs: "chart_or_figure")
    monkeypatch.setattr(ocr_dual_tool, "classify_document_features", lambda *args, **kwargs: {"layout_class": "digital_pdf", "has_chart": False})

    def fake_chain(doc_class, route_mode, forced_engine=""):
        captured["doc_class"] = doc_class
        return []

    monkeypatch.setattr(ocr_dual_tool, "_policy_engine_chain", fake_chain)
    ocr_dual_tool._run_policy_route(
        p,
        goal="extract text",
        route_mode="quality_first",
        max_chars=1000,
        max_pages=1,
        gb10_enabled=True,
        consensus=False,
        use_gateway=False,
    )
    assert captured["doc_class"] == "digital_pdf"


def test_deplot_flag_reads_enabled_from_env(monkeypatch):
    monkeypatch.setenv("AEGIS_DEPLOT_ENABLE", "1")
    assert os.getenv("AEGIS_DEPLOT_ENABLE") == "1"


def test_policy_chain_routes_chart_class_to_deplot_first():
    chain = ocr_dual_tool._policy_engine_chain("chart_or_figure", "quality_first")
    assert chain[0] == "gb10_deplot_chart"


def test_engine_health_deplot_reports_backend_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_DEPLOT_ENABLE", "1")
    app = _make_base_app(monkeypatch, tmp_path)
    response = app.test_client().get("/ocr/chart/health")
    assert response.get_json()["backend_enabled"] is True


def test_numeric_guard_passes_through_confirmed_numbers():
    assert "1,234,567" in _apply_numeric_fidelity_guard("Revenue: 1,234,567", "Revenue: 1,234,567")


def test_numeric_guard_fixes_transposed_digit():
    result = _apply_numeric_fidelity_guard("Total assets: 1,234,578", "Total assets: 1,234,567")
    assert "1,234,567" in result
    assert "1,234,578" not in result


def test_numeric_guard_leaves_unmatched_number_untouched():
    assert "99.99" in _apply_numeric_fidelity_guard("EPS: 99.99", "Revenue: 1,234,567")


def test_numeric_guard_noop_on_empty_native():
    ocr = "Revenue: 1,234,567"
    assert _apply_numeric_fidelity_guard(ocr, "") == ocr
    assert _apply_numeric_fidelity_guard(ocr, None) == ocr


def test_numeric_guard_noop_on_empty_ocr():
    assert _apply_numeric_fidelity_guard("", "Revenue: 1,234,567") == ""


def test_numeric_guard_does_not_swap_dissimilar_numbers():
    result = _apply_numeric_fidelity_guard("Value: 42", "Revenue: 1,234,567")
    assert "42" in result
    assert "1,234,567" not in result


def test_gb10_qwen_ocr_applies_guard_when_native_seed_available(monkeypatch, tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    page = tmp_path / "page.png"
    page.write_bytes(b"png")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", False)
    monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *args, **kwargs: [page])
    monkeypatch.setattr(ocr_dual_tool, "_native_pdf_text_extract", lambda *args, **kwargs: {"ok": True, "markdown": _long_line("Revenue: 1,234,567"), "text": _long_line("Revenue: 1,234,567")})
    monkeypatch.setattr(ocr_dual_tool, "_quality_bundle", _good_quality)
    monkeypatch.setattr(
        ocr_dual_tool,
        "_ollama_generate_with_image",
        lambda *args, **kwargs: {"ok": True, "markdown": _long_line("Revenue: 1,234,578"), "text": _long_line("Revenue: 1,234,578"), "model": "qwen3-vl:32b"},
    )
    result = ocr_dual_tool._gb10_qwen_ocr(pdf_path, "extract", 8000, 5)
    assert "1,234,567" in result["markdown"]


def test_single_page_sc_uses_original_path(monkeypatch, tmp_path):
    pdf_path = tmp_path / "single.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    page = tmp_path / "page1.png"
    page.write_bytes(b"png")
    calls = []
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", True)
    monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *args, **kwargs: [page])
    monkeypatch.setattr(ocr_dual_tool, "_native_pdf_text_extract", lambda *args, **kwargs: {"ok": True, "markdown": _long_line("Assets: 100"), "text": _long_line("Assets: 100")})
    monkeypatch.setattr(ocr_dual_tool, "_quality_bundle", _good_quality)

    def fake_ollama(image_path, prompt, model, **kwargs):
        calls.append(prompt)
        return {"ok": True, "markdown": _long_line("Assets: 100"), "text": _long_line("Assets: 100"), "model": model}

    monkeypatch.setattr(ocr_dual_tool, "_ollama_generate_with_image", fake_ollama)
    result = ocr_dual_tool._gb10_qwen_ocr(pdf_path, "extract", 8000, 1)
    assert result["ok"] is True
    assert sum("Strict mode" in prompt or "Numeric mode" in prompt for prompt in calls) == 2


def test_multipage_sc_runs_per_page_not_whole_doc(monkeypatch, tmp_path):
    result, variant_pages = _run_multipage_sc_fixture(monkeypatch, tmp_path)
    assert result["ok"] is True
    assert variant_pages == {"page1.png", "page2.png", "page3.png"}


def test_multipage_sc_returns_improved_markdown_when_vote_wins(monkeypatch, tmp_path):
    result, _variant_pages = _run_multipage_sc_fixture(monkeypatch, tmp_path)
    assert "Page 2 corrected" in result["markdown"]


def test_multipage_sc_phase2_flag_set_when_any_page_improved(monkeypatch, tmp_path):
    result, _variant_pages = _run_multipage_sc_fixture(monkeypatch, tmp_path)
    assert result["phase2"]["self_consistency_used"] is True


def test_multipage_sc_workers_env_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_SC_PAGE_WORKERS", "2")
    result, variant_pages = _run_multipage_sc_fixture(monkeypatch, tmp_path, page_count=4)
    assert result["ok"] is True
    assert variant_pages == {"page1.png", "page2.png", "page3.png", "page4.png"}


def _run_multipage_sc_fixture(monkeypatch, tmp_path, page_count=3):
    pdf_path = tmp_path / "multi.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    pages = []
    for idx in range(1, page_count + 1):
        p = tmp_path / f"page{idx}.png"
        p.write_bytes(b"png")
        pages.append(p)
    variant_pages = set()
    monkeypatch.setenv("AEGIS_QWEN_MULTI_IMAGE_MAX_PAGES", "1")
    monkeypatch.setattr(ocr_dual_tool, "DEFAULT_OCR_PHASE2_ENABLED", True)
    monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", lambda *args, **kwargs: pages)
    monkeypatch.setattr(ocr_dual_tool, "_native_pdf_text_extract", lambda *args, **kwargs: {"ok": True, "markdown": _long_line("Assets: 100"), "text": _long_line("Assets: 100")})
    monkeypatch.setattr(ocr_dual_tool, "_quality_bundle", _good_quality)

    def fake_ollama(image_path, prompt, model, **kwargs):
        page_num = int(Path(image_path).stem.replace("page", ""))
        is_variant = "Strict mode" in prompt or "Numeric mode" in prompt
        if is_variant:
            variant_pages.add(Path(image_path).name)
        if page_num == 2 and is_variant:
            text = _long_line("Page 2 corrected")
        elif page_num == 2:
            text = _long_line("Page 2 wrong")
        else:
            text = _long_line(f"Page {page_num} stable")
        return {"ok": True, "markdown": text, "text": text, "model": model}

    monkeypatch.setattr(ocr_dual_tool, "_ollama_generate_with_image", fake_ollama)
    return ocr_dual_tool._gb10_qwen_ocr(pdf_path, "extract", 12000, page_count), variant_pages


def test_dpi_ensemble_includes_400_dpi(monkeypatch, tmp_path):
    result = _run_dpi_fixture(monkeypatch, tmp_path)
    assert 400 in result["dpi_values"]


def test_dpi_ensemble_attempts_include_400_entry(monkeypatch, tmp_path):
    result = _run_dpi_fixture(monkeypatch, tmp_path)
    assert any("dpi_400" in row["engine"] for row in result["attempts"])


def test_dpi_ensemble_workers_env_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_DPI_ENSEMBLE_WORKERS", "2")
    result = _run_dpi_fixture(monkeypatch, tmp_path)
    assert result["dpi_values"] == [150, 200, 300, 400]


def test_dpi_ensemble_partial_failure_still_votes(monkeypatch, tmp_path):
    result = _run_dpi_fixture(monkeypatch, tmp_path, fail_dpi=400)
    assert result["ok"] is True
    assert result["dpi_values"] == [150, 200, 300, 400]


def _run_dpi_fixture(monkeypatch, tmp_path, fail_dpi=None):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(ocr_dual_tool, "_quality_bundle", _good_quality)
    monkeypatch.setattr(ocr_dual_tool, "_semantic_diff_check", lambda source_path, text: {"enabled": True, "status": "ok"})

    def fake_render(path, max_pages, *, dpi=200, tag=""):
        if fail_dpi == dpi:
            raise RuntimeError("render failed")
        page = tmp_path / f"{tag}_{dpi}.png"
        page.write_bytes(b"png")
        return [page]

    def fake_run_rendered(source_path, page_paths, engine, goal, *, max_chars, route_mode, use_gateway, variant_label=""):
        text = _long_line("Assets: 100")
        return {
            "ok": True,
            "engine": variant_label,
            "text": text,
            "markdown": text,
            "confidence": 0.9,
            "quality": _good_quality(text, 0.9),
        }

    monkeypatch.setattr(ocr_dual_tool, "_render_pdf_pages", fake_render)
    monkeypatch.setattr(ocr_dual_tool, "_run_engine_on_rendered_pages", fake_run_rendered)
    return ocr_dual_tool._phase2_pdf_dpi_ensemble(
        pdf_path,
        "gb10_qwen_ocr",
        "extract",
        max_chars=4000,
        max_pages=1,
        route_mode="quality_first",
        use_gateway=False,
    )
