# ApolloNightlyPipeline0100 - April 29, 2026 Guarded Update

The `ApolloNightlyPipeline0100` scheduled task has been updated to a one-time guarded run after the current OCR97 and Apollo validation sequence.

## Schedule

- Task name: `ApolloNightlyPipeline0100`
- Local start: `2026-04-29 01:00 America/Chicago`
- Sky calendar window: `2026-04-29 01:00-08:00 America/Chicago`
- Runner: `Apollo/watchdog/run_apollo_nightly_pipeline0100_apr29_guarded.ps1`
- Scheduler: `Apollo/watchdog/schedule_apollo_nightly_pipeline0100_apr29.ps1`
- Manifest: `_tmp/apollo_nightly_pipeline0100_apr29/schedule_manifest.json`
- Expected run directory: `Apollo/logs/nightly/2026-04-29/apollo_nightly_pipeline0100_apr29`

## Gate

The guarded runner checks the April 28 pass first:

- `Apollo/logs/nightly/2026-04-28/apollo_pipeline_pass_apr28_0100/status.json`
- `ok=true`
- `quality.gate_pass=true`
- a non-empty `finished_at`

If the April 28 pass succeeds, `ApolloNightlyPipeline0100` runs the Apollo nightly pipeline with run id `apollo_nightly_pipeline0100_apr29`.

If the April 28 pass does not succeed, the runner writes:

- `_tmp/apollo_nightly_pipeline0100_apr29/promotion_status.json`
- `_tmp/apollo_nightly_pipeline0100_apr29/REMINDER.md`

Sky's morning report includes this status either way.

## Boundary

This is still a pipeline readiness run. Live trade execution remains disabled.

## Future Work

Do not schedule another automatic Apollo run after this guarded task until the April 26-29 evidence is reviewed.

Follow-up decision note: `Apollo/FUTURE_WORK_AFTER_APR29.md`
