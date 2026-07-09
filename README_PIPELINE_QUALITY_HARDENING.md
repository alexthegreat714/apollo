# Apollo Pipeline Quality Hardening (Regression + Rollback)

This is the canonical regression/rollback reference for Apollo background pipeline hardening.

## What shipped

- Run-state truthfulness:
  - production-only current-run provenance
  - persisted pointer file: `logs/nightly/background_current_pointer.json`
  - monotonic `status_seq` + `updated_at` on each status save
  - atomic JSON writes (`.tmp` + `os.replace`) to reduce stale overwrite risk
- Quality scoring contract:
  - strict per-stage gate (`>=70`)
  - current run score + rolling 7-run median fields
  - confidence band from variance + artifact completeness
- Stage quality gates:
  - gather topic-intent mismatch rejection
  - OCR semantic/table depth checks
  - self-check verifies stage evidence + score consistency
- API surface:
  - `GET /admin/background_pipeline/scorecard?window=7`
  - history payload includes `history_cursor` and quality metadata
  - RAG sample requires valid current-run pointer

## Config defaults

Pipeline/runtime:
- `APOLLO_PIPELINE_STAGE_MIN_SCORE=70`
- `APOLLO_BACKGROUND_MAX_ACTIVE_MODELS=1`
- `APOLLO_BACKGROUND_MIN_IDLE_SEC=300`
- `APOLLO_BACKGROUND_INTERVAL_MIN=10`

OCR depth:
- `APOLLO_OCR_DOC_MIN_CHARS=600`
- `APOLLO_OCR_AVG_MIN_CHARS=1200`
- `APOLLO_OCR_MIN_CONFIDENCE=0.45`
- `APOLLO_OCR_MIN_SEMANTIC_HITS=2`
- `APOLLO_OCR_REQUIRE_TABLE_DOCS=0`

Gather quality:
- `APOLLO_GATHER_MIN_ACCEPTED_SOURCES=2`
- `APOLLO_GATHER_MIN_AVG_SCORE=68`

## Endpoint contracts

- `GET /admin/background_pipeline/status`
  - returns `status.run_meta.current_pointer_valid`
  - returns `quality.current`, `quality.rolling`, `quality.confidence_band`
  - returns `stage_details` and `next_run_at`
- `GET /admin/background_pipeline/history?limit=<n>`
  - returns `current_run`, `archive_runs`, `history_cursor.available_days`
  - top card should use `current_run`; history list should use `archive_runs`
- `GET /admin/background_pipeline/scorecard?window=<n>`
  - returns current + rolling score payload only
- `POST /admin/background_pipeline/rag_sample`
  - fails with `current_run_pointer_invalid` when current provenance is not valid

## Known failure signatures

- `current_run_pointer_invalid`
  - current pointer missing/invalid or points to non-production path
- `gather_quality_failed:*`
  - gather accepted sources or average score below thresholds
- `ocr_quality_failed:min_chars=...,avg_chars=...`
  - OCR depth gate failed
- `hipporag_zero_triples`
  - graph step progressed but produced no triples
- `stage_timeout:<stage>`
  - background stage subprocess exceeded timeout budget

## Rollback procedure (subsystem-targeted)

1) Identify failing subsystem from `quality.gate_fail_reasons` and stage artifacts.
2) Use latest stable snapshot (table below) and restore only impacted behavior:
   - status truthfulness: pointer + status save logic
   - quality scoring: `_compute_quality` + rolling/confidence logic
   - stage gate: gather/OCR/Hippo/self-check evaluators
3) Re-run targeted tests:
   - `pytest -q tests/test_hipporag_entity_extractor.py tests/test_nightly_pipeline.py`
4) Validate live payload:
   - `/admin/background_pipeline/status`
   - `/admin/background_pipeline/history`
   - `/admin/background_pipeline/scorecard`

## Verification checklist

- `status.run_meta.current_pointer_valid == true` for live background run.
- Top card run id matches pointer run id.
- History panel excludes current run from archive list.
- `quality.score_basis == current_plus_rolling7_median`.
- `quality.rolling7_median_score` and `quality.confidence_band.label` present.
- RAG sample returns data only when pointer is valid.

## Immutable snapshots (regression anchor)

| Snapshot | Commit | Run ID | Scorecard | Artifacts |
|---|---|---|---|---|
| Baseline | `2cab131` | (pre-hardening baseline) | Gather 38 / OCR 63 / HippoRAG 7 / Self-check 85 / Overall 48 | `logs/nightly/.../status.json` |
| Post-hardening sample | `2cab131` | `apollo_quality_sample_20260418_105019` | Gather 76 / OCR 84 / HippoRAG 78 / Self-check 90 / Overall 80 | `logs/nightly_quality_sample/2026-04-18/apollo_quality_sample_20260418_105019/` |

When creating a new snapshot, append a new row with:
- run id
- commit hash
- stage scores + overall
- absolute artifact directory path
