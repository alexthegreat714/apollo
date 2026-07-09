# OCR97 Gap Closure — Test Plan (2026-05-05)

Tests for the four gaps documented in `Apollo/OCR97_GAPS_PLAN_20260505.md`.

## How to run

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m pytest Apollo/tests/test_ocr_gaps_20260505.py -v
```

All 19 tests should pass. To run alongside the prior suites:

```powershell
python -m pytest Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_ocr_phase2_services.py Apollo/tests/test_ocr_gaps_20260505.py -q
```

Expected: 65 + 3 + 19 = 87 tests pass, 0 failures.

---

## Test file

`Apollo/tests/test_ocr_gaps_20260505.py`

---

## Gap 1 — PIL image signal augmentation (4 tests)

| Test | What it verifies |
|---|---|
| `test_image_signals_returns_aspect_ratio_and_whitespace` | `_image_signals` on a 100×200 white PNG returns `aspect_ratio=0.5`, `whitespace_fraction≥0.95` |
| `test_image_signals_returns_empty_dict_when_pil_unavailable` | `_image_signals` returns `{}` when `PIL_AVAILABLE=False` |
| `test_classify_promotes_chart_from_image_signal_on_generic_goal` | `classify_document_features` returns `chart_or_figure` + `confidence_reason="image_signals"` when image signals indicate chart and goal is generic |
| `test_classify_text_signal_wins_when_strong` | Text keyword hits (2+) override image signals → `layout_class="handwritten"`, `confidence_reason="text_signals"` |
| `test_classify_confidence_reason_present_on_all_outputs` | `confidence_reason` and `image_signals` keys are always present in output dict |

### Key assertions

- `result["layout_class"] == "chart_or_figure"` when `edge_density > 0.12` and `whitespace_fraction > 0.50` and no chart keywords in goal
- `result["confidence_reason"] in ("text_signals", "image_signals", "text_and_image_signals")`
- No model load in `_image_signals` — only `PIL.Image`, `PIL.ImageFilter`

---

## Gap 2 — Checkbox detection (4 tests)

| Test | What it verifies |
|---|---|
| `test_parse_checkbox_response_extracts_checked_and_unchecked` | `[checked] Married\n[unchecked] Single` → two items with correct `checked` bools |
| `test_parse_checkbox_response_handles_x_bracket` | `[x]` → checked, `[ ]` → unchecked |
| `test_parse_checkbox_response_handles_unicode_symbols` | `☑` → checked, `☐` → unchecked, labels extracted |
| `test_parse_checkbox_response_returns_empty_on_no_match` | Plain text with no checkbox patterns → `[]` |
| `test_visual_controls_augmentation_issues_prompt_variant` | `_augment_visual_lanes` with `has_visual_controls=True` issues a `"checkbox_detection:"` goal to Qwen, result has `visual_controls.ok=True`, `unsupported_visual_control_detection=False` in both top-level and `document_features` |
| `test_visual_controls_augmentation_skipped_when_qwen_unavailable` | Qwen returns `ok=False` → no exception, `unsupported_visual_control_detection=True` |

### Key assertions

```python
vc = result["visual_controls"]
assert vc["ok"] is True
assert vc["count_checked"] == 1
assert vc["count_unchecked"] == 1
assert vc["detection_method"] == "qwen_prompt_variant"
assert result["document_features"]["unsupported_visual_control_detection"] is False
```

---

## Gap 3 — Model-not-found error classification (4 tests)

| Test | What it verifies |
|---|---|
| `test_load_trocr_runtime_returns_not_found_code_on_oserror` | `pipeline()` raises `OSError("does not appear to have a file named...")` → `error="trocr_model_not_found:{model_id}"`, `install_hint` starts with `"huggingface-cli download"` |
| `test_load_trocr_runtime_returns_generic_code_on_other_error` | `pipeline()` raises `RuntimeError("CUDA out of memory")` → `error` starts with `"trocr_model_load_failed"`, `install_hint=""` |
| `test_load_deplot_runtime_returns_not_found_code_on_oserror` | `AutoProcessor.from_pretrained()` raises OSError → `error="deplot_model_not_found:{model_id}"`, `install_hint` populated |
| `test_load_deplot_runtime_returns_generic_code_on_other_error` | Generic RuntimeError → `error` starts with `"deplot_model_load_failed"`, `install_hint=""` |
| `test_chart_health_exposes_install_hint_on_not_found` | `/ocr/chart/health` response contains `install_hint` field populated from `_DEPLOT_RUNTIME["install_hint"]` |
| `test_handwriting_health_exposes_install_hint_on_not_found` | `/ocr/handwriting/health` response contains `install_hint` field from `_TROCR_RUNTIME["install_hint"]` |

### Monkeypatch setup for runtime tests

```python
monkeypatch.setattr(li, "TRANSFORMERS_AVAILABLE", True)
monkeypatch.setattr(li, "PIL_AVAILABLE", True)
monkeypatch.setattr(li, "_trocr_enabled", lambda: True)
monkeypatch.setattr(li, "_trocr_device", lambda: "cpu")          # avoid torch.cuda call
monkeypatch.setattr(li, "_ensure_transformers_loaded", lambda: {"ok": True})
monkeypatch.setattr(li, "_transformers_device_index", lambda name: -1)
monkeypatch.setattr(li, "pipeline", raise_not_found)
```

The `_trocr_device` and `_transformers_device_index` patches are required because `torch` is `None` in test context — without them, the device resolution raises `AttributeError` before `pipeline` is called, masking the intended test case.

---

## Gap 4 — PaddleOCR install hint (2 tests)

| Test | What it verifies |
|---|---|
| `test_paddle_health_exposes_install_hint_when_engine_not_installed` | `/ocr/paddle/health` returns `install_hint` containing `"pip install"` when `PADDLE_OCR_AVAILABLE=False` and no override flag set |
| `test_capabilities_exposes_paddle_install_hint` | `/ocr/capabilities` `engines` array contains `gb10_paddleocr_vl` entry with `install_hint` containing `"pip install"` |

### Key assertions

```python
# /ocr/paddle/health
assert "install_hint" in payload
assert "pip install" in payload["install_hint"]

# /ocr/capabilities
engines = {e["name"]: e for e in payload["engines"]}
paddle_entry = engines["gb10_paddleocr_vl"]
assert "pip install" in paddle_entry["install_hint"]
```

### Setup notes

These tests unset `AEGIS_OCR_ENGINE_PADDLEOCR_VL_READY` and `APOLLO_GB10_PADDLEOCR_VL_URL` so `_paddle_backend_status()` computes naturally with `PADDLE_OCR_AVAILABLE=False`, producing `reason="engine_not_installed"` and thus a populated `install_hint`.

---

## Files changed in this gap closure

| File | Gap | Change |
|---|---|---|
| `common/ocr_dual_tool.py` | 1 | `_image_signals()` function; updated `classify_document_features()` |
| `common/ocr_dual_tool.py` | 2 | `_parse_checkbox_response()`; updated `_augment_visual_lanes()` |
| `common/ocr_local_inference.py` | 3 | Updated catch blocks in `load_trocr_runtime()` and `load_deplot_runtime()` |
| `common/gb10_ocr_gateway.py` | 3 | Added `install_hint` to `/ocr/handwriting/health` and `/ocr/chart/health` |
| `common/gb10_ocr_gateway.py` | 4 | Added `install_hint` to `_paddle_backend_status()`, `/ocr/paddle/health`, and `_engine_snapshot()` |
| `.env.example` | 4 | Created with PaddleOCR URL comment and feature flags |
| `docker-compose.paddle.yml` | 4 | Created PaddleOCR sidecar compose file |
| `Apollo/tests/test_ocr_gaps_20260505.py` | all | 19 new tests covering all four gaps |
