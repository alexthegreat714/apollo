# OCR97 Gap Closure Plan — 2026-05-05

Generated from independent review. Addresses the four gaps documented in
`Apollo/evaluations/README_OCR97_RELEASE_2026-04-21.md` under the 2026-05-05
section. The previous Codex run did not address any of them; this plan is the
direct brief.

---

## Summary of Gaps

| # | Gap | Impact |
|---|-----|--------|
| 1 | Classification is goal-text-only | Specialized lanes (TrOCR, DePlot) do not activate when goal is generic |
| 2 | Checkbox state detection absent | `forms_or_checkboxes` class is detected but checked/unchecked state is never reported |
| 3 | DePlot model validation missing | Model-not-downloaded failure surfaces as generic `deplot_model_load_failed` with no install hint |
| 4 | PaddleOCR-VL not activatable | `PADDLE_OCR_AVAILABLE` is False (package not installed), URL not set, no actionable guidance in health output |

---

## Gap 1 — PIL-Based Image Signal Augmentation

### Problem

`classify_document_features()` at `common/ocr_dual_tool.py:1063` builds its
classification entirely from keyword matching against the goal string, filename
stem, and draft text. If the user submits a generic goal (`"extract text"`,
`"read this document"`), no specialized lane activates regardless of what the
image contains.

### Fix

Add `_image_signals(path: Path) -> Dict[str, Any]` — a pure-PIL function (no
model load) that extracts:

- `aspect_ratio` — width / height. Tall narrow ratio suggests form or letter;
  wide ratio suggests chart or landscape table.
- `whitespace_fraction` — fraction of pixels with luminance > 240. High
  whitespace (> 0.55) combined with regular horizontal bands indicates a form
  or table layout.
- `edge_density` — fraction of pixels that are Sobel edges (approximated via
  PIL `ImageFilter.FIND_EDGES` + threshold). High edge density with low text
  density = chart or figure candidate.
- `horizontal_line_score` — count of rows where > 70% of pixels are near-black,
  normalized. Indicates ruled lines (forms, tables).
- `dark_pixel_fraction` — fraction of near-black pixels. Low value on a
  high-whitespace image = likely blank form.

Function signature:

```python
def _image_signals(path: Path, *, max_dim: int = 512) -> Dict[str, Any]:
    """Return PIL-derived layout signals without loading any ML model.
    Returns empty dict if PIL is unavailable or the file is not renderable."""
```

Rules for converting signals into layout hints (to be merged with text signals
in `classify_document_features`):

| Condition | Hint |
|---|---|
| `edge_density > 0.12` and `whitespace_fraction > 0.50` | `has_chart_signal = True` |
| `horizontal_line_score > 0.08` and `whitespace_fraction > 0.45` | `has_form_signal = True` |
| `whitespace_fraction < 0.30` and `edge_density < 0.06` | `has_handwriting_signal = True` (dense irregular ink) |

Merge strategy in `classify_document_features`:

1. Compute text-based signals as today.
2. Call `_image_signals(path)` if `PIL_AVAILABLE` and path is not empty.
3. If image signals contradict text signals and confidence in text signals is
   low (no strong keyword hits), promote the image signal result.
4. Add `"confidence_reason"` values: `"text_signals"`, `"image_signals"`,
   `"text_and_image_signals"`.

### Files changed

- `common/ocr_dual_tool.py` — add `_image_signals()`, update
  `classify_document_features()` signature and body.

### Tests required

Add to `Apollo/tests/test_ocr_dual_tool.py`:

- `test_image_signals_returns_aspect_ratio_and_whitespace` — create a
  100×200 white PIL image, assert `aspect_ratio == 0.5` and
  `whitespace_fraction >= 0.95`.
- `test_classify_promotes_chart_from_image_signal_on_generic_goal` —
  monkeypatch `_image_signals` to return `{"edge_density": 0.15,
  "whitespace_fraction": 0.60, ...}`, call `classify_document_features(path,
  "extract text")` with no chart keywords, assert `layout_class ==
  "chart_or_figure"` and `confidence_reason == "image_signals"`.
- `test_classify_text_signal_wins_when_strong` — monkeypatch `_image_signals`
  returning low scores but pass goal `"Read this handwritten note"`, assert
  `layout_class == "handwritten"` and `confidence_reason == "text_signals"`.
- `test_image_signals_returns_empty_dict_when_pil_unavailable` — monkeypatch
  `PIL_AVAILABLE = False`, assert `_image_signals(path) == {}`.

### Acceptance criteria

- `test_classify_promotes_chart_from_image_signal_on_generic_goal` passes.
- `confidence_reason` is present in all `classify_document_features` outputs.
- No new model loads — function must use only `PIL.Image`, `PIL.ImageFilter`,
  and basic array math.

---

## Gap 2 — Checkbox State Detection via Qwen Prompt Variant

### Problem

When `classify_document_features` returns `forms_or_checkboxes`, the engine
chain routes to `gb10_qwen_ocr` and sets `unsupported_visual_control_detection:
True` but never attempts to read checkbox states. The caller has no way to know
which boxes are checked vs unchecked.

### Fix

In `_augment_visual_lanes()` at `common/ocr_dual_tool.py:2901`, add a
`visual_controls` augmentation path alongside the existing `handwriting` and
`figures` paths:

When `document_features["has_visual_controls"]` is True:

1. Issue a second Qwen VL call via `_run_gb10_engine` with goal prefixed
   `"checkbox_detection: List each checkbox, radio button, or form control in
   this image. For each, state whether it is checked/filled or unchecked/empty.
   Use format: [checked] <label> or [unchecked] <label>."`.
2. Parse the response with `_parse_checkbox_response(text: str) -> list[Dict]`
   which extracts entries of the form `{"state": "checked"|"unchecked",
   "label": str, "raw": str}`.
3. Merge into the output payload as:
   ```json
   "visual_controls": {
     "ok": true,
     "engine": "gb10_qwen_ocr",
     "items": [{"state": "checked", "label": "Married"}, ...],
     "count_checked": 2,
     "count_unchecked": 3,
     "detection_method": "qwen_prompt_variant",
     "raw": "..."
   }
   ```
4. Set `"unsupported_visual_control_detection": False` in
   `document_features` when `visual_controls.ok` is True (the detection is now
   supported).

Add `_parse_checkbox_response()` to `common/ocr_dual_tool.py`:

```python
def _parse_checkbox_response(text: str) -> list[Dict[str, Any]]:
    """Parse Qwen checkbox_detection output into structured items."""
```

It should match lines of the form `[checked] ...` and `[unchecked] ...`
case-insensitively, strip leading dashes/bullets, and fall back to scanning for
`☑`, `☒`, `✓`, `✗`, `[x]`, `[ ]` patterns.

### Files changed

- `common/ocr_dual_tool.py` — add `_parse_checkbox_response()`, update
  `_augment_visual_lanes()`.

### Tests required

Add to `Apollo/tests/test_ocr_dual_tool.py`:

- `test_parse_checkbox_response_extracts_checked_and_unchecked` — pass literal
  string `"[checked] Married\n[unchecked] Single"`, assert two items with
  correct states.
- `test_parse_checkbox_response_handles_unicode_symbols` — pass `"☑ Accept
  terms\n☒ Decline"`, assert correct extraction.
- `test_visual_controls_augmentation_issues_prompt_variant` — monkeypatch
  `_run_gb10_engine` to capture prompt and return a checkbox response, call
  `_augment_visual_lanes` with `has_visual_controls=True`, assert prompt starts
  with `"checkbox_detection:"`, assert `out["visual_controls"]["ok"]` is True,
  assert `out["document_features"]["unsupported_visual_control_detection"]` is
  False.
- `test_visual_controls_augmentation_skipped_when_qwen_unavailable` —
  monkeypatch `_run_gb10_engine` to return `{"ok": False}`, assert
  `visual_controls` is absent or has `ok: False`, no exception raised.

### Acceptance criteria

- `test_visual_controls_augmentation_issues_prompt_variant` passes.
- `unsupported_visual_control_detection` becomes `False` in output when Qwen
  successfully returns a parsed checkbox list.
- No new model required — reuses `gb10_qwen_ocr` lane.

---

## Gap 3 — DePlot (and TrOCR) Model-Not-Found Error Classification

### Problem

`load_deplot_runtime()` at `common/ocr_local_inference.py:523` catches all
exceptions uniformly as `deplot_model_load_failed:{type(exc).__name__}:{exc}`.
When the model has never been downloaded (most common production failure),
`from_pretrained()` raises `OSError` with a message containing `"does not
appear to have a file named"` or `"No such file or directory"`. This looks
identical to a corrupted weight file or a permissions error. No install hint is
provided.

Same issue exists in `load_trocr_runtime()` at
`common/ocr_local_inference.py:421`.

### Fix

In both `load_deplot_runtime()` and `load_trocr_runtime()`, replace the catch
block with:

```python
except Exception as exc:
    exc_lower = str(exc).lower()
    is_not_found = any(
        term in exc_lower
        for term in (
            "no such file",
            "does not appear to have a file",
            "repository not found",
            "404",
            "entry not found",
            "model not found",
        )
    )
    if is_not_found:
        install_hint = f"huggingface-cli download {model_id}"
        error_code = f"deplot_model_not_found:{model_id}"  # or trocr_model_not_found
    else:
        install_hint = ""
        error_code = f"deplot_model_load_failed:{type(exc).__name__}:{exc}"
    _DEPLOT_RUNTIME["last_error"] = error_code
    _DEPLOT_RUNTIME["backend"] = "load_failed"
    return {
        "ok": False,
        "error": error_code,
        "install_hint": install_hint,
    }
```

The `install_hint` field must propagate through `deplot_extract_chart()` and
`trocr_extract_handwriting()` so it surfaces in the gateway health routes at
`/ocr/chart/health` and `/ocr/handwriting/health`.

In `gb10_ocr_gateway.py`, both health handlers already read `last_error` from
the runtime dict. Add `install_hint` to the health response payload:

```python
"install_hint": str(_DEPLOT_RUNTIME.get("install_hint") or ""),
```

### Files changed

- `common/ocr_local_inference.py` — update `load_deplot_runtime()` catch block
  (line ~523), update `load_trocr_runtime()` catch block (line ~421).
- `common/gb10_ocr_gateway.py` — add `install_hint` to `ocr_chart_health()` and
  `ocr_handwriting_health()` response payloads.

### Tests required

Add to `Apollo/tests/test_ocr_dual_tool.py` or a new
`Apollo/tests/test_ocr_local_inference.py`:

- `test_load_deplot_runtime_returns_not_found_code_on_oserror` — monkeypatch
  `AutoProcessor.from_pretrained` to raise `OSError("does not appear to have a
  file named config.json")`, call `load_deplot_runtime()`, assert
  `result["error"] == "deplot_model_not_found:google/deplot"`, assert
  `result["install_hint"]` starts with `"huggingface-cli download"`.
- `test_load_deplot_runtime_returns_generic_code_on_other_error` — monkeypatch
  to raise `RuntimeError("CUDA out of memory")`, assert `result["error"]`
  starts with `"deplot_model_load_failed"`, assert `result["install_hint"] ==
  ""`.
- Same two tests for `load_trocr_runtime` with `trocr_model_not_found`.

Add to `Apollo/tests/test_ocr_phase2_services.py`:

- `test_chart_health_exposes_install_hint_on_not_found` — monkeypatch
  `_DEPLOT_RUNTIME["last_error"]` to `"deplot_model_not_found:google/deplot"`
  and `_DEPLOT_RUNTIME["install_hint"]` to `"huggingface-cli download
  google/deplot"`, call `/ocr/chart/health`, assert `payload["install_hint"]`
  is non-empty.

### Acceptance criteria

- `test_load_deplot_runtime_returns_not_found_code_on_oserror` passes.
- `/ocr/chart/health` response contains `install_hint` field (empty string when
  no error, populated when model not found).
- No behaviour change when model loads successfully.

---

## Gap 4 — PaddleOCR-VL Actionable Install Path

### Problem

`PADDLE_OCR_AVAILABLE = bool(importlib.util.find_spec("paddleocr"))` at
`common/gb10_ocr_gateway.py:72` evaluates to False because the `paddleocr`
package is not installed. There is no remote URL set in `.env`. The health
output from `_paddle_backend_status()` reports `reason: "engine_not_installed"`
but provides no install guidance.

The lane appears in routing chains (`digital_pdf`, `table_dense`,
`chart_or_figure`) but can never activate. All calls silently fall through to
the next engine.

### Fix — three parts

**Part A: Install hint in `_paddle_backend_status()`**

In `common/gb10_ocr_gateway.py` at line ~541, where `reason =
"engine_not_installed"` is set, add:

```python
install_hint = (
    "pip install paddlepaddle==2.6.1 paddleocr==2.7.3  "
    "# GPU: pip install paddlepaddle-gpu==2.6.1 -f https://www.paddlepaddle.org.cn/whl/windows/mkl/avx/stable.html"
)
```

Return `install_hint` in the status dict. Expose it in the `/ocr/paddle/health`
response and in the `/ocr/capabilities` engine entry for `gb10_paddleocr_vl`.

**Part B: `.env.example` documentation**

Add or update `C:/Users/blyth/Desktop/Engineering/.env.example` (create if
absent) with:

```bash
# PaddleOCR-VL — set to a running PaddleOCR service URL, OR install locally:
#   pip install paddlepaddle==2.6.1 paddleocr==2.7.3
# APOLLO_GB10_PADDLEOCR_VL_URL=http://localhost:8866/predict/ocr_system
APOLLO_GB10_PADDLEOCR_VL_URL=
```

**Part C: Docker Compose sidecar**

Create `C:/Users/blyth/Desktop/Engineering/docker-compose.paddle.yml`:

```yaml
version: "3.9"
services:
  paddleocr-vl:
    image: paddlecloud/paddle:2.6.0-cpu-mkl-avx-ubuntu20.04
    command: >
      bash -c "pip install paddleocr==2.7.3 flask &&
               python -m paddleocr.server --port 8866"
    ports:
      - "8866:8866"
    restart: unless-stopped
```

This gives a one-command path to a running PaddleOCR service:
`docker compose -f docker-compose.paddle.yml up -d`

After which: `APOLLO_GB10_PADDLEOCR_VL_URL=http://localhost:8866/predict/ocr_system`
in `.env` will enable the lane.

### Files changed

- `common/gb10_ocr_gateway.py` — add `install_hint` to `_paddle_backend_status()` return dict and `/ocr/paddle/health` and `/ocr/capabilities` payloads.
- `.env.example` — create or update with PaddleOCR URL comment.
- `docker-compose.paddle.yml` — create.

### Tests required

Add to `Apollo/tests/test_ocr_phase2_services.py`:

- `test_paddle_health_exposes_install_hint_when_engine_not_installed` —
  monkeypatch `PADDLE_OCR_AVAILABLE = False`, call `/ocr/paddle/health`, assert
  `payload["install_hint"]` is a non-empty string containing `"pip install"`.
- `test_capabilities_exposes_paddle_install_hint` — same monkeypatch, call
  `/ocr/capabilities`, find `gb10_paddleocr_vl` engine entry, assert
  `entry["install_hint"]` is non-empty when `ready` is False.

### Acceptance criteria

- `/ocr/paddle/health` includes `install_hint` (populated when not installed,
  empty string when ready).
- `/ocr/capabilities` `gb10_paddleocr_vl` entry includes `install_hint`.
- `docker-compose.paddle.yml` passes `docker compose config` syntax check.

---

## Verification

Run after all four parts are complete:

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m py_compile common\ocr_local_inference.py common\ocr_dual_tool.py common\gb10_ocr_gateway.py
python -m pytest Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_ocr_phase2_services.py -q
```

Expected: all prior tests continue to pass, plus the new tests listed above.

---

## Grade Projection

If all four gaps are closed as specified:

| Corpus | Current | Projected |
|---|---|---|
| Finance / market | 83 | ~88 |
| Wider corpus | 74 | ~83 |

The wider corpus gain is larger because Gap 1 (image-signal classification) is
the primary reason for the finance/wider split. When classification becomes
document-content-driven rather than goal-text-driven, general-purpose extraction
goals activate the same specialized lanes that finance users get today.
