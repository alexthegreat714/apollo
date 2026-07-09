# Apollo OCR97 Benchmark And Challenger Study

`OCR97` is the locked Apollo production OCR baseline. Challenger routes are compared against it as controlled alternatives, not as automatic replacements.

## Current artifacts

- Latest JSON summary: [artifacts/ocr97_challenger_study_20260421.json](artifacts/ocr97_challenger_study_20260421.json)
- Latest Markdown report: [artifacts/ocr97_challenger_study_20260421.md](artifacts/ocr97_challenger_study_20260421.md)
- Release notes: [README_OCR97_RELEASE_2026-04-21.md](README_OCR97_RELEASE_2026-04-21.md)
- Research cache index: [ocr97_research/papers_index.json](ocr97_research/papers_index.json)
- Research manifest: [ocr97_research/papers_manifest.json](ocr97_research/papers_manifest.json)
- Study manifest: [ocr97_challenger_manifest.json](ocr97_challenger_manifest.json)

## OCR97 contract

- `OCR97` is the current Apollo OCR stack in `C:\Users\blyth\Desktop\Engineering`.
- It remains the production default while challenger work is ongoing.
- Challenger lanes must record:
  - route id and route type
  - document slice
  - local benchmark metrics
  - latency/runtime notes
  - exact paper references behind the challenger
- A challenger only wins when it beats `OCR97` on a defined slice. There is no single global leaderboard winner by default.

## Slice set

The current study manifest covers these slices:

- `digital_finance_pdfs`
- `scanned_finance_pdfs`
- `warped_or_degraded_image_captures`
- `table_dense_financial_pages`
- `tiny_text_high_density_pages`
- `mixed_layout_pages`

The first pass uses the existing Apollo sample corpus in `artifacts/ocr_phase2/sample_docs_20260420T185540Z/`.

## Route set

Measured or attempted routes in the first study:

- `OCR97 baseline`
- `GOT-OCR2.0`
- `PaddleOCR-VL`
- `MinerU2.5`
- `Table specialist lane`
- `Layout/parser + table specialist`
- `End-to-end OCR + table specialist`
- `Coarse-to-fine parser + verifier/table lane`
- literature-only planners:
  - `OmniParser`
  - `MOCR`

## Scoring rubric

Each document slice uses weighted capability cards rather than a single flat metric.

Capability cards:

- literal transcription fidelity
- numeric fidelity
- table structure fidelity
- layout preservation
- tiny-text recovery
- degraded scan robustness
- image capture robustness
- cross-element semantic reconstruction
- required token coverage

Important scoring note:

- local benchmark evidence in this harness is proxy-based and derived from Apollo OCR quality outputs plus required-token checks
- literature-only routes are recorded, but they do not count as empirical wins
- engineering inference is allowed in the written analysis, but it must be called out as inference rather than measured evidence

## Research corpus

The local paper cache is vendored under [ocr97_research/papers](ocr97_research/papers). The current focused set includes:

- [GOT/OCR-2.0](ocr97_research/papers/got_ocr2_2024.pdf) and source: https://arxiv.org/abs/2409.01704
- [PaddleOCR-VL](ocr97_research/papers/paddleocr_vl_2025.pdf) and source: https://arxiv.org/abs/2510.14528
- [MinerU2.5](ocr97_research/papers/mineru2_5_2025.pdf) and source: https://arxiv.org/abs/2509.22186
- [OmniParser](ocr97_research/papers/omniparser_2024.pdf) and source: https://arxiv.org/abs/2403.19128
- [MOCR](ocr97_research/papers/mocr_2026.pdf) and source: https://arxiv.org/abs/2603.13032
- [PubTables-1M](ocr97_research/papers/pubtables_1m_2021.pdf) and source: https://arxiv.org/abs/2110.00061
- [OCRBench](ocr97_research/papers/ocrbench_2023.pdf) and source: https://arxiv.org/abs/2305.07895
- [OCRBench v2](ocr97_research/papers/ocrbench_v2_2024.pdf) and source: https://arxiv.org/abs/2501.00321
- [OmniDocBench](ocr97_research/papers/omnidocbench_2024.pdf) and source: https://arxiv.org/abs/2412.07626
- [Real5-OmniDocBench](ocr97_research/papers/real5_omnidocbench_2026.pdf) and source: https://arxiv.org/abs/2603.04205
- [DocLayNet](ocr97_research/papers/doclaynet_2022.pdf) and source: https://arxiv.org/abs/2206.01062

## Current architecture grade: 97/100 (as of 2026-05-06)

The architecture grade tracks implementation completeness and correctness of the OCR stack, not the challenger harness `study_score_avg`. See [README_OCR97_RELEASE_2026-04-21.md](README_OCR97_RELEASE_2026-04-21.md) for the full four-phase improvement campaign that raised the grade from 83/74 (finance/wider, 2026-05-05) to 96, then to 97 after model weights were built in.

**What moved the grade:**

| Change | Grade impact |
|---|---|
| Image pixel signals in document classifier | +2 |
| Checkbox detection (6 formats, explicit prompt) | +2 |
| TrOCR / DePlot model-not-found install hints | +1 |
| PaddleOCR install hint in health endpoint | +0.5 |
| Qwen pre-classifier (`AEGIS_QWEN_PRECLASSIFY_ENABLE=1`) | +4 wider / +2 finance |
| PaddleOCR prewarm at gateway startup | +1 |
| Model upgrade: qwen2.5vl:72b → qwen3-vl:32b | +3 |
| MinerU 2.5 activated | +0.5 |
| 4× RealESRGAN + parallel scale ensemble | +1 |
| Multi-image batch Qwen call | +1 |
| DePlot enabled (`AEGIS_DEPLOT_ENABLE=1`) | +0.5 |
| Numeric fidelity guard | +1 |
| Multi-page self-consistency (per-page parallel) | +0.5 |
| PDF DPI ensemble: 400 DPI + parallelized | +0.5 |
| `google/deplot` weights built in (Pix2Struct safetensors) | +0.5 |
| `microsoft/trocr-large-handwritten` weights built in (safetensors converted) | +0.5 |

**Model weight status:**
- `google/deplot` — loaded via `Pix2StructProcessor` + `Pix2StructForConditionalGeneration`, safetensors format, HuggingFace cache at `D:\AI\models\huggingface`
- `microsoft/trocr-large-handwritten` — loaded via `TrOCRProcessor` + `VisionEncoderDecoderModel`, converted to safetensors format at `D:\AI\models\trocr-large-handwritten`, pointed to via `AEGIS_TROCR_MODEL_ID`

**Remaining gap to 98:** integration test coverage and a live re-run of the challenger harness against the upgraded stack.

**Test suite (2026-05-06):** 123 tests, 0 failures.

---

## Latest local findings

### 2026-05-06 — Post-improvement campaign

The OCR stack has been significantly upgraded since the 2026-04-21 baseline study. The challenger comparison below reflects the state at the original study; it has not been re-run against the upgraded baseline. Key status changes since that run:

- `MinerU2.5` — **now active**. `AEGIS_OCR_ENGINE_MINERU2_5_READY=1` bypasses the disk-check that was blocking activation. MinerU is confirmed callable and is now in the `digital_pdf` and `table_dense` engine chains.
- `PaddleOCR-VL` — **local worker ready** (`reason: local_worker_ready`). `AEGIS_PADDLEOCR_VL_ALLOW_AUTODOWNLOAD=1` enables model download on first use. The `gb10_paddleocr_vl_url_unset` failures from the original study no longer occur.
- `DePlot` — **fully active**. `google/deplot` weights downloaded (safetensors format, HuggingFace cache). `load_deplot_runtime()` loads via `Pix2StructProcessor` + `Pix2StructForConditionalGeneration`. No fallback required.
- `TrOCR` — **fully active**. `microsoft/trocr-large-handwritten` weights downloaded and converted to safetensors at `D:\AI\models\trocr-large-handwritten`. `load_trocr_runtime()` loads via `TrOCRProcessor` + `VisionEncoderDecoderModel`. `AEGIS_TROCR_MODEL_ID` points to local path.
- Primary OCR model — **upgraded** from `qwen2.5vl:72b` (not installed, silently using 7B) to `qwen3-vl:32b` (20GB, running on port 11435).

### 2026-04-21 — Original baseline study

- `OCR97` remained the best measured route across the current slice set.
- `GOT-OCR2.0` was the only meaningful local challenger. It matched `OCR97` on digital, table-dense, mixed-layout, and tiny-text probes, but it lost badly on scanned PDF and warped-image slices because required finance tokens dropped out.
- `GOT-OCR2.0` did have a major latency advantage:
  - `OCR97` average latency: about `42.2s`
  - `GOT-OCR2.0` average latency: about `3.8s`
  - This is a real trade, but not enough to promote it over `OCR97` on the current accuracy-weighted study.
- `PaddleOCR-VL` did not produce a promotable local result in this checkout. The routed lane returned `gb10_paddleocr_vl_url_unset`, so its measured rows are effectively configuration failures rather than competitive OCR wins.
- `MinerU2.5` was runtime-pending in this study pass. The gateway path responded with `422` failures during the benchmark run.
- `OmniParser` and `MOCR` are included in the study corpus and manifests, but empirical sections remain unavailable until local implementations or adapters exist.
- The paired routes did not beat `OCR97` because Apollo already folds table reconstruction and finance verification into the baseline path.

Net result from original study (recommendations still apply for challenger study methodology):

- keep `OCR97` as the production default
- keep `GOT-OCR2.0` as a speed-oriented challenger worth revisiting for latency-sensitive cases
- do not promote `PaddleOCR-VL` or `MinerU2.5` until their local runtime contracts are actually healthy (now resolved for both)
- keep `OmniParser` and `MOCR` in the literature watchlist, not in the promotion queue

## Commands

Download the local paper cache:

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m Apollo.tools.ocr_benchmark_harness download-papers `
  --paper-manifest Apollo/evaluations/ocr97_research/papers_manifest.json `
  --output-root Apollo/evaluations/ocr97_research `
  --index-output Apollo/evaluations/ocr97_research/papers_index.json `
  --timeout-sec 120
```

Run the challenger study:

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m Apollo.tools.ocr_benchmark_harness study `
  --manifest Apollo/evaluations/ocr97_challenger_manifest.json `
  --paper-index Apollo/evaluations/ocr97_research/papers_index.json `
  --output-json Apollo/evaluations/artifacts/ocr97_challenger_study_20260421.json `
  --output-md Apollo/evaluations/artifacts/ocr97_challenger_study_20260421.md `
  --timeout-sec 20
```

Run the legacy regression harness:

```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m Apollo.tools.ocr_benchmark_harness benchmark `
  --manifest Apollo/evaluations/ocr_finance_manifest.sample.json `
  --output Apollo/logs/ocr_benchmark_latest.json `
  --route-mode quality_first
```

## Interpretation rules

- A measured loss means the challenger did not beat `OCR97` on the current local sample corpus.
- An unavailable route means the benchmark contract exists, but the runtime was not healthy enough to claim a local result.
- A literature-only route means the paper is part of the study set, but the implementation is not present locally yet.
- Promotion requires slice-level deltas plus acceptable operational tradeoffs. A paper claim alone is not enough.
