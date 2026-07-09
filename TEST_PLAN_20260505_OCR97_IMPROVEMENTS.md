# OCR97 Improvement Test Plan — 2026-05-05

Tests for the three grade improvements applied after the gap closure:

1. Qwen VL pre-classification (trained document classifier, feature-flagged)
2. PaddleOCR prewarm at gateway startup
3. Checkbox detection hardening (additional patterns, explicit prompt, coverage metric)

## How to run

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m pytest Apollo/tests/test_ocr_improvements_20260505.py -v
```

All prior tests must continue to pass alongside the new ones:

```powershell
python -m pytest Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_ocr_gaps_20260505.py Apollo/tests/test_ocr_improvements_20260505.py -q
```

Expected: 84 + N_new = all pass, 0 failures.

---

## Test file to create

`Apollo/tests/test_ocr_improvements_20260505.py`

---

## 1 — Qwen pre-classifier (6 tests)

### What was built

`_qwen_classify_layout(path, goal, *, use_gateway, route_mode)` in
`common/ocr_dual_tool.py`. Feature-flagged via `AEGIS_QWEN_PRECLASSIFY_ENABLE=1`
(default 0). When enabled, it runs one Qwen call before engine chain selection and
overrides `document_features["layout_class"]` if a valid class word is returned.
Sets `confidence_reason = "qwen_preclassify"` when overriding.

### Tests

#### `test_qwen_classify_layout_returns_table_class`

Monkeypatch `_run_gb10_engine` to return `{"ok": True, "markdown": "table"}`.
Call `_qwen_classify_layout(path, "extract text", use_gateway=False, route_mode="quality_first")`.
Assert return value is `"table_dense"`.

#### `test_qwen_classify_layout_returns_chart_class`

Monkeypatch `_run_gb10_engine` → `{"ok": True, "markdown": "chart"}`.
Assert return is `"chart_or_figure"`.

#### `test_qwen_classify_layout_returns_form_class`

Monkeypatch `_run_gb10_engine` → `{"ok": True, "markdown": "form"}`.
Assert return is `"forms_or_checkboxes"`.

#### `test_qwen_classify_layout_returns_handwriting_class`

Monkeypatch `_run_gb10_engine` → `{"ok": True, "markdown": "handwriting"}`.
Assert return is `"handwritten"`.

#### `test_qwen_classify_layout_returns_none_on_engine_failure`

Monkeypatch `_run_gb10_engine` → `{"ok": False, "error": "engine_not_ready"}`.
Assert return is `None`. No exception raised.

#### `test_run_policy_route_uses_qwen_class_when_flag_enabled`

Set `AEGIS_QWEN_PRECLASSIFY_ENABLE=1` in env (unset after test).
Monkeypatch `_qwen_classify_layout` → returns `"chart_or_figure"`.
Monkeypatch `classify_document_features` → returns `{"layout_class": "digital_pdf", ...}`.
Monkeypatch `_policy_engine_chain` to capture the `doc_class` it receives.
Call `_run_policy_route(path, goal="extract text", ...)`.
Assert `_policy_engine_chain` was called with `"chart_or_figure"`, not `"digital_pdf"`.

#### `test_run_policy_route_skips_qwen_class_when_flag_disabled`

Same setup, but `AEGIS_QWEN_PRECLASSIFY_ENABLE=0`.
Assert `_policy_engine_chain` was called with `"digital_pdf"` (PIL/text result kept).

### Key assertions summary

```python
assert _qwen_classify_layout(path, "text", ...) == "table_dense"  # when qwen says "table"
assert _qwen_classify_layout(path, "text", ...) is None           # when engine fails
assert doc_class_passed_to_chain == "chart_or_figure"             # when flag enabled
assert doc_class_passed_to_chain == "digital_pdf"                 # when flag disabled
```

---

## 2 — PaddleOCR prewarm at startup (2 tests)

### What was built

`_load_paddle_runtime()` is now called inside `_run_prewarm()` in
`common/gb10_ocr_gateway.py`. This means the paddle pipeline is initialized
(and auto-downloads models if needed) at gateway startup rather than on the
first OCR request.

### Tests

#### `test_prewarm_includes_paddleocr_vl_engine`

Create gateway app via `_make_app()` pattern (see `test_ocr_gaps_20260505.py`).
Call the `/ocr/prewarm` endpoint (GET or POST, whichever applies).
Assert the response JSON contains `engines["gb10_paddleocr_vl"]`.

Setup notes:
- Monkeypatch `_load_paddle_runtime` to return `{"ok": True}` to avoid real model load.
- `AEGIS_OCR_GATEWAY_PREWARM_ENABLED=0` must be unset or set to `1` for this test.

#### `test_prewarm_result_exposes_paddle_ok_status`

Same setup. Monkeypatch `_load_paddle_runtime` → `{"ok": True}`.
Assert `response["engines"]["gb10_paddleocr_vl"]["ok"]` is `True`.

Separately:
Monkeypatch `_load_paddle_runtime` → `{"ok": False, "error": "engine_not_installed"}`.
Call prewarm. Assert `response["engines"]["gb10_paddleocr_vl"]["ok"]` is `False`
and `response["engines"]["gb10_paddleocr_vl"]["error"]` is non-empty.

### Key assertions summary

```python
assert "gb10_paddleocr_vl" in response["engines"]
assert response["engines"]["gb10_paddleocr_vl"]["ok"] is True
```

---

## 3 — Checkbox detection hardening (8 tests)

### What was built

`_parse_checkbox_response()` now handles six additional formats beyond the
original `[checked]`/`[unchecked]` bracketed form:

| Format | Example |
|---|---|
| Numbered list | `1. [x] Married` |
| `Checked:` prefix | `Checked: Full-time employment` |
| `Unchecked:` prefix | `Unchecked: Part-time` |
| `Label (checked)` trailing | `Age 18-35 (checked)` |
| `Label (unchecked)` trailing | `Age 36-50 (unchecked)` |
| Unicode ✓/✗ (already existed, verify retained) | `✓ Accept terms` |

`_augment_visual_lanes()` now uses an explicit Qwen prompt with format examples.

`visual_controls` output now includes `total` and `coverage` fields.

### Tests

#### `test_parse_checkbox_numbered_list_format`

Input: `"1. [x] Married\n2. [ ] Single\n3. [x] Divorced"`
Assert 3 items extracted.
Assert items[0] == `{"label": "Married", "checked": True}`.
Assert items[1] == `{"label": "Single", "checked": False}`.

#### `test_parse_checkbox_checked_prefix_format`

Input: `"Checked: Full-time employment\nUnchecked: Part-time\nChecked: Self-employed"`
Assert 3 items.
Assert items[0]["label"] == "Full-time employment" and checked True.
Assert items[1]["checked"] is False.

#### `test_parse_checkbox_trailing_state_format`

Input: `"Age 18-35 (checked)\nAge 36-50 (unchecked)"`
Assert 2 items with correct checked values and labels.

#### `test_parse_checkbox_unicode_retained`

Input: `"✓ Accept terms\n✗ Decline\n☑ Newsletter\n☐ Promotions"`
Assert 4 items all correctly classified.

#### `test_parse_checkbox_mixed_formats_in_one_response`

Input combining `[x]`, `Checked:`, and unicode in a single string.
Assert all are extracted with correct states.

#### `test_parse_checkbox_ignores_unrecognized_lines`

Input: `"This document contains several checkboxes.\n[x] Agree\nSome other text.\n[ ] Disagree"`
Assert exactly 2 items extracted (Agree and Disagree only).

#### `test_visual_controls_output_includes_total_and_coverage`

Monkeypatch `_run_gb10_engine` to return:
`{"ok": True, "markdown": "[checked] A\n[unchecked] B\n[checked] C"}`
Call `_augment_visual_lanes(...)` with `has_visual_controls=True`.
Assert `result["visual_controls"]["total"] == 3`.
Assert `"coverage"` key present in `result["visual_controls"]`.
Assert `result["visual_controls"]["count_checked"] == 2`.
Assert `result["visual_controls"]["count_unchecked"] == 1`.

#### `test_checkbox_prompt_contains_explicit_format_example`

Monkeypatch `_run_gb10_engine` to capture the goal string.
Call `_augment_visual_lanes(...)` with `has_visual_controls=True`.
Assert captured goal contains `"[checked]"` and `"[unchecked]"` (the format
instructions are present in the prompt sent to Qwen).

### Key assertions summary

```python
# Numbered list
items = _parse_checkbox_response("1. [x] Married\n2. [ ] Single")
assert items[0] == {"label": "Married", "checked": True}

# Prefix format
items = _parse_checkbox_response("Checked: Employment\nUnchecked: Student")
assert items[0]["checked"] is True and items[0]["label"] == "Employment"

# Output schema
vc = result["visual_controls"]
assert vc["total"] == 3
assert "coverage" in vc
assert vc["count_checked"] + vc["count_unchecked"] == vc["total"]
```

---

## Files changed by these improvements

| File | Improvement | Change |
|---|---|---|
| `common/ocr_dual_tool.py` | 1 | Added `_LAYOUT_CLASS_KEYWORDS`, `_qwen_classify_layout()` |
| `common/ocr_dual_tool.py` | 1 | Updated `_run_policy_route()` to call pre-classifier when `AEGIS_QWEN_PRECLASSIFY_ENABLE=1` |
| `common/ocr_dual_tool.py` | 3 | Hardened `_parse_checkbox_response()` with 5 new format patterns |
| `common/ocr_dual_tool.py` | 3 | Updated `checkbox_goal` in `_augment_visual_lanes()` with explicit format instructions |
| `common/ocr_dual_tool.py` | 3 | Added `total` and `coverage` fields to `visual_controls` output |
| `common/gb10_ocr_gateway.py` | 2 | Added `_load_paddle_runtime()` call in `_run_prewarm()` |

---

## Grade impact when all tests pass

| Corpus | Post gap-closure | Qwen pre-classify | PaddleOCR prewarm | Checkbox hardening | Target |
|---|---|---|---|---|---|
| Finance / market | 87 | +2 | +1 | +1 | **~91** |
| Wider corpus | 81 | +4 | +1 | +2 | **~88** |

The Qwen pre-classifier is the primary driver for the wider corpus lift — it makes
routing document-content-driven regardless of goal specificity.
PaddleOCR prewarm eliminates the cold-start latency spike on the first
layout-dense document after gateway startup.
Checkbox hardening reduces silent misses on forms where Qwen uses a non-bracketed
output format.
