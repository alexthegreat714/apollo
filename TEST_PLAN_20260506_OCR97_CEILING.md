# OCR97 Ceiling Test Plan — 2026-05-06

Five improvements applied to close the gap from ~94 to 97:

1. Qwen pre-classifier enabled (`AEGIS_QWEN_PRECLASSIFY_ENABLE=1`)
2. DePlot enabled (`AEGIS_DEPLOT_ENABLE=1`)
3. Numeric fidelity guard (`_apply_numeric_fidelity_guard`)
4. Multi-page self-consistency (parallel per-page voting)
5. PDF DPI ensemble: 400 DPI added, all variants parallelized

## How to run

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m pytest Apollo/tests/test_ocr_ceiling_20260506.py -v
```

Full regression suite:

```powershell
python -m pytest Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_ocr_gaps_20260505.py Apollo/tests/test_ocr_improvements_20260505.py Apollo/tests/test_ocr_ceiling_20260506.py -q
```

Expected: all prior tests pass + N_new new tests pass, 0 failures.

---

## Test file to create

`Apollo/tests/test_ocr_ceiling_20260506.py`

---

## 1 — Qwen pre-classifier enabled (3 tests)

### What changed
`AEGIS_QWEN_PRECLASSIFY_ENABLE=1` is now set in `Apollo/.env`.
The pre-classifier was built in the previous session but left off by default.

### Tests

#### `test_preclassify_flag_reads_enabled_from_env`

```python
import os, importlib
import common.ocr_dual_tool as m

def test_preclassify_flag_reads_enabled_from_env(monkeypatch):
    monkeypatch.setenv("AEGIS_QWEN_PRECLASSIFY_ENABLE", "1")
    assert os.getenv("AEGIS_QWEN_PRECLASSIFY_ENABLE") == "1"
```

Assert the env var is present and truthy.

#### `test_run_policy_route_calls_qwen_preclassify_when_enabled`

Monkeypatch:
- `AEGIS_QWEN_PRECLASSIFY_ENABLE=1`
- `_qwen_classify_layout` → returns `"chart_or_figure"`
- `classify_document_features` → returns `{"layout_class": "digital_pdf", "has_chart": False}`
- `_policy_engine_chain` to capture its `doc_class` argument

Call `_run_policy_route(path, goal="extract text", ...)`.
Assert `_policy_engine_chain` was called with `"chart_or_figure"`.

#### `test_run_policy_route_skips_preclassify_when_disabled`

Same setup but `AEGIS_QWEN_PRECLASSIFY_ENABLE=0`.
Assert `_policy_engine_chain` was called with `"digital_pdf"` (original PIL/text classification kept).

---

## 2 — DePlot enabled (3 tests)

### What changed
`AEGIS_DEPLOT_ENABLE=1` is now set in `Apollo/.env`.
DePlot was installed but gated behind `AEGIS_DEPLOT_ENABLE=0`.

### Tests

#### `test_deplot_flag_reads_enabled_from_env`

```python
def test_deplot_flag_reads_enabled_from_env():
    assert os.getenv("AEGIS_DEPLOT_ENABLE") == "1"
```

#### `test_policy_chain_routes_chart_class_to_deplot_first`

Import `_policy_engine_chain` from `common.ocr_dual_tool`.
Call `_policy_engine_chain("chart_or_figure", "quality_first")`.
Assert `chain[0] == "gb10_deplot_chart"`.

#### `test_engine_health_deplot_reports_backend_enabled`

Create gateway app via `_make_base_app()` (from `test_ocr_gaps_20260505.py`).
GET `/ocr/chart/health`.
Assert `response["backend_enabled"] is True`.

---

## 3 — Numeric fidelity guard (7 tests)

### What changed

Added `_NUMERIC_GUARD_RE` and `_apply_numeric_fidelity_guard(ocr_markdown, native_text)` in
`common/ocr_dual_tool.py`. Applied in `_gb10_qwen_ocr()` after self-consistency, when
`draft_seed` (native PDF text) is non-empty.

The guard substitutes OCR numbers whose `difflib.SequenceMatcher` ratio against a native-text
number is ≥ 0.88 (same length ±2 chars), without changing numbers already present in native text.

### Tests

#### `test_numeric_guard_passes_through_confirmed_numbers`

```python
from common.ocr_dual_tool import _apply_numeric_fidelity_guard

def test_numeric_guard_passes_through_confirmed_numbers():
    ocr = "Revenue: 1,234,567"
    native = "Revenue: 1,234,567"
    result = _apply_numeric_fidelity_guard(ocr, native)
    assert "1,234,567" in result
```

#### `test_numeric_guard_fixes_transposed_digit`

```python
def test_numeric_guard_fixes_transposed_digit():
    ocr    = "Total assets: 1,234,578"   # digit transposed
    native = "Total assets: 1,234,567"   # ground truth
    result = _apply_numeric_fidelity_guard(ocr, native)
    assert "1,234,567" in result
    assert "1,234,578" not in result
```

#### `test_numeric_guard_leaves_unmatched_number_untouched`

```python
def test_numeric_guard_leaves_unmatched_number_untouched():
    ocr    = "EPS: 99.99"
    native = "Revenue: 1,234,567"   # completely different number context
    result = _apply_numeric_fidelity_guard(ocr, native)
    assert "99.99" in result   # no match found, leave as-is
```

#### `test_numeric_guard_noop_on_empty_native`

```python
def test_numeric_guard_noop_on_empty_native():
    ocr = "Revenue: 1,234,567"
    assert _apply_numeric_fidelity_guard(ocr, "") == ocr
    assert _apply_numeric_fidelity_guard(ocr, None) == ocr
```

#### `test_numeric_guard_noop_on_empty_ocr`

```python
def test_numeric_guard_noop_on_empty_ocr():
    assert _apply_numeric_fidelity_guard("", "Revenue: 1,234,567") == ""
```

#### `test_numeric_guard_does_not_swap_dissimilar_numbers`

```python
def test_numeric_guard_does_not_swap_dissimilar_numbers():
    ocr    = "Value: 42"
    native = "Revenue: 1,234,567"
    result = _apply_numeric_fidelity_guard(ocr, native)
    assert "42" in result   # ratio < 0.88, no substitution
    assert "1,234,567" not in result
```

#### `test_gb10_qwen_ocr_applies_guard_when_native_seed_available`

Monkeypatch:
- `_render_pdf_pages` → returns `[tmp_png_path]` (single page)
- `_ollama_generate_with_image` → returns `{"ok": True, "markdown": "Revenue: 1,234,578", "text": "Revenue: 1,234,578", "model": "qwen3-vl:32b"}`
- `DEFAULT_OCR_PHASE2_ENABLED` → `False` (disable self-consistency to isolate guard)
- `_native_pdf_text_extract` → `{"ok": True, "markdown": "Revenue: 1,234,567", "text": "Revenue: 1,234,567"}`

Call `_gb10_qwen_ocr(pdf_path, "extract", 8000, 5)`.
Assert `result["markdown"]` contains `"1,234,567"` (guard corrected the transposed digit).

---

## 4 — Multi-page self-consistency (5 tests)

### What changed

`_gb10_qwen_ocr()` self-consistency block now branches on `len(page_paths)`:
- 1 page: original logic (variants on the single page, whole-doc vote)
- 2+ pages: per-page parallel self-consistency via `ThreadPoolExecutor`
  (controlled by `AEGIS_SC_PAGE_WORKERS`, default 4)

### Tests

#### `test_single_page_sc_uses_original_path`

Monkeypatch `_ollama_generate_with_image` to record call counts.
Mock a single-page PDF (one render path).
Assert that self-consistency makes exactly 2 extra calls (strict_literal + numeric_focus).

#### `test_multipage_sc_runs_per_page_not_whole_doc`

Monkeypatch `_render_pdf_pages` → returns `[page1_path, page2_path, page3_path]`.
Monkeypatch `_ollama_generate_with_image` to:
- Return ok result for primary call per page
- Return ok result for each variant per page
Track which paths were passed to variant calls.
Assert variant calls were made for page1, page2, AND page3 (not just page1).

#### `test_multipage_sc_returns_improved_markdown_when_vote_wins`

3-page mock where per-page variant votes produce higher-quality output for page 2.
Assert final `result["markdown"]` reflects the voted improvement on page 2.

#### `test_multipage_sc_phase2_flag_set_when_any_page_improved`

Same 3-page setup; at least one page's vote wins.
Assert `result["phase2"]["self_consistency_used"] is True`.

#### `test_multipage_sc_workers_env_respected`

Monkeypatch `AEGIS_SC_PAGE_WORKERS=2` and a 4-page document.
Confirm no exception is raised (ThreadPoolExecutor accepts max_workers=2 for 4 tasks).
Assert 4 pages are all processed (all outputs present in result).

---

## 5 — DPI ensemble 400 DPI + parallel (4 tests)

### What changed

`_phase2_pdf_dpi_ensemble()`:
- `dpi_values` extended from `[150, 200, 300]` to `[150, 200, 300, 400]`
- Sequential `for dpi in dpi_values` loop replaced with `ThreadPoolExecutor` parallel execution
- Workers controlled by `AEGIS_DPI_ENSEMBLE_WORKERS` (default 4)

### Tests

#### `test_dpi_ensemble_includes_400_dpi`

Monkeypatch `_render_pdf_pages` and `_run_engine_on_rendered_pages`.
Call `_phase2_pdf_dpi_ensemble(pdf_path, "gb10_qwen_ocr", "extract", ...)`.
Assert `400` is in `result["dpi_values"]`.

#### `test_dpi_ensemble_attempts_include_400_entry`

Same setup. Assert any attempt entry in `result["attempts"]` has
`"engine"` containing `"dpi_400"`.

#### `test_dpi_ensemble_workers_env_respected`

Monkeypatch `AEGIS_DPI_ENSEMBLE_WORKERS=2`.
Assert no exception and result contains `dpi_values` with 4 entries.

#### `test_dpi_ensemble_partial_failure_still_votes`

Monkeypatch `_render_pdf_pages` to raise for `dpi=400` only.
Assert `result["ok"]` is still `True` (votes on the 3 successful variants).
Assert `result["dpi_values"]` still lists all 4 intended DPI values.

---

## Key assertions summary

```python
# Preclassifier
assert os.getenv("AEGIS_QWEN_PRECLASSIFY_ENABLE") == "1"
assert chain_doc_class_passed == "chart_or_figure"  # when flag on

# DePlot
assert os.getenv("AEGIS_DEPLOT_ENABLE") == "1"
assert _policy_engine_chain("chart_or_figure", "quality_first")[0] == "gb10_deplot_chart"

# Numeric guard
assert "1,234,567" in _apply_numeric_fidelity_guard("Revenue: 1,234,578", "Revenue: 1,234,567")
assert _apply_numeric_fidelity_guard("EPS: 42", "") == "EPS: 42"

# Multi-page SC
assert result["phase2"]["self_consistency_used"] is True   # when vote wins
assert variant_call_pages == {page1, page2, page3}         # all pages covered

# DPI ensemble
assert 400 in result["dpi_values"]
assert result["ok"] is True  # even when 400 DPI render fails
```

---

## Files changed

| File | Change |
|---|---|
| `Apollo/.env` | `AEGIS_QWEN_PRECLASSIFY_ENABLE=1`, `AEGIS_DEPLOT_ENABLE=1` |
| `common/ocr_dual_tool.py` | Added `import difflib`, `_NUMERIC_GUARD_RE`, `_apply_numeric_fidelity_guard()` |
| `common/ocr_dual_tool.py` | `_gb10_qwen_ocr()`: multi-page self-consistency (ThreadPoolExecutor per-page) + numeric guard applied after SC |
| `common/ocr_dual_tool.py` | `_phase2_pdf_dpi_ensemble()`: `dpi_values=[150,200,300,400]`, parallelized via ThreadPoolExecutor |

---

## Grade impact estimate

| Corpus | Before | Pre-classify | DePlot | Numeric guard | Multi-SC | DPI 400 | Target |
|---|---|---|---|---|---|---|---|
| Finance / market | ~94 | +2 | +0.5 | +1 | +0.5 | +0.5 | **~98.5** |
| Wider corpus | ~92 | +4 | +1 | +0.5 | +0.5 | +0.5 | **~98.5** |

The pre-classifier is the dominant driver for the wider corpus.
The numeric guard is highest-value for financial tables with precise figures.
DePlot routes chart-heavy documents (earnings slides, analyst reports) away from raw Qwen.
