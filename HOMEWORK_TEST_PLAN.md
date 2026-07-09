# Apollo Nightly Homework — Full Test Plan

**System under test:** `nightly_high_risk_homework.py` and its full dependency chain  
**Reference run:** `apollo_high_risk_homework_20260625_2300` (Jun 25 night)  
**Failures captured:** OCR97 ledger staleness (score 25 vs actual 100), adjudication parse (fixed prior session)  
**Goal:** Validate every gate, fix regressions, and push toward tier advancement  

---

## Overview of Layers

| Layer | Name | Scope | Run time |
|---|---|---|---|
| 0 | Unit | Individual functions, no I/O | < 30s |
| 1 | Component integration | Phase-to-phase handoffs | 2–5 min |
| 2 | End-to-end pipeline | Full nightly run, real data | 25–45 min |
| 3 | Stress / adversarial | GB10 contention, RAM pressure, OCR edge cases | 60–90 min |
| 4 | Ceiling push | Risk-on regime, tier advancement, live simulation gate | 30–60 min |

---

## Layer 0 — Unit Tests

These are pure-Python, no Ollama, no GB10 required. All should pass on any machine.

### 0.1 — `_parse_adjudication_reply` robustness

File: `Apollo/trade_cycle.py`  
Prior failure: Jun 17 run got `parse_ok: False` because qwen3 wraps output in `<think>` blocks.

**Test cases (all must return `parse_ok: True`):**

```python
# Clean JSON
_parse_adjudication_reply('{"decision": "approve", "rationale": "strong setup"}')

# JSON wrapped in think block (qwen3 chain-of-thought)
_parse_adjudication_reply('<think>Let me reason...</think>\n{"decision": "approve", "rationale": "ok"}')

# JSON in markdown code fence
_parse_adjudication_reply('```json\n{"decision": "watch", "rationale": "regime weak"}\n```')

# JSON buried in prose
_parse_adjudication_reply('After careful analysis: {"decision": "reject", "rationale": "no catalyst"} end.')

# Nested think + fence
_parse_adjudication_reply('<think>deep thought</think>\n```json\n{"decision": "approve", "rationale": "x"}\n```')
```

**Must fail cleanly (return `parse_ok: False`, no exception):**

```python
_parse_adjudication_reply("")                  # empty
_parse_adjudication_reply("not json at all")   # garbage
_parse_adjudication_reply(None)                # None input
```

**Pass criteria:** No exceptions raised. `parse_ok` correct in all cases.

---

### 0.2 — OCR97 ledger gate

File: `Apollo/nightly_high_risk_homework.py::_load_ocr97_status()`  
Prior failure: Returned score=25 (placeholder), causing `quality_confident=False`.

**Test scenario A — current state (should pass after fix):**

```python
status = _load_ocr97_status()
assert status["fresh"] == True          # completed_at < 168h ago
assert status["quality_confident"] == True  # score >= 85.0
assert status["score"] == 100.0
assert status["status"] == "complete"
```

**Test scenario B — stale result (>168h old):**

Temporarily set `latest_completed_at` in ledger to 8 days ago. Expect:
```python
assert status["fresh"] == False
assert status["quality_confident"] == False  # fresh gate already fails
```

**Test scenario C — score 80 (below threshold):**

Set ledger `latest_score = 80`. Expect:
```python
assert status["fresh"] == True
assert status["quality_confident"] == False  # 80 < 85.0 threshold
```

**Test scenario D — ledger unavailable (Sky not on path):**

Remove Sky from `sys.path`. Expect graceful fallback:
```python
assert status["status"] == "unavailable"
assert status["fresh"] == False
assert status["quality_confident"] == False
assert "error" in status
```

**Pass criteria:** All four scenarios behave exactly as described. No KeyError or AttributeError.

---

### 0.3 — `_blocker_hierarchy` ordering

File: `Apollo/nightly_high_risk_homework.py`  
The hierarchy must always be: `ocr_document_quality` → `hipporag_usefulness` → `price_volume_confirmation` → `trade_risk_gate`

**Test:** Construct a context where all four blockers are active. Verify ordering:
```python
blockers = _blocker_hierarchy(context_with_all_blockers)
assert blockers == ["ocr_document_quality", "hipporag_usefulness", "price_volume_confirmation", "trade_risk_gate"]
```

**Test:** OCR passing, hipporag weak, trade blocked:
```python
blockers = _blocker_hierarchy(context_ocr_ok_hipporag_weak)
assert "ocr_document_quality" not in blockers
assert blockers[0] == "hipporag_usefulness"
```

---

### 0.4 — Paper screen gate thresholds

File: `Apollo/nightly_high_risk_homework.py::_phase_high_risk_screen()`

From source (lines 420–424):
- **Pressed** requires: `rr >= 2.0` AND `graph_score >= 65` AND `pressed_candidate == True`
- **Aggressive** requires: `rr >= 1.5` AND (`event_score >= 65` OR `volume_ratio >= 1.25` OR `graph_score >= 65`)
- **Entry reset** requires: `score >= 70`

**Boundary tests:**

| Case | rr | graph | pressed_candidate | Expected tier |
|---|---|---|---|---|
| A | 2.0 | 65 | True | pressed |
| B | 1.99 | 65 | True | aggressive (rr just under) |
| C | 2.0 | 64 | True | aggressive (graph just under) |
| D | 2.0 | 65 | False | aggressive (not flagged pressed) |
| E | 1.5 | 64 | False | aggressive (event_score=65 qualifies) |
| F | 1.49 | 64 | False | entry_reset if score>=70, else nothing |
| G | 1.5 | 20 | False | nothing (no event/volume/graph qual) |

**Pass criteria:** Each case routes to exactly the expected tier. No off-by-one on thresholds.

---

### 0.5 — `wait_for_gb10_ready` gate logic

File: `common/gb10_gate.py`

**Test scenario A — GB10 idle (no models swapping):**

Mock `/api/ps` returning `{"models": [{"name": "qwen2.5:72b", "size_vram": 1000}]}`. Expect:
```python
result = wait_for_gb10_ready(base_url=mock_url, target_model="qwen2.5:72b", max_wait_sec=30)
assert result["ready"] == True
assert result["waited_sec"] <= 5
assert result["reason"] in {"target_model_loaded", "gb10_idle"}
```

**Test scenario B — GB10 loading (expires max_wait):**

Mock `/api/ps` always returning a model with `size_vram=0` (loading). Expect:
```python
result = wait_for_gb10_ready(base_url=mock_url, target_model="qwen2.5:72b", max_wait_sec=5)
assert result["ready"] == False
assert result["reason"] == "max_wait_exceeded"
assert result["waited_sec"] >= 5
```

**Test scenario C — GB10 unreachable:**

Mock connection refused. Expect:
```python
result = wait_for_gb10_ready(base_url="http://127.0.0.1:9999", target_model="qwen2.5:72b", max_wait_sec=5)
assert result["ready"] == False
assert "unreachable" in result["reason"]
```

---

## Layer 1 — Component Integration

Requires Apollo running or the pipeline importable. No live GB10 or web scrape required for most.

### 1.1 — OCR97 benchmark → ledger registration flow

**This tests the bug we fixed on Jun 26.**

1. Run `Apollo/tools/run_ocr97_real_doc_benchmark.py` with `--manifest config/ocr97_real_documents_manifest.json`
2. Confirm it exits 0 and writes `reports/ocr97_real_docs/<run_id>/summary.json`
3. Run `Apollo/tools/ocr97_real_doc_calendar_update.py --stdout-log <log> --exit-code 0 --event-id "" --task-name test_manual`
4. Read the capability ledger back:

```python
ledger = load_project_capability_ledger()
ocr97 = ledger["projects"]["ocr97"]
assert float(ocr97["latest_score"]) >= 85.0
assert ocr97["latest_status"] == "complete"
```

**Pass criteria:** Score propagates from benchmark → calendar update → capability ledger. The `initial_tracking` daily file generated the following day must inherit the real score, not default to 25.

**Regression check:** Run `task_grading._initial_project_report("ocr97", existing=ledger["projects"]["ocr97"])` and confirm `capability_grade >= 85`, not 25.

---

### 1.2 — Deep adjudication 3-layer flow

**Layer 1 — Direct GB10 with gate:**

1. Ensure GB10 is reachable on `http://127.0.0.1:11435`
2. Call `_stage_deep_trade_adjudication` with a well-formed candidate list
3. Verify `parse_ok: True` and `decision` is one of `{approve, downgrade_to_watch, reject, adjudication_unavailable}`
4. Verify `fallback_reason_code` is empty (provider reached successfully)

Expected elapsed: 30–180s (qwen2.5:72b first token latency on GB10)

**Layer 2 — Gate timeout (GB10 busy):**

1. Start a long qwen2.5:72b inference on GB10 manually
2. Call `_stage_deep_trade_adjudication` with `max_wait_sec=15` so gate expires fast
3. Verify it falls through to Layer 3 (Sky queue) rather than hanging

**Layer 3 — Queue fallback (GB10 unreachable):**

1. Point GB10 URL to a dead port (`127.0.0.1:9999`)
2. Call `_stage_deep_trade_adjudication`
3. Verify it posts to `SKY_URL/gb10/enqueue` and polls until done or timeout
4. Verify final output is either a valid parse or `adjudication_unavailable` with `deterministic_decision_used: True`

**Pass criteria:** All three layers exit cleanly. No unhandled exceptions. Timeout does not hang the process.

---

### 1.3 — Phase handoff data contracts

Each phase produces a dict consumed by the next. Verify the contract:

| From phase | Key | Consumed by | Test |
|---|---|---|---|
| `event_dislocation_study` | `top_proposals[].score` | `high_risk_paper_screen` | assert all proposals have float score |
| `event_dislocation_study` | `top_proposals[].risk_reward_estimate` | `high_risk_paper_screen` | assert all proposals have float rr |
| `high_risk_paper_screen` | `pressed_candidates` | `technical_confirmation_trade_cycle` | assert list, items have ticker + score |
| `graph_rag_enrichment` | `label` | `pressed_aggressive_projection` | assert in `{graph_augmented, supporting_context_only, not_used}` |
| `technical_confirmation_trade_cycle` | `deep_trade_adjudication.parse_ok` | `pressed_aggressive_projection` | assert bool |

Run each phase in isolation with fixture inputs and validate outputs match the contract schema.

---

### 1.4 — Confidence model and blocker promotion

The `confidence_model` block controls human display and tier eligibility.

**Test:** With OCR97 score=100, hipporag=graph_augmented, adjudication=approve, strong price/volume:
```python
assert confidence["source_confidence"] in {"high", "medium"}
assert confidence["technical_confirmation"] in {"high", "medium"}
assert confidence["top_blocker"] != "ocr_document_quality"
assert "ocr_document_quality" not in confidence["blocker_hierarchy"]
```

**Test:** With OCR97 score=60 (below threshold):
```python
assert "ocr_document_quality" in confidence["blocker_hierarchy"]
assert confidence["blocker_hierarchy"][0] == "ocr_document_quality"  # must be first
```

---

## Layer 2 — End-to-End Pipeline

Full run. GB10 must be reachable. Runs at or after 10:00 PM CT.

### 2.1 — Baseline full run

```bash
cd Apollo
python -m Apollo.nightly_high_risk_homework --watchlist AMD CRWD TSLA NVDA META
```

**Expected phase outcomes:**

| Phase | Pass criteria |
|---|---|
| `post_close_market_snapshot` | ≥15 of 20 snapshots usable |
| `news_catalyst_gather` | ≥15 event summaries gathered |
| `event_dislocation_study` | ≥10 proposals built, `best` is a known ticker |
| `high_risk_paper_screen` | AMD and/or CRWD appear in pressed or aggressive list |
| `graph_rag_enrichment` | label is `graph_augmented` (not `not_used`) |
| `technical_confirmation_trade_cycle` | `top_ticker` populated, adjudication `parse_ok: True` |
| `pressed_aggressive_projection` | `primary_action` is valid (not null) |
| `deep_daily_report` | Report file written, non-empty |

**OCR97 gate check (regression from Jun 25 bug):**

```python
assert report["confidence_model"]["top_blocker"] != "ocr_document_quality"
assert report["ocr97_status"]["quality_confident"] == True
assert report["ocr97_status"]["score"] >= 85.0
```

**Adjudication check:**

```python
trade = report["phase_outputs"]["technical_confirmation_trade_cycle"]
adj = trade["deep_trade_adjudication"]
assert adj["parse_ok"] == True        # was False on Jun 17
assert adj["decision"] != ""           # must have a real decision
assert (adj.get("model_meta") or {}).get("fallback_reason_code", "") == ""  # not a fallback
```

**Run time target:** 25–45 min end-to-end.

---

### 2.2 — Re-run with regime-favorable conditions (market stress)

Manually inject a mock market snapshot with `market_regime_score >= 60` to test tier advancement.

Set env vars:
```bash
APOLLO_MARKET_REGIME_OVERRIDE=65
APOLLO_PAPER_SCREEN_THRESHOLD_OVERRIDE=True
```

**Expected outcomes:**
- AMD with `score=72.55`, `rr=3.27`, `graph_asym=87.3` should advance to `paper_pressed_aggressive_probe`
- CRWD with `score=70.02`, `rr=2.15` should advance to `paper_pressed_aggressive_probe`
- `primary_action` should be `paper_pressed_aggressive_probe`, not `wait_for_entry_reset`
- `risk_posture` should be `pressed_simulation_only`

This is the test that confirms AMD and CRWD are correctly gated on regime, not on fundamental weakness.

---

### 2.3 — Morning refresh consistency

The homework produces a `morning_refresh_required: True` flag. Verify:

1. OCR97 score at run time persists correctly to next day's `initial_tracking`
2. The stress sandbox report path is valid and accessible
3. `open_behavior` is one of `{wait_15_to_30_min_for_price_volume_confirmation, monitor_and_wait, paper_probe_on_open}`

---

## Layer 3 — Stress and Adversarial

### 3.1 — GB10 contention (the original Jun 17 failure)

**Setup:** Queue two long-running jobs on GB10 before the homework trade cycle reaches Stage 4b.

1. Start a `qwen2.5:72b` job manually via Ollama (`/api/generate` with a 2000-token prompt)
2. Immediately start the homework run
3. When Stage 4b (`_stage_deep_trade_adjudication`) fires, GB10 is busy

**Expected behavior:**
- `wait_for_gb10_ready` polls every 8s, waits up to 300s
- If GB10 frees within 300s: adjudication succeeds
- If GB10 stays busy past 300s: falls to Layer 3 queue
- No `parse_ok: False` due to timeout (the Jun 17 bug)
- No `0xC000012D` process crash from RAM pressure during wait

**Pass criteria:** Homework completes with a valid adjudication decision regardless of GB10 timing. Elapsed time may be long but the result must be clean.

---

### 3.2 — Partial OCR97 documents (low quality input)

Simulate a bad OCR97 run by writing a temporary ledger entry with `score=60, status=complete, age_hours=10`:

```python
# Inject bad OCR score
ledger["projects"]["ocr97"]["latest_score"] = 60.0
```

Run the homework. Verify:
- `quality_confident == False`
- `ocr_document_quality` appears as `top_blocker`
- Homework still completes (score doesn't crash it, just blocks tier)
- Final `primary_action == "wait_for_entry_reset"` (not null, not exception)

---

### 3.3 — GB10 completely unreachable

Set `APOLLO_GB10_BASE_URL=http://127.0.0.1:9999`.

**Expected cascade:**
1. `wait_for_gb10_ready` returns `ready=False, reason=gb10_unreachable` immediately
2. `_direct_attempt` returns `None`
3. `_sky_queue_attempt` fires, POSTs to `SKY_URL/gb10/enqueue`
4. Sky worker picks it up, also fails (same unreachable URL)
5. `adjudication_unavailable` is returned with `deterministic_decision_used: True`
6. Homework continues to projection phase using deterministic fallback

**Pass criteria:** No hang. Completes in under 15 minutes total. Reports `adjudication_unavailable` honestly.

---

### 3.4 — RAM pressure during homework (>90% utilization)

Monitor RAM via `psutil` during a full homework run. If RAM exceeds 92% at any point:
- Verify no `0xC000012D` errors (Defender exclusions applied)
- Verify the OCR phase doesn't OOM-kill the subprocess
- Verify Ollama saturation guard (`APOLLO_OLLAMA_MAX_QUEUE=4`) fires `model_busy` rather than hanging

**Mitigation checklist before run:**
- [ ] Discord running in browser (not desktop app) — saves ~700MB
- [ ] Defender exclusions applied via `add_defender_exclusions.bat`
- [ ] `louisa-employee-vm` container stopped if Louisa is offline

---

### 3.5 — Sky queue concurrent jobs

Submit 3 GB10 jobs simultaneously via the queue:

```bash
curl -X POST http://127.0.0.1:5011/gb10/enqueue -H "Content-Type: application/json" \
  -d '{"prompt": "test 1", "model": "qwen2.5:72b"}'
curl -X POST http://127.0.0.1:5011/gb10/enqueue -H "Content-Type: application/json" \
  -d '{"prompt": "test 2", "model": "qwen2.5:72b"}'
curl -X POST http://127.0.0.1:5011/gb10/enqueue -H "Content-Type: application/json" \
  -d '{"prompt": "test 3", "model": "qwen2.5:72b"}'
```

Poll `/gb10/queue/status`. Verify:
- `depth` counts correctly
- Jobs run sequentially (worker is single-threaded)
- All three jobs eventually reach `status: done` or `status: error`
- No deadlock (both `_jobs_lock` and `_work_queue` unblocked)
- TTL eviction: jobs older than `SKY_GB10_JOB_TTL_SEC` disappear from `/gb10/queue/status` counts

---

## Layer 4 — Ceiling Push

These tests probe the upper capability boundary of the system.

### 4.1 — Tier advancement: AMD paper_pressed_aggressive_probe

**Conditions required:**
- Market regime score ≥ 60
- AMD score ≥ 65 (currently 72.55 — already met)
- AMD rr ≥ 2.0 (currently 3.27 — already met)
- AMD graph_asymmetry_score ≥ 65 (currently 87.3 — already met)
- `pressed_candidate == True` in AMD's `aggressive_reward_profile`
- Adjudication returns `approve` or `probe_approved`
- OCR97 `quality_confident == True` (fixed Jun 26)

**Test procedure:**
1. Identify a day when VIX < 20 and AMD shows price/volume confirmation
2. Run the full homework
3. Verify `pressed_aggressive: True` in trade cycle output
4. Verify `primary_action == "paper_pressed_aggressive_probe"`
5. Verify `risk_posture == "pressed_simulation_only"`

**This is the benchmark for a fully functional run.** AMD hit every fundamental gate (72.55 score, 3.27 RR, 87.3 graph asymmetry) on Jun 25. The only thing blocking it was regime (25/100) and OCR97 (25/100, now fixed). This test confirms the ceiling is reachable.

---

### 4.2 — CRWD independent confirmation

CRWD `score=70.02`, `rr=2.15`, `graph_asym=73.64`. Marginally weaker than AMD but still above pressed thresholds.

**Test:** In the same run as 4.1, verify CRWD also appears in `pressed_candidates`. A run where both AMD and CRWD are pressed candidates on the same night is a high-confidence signal.

**Pass criteria:** Both tickers in `pressed_candidates`, not just AMD.

---

### 4.3 — HippoRAG graph augmentation quality

Current behavior: `graph_usefulness: supporting_context_only` (last night). This caps confidence.

**Test:** Ingest 10+ AMD-specific earnings transcripts or SEC filings into the financial corpus, rebuild the KG:

```bash
python -m apollo_hipporag.build_graph --rag-dir ./chroma_db --use-llm
```

Re-run the homework enrichment phase. Verify:
- `label` changes from `supporting_context_only` to `graph_augmented`
- `graph_usefulness` score increases
- This removes `hipporag_usefulness` from the blocker hierarchy

This is a real document ingestion task, not a code fix. The pipeline is ready; the corpus is synthetic.

---

### 4.4 — Full simulation paper trade execution gate

**This tests the human-approval gate and simulation broker.**

Set `APOLLO_SIM_EXECUTION_MODE=paper` and `APOLLO_SIM_LIVE_ENABLED=0` (already defaults).

1. Get the system to `paper_pressed_aggressive_probe` state (see test 4.1)
2. Confirm `human_approval_required: True` in report
3. Manually trigger approval via `POST /admin/trading_homework/run` with `force_submit: true`
4. Verify the simulation broker logs a paper trade (not a live order)
5. Verify `APOLLO_SIM_ALLOW_FORCE_WITHOUT_APPROVAL=0` blocks submission without approval

**Pass criteria:** Paper trade logged. No live order. Approval gate enforced.

---

### 4.5 — OCR97 GB10 benchmark score ≥ 90

The Jun 24 benchmark scored 100 (average) and 81 (computed). The `recommended_score` determines whether `quality_confident=True`.

**Test:** Run the full benchmark against the real document manifest:

```bash
python tools/run_ocr97_real_doc_benchmark.py \
  --manifest config/ocr97_real_documents_manifest.json \
  --update-readme
```

**Pass criteria:**
- `average_score ≥ 90`
- `minimum_score ≥ 80`
- `recommended_score ≥ 86`
- `failures == 0`
- After completion, capability ledger `latest_score ≥ 86` (via the registration fix)
- Next day's `initial_tracking` inherits the real score (regression check)

---

## Regression Checklist (run before every nightly homework)

These are the specific regressions that have occurred and must not recur:

- [ ] **OCR97 score 25** — Ledger `latest_score` must be ≥ 85 and `latest_status: complete`
- [ ] **`parse_ok: False` from `<think>` blocks** — Parser strips think blocks before JSON parse
- [ ] **GB10 120s timeout crash** — Timeout is now 600s; gate waits up to 300s before adjudication call
- [ ] **`provider_unreachable_ollama` misclassified** — Fallback code detected; adjudication skipped, not parsed
- [ ] **`0xC000012D` process crash** — Defender exclusions applied; RAM below 90% before run
- [ ] **Trade cycle hung on GB10 busy** — Queue fallback active; Sky blueprint registered at `/gb10/*`
- [ ] **Daily `initial_tracking` overwrites real score** — Benchmark registers to ledger before tracking runs

---

## Running Order (per nightly cycle)

```
1. [~10:00 PM] Check RAM usage — must be < 90% (psutil check)
2. [~10:00 PM] Verify GB10 is reachable: curl http://127.0.0.1:11435/api/ps
3. [~10:00 PM] Verify OCR97 ledger is fresh: check latest_completed_at < 7 days ago
4. [~10:15 PM] Start OCR97 real-doc benchmark (if not run in last 72h)
5. [~11:15 PM] Start nightly homework run (after OCR97 benchmark completes)
6. [~11:40 PM] Check report: top_blocker, adjudication decision, AMD/CRWD tiers
7. [~11:45 PM] Verify OCR97 score propagated to ledger (regression check)
8. [Next AM]   Review morning report, check stress sandbox, confirm primary_action
```

---

## Pass/Fail Summary

| Test | Priority | Blocker? | Automated? |
|---|---|---|---|
| 0.1 adjudication parse cases | P0 | Yes | Yes — unit test |
| 0.2 OCR97 ledger gate | P0 | Yes | Yes — unit test |
| 0.3 blocker hierarchy order | P1 | No | Yes — unit test |
| 0.4 paper screen thresholds | P0 | Yes | Yes — unit test |
| 0.5 GB10 gate logic | P1 | No | Yes (mock) |
| 1.1 OCR97 → ledger flow | P0 | Yes | Semi-manual |
| 1.2 adjudication 3-layer | P0 | Yes | Manual (needs GB10) |
| 1.3 phase data contracts | P1 | No | Semi-automated |
| 1.4 confidence model | P1 | No | Yes — unit test |
| 2.1 baseline full run | P0 | Yes | Manual (nightly) |
| 2.2 regime-favorable run | P1 | No | Manual (env override) |
| 3.1 GB10 contention | P0 | Yes | Manual (stress) |
| 3.2 bad OCR input | P1 | No | Manual (inject) |
| 3.3 GB10 unreachable | P0 | Yes | Manual |
| 3.4 RAM pressure | P1 | No | Monitor during run |
| 3.5 queue concurrent jobs | P1 | No | Manual (curl) |
| 4.1 AMD tier advancement | P0 | Yes | Manual (regime-gated) |
| 4.2 CRWD confirmation | P1 | No | Covered in 4.1 |
| 4.3 HippoRAG augmentation | P2 | No | Requires doc ingestion |
| 4.4 simulation paper trade | P1 | No | Manual |
| 4.5 OCR97 score ≥ 90 | P0 | Yes | Scheduled nightly |

**P0 = must pass before any trade tier advances**  
**P1 = should pass; failure means investigation, not full stop**  
**P2 = capability ceiling; failure means feature is incomplete**
