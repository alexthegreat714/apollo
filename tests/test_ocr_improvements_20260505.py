from pathlib import Path

import common.gb10_ocr_gateway as gw
import common.ocr_dual_tool as ocr_dual_tool

from Apollo.tests.test_ocr_gaps_20260505 import _make_base_app
from common.ocr_dual_tool import _augment_visual_lanes, _parse_checkbox_response, _qwen_classify_layout


def test_qwen_classify_layout_returns_table_class(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", lambda *args, **kwargs: {"ok": True, "markdown": "table"})
    assert _qwen_classify_layout(p, "extract text", use_gateway=False, route_mode="quality_first") == "table_dense"


def test_qwen_classify_layout_returns_chart_class(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", lambda *args, **kwargs: {"ok": True, "markdown": "chart"})
    assert _qwen_classify_layout(p, "extract text", use_gateway=False, route_mode="quality_first") == "chart_or_figure"


def test_qwen_classify_layout_returns_form_class(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", lambda *args, **kwargs: {"ok": True, "markdown": "form"})
    assert _qwen_classify_layout(p, "extract text", use_gateway=False, route_mode="quality_first") == "forms_or_checkboxes"


def test_qwen_classify_layout_returns_handwriting_class(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", lambda *args, **kwargs: {"ok": True, "markdown": "handwriting"})
    assert _qwen_classify_layout(p, "extract text", use_gateway=False, route_mode="quality_first") == "handwritten"


def test_qwen_classify_layout_returns_none_on_engine_failure(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", lambda *args, **kwargs: {"ok": False, "error": "engine_not_ready"})
    assert _qwen_classify_layout(p, "extract text", use_gateway=False, route_mode="quality_first") is None


def test_run_policy_route_uses_qwen_class_when_flag_enabled(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    captured = {}
    monkeypatch.setenv("AEGIS_QWEN_PRECLASSIFY_ENABLE", "1")
    monkeypatch.setattr(ocr_dual_tool, "_qwen_classify_layout", lambda *args, **kwargs: "chart_or_figure")
    monkeypatch.setattr(
        ocr_dual_tool,
        "classify_document_features",
        lambda *args, **kwargs: {"layout_class": "digital_pdf", "has_chart": False},
    )

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


def test_run_policy_route_skips_qwen_class_when_flag_disabled(monkeypatch, tmp_path):
    p = tmp_path / "doc.png"
    p.write_bytes(b"png")
    captured = {}
    monkeypatch.setenv("AEGIS_QWEN_PRECLASSIFY_ENABLE", "0")
    monkeypatch.setattr(ocr_dual_tool, "_qwen_classify_layout", lambda *args, **kwargs: "chart_or_figure")
    monkeypatch.setattr(
        ocr_dual_tool,
        "classify_document_features",
        lambda *args, **kwargs: {"layout_class": "digital_pdf", "has_chart": False},
    )

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


def test_prewarm_includes_paddleocr_vl_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "_load_paddle_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_load_got_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_load_finbert_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_load_tableformer_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw.ocr_local_inference, "load_trocr_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw.ocr_local_inference, "load_deplot_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_prewarm_model", lambda *args, **kwargs: {"ok": True, "model": "qwen"})
    app = _make_base_app(monkeypatch, tmp_path)
    payload = app.test_client().post("/ocr/prewarm").get_json()
    assert "gb10_paddleocr_vl" in payload["prewarm"]["engines"]


def test_prewarm_result_exposes_paddle_ok_status(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "_load_paddle_runtime", lambda: {"ok": False, "error": "engine_not_installed"})
    monkeypatch.setattr(gw, "_load_got_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_load_finbert_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_load_tableformer_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw.ocr_local_inference, "load_trocr_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw.ocr_local_inference, "load_deplot_runtime", lambda: {"ok": True})
    monkeypatch.setattr(gw, "_prewarm_model", lambda *args, **kwargs: {"ok": True, "model": "qwen"})
    app = _make_base_app(monkeypatch, tmp_path)
    payload = app.test_client().post("/ocr/prewarm").get_json()
    paddle = payload["prewarm"]["engines"]["gb10_paddleocr_vl"]
    assert paddle["ok"] is False
    assert paddle["error"] == "engine_not_installed"


def test_parse_checkbox_numbered_list_format():
    items = _parse_checkbox_response("1. [x] Married\n2. [ ] Single\n3. [x] Divorced")
    assert items == [
        {"label": "Married", "checked": True},
        {"label": "Single", "checked": False},
        {"label": "Divorced", "checked": True},
    ]


def test_parse_checkbox_checked_prefix_format():
    items = _parse_checkbox_response("Checked: Full-time employment\nUnchecked: Part-time\nChecked: Self-employed")
    assert items[0] == {"label": "Full-time employment", "checked": True}
    assert items[1] == {"label": "Part-time", "checked": False}
    assert items[2] == {"label": "Self-employed", "checked": True}


def test_parse_checkbox_trailing_state_format():
    items = _parse_checkbox_response("Age 18-35 (checked)\nAge 36-50 (unchecked)")
    assert items == [
        {"label": "Age 18-35", "checked": True},
        {"label": "Age 36-50", "checked": False},
    ]


def test_parse_checkbox_unicode_retained():
    items = _parse_checkbox_response("✓ Accept terms\n✗ Decline\n☑ Newsletter\n☐ Promotions")
    assert items == [
        {"label": "Accept terms", "checked": True},
        {"label": "Decline", "checked": False},
        {"label": "Newsletter", "checked": True},
        {"label": "Promotions", "checked": False},
    ]


def test_parse_checkbox_mixed_formats_in_one_response():
    items = _parse_checkbox_response("[x] Agree\nChecked: Employment\n✓ Newsletter\n[ ] Decline")
    assert len(items) == 4
    assert [item["checked"] for item in items] == [True, True, True, False]


def test_parse_checkbox_ignores_unrecognized_lines():
    items = _parse_checkbox_response("This document contains several checkboxes.\n[x] Agree\nSome other text.\n[ ] Disagree")
    assert items == [{"label": "Agree", "checked": True}, {"label": "Disagree", "checked": False}]


def test_visual_controls_output_includes_total_and_coverage(monkeypatch, tmp_path):
    captured = {}
    p = tmp_path / "form.png"
    p.write_bytes(b"png")

    def fake_run_gb10(path, engine, goal, **kwargs):
        captured["goal"] = goal
        return {"ok": True, "markdown": "[checked] A\n[unchecked] B\n[checked] C"}

    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", fake_run_gb10)
    result = _augment_visual_lanes(
        {"ok": True, "markdown": "form"},
        p,
        "process form",
        {"has_visual_controls": True, "unsupported_visual_control_detection": True},
        max_chars=4000,
        max_pages=1,
        route_mode="quality_first",
        use_gateway=False,
    )
    assert result["visual_controls"]["total"] == 3
    assert result["visual_controls"]["coverage"] == 1.0
    assert result["visual_controls"]["count_checked"] == 2
    assert result["visual_controls"]["count_unchecked"] == 1
    assert "[checked]" in captured["goal"]
    assert "[unchecked]" in captured["goal"]


def test_checkbox_prompt_contains_explicit_format_example(monkeypatch, tmp_path):
    captured = {}
    p = tmp_path / "form.png"
    p.write_bytes(b"png")

    def fake_run_gb10(path, engine, goal, **kwargs):
        captured["goal"] = goal
        return {"ok": True, "markdown": "[checked] A\n[unchecked] B"}

    monkeypatch.setattr(ocr_dual_tool, "_run_gb10_engine", fake_run_gb10)
    _augment_visual_lanes(
        {"ok": True, "markdown": "form"},
        p,
        "process form",
        {"has_visual_controls": True, "unsupported_visual_control_detection": True},
        max_chars=4000,
        max_pages=1,
        route_mode="quality_first",
        use_gateway=False,
    )
    assert "[checked]" in captured["goal"]
    assert "[unchecked]" in captured["goal"]
