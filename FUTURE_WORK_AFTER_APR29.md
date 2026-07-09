# Apollo Future Work After April 29

Do not schedule another automatic Apollo run after `ApolloNightlyPipeline0100` until the April 26-29 evidence is reviewed.

## Decision Rule

Use Sky's morning report after the April 29 guarded run to choose the next action:

- If OCR97 passes cleanly, Apollo's April 27 test passes, April 28 pass runs and passes, and April 29 `ApolloNightlyPipeline0100` passes, consider restoring a cautious recurring Apollo cadence.
- If any run is blocked, incomplete, or fails a quality gate, fix that failing stage before scheduling more automation.
- If everything runs but confidence is low, schedule one more guarded test pass instead of recurring automation.

## Recommended Next Actions

If April 29 passes:

- Keep live trade execution disabled.
- Restore Apollo on a monitored cadence rather than full unattended daily operation.
- Start with either two or three scheduled research-only nights per week, or one more week of nightly runs where Sky reports every decision gate.
- Require the morning report to include stage scores, gate failures, decision readiness, and artifact paths before any trade-decision automation is reconsidered.

If April 29 is blocked or fails:

- Do not re-enable `ApolloBackgroundPipeline`.
- Do not restore recurring `ApolloNightlyPipeline0100`.
- Open the artifact referenced in Sky's morning report.
- Fix the first failed stage in this order: gather, OCR, HippoRAG, self-check, decision gate.
- Run only a targeted repair test for the failed stage before scheduling another full pass.

## Stage-Specific Guidance

Gather failure:

- Tune trusted source selection and rate-limit fallback.
- Prefer real public finance sources over seed/local fallbacks.
- Require enough PDF/report sources before OCR.

OCR failure:

- Review OCR97 benchmark results first.
- Fix Apollo's document extraction policy if documents are being excluded as `ocr_no_documents`.
- Check whether native PDF text, visual OCR, and quality scan rules are disagreeing.

HippoRAG failure:

- Check whether Apollo produced finance chunks, triples, or validated non-triple finance evidence.
- If graph build stalls, reduce batch size or requeue bad documents instead of broadening automation.

Self-check or decision-gate failure:

- Keep Apollo in research-only mode.
- Improve evidence scoring and decision readiness rules before any schedule expansion.

## Morning Report Reminder

Sky should remind us that April 30 is a decision point, not an automatic run day:

Review OCR97, Apollo test, Apollo conditional pass, and `ApolloNightlyPipeline0100` evidence. Choose the next schedule only after reading the reported stage scores and gate failures.
