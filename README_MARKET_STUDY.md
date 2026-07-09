# Apollo Market And Swing Study

Apollo's market-study lane is research-only. It produces structured trade-consideration proposals, but every proposal includes `human_approval_required=true` and `live_trade_execution=false`.

## Run

Manual run:

```powershell
py -3 -m Apollo.market_study run --tickers "NVDA,AMD,AVGO"
py -3 -m Apollo.swing_study run --tickers "NVDA,AMD,AVGO,MSFT,AMZN"
```

Scheduled wrapper:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_market_study_0730.ps1 -Notify
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_swing_study_1620.ps1 -Notify
```

Windows task:

```text
ApolloMarketStudy0730
Weekdays at 7:30 AM local/Central

ApolloSwingStudy1620
Weekdays at 4:20 PM local/Central
```

## Outputs

Latest pointer:

```text
Apollo/logs/market_study/latest_market_study.json
```

Per-run artifacts:

```text
Apollo/logs/market_study/YYYY-MM-DD/<run_id>/market_study.json
Apollo/logs/market_study/YYYY-MM-DD/<run_id>/trade_proposals.json
Apollo/logs/market_study/YYYY-MM-DD/<run_id>/market_study_report.md
```

Swing latest pointer:

```text
Apollo/logs/swing_study/latest_swing_study.json
```

Swing per-run artifacts:

```text
Apollo/logs/swing_study/YYYY-MM-DD/<run_id>/swing_study.json
Apollo/logs/swing_study/YYYY-MM-DD/<run_id>/swing_proposals.json
Apollo/logs/swing_study/YYYY-MM-DD/<run_id>/swing_study_report.md
```

Swing lifecycle state:

```text
Apollo/logs/swing_study/swing_state.json
```

Persistent source state:

```text
Apollo/logs/market_study/state.json
Apollo/logs/market_study/browser_status.json
```

Scheduler logs:

```text
Apollo/logs/market_study/scheduler/*.stdout.log
Apollo/logs/market_study/scheduler/*.stderr.log
Apollo/logs/swing_study/scheduler/*.stdout.log
Apollo/logs/swing_study/scheduler/*.stderr.log
```

## Source Policy

The source order is API/free structured sources first, browser-session status second. Browser fallback means saved-session continuity only. If a market source reports login, CAPTCHA, or bot-blocked state, Apollo records `manual refresh needed`, lowers source health, and continues with other lanes. It does not attempt CAPTCHA solving or bot-blocker bypass.

Argus/ntfy notifications are sparse: high-confidence proposal, watched setup confirmation, invalidation, blocked browser session, or run failure.

## Swing Research

The swing lane is long-biased and targets 2-20 trading day setups. It adds daily candles, 20/50/200 SMA, 8/21 EMA, RSI14, ATR14, 20-day range, 52-week range position, volume trend, relative strength against SPY/QQQ, market regime, entry/stop/target zones, risk notes, and lifecycle status.

The Apollo UI exposes this in the `Swing` tab. The tab can run a study, filter by lifecycle status, review proposal details, and mark local state as watching, rejected, or paper trade. These actions do not place broker orders.

## Swing V2

Swing V2 adds provider health, normalized catalysts, lightweight outcome tracking, chart payloads, and watchlist learning without changing the research-only boundary.

Additional artifacts:

```text
Apollo/logs/swing_study/swing_outcomes.json
Apollo/logs/swing_study/provider_health.json
Apollo/logs/swing_study/catalyst_records.json
Apollo/logs/swing_study/candle_cache.json
```

Per-run copies are also written beside `swing_study.json`. Provider health compares Yahoo daily candles with the optional Stooq adapter. Stooq historical data currently requires an API key/captcha flow, so Apollo only calls it when `APOLLO_STOOQ_APIKEY` is configured; otherwise it records `api_key_required` and continues with Yahoo plus last-good cache.

Additional admin endpoints:

```text
GET  /admin/swing/outcomes
GET  /admin/swing/provider_health
POST /admin/swing/recompute_outcomes
```

Additional read-only MCP tools:

```text
apollo.swing_outcomes
apollo.swing_provider_health
```

## News Event Impact Engine

Apollo includes a fast hybrid event-impact lane for swing research. It reads free-first event sources already used by Apollo, classifies whether a headline/filing is fresh, stale, positive, negative, or likely priced in, and adds a bounded `event_score_component` to swing proposals when explicitly enabled.

Primary interfaces:

```text
POST /admin/event_impact/run
GET  /admin/event_impact/latest
GET  /admin/event_impact/history
GET  /admin/event_impact/health

apollo.event_impact
apollo.event_swing_study
apollo.event_watchlist
apollo.event_impact_health
```

Swing integration:

```text
POST /admin/swing/run
payload: {"tickers": "NVDA,AMD", "include_event_impact": true}
```

When enabled, swing artifacts include:

```text
Apollo/logs/swing_study/YYYY-MM-DD/<run_id>/event_impact.json
Apollo/logs/swing_study/YYYY-MM-DD/<run_id>/event_analog_report.md
```

Standalone event artifacts:

```text
Apollo/logs/event_impact/latest_event_impact.json
Apollo/logs/event_impact/YYYY-MM-DD/<run_id>/event_impact.json
Apollo/logs/event_impact/YYYY-MM-DD/<run_id>/event_impact_report.md
Apollo/logs/event_impact/YYYY-MM-DD/<run_id>/event_analog_report.md
```

Storage policy:

```text
APOLLO_EVENT_CACHE_ROOT=D:/AI/apollo_market_event_engine
HF_HOME=D:/AI/hf_cache
TRANSFORMERS_CACHE=D:/AI/hf_cache/transformers
TORCH_HOME=D:/AI/torch_cache
APOLLO_EVENT_MODEL_ID=D:/AI/models/finbert-financial-sentiment
```

The model lane is D-drive safe. If the configured FinBERT/FinGPT-style model is missing, Apollo falls back to deterministic financial heuristics and reports the fallback in health; it should not download models to C drive.

Trading boundary:

```text
human_approval_required=true
live_trade_execution=false
broker_order_created=false
```

The event engine may emit a `paper_order_candidate` only as broker-prep evidence for a high-confidence event plus swing setup. It does not submit paper or live broker orders.

## Market Forecast Turn

Apollo also exposes a combined forecast turn that runs event impact, event-aware swing study, weekend/next-session normalization, and a simulated future status update.

```text
POST /admin/market_forecast_turn/run
GET  /admin/market_forecast_turn/latest
GET  /admin/market_forecast_turn/health

apollo.market_forecast_turn
apollo.market_forecast_health
```

Example:

```json
{
  "tickers": "SPY,QQQ,NVDA,AMD,AVGO,MSFT,AMZN",
  "as_of": "2026-05-08T22:00:00-05:00",
  "simulate_at": "2026-05-09T12:00:00-05:00",
  "write_artifacts": true
}
```

Outputs include:

```text
forecast.overall_bias
forecast.confidence
forecast.watch_candidates
simulation.next_trading_session
simulation.expected_apollo_turn
ocr97.mode
```

OCR97 is not run for normal news/RSS text. It is only used when `ocr_documents` are supplied and `APOLLO_MARKET_FORECAST_OCR97_ENABLED=1`; otherwise the response records `ocr97.mode=skipped_no_documents` or `disabled`.

## Homework Trade Pipeline

The one-shot April 28 homework pipeline waits for the OCR97 mixed-corpus benchmark to finish, then asks Apollo to build a research-only swing trade plan using the Swing V2 stack.

```text
Scheduled task: ApolloHomeworkTradeAfterOCRApr28
Upstream OCR task: OCR97MixedCorpusBenchmarkApr28_0100
Runner: Apollo/watchdog/run_apollo_homework_trade_after_ocr_apr28_scheduled.ps1
Latest artifact: Apollo/logs/homework_trade/latest_homework_trade.json
```

The homework plan keeps `human_approval_required=true`, `live_trade_execution=false`, and `broker_order_created=false`.

## Homework Phase 2 Strategy KB

The second April 28 homework phase runs after the same OCR task clears. It seeds Apollo's local swing-strategy knowledge base, refreshes the Apollo financial HippoRAG graph, then runs the homework trade plan with retrieved strategy rubric notes attached to the decision.

```text
Scheduled task: ApolloHomeworkPhase2AfterOCRApr28
Upstream OCR task: OCR97MixedCorpusBenchmarkApr28_0100
Runner: Apollo/watchdog/run_apollo_homework_phase2_after_ocr_apr28_scheduled.ps1
Strategy docs: Apollo/memory/kb/ingest/swing_strategy
Latest artifact: Apollo/logs/homework_trade_phase2/latest_homework_phase2.json
```

The phase is intentionally autonomous and degraded-mode tolerant: OCR nonzero results and HippoRAG graph refresh failures are recorded, but Apollo still produces a research-only homework plan from market data, catalysts, cached state, and the strategy KB when available.
