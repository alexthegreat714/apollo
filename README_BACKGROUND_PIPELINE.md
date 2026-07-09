# Apollo Background GB10 Pipeline

This document describes the **background GB10 pipeline** that replaced Apollo’s
night-only batch model.

Purpose:
- let Apollo make forward progress during the day
- only use GB10 when the machine appears idle
- yield automatically when GB10 is needed for higher-priority work
- keep a simple, inspectable log trail that another agent can audit and extend

This file is intended to be readable by Claude, Codex, or a human operator
without needing to reverse-engineer `Apollo/nightly_pipeline.py`.

---

## Summary

Apollo now runs a **stepwise background pipeline** instead of waiting for a
single overnight window.

The pipeline still follows the same logical stages:
1. `gather`
2. `ocr`
3. `hipporag`
4. `self_check`

The difference is that Apollo now advances **one small step at a time** on a
recurring background schedule.

GB10-heavy stages only run when:
- the desktop has been idle long enough
- GB10/Ollama is not already busy above the configured threshold

If GB10 is busy, Apollo does **not** start the next heavy step. It records a
waiting state and tries again on the next scheduled tick.

This is **cooperative pause/resume**, not hard preemption mid-inference.

---

## Current Control Surface

### Code

- `Apollo/nightly_pipeline.py`
  - background gating
  - incremental OCR step execution
  - incremental HippoRAG step execution
  - task scheduling
- `Apollo/watchdog/run_apollo_background_pipeline.ps1`
  - Windows scheduled-task wrapper
- `Apollo/app.py`
  - admin endpoints for run/status/schedule
- `Apollo/apollo_hipporag/build_graph.py`
  - incremental graph-build budget (`max_new_chunks`)

### API endpoints

- `POST /admin/background_pipeline/run`
  - run one background step immediately
- `GET /admin/background_pipeline/status`
  - read latest status
- `POST /admin/background_pipeline/schedule`
  - register/update the recurring background task

Nightly one-shot mode still exists, but the background scheduler is now the
primary path.

---

## Scheduler Model

### Background task

Task name:
- `ApolloBackgroundPipeline`

Wrapper:
- `Apollo/watchdog/run_apollo_background_pipeline.ps1`

Default interval:
- every `10` minutes

### Nightly task

Task name:
- `ApolloNightlyPipeline0100`

Current intended state:
- **disabled** when background scheduling is active

Reason:
- avoid competing orchestration paths for the same GB10 work

---

## Gating Rules

Background GB10 work is allowed only when the gate passes.

Current gate fields:
- `active_models`
- `max_active_models`
- `idle_seconds`
- `min_idle_sec`
- `base_url`

### Default policy

From `Apollo/.env`:
- `APOLLO_BACKGROUND_INTERVAL_MIN=10`
- `APOLLO_BACKGROUND_MAX_ACTIVE_MODELS=1`
- `APOLLO_BACKGROUND_MIN_IDLE_SEC=300`
- `APOLLO_BACKGROUND_HIPPORAG_STEP_CHUNKS=8`
- `APOLLO_BACKGROUND_OCR_DOCS_PER_TICK=1`

Interpretation:
- Apollo checks every `10` minutes
- Apollo only starts a GB10-heavy step if the system has been idle for at least
  `300` seconds
- Apollo allows at most `1` active Ollama model slot before treating GB10 as busy
- OCR advances `1` document per tick
- HippoRAG advances `8` unindexed chunks per tick

### Important behavior

The gate is checked before **heavy stages**:
- `ocr`
- `hipporag`

It is **not** checked before `gather`, because gather can still make progress
without monopolizing GB10.

---

## Resume Model

The background pipeline persists state in a stable run directory:

- run id: `apollo_background_current`

This lets Apollo resume over many scheduler ticks instead of starting over.

### Stage completion semantics

- `gather`
  - completes in one step
- `ocr`
  - completes after all queued source documents have been processed
- `hipporag`
  - completes after all remaining unindexed chunks have been processed
- `self_check`
  - completes in one step

Apollo determines the next step from stage completion flags rather than assuming
a monolithic run.

---

## OCR in Background Mode

OCR is now GB10-first.

Routing policy:
- finance PDFs / layout-heavy docs -> `PaddleOCR-VL` primary
- dense scans / handwriting / tiny text -> `GOT-OCR2` primary
- semantic cleanup / fallback -> `Qwen OCR`
- local `rapidocr` / `tesseract` only as fallback

Background OCR step behavior:
- process up to `APOLLO_BACKGROUND_OCR_DOCS_PER_TICK`
- ingest extracted text into Apollo RAG immediately
- write/update `02_ocr_ingest.json`
- write/update `02_ocr_extracts.md`

---

## HippoRAG in Background Mode

HippoRAG graph build is now incremental.

Key addition:
- `max_new_chunks` in `Apollo/apollo_hipporag/build_graph.py`

This allows Apollo to build the graph in bounded chunks per background tick
instead of trying to process the full queue in one long uninterrupted run.

The graph step records:
- `remaining_before`
- `remaining_after`
- `completed`
- normal KG stats

This is the core mechanism that makes pause/resume practical.

---

## Logging Model

The logging goal is:
- one stable status file for “what is happening now”
- one stable run directory for incremental artifacts
- launcher logs for task-level debugging
- schedule metadata for task registration state

### Primary files

- `Apollo/logs/nightly/latest_status.json`
  - latest global status snapshot

- `Apollo/logs/nightly/YYYY-MM-DD/apollo_background_current/status.json`
  - canonical background run state

- `Apollo/logs/nightly/background_schedule.json`
  - current recurring background task registration

- `Apollo/logs/nightly/background_launcher_*.log`
  - wrapper-level execution logs for each scheduled tick

### Stage artifacts

Inside the run directory:
- `00_preflight.json`
- `00_preflight.log`
- `01_data_gather.json`
- `02_ocr_ingest.json`
- `02_ocr_extracts.md`
- `03_hipporag.json`
- `04_self_check.json`
- `summary.md` once fully complete

### Operational recommendation

If another agent is extending this system, it should treat:
- `status.json`
- `latest_status.json`
- `background_schedule.json`

as the **authoritative operational log surface**.

Launcher logs are supporting evidence, not the primary state store.

---

## Example State Progression

### 1. Fresh background run

Expected fields:
- `run_mode: "background"`
- `current_stage: "gather"`
- no completed stages yet

### 2. After gather succeeds

Expected fields:
- `stages.gather.completed = true`
- `current_stage = "ocr"`

### 3. If GB10 is busy

Expected fields:
- `waiting.reason = "gb10_busy"`
- `gb10_gate.idle = false`

### 4. After OCR finishes

Expected fields:
- `stages.ocr.completed = true`
- OCR report path present
- `current_stage = "hipporag"`

### 5. After all incremental HippoRAG steps finish

Expected fields:
- `stages.hipporag.completed = true`
- `current_stage = "self_check"`

### 6. Final state

Expected fields:
- `ok = true`
- `finished_at` populated
- `current_stage = "done"`

---

## What Claude Should Check

If Claude is reviewing or building on this system, these are the main review items:

### 1. Gate correctness
- does the idle-time gate match real user behavior?
- is `max_active_models` too strict or too permissive?
- should other GB10 consumers be detected more explicitly?

### 2. Resume correctness
- can partial OCR state survive restarts safely?
- can partial HippoRAG state survive restarts safely?
- are stage completion flags consistent across all paths?

### 3. Logging correctness
- does every scheduler tick produce enough evidence to debug failures?
- should there be an append-only high-level event log in addition to status snapshots?

### 4. Throughput tuning
- should `background_hipporag_step_chunks` be larger or smaller?
- should OCR process more than one document per tick?
- should gather be rate-limited more aggressively?

### 5. Preemption strategy
- current behavior is cooperative between ticks
- if true mid-step preemption is needed later, that will require a different
  execution model and probably child-process cancellation rules

---

## Suggested Next Extensions

High-value follow-ons:

1. Add an append-only event log
   - file example: `Apollo/logs/nightly/background_events.jsonl`
   - each line: timestamp, stage, action, gate state, result

2. Add stronger GB10-busy detection
   - distinguish Apollo’s own GB10 usage from other agents
   - detect user-triggered foreground requests explicitly

3. Add admin controls
   - pause background pipeline
   - resume background pipeline
   - reset current background run
   - force-next-stage

4. Add dashboard surface
   - expose current background gate state in Apollo UI
   - show remaining OCR docs / remaining HippoRAG chunks

5. Add run rollover
   - move `apollo_background_current` to an archived run id when fully complete
   - automatically seed a fresh background run after completion

---

## Known Constraints

- Background pause is **between steps**, not in the middle of an inference call
- OCR and HippoRAG quality depend on real GB10 service endpoints and model availability
- `latest_status.json` is a snapshot, not a history log
- scheduled-task metadata may lag one tick behind the latest run status if another
  process updates the background run immediately after inspection

---

## Minimal Review Commands

Useful checks for another agent:

- read scheduler metadata:
  - `Apollo/logs/nightly/background_schedule.json`

- read current background state:
  - `Apollo/logs/nightly/YYYY-MM-DD/apollo_background_current/status.json`

- inspect latest snapshot:
  - `Apollo/logs/nightly/latest_status.json`

- run one step manually:
  - `python -m Apollo.nightly_pipeline background-step`

- re-register the recurring task:
  - `python -m Apollo.nightly_pipeline background-schedule`

---

## Bottom Line

Apollo’s GB10 work is now **continuous, resumable, and opportunistic** rather
than locked to a single overnight window.

That is the intended operating model going forward:
- small bounded steps
- explicit gate checks
- resumable state
- simple log surface
- no need to wait until tonight to make progress
