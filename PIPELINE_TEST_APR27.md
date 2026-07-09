# Apollo Pipeline Test - April 27, 2026

This is a one-time Apollo pipeline test run. It does not re-enable Apollo's recurring background or nightly scheduled tasks.

## Schedule

- Task name: `ApolloPipelineTestApr27_0100`
- Local start: `2026-04-27 01:00 America/Chicago`
- Sky calendar window: `2026-04-27 01:00-08:00 America/Chicago`
- Runner: `Apollo/watchdog/run_apollo_pipeline_test_apr27_0100.ps1`
- Scheduler: `Apollo/watchdog/schedule_apollo_pipeline_test_apr27_0100.ps1`
- Manifest: `_tmp/apollo_pipeline_test_apr27_0100/schedule_manifest.json`
- Expected run directory: `Apollo/logs/nightly/2026-04-27/apollo_pipeline_test_apr27_0100`

## Purpose

The run is a test of Apollo's pipeline mechanics:

- Web gather/source scoring
- OCR/document ingestion
- HippoRAG graph build
- Self-check
- Decision-gate readiness reporting

It is not a live trading job and does not execute trades. The test is meant to show whether Apollo can produce measurable evidence quality before recurring trade-decision automation is turned back on.

## Resource Posture

The runner sets below-normal process priority and caps common numeric/OCR thread pools:

- `OMP_NUM_THREADS=4`
- `OPENBLAS_NUM_THREADS=4`
- `MKL_NUM_THREADS=4`
- `NUMEXPR_NUM_THREADS=4`
- `OPENCV_FOR_THREADS_NUM=4`

Apollo's recurring tasks remain disabled:

- `ApolloBackgroundPipeline`
- `ApolloNightlyPipeline0100`

## Morning Report

Sky's morning report reads `_tmp/apollo_pipeline_test_*` manifests and the expected Apollo `status.json` when it exists. The report includes the Apollo test status even if the run is still scheduled, running, incomplete, or complete with gate failures.
