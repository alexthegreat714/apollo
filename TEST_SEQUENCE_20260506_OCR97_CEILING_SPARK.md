# OCR97 Ceiling Spark Resume Sequence - 2026-05-06

## Purpose
This sequence finishes the OCR97 ceiling plan after the missing dated tests were added and the prewarm hardness defect was fixed.

## What Was Fixed Before Spark Resume
- Added `Apollo/tests/test_ocr_improvements_20260505.py`.
- Added `Apollo/tests/test_ocr_ceiling_20260506.py`.
- Updated stale DPI assertions in `Apollo/tests/test_ocr_dual_tool.py` from 3 variants to 4 variants.
- Fixed `common/gb10_ocr_gateway.py` prewarm `NameError` by using `ocr_dual_tool.DEFAULT_GB10_QWEN_OCR_MODEL`.
- Extended checkbox parsing in `common/ocr_dual_tool.py` to accept real Unicode checkbox/checkmark symbols as well as legacy mojibake output.

## Environment
Run from:

```powershell
cd C:\Users\blyth\Desktop\Engineering
```

Optional speed/cleanliness flags:

```powershell
$env:DISABLE_MODEL_SOURCE_CHECK="True"
$env:AEGIS_QWEN_PRECLASSIFY_ENABLE="1"
$env:AEGIS_DEPLOT_ENABLE="1"
```

## Step 1 - Syntax Gate

```powershell
python -m py_compile common\ocr_dual_tool.py common\gb10_ocr_gateway.py Apollo\tests\test_ocr_improvements_20260505.py Apollo\tests\test_ocr_ceiling_20260506.py
```

Expected: exit `0`.

## Step 2 - 2026-05-05 Improvement Coverage

```powershell
python Apollo\tools\run_ocr_pytest_guarded.py -- tests\test_ocr_improvements_20260505.py -q
```

Expected: all tests pass.

## Step 3 - 2026-05-06 Ceiling Coverage

```powershell
python Apollo\tools\run_ocr_pytest_guarded.py -- tests\test_ocr_ceiling_20260506.py -q
```

Expected: all tests pass.

## Step 4 - Original OCR97 Gap Coverage

```powershell
python Apollo\tools\run_ocr_pytest_guarded.py -- tests\test_ocr_gaps_20260505.py -q
```

Expected: all tests pass.

## Step 5 - Core Dual Tool Regression

```powershell
python Apollo\tools\run_ocr_pytest_guarded.py -- tests\test_ocr_dual_tool.py -q
```

Expected: all tests pass, including the updated 400 DPI assertions.

## Step 6 - Documented Full Regression

```powershell
python Apollo\tools\run_ocr_pytest_guarded.py -- tests\test_ocr_dual_tool.py tests\test_ocr_gaps_20260505.py tests\test_ocr_improvements_20260505.py tests\test_ocr_ceiling_20260506.py -q
```

Expected: all pass.

## Notes For Spark
- If prewarm tests fail with slow model-source connectivity, keep `DISABLE_MODEL_SOURCE_CHECK=True` and rerun Step 2.
- If a phase2 service test hangs, keep it out of this ceiling sequence unless the user explicitly asks for the service suite.
- The prior `Apollo/tests/test_ocr_phase2_services.py` run timed out and is not part of the ceiling completion gate.
- Do not pipe these pytest commands through `tail`; the guarded launcher writes full logs to `Apollo/logs/pytest/`, prints a short tail, refuses duplicate OCR runs, and cleans stale OCR pytest trees.
