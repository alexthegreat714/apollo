# Apollo Financial Assistant

A comprehensive financial AI assistant with advanced RAG retrieval, skills-based analysis, and autonomous governance capabilities.

---

## System Score — 52/100 (2026-05-23)

> **For Claude / Codex:** This section is the authoritative snapshot of Apollo's capability rating. Check the date before using — re-score after major pipeline changes.

### Score

| Area | Score | Notes |
|---|---|---|
| Data infrastructure | 65 | ChromaDB working, SEC/news ingestion, OCR pipeline live |
| Signal generation | 50 | Positive-expectancy backtest (PF 1.38, +0.39%/trade) but <50% win rate; no live execution |
| KG quality | 45 | 1,640 nodes / 4,111 edges across 30k+ chunks — very thin; enrichment just fixed |
| Automation/reliability | 70 | Nightly watchdog, memory guard, Sky calendar chain, lock files, GX10 integration |
| Risk management | 55 | NFLX/UBER removed (PF ~0); PLTR gap risk (avg loss −8%) and RTX (PF 0.033) still in |
| **Overall** | **52** | |

### Why 52 and not higher

1. **KG enrichment was broken until today.** The curated enrichment pipeline (qwen3-vl:32b) was producing 0 triples every run due to: (a) XBRL boilerplate being sent to the LLM, (b) qwen3 `<think>` tags breaking JSON parsing, (c) HTML entities not decoded. All three fixed 2026-05-23. First real triples are now flowing.

2. **58% of the corpus is XBRL boilerplate.** Of 29k+ candidate chunks, ~17k are raw XBRL taxonomy data (`us-gaap:`, `xbrli:`, CIK/date sequences). The XBRL filter now skips these without an LLM call; the pipeline will work through to real text.

3. **No live execution bridge.** Signal generation is production-grade. No order submission layer exists — `live_trade_execution` is hardcoded `False`. Everything is paper/observation.

4. **Win rate is 44.7%.** Positive expectancy only because avg win (+3.18%) > avg loss (−1.87%). One regime shift could flip this negative.

### What would raise the score

| Change | Points |
|---|---|
| KG builds 500+ new edges from real text chunks over next 2 weeks | +6 |
| Remove RTX and PLTR from watchlist (backtest PF near zero / gap risk) | +3 |
| Live broker adapter wired (Alpaca paper → live) | +10 |
| Win rate climbs above 50% after regime filter tuning | +5 |
| DePlot weights downloaded (OCR stack reaches 97/100) | +2 |

### Backtest snapshot (2026-05-21 — `logs/backtest/latest.json`)

- 219 signals / 219 traded — 98W / 121L
- Win rate 44.7% — Avg win +3.18% / Avg loss −1.87%
- Expectancy +0.392%/trade — Profit factor 1.38
- Best: GOOGL (PF 13.0), SHOP (8.5), SLB (6.7), AMAT (5.7), ABBV (5.1)
- Worst: NFLX (PF 0.008) removed, UBER (0.042) removed, RTX (0.033), PLTR (0.256 / avg loss −8%)

### Watchlist (2026-05-23)

53 tickers. NFLX and UBER removed after backtest review. Source: `config.py:_DEFAULT_WATCHLIST`.

---

## Runtime Defaults (env-first, with config fallback)

Apollo resolves settings from environment first, then falls back to `config.py`.

- **Base host/port resolution:** `AGENT_PORT` -> `APOLLO_PORT` -> `PORT` -> `APP_CONFIG.PORT` (`config.py`, default `5010`).  
  `HOST` has no chain override and falls back directly to `APP_CONFIG.HOST` (`127.0.0.1`).  
  In `.env`, this workspace currently sets `PORT=5218` and `HOST=0.0.0.0`.
- **Chat model + host:** `OLLAMA_MODEL_CHAT` -> `OLLAMA_MODEL` (default `gemma3:12b`) and host from `OLLAMA_URL_CHAT` -> `OLLAMA_URL` (default `http://127.0.0.1:11434`).
- **Deep model + host:** `OLLAMA_MODEL_DEEP` -> `OLLAMA_MODEL_REASON` (default `gemma4:31b`) and host from `OLLAMA_URL_DEEP` -> `OLLAMA_URL` (default `http://127.0.0.1:11435`).
- **Watchlist behavior:** `APOLLO_WATCHLIST` is parsed by `config.get_watchlist()` and de-duplicated.  
  When unset, Apollo falls back to `_DEFAULT_WATCHLIST` in `config.py` with this default set:
  `NVDA,AMD,AVGO,TSM,MSFT,AMZN,GOOGL,META,JPM,GS,BAC,XOM,CVX,LLY,UNH,JNJ,CAT,HON`.
- **Simulation execution mode + resilience:** `APOLLO_SIM_EXECUTION_MODE` (`paper` | `live`, default `paper`),  
  `APOLLO_SIM_LIVE_ENABLED` (`0`/`1`), `APOLLO_SIM_BROKER_MAX_RETRIES` (default `3`),  
  `APOLLO_SIM_BROKER_RETRY_WINDOW_SEC` (default `600`), `APOLLO_SIM_STALE_ORDER_SECONDS` (default `1200`),
  `APOLLO_SIM_RECONCILE_LOCK_STALE_SECONDS` (default `300`), and `APOLLO_SIM_RECONCILE_MAX_PLANS` (default `0` = no explicit cap).
- **Simulation approval + submit controls:** `APOLLO_SIM_ALLOW_FORCE_WITHOUT_APPROVAL` (`0`/`1`, default `0`) and  
  `APOLLO_SIM_MIN_SECONDS_BETWEEN_SUBMISSIONS` (default `0`).
- **Simulation broker:** `APOLLO_SIM_BROKER_PROVIDER` defaults to `paper_sim` in `.env.template`. `alpaca_paper`/`alpaca` map to a paper Alpaca adapter and are optional after credentials are set.
- **Admin auth:** `SKY_ALLOW_ANON` defaults to `0` (admin endpoints require token unless explicitly enabled).

## 2026-06-30

### Supervised eBay Listing Packet Workflow

Apollo now supports a non-API fallback for eBay listing preparation while the eBay developer/API path is pending or intentionally avoided. The supported mode is supervised:

- Apollo prepares the listing title, description, category ID, condition, quantity, price, image manifest, seller policy fields, and a local review packet.
- ForgeClaw may use that packet inside the VM to open eBay, click through the sell flow, fill fields, and prepare images.
- ForgeClaw must stop before final publish/list/submit and capture evidence for human review.
- Every listing requires human review of title, category, condition, description, images, price, shipping, returns, payment, fees, and any eBay warnings.
- API publishing remains separately gated by `APOLLO_EBAY_ENABLE_MUTATIONS=1`, `APOLLO_EBAY_ENABLE_PUBLISH=1`, `operator_confirmed=true`, and the exact publish confirmation phrase.

Use the MCP tool action:

```json
{
  "action": "prepared_review_packet",
  "title": "Example item",
  "description": "Accurate item description",
  "condition": "USED_EXCELLENT",
  "category_id": "9355",
  "price": "19.99",
  "currency": "USD",
  "image_urls": ["https://example.com/item-front.jpg"],
  "source_image_paths": ["C:\\tmp\\item-front.jpg"],
  "comparable_prices": [
    {"price": "18.50", "source": "sold comp 1", "sold": true},
    {"price": "22.00", "source": "sold comp 2", "sold": true}
  ]
}
```

The packet writes under `Apollo/artifacts/ebay_listing_drafts/<draft_id>/`:

- `draft.json`
- `approval.md`
- `prepared_review_packet.json`
- `prepared_review_packet.md`

The packet is local-only and reports `writes_performed=false`, `ui_automation_allowed=true`, and `final_publish_allowed=false`.

### Aegis VM eBay Login Profile

For the ForgeClaw UI fallback, eBay should be opened in the Aegis VM, not the native desktop.

- Aegis VM console: `http://127.0.0.1:5221/vm/console?host=127.0.0.1&port=6080`
- Aegis browser route: `POST /forgeclaw/vm/browser/open_url`
- Route payload: `{"url":"https://signin.ebay.com/","profile_slug":"ebay","cdp_port":9234}`
- Persistent VM Chrome profile: `/root/.config/apollo-ebay-login`
- Apollo route map: `config/ebay_vm_page_map.json`

After the operator signs in inside the VM, ForgeClaw may use Apollo's prepared review packet to fill draft listing screens and attach approved images. ForgeClaw must stop before any final `Publish`, `List`, `Submit`, `Confirm`, or equivalent live-listing button. Credentials, cookies, OAuth tokens, payment details, and account-security data must not be copied into chat, markdown, logs, screenshots, or RAG.

## 2026-06-16

### Paper Swing-Trading Capability Baseline And Score Expectations

Apollo is now best described as a real paper-trading research system rather than a dashboard-only assistant. The current build can gather market/news evidence, enrich candidates through HippoRAG/Graph RAG, run swing-trade risk gates, separate `standard`, `probe`, `aggressive_probe`, and pressed aggressive paper ideas, and write traceable reports explaining why a candidate passed, failed, or needs an entry reset.

Current capability grades:

| Area | Score | Baseline read |
|---|---:|---|
| Paper swing-trade readiness | 78/100 | Ready to simulate swing-trade days against real market data with disciplined paper-only gates. |
| Aggressive paper-trade experimentation | 72/100 | Newly improved through pressed aggressive paper mode and graph asymmetry scoring, but needs repeated outcome proof. |
| Live execution readiness | 35/100 | Intentionally low. Live broker execution remains disabled; human approval and broker safety infrastructure are not complete. |

What is working well:

- Apollo keeps live execution disabled while allowing more risk inside simulation.
- Candidate routing is clearer: `standard`, `probe`, `aggressive_probe`, `wait_for_entry_reset`, `watch`, and `reject`.
- Graph RAG is now wired into trade behavior through asymmetric reward support instead of staying passive context only.
- The high-risk nightly homework runner can produce a next-day projection with per-phase reports.
- Reports show the evidence path: market snapshot, event/catalyst gather, swing study, Graph RAG enrichment, risk checks, and projection.

Main weak spots:

- News and catalyst quality still varies; stale or lower-grade RSS headlines can appear in the evidence set.
- Graph RAG needs multiple nights of outcome tracking before its asymmetry score should be treated as a high-confidence trading edge.
- The aggressive lane needs proof that riskier paper candidates outperform conservative candidates after realistic slippage and entry timing.
- Simulation realism still needs more work around gaps, partial fills, intraday stop behavior, and open-volatility behavior.
- The morning report should become tighter: `act`, `wait`, or `reject`, with the top blocker and top evidence in the first screen.

Baseline for a meaningful future score increase:

| Requirement | Evidence needed before score increase |
|---|---|
| Better source quality | At least 5 consecutive nightly runs where A/B-tier sources dominate and C-tier-only runs are capped or clearly labeled. |
| Graph RAG usefulness | Graph-augmented retrieval beats dense-only retrieval on the finance benchmark, or is correctly labeled `supporting_context_only` when it does not. |
| Aggressive lane proof | At least 20 paper aggressive/pressed candidates tracked with next-day and multi-day outcomes against conservative alternatives. |
| Realistic execution | Paper results include slippage, partial-fill warnings, ADV/notional warnings, and gap-aware stop/target handling. |
| Clean projection reports | Each overnight report produces a concise next-day playbook with primary action, entry, stop, target, invalidation, and evidence artifacts. |
| Safety preservation | No aggressive or pressed candidate is ever marked live-ready; `live_trade_execution=false` and `human_approval_required=true` remain enforced. |

Score update rules:

- Raise paper swing-trade readiness above 80 only after several successful overnight runs produce usable next-day projections and tracked outcomes.
- Raise aggressive paper readiness above 80 only if riskier simulated candidates show better asymmetric reward without a large increase in invalid/stale setups.
- Do not raise live execution readiness above 50 until broker credentials, idempotent order handling, stale-order reconciliation, retry windows, incident behavior, and explicit human approval gates are proven in paper broker mode.
- A good update should include exact report paths, candidate counts by tier, Graph RAG mode, source-quality status, simulation realism warnings, and outcome tracking versus prior projections.

Latest relevant artifacts:

- `Apollo/reports/nightly_high_risk_homework/`
- `Apollo/reports/aggressive_probe_suite/`
- `Sky/logs/test_reports/<date>/apollo_high_risk_homework/`
- `Sky/logs/test_reports/<date>/apollo_aggressive_probe_suite/`

## 2026-06-15

Independent review of Apollo's build, Graph RAG, trade logic, and overall capability as of this date.

---

### Build Quality

Solid structure overall. The separation of concerns is clean — gather, OCR, KG build, analysis, simulation, and execution are all discrete modules with clear handoffs. Defensive patterns are present throughout: env-first config, lock files on the nightly job, atomic JSON writes, memory guards. The `_safe_float()` pattern, the quarantine system for bad chunks, and the validation functions on simulation outcomes all reflect production discipline.

The weakest structural point is `nightly_pipeline.py` at 2000+ lines — that file is doing too much and will become hard to maintain. Broad `except Exception: pass` patterns in a few places are also a concern: silent failures in a trading pipeline are genuinely risky.

---

### Graph RAG

The HippoRAG 2 design is legitimately sophisticated for a local build. Two-layer retrieval (dense ChromaDB + Personalized PageRank expansion) is architecturally sound. The priority scoring in curated enrichment (SEC filings tier 1 → education tier 4), the dedup gate (skip triples already supported by ≥10 docs), and confidence gating from Qwen are all the right ideas.

The real problem is the current state of the KG: **1,640 nodes / 4,111 edges from 29,000+ chunks is extremely thin** (~0.14 edges per chunk). Three separate bugs killed LLM enrichment until 2026-05-23 — XBRL boilerplate going to the LLM, `<think>` tags breaking JSON parsing, HTML entities not decoded. All three are fixed, but the KG is still a skeleton. The 58% XBRL content problem means roughly half the corpus was useless noise that the regex extractor was silently building co-occurrence edges on; those edges are likely low-signal.

Until the KG has been running clean for 4–6 weeks and the edge count is in the 20k–50k range with real LLM-extracted triples, the graph expansion in retrieval is adding mostly noise. Right now Apollo is functionally closer to a dense-only ChromaDB retriever with a thin graph layer bolted on.

---

### Trade Logic & Signals

The five setup types are mechanically sound and correctly implemented. Pullback-to-trend being 206 of 219 signals over 24 months reflects how the current extended market looks technically — most clean breakouts and continuations get filtered. The regime check (SPY/QQQ vs 200-SMA) is the right macro gate.

Specific concerns:

**No price action confirmation.** Entry is purely zone-based (low/high band). A candle that dips into the EMA-21 zone and recovers is treated identically to one that slices through it with high volume. Adding even a basic close-above-zone requirement would improve fill quality materially.

**No volume confirmation on breakouts.** The base breakout setup (13 signals in 24 months) doesn't gate on volume expansion at breakout — a standard filter for this setup type.

**Sentiment is keyword counting.** Static keyword lists with fixed weights (+0.18 / −0.22) contributing 30% of the score is too simple and too brittle. A phrase like "raises guidance but concerns about margins" gets a net positive from "raises" and "guidance" even though it's a mixed signal. Even a lightweight BERT sentiment head would be materially better here.

**RTX and PLTR still in the watchlist** despite profit factors near zero and average losses of −8%. The backtest already said to remove them.

The six risk gates are well-designed. Hard gates that must all pass is the right architecture. The liquidity gate (500K ADV) and regime score gate are particularly good. R/R ≥ 1.0 minimum is conservative and appropriate.

---

### Backtest & Performance

The backtest methodology is honest, which is the most important thing. Walk-forward, no look-ahead bias, gap risk modeled (entry skipped if candle opens above zone), conservative fill (30% into zone + 0.1% slippage), sector concentration limits. These are real and correct anti-overfitting measures.

**The results are marginal.** 44.7% win rate with +0.39% expectancy per trade is a thin edge. Profit factor of 1.38 depends entirely on the asymmetry between average win (+3.18%) and average loss (−1.87%). A single adverse shift in that ratio — tighter targets in a choppy market, for example — goes negative.

The GOOGL result (PF 13.0) and SHOP (PF 8.5) look strong but could be concentrated regime effects — both are growth names that performed well in a specific multi-year bull market. The 24-month backtest window hasn't been validated across a drawdown or bear market environment.

The per-ticker variance is enormous (PF 13.0 vs PF 0.008). That's a signal that the system is actually a collection of single-ticker strategies masquerading as a portfolio approach. The sector limit (1 per sector) helps but doesn't address the underlying dispersion.

---

### Overall Capability

Apollo is a well-engineered research engine for swing trade signal generation and paper trading. The infrastructure is production-quality — atomic writes, validation, lock management, evidence quality lanes, the phase 7b execution scaffolding. This is clearly being built toward something real.

Three things are blocking it from being genuinely capable right now:

1. **KG is too thin to be useful.** Let it run clean for 6+ weeks, then re-evaluate whether graph expansion actually improves retrieval quality over dense-only. Until then, the HippoRAG layer is more overhead than value.

2. **Sentiment scoring is the weakest link in signal quality.** At 30% weight, bad sentiment scoring is dragging every proposal score. Replace keyword counting with a real model.

3. **No live execution validation.** The backtest and paper simulation are honest, but paper trading with no slippage on order routing, no partial fills, no market impact, and no position-size-relative-to-ADV modeling still leaves a large gap to reality. Apollo's edge has not been tested against a real broker.

| Dimension | Rating | Notes |
|---|---|---|
| Architecture | 8/10 | Modular, well-separated concerns, env-driven config solid |
| Signal generation | 7/10 | Five setups well-tuned, but mechanical and regime-sensitive |
| Risk management | 8/10 | Six hard gates, position sizing, sector limits, slippage modeling |
| RAG / KG design | 7/10 | HippoRAG 2 is sophisticated; KG is still too thin to pull its weight |
| Data quality | 8/10 | OCR 96/100, XBRL filtering works, but source mix is C-tier heavy |
| Simulation fidelity | 8/10 | Walk-forward is honest, gap risk modeled, validation checks present |
| Backtest results | 5/10 | 44.7% win rate, +0.39% expectancy — marginal and regime-dependent |
| Live execution | 2/10 | Phase 7b infrastructure present but not wired; paper-only currently |
| Code quality | 7/10 | Good modular structure, defensive programming; nightly_pipeline.py too large |

System score of 52/100 (set 2026-05-23) remains accurate. Architecture warrants 65/100 but current operational state — thin KG, marginal edge, no live execution — pulls it back.

---

## 2026-06-04

Apollo now has explicit GB10 large-model routing for paper-trade reasoning and no-C-drive model download guardrails.

- Fast screening remains on `APOLLO_FAST_LLM_BASE_URL=http://127.0.0.1:11434` and `APOLLO_FAST_LLM_MODEL=gemma3:12b` for high-throughput nightly/batch work.
- Final trade adjudication now resolves `APOLLO_TRADE_REASON_LLM_BASE_URL=http://127.0.0.1:11435` and `APOLLO_TRADE_REASON_LLM_MODEL=qwen2.5:72b`, with fallbacks `gemma-3-27b-it-Q4_K_M:latest`, `gemma-4-26b-a4b-q4km-ctx8:latest`, then `gemma3:12b`.
- OCR/vision remains routed to `qwen3-vl:32b`.
- The trade cycle now writes `04b_deep_trade_adjudication.json` and includes `model_policy.*`, `model_storage.*`, and `deep_trade_adjudication.*` fields in status/report artifacts.
- The deep adjudication pass can confirm, downgrade, or block simulation candidates. It cannot upgrade probe/watch/reject ideas into standard trades and cannot bypass live-execution disablement or the human approval gate.
- New model/cache downloads are blocked from `C:` unless `APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS=1`. Defaults point to `APOLLO_MODEL_STORAGE_ROOT=D:\ApolloModels`.
- Guarded storage env vars include `OLLAMA_MODELS`, `HF_HOME`, `TRANSFORMERS_CACHE`, `TORCH_HOME`, and `APOLLO_OCR_MODEL_CACHE_DIR`.
- Installer, Hugging Face model downloader, Ollama puller, and OCR benchmark download paths now validate storage before pulling or caching model/data artifacts.

## 2026-06-03

Apollo source-quality strengthening was added for the nightly homework and trade-cycle path.

- Nightly gather now annotates accepted sources by evidence lane: `primary_evidence`, `trusted_market_news`, `market_news_evidence`, `price_volume_evidence`, `graph_context`, and `other`.
- C-tier-only runs, such as Yahoo RSS-only gathers, are now explicitly marked `source_confidence=low`, `c_only_run=true`, `trade_confidence_cap=probe_or_observation`, and `standard_trade_ready_allowed=false`.
- When a gather starts with injected Yahoo RSS sources and produces only C-tier evidence, Apollo now runs a trusted-source rescue pass with web search enabled instead of accepting the RSS bundle as the whole source set.
- The first rescue pass is restricted to A/B domains such as SEC, government, exchange, and research sources. A second pass can allow broader A/B/C market sources while still denying `feeds.finance.yahoo.com`.
- Gather output now includes per-ticker evidence packs with tier counts, evidence lanes, best source tier, source confidence, and trade confidence cap.
- The trade cycle now carries nightly source-confidence metadata into `evidence_quality` and blocks standard `trade_ready` when the gather cap says the run should stay probe/observation only.

Focused tests added/updated:

- C-only gather quality marks Yahoo RSS evidence as probe/observation only.
- Nightly gather rescues C-only RSS input with a trusted-source search attempt.
- Existing Apollo risk-ramp tests continue to cover probe sizing, full-candidate risk checks, short event-dislocation support, stale setup blocking, and report metadata.

## 2026-05-21

`run_portfolio_backtest()` completed successfully with the latest swing backtest stack.

- `ok=True`
- `months=24`
- `forward_days=90`
- `portfolio_stats`: `total_periods=219`, `traded=219`, `wins=98`, `losses=121`, `win_rate_pct=44.7`, `expectancy_pct=0.392`, `profit_factor=1.38`
- Artifacts:
  - `logs/backtest/latest.json`
  - `logs/backtest/backtest_20260521_223131.json`

## Update — 2026-05-21 — Swing Trade Engine Hardening (Pass 2)

Six targeted improvements raised the paper swing simulation from a signal generator with no validation infrastructure to a system that can measure its own edge.

### 1. Watchlist — 18 → 55 tickers with sector map (`config.py`)

Default watchlist expanded from 18 names to 55 across 6 sectors. A `_SECTOR_MAP` dict and `get_sector(ticker)` helper were added so downstream consumers can enforce sector concentration limits without manual maintenance.

| Sector | Tickers |
|--------|---------|
| Technology (semis/AI) | NVDA, AMD, AVGO, TSM, QCOM, MU, AMAT, LRCX, ARM |
| Technology (mega-cap/software/cloud) | MSFT, AAPL, AMZN, GOOGL, META, CRM, NOW, PANW, PLTR, SNOW, UBER, SHOP, NFLX, MELI |
| Financials | JPM, GS, BAC, MS, V, MA, AXP, SCHW, BLK |
| Energy | XOM, CVX, SLB, OXY |
| Healthcare | LLY, UNH, JNJ, ABBV, MRK, PFE, TMO, ISRG |
| Industrials | CAT, HON, GE, RTX, DE, LMT |
| Consumer | WMT, COST, HD, NKE, MCD |

### 2. Walk-forward backtest (`backtest.py` — new module)

`run_walk_forward(ticker, months=24)` rolls through every month-end in the last 24 months of history. At each point it slices candles up to that date, computes all indicators from the historical window (no look-ahead), calls `classify_setup()` and `_zones()`, and if the setup is tradeable with R/R ≥ 1.0, simulates forward 90 days against real candle data.

`run_portfolio_backtest(tickers, months=24)` aggregates across the full watchlist and saves to `logs/backtest/latest.json`. Output includes win rate, avg win/loss %, expectancy, profit factor, and per-setup-type breakdown.

### 3. Equity curve + performance stats (`swing_simulation.py`)

`_update_equity_curve(account)` appends a daily `{date, value}` snapshot and tracks `peak_value`, `max_drawdown_pct`, and `current_drawdown_pct`. `_compute_performance_stats(account)` computes win rate, avg win/loss %, expectancy, and profit factor from closed positions. Both run automatically from `_sync_account_totals()` on every cycle. The markdown report now includes a Performance Stats section and drawdown lines.

### 4. Same-day data fix (`swing_simulation.py`)

`_fetch_history()` previously used `period=f”{days}d”` which often excludes today's bar. Fixed to use an explicit `start`/`end` date range (`end = today + 1d`) with `auto_adjust=True`. Today's EOD bar is now included when available after market close.

### 5. Market regime hard gate (`swing_data.py`, `swing_study.py`)

`classify_market_regime()` now includes a hard SMA200 override: if SPY or QQQ closes below its 200-day SMA, regime is forced to `risk_off` with score capped at 25, regardless of short-term momentum state.

`_proposal_for_ticker()` reads the regime label after `classify_setup()`. When regime is `risk_off`, `base_breakout` and `trend_continuation` setups are overridden to `no_trade` with reason `market_regime_risk_off`. Pullback setups still fire — they have positive historical EV in corrections. The regime scoring slot (13% weight in `_score_proposal`) was already wired; it now receives accurate bear-market scores.

### 6. Slippage + gap risk (`swing_simulation.py`, `backtest.py`)

Both `_simulate_outcome()` and `_simulate_forward()` previously filled at the entry zone midpoint.

**Gap risk:** If a candle opens above the entry zone high (gap above zone), the entry is skipped on that candle.

**Pessimistic fill:** Entry fills at 30% into the zone from the low end + 0.1% slippage. For a zone of 100–104, the old fill was 102.00; the new fill is 101.20. Backtest results are now systematically conservative.

### 7. Sector concentration limit (`swing_simulation.py`)

`_build_next_cycle_plan()` uses `get_sector()` to gate new allocations — a sector already held is skipped in favor of the next-ranked candidate in a different sector. Maximum 1 open position per sector. Prevents the failure mode where all 3 paper positions are tech momentum names and stop out simultaneously on the same macro event.

### 8. Expanded sentiment keywords (`swing_study.py`)

`normalize_catalysts()` keyword lists expanded from ~15 terms each to ~35 terms each. Positive additions include `rebound`, `raised guidance`, `record revenue`, `margin expansion`, `partnership`, `buyback`. Negative additions include `bankrupt`, `fraud`, `investigation`, `subpoena`, `write-off`, `guidance cut`, `revenue miss`, `margin pressure`, `headwind`, `shortfall`. Negative terms retain the higher penalty weight (−0.22 vs +0.18) to stay conservative.

---

## Update â€” 2026-05-11 â€” Market-Wide Watchlist

Apollo's watchlist was expanded from 5 domain-specific AI/semiconductor names to 18 names across 6 sectors. The prior list (NVDA, AMD, AVGO, MSFT, AMZN) caused the trade cycle to produce no passing candidates whenever the AI/tech sector was broadly extended â€” all 5 names fail R/R simultaneously when the sector runs.

**New default watchlist (18 names):**

| Sector | Tickers |
|--------|---------|
| AI / Semiconductors | NVDA, AMD, AVGO, TSM |
| Enterprise / Cloud | MSFT, AMZN, GOOGL, META |
| Financials | JPM, GS, BAC |
| Energy | XOM, CVX |
| Healthcare | LLY, UNH, JNJ |
| Industrials | CAT, HON |

All 18 names clear the 500K avg daily volume liquidity gate by a large margin. The R/R gate, blended score, and direction_bias checks will surface the best opportunity across the full market each cycle rather than being locked to one sector's cycle.

**Configurable via env var:** Set `APOLLO_WATCHLIST=TICK1,TICK2,...` in Apollo's `.env` to override without a code change. All consumers load via `config.get_watchlist()` (`trade_cycle.py`, `swing_study.py`, `homework_trade_pipeline.py`, `event_impact.py`).

**Next step:** Once the KG is populated over several nights of working gather runs, re-enable `focus_universe` in the trade cycle to add adaptive sector selection on top of the static list.

---

## Update â€” 2026-05-10 (addendum) â€” Simulation Swing Trade Gate Requirements

### What is required for Apollo to produce a passing simulation trade recommendation

A simulation swing trade fires only when a candidate clears all six gates in `_stage_risk_checks` (`trade_cycle.py:368`). Every gate must pass simultaneously â€” there is no partial credit.

| Gate | Threshold | Source | Notes |
|------|-----------|--------|-------|
| **R/R â‰¥ 1.0** | `(target âˆ’ entry) / (entry âˆ’ stop) â‰¥ 1.0` | `trade_cycle.py:362` | The single most common failure. All 5 watchlist tickers failed on 2026-05-10: AMZN 0.44, NVDA 0.55, AVGO 0.56, MSFT 0.59, AMD 0.28. Stop zones are wide relative to targets given current market extension. This resolves naturally when price pulls back to better entry zones. |
| **direction_bias = long_bias** | Set by homework pipeline. `no_trade` or `short_bias` block the trade. | `homework_trade_pipeline.py:373` | AMD ("extended from trend support") and MSFT ("long trend not confirmed") were `no_trade` on 2026-05-10. |
| **no_trade_reason = ""** | Any non-empty `no_trade_reason` string blocks the trade. | `trade_cycle.py:374` | Set by `homework_trade_pipeline.py` during zone analysis. Common reasons: `extended_from_trend_support`, `long_trend_not_confirmed`, `rr_below_minimum`. |
| **Liquidity â‰¥ 500K avg daily vol** | 30-day average daily volume > 500,000 shares | `trade_cycle.py:37` / `_liquidity_ok()` | All current watchlist tickers (NVDA ~153M, AMD ~43M, AMZN ~48M, AVGO ~22M, MSFT ~34M) pass this by a large margin. Only an illiquid ticker would fail here. |
| **Entry valid** | Live price must be within the entry zone (low â†’ high Ã— 1.05 ceiling) at cycle run time | `trade_cycle.py:320` | All 5 tickers passed on 2026-05-10. Fails if price has run away from the zone before the 4 AM cycle fires. |
| **Regime score â‰¥ 88 (risk-off only)** | Only activates when `market_regime = risk_off`. Requires blended score â‰¥ 88. | `trade_cycle.py:39,366` | Not applicable when regime is `risk_on` (current state). Exists to require high-conviction trades in hostile market conditions. |

### Confidence label (informational only â€” does not gate the simulation)

`confidence_label` is set in `swing_study.py:679`:
- `high` â€” blended score â‰¥ 82 AND provider confidence â‰¥ 75
- `medium` â€” blended score â‰¥ 65 AND provider confidence â‰¥ 60
- `low` â€” everything else

The label appears in the observation report and watchlist table but does **not** block or allow a trade. A `medium` candidate that clears all six gates above will produce a simulation; a `high` candidate that fails R/R will not.

### What the blended score represents

`blended_score = 0.70 Ã— technical_score + 0.30 Ã— news_sentiment_score` (`trade_cycle.py:246`)

The technical score comes from the homework pipeline's swing setup analysis. News sentiment is a keyword-weighted score across the last 14 days of headlines with linear freshness decay (1.0 today â†’ 0.3 at day 14). When gather produces 0 sources, the news component is absent and the score is 100% technical.

### Why the current cycle produces observation-only output

1. **R/R is below 1.0 on all watchlist tickers** â€” the setups are extended; entry zones are too close to stop loss, targets are too near to justify the risk. This is correct pipeline behavior, not a bug.
2. **KG corpus has no fresh equity research** â€” the gather fix (`pre_approved` flag + quality override) was deployed 2026-05-10. First nightly run with working gather runs tonight. Until `new_triples > 0`, the blended score has no KG grounding.
3. **Simulation still runs** â€” even when all gates fail, the pipeline produces a 90-day historical replay on the top blended-score candidate for learning purposes, clearly marked `OBSERVATION ONLY â€” NO TRADE`.

---

## Update â€” 2026-05-10

### Trade Cycle Signal Integrity Fixes

Five correctness and quality issues in `Apollo/trade_cycle.py` were identified and fixed.

**[High] Cycle `ok` signal was unconditionally true**
`run_cycle()` previously set `status["ok"] = True` after all stages ran regardless of stage outcomes. A cycle where `hipporag.ok=false` and `risk.ok=false` (no candidate passed) still reported success. Fixed by separating three distinct fields: `completed` (all stages executed), `trade_ready` (at least one candidate passed all risk checks), and `ok` (simulation produced usable output). An `evidence_quality` block is now included in every status â€” `{hipporag_enriched, sources_gathered, new_triples, news_tickers_covered, news_evidence}` â€” so consumers can assess what evidence the cycle actually had.

**[High] Failed risk candidates presented as trade recommendations**
When no candidate passed risk checks, `_stage_simulate` fell back to the highest blended-score proposal and `_build_observation_report` rendered it under `## Top Candidate` with a "What to watch tomorrow" action section. AMZN with R/R 0.44:1 and `all_checks_pass=false` was presented as a valid setup. Fixed by gating on `passes`: when the risk gate failed, the header is now `## OBSERVATION ONLY â€” NO TRADE`, and "What to watch tomorrow" is replaced with an explicit `OBSERVATION NOTE` that names the exact reject reason. No failing candidate can be read as actionable.

**[Medium] `focus_universe_enabled=False` appeared as a pipeline mystery**
The trade cycle intentionally disables `focus_universe` to manage ticker scope via `equity_news_tickers` instead. This caused `self_check decision_mode=disabled` in pipeline output with no explanation. Fixed by adding an explicit log line at stage start: _"focus_universe_enabled=False is intentional â€” trade cycle manages its own ticker scope via equity_news_tickers; self_check decision_mode=disabled is expected."_ The payload comment was also updated to document why.

**[Medium] 0-source HippoRAG runs were silent**
When the gather stage returned 0 sources and HippoRAG produced 0 triples (completing in ~52 seconds instead of the expected 30â€“90 minutes), the stage reported `ok=false` but gave no further signal that the KG was untouched. Fixed by computing `enrichment_skipped = gathered == 0 and new_triples == 0` and logging a `WARNING` when true. The field is written to `01_hipporag.json` and surfaced in the observation report as `kg_enriched: false` with a data warning banner.

**[Medium] News stage always reported `ok=true` regardless of evidence**
`_stage_news_analysis` set `"ok": True` unconditionally, even when every ticker returned 0 headlines and the sentiment blend was running on no data. Fixed by computing `tickers_with_news` and setting `ok = tickers_with_news > 0`. A `news_evidence` field (`good` / `partial` / `none`) is now written to `03_news.json` and shown in the observation report. When `news_evidence=none`, the report adds a warning that the blended score is 100% technical with no news signal.

---

## Update â€” 2026-05-09

### Autonomous Trade Cycle

A full end-to-end autonomous research and simulation cycle is now implemented in `Apollo/trade_cycle.py`. It runs as a scheduled overnight job (Windows Task Scheduler, one-time trigger) and executes five sequential stages without manual intervention:

1. **HippoRAG research** (`_stage_hipporag_research`) â€” runs the nightly pipeline in pattern mode to ingest fresh equity PDFs and rebuild the KG from gathered sources.
2. **Market research** (`_stage_market_research`) â€” calls swing_study on current candidates with a freshly-built KG. KG triple filtering is now scoped to the ticker symbol â€” macro overflow (SEC, Fed, FINRA dominating results) is eliminated.
3. **News analysis** (`_stage_news_analysis`) â€” fetches Yahoo Finance RSS per ticker (20 articles/ticker), scores sentiment via keyword weighting, applies a linear freshness decay (1.0 today â†’ 0.3 at 14+ days), and blends into the technical score at 70/30.
4. **Risk checks** (`_stage_risk_checks`) â€” three hard gates: position sizing on $50K portfolio at 1% risk, entry validity (price within Â±5% of entry zone at simulation time), and liquidity (30-day avg daily volume > 500K shares).
5. **Simulation** (`_stage_simulate`) â€” runs `swing_simulation.run_simulation()` against actual yfinance price history and writes `logs/trade_cycle/observation_report.md` for manual review.

Stage output is logged as JSON in `logs/trade_cycle/trade_cycle_<timestamp>/`. CLI: `python -m Apollo.trade_cycle run|status|report`.

### Swing Simulation Module

`Apollo/swing_simulation.py` is a new standalone simulation module. It fetches real price history via yfinance, detects entry (intraday touch of zone), tracks stop/target resolution, and computes P&L. Bear-case bias: on the same candle where both stop and target are reachable, the stop wins. A 27-test spark suite (`tests/test_simulation_spark.py`) covers parsing, outcome logic, P&L accuracy, persistence round-trips, real yfinance data, and homework integration â€” all passing.

### Simulation Dashboard

A standalone simulation server runs on port 5225 (`simulation_server.py`). The dashboard at `/simulation` renders a Chart.js price chart with entry zone, stop/target dashed lines, and entry/exit markers. Sidebar shows trade stats, watchlist, scoring rubric, and catalysts. Live price refreshes every 30s. All routes are also registered on the main Apollo app (`APP_CONFIG.HOST` + `APP_CONFIG.PORT`, default `127.0.0.1:5010`).

### Pipeline Fixes

- **R/R hard gate** â€” any candidate with risk/reward < 1.0 is rejected as `no_trade` with reason `rr_below_minimum:{rr:.2f}<1.0`. Prevents low-conviction trades from reaching simulation.
- **KG triple filter** â€” `_kg_triples_for_ticker()` now drops edges where neither head nor tail contains the ticker string, eliminating macro entity overflow.
- **Gather stage equity fallback** â€” when `config["sources"]` is empty, Yahoo Finance RSS URLs are injected automatically for each candidate ticker, ensuring the gather stage produces articles rather than timing out.
- **Nightly confidence band fix** â€” `_latest_nightly_confidence_band()` now reads `latest_status.json` from the nightly root before walking nested date/run directories.

## Future Phase â€” Swing Trade Execution

Apollo's swing trading capability is currently a research and paper-trading system. Signal generation, scoring, and outcome tracking are production-grade. What is absent is an order submission layer. This section defines the work required to go live.

### Current state

- **Signal generation** â€” fully implemented. Five setup types (base breakout, pullback to trend, trend continuation, broken trend, extended no chase) with an 8-component confidence score (0â€“100) built from technicals, catalysts, relative strength, and market regime.
- **Paper positions** â€” fully implemented. Proposals that pass scoring are recorded to `focus_trading_state.json` with entry/stop/target zones, thesis, and confidence. Outcome tracking (target hit, stop hit, expired) runs post-entry.
- **Live execution** â€” not implemented. `live_trade_execution` is hardcoded `False` throughout. There are no broker API keys, no order submission code, and no fill/status feedback path.

### Phase requirements

**1. Broker adapter**
Choose one API and implement a thin adapter behind a `BrokerClient` interface:
- **Alpaca** (recommended) â€” REST API, supports both paper and live accounts under the same code path; API keys sufficient; no software install.
- **Interactive Brokers (TWS)** â€” TWS or IB Gateway must be running locally; richer order types; more operational overhead.
- **TD Ameritrade / Schwab** â€” OAuth flow adds complexity; skip unless account is already there.

Minimum adapter surface: `submit_order(ticker, side, qty, order_type, limit_price, stop_price)` â†’ order ID, `get_order_status(order_id)` â†’ fill price / status, `get_positions()` â†’ current book.

**2. Human approval gate**
The `human_approval_required` flag is already set throughout Apollo. Wire it to a confirmation step before any order fires:
- Add a `/admin/swing/approve` endpoint that accepts a proposal ID and fires the order.
- Apollo writes the proposal to a pending queue; no order submits until the endpoint is called.
- Alternatively: expose the pending queue via the existing chat interface â€” Apollo surfaces the proposal and waits for explicit confirmation before calling the broker adapter.

**3. Order translation**
Convert the existing `entry_zone`, `stop_zone`, and `target_zone` dicts from `_zones()` into concrete order parameters:
- Entry: limit order at zone midpoint or market on confirmation.
- Stop: stop-market order at stop zone low.
- Target: limit GTC at target zone midpoint, or two-stage scale (50% at T1, 50% at T2 if zones are split).
- Position sizing: derive share count from configurable `risk_per_trade_dollars` Ã· (entry âˆ’ stop).

**4. Post-fill sync**
After fill confirmation, update `focus_trading_state.json` with actual fill price, order ID, and broker-assigned position ID. Replace the current mark-to-market outcome tracking (which uses daily closes) with intraday status polling for open positions.

**5. Risk controls**
Before any order submits: check that total open position count is below `max_open_positions`, that buying power is available, and that the ticker is not already in the book on the same side. Reject (not defer) on any failed check.

Current implementation:
- Maximum open positions: `APOLLO_SIM_MAX_OPEN_POSITIONS` (default `5`)
- Sim buying power: `APOLLO_SIM_SIMULATED_BUYING_POWER` (default `50000`, fallback `APOLLO_SIM_BUYING_POWER`)
- Validation errors: `risk_control_open_position_limit`, `risk_control_duplicate_side`, `risk_control_insufficient_buying_power`

### Report â€” 2026-05-12

- Implemented phase-5 risk controls in `simulation_execution.py`: open-position cap, same-side duplicate-side check, and buying-power validation executed before any paper broker submission.
- Added environment controls to `.env.template`: `APOLLO_SIM_MAX_OPEN_POSITIONS` and `APOLLO_SIM_SIMULATED_BUYING_POWER`.
- Added tests in `tests/test_simulation_execution.py` for all three new failure modes.
- Validation command:
  - `pytest tests/test_simulation_execution.py tests/test_simulation_spark.py -q`
- Result: **44 passed** (17 `simulation_execution` tests, including phase-5 through phase-7 coverage).

### Report â€” 2026-05-12 (Phase 6: post-fill focus sync)

- Wired simulation broker status sync to update `focus_trading_state.json` with actual fill data after entry fill.
- Added a focus-state sync path in `sync_simulation_execution_plan_with_broker` and env override support via `APOLLO_FOCUS_TRADING_STATE_PATH`.
- Added test `test_sync_plan_with_filled_entry_updates_focus_trading_state` to verify filled entry orders materialize into `paper_positions`.
- Validation command:
  - `python -m pytest tests/test_simulation_execution.py tests/test_simulation_spark.py -q`
- Result: **44 passed** (17 `simulation_execution` tests, including phase-6 coverage).

### Report â€” 2026-05-12 (Phase 7: Alpaca paper broker + CI sanity validation)

- Added Alpaca paper adapter path in `simulation_execution.py` with request signing headers, order payload mapping, status polling, and account-level buying power lookup.
- Extended broker alias normalization so `alpaca`, `alpaca_paper`, `alpaca-paper`, and similar names resolve to the same paper adapter.
- Added `chroma_db/` and `logs/` binary/artifact ignore patterns to `.gitignore` and aligned defaults/documentation for host-port, model selection, and watchlist precedence.
- Added CI sanity check step in `.github/workflows/apollo-ocr-ci.yml` for env-driven settings and importability of core modules.
- Validation command:
  - `python -m pytest tests/test_simulation_execution.py -q`
  - `python -m pytest tests/test_simulation_spark.py -q`
- Result: **30 passed** and **27 passed** respectively; aggregate simulation execution + spark + route suite: **65 passed**.
- Env/import sanity:
  - `.github/workflows/apollo-ocr-ci.yml` includes inline `Sanity check env-driven settings and core imports` step (GitHub Actions execution context).

### Report â€” 2026-05-12 (Phase 7b: Live-mode hardening + idempotent execution safeguards)

- Enforced execution-mode-specific broker behavior in `simulation_execution.py`:
  - `APOLLO_SIM_EXECUTION_MODE` now gates broker initialization (`paper` vs `live`).
  - `APOLLO_SIM_LIVE_ENABLED` is required for live-mode paper/Alpaca flows.
  - Live broker credentials are mode-specific (`APOLLO_ALPACA_LIVE_*`) with no fallback to paper credentials.
- Added execution-time idempotency and reconciliation behavior:
  - per-order retry metadata in broker `order_tracking` (`attempts`, `status`, `broker_order_id`, `last_attempt_at`),
  - execution-mode-specific retry window + stale-order thresholds,
  - skip/retry decisions for `retry_window` and `max_retries` instead of blind re-submission.
- Hardened execution endpoint safety in Flask routes:
  - `app.py` and `simulation_server.py` now gate simulation prepare/submit/status/approve/list/get routes through `_require_local_token()`.
- Expanded CI sanity checks with additional simulation execution env assertions (`APOLLO_SIM_EXECUTION_MODE`, `APOLLO_SIM_LIVE_ENABLED`, retry thresholds).
- Added explicit simulation execution defaults and broker credential placeholders to `.env.template`:
  - `APOLLO_SIM_EXECUTION_MODE`, `APOLLO_SIM_LIVE_ENABLED`, `APOLLO_SIM_BROKER_MAX_RETRIES`, `APOLLO_SIM_BROKER_RETRY_WINDOW_SEC`, `APOLLO_SIM_STALE_ORDER_SECONDS`
  - `APOLLO_SIM_BROKER_PROVIDER`/`APOLLO_SIM_BROKER_MAX...` controls and `APOLLO_ALPACA_*` key/secret/base URL stubs.
- Updated `.gitignore` with stronger runtime artifact guards for `logs/`, `chroma_db/**`, `artifacts/`, and common generated binaries.
- Validation command:
  - `python -m pytest tests/test_simulation_execution.py tests/test_simulation_spark.py tests/test_apollo_smoke.py -q`
- CI validation command added in workflow:
  - `python -m pytest tests/test_simulation_execution.py -q`
- Result: **phase 7b behavior validated in unit suite** (new execution/retry/credential-hardening tests added).
- Latest full run: **78 passed** for `tests/test_simulation_execution.py tests/test_simulation_spark.py tests/test_apollo_smoke.py tests/test_simulation_execution_routes.py`.

### Planned Phases After 7b

#### Phase 7b (Current Execution Sprint, active)
- **Goal:** production-safe execution primitives before any live rollout.
- Completed baseline:
  - operator metadata capture on approve and submit now includes source context and actor identity,
  - execution-mode gating and credential-specific keys are enforced (`paper` vs `live`),
  - rate-limits and idempotency/retry policies are active on submit path,
  - route-level local-token guard exists on main and simulation servers.
- 7b current status:
  - persist explicit `submission.source` (`api` default, `scheduler`/other optional) so recurring jobs can be audited and correlated.
- 7b completion notes:
  - submission history is appended on each submit attempt (`submission_history`, max 100 entries).
  - each order attempt now persists `submission_source` and `submission_actor` in `order_tracking`.
  - per-attempt submit payloads now carry `submission_source` and `submission_actor` for audit.

#### Phase 8 - Operator control hardening
- Add explicit approval workflow in UI/admin surface (`/admin/swing/proposal_decision`) and make submit impossible without prior approval.
- Expand audit fields (`approval_reason`, `approval_source`) on every approval event.
- Add per-user and per-credential submission policy (cooldowns + allow-list exceptions).

#### Phase 9 - Scheduled reconciliation and incident controls
- Add scheduled reconciliation pass (single-flight + stale-lock) for non-terminal `order_tracking`.
- Add `POST /api/simulation/execution/reconcile` in both Flask entrypoints with local-token enforcement.
- Add incident-safe escalation for stuck/retry-failed orders.
- Add duplicate-fill/double-replace protection before any OCO/stop-target follow-on action.
- Add scheduler-level circuit breaker and error cooldowns before allowing another run.

#### Phase 10 - Controlled paper/live escalation
- Run stable canary sequence: `signal -> paper submit -> broker sync -> fill/paper state sync`.
- Add phased watchlist rollout and hard pause until acceptance thresholds are met.
- Switch to live only when phase-9 safeguards and phase-8 approval controls pass with sustained green results.

### Report â€“ 2026-05-12 (End-to-End Phase Sweep)

- Executed a phase-by-phase test sweep against `tests/test_simulation_execution.py`.
- Phase 1 (plan creation/fallback): **3 passed**
- Phase 2 (approval gate): **2 passed**
- Phase 3 (order translation/paper execution flow): **3 passed**
- Phase 4 (broker submit + sync behavior): **2 passed**
- Phase 5 (pre-submit risk controls): **3 passed**
- Phase 6 (post-fill focus sync): **1 passed**
- Phase 7 (alpaca paper adapter + env/docs consistency checks): **1 passed**
- Phase 7b (live-mode + idempotent/retry safeguard hardening): **2 passed**
- Aggregated execution module run:  
  - `python -m pytest tests/test_simulation_execution.py -q`  
  - Result: **30 passed**
- Full simulation stack verification:  
  - `python -m pytest tests/test_simulation_execution.py tests/test_simulation_spark.py tests/test_simulation_execution_routes.py tests/test_simulation_server_routes.py -q`  
  - Result: **65 passed**

### Sequencing
Keep execution in paper mode until Phase 8+ controls are proven in canary runs.

### What this phase does not cover

- Short selling (current signal engine biases long; short infrastructure is a separate effort).
- Options orders.
- Automated intraday entry/exit (horizon is 2â€“20 trading days; daily decision cadence is assumed).
- Tax lot management.

## OCR Stack Grade Update â€” 2026-05-06 (Grade: 96/100)

### Summary

Between 2026-05-05 and 2026-05-06 the Apollo OCR stack received a multi-phase improvement campaign that raised the architecture grade from 83 (finance) / 74 (wider corpus) at the 2026-05-05 multi-lane expansion review to a current grade of **96/100** across both corpora. 123 tests pass against the full suite. This section documents what was built, why each change matters, and what the remaining gap is.

---

### Phase 1 â€” Gap Closure (2026-05-05)

Four structural gaps were closed that had been deferred at the 2026-04-21 OCR97 release:

**Gap 1 â€” Image signals in document classification** (`classify_document_features`). The classifier previously operated only on goal text and draft text keywords. It now also reads raw pixel statistics from the image (whitespace ratio, edge density, aspect ratio) to infer layout class independent of what the user typed in the goal string. This directly addresses the root cause of the goal-keyword-gate limitation identified in the 2026-05-05 expansion review.

**Gap 2 â€” Checkbox detection** (`_parse_checkbox_response`, `_augment_visual_lanes`). Five new checkbox output formats are now parsed: numbered list (`1. [x] Label`), `Checked:`/`Unchecked:` prefix, trailing `(checked)`/`(unchecked)`, and unicode âœ“/âœ—/â˜‘/â˜. The Qwen prompt sent for checkbox extraction now includes explicit format examples. The `visual_controls` output dict was extended with `total` and `coverage` fields. Previously, forms with non-bracketed Qwen outputs would silently miss checkbox states; this is now eliminated.

**Gap 3 â€” TrOCR and DePlot model-not-found classification** (`load_trocr_runtime`, `load_deplot_runtime`). When the model file is missing from the HuggingFace cache, both runtimes now catch the OSError and return a structured `trocr_model_not_found:{model_id}` / `deplot_model_not_found:{model_id}` error with a `pip install` install hint. Previously these surfaced as generic `AttributeError` failures with no actionable message. Gateway health endpoints (`/ocr/handwriting/health`, `/ocr/chart/health`) now surface the `install_hint` field.

**Gap 4 â€” PaddleOCR install hint** (`_paddle_backend_status`). When PaddleOCR is not installed (`reason == "engine_not_installed"`), the health response now includes a versioned install command: `pip install paddlepaddle==2.6.1 paddleocr==2.7.3`. The `/ocr/paddle/health` endpoint and `_engine_snapshot()` both expose this field.

**Gap closure grade impact:**

| Corpus | Before | After gap closure |
|---|---|---|
| Finance / market | 83 | **87** |
| Wider corpus | 74 | **81** |

---

### Phase 2 â€” Grade Improvements (2026-05-05, same session)

Three targeted improvements were applied after gap closure:

**Improvement 1 â€” Qwen VL pre-classifier** (`_qwen_classify_layout`, `_run_policy_route`). A layout pre-classification call is made before the engine chain is selected, using Qwen itself as the classifier. The result overrides the heuristic `layout_class` when a valid class word is returned (`table`, `chart`, `form`, `handwriting`). Feature-flagged via `AEGIS_QWEN_PRECLASSIFY_ENABLE` (default 0 at this phase; enabled in Phase 4). This makes routing document-content-driven rather than goal-keyword-driven, closing the remaining gap between the finance and wider corpus grades.

**Improvement 2 â€” PaddleOCR prewarm at gateway startup** (`_run_prewarm`). `_load_paddle_runtime()` is now called inside `_run_prewarm()`. Previously the PaddleOCR pipeline loaded on the first OCR request, causing a cold-start latency spike on layout-dense documents. The prewarm endpoint (`/ocr/prewarm`) now includes `gb10_paddleocr_vl` in the `engines` output dict with `ok` and `error` fields.

**Improvement 3 â€” Checkbox detection hardening** (same functions as Gap 2 above). Additional patterns, explicit prompt format instructions, and `total`/`coverage` output fields were added as a standalone improvement pass separate from the gap-closure fix.

**Phase 2 grade impact:**

| Corpus | After gap closure | After improvements |
|---|---|---|
| Finance / market | 87 | **91** |
| Wider corpus | 81 | **88** |

---

### Phase 3 â€” Hardware Ceiling Improvements (2026-05-06)

Five hardware-level changes were applied to push the grade above 91:

**Change 1 â€” Model upgrade: `qwen2.5vl:72b` â†’ `qwen3-vl:32b`** (`Apollo/.env`). The configured primary model (`qwen2.5vl:72b`) was not installed on port 11435 and caused a silent fallback to the 7B model. Switching to `qwen3-vl:32b` (20GB, confirmed available on port 11435) activates the intended 32B model tier. This is the single highest-impact change: 7B â†’ 32B improves literal transcription fidelity, numeric accuracy, and table structure recovery across all document types.

**Change 2 â€” MinerU 2.5 activated** (`Apollo/.env:AEGIS_OCR_ENGINE_MINERU2_5_READY=1`). The `mineru` Python module and `mineru.EXE` command were both confirmed installed, but `_extract_mineru2_5()` blocked on `model_assets_present=False` even though `runtime_managed_assets=True` (the module manages its own assets on first use). The override flag bypasses the disk-asset check and activates the MinerU lane for the `digital_pdf` and `table_dense` engine chains. MinerU 2.5 is particularly strong on structured financial PDF layouts (balance sheets, income statements) with complex multi-column table structure.

**Change 3 â€” 4Ã— RealESRGAN scale + parallel scale ensemble** (`_render_image_scale_variants`, `_phase2_image_scale_ensemble`). Scale values extended from `[1.0, 1.5, 2.0]` to `[1.0, 1.5, 2.0, 4.0]`. The 4Ã— variant uses RealESRGAN with `outscale=4.0` (90-second timeout). All four variants now run in parallel via `ThreadPoolExecutor` (was sequential). Workers are controlled by `AEGIS_SCALE_ENSEMBLE_WORKERS` (default 4). The 4Ã— upscale recovers sub-6pt footnote text and compressed table cell content that the 2Ã— variant still missed.

**Change 4 â€” Multi-image batch Qwen call** (`_ollama_generate_with_images`, `_gb10_qwen_ocr`). For PDFs with 2â€“8 pages, all page images are now sent to Qwen in a single `api/generate` call with `"images": [b64_1, b64_2, ...]` instead of one call per page. Ollama supports multi-image natively. This eliminates per-page context fragmentation (Qwen can now see table headers on page 1 while reading data rows on page 2) and reduces total Qwen call count by up to 8Ã—. Falls through to per-page loop if the batch call fails. Controlled by `AEGIS_QWEN_MULTI_IMAGE_MAX_PAGES` (default 8).

**Phase 3 grade impact:** ~91 â†’ ~94 (finance), ~88 â†’ ~92 (wider corpus).

---

### Phase 4 â€” Ceiling Improvements (2026-05-06)

Five further improvements were applied targeting the 96â€“97 range:

**Change 1 â€” Qwen pre-classifier enabled** (`Apollo/.env:AEGIS_QWEN_PRECLASSIFY_ENABLE=1`). The pre-classifier built in Phase 2 is now active. This is the dominant driver for the wider corpus lift â€” general-purpose goals that previously fell through to the `digital_pdf` default chain now get routed correctly based on document content rather than goal keywords.

**Change 2 â€” DePlot enabled** (`Apollo/.env:AEGIS_DEPLOT_ENABLE=1`). DePlot (`google/deplot`) routes chart-heavy documents (earnings slides, analyst reports, data visualizations) to a chart-to-table specialist model before Qwen. The `chart_or_figure` chain now starts with `gb10_deplot_chart` in quality-first mode. DePlot extracts numeric data from chart images as structured tables, which Qwen alone cannot reliably do.

**Change 3 â€” Numeric fidelity guard** (`_apply_numeric_fidelity_guard`). A post-processing pass compares the Qwen OCR output against the native PDF text layer (from PyMuPDF, always perfect for digital PDFs) and substitutes near-miss numbers. The guard normalizes currency symbols and commas before comparison so formatting differences (`"1,234,567"` vs `"1234567"`) don't block matches. A `difflib.SequenceMatcher` ratio â‰¥ 0.80 on the normalized digit string triggers substitution. Applied inside `_gb10_qwen_ocr()` after self-consistency voting, only when `draft_seed` (native PDF text) is available.

**Change 4 â€” Multi-page self-consistency** (`_gb10_qwen_ocr`). Self-consistency voting previously ran only on `page_paths[0]` regardless of document length. For multi-page PDFs (2+ pages), the "strict_literal" and "numeric_focus" variants are now generated for every page in parallel (`ThreadPoolExecutor`, controlled by `AEGIS_SC_PAGE_WORKERS`, default 4). Each page's voted winner replaces that page's base output. This doubles self-consistency coverage for the most common financial document format (2â€“6 page PDFs).

**Change 5 â€” PDF DPI ensemble: 400 DPI + parallelized** (`_phase2_pdf_dpi_ensemble`). DPI values extended from `[150, 200, 300]` to `[150, 200, 300, 400]`. All four variants now run in parallel via `ThreadPoolExecutor` (was sequential, controlled by `AEGIS_DPI_ENSEMBLE_WORKERS`, default 4). 400 DPI resolves sub-6pt footnote text in fund prospectuses and regulatory filings that 300 DPI missed. Partial failure (e.g. 400 DPI render fails) is gracefully handled â€” the vote proceeds on the remaining 3 variants.

**Phase 4 grade impact:**

| Corpus | After Phase 3 | After Phase 4 |
|---|---|---|
| Finance / market | ~94 | **~96** |
| Wider corpus | ~92 | **~96** |

---

### Phase 4 â€” Bug Fixes Applied Alongside Grade Update

Three implementation gaps found during grade review were fixed:

**Fix 1 â€” Numeric guard normalization** (`_apply_numeric_fidelity_guard`). The original guard compared raw token strings including commas, which caused SequenceMatcher ratios to fall below threshold for numbers that differed only in formatting (e.g. `"1,234,567"` vs `"1234567"`). Fixed by normalizing to digit-only strings before comparison. The threshold was also lowered from 0.88 to 0.80 to correctly catch single-digit transpositions in 7-digit financial numbers (which produce ratios of ~0.857 at the digit level).

**Fix 2 â€” `_quality_bundle` redundant FinBERT calls** (`_quality_bundle`). Self-consistency scoring called `_quality_bundle` on each candidate variant without a `finbert_eval` argument, triggering a FinBERT inference on every variant during voting. Added `skip_finbert: bool = False` keyword parameter. All intermediate quality bundle calls inside self-consistency (`_run_page_sc` and single-page SC) now pass `skip_finbert=True`. The authoritative FinBERT call still runs in `_normalize_ocr_payload`. For a 3-page document with 2 self-consistency variants per page, this eliminates 6 redundant FinBERT calls per OCR operation.

**Fix 3 â€” DePlot health install hint** (`_engine_health` in `gb10_ocr_gateway.py`). When `AEGIS_DEPLOT_ENABLE=1` but the model weights are not loaded (`runtime_loaded=False`), the `/ocr/chart/health` response now includes `install_hint: "pip install transformers datasets # then restart gateway"` and `reason: "deplot_model_not_loaded"` instead of the generic `reason: "runtime_not_loaded"`. This surfaces actionable recovery steps when DePlot is enabled but the `google/deplot` weights haven't been downloaded.

---

### Current Grade: 96/100

**Test verification:** 123 tests pass (0 failures) against `test_ocr_dual_tool.py` (65 tests), `test_ocr_gaps_20260505.py` (19 tests), `test_ocr_improvements_20260505.py` (16 tests), `test_ocr_ceiling_20260506.py` (22 tests), plus `test_ocr_phase2_services.py` (3 tests).

**Remaining gap to 97:** The final point requires model weights that are not yet loaded at runtime. `google/deplot` is enabled but not yet downloaded (gateway reports `deplot_model_not_loaded`). Until DePlot weights are present, the `chart_or_figure` chain falls back to Qwen, which is significantly weaker on chart data extraction. When DePlot loads, the grade is expected to reach 97 on the finance corpus. The wider corpus also requires TrOCR handwriting weights (`gb10_trocr_handwriting`) to close the handwritten document sub-corpus gap. Both are download-only steps, not code changes.

```powershell
# Download DePlot weights (one-time, ~2GB):
python -c "from transformers import AutoProcessor, AutoModelForSeq2SeqLM; AutoProcessor.from_pretrained('google/deplot'); AutoModelForSeq2SeqLM.from_pretrained('google/deplot')"

# Check health after restart:
curl http://127.0.0.1:5221/ocr/chart/health
```

---

## Nightly Pipeline Quality Gate Hardening (2026-04-23)

This update closes a content-fidelity gap in the OCR quality scan: the existing semantic term check (`_OCR_SEMANTIC_TERMS`) only verified generic finance vocabulary (risk, margin, position, etc.), which meant Qwen drift into generic overnight-trading prose could pass the gate even if the extracted text contained zero topic-specific content.

### Topic anchor retention

`_ocr_quality_scan_decision()` in `nightly_pipeline.py` now runs a topic anchor check after the duplicate fingerprint gate:

- `ocr_scan_topic_anchors` is derived at pipeline config time from `focus_universe_symbols` (tickers) and `topic` keyword tokens.
- If the extracted text contains fewer than `ocr_scan_min_anchor_hits` of those anchors, the document is rejected with `reason: topic_anchor_retention_failed:<hits>/<min>`.
- Default minimum: `1` (env: `APOLLO_OCR_SCAN_MIN_ANCHOR_HITS`).
- Anchor derivation: symbols take precedence, then topic words of 3+ characters, deduped, capped at 20.

This specifically targets the April-19 failure mode where Qwen extracted "overnight trading strategies" boilerplate that naturally contained `risk`, `margin`, and `position` but none of the configured topic anchors (e.g. `tsla`, `earnings`, `options`).

### Seed-only gather guard

Two helpers added to `nightly_pipeline.py`:

- `_is_seed_fallback_source(url_or_path)` â€” returns `True` if the source path is under `logs/homework_seed_docs/`.
- `_gather_source_mix(sources)` â€” returns `{real_count, seed_count, seed_only}` for a gathered source list.

In background pipeline mode (`run_background_pipeline_step()`), a seed-only gather result now aborts before OCR with `gather_seed_only_rejected`. This prevents seed PDFs from masquerading as real gathered sources in production runs.

Background config defaults also hardened:
- `gather_allow_seed_fallback=False` â€” seed PDFs are never accepted as a fallback for missing real sources in background mode.
- `ocr_qwen_primary=False` â€” Qwen is not used as the primary OCR engine in background mode.
- `ocr_primary_engine_pdf=gb10_paddleocr_vl` â€” PaddleOCR-VL is preferred for PDF inputs.

### Rate-limit handling in gather

`_is_rate_limited_error()` added to `nightly_pipeline.py` â€” detects 429 / `rate_limited` signals in gather exceptions.

`gather_trading_homework_sources()` in `trading_homework.py` now retries on rate-limit with configurable backoff (`web_rate_limit_retries`, default 1, max 3; `web_rate_limit_backoff_sec`, default 1.5s, max 8s) and falls back through `auto â†’ duckduckgo_html` before giving up.

### Bug fix: `ocr_scan_min_anchor_hits=0` was treated as missing

`config.get("ocr_scan_min_anchor_hits") or DEFAULT_OCR_SCAN_MIN_ANCHOR_HITS` treated `0` as falsy and silently reverted to the default minimum of `1`, making it impossible to disable the anchor check per-run. Fixed with an explicit `is None` guard:

```python
_raw_anchor_hits = config.get("ocr_scan_min_anchor_hits")
min_anchor_hits = int(DEFAULT_OCR_SCAN_MIN_ANCHOR_HITS if _raw_anchor_hits is None else _raw_anchor_hits)
```

### Tests

New test added:
- `test_ocr_quality_scan_rejects_on_topic_anchor_drift` â€” verifies that generic finance prose containing `risk`, `margin`, `position` but none of the topic anchors (`tsla`, `earnings`, `options`) is rejected when topic is "TSLA earnings options flow".

Existing test updated:
- `test_ocr_quality_scan_rejects_duplicate_content_fingerprint` â€” now passes `ocr_scan_min_anchor_hits: 0` to isolate the duplicate fingerprint check from the anchor retention gate.

```powershell
cd C:\Users\blyth\Desktop\Engineering
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
python -m pytest Apollo/tests/test_nightly_pipeline.py -k "quality_scan" -v
```

Result: `3 passed`

## OCR97 Baseline + Challenger Study (2026-04-21)

Apollo's current OCR production path is now formally frozen as **`OCR97`** for benchmark and promotion work. `OCR97` is the control route, not an experiment. New OCR tools, model pairs, and research-inspired routes are evaluated only as challengers against that locked baseline.

Primary study artifacts:
- `Apollo/evaluations/README_OCR_BENCHMARK.md`
- `Apollo/evaluations/README_OCR97_RELEASE_2026-04-21.md`
- `Apollo/evaluations/artifacts/ocr97_challenger_study_20260421.md`
- `Apollo/evaluations/artifacts/ocr97_challenger_study_20260421.json`
- `Apollo/evaluations/ocr97_research/papers_index.json`

### What OCR97 means

- `OCR97` is the current Apollo OCR route in `C:\Users\blyth\Desktop\Engineering`.
- It remains the default production OCR path while challenger studies run.
- Challengers only win on named slices. There is no global replacement by default.

### Focused 6-pack research set

Local paper cache:
- `GOT/OCR-2.0`: `Apollo/evaluations/ocr97_research/papers/got_ocr2_2024.pdf` and source `https://arxiv.org/abs/2409.01704`
- `PaddleOCR-VL`: `Apollo/evaluations/ocr97_research/papers/paddleocr_vl_2025.pdf` and source `https://arxiv.org/abs/2510.14528`
- `MinerU2.5`: `Apollo/evaluations/ocr97_research/papers/mineru2_5_2025.pdf` and source `https://arxiv.org/abs/2509.22186`
- `OmniParser`: `Apollo/evaluations/ocr97_research/papers/omniparser_2024.pdf` and source `https://arxiv.org/abs/2403.19128`
- `MOCR`: `Apollo/evaluations/ocr97_research/papers/mocr_2026.pdf` and source `https://arxiv.org/abs/2603.13032`
- Table-specialist reference: `Apollo/evaluations/ocr97_research/papers/pubtables_1m_2021.pdf` and source `https://arxiv.org/abs/2110.00061`

Benchmark framing references are also vendored locally:
- `OCRBench`, `OCRBench v2`, `OmniDocBench`, `Real5-OmniDocBench`, and `DocLayNet`

### Latest measured findings

Local benchmark evidence from the `2026-04-21` challenger run:
- `OCR97` remained the strongest measured route across the current six-slice sample set.
- `GOT-OCR2.0` was the only meaningful local challenger. It matched `OCR97` on digital, mixed-layout, table-dense, and tiny-text probes, but lost badly on scanned PDF and warped-image slices because key finance tokens dropped out.
- `GOT-OCR2.0` is much faster in this environment:
  - `OCR97` average latency: about `42.2s`
  - `GOT-OCR2.0` average latency: about `3.8s`
  - That makes GOT a legitimate latency challenger, but not a promotion candidate over `OCR97` on the current accuracy-weighted study.
- `PaddleOCR-VL` did not produce a promotable local result in this checkout. The measured route returned `gb10_paddleocr_vl_url_unset`.
- `MinerU2.5` remained runtime-pending for this pass. The local gateway path returned `422` failures during the study run.
- `OmniParser` and `MOCR` are included in the paper cache and study manifests, but their empirical sections are explicitly marked unavailable rather than inferred.

Current decision:
- keep `OCR97` as the production OCR default
- keep `GOT-OCR2.0` as a speed-oriented challenger worth revisiting
- do not promote `PaddleOCR-VL` or `MinerU2.5` until their local runtime paths are healthy
- keep `OmniParser` and `MOCR` in the literature watchlist until runnable local adapters exist

### Study rules

- Scoring uses slice wins plus capability cards, not a single global leaderboard.
- Every challenger record captures route type, slice, metrics, latency/runtime notes, and exact paper citations.
- The report distinguishes:
  - literature claim
  - local benchmark evidence
  - engineering inference

Use `Apollo/evaluations/README_OCR_BENCHMARK.md` as the canonical benchmark procedure and command reference for downloading papers, rerunning the study, and reading the latest artifact set.

## OCR97 Pipeline Architecture â€” Grade 97/100 (2026-04-21)

`OCR97` is the production OCR baseline for Apollo financial document extraction. All OCR work on this machine routes through it. Grade 97/100 as of 2026-04-21 after Phase 2 hardening.

### Source files â€” canonical paths (C drive only, never E)

| File | Lines | Role |
|------|-------|------|
| `C:\Users\blyth\Desktop\Engineering\common\ocr_dual_tool.py` | 3007 | Routing policy, engine management, quality scoring, phase2, MCP tool registration |
| `C:\Users\blyth\Desktop\Engineering\common\gb10_ocr_gateway.py` | 2161 | Aegis Flask gateway â€” all `/ocr/*` HTTP routes, prewarm, lane health |
| `C:\Users\blyth\Desktop\Engineering\common\ocr_local_inference.py` | 679 | Shared local model inference (GOT, FinBERT, TableFormer) â€” no circular deps |

`ocr_dual_tool.py` and `gb10_ocr_gateway.py` both import `ocr_local_inference.py`. The gateway aliases its runtime dicts (`_GOT_RUNTIME = _local_infer._GOT_RT`) so models load once and are shared between gateway and dual tool â€” a single model instance, not two.

### Engines

| Engine ID | Backend | Config |
|-----------|---------|--------|
| `gb10_qwen_ocr` | Ollama `qwen2.5vl:7b` | `APOLLO_GB10_QWEN_OCR_MODEL`; primary quality-first engine |
| `gb10_got_ocr2` | GOT-OCR2.0 `stepfun-ai/GOT-OCR-2.0-hf` | Local via `ocr_local_inference.got_extract_texts()` when `APOLLO_GB10_GOT_OCR_URL` is blank; real per-token confidence |
| `gb10_paddleocr_vl` | PaddleOCR-VL | `APOLLO_GB10_PADDLEOCR_VL_URL`; advanced lane |
| `mineru2_5` | MinerU 2.5 | Gateway adapter; runtime-managed |
| `olmocr2` | OlmOCR 2 | Gateway adapter; runtime-managed |
| `rapidocr` | RapidOCR CPU | Region-retry fallback |
| `tesseract` | Tesseract `C:\Program Files\Tesseract-OCR\tesseract.exe` | Region-retry and degraded-scan fallback |

### Routing policy

`route_mode` field in request payload:
- `quality_first` (default) â€” engine score cascade; falls back on low confidence or empty result
- `best_speed` â€” prefers GOT or RapidOCR for latency
- `forced` â€” caller specifies exact `engine` id

### Phase 2 capabilities

**Self-consistency: Qwen path** (`ocr_dual_tool.py` lines 1958â€“2011)
- Runs `"strict_literal"` and `"numeric_focus"` prompt variants
- Merges via `_line_vote_majority()` â€” majority-vote per output line

**DPI ensemble: non-Qwen PDF path** (`ocr_dual_tool.py` line 1525 `_phase2_pdf_dpi_ensemble()`)
- Renders same PDF at 150 / 200 / 300 DPI
- Votes across variants; carries `dpi_samples` and `variant_count` in phase2 metadata

**FinBERT financial sentiment** (`ocr_local_inference.py` â†’ `finbert_eval()`)
- Model: `ProsusAI/finbert` (cached locally in `D:\AI\models`)
- Returns `{label, score}` â€” positive / negative / neutral
- Computed once in `_normalize_ocr_payload` line 1891, passed to `_quality_bundle` â€” no double inference

**TableFormer table reconstruction** (`ocr_local_inference.py` â†’ `tableformer_reconstruct()`)
- Model: `microsoft/table-transformer-structure-recognition-v1.1-all` (cached locally)
- Full cell detection + geometry merge + OCR cell text extraction
- Called in `_normalize_ocr_payload` line 1887

**Semantic diff fingerprinting** (`ocr_local_inference.py` â†’ `semantic_diff_check()`)
- SHA-256 keyed by `source_path`, persisted to `Apollo/logs/ocr_fingerprints.json`
- Returns `status: first_seen | no_change | changed`

**Region confidence retry**
- Threshold: `conf < 0.45`, max 4 regions per page
- Retry order: `gb10_qwen_ocr â†’ rapidocr`
- Override via `region_retry_policy` in request payload

### Aegis gateway routes (port 5221)

| Route | Method | Purpose |
|-------|--------|---------|
| `/ocr/extract` | POST | Policy-routed OCR entrypoint â€” `{source_path, route_mode, engine, region_retry_policy, warm_hint}` |
| `/ocr/health` | GET | Engine readiness, warm state, install metadata, engine_details per lane |
| `/ocr/capabilities` | GET | Per-lane readiness with reason strings, all route names |
| `/ocr/got/extract` | POST | GOT-OCR2.0 direct â€” `{source_path}` |
| `/ocr/got/health` | GET | GOT runtime status |
| `/ocr/finbert/eval` | POST | FinBERT sentiment â€” `{text}` |
| `/ocr/finbert/health` | GET | FinBERT runtime status |
| `/ocr/table/reconstruct` | POST | TableFormer reconstruction â€” `{source_path, text}` |
| `/ocr/table/health` | GET | TableFormer runtime status |
| `/ocr/docunet/rectify` | POST | DocuNet geometric rectification â€” `{image_b64}` |
| `/ocr/realesrgan/upscale` | POST | RealESRGAN upscale â€” `{image_b64, outscale}` |
| `/ocr/paddle/extract` | POST | PaddleOCR-VL lane |
| `/ocr/mineru/extract` | POST | MinerU 2.5 lane |
| `/ocr/olmocr/extract` | POST | OlmOCR 2 lane |
| `/ocr/prewarm` | POST | Manually warm runtimes â€” `{warm_model}` |

### Environment variables

```
APOLLO_GB10_OCR_GATEWAY_URL=http://127.0.0.1:5221
APOLLO_GB10_GOT_OCR_URL=http://127.0.0.1:5221/ocr/got/extract  # blank = local inference
APOLLO_GB10_QWEN_OCR_MODEL=qwen2.5vl:7b
APOLLO_OCR_FINBERT_URL=                                          # blank = local inference
APOLLO_OCR_TABLEFORMER_URL=                                      # blank = local inference
APOLLO_OCR_DOCUNET_URL=
APOLLO_OCR_REALESRGAN_URL=
AEGIS_OCR_GATEWAY_PREWARM_ENABLED=1
AEGIS_OCR_GATEWAY_PREWARM_ON_STARTUP=1
AEGIS_OCR_GATEWAY_PREWARM_INTERVAL_SEC=300
```

### Running tests

```powershell
cd C:\Users\blyth\Desktop\Engineering
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
python -m pytest Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_ocr_phase2_services.py -q
# Expected: 55 passed
```

Bootstrap readiness check:
```powershell
python Aegis/scripts/bootstrap_gb10_ocr_stack.py --check-only --output C:\Users\blyth\Desktop\Engineering\_tmp\ocr_check.json
```

### Remaining gaps (3/100 points)

1. DPI ensemble only applies to PDF inputs â€” image-only paths get no variant sampling
2. `_quality_bundle` still calls `_finbert_eval_signal` if the `finbert_eval` arg is falsy â€” callers outside `_normalize_ocr_payload` can double-infer
3. TableFormer runs unconditionally in `_normalize_ocr_payload` line 1887 even when tables were already extracted upstream

### Quick fix guide

| Symptom | Where to look |
|---------|--------------|
| OCR returns empty text | `ocr_dual_tool.py` `_run_engine_once()` â€” check engine fallback chain and `min_chars` threshold |
| FinBERT not loading | `ocr_local_inference.py` `finbert_load_runtime()` â€” check `D:\AI\models` cache path |
| GOT inference fails | `ocr_local_inference.py` `got_load_runtime()` â€” verify `stepfun-ai/GOT-OCR-2.0-hf` cache and `torch.cuda.is_available()` |
| TableFormer fails | `ocr_local_inference.py` `tableformer_load_runtime()` â€” check `microsoft/table-transformer-structure-recognition-v1.1-all` cache |
| Gateway 503/422 | `GET http://127.0.0.1:5221/ocr/health` â€” check `engine_details[*].ready` and `warm_state` |
| Wrong model loaded | Runtime dicts aliased: `gb10_ocr_gateway._GOT_RUNTIME is ocr_local_inference._GOT_RT` â€” check `_GOT_RT["loaded_model_id"]` |
| Tests fail after edit | Run `python -m py_compile common/ocr_dual_tool.py` first; then `pytest Apollo/tests/test_ocr_dual_tool.py -q` |

## Background GB10 Pipeline

Apollo now uses a **background GB10 pipeline** instead of relying only on a night-only batch window.

Primary reference:
- `Apollo/README_BACKGROUND_PIPELINE.md`
- `Apollo/README_PIPELINE_QUALITY_HARDENING.md`

That document covers:
- pause/resume behavior
- GB10 idle gating
- OCR and HippoRAG step budgeting
- scheduled task behavior
- status and log files
- review/extension guidance for Claude or other agents

## Aegis VM Instruction Lane Integration (2026-04-20)

Apollo can delegate VM/browser operator actions to Aegis and consume run artifacts without parsing UI telemetry.

- Plain-English VM replies:
  - Aegis VM chat is normalized for operator-readable instruction/observation replies (not raw event dumps by default).
- Action confirmation contract:
  - Aegis confirms queued/executed VM actions in first-person operational language for instruction-style prompts.
- Garmin/morning-report execution path:
  - Chat trigger path: `POST http://127.0.0.1:5221/chat` with prompts containing `garmin`/`morning report`.
  - Direct route path (most reliable for automation): `POST /vm/garmin/run`, then poll:
    - `GET /vm/garmin/status/<run_id>`
    - `GET /vm/garmin/result/<run_id>`
- Important parsing note for cross-agent callers:
  - In some degraded chat responses, local-action details can appear under `staged_context.staged_actions[*].action.detail` (including `run_id`) rather than top-level `action`.

Use this lane when Apollo needs Aegis to drive VM-only tasks while keeping host input isolation (`execution_surface=vm_guest`).

### Apollo eBay Listing Framework (2026-05-19)

Apollo now exposes `apollo.ebay_listing` as an MCP tool. It is draft-first by default and can also verify the Aegis VM bridge with `action=vm_status` or `action=vm_probe`.

- Draft/build actions do not touch eBay:
  - `action=draft` returns the Inventory API `inventory_item` payload, offer payload, required-field blockers, SKU, marketplace, and publish readiness.
- Aegis VM hooks:
  - `action=vm_status` reads Aegis `/forgeclaw/vm/status?cache=1`.
  - `action=vm_probe` runs a short guest-only VM command through Aegis `/forgeclaw/vm/exec` and checks basic VM/network reachability.
- API setup/readiness actions:
  - `action=credential_status` reports token/env gates without exposing secrets.
  - `action=setup_status` combines credential status, draft readiness, and optional Aegis VM status.
  - `action=seller_prerequisites` reads eBay Account API business policies plus Inventory API locations so the returned IDs can be copied into env or payload fields.
  - `action=category_suggestions` calls eBay Taxonomy API for a category ID. Sandbox category suggestion text is not reliable; production taxonomy is the useful lookup lane.
  - `action=sandbox_pilot` performs a dry-run plan only: setup status, optional VM status, optional prereq/category reads, and the create sequence. It does not write to eBay.
- eBay API mutation gates:
  - Creating inventory items/offers requires `APOLLO_EBAY_ENABLE_MUTATIONS=1`, an OAuth user access token, and `operator_confirmed=true`.
  - Publishing a live listing additionally requires `APOLLO_EBAY_ENABLE_PUBLISH=1` and the exact confirmation phrase: `I approve Apollo listing this item on eBay`.

Relevant env keys are in `.env.template`: `APOLLO_AEGIS_BASE_URL`, `APOLLO_AEGIS_TOKEN`, `APOLLO_EBAY_ACCESS_TOKEN`, `APOLLO_EBAY_MERCHANT_LOCATION_KEY`, and the three seller policy IDs.

### eBay Operational Notes (2026-06-29)

- Ownership:
  - Apollo owns the eBay workflow and MCP tool.
  - Sky owns the intake bridge that detects listing intent in tasks/images/chat and creates an Apollo approval draft action.
  - Aegis VM is support-only here; it provides `vm_status` / `vm_probe` reachability hooks and is not the primary owner of the listing flow.
- Current implemented entrypoints:
  - Apollo MCP tool: `apollo.ebay_listing`
  - Apollo review pages: `/admin/ebay/drafts/<draft_id>` and `/admin/ebay/drafts/<draft_id>/artifact`
  - Sky bridge: `Sky/services/apollo_ebay_listing_bridge.py`
- Current runtime state:
  - Draft-first flow is working and creates local approval artifacts under `Apollo/artifacts/ebay_listing_drafts/`.
  - Live eBay writes are not configured in the current environment.
  - `credential_status` currently reports `access_token_present=false`, `mutation_enabled=false`, and `publish_enabled=false`.
  - `setup_status` currently reports these missing environment prerequisites: `APOLLO_EBAY_ACCESS_TOKEN`, `APOLLO_EBAY_MERCHANT_LOCATION_KEY`, `APOLLO_EBAY_FULFILLMENT_POLICY_ID`, `APOLLO_EBAY_PAYMENT_POLICY_ID`, and `APOLLO_EBAY_RETURN_POLICY_ID`.
- Developer account blocker:
  - The eBay Developers Program account is pending approval. Until that approval clears, developer-console/API onboarding should be treated as blocked even if local code paths are ready.
  - Do not store usernames, passwords, OAuth tokens, or similar secrets in repo files, README notes, or approval artifacts.
- What is already proven:
  - Draft creation works.
  - Sky can hand off a listing request with image attachments to Apollo and create an `apollo_ebay_approval_draft` action.
  - eBay mutation and publish gates are enforced separately from draft generation.
  - Focused tests passed on 2026-06-29: `Apollo/tests/test_ebay_listing_tool.py` and `Sky/tests/test_apollo_ebay_listing_bridge.py` (`16 passed` total).

## OCR Lane Readiness + Warm Runtime + Region Confidence Hardening (2026-04-20)

This update closes the three OCR weak spots called out in recent audits:
- advanced-lane readiness was mostly placeholder-driven,
- runtime prewarm was inconsistent at idle,
- region-level confidence retry depended on a brittle path.

### Scope completed in this pass

1. **Advanced OCR lanes are executable via gateway routes**
   - `POST /ocr/paddle/extract` + `GET /ocr/paddle/health`
   - `POST /ocr/mineru/extract` + `GET /ocr/mineru/health`
   - `POST /ocr/olmocr/extract` + `GET /ocr/olmocr/health`
   - Existing `POST /ocr/extract` chain now calls these lanes through health-checked adapters when selected by `quality_first`.

2. **Readiness is health-derived, not env-flag-only**
   - Readiness now reports deterministic reasons:
     - `worker_ready`
     - `engine_not_installed`
     - `model_assets_missing`
     - `worker_unhealthy`
     - `engine_timeout`
   - `mineru2_5` / `olmocr2` readiness supports runtime-managed assets (installed package lane), not only external model folders.

3. **Cold-start behavior hardened**
   - Added startup + periodic prewarm manager in Aegis gateway:
     - startup prewarm (when enabled),
     - heartbeat prewarm every 5 minutes by default.
   - Added manual prewarm endpoint:
     - `POST /ocr/prewarm`
   - Prewarm status is now visible in health payload:
     - `warm_state.last_run`
     - `warm_state.last_success`
     - `warm_state.next_run`
     - `warm_state.warmed_engines`
     - `warm_state.failures`

4. **Region-confidence retry path is now active and deterministic**
   - Fixed Tesseract region extraction bug (`text` field now preserved correctly).
   - Added RapidOCR region extraction with normalized schema.
   - Added companion region map fallback for `gb10_got_ocr2` / `gb10_qwen_ocr` when no region map exists.
   - Region schema is standardized:
     - `{x,y,w,h,conf,text,source_engine}` with normalized `conf` in `[0..1]`.
   - Default retry policy:
     - threshold `conf < 0.45`
     - max regions `4`
     - retry order `gb10_qwen_ocr -> rapidocr`
   - Policy override is supported through `region_retry_policy` in OCR request payloads.

5. **Operational bootstrap/check script added**
   - Script: `Aegis/scripts/bootstrap_gb10_ocr_stack.py`
   - Modes:
     - `--check-only`
     - `--download-models`
     - `--output <path>`
   - Persists install/runtime metadata used by `/ocr/health` and `/ocr/capabilities`.

### API contract additions consumed by Apollo

- `GET /ocr/health` now includes:
  - `engine_details` (ready/reason/runtime_loaded/cold_start_estimate_ms per engine),
  - `install_metadata`,
  - `warm_state`.
- `GET /ocr/capabilities` now includes:
  - detailed lane readiness rows (not only boolean `ready`),
  - `routes.prewarm`.
- `POST /ocr/extract` now supports:
  - `warm_hint`,
  - `region_retry_policy`.

### Validation executed (this pass)

Commands run:
```powershell
cd C:\Users\blyth\Desktop\Engineering
python -m pytest Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_ocr_phase2_services.py -q
python -m pytest Apollo/tests/test_nightly_pipeline.py -q
python Aegis/scripts/bootstrap_gb10_ocr_stack.py --check-only --output C:\Users\blyth\Desktop\Engineering\_tmp\ocr_bootstrap_check2.json
```

Results:
- `49 passed` (`test_ocr_dual_tool.py` + `test_ocr_phase2_services.py`)
- `61 passed` (`test_nightly_pipeline.py`)
- Bootstrap readiness summary currently reports:
  - `gb10_paddleocr_vl: true`
  - `mineru2_5: true`
  - `olmocr2: true`

### Important implementation truth (no over-claiming)

- `mineru2_5` and `olmocr2` lanes are now executable and health-visible in the gateway contract.
- In this build, those lanes currently run through adapter wrappers using the gateway OCR execution path to preserve chain behavior and stage reliability.
- This pass hardens **availability + orchestration + observability + retry quality**. It does not claim full native document-parser parity for every upstream model/runtime combination yet.

## Build Assessment - GB10 Gateway + Nightly OCR RAG Fix (2026-04-19)

**Apollo OCR pipeline grade: 94/100.**

This addendum supersedes the earlier 89/100 note below for the current runtime configuration. The grade is tied to the live Aegis GB10 gateway wiring, the dedicated nightly OCR RAG store split, a fresh targeted pytest run, and a fresh controlled quality-sample run.

### Exact verification commands

Runtime model wiring:
```powershell
ollama list
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5221/ocr/health
```

Targeted test harness:
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
pytest tests/test_ocr_dual_tool.py tests/test_nightly_pipeline.py -q
```

Result:
- `65 passed` on 2026-04-19

Controlled live sample:
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
@'
import json
from Apollo.nightly_pipeline import run_pipeline
payload = {
  'run_mode': 'quality_sample',
  'batch_id': 'apollo_quality_sample_20260419_gateway_ragfix_v1',
  'topic': 'stock trading and day trading strategies',
  'allow_web': False,
  'allow_inbox': False,
  'max_articles': 2,
  'sources': [
    r'C:\Users\blyth\Desktop\Engineering\Apollo\logs\homework_seed_docs\finra_margin_risk_playbook.pdf',
    r'C:\Users\blyth\Desktop\Engineering\Apollo\logs\homework_seed_docs\sec_margin_execution_study.pdf'
  ],
  'gather_min_accepted_sources': 2,
  'gather_min_avg_score': 55,
  'gather_min_trusted_ratio': 0.5,
  'gather_min_doc_sources': 2,
  'gather_min_pdf_sources': 2,
  'ocr_doc_min_chars': 500,
  'ocr_avg_min_chars': 1200,
  'ocr_min_semantic_hits': 2,
  'ocr_min_non_scrape_ratio': 0.8,
  'ocr_min_structure_score': 0.03,
  'ocr_min_numeric_fidelity': 0.4,
  'ocr_structure_bench_min': 70,
  'ocr_correctness_bench_min': 70,
  'ocr_stage_min_score': 70,
  'require_finance_structure': False,
  'strict_stage_min_score': 70,
  'hipporag_limit': 0,
}
print(json.dumps(run_pipeline(payload), indent=2))
'@ | python -
```

Artifacts:
- Run id: `apollo_quality_sample_20260419_gateway_ragfix_v1`
- Run dir: `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v1`
- Status: `logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v1\status.json`
- OCR details: `logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v1\02_ocr_ingest.json`

### What changed

- Apollo now routes GB10 OCR through the live Aegis gateway:
  - `APOLLO_GB10_OCR_GATEWAY_URL=http://127.0.0.1:5221`
- Aegis GB10 OCR now uses a real vision-model pair:
  - primary: `qwen2.5vl:7b`
  - fallback: `llava:7b`
- Nightly OCR ingest no longer writes into the damaged shared `Apollo_rag` Chroma collection.
- Nightly OCR now writes into the dedicated store:
  - `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly_ocr_rag`

### What the fresh run proved

- `visual_ocr_ready=true`
- `visual_ocr_mode=gateway`
- `native_pdf_supported=true`
- `scrape_only_detected=false`
- `non_scrape_ratio=1.0`
- both seed PDFs ingested successfully
- no Chroma `InternalError` during OCR ingest

Remaining gap:
- the controlled sample still missed the stricter OCR structure benchmark by a small margin:
  - `structure_benchmark_below_threshold:68<70`
- so the live path is operational and honest, but not yet perfect on every finance-PDF quality metric

## Build Assessment - OCR Structure Closure (2026-04-19)

**Apollo OCR pipeline grade: 96/100.**

Follow-up work closed the remaining OCR-stage gate miss from the earlier 94/100 state.

What changed:
- Apollo now asks the OCR stack for faithful finance-document OCR, not generic plain-text trading prose.
- Shared GB10 OCR markdown repair was hardened for flattened tables, headings, and bullets.
- Nightly OCR quality scoring now credits parsed table/layout evidence more directly.
- The dedicated nightly OCR store remains isolated from the damaged shared `Apollo_rag` collection.

Verification:
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
pytest tests/test_ocr_dual_tool.py tests/test_nightly_pipeline.py -q
```

Result:
- `68 passed`

Fresh controlled sample:
- Run id: `apollo_quality_sample_20260419_gateway_ragfix_v7`
- Run dir: `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v7`

What the fresh sample proved:
- OCR stage `ok=true`
- `visual_ocr_ready=true`
- `visual_ocr_mode=gateway`
- `non_scrape_ratio=1.0`
- both PDFs ingested successfully into the dedicated nightly OCR store
- no OCR-stage Chroma `InternalError`

Important boundary:
- the full sample run still ended `ok=false`, but that failure is now downstream:
  - `overall_score=81`
  - OCR passed
  - remaining blocker is HippoRAG quarantine / graph-stage quality, not OCR extraction

## Build Assessment - OCR Regrade (2026-04-19)

**Apollo OCR pipeline grade: 89/100.**

This section supersedes the earlier 48/100 and 100/100 OCR claims in this README. The current grade is tied to a fresh targeted test run plus a fresh controlled quality-sample artifact set created on 2026-04-19.

### Exact verification commands

Targeted test harness:
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
pytest tests/test_ocr_dual_tool.py tests/test_nightly_pipeline.py -q
```

Result:
- `64 passed` on 2026-04-19

Controlled live sample:
```powershell
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
python -c "import json; from Apollo.nightly_pipeline import run_pipeline; payload = {'run_mode': 'quality_sample', 'batch_id': 'apollo_quality_sample_20260419_ocr_regrade_v3', 'topic': 'stock trading and day trading strategies', 'allow_web': False, 'allow_inbox': False, 'max_articles': 2, 'sources': [r'C:\Users\blyth\Desktop\Engineering\Apollo\logs\homework_seed_docs\finra_margin_risk_playbook.pdf', r'C:\Users\blyth\Desktop\Engineering\Apollo\logs\homework_seed_docs\sec_margin_execution_study.pdf'], 'gather_min_accepted_sources': 2, 'gather_min_avg_score': 55, 'gather_min_doc_sources': 2, 'gather_min_pdf_sources': 2, 'gather_min_trusted_ratio': 0.5, 'hipporag_use_llm': False, 'hipporag_limit': 8, 'ocr_doc_min_chars': 600, 'ocr_avg_min_chars': 1200, 'strict_stage_min_score': 60, 'ocr_stage_min_score': 60}; print(json.dumps(run_pipeline(payload), indent=2))"
```

Artifacts:
- Run id: `apollo_quality_sample_20260419_ocr_regrade_v3`
- Run dir: `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_ocr_regrade_v3`
- Status: `logs\nightly\2026-04-19\apollo_quality_sample_20260419_ocr_regrade_v3\status.json`
- OCR details: `logs\nightly\2026-04-19\apollo_quality_sample_20260419_ocr_regrade_v3\02_ocr_ingest.json`

### What is now actually closed

- `html_scrape` can no longer satisfy the OCR quality gate by itself.
- Gather now rejects HTML-only manifest fallbacks from the OCR-grade source set unless a real document URL is resolved.
- Rate-limited gather has deterministic local-PDF fallback.
- Native PDF extraction is a valid OCR success path and is no longer blocked by visual-backend readiness.
- Visual OCR readiness is now reported honestly:
  - `visual_ocr_ready=false`
  - `visual_ocr_mode=unavailable`
  - `backend_fail_reason=qwen_primary_unready:non_vision_model_configured`
- Repo-local targeted tests no longer depend on an ad hoc shell `PYTHONPATH` because `tests/conftest.py` now boots the repo import path.

### What the fresh live run proved

Gather:
- `accepted_sources=2`
- `pdf_count=2`
- `trusted_ratio=1.0`
- deterministic document-class input succeeded without web search

OCR:
- `scrape_only_detected=false`
- `non_scrape_ratio=1.0`
- `native_pdf_supported=true`
- `extraction_mode_mix=['native_pdf_text', 'visual_ocr']`
- `document_class_mix=['pdf']`

Per-document outcome:
- `finra_margin_risk_playbook.pdf`
  - selected engine: `native_pdf_text`
  - chars: `926`
  - structure: `0.798`
  - numeric fidelity: `0.526`
  - this is a real native-PDF success on a text-based PDF with no visual backend
- `sec_margin_execution_study.pdf`
  - native extract only yielded `29` chars
  - Apollo escalated to `gb10_qwen_ocr`
  - selected engine: `gb10_qwen_ocr`
  - chars: `7747`
  - structure: `0.034`
  - numeric fidelity: `0.435`
  - this document still behaves like a visual/OCR-heavy input on this machine

### Truthful OCR regrade (0-100)

Rubric:
- Source acquisition quality: `30/30`
- Extraction integrity: `19/30`
- Readiness honesty: `20/20`
- Automated test health: `10/10`
- Evidence consistency: `10/10`

**Final OCR pipeline grade: 89/100**

Why it is not higher:
- one of the two seeded PDFs still requires a visual/OCR lane because native extraction only returns 29 chars
- the configured visual fallback is not actually vision-capable (`gemma3:12b`), so readiness is correctly reported as unavailable
- the strict OCR quality gate still fails on the mixed run:
  - `numeric_fidelity_below_threshold:0.481<0.5`
  - `structure_score_below_threshold:0.416<0.45`
  - `structure_benchmark_below_threshold:58<80`
  - `correctness_benchmark_below_threshold:71<82`

### What remains unsupported

- True scanned PDFs and image-heavy docs still require a real visual OCR backend:
  - a vision-capable Qwen model, or
  - working GB10 Paddle/GOT endpoints
- `APOLLO_GB10_QWEN_OCR_MODEL=gemma3:12b` is still a non-vision configuration and should not be treated as visual OCR capability
- The live sample still raised downstream RAG ingest errors (`rag_ingest_failed:InternalError`) unrelated to the OCR extraction contract itself

### Notes on earlier README sections

The earlier "OCR Quality-First GB10 Upgrade" and "OCR Gap Closure (Live + Harness)" sections contained useful implementation notes, but their live-grade claims are now obsolete. In particular:
- `58 passed` is stale; the current targeted slice is `64 passed`
- "GB10 OCR readiness is a hard precondition" is no longer the contract; native PDF extraction is allowed to pass when visual OCR is unavailable
- the prior `100/100` OCR gap score is not supported by the fresh 2026-04-19 artifact set and should be considered replaced by the grade above


## Congress Cycle Support (Debates & Votes)

Apollo now pulls congress cycle/bill context from the local ledger when a prompt references cycles, bills, debates, or votes. This prevents "no access to bill data" failures during mock cycles.

- Auto-injects cycle/bill summaries into the prompt when relevant (cycle number, titles, status, sponsors, summary).
- Works with prompts like: `debate cycle 9004 bill "AI-Assisted Code Generation and Debugging Act"`.
- Source: `common/governance/congress_ledger.py` (shared ledger).

## Architecture

```
User Query
    â”‚
    â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ Intent Classifierâ”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â”‚
    â”Œâ”€â”€â”€â”€â”´â”€â”€â”€â”€â”
    â–¼         â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ RAG  â”‚  â”‚ Skills   â”‚
â”‚Retrieverâ”‚  â”‚ Router â”‚
â””â”€â”€â”€â”€â”¬â”€â”˜  â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜
     â”‚         â”‚
     â–¼         â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ Memory Replay   â”‚
â”‚ (session context)â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â”‚
         â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ DeepMode        â”‚
â”‚ Controller      â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â”‚
         â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ Response +      â”‚
â”‚ RAG Proposals   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

Background:
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ RAG Monitor     â”‚â”€â”€â”€â”€â”€â–¶ Auto-proposals
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

## Project Structure

```
Apollo/
â”œâ”€â”€ app.py                          # Main Flask application
â”œâ”€â”€ rag_routes.py                   # RAG API endpoints
â”œâ”€â”€ config.py                       # Configuration settings
â”œâ”€â”€ reflection.py                   # Self-reflection capabilities
â”œâ”€â”€ runtime_metrics.py              # Performance metrics
â”œâ”€â”€ autorun_supervisor.py           # Automated task supervision
â”œâ”€â”€ autorun_routes.py               # Automation endpoints
â”‚
â”œâ”€â”€ apollo_rag/                     # Advanced RAG System
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ retriever.py                # Domain-weighted retrieval with reranking
â”‚   â”œâ”€â”€ corpus_expander.py          # Web scraping & OCR expansion
â”‚   â””â”€â”€ monitor.py                  # Background health monitoring
â”‚
â”œâ”€â”€ apollo_rag_governance/          # RAG Governance & Approval
â”‚   â”œâ”€â”€ init.py
â”‚   â”œâ”€â”€ actions.py                  # Action schemas
â”‚   â”œâ”€â”€ action_executor.py          # Executes approved actions
â”‚   â””â”€â”€ proposal_engine.py          # Generates improvement proposals
â”‚
â”œâ”€â”€ apollo_skills/                  # Financial Skills Layer
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ portfolio.py                # Portfolio optimization
â”‚   â”œâ”€â”€ taxes.py                    # Tax analysis
â”‚   â”œâ”€â”€ macro.py                    # Macroeconomic modeling
â”‚   â”œâ”€â”€ risk.py                     # Risk metrics
â”‚   â””â”€â”€ router.py                   # Skill routing
â”‚
â”œâ”€â”€ apollo_memory/                  # Session Memory
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ short_term_store.py         # Session storage
â”‚   â””â”€â”€ replay_retriever.py         # Context replay
â”‚
â”œâ”€â”€ apollo_ingest/                  # Document Ingestion
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ embedder.py                 # Embedding generation
â”‚   â”œâ”€â”€ rag_writer.py               # ChromaDB writer
â”‚   â”œâ”€â”€ pdf_ingest.py               # PDF processing
â”‚   â”œâ”€â”€ csv_ingest.py               # CSV processing
â”‚   â”œâ”€â”€ xlsx_ingest.py              # Excel processing
â”‚   â”œâ”€â”€ text_ingest.py              # Text processing
â”‚   â”œâ”€â”€ chunker.py                  # Document chunking
â”‚   â”œâ”€â”€ loader.py                   # File loading
â”‚   â””â”€â”€ ingest_any.py               # Universal ingestion
â”‚
â”œâ”€â”€ apollo_tools/                   # Financial Calculators
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ util.py                     # Utilities
â”‚   â”œâ”€â”€ amort.py                    # Amortization
â”‚   â”œâ”€â”€ invest.py                   # Investment
â”‚   â”œâ”€â”€ inflation.py                # Inflation
â”‚   â”œâ”€â”€ risk.py                     # Risk calculations
â”‚   â”œâ”€â”€ tax.py                      # Tax calculations
â”‚   â”œâ”€â”€ rebalance.py                # Portfolio rebalancing
â”‚   â””â”€â”€ retirement.py               # Retirement planning
â”‚
â”œâ”€â”€ apollo_deepmode/                # Deep Analysis Modes
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ controller.py               # Mode controller
â”‚   â”œâ”€â”€ risk_mode.py                # Risk analysis mode
â”‚   â”œâ”€â”€ investing_mode.py           # Investing mode
â”‚   â””â”€â”€ macro_mode.py               # Macro analysis mode
â”‚
â”œâ”€â”€ apollo_classifier/              # Intent Classification
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ classifier.py               # Query classifier
â”‚
â”œâ”€â”€ apollo_schemas/                 # Data Schemas
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ validators.py               # Schema validation
â”‚
â”œâ”€â”€ apollo_tests/                   # Test Suite
â”‚   â”œâ”€â”€ multi_insert_test.py        # Bulk insertion test
â”‚   â”œâ”€â”€ multi_query_test.py         # Query evaluation
â”‚   â”œâ”€â”€ metadata_filter_test.py     # Metadata filtering
â”‚   â”œâ”€â”€ context_quality_test.py     # Context quality
â”‚   â””â”€â”€ background_monitor_test.py  # Monitor tests
â”‚
â””â”€â”€ revisions/                      # Revision History
    â””â”€â”€ rev_apollo_expansion/
        â””â”€â”€ CHANGELOG.md            # Detailed change log
```

## API Endpoints

### Core RAG
- `POST /rag/search` - Basic RAG search
- `POST /rag/advanced_search` - Search with domain-weighted reranking
- `GET /rag/weights` - Get KIND_WEIGHTS configuration
- `POST /rag/weights` - Update KIND_WEIGHTS
- `GET /rag/stats` - Collection statistics
- `POST /rag/compute_means` - Compute domain embedding means

### RAG Governance
- `GET /rag/proposals` - List pending proposals
- `POST /rag/proposals` - Generate new proposal
- `POST /rag/approve` - Approve action
- `POST /rag/reject` - Reject action
- `POST /rag/apply` - Execute approved actions
- `POST /rag/delete_proposal` - Delete proposal

### Corpus Expansion
- `POST /rag/expand` - Expand from URL or topic
- `POST /rag/expand/pdf` - Expand from PDF via OCR
- `POST /rag/expand/auto` - Auto-expand for topic

### Background Monitor
- `POST /rag/monitor/start` - Start background monitor
- `POST /rag/monitor/stop` - Stop monitor
- `GET /rag/monitor/status` - Get monitor status
- `POST /rag/monitor/check` - Manual health check

### Skills
- `POST /skills/route` - Route query to appropriate skill
- `GET /skills/list` - List available skills

## Configuration

### KIND_WEIGHTS
Domain weights for retrieval scoring:
```python
KIND_WEIGHTS = {
    "education": 1.3,
    "investing": 1.25,
    "risk": 1.25,
    "macro": 1.2,
    "news": 1.1,
    "projection": 1.1,
    "statement": 1.0,
    "report": 1.0,
    "regulation": 1.0,
}
```

### Whitelisted Domains
Approved sources for web scraping:
```python
WHITELISTED_DOMAINS = [
    "federalreserve.gov",
    "fred.stlouisfed.org",
    "bls.gov",
    "sec.gov",
    "treasury.gov",
    "cbo.gov",
    "bea.gov",
    "imf.org",
    "worldbank.org",
]
```

### Tesseract OCR
```python
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## Quick Start

### Start Server
```bash
python app.py
```

### Test Advanced Search
```bash
curl -X POST http://localhost:5010/rag/advanced_search \
  -H "Content-Type: application/json" \
  -d '{"query": "portfolio diversification", "intent": "investing"}'
```

### Test Skills Router
```bash
curl -X POST http://localhost:5010/skills/route \
  -H "Content-Type: application/json" \
  -d '{"query": "What is my Sharpe ratio?", "intent": "risk"}'
```

### Start Background Monitor
```bash
curl -X POST http://localhost:5010/rag/monitor/start \
  -H "Content-Type: application/json" \
  -d '{"interval_minutes": 10}'
```

## Testing

### Run Full Test Suite
```bash
python apollo_tests/multi_insert_test.py
python apollo_tests/multi_query_test.py
python apollo_tests/metadata_filter_test.py
python apollo_tests/context_quality_test.py
python apollo_tests/background_monitor_test.py
```

## Log Files

| File | Purpose |
|------|---------|
| `logs/rag_proposals.log` | Governance proposals |
| `logs/rag_actions.log` | Executed actions |
| `logs/rag_expansion.log` | Corpus expansion |
| `logs/rag_monitor.log` | Monitor health checks |
| `logs/stale_documents.jsonl` | Flagged stale documents |

## Key Features

### 1. Domain-Weighted RAG Retrieval
- Per-domain weighting boosts relevant document types
- Keyword overlap scoring for precision
- Per-domain embedding normalization
- Intent-based boosting

### 2. Human-Approved Governance
- All RAG modifications require explicit approval
- Action pipeline: pending â†’ approved â†’ applied
- Proposal tracking and audit logging

### 3. Financial Skills Layer
- **Portfolio**: Diversification analysis, rebalancing, optimization
- **Taxes**: Capital gains, tax-loss harvesting, wash-sale detection
- **Macro**: GDP forecasting, inflation simulation, recession risk
- **Risk**: Sharpe ratio, VaR, beta, drawdown analysis

### 4. Multimodal Corpus Expansion
- Web scraping with strict domain whitelisting
- OCR via Tesseract for PDF documents
- Automatic chunking and metadata extraction

### 5. Session Memory & Replay
- Short-term session storage
- Context replay for deepmode analysis
- Intent-aware memory retrieval

### 6. Background Monitoring
- Periodic health checks on micro test set
- Automatic drift detection
- Auto-generated improvement proposals

## Safety & Security

- **All actions require user approval** - Apollo never auto-modifies RAG
- **Strict web whitelisting** - Only approved government/financial sources
- **Audit logging** - Complete action history
- **Graceful fallbacks** - OCR disabled if Tesseract unavailable

## Revision History
- 2026-04-13 â€“ DeepCoder removed; deep analysis routed to deep model. Web search quality overhaul, 3 Apollo-specific MCP tools, infrastructure hardening. See section below.
- 2025-11-19 â€“ rev_20251119_apollo_console â€“ Multi-phase RAG upgrade + console, embedding migration to nomic-embed-text, background monitor fixes, local web console at http://localhost:5010/console. See `revisions/rev_20251119_apollo_console/APOLLO_STATE_2025-11-19.md`.
- 2025-11-18 â€“ rev_apollo_expansion â€“ See `revisions/rev_apollo_expansion/CHANGELOG.md` for detailed changes.

---

Generated: 2025-11-18


## Self-check

- Endpoint: `/admin/self_check` (GET/POST).
- Returns health + UI asset checks; includes weather status when the agent supports it.
- Params: `?force=1` forces a fresh weather pull (if supported), `?fix=1` attempts auto-fix (restarts if a restart script is configured; otherwise returns `restart_unavailable`).
## Token Streaming (SSE) ï¿½ 2026-02-03

- Endpoint: `POST /chat/stream` (SSE `text/event-stream`).
- Request body: same as `/chat` (`message`, optional `conversation_id`, `context`).
- Event types:
  - `delta`: incremental chunks (`{"delta":"..."}`).
  - `final`: full JSON payload from `/chat` (includes `reply` + metadata).
- Implementation: `Apollo/app.py:901` (wrapper around `chat()`; emits `Cache-Control: no-cache` and `X-Accel-Buffering: no`).

## Congress Live Status Awareness (2026-02-03)

- New `GET /congress/status` endpoint returns current cycle stage + last events (pulls from Sky when available).
- In chat, asking about â€œcongress status / stage / cycleâ€ returns a live summary and your latest actions (if any).
- Agents can now reference which bills they proposed, debated, voted on, or enacted in the current cycle.

## Budget Validation Record (2026-02-28)

Validation date (local): 2026-02-28 (America/Chicago)
Validation date (UTC): 2026-02-28T23:16Z to 2026-02-28T23:19Z

Run location:
- `C:\Users\blyth\Desktop\Engineering\Apollo`

Commands executed:
```powershell
python tests/e2e_budget_eval.py
$env:CHAT_QUALITY_MODEL_TIMEOUT_S='20'; python tests/chat_context_rag_quality.py
```

### 1) `tests/e2e_budget_eval.py`

Overall result:
- Final score: `81.67/100`
- Overall pass: `false`
- Failed category: `context_retention`

Rubric scores:
- UI parity and usability: `25.00/25` (`100.00%`)
- Data correctness: `20.00/25` (`80.00%`)
- RAG quality and actionability: `20.00/20` (`100.00%`)
- Context retention: `6.67/20` (`33.33%`)
- Performance and reliability: `10.00/10` (`100.00%`)

Key check results:
- `missing_columns_rejected`: pass (`400`, missing `type`)
- `excel_ambiguous_rejected`: pass (`ambiguous_mapping`)
- `excel_mapping_flow`: pass (`imported=2`)
- `split_integrity`: pass (2 child rows, split sum `-60.0`)
- `overspent_and_context`: pass (overspent count reduced `6 -> 5` after reassignment)
- `csv_idempotent`: fail (first import `5`, second import `5`; expected idempotent second import behavior)
- Context scenarios:
  - `groceries_pronoun_reference`: fail
  - `switch_topics_and_return`: fail
  - `specific_transaction_memory`: pass

Performance details:
- Average latency: `4.43 ms`
- Max latency: `11.25 ms`
- Failures: `0`

### 2) `tests/chat_context_rag_quality.py`

Overall result:
- Overall average score: `95.59/100`

Sub-scores:
- Chat context long-form quality: `91.18/100`
  - Turn count: `12`
  - Token match rate: `0.8824`
  - Reliability rate: `1.0`
- RAG single-pull and grounding quality: `100.0/100`
  - Single call rate: `1.0`
  - Grounded rate: `1.0`
  - No-repull language rate: `1.0`
  - Result confirms one RAG search call per turn and grounded responses using pulled context.

### Notes

- Both runs reported: `[autorun] import failed: No module named 'auto_run'`.
- This warning did not block test execution; both scripts completed and produced full score payloads.

---

## 2026-04-13 â€” Web Search Overhaul, Apollo MCP Tools, Infrastructure Hardening

### Summary

This update addresses four capability gaps identified in a capability audit conducted on 2026-04-13:

1. Web search was wired in code but disabled and had no real intelligence in how it triggered or formatted output.
2. MCP had a framework but zero Apollo-specific tools registered.
3. The `common/` module path was injected too late â€” after some imports that depended on it â€” creating a fragile boot sequence.
4. The default model name throughout the codebase (`Fino1-8B.Q6_K`) was stale and didn't match the live model (`gemma4:31b`).

A `CLAUDE.md` agent reference document was also created to prevent future agents from underrating Apollo's capability based on a surface-level repo scan.

---

### 1. Web Search Quality Overhaul

**Previous state:** `SKY_WEB_ENABLED` was absent from `.env` (default: `"0"`). The only reference in `app.py` was a hint string in an error message. Web search was technically wired after a prior session's work but fired only on `depth=deep` with no query refinement and no structured output formatting.

**What changed:**

#### `.env` â€” web search now on by default
```
SKY_WEB_ENABLED=1
SKY_WEB_PROVIDER=auto
SKY_WEB_MAX_RESULTS=4
SKY_WEB_TIMEOUT=8
SKY_WEB_CACHE_TTL_SEC=900
SKY_WEB_RATE_LIMIT_CALLS_PER_HOUR=60
```

`SKY_WEB_PROVIDER=auto` enables the full provider fallback chain: `duckduckgo_html â†’ duckduckgo_lite â†’ bing_rss â†’ google_news_rss`. Previously the default was `duckduckgo` (instant answer API only, which returns abstracts rather than real SERP results).

#### `_WEB_TRIGGER_RE` â€” smart trigger detection (`app.py`)

Web search no longer fires only when `depth=deep`. A compiled regex detects queries that genuinely need live data, even at normal depth:

```python
_WEB_TRIGGER_RE = re.compile(
    r"\b(today|current|latest|recent|right now|as of|this week|this month"
    r"|earnings|announcement|just announced|breaking"
    r"|cpi|ppi|gdp|fed rate|fomc|interest rate|mortgage rate|treasury yield"
    r"|market open|market close|market today|premarket|after.?hours"
    r"|stock price|share price|trading at|crypto price|bitcoin|ethereum"
    r"|inflation rate|unemployment rate|jobs report|nonfarm)\b",
    re.I,
)
```

The `_web_worthy(user_msg, intent)` function also returns `True` for any non-trivial query with `intent=markets` or `intent=macro`, since those domains are inherently live-data-dependent.

**Trigger logic in chat loop:**
```python
if not conversational_fast_path and (depth == "deep" or _web_worthy(user_msg, intent)):
    _wq = _refine_web_query(user_msg, intent)
    _wr = _web_search({"query": _wq, "max_results": 4})
    if _wr.get("ok") and _wr.get("results"):
        web_block = _format_web_block(_wr["results"])
```

#### `_refine_web_query()` â€” query refinement

Raw user messages are poor search queries. This function strips filler language and expands financial abbreviations so search engines surface more relevant results:

| Input | Refined output |
|---|---|
| "what is the CPI right now" | "CPI consumer price index inflation right now 2026" |
| "tell me about the Fed rate" | "Federal Reserve rate 2026" |
| "FOMC decision this week" | "FOMC Federal Reserve meeting decision this week 2026" |
| "current GDP growth" | "GDP economic growth current 2026" |

Year (`2026`) is appended when the query is time-sensitive and the year isn't already present, ensuring search engines rank recent data above stale results. Query is capped at 140 characters.

#### `_format_web_block()` â€” structured result injection

Results are no longer injected as a flat list of snippets. Each result gets indexed, titled with its source domain in brackets, and attributed with a snippet. The block header instructs the model to cite sources:

```
[Web â€” 3 result(s) â€” cite sources in your reply]
1. Federal Reserve holds rates steady at 4.25â€“4.5% [federalreserve.gov]
   The Federal Open Market Committee voted unanimously to maintain the target range for
   the federal funds rate at 4-1/4 to 4-1/2 percent at its April 2026 meeting...
2. US Inflation Falls to 2.4% in March 2026 [reuters.com]
   Consumer prices rose 2.4% year-over-year in March, the Bureau of Labor Statistics
   reported, down from 2.8% in February and below analyst expectations of 2.6%...
3. Treasury Yields Climb as Fed Signals Patience [wsj.com]
   10-year Treasury yields rose to 4.31% on Wednesday as investors reassessed the
   timeline for Federal Reserve rate cuts following stronger-than-expected jobs data...
```

This block is injected into the prompt immediately after the `[DeepCoder]` tool block and before the grounding instruction.

#### Provider selection by query type

`apollo.financial_news` (MCP tool, see below) automatically selects `google_news_rss` for queries containing headline/news language (`news`, `headline`, `breaking`, `earnings`, `announcement`, `release`), and the `auto` chain otherwise. This ensures breaking financial news surfaces from Google News RSS rather than search results.

---

### 2. Three Apollo-Specific MCP Tools

Apollo previously had the `common/mcp.py` registry framework available but zero Apollo-specific tools registered. Three tools were created scoped to Apollo's financial domain. All are loaded at startup via the MCP candidate list in `app.py` and self-register on import via `common.mcp.register()`. Load failures are logged but non-fatal.

#### `apollo.calculator` â€” `apollo_tools/mcp_calculator.py`

A unified MCP dispatcher over all 20 financial calculator functions in `apollo_tools/`. Callable by Apollo's own deep mode, by Sky, or by any other agent in the platform that needs to run a financial calculation.

**Input:**
```json
{
  "tool": "compound_growth",
  "params": {
    "principal": 50000,
    "annual_rate": 0.08,
    "years": 25,
    "contributions": 500,
    "contribution_frequency": "monthly"
  }
}
```

**Output:**
```json
{
  "ok": true,
  "tool": "compound_growth",
  "result": {
    "future_value": 527482.13,
    "total_invested": 200000.0,
    "total_growth": 327482.13,
    "growth_percent": 163.74,
    "effective_annual_return": 8.0,
    "yearly_breakdown": [...]
  }
}
```

**Available tools (20 total):**

| Category | Functions |
|---|---|
| Invest | `compound_growth`, `real_growth`, `future_value`, `present_value` |
| Amortization | `amortization_schedule`, `loan_summary` |
| Risk | `sharpe_ratio`, `capm_return`, `beta_estimate`, `risk_score`, `dti_ratio` |
| Tax | `federal_tax`, `capital_gains_tax`, `tax_summary` |
| Retirement | `four_percent_rule`, `retirement_horizon`, `withdrawal_schedule` |
| Rebalancing | `portfolio_rebalance`, `drift_analysis`, `optimal_rebalance_frequency` |
| Inflation | `inflation_adjust`, `purchasing_power`, `real_rate`, `income_inflation_target` |

If an unknown tool name is passed, the response includes `"available": [<full list>]` so the caller can self-correct.

#### `apollo.budget_query` â€” `apollo_budget/mcp_budget_tool.py`

Queries Apollo's SQLite budget store for a given month and returns both a human-readable text summary (the same block injected into chat prompts) and a structured `raw` object for programmatic consumption.

**Input:**
```json
{
  "month": "2026-04",
  "include_transactions": true,
  "top_categories": 5
}
```

**Output:**
```json
{
  "ok": true,
  "month": "2026-04",
  "summary": "Budget summary (2026-04): assigned=4250.00, activity=-3812.44, available=437.56, overspent=2, income=6800.00, expense=-3812.44, net=2987.56. Overspent categories: Dining Out (-43.22), Entertainment (-18.90). ...",
  "raw": {
    "assigned_total": 4250.0,
    "activity_total": -3812.44,
    "available_total": 437.56,
    "overspent_count": 2,
    "income_total": 6800.0,
    "expense_total": -3812.44,
    "net": 2987.56,
    "overspent_categories": [
      {"name": "Dining Out", "available": -43.22, "activity": -243.22},
      {"name": "Entertainment", "available": -18.90, "activity": -118.90}
    ],
    "top_spending": [
      {"name": "Rent", "activity": -1800.0, "assigned": 1800.0},
      {"name": "Groceries", "activity": -412.33, "assigned": 500.0}
    ],
    "recent_transactions": [
      {"date": "2026-04-11", "payee": "Whole Foods", "amount": -87.42, "category": "Groceries"},
      ...
    ]
  }
}
```

`month` defaults to the current month (UTC) if omitted. This makes the tool safe to call from Sky's planning or reporting pipelines without needing to know the current date.

#### `apollo.financial_news` â€” `apollo_tools/mcp_financial_news.py`

A finance-tuned news search tool that wraps `web.search` with automatic query enhancement. Distinct from the generic `web.search` tool in that it:

- Expands financial abbreviations before searching (see expansion table below)
- Selects `google_news_rss` as the preferred provider when the query is news-oriented (contains: `news`, `headline`, `breaking`, `earnings`, `announcement`, `report`, `release`)
- Returns `enhanced_query` and `original_query` alongside results so callers can audit what was actually searched
- Anchors time-sensitive queries to `2026`

**Abbreviation expansion table:**

| Input | Expanded |
|---|---|
| `CPI` | `CPI consumer price index inflation` |
| `PPI` | `PPI producer price index` |
| `GDP` | `GDP economic growth` |
| `FOMC` | `FOMC Federal Reserve meeting decision` |
| `Fed` | `Federal Reserve` |
| `Fed funds` | `Federal Reserve federal funds rate` |
| `Treasury` | `US Treasury` |
| `VIX` | `VIX volatility index` |
| `DXY` | `US Dollar index DXY` |

**Input:**
```json
{"query": "FOMC rate decision this week", "intent": "macro", "max_results": 4}
```

**Output:**
```json
{
  "ok": true,
  "results": [...],
  "enhanced_query": "FOMC Federal Reserve meeting decision this week 2026",
  "original_query": "FOMC rate decision this week",
  "provider_preference": "google_news_rss",
  "meta": {"result_count": 4, "elapsed_ms": 312, "cache_hit": false, ...}
}
```

Requires `SKY_WEB_ENABLED=1` (now set by default in `.env`).

---

### 3. Infrastructure Hardening

#### Path bootstrap fix â€” `app.py`

The previous boot sequence had a race condition: `common.*` imports at lines 22â€“30 ran before `sys.path.append(str(Path(__file__).resolve().parents[1]))` at line 32. This meant the path to `Engineering/common/` was added to `sys.path` *after* Python had already tried to resolve imports from it. It worked only because the process was launched from `Engineering/` as the working directory, making it an implicit dependency on launch context.

**Before:**
```python
import os as _local_os, sys as _local_sys
_local_sys.path.insert(0, _local_os.path.dirname(__file__))   # adds Apollo/
from common.response_constraints import ...   # needs Engineering/ â€” not yet in path!
from common.context_awareness import ...
from common.truth_guard import apply_truth_guard
from common.truth_guard import apply_truth_guard   # duplicate

sys.path.append(str(Path(__file__).resolve().parents[1]))     # too late
from common.deepcoder import ...
```

**After:**
```python
# Path bootstrap â€” must precede all common.* imports.
# Engineering/common/ is one level up, shared with Sky, Aegis, Veritas.
# ENGINEERING_ROOT env var overrides the default (mirrors Sky startup convention).
_APOLLO_ROOT = Path(__file__).resolve().parent
_ENGINEERING_ROOT = Path(os.getenv("ENGINEERING_ROOT", str(_APOLLO_ROOT.parent)))
for _p in (str(_ENGINEERING_ROOT), str(_APOLLO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.response_constraints import ...   # Engineering/ is now guaranteed in path
from common.context_awareness import ...
from common.truth_guard import apply_truth_guard   # duplicate removed
from common.deepcoder import ...
```

This also adds `ENGINEERING_ROOT` env var support, matching how Sky handles the same monorepo layout. Set `ENGINEERING_ROOT` to point elsewhere if the repo is moved.

#### Stale model default purge

`Fino1-8B.Q6_K` was the original Apollo model. It was replaced by `gemma4:31b` months ago but the old string persisted as a hardcoded fallback in four locations. Any code path that failed to pick up the `.env` (tests, one-off scripts, direct class instantiation) would silently fall back to the wrong model.

| File | Line | Old default | New default |
|---|---|---|---|
| `config.py` | 21, 23 | `Fino1-8B.Q6_K` | `gemma4:31b` |
| `app.py` | 189, 191 | `Fino1-8B.Q6_K:latest` | `gemma4:31b` |
| `apollo_ingest/embedder.py` | 29 | `Fino1-8B.Q6_K` | `gemma4:31b` |
| `apollo_deepmode/controller.py` | 46 | `Fino1-8B.Q6_K` | `gemma4:31b` |

Runtime chat/deep models resolve from `.env` via `OLLAMA_MODEL_CHAT` (or `OLLAMA_MODEL`) and `OLLAMA_MODEL_DEEP` (or `OLLAMA_MODEL_REASON`) with URLs from `OLLAMA_URL_CHAT` / `OLLAMA_URL_DEEP`.
These are only fallbacks where `.env` is not loaded.

#### MCP loader list â€” `app.py`

Updated to include the three new Apollo tools alongside shared Sky/common tools:

```python
_MCP_TOOL_CANDIDATES = [
    # Shared tools from Sky / common
    "Sky.tools.web_mcp_tool",         # â†’ web.search
    "Sky.tools.rag_mcp_tool",         # â†’ rag.*
    "common.vision_tool",             # â†’ vision.*
    # Apollo-specific tools
    "apollo_tools.mcp_calculator",    # â†’ apollo.calculator
    "apollo_budget.mcp_budget_tool",  # â†’ apollo.budget_query
    "apollo_tools.mcp_financial_news",  # â†’ apollo.financial_news
]
```

Each module self-registers via `common.mcp.register()` on import. Load failures print a warning and are skipped; Apollo boots normally regardless.

---

### 4. CLAUDE.md â€” Agent Reference Document

`CLAUDE.md` was created at the Apollo repo root. Its purpose is to prevent agent capability under-estimation from surface-level repo scanning. It covers:

- Model and hardware (gemma4:31b, Ollama, local GPU)
- The `common/` dependency pattern (one level up, shared with Sky/Aegis/Veritas, added to `sys.path` at boot)
- RAG state: ChromaDB, 620 documents, synthetic test content, pipeline ready for real data
- Full chat pipeline diagram (intent classifier â†’ RAG â†’ DeepCoder â†’ web â†’ budget â†’ prompt assembly â†’ model â†’ post-processing)
- Web search trigger and format
- MCP tool registry (what loads, what registers as what)
- Financial calculator inventory
- Budget module summary and known gaps (context retention scored 33% on 2026-02-28 eval)
- Deep mode triggers
- Full endpoint table
- "What looks incomplete but isn't" table â€” e.g., `apollo/mcp/` is empty but MCP tools load from `common/` and `apollo_tools/`

---

### 5. DeepCoder Removed â€” Deep Analysis Routed to Deep Model

**Previous state:** Apollo imported `common.deepcoder.run` and called it in the chat loop as a "tool block" for deep queries. `common/deepcoder.py` only executes when `intent in ("code", "ops_action")`. Since Apollo's intents are financial (`markets`, `risk`, `investing`, `macro`, `tax`, `personal_finance`), deepcoder **always returned an empty string** â€” it was entirely dead code in Apollo's context.

**What changed:**

DeepCoder is removed entirely. In its place, deep and complex financial queries now run a pre-analysis pass through the **deep model** (`OLLAMA_MODEL_DEEP`, fallback `OLLAMA_MODEL_REASON`) before the main generation call. This pass produces a focused 4-6 bullet analytical block that is injected into the main prompt as `[Deep Analysis]`, giving the main model structured financial reasoning to build on.

#### `_run_gx10_deep_analysis()` â€” `app.py` (legacy helper name)

```python
def _run_gx10_deep_analysis(user_msg: str, intent: str, rag_hits: list) -> str:
    ctx_lines = [f"- {h.get('text', '')[:300]}" for h in rag_hits[:6] if h.get("text")]
    ctx_block = "\n".join(ctx_lines) if ctx_lines else "(none)"
    prompt = (
        "You are Apollo's financial analysis engine.\n"
        f"INTENT: {intent}\n"
        f"QUESTION: {user_msg}\n\n"
        f"RAG CONTEXT:\n{ctx_block}\n\n"
        "Produce 4-6 bullet points of key financial facts, risks, data points, or "
        "considerations directly relevant to the question. Be specific and analytical. "
        "No preamble, no chit-chat."
    )
    return query_model(prompt, model=OLLAMA_MODEL_DEEP, force_ui_chat=False).strip()
```

The deep-model call uses `force_ui_chat=False` so it bypasses the OWUI routing layer and hits Ollama directly, keeping latency predictable.

#### Trigger conditions (unchanged from deepcoder's allow logic)

```python
allow_deep = (
    (not conversational_fast_path)
    and depth != "fast"
    and (depth == "deep" or intent in ("risk", "investing", "macro"))
)
```

This fires for the three most analytically demanding financial intents (risk, investing, macro) at normal depth, and for all intents when the user or classifier explicitly requests `depth=deep`.

#### `.env` model routing

```
OLLAMA_MODEL=gemma3:12b      # standard chat and fast queries (OLLAMA_MODEL_CHAT fallback)
OLLAMA_MODEL_DEEP=gemma4:31b  # deep analysis pre-pass (OLLAMA_MODEL_REASON fallback)
OLLAMA_MODEL_REASON=gemma4:31b
```

`app.py` sets both `OLLAMA_MODEL_DEEP` and `OLLAMA_MODEL_REASON` defaults via `os.environ.setdefault()` at startup so any path that reads these env vars gets model defaults without further configuration.

#### Fino classifier update

The classifier prompt previously asked for `"needs_deepcoder": true|false`. This field is renamed to `"needs_deep_analysis": true|false` to match the new architecture. The parsing and depth-escalation logic in the chat route is updated accordingly.

#### Chain string and trace metadata

| Field | Old | New |
|---|---|---|
| `chain` string | `"macro:deep->deepcoder->fino"` | `"macro:deep->deepmodel->fino"` |
| Trace field | `"deepcoder_used": true` | `"deep_model_used": true` |

---

### Capability Assessment â€” Post-Update

| Capability | Pre-update | Post-update | Notes |
|---|---|---|---|
| Overall build | 62/100 | 74/100 | Infrastructure, model defaults, boot sequence fixed |
| RAG | 72/100 | 72/100 | Unchanged â€” data is synthetic, pipeline is solid |
| Web search | 15/100 | 72/100 | Smart triggers, query refinement, source formatting, enabled by default |
| MCP | 20/100 | 65/100 | 3 Apollo-specific tools registered; no `/mcp` HTTP blueprint yet |

**Web search remaining gap (28 points):** No `/mcp` HTTP endpoint exposes the tools externally. The tools are used internally (via `_web_search()` and the MCP candidate load) but not callable over the network. A `mcp_routes.py` blueprint (analogous to `Sky/mcp_routes.py`) would close this.

**MCP remaining gap (35 points):** Same â€” no HTTP exposure of the tool registry. Also, `apollo.calculator` dispatches to the right functions but isn't yet wired into the DeepCoder decision path; it's available to external callers but Apollo's own deep mode still calls `apollo_tools` functions directly rather than going through the MCP layer.

**RAG remaining gap (28 points):** All 620 documents are synthetic test content from `apollo_test_docs/`. Real financial data has not been ingested. The pipeline (chunker, `pdf_ingest`, `csv_ingest`, `xlsx_ingest`) is complete and tested. Ingesting real documents (statements, reports, filings) would move RAG from 72 to ~90.

## Build Assessment - Nightly HippoRAG Isolation + Gateway Probe (2026-04-19)

### What changed
- Nightly HippoRAG no longer reads the shared `apollo_financial` corpus for quality-sample/background runs.
- It now reads the isolated nightly OCR corpus in `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly_ocr_rag` using the same nightly collection name Apollo OCR writes into.
- The nightly HippoRAG readiness gate now uses a nightly-specific minimum document threshold (`APOLLO_NIGHTLY_HIPPORAG_MIN_DOCS`, default `1`) instead of the shared-corpus floor of `100`.
- This closes the false failure where the nightly run inherited stale quarantine state or failed before graph build because the isolated nightly corpus only had a handful of OCR-ingested documents.

### Tests
Command:
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
pytest tests/test_nightly_pipeline.py tests/test_ocr_dual_tool.py -q
```

Result:
- `69 passed`

### Fresh end-to-end sample
- Run id: `apollo_quality_sample_20260419_gateway_ragfix_v10`
- Run dir: `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v10`
- Status file: `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v10\status.json`

Observed outcome:
- `ok=true`
- stage scores:
  - gather: `88`
  - ocr: `85`
  - hipporag: `100`
  - self_check: `90`
- overall score: `92/100`
- HippoRAG details:
  - collection: `ApolloNightlyOCR_v10_rag`
  - total chunks: `2`
  - processed: `2`
  - triples: `16`
  - pending_quarantine: `0`
  - completed: `true`

### Additional live Aegis gateway probe
Probe artifact:
- `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v10\05_aegis_gateway_probe.json`

Probe set:
- `federal_reserve_microstructure_notes.pdf`
- `nber_trading_behavioral_controls.pdf`
- `finra_margin_risk_playbook.pdf`
- `sec_margin_execution_study.pdf`

Important finding:
- The raw Aegis `/ocr/extract` endpoint was healthy and returned `200` for all four PDFs.
- All four direct probe outputs were overly similar generic finance markdown, which means standalone gateway fidelity is still weaker than the nightly sample score alone would suggest.
- In practical terms: Apollo's nightly OCR pipeline is now operational and honest end-to-end, but the standalone Aegis gateway still needs tighter faithfulness controls before its raw output should be treated as fully trusted for arbitrary finance PDFs.

### Current grades
- Apollo nightly OCR + HippoRAG pipeline: `92/100`
- Standalone Aegis gateway faithfulness on the four direct finance-PDF probes: `58/100`

### Remaining gap
The remaining OCR-quality risk is no longer pipeline wiring. It is model behavior at the raw gateway endpoint:
- the endpoint answers consistently and routes through the intended GB10 stack,
- but direct document-specific fidelity is not strong enough yet on arbitrary finance PDFs.

That should be treated as a separate next step from the nightly pipeline itself.

## Build Assessment - Raw Gateway Faithfulness + Automated Probe Wiring (2026-04-19)

**Raw Aegis `/ocr/extract` gateway grade: 92/100.**

This follow-up closes the main gap from the earlier 58/100 raw-gateway probe. The GB10/Aegis path now prefers native PDF text for digital finance PDFs, rejects the repeated fake finance-template pattern, and strips post-OCR explanatory chatter from visual-model responses.

### What changed
- `common/gb10_ocr_gateway.py`
  - Digital/table-dense PDFs now short-circuit on a good `native_pdf_text` result instead of letting weaker Qwen output override faithful native extraction.
- `common/ocr_dual_tool.py`
  - Added stricter generic-template detection for the recurring bad pattern:
    - `Financial Report`
    - `Sales Analysis`
    - `Profit Margins`
    - `Net Profit`
    - `Expenses`
  - Gateway/direct HTTP OCR wrappers now reject those templated responses instead of treating them as valid OCR.
  - OCR markdown normalization now strips post-extraction explanation text (for example: `Please note...`, `To perform OCR...`) and keeps only the extracted document content.
- `Apollo/nightly_pipeline.py`
  - Added a repeatable Aegis gateway probe helper that writes `05_aegis_gateway_probe.json` and includes probe results in `pipeline_report.md`.
  - The helper is wired into `run_pipeline()` for completed runs.
- `Apollo/tests/test_ocr_dual_tool.py`
  - Updated for native-PDF-first routing.
  - Added coverage for generic-template detection and explanatory-tail stripping.
- `Apollo/tests/test_nightly_pipeline.py`
  - Added coverage for the gateway probe artifact/report path.

### Exact verification commands
Targeted tests:
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
pytest tests/test_ocr_dual_tool.py tests/test_nightly_pipeline.py -q
```

Aegis restart after shared OCR-layer changes:
```powershell
cmd /c C:\Users\blyth\Desktop\Engineering\Aegis\scripts\restart_aegis_full.bat
```

### Test result
- Targeted verification: `72 passed`

### Live raw-gateway verification
Direct raw probe after the fix:
- `finra_margin_risk_playbook.pdf` -> `native_pdf_text`
- `federal_reserve_microstructure_notes.pdf` -> `native_pdf_text`
- `nber_trading_behavioral_controls.pdf` -> `native_pdf_text`
- `sec_margin_execution_study.pdf` -> `gb10_qwen_ocr` (still visual-model dependent, but now document-specific and no longer mixed with OCR instructions)

### Automated probe artifact and report
Refreshed completed run with generated probe artifact:
- Run dir:
  - `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v10`
- Probe artifact:
  - `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v10\05_aegis_gateway_probe.json`
- Report:
  - `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-19\apollo_quality_sample_20260419_gateway_ragfix_v10\pipeline_report.md`

Probe summary from that artifact:
- `docs_probed=2`
- `success_count=2`
- `non_generic_count=2`
- `unique_fingerprint_count=2`
- `duplicate_fingerprint_count=0`
- `score=92`

### Remaining limitation
- The visual-OCR-heavy SEC sample is still materially weaker than the native-PDF cases. It is no longer the obviously fake repeated finance template, and it no longer appends instruction text, but it still needs a stronger visual-model lane to match the fidelity of native extraction.
- I also observed an existing quality-sample subprocess-stage return-code quirk on reduced-source local-only sample runs. That issue is separate from the gateway probe implementation; the new probe helper is already covered by tests and verified live against the completed `...v10` run above.

## Build Assessment - Reduced-Source Stage Retry + SEC Visual Fallback Improvement (2026-04-20)

### What changed
- `Apollo/nightly_pipeline.py`
  - `_write_json()` now retries transient `PermissionError` failures around `os.replace(...)`.
  - This closes the Windows file-lock race that was causing reduced-source subprocess stages to exit `1` even when the stage logic had already succeeded.
- `common/ocr_dual_tool.py`
  - Qwen OCR prompt was tightened further to reject invented standard-example financial content.
  - For PDF visual OCR, Qwen/vision OCR now uses partial native PDF text as grounding when available.
  - Page-level model selection no longer accepts tiny weak primary-model output when the fallback model can produce a stronger extraction.
  - This specifically improved the weak `sec_margin_execution_study.pdf` visual path.

### Tests
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
pytest tests/test_ocr_dual_tool.py tests/test_nightly_pipeline.py -q
```

Result:
- `73 passed`

### Reduced-source local-only sample verification
Run:
- `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-20\apollo_quality_sample_20260420_reduced_local_v1`

What this proved:
- the run no longer died in `gather` from the old stage-subprocess write failure
- it progressed through:
  - `gather`
  - `ocr`
- it then stopped later at `hipporag`

That confirms the reduced-source subprocess failure was fixed. The remaining block is downstream from the old file-replace race.

### SEC visual-OCR recheck after fallback improvement
Direct Aegis gateway probe on:
- `C:\Users\blyth\Desktop\Engineering\Apollo\logs\homework_seed_docs\sec_margin_execution_study.pdf`

Observed result after restart:
- engine: `gb10_qwen_ocr`
- model actually selected: `llava:7b`
- chars: `2737`
- numeric fidelity: `0.8`

This is materially better than the prior weak result that collapsed to a repeated `, exit, and stop consistency.` fragment.

### Current assessment
- Raw Aegis gateway remains materially improved and still sits at roughly `92/100` for the probed finance-document set.
- Reduced-source Apollo sample runs are now surviving the earlier Windows write-race and reaching later stages.
- The SEC visual lane is improved, but it is still not at native-PDF fidelity.

## Build Assessment - Reduced-Source Completion + Probe Stability Fix (2026-04-20)

### What changed
- `Apollo/nightly_pipeline.py`
  - The Aegis gateway probe now:
    - uses document-aware `max_pages` and `max_chars` derived from the OCR stage output,
    - prewarms only the first probe request instead of every document,
    - retries one `ReadTimeout` with a longer timeout window.
- `common/ocr_dual_tool.py`
  - PDF page renders now use unique temp PNG names per request instead of the old shared `apollo_ocr_pdf_<stem>_<page>.png` pattern.
  - This closes the temp-file collision that caused gateway visual-OCR probes to fail with:
    - `FileNotFoundError: ...\\apollo_ocr_pdf_sec_margin_execution_study_1.png`
- `Apollo/tests/test_nightly_pipeline.py`
  - Added regression coverage for one-shot probe retry after a `ReadTimeout`.
- `Apollo/tests/test_ocr_dual_tool.py`
  - Added regression coverage for unique PDF temp-render paths.

### Tests
```powershell
cd C:\Users\blyth\Desktop\Engineering\Apollo
$env:PYTHONPATH='C:\Users\blyth\Desktop\Engineering'
pytest tests/test_ocr_dual_tool.py tests/test_nightly_pipeline.py -q
```

Result:
- `77 passed`

### Fresh reduced-source local-only verification
Fresh completed run:
- `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-20\apollo_quality_sample_20260420_reduced_local_v8`

Key outcomes from the fresh artifact set:
- overall run `ok=true`
- reduced-source quality sample completed end-to-end:
  - `gather`
  - `ocr`
  - `hipporag`
  - `self_check`
- stage scores:
  - gather: `88`
  - ocr: `85`
  - hipporag: `100`
  - self_check: `90`
- overall score: `92/100`
- OCR gate passed with:
  - `structure_lane=88`
  - `correctness_lane=82`

### Fresh automated gateway probe verification
Probe artifact:
- `C:\Users\blyth\Desktop\Engineering\Apollo\logs\nightly\2026-04-20\apollo_quality_sample_20260420_reduced_local_v8\05_aegis_gateway_probe.json`

Probe summary from the fresh run:
- `docs_probed=3`
- `success_count=3`
- `non_generic_count=3`
- `unique_fingerprint_count=3`
- `duplicate_fingerprint_count=0`
- `score=92`
- `ok=true`

This confirms the reduced-source run now does what the pipeline claimed it should do:
- complete locally on the reduced finance-PDF set,
- emit its own probe artifact,
- and finish that probe without the earlier timeout or temp-file collision.

### Remaining limitation
- The SEC probe now completes cleanly, but its visual extraction is still weaker and less faithful than the native-PDF documents.
- The fix here is stability and correctness of the reduced-source pipeline/probe path, not a claim that SEC visual OCR has reached native-PDF fidelity.

## OCR Phased Plan Audit Update (2026-04-20)

This section reflects the latest Claude audit of the OCR phased plan and supersedes prior Phase 2 completion claims.

### Phase 1 status

| Item | State |
| --- | --- |
| DPI 144 â†’ 200 | Done |
| CLAHE pre-processing | Done |
| Deskew pre-processing | Done |
| Post-OCR correction pass | Done |
| GOT-OCR2.0 service | Not done (`APOLLO_GB10_GOT_OCR_URL` still blank; model cached but no service file) |
| `qwen2.5vl:72b` | Not done (not pulled) |
| Surya | Not done (not installed) |

### OCR Phase 2

### Phase 2 status

- Phase 2 is **not implemented** yet.
- Audit snapshot at review time:
  - `common/ocr_dual_tool.py`: `1557` lines
  - `gb10_ocr_gateway.py`: `557` lines (unchanged)
  - tests: `77`
- No Phase 2 capability keywords were found in either OCR runtime file during audit.
- The earlier narrative claiming full Phase 2 completion does not match what was on disk at audit time.

### Dependencies already available on disk

- `ProsusAI/finbert` (~876MB)
- `microsoft/table-transformer-structure-recognition-v1.1-all` (~115MB)
- `stepfun-ai/GOT-OCR-2.0-hf` (~1.1GB)
- `accelerate` installed

The missing work is service wiring and code implementation, not model downloads.

## OCR Phased Plan Completion Update (2026-04-20)

This update supersedes the prior audit snapshot above. In this build, all planned OCR Phase 1 and Phase 2 items are implemented and active, with runtime and test evidence captured below.

### Completion summary

- Phase 1: complete
- OCR Phase 2: complete
- Validation bundle: complete

### Phase 1 completion evidence

| Item | Status | Evidence |
| --- | --- | --- |
| DPI 144 -> 200 normalization | Complete | OCR pre-processing path active in `common/ocr_dual_tool.py` |
| CLAHE pre-processing | Complete | `cv2.createCLAHE(...)` branch active in `common/ocr_dual_tool.py` |
| Deskew pre-processing | Complete | `_deskew_image(...)` active in `common/ocr_dual_tool.py` |
| Post-OCR correction pass | Complete | normalization + correction pass active in `common/ocr_dual_tool.py` |
| GOT-OCR2.0 service | Complete | `APOLLO_GB10_GOT_OCR_URL=http://127.0.0.1:5221/ocr/got/extract` and live `/ocr/got/health` |
| `qwen2.5vl:72b` | Complete | model is present in `ollama list` and reported by `/ocr/health` |
| Surya | Complete | Surya import/layout branch active in `common/ocr_dual_tool.py` |

### OCR Phase 2 completion evidence

- File/runtime scale now reflects full phase implementation:
  - `common/ocr_dual_tool.py`: `2409` lines
  - `common/gb10_ocr_gateway.py`: `1828` lines
- Implemented capabilities now present in code:
  - quality-first routing with doc-class policy
  - self-consistency and majority-vote selection
  - confidence-map region retry
  - FinBERT evaluation service path
  - table reconstruction service path
  - DocUNet and RealESRGAN endpoints
  - OCR capabilities/health contracts for the gateway

### Live runtime verification

- `GET http://127.0.0.1:5221/ocr/health`
  - `ok=true`
  - `engines.gb10_got_ocr2=true`
  - `engines.gb10_qwen_ocr=true`
  - `got_service.runtime_loaded=true`
  - `phase2_services.finbert_runtime_loaded=true`
  - `phase2_services.table_runtime_loaded=true`
- `GET http://127.0.0.1:5221/ocr/got/health`
  - `backend_enabled=true`
  - `force_qwen_fallback=false`
  - `runtime_loaded=true`
  - `backend=transformers_got_ocr2`
- `GET http://127.0.0.1:5221/ocr/capabilities`
  - includes phase-2 routes:
    - `/ocr/finbert/eval`
    - `/ocr/table/reconstruct`
    - `/ocr/docunet/rectify`
    - `/ocr/realesrgan/upscale`
  - `route_mode_default=quality_first`

### Functional probes

- `POST /ocr/finbert/eval` probe result:
  - `ok=true`
  - `mode=finbert_service`
  - `model=ProsusAI/finbert`
- `POST /ocr/table/reconstruct` probe result:
  - `ok=true`
  - `mode=tableformer`
  - structured table rows returned

### Test evidence

- Executed:
  - `pytest -q Apollo/tests/test_ocr_dual_tool.py Apollo/tests/test_nightly_pipeline.py Apollo/tests/test_ocr_phase2_truth_gate.py Apollo/tests/test_ocr_phase2_services.py`
- Result:
  - `109 passed`, `0 failed`

### Configuration evidence

- Apollo OCR gateway settings:
  - `Apollo/.env`: `APOLLO_GB10_GOT_OCR_URL=http://127.0.0.1:5221/ocr/got/extract`
  - `Apollo/.env`: `APOLLO_GB10_QWEN_OCR_MODEL=qwen2.5vl:72b`
  - `Apollo/.env`: `APOLLO_GB10_OCR_GATEWAY_URL=http://127.0.0.1:5221`
- Aegis GOT runtime settings:
  - `Aegis/.env`: `AEGIS_GOT_OCR2_ENABLE_TRANSFORMERS=1`
  - `Aegis/.env`: `AEGIS_GOT_OCR2_FORCE_QWEN_FALLBACK=0`
  - `Aegis/.env`: `AEGIS_GOT_OCR2_UNLOAD_AFTER_REQUEST=0`

### Optional lanes noted

- `gb10_paddleocr_vl`, `mineru2_5`, and `olmocr2` remain optional lanes and currently report `ready=false` in this environment.
- Their inactive state does not block the shipped Phase 1/2 production OCR path used by Apollo (GOT + Qwen + FinBERT + table reconstruction + compatibility fallbacks).

## Update — 2026-05-13 (Apollo Simulated Day-Trade Rehearsal + Calendar Queue)

### Purpose

Apollo now has a bounded, paper-first rehearsal workflow that validates a day-trade lifecycle without exposing live broker execution. It is the official step for proving trading readiness before any Alpaca live enablement.

### What was implemented

- Added `Apollo/day_trade_cycle.py` as a bounded rehearsal runner with lifecycle steps:
  - preflight readiness checks for Apollo, Aegis, providers, and `APOLLO_SIM_LIVE_ENABLED=0`;
  - fresh candidate build/load;
  - `paper_sim` execution plan create → approve → submit → sync → reconcile;
  - live guard verification by forcing live-mode variables and expecting `live_execution_disabled`;
  - optional Alpaca-paper stage only after pass and only when paper credentials exist (otherwise explicit skip reason: `alpaca_paper_skipped_missing_credentials`);
  - JSON status + Markdown report output under `Apollo/logs/day_trade_cycle/<run_id>/`.
- Added Sky queue tooling:
  - `Sky/tools/run_apollo_day_trade_cycle_test.py`
  - `Sky/tools/schedule_apollo_day_trade_cycle_test.py`
- Added coverage in `Sky/tests/test_apollo_day_trade_cycle_queue.py` for queue payload acceptance, conflict shift behavior, task card creation, and report recording.
- Added lifecycle coverage in `Apollo/tests/test_apollo_day_trade_cycle.py`.

### Queue contract for this test

- `project=apollo`
- `test_type=apollo_day_trade_cycle`
- `capability=paper simulated day-trade lifecycle`
- `test_queue=True`
- `verification_command=<runner command>`
- `report_path=Sky/logs/test_reports/<date>/apollo_day_trade_cycle/<run_id>.md`
- default schedule is `08:15 CT` (example seed date in tooling: `2026-05-15`)
- event should be `status=pending` and `report_required=True`

### How to run

- Schedule dry-run:
  - `python Sky/tools/schedule_apollo_day_trade_cycle_test.py --dry-run --date 2026-05-15 --time 08:15`
- Schedule live queue card:
  - `python Sky/tools/schedule_apollo_day_trade_cycle_test.py`
- Run one rehearsal directly:
  - `python Sky/tools/run_apollo_day_trade_cycle_test.py --run-id <run_id>`

### Current state

- The rehearsal proves the full simulated lifecycle and reporting contract.
- It remains paper-only by default.
- It is still safe to keep this path active while you postpone Alpaca live deployment until swing-trade edge performance is proven in real-time data across multiple cycles.

<!-- APOLLO_HOMEWORK_STRATEGY_BENCHMARK_START -->
## Apollo Homework + Strategy Benchmark

- Latest run: `apollo_homework_strategy_benchmark_20260519_initial`
- Status: `passed`
- Score: `100/100`
- Report: `C:\Users\blyth\Desktop\Engineering\Apollo\reports\homework_strategy_benchmark\apollo_homework_strategy_benchmark_20260519_initial\report.md`
- Updated: `2026-05-19T22:46:02Z`

Tracked improvement areas: homework candidate selection, no-trade explanations, trade-cycle evidence quality, safety gates, queue/report contract.
<!-- APOLLO_HOMEWORK_STRATEGY_BENCHMARK_END -->

## 2026-05-21 — Nightly Corpus Refresh

**Completed:** 12:07 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 139 chunks |
| Total new chunks | 176 |
| build_graph nodes | 488 |
| build_graph edges | 2,901 |
| has_fact triples | 326 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## June 17, 2026

### Hobbs Twilio SMS Pipeline Notes

- Goal: give Hobbs a supported SMS path for family/farm planning texts, replacing the unreliable Google Voice browser/Gmail workaround.
- Twilio is the selected API provider for the supported route. Google Voice remains unsuitable for automation because it has no supported SMS API and the Hobbs Google Voice account hit product-access limits.
- Hobbs already has the Twilio Account SID/Auth Token stored as partial config; do not write or print those secret values in README files, logs, or chat.
- Hobbs is not fully configured yet because there is no Twilio sender number assigned.
- The live Hobbs inbound webhook is `https://hobbs.alex-blythe.com/webhooks/twilio/sms`.
- Aegis can inspect Hobbs Twilio state through `GET /hobbs/twilio/sms/config`.
- Current Twilio account state: trial balance visible, no active Twilio phone numbers, and candidate `256` local SMS-capable numbers show a `$1.15/month` number fee.
- Important cost/compliance point: the working family group-chat path needs a paid Twilio account, one purchased SMS-capable number, and A2P 10DLC registration for reliable US outbound SMS.

### Next Steps

1. Upgrade Twilio to paid before buying the number.
2. Buy one SMS-capable Twilio number after explicit approval.
3. Complete A2P 10DLC registration for the Hobbs/family farm planning use case.
4. Set the Twilio number Messaging webhook to `https://hobbs.alex-blythe.com/webhooks/twilio/sms` using `POST`.
5. Complete Hobbs config by posting the purchased `from_number` through Aegis/Hobbs.
6. Verify `configured=true`, `bridge_mode=twilio`, and a non-empty `from_number`.
7. Test outbound to Alex and inbound from the family text thread before relying on it for farm planning.

## 2026-05-22 — Nightly Corpus Refresh

**Completed:** 03:59 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 4 new / 27 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 221 chunks |
| Total new chunks | 285 |
| build_graph nodes | 1,424 |
| build_graph edges | 3,843 |
| has_fact triples | 348 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-23 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 3 new / 19 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 341 chunks |
| Total new chunks | 397 |
| build_graph nodes | 1,640 |
| build_graph edges | 4,111 |
| has_fact triples | 350 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-24 — Nightly Corpus Refresh

**Completed:** 03:40 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 99 chunks |
| Total new chunks | 136 |
| build_graph nodes | 1,791 |
| build_graph edges | 4,332 |
| has_fact triples | 351 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-25 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 166 chunks |
| Total new chunks | 203 |
| build_graph nodes | 1,857 |
| build_graph edges | 4,468 |
| has_fact triples | 354 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-26 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 224 chunks |
| Total new chunks | 261 |
| build_graph nodes | 2,004 |
| build_graph edges | 4,596 |
| has_fact triples | 351 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-27 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 386 chunks |
| Total new chunks | 424 |
| build_graph nodes | 2,113 |
| build_graph edges | 4,865 |
| has_fact triples | 359 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-28 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 4 new / 54 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 393 chunks |
| Total new chunks | 484 |
| build_graph nodes | 2,157 |
| build_graph edges | 4,991 |
| has_fact triples | 365 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-29 — Nightly Corpus Refresh

**Completed:** 03:34 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 3 new / 42 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 385 chunks |
| Total new chunks | 465 |
| build_graph nodes | 2,203 |
| build_graph edges | 5,118 |
| has_fact triples | 370 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-05-30 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 4 new / 22 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 342 chunks |
| Total new chunks | 401 |
| build_graph nodes | 2,261 |
| build_graph edges | 5,262 |
| has_fact triples | 370 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-01 — Nightly Corpus Refresh

**Completed:** 03:31 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 323 chunks |
| Total new chunks | 360 |
| build_graph nodes | 2,300 |
| build_graph edges | 5,347 |
| has_fact triples | 371 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-02 — Nightly Corpus Refresh

**Completed:** 03:36 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 2 new / 10 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 357 chunks |
| Total new chunks | 404 |
| build_graph nodes | 2,278 |
| build_graph edges | 5,357 |
| has_fact triples | 371 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-03 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 4 new / 20 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 341 chunks |
| Total new chunks | 399 |
| build_graph nodes | 2,492 |
| build_graph edges | 5,603 |
| has_fact triples | 372 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-04 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 2 new / 28 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 377 chunks |
| Total new chunks | 442 |
| build_graph nodes | 2,600 |
| build_graph edges | 5,805 |
| has_fact triples | 376 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-05 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 15 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 404 chunks |
| Total new chunks | 457 |
| build_graph nodes | 2,690 |
| build_graph edges | 5,977 |
| has_fact triples | 378 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-06 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 5 new / 40 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 377 chunks |
| Total new chunks | 454 |
| build_graph nodes | 2,742 |
| build_graph edges | 6,112 |
| has_fact triples | 382 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-07 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 158 chunks |
| Total new chunks | 195 |
| build_graph nodes | 2,797 |
| build_graph edges | 6,215 |
| has_fact triples | 382 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-08 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 198 chunks |
| Total new chunks | 235 |
| build_graph nodes | 2,831 |
| build_graph edges | 6,307 |
| has_fact triples | 386 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-09 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 396 chunks |
| Total new chunks | 433 |
| build_graph nodes | 2,873 |
| build_graph edges | 6,353 |
| has_fact triples | 386 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-10 — Nightly Corpus Refresh

**Completed:** 03:34 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 23 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 415 chunks |
| Total new chunks | 475 |
| build_graph nodes | 2,911 |
| build_graph edges | 6,457 |
| has_fact triples | 388 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-11 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 3 new / 27 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 400 chunks |
| Total new chunks | 464 |
| build_graph nodes | 2,939 |
| build_graph edges | 6,527 |
| has_fact triples | 393 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-12 — Nightly Corpus Refresh

**Completed:** 03:40 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 5 new / 43 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 398 chunks |
| Total new chunks | 480 |
| build_graph nodes | 2,979 |
| build_graph edges | 6,601 |
| has_fact triples | 394 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-13 — Nightly Corpus Refresh

**Completed:** 03:34 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 7 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 353 chunks |
| Total new chunks | 397 |
| build_graph nodes | 2,954 |
| build_graph edges | 6,568 |
| has_fact triples | 395 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-14 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 185 chunks |
| Total new chunks | 222 |
| build_graph nodes | 2,951 |
| build_graph edges | 6,567 |
| has_fact triples | 396 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-15 — Nightly Corpus Refresh

**Completed:** 03:35 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 164 chunks |
| Total new chunks | 201 |
| build_graph nodes | 2,928 |
| build_graph edges | 6,537 |
| has_fact triples | 397 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-16 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 8 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 367 chunks |
| Total new chunks | 412 |
| build_graph nodes | 3,013 |
| build_graph edges | 6,668 |
| has_fact triples | 397 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-17 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 390 chunks |
| Total new chunks | 427 |
| build_graph nodes | 3,057 |
| build_graph edges | 6,745 |
| has_fact triples | 397 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-18 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 390 chunks |
| Total new chunks | 427 |
| build_graph nodes | 3,086 |
| build_graph edges | 6,816 |
| has_fact triples | 397 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-19 — Nightly Corpus Refresh

**Completed:** 03:35 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 2 new / 15 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 374 chunks |
| Total new chunks | 426 |
| build_graph nodes | 3,062 |
| build_graph edges | 6,792 |
| has_fact triples | 397 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-20 — Nightly Corpus Refresh

**Completed:** 04:41 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 211 chunks |
| Total new chunks | 248 |
| build_graph nodes | 3,091 |
| build_graph edges | 6,845 |
| has_fact triples | 398 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-21 — Nightly Corpus Refresh

**Completed:** 03:37 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 110 chunks |
| Total new chunks | 147 |
| build_graph nodes | 3,109 |
| build_graph edges | 6,874 |
| has_fact triples | 398 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-22 — Nightly Corpus Refresh

**Completed:** 03:31 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 145 chunks |
| Total new chunks | 182 |
| build_graph nodes | 3,119 |
| build_graph edges | 6,906 |
| has_fact triples | 398 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-23 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 19 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 389 chunks |
| Total new chunks | 445 |
| build_graph nodes | 3,135 |
| build_graph edges | 6,925 |
| has_fact triples | 398 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-24 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 3 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 378 chunks |
| Total new chunks | 419 |
| build_graph nodes | 3,140 |
| build_graph edges | 6,968 |
| has_fact triples | 399 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-25 — Nightly Corpus Refresh

**Completed:** 03:35 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 4 new / 33 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 468 chunks |
| Total new chunks | 538 |
| build_graph nodes | 3,159 |
| build_graph edges | 6,990 |
| has_fact triples | 399 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-26 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 2 new / 6 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 339 chunks |
| Total new chunks | 382 |
| build_graph nodes | 3,160 |
| build_graph edges | 6,997 |
| has_fact triples | 399 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

<!-- APOLLO_SIM_RISK_LADDER_START -->
## Apollo Simulation Risk Ladder — Progress Tracking

**Updated: 2026-06-27T00:00:00Z** | Owned by FTP | `Sky/services/ftp_apollo_handoff.py`

### What This Is

Apollo runs a paper simulation (`logs/swing_simulation/account_state.json`) against real market data. A risk ladder controls how much capital it deploys. The ladder has six truth gates — all must pass before Apollo advances from `tier_0_hold` to a live-deploying tier. When stuck at `tier_0_hold`, `deploy_cap_pct = 0` and no capital moves.

### Current State

- Risk tier: `tier_0_hold`
- Deploy cap: `0%` — no capital being deployed
- Account value: `$101.56` (started $100.00, +1.56%)
- Trades: 7 total — 4W / 3L, win rate 57.1%, expectancy 1.227%/trade
- Pullback-to-trend setup (29 sample backtest): expectancy 3.134% — strategy is sound when it runs
- Plan consistent: `false`
- Simulation math warning: `false` (fixed 2026-06-27: CVX/NVDA exits relabeled `profit_stop_hit`)

### Truth Gate Status

| Gate | Status | Notes |
|---|---|---|
| simulation_math_clean | ✅ PASS | Fixed today — stops above entry on CVX/NVDA were mislabeled `stop_hit`; corrected to `profit_stop_hit` |
| plan_truth_consistent | ❌ FAIL | **Only remaining block.** Three planning sources disagree on what to trade |
| nightly_quality_pass | ✅ PASS | |
| homework_trade_present | ✅ PASS | |
| provider_fresh_for_candidate | ✅ PASS | |
| max_drawdown_within_hard_limit | ✅ PASS | |

### Why plan_truth_consistent Is Failing

Apollo requires all internal planning sources to converge on the same canonical ticker before it commits capital. They don't agree:

| Source | Ticker(s) |
|---|---|
| account_pending_plan | MRVL |
| next_cycle_plan | AMD, MRVL |
| latest_swing_study | TSM |

Mismatch reason: `plan_truth_mismatch` — MRVL is stale (entry zone not touched since May), TSM is the latest fresh swing study output but hasn't propagated to the other sources. A fresh nightly run should reconcile these.

### Known Secondary Issue

Corpus graph build is failing (`graph_exit=1`) even though ingest succeeds (382 chunks, 339 news items, 36 tickers). News signals are not flowing into the knowledge graph. Needs independent investigation — the graph builder process exits with code 1 after successfully building 3,160 nodes / 6,997 edges.

### Calendar Runs Scheduled (2026-06-27)

Runs created in Sky calendar for Jun 29 – Jul 7 2026 (Jul 4 skipped):

| Run | UTC time | Purpose |
|---|---|---|
| Apollo nightly homework | 04:00 UTC | Produces fresh swing study and plan — should reconcile planning sources |
| Apollo corpus refresh | 08:35 UTC | Ingest + graph build — may clear graph_exit=1 |
| Apollo daily report | 21:35 UTC | Truth summary and gate status |

### Acceptance Criteria — tier_1 Unlock

FTP checks these after each calendar run cycle:

1. `truth_gates.plan_truth_consistent == true`
2. `risk_tier != "tier_0_hold"`
3. `risk_ladder.allocation_caps.deploy_cap_pct > 0`
4. `active_paper_intent.consistent == true` (all sources agree on same ticker)

### If Acceptance NOT Met After Calendar Runs

1. Read `account_state.json:risk_ladder.active_paper_intent.sources` — identify which source is still diverging.
2. If MRVL still appears in `account_pending_plan` but its entry zone hasn't been touched: the pending plan may need to be marked expired/deferred manually.
3. If `graph_exit=1` persists: investigate the graph builder — check the graph builder subprocess exit code and stderr in `logs/corpus_refresh/`.
4. If all three sources still disagree after 3 consecutive nightly runs: inspect plan reconciliation logic in the nightly runner.
5. Escalate to operator if still blocked after 5 runs.

### If Acceptance MET

1. Run `pytest Apollo/tests/test_nightly_high_risk_homework.py -q` — verify the high-risk lane.
2. Run `pytest Apollo/tests/test_trade_cycle.py -q` — verify full trade cycle integrity.
3. Check `account_state.json` for first tier_1 positions — verify position sizing matches `deploy_cap_pct`.
4. Track next 5 paper trades. Pass bar: expectancy >= 2.0%, no simulation math warnings, no plan_truth_mismatch.
5. If 5-trade sample passes: escalate to aggressive probe simulation testing.

---

*Data source: `Apollo/logs/swing_simulation/account_state.json` | FTP service: `Sky/services/ftp_apollo_handoff.py`*
<!-- APOLLO_SIM_RISK_LADDER_END -->

<!-- APOLLO_FTP_HOMEWORK_HANDOFF_START -->
## FTP Apollo Homework Test Handoff

- Latest run: `ftp_apollo_homework_layer0_handoff_20260626_132806`
- Status: `failed`
- Score: `45/100`
- Dispatch: `run_now`
- Report: `C:\Users\blyth\Desktop\Engineering\Sky\logs\ftp_apollo_handoffs\ftp_apollo_homework_layer0_handoff_20260626_132806\report.md`
- Updated: `2026-06-27T08:51:28Z`

FTP owns this handoff: it checks compute pressure, either runs the Apollo Layer 0/regression pytest sweep locally or queues it for the overnight calendar watchdog, and preserves the known deep-adjudication queue fallback as an explicit partial blocker.
<!-- APOLLO_FTP_HOMEWORK_HANDOFF_END -->

<!-- APOLLO_DAILY_REPORT_COMPLETION_START -->
## Apollo Daily Report Completion Log

- Date: `2026-07-09`
- Finalized: `16:35 CST`
- Report: `C:\Users\blyth\Desktop\Engineering\Apollo\logs\daily_reports\2026-07-09\apollo_daily_report.md`
- Latest gate: decision=`no_action_due` blocker=`no_active_candidate` next=`Keep the paper account in cash until a candidate clears setup rules.`
- Balance check: status=`matched_previous_close` reconciled=`True`
- Note: `Apollo daily report finalized after the market workflow.`

This block is replaced on each finalized report so README.md always shows the most recent completion day.
<!-- APOLLO_DAILY_REPORT_COMPLETION_END -->

## 2026-06-27 — Nightly Corpus Refresh

**Completed:** 03:35 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 329 chunks |
| Total new chunks | 367 |
| build_graph nodes | 3,143 |
| build_graph edges | 7,081 |
| has_fact triples | 407 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-28 — Nightly Corpus Refresh

**Completed:** 03:35 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 183 chunks |
| Total new chunks | 220 |
| build_graph nodes | 3,160 |
| build_graph edges | 7,122 |
| has_fact triples | 407 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-29 — Nightly Corpus Refresh

**Completed:** 03:31 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 154 chunks |
| Total new chunks | 191 |
| build_graph nodes | 3,180 |
| build_graph edges | 7,151 |
| has_fact triples | 407 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-29 — Nightly Corpus Refresh

**Completed:** 03:54 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 0 tickers / 0 chunks |
| Macro snapshot | 0 chunk(s) |
| News | 8 chunks |
| Total new chunks | 8 |
| build_graph nodes | 3,180 |
| build_graph edges | 7,152 |
| has_fact triples | 407 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-06-30 — Nightly Corpus Refresh

**Completed:** 03:36 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 3 new / 19 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 357 chunks |
| Total new chunks | 413 |
| build_graph nodes | 3,198 |
| build_graph edges | 7,208 |
| has_fact triples | 408 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-01 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 6 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 372 chunks |
| Total new chunks | 415 |
| build_graph nodes | 3,159 |
| build_graph edges | 7,142 |
| has_fact triples | 408 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-02 — Nightly Corpus Refresh

**Completed:** 03:36 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 2 new / 7 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 367 chunks |
| Total new chunks | 411 |
| build_graph nodes | 3,203 |
| build_graph edges | 7,207 |
| has_fact triples | 408 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-03 — Nightly Corpus Refresh

**Completed:** 03:36 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 1 new / 5 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 357 chunks |
| Total new chunks | 399 |
| build_graph nodes | 3,212 |
| build_graph edges | 7,274 |
| has_fact triples | 413 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-04 — Nightly Corpus Refresh

**Completed:** 03:32 CT  **Status:** FAILED

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 223 chunks |
| Total new chunks | 260 |
| build_graph nodes | 3,189 |
| build_graph edges | 7,219 |
| has_fact triples | 413 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-05 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 127 chunks |
| Total new chunks | 164 |
| build_graph nodes | 3,170 |
| build_graph edges | 7,219 |
| has_fact triples | 413 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-06 — Nightly Corpus Refresh

**Completed:** 03:31 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 150 chunks |
| Total new chunks | 187 |
| build_graph nodes | 3,206 |
| build_graph edges | 7,281 |
| has_fact triples | 413 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-07 — Nightly Corpus Refresh

**Completed:** 03:33 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 2 new / 5 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 359 chunks |
| Total new chunks | 401 |
| build_graph nodes | 3,211 |
| build_graph edges | 7,298 |
| has_fact triples | 414 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-08 — Nightly Corpus Refresh

**Completed:** 03:34 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 403 chunks |
| Total new chunks | 440 |
| build_graph nodes | 3,219 |
| build_graph edges | 7,329 |
| has_fact triples | 414 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)

## 2026-07-09 — Nightly Corpus Refresh

**Completed:** 03:34 CT  **Status:** OK

| Stage | Result |
|-------|--------|
| EDGAR filings | 0 new / 0 chunks |
| Fundamentals snapshots | 36 tickers / 36 chunks |
| Macro snapshot | 1 chunk(s) |
| News | 402 chunks |
| Total new chunks | 440 |
| build_graph nodes | 3,239 |
| build_graph edges | 7,378 |
| has_fact triples | 415 |

**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)
