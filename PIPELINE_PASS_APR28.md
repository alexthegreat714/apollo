# Apollo Legit Pipeline Pass - April 28, 2026

This is a one-time conditional Apollo pipeline pass. It keeps the recurring Apollo tasks disabled and only runs if the April 27 Apollo pipeline test succeeds.

## Schedule

- Task name: `ApolloPipelinePassApr28_0100`
- Local start: `2026-04-28 01:00 America/Chicago`
- Sky calendar window: `2026-04-28 01:00-08:00 America/Chicago`
- Runner: `Apollo/watchdog/run_apollo_pipeline_pass_apr28_0100.ps1`
- Scheduler: `Apollo/watchdog/schedule_apollo_pipeline_pass_apr28_0100.ps1`
- Manifest: `_tmp/apollo_pipeline_pass_apr28_0100/schedule_manifest.json`
- Expected run directory: `Apollo/logs/nightly/2026-04-28/apollo_pipeline_pass_apr28_0100`

## Gate

The runner checks:

- `Apollo/logs/nightly/2026-04-27/apollo_pipeline_test_apr27_0100/status.json`
- `ok=true`
- `quality.gate_pass=true`
- a non-empty `finished_at`

If those checks pass, it runs `python -m Apollo.nightly_pipeline run` with run mode `nightly` and run id `apollo_pipeline_pass_apr28_0100`.

If those checks do not pass, it does not run the full pipeline. Instead it writes:

- `_tmp/apollo_pipeline_pass_apr28_0100/pass_status.json`
- `_tmp/apollo_pipeline_pass_apr28_0100/REMINDER.md`

The reminder explains which test gate failed and tells Sky to report that Apollo needs adjustment.

## Boundaries

- Live trade execution is disabled.
- `ApolloBackgroundPipeline` remains disabled.
- `ApolloNightlyPipeline0100` remains disabled.
- Sky's morning report includes the pass status whether it is scheduled, running, complete, or blocked.
