# Apollo — Agent Reference

This file is read by agents (Claude Code, Sky, etc.) at the start of every session.
Its purpose is to prevent capability under-estimation from a repo surface scan.

Background pipeline reference:
- `Apollo/README_BACKGROUND_PIPELINE.md`

Read that file before changing `Apollo/nightly_pipeline.py` or the GB10 background worker.

---

## What Apollo Is

Apollo is a **local financial AI assistant** running Flask on env-driven host/port.
Port resolution is `AGENT_PORT` -> `APOLLO_PORT` -> `PORT` -> `APP_CONFIG.PORT`.
Host is resolved from `HOST` -> `APP_CONFIG.HOST`.
Defaults resolve to `127.0.0.1:5010` via `config.py` when no env vars are set.
In this repo snapshot, `.env` sets `PORT=5218` and `HOST=0.0.0.0`, which takes precedence.
Model resolution is env-first in `app.py`:
`OLLAMA_MODEL_CHAT` -> `OLLAMA_MODEL` (default `gemma3:12b`) and
`OLLAMA_MODEL_DEEP` -> `OLLAMA_MODEL_REASON` (default `gemma4:31b`).

The `.env` file is authoritative at runtime; `config.py` provides safe fallbacks.
Do not infer capability from the module list alone — many modules are stubs or
growth areas; the live chat pipeline is fully functional.

---

## Dependency: `common/`

`common/` lives at `c:\Users\blyth\Desktop\Engineering\common\`, **one level above** the
Apollo directory. It is a shared library used by Sky, Aegis, Veritas, and Apollo.

`app.py` adds `ENGINEERING_ROOT` (defaults to `Apollo/../`) to `sys.path` at startup
before any `common.*` imports. This is intentional monorepo design, not an accident.

Key shared modules Apollo uses:
- `common.rag_store` — ChromaDB-backed RAG with short-term/long-term split, sim+priority+age scoring
- `common.query_client` — Ollama/OWUI model call abstraction
- `common.mcp` — MCP tool registry (`register`, `run`, `ensure_loaded`)
- `common.dialogue_broker`, `common.dialog_router` — conversation classification
- `common.deepcoder` — financial tool dispatch for deep mode
- `common.truth_guard`, `common.response_constraints` — output quality post-processing
- `common.session_facts` — in-session memory (recall, note-taking)
- `common.governance.*` — Congress ledger, cycle status, governance routes

---

## Model & Hardware

| Setting | Value |
|---|---|
| Primary model | `OLLAMA_MODEL_CHAT` (fallback: `OLLAMA_MODEL`) |
| Primary model default | `gemma3:12b` |
| Deep mode model | `OLLAMA_MODEL_DEEP` (fallback: `OLLAMA_MODEL_REASON`) |
| Deep model default | `gemma4:31b` |
| Ollama host | `OLLAMA_URL_CHAT` / `OLLAMA_URL` defaulting to `127.0.0.1:11434` |
| Deep Ollama host | `OLLAMA_URL_DEEP` / `OLLAMA_URL` defaulting to `127.0.0.1:11435` |
| Context window | 8192 tokens (`OLLAMA_NUM_CTX`) |
| Base host | `HOST` then `APP_CONFIG.HOST` (default `127.0.0.1`) |
| Base port | `AGENT_PORT` -> `APOLLO_PORT` -> `PORT` -> `APP_CONFIG.PORT` (default `5010`) |
| Simulation execution mode | `APOLLO_SIM_EXECUTION_MODE` (`paper` / `live`, default `paper`) |
| Simulation live gate | `APOLLO_SIM_LIVE_ENABLED` (`0` / `1`, default `0`) |
| Simulation retry policy | `APOLLO_SIM_BROKER_MAX_RETRIES` (default `3`), `APOLLO_SIM_BROKER_RETRY_WINDOW_SEC` (default `600`), `APOLLO_SIM_STALE_ORDER_SECONDS` (default `1200`) |
| Simulation reconcile policy | `APOLLO_SIM_RECONCILE_LOCK_STALE_SECONDS` (default `300`), `APOLLO_SIM_RECONCILE_MAX_PLANS` (default `0`) |
| Simulation forced submit bypass | `APOLLO_SIM_ALLOW_FORCE_WITHOUT_APPROVAL` (default `0`) |
| Simulation submit rate limit | `APOLLO_SIM_MIN_SECONDS_BETWEEN_SUBMISSIONS` (default `0`) |
| Simulation broker | `APOLLO_SIM_BROKER_PROVIDER` (default `paper_sim`) |
| Max open positions (sim) | `APOLLO_SIM_MAX_OPEN_POSITIONS` (default `5`) |
| Sim buying power | `APOLLO_SIM_SIMULATED_BUYING_POWER` (default `50000`, fallback `APOLLO_SIM_BUYING_POWER`) |
| Admin anon default | `SKY_ALLOW_ANON=0` |
| Hardware | Local Windows 11, GPU (runs 19 GB quant cleanly) |
| Watchlist | `APOLLO_WATCHLIST` (or `_DEFAULT_WATCHLIST` in `config.py`) |

The `.env` file is authoritative. `config.py` defaults are fallbacks only.

Watchlist behavior is also env-driven: `APOLLO_WATCHLIST` flows through `config.get_watchlist()` and is shared across core trade modules (`trade_cycle.py`, `swing_study.py`, `homework_trade_pipeline.py`, `event_impact.py`). Defaults are deduplicated and normalized to upper-case.

---

## RAG — Three-Layer Knowledge Architecture

### Layer 1 — Financial Corpus (`apollo_financial`) via HippoRAG 2
- Path: `./chroma_db/apollo_financial`
- Documents: **620 chunks** of synthetic financial education docs
- Retrieval: **HippoRAG 2** — dense ChromaDB → PPR graph expansion → merged re-rank
- Graph: `./chroma_db/financial_kg.json` (nodes=entities, edges=relations, built by `build_graph.py`)
- Falls back to flat ChromaDB similarity if HippoRAG is unavailable
- Build/update the graph: `python -m apollo_hipporag.build_graph --rag-dir ./chroma_db`

### Layer 2 — Operational Memory (`AgentRAG("Apollo")`)
- Path: `C:\Users\blyth\Desktop\Engineering\rag_data\Apollo\Apollo_rag`
- ~34 docs of session notes, budget guidance, user preferences
- Embedding: `common.rag_store` sentence-transformer (warmed async at startup)
- Scored by: cosine similarity + priority + recency (weighted composite)
- Short-term vs long-term split: notes below priority 0.8 go to short-term JSONL
- Promotion endpoint: `POST /rag/appendix` summarises and promotes short-term to long-term

### Layer 3 — Session Memory (`apollo_memory`)
- Persistent user profile (income, age, risk tolerance, debts, goals, etc.)
- Auto-extracted from conversation via regex patterns in `apollo_memory/extractor.py`
- Stored at priority 0.85–0.95 so profile hits float above corpus hits
- Injected as `[User Profile]` block in every prompt
- `apollo_memory/store.py` — CRUD wrapper around AgentRAG for profile fields
- `apollo_memory/context.py` — formats profile dict into compact prompt block

Both Layer 1 and Layer 2 are merged in `gather_hits()` and deduplicated by 120-char text prefix.

**How to talk to Apollo about its RAG:**
- Apollo automatically uses all three layers on every non-trivial message
- For explicit doc search with visible results, say any of:
  - `!rag <query>` — searches corpus + operational memory, returns raw hits
  - `rag search: <query>` — same
  - `search your docs for <topic>` — natural language trigger
  - `look in your docs: <topic>` — natural language trigger

**Current corpus content** (all synthetic test documents):

| Kind | Chunks |
|---|---|
| projection | 259 |
| regulation | 118 |
| news | 100 |
| report | 90 |
| education | 53 |

Real financial data has not been ingested yet. The pipeline is ready — drop files and call
`POST /rag/write` or run `apollo_ingest/ingest_any.py`. Re-run `build_graph.py` after ingestion
to update the HippoRAG knowledge graph (it skips already-indexed chunks).

RAG endpoints: `/rag/search`, `/rag/write`, `/rag/list`, `/rag/get`, `/rag/delete`,
`/rag/update_meta`, `/rag/export`, `/rag/import`, `/rag/count`, `/rag/tags`,
`/rag/shortterm/list`, `/rag/shortterm/export`, `/rag/appendix`, `/rag/review`.

---

## Web Search — Enabled, Fires Automatically

Tool: `Sky.tools.web_mcp_tool` (loaded via MCP at startup).
Providers: DuckDuckGo, DuckDuckGo HTML/Lite, Google News RSS, Bing RSS.
Rate limiting and 15-minute cache built in. **`SKY_WEB_ENABLED=1` is set in `.env`.**

**Auto-fires when** the query contains: `today`, `current`, `latest`, `2026`, `this year`,
`stock price`, `CPI`, `fed rate`, `earnings`, `bitcoin`, `jobs report`, and similar live-data keywords,
or when intent is `markets`/`macro`, or when `depth=deep`.

**Explicit triggers** (no auto-detection needed):
- `!web <query>` — direct web search, returns formatted results
- `web search: <query>` — same
- `search the web for <topic>` — same

When web results are injected, the model is told to cite sources. The post-processing disclaimer
("I don't have live data") is suppressed when web search actually ran.

---

## MCP — Framework Present, Apollo Tools Loaded at Startup

`common/mcp.py` provides a tool registry. Apollo loads these at startup:

```
Sky.tools.web_mcp_tool   → web.search
Sky.tools.rag_mcp_tool   → rag.*
common.vision_tool       → vision.*
Apollo.apollo_tools.mcp_ebay_listing -> apollo.ebay_listing
```

Each is attempted via `importlib.import_module`; unavailable tools are skipped with a
warning, never a crash. The MCP `/mcp` URL prefix is in the allowed route list.

Apollo has an `/mcp` blueprint in `mcp_routes.py`. `apollo.ebay_listing` is exposed there as a draft-first commerce tool with separate gates for eBay mutation and live publish.

For eBay listing work:
- Use `action=oauth_status` first when API credentials are involved. It only reports presence/absence of client credentials, RuName, access token, refresh token, and scopes.
- Use `action=oauth_authorize_url` to generate the eBay seller-consent URL. Open it in the Aegis VM eBay profile, not in an unrelated browser profile.
- Use `action=oauth_exchange_code` with the returned `code` parameter only. The tool stores `APOLLO_EBAY_ACCESS_TOKEN` and `APOLLO_EBAY_REFRESH_TOKEN` in Apollo's env file and returns only metadata.
- Use `action=oauth_refresh_access_token` when the short-lived user access token expires.
- Use `action=draft` first. It is local-only and returns Inventory API payloads plus missing publish fields.
- Use `action=prepared_review_packet` when the operator wants the non-API/ForgeClaw workaround: Apollo writes a local review packet with title, description, pricing review, image manifest, field fill order, and explicit instruction that ForgeClaw must stop before final publish/list/submit.
- Use `action=vm_status` or `action=vm_probe` to verify Apollo can reach the Aegis VM bridge.
- Use `action=seller_prerequisites` to read business policy IDs and inventory locations, and `action=category_suggestions` to find a category ID.
- Use `action=sandbox_pilot` for the dry-run setup plan. It performs no writes.
- Never print OAuth tokens, refresh tokens, client secrets, cookies, payment settings, payout settings, or account-security details. Presence/absence and env key names are enough.
- Do not publish through API or UI automation unless the human operator has reviewed every listing. API publishing still requires `APOLLO_EBAY_ENABLE_MUTATIONS=1`, `APOLLO_EBAY_ENABLE_PUBLISH=1`, `operator_confirmed=true`, and the exact publish confirmation phrase. ForgeClaw UI automation may fill drafts and upload/attach prepared images, but must not click final publish/list/submit.
- The supervised UI fallback uses the Aegis VM Chrome profile `profile_slug=ebay`, `cdp_port=9234`, and profile dir `/root/.config/apollo-ebay-login`; see `config/ebay_vm_page_map.json`. Keep this VM-only unless the operator explicitly asks for native desktop execution.

---

## Chat Pipeline (live, what actually runs on `POST /chat`)

```
POST /chat
  │
  ├─ !web / !rag commands and natural language equivalents (early return with raw results)
  │     !web <q> | "web search: <q>" | "search the web for <q>"
  │     !rag <q> | "rag search: <q>" | "search your docs for <q>" | "look in your docs: <q>"
  │
  ├─ Auto web decision (APOLLO_ALLOW_CHAT_TOOLS=1, APOLLO_AUTO_WEB_MODE=auto)
  │   → fires silently on live-data queries before dialog routing
  │
  ├─ Dialog router → "dialog" bucket: fast LLM call, return early
  │
  ├─ Congress context injection (bill/cycle/debate keyword detection)
  │
  ├─ Session facts (recall questions, note-taking)
  │
  ├─ Session memory extraction — auto-pulls financial facts from user message into profile
  │
  ├─ Intent classifier (Fino → markets/investing/risk/tax/macro/personal_finance)
  │
  ├─ RAG gather_hits() — three-layer merge:
  │     • Session memory profile hits (priority 0.85–0.95, float to top)
  │     • AgentRAG operational memory (34 docs)
  │     • Financial corpus via HippoRAG 2 (620 docs, dense→PPR→merge)
  │   All deduplicated by 120-char text prefix, sorted by score
  │
  ├─ [User Profile] block — injected if session memory has profile data
  │
  ├─ deep model pre-pass (depth=deep or risk/investing/macro intent)
  │
  ├─ Web search (auto on live-data queries, SKY_WEB_ENABLED=1) — injects [Web] block
  │
  ├─ Budget context (personal_finance intent or budget_focus flag)
  │
  ├─ Prompt assembly: personality + session facts + [User Profile] + finance quality policy +
  │   topic state + reference block + budget ctx + history + RAG ctx +
  │   deep analysis block + web block + instruction
  │
  ├─ Saturation guard — checks /api/ps; returns model_busy if queue > APOLLO_OLLAMA_MAX_QUEUE
  │
  ├─ query_model() -> Ollama chat model (OLLAMA_MODEL_CHAT / OLLAMA_MODEL)
  │
  └─ Post-processing: enforce_short_reply, dedupe_repeated_facts,
      apply_truth_guard, finance quality postprocess (web_used flag suppresses live-data disclaimer)
```

Streaming endpoint: `POST /chat/stream` (SSE, `text/event-stream`).

---

## Financial Tools (`apollo_tools/`)

Pure-Python calculators, no external API calls:

| Module | Capabilities |
|---|---|
| `amort.py` | Amortization schedules, loan payoff |
| `invest.py` | Compound growth, DCA projections |
| `inflation.py` | Real vs nominal, purchasing power |
| `risk.py` | Sharpe, VaR, drawdown, beta |
| `tax.py` | Capital gains, brackets, withholding |
| `rebalance.py` | Portfolio drift, rebalancing trades |
| `retirement.py` | 401k/IRA projections, withdrawal rates |
| `budget.py` | Budget analysis helpers |

These are invoked by the DeepCoder when intent matches (risk, investing, macro).

---

## Budget Module (`apollo_budget/`)

- Full CRUD for budget entries (CSV / XLSX import supported)
- Context injection: when `budget_focus=true` or `ui_tab=budget`, budget data is
  pulled from the store and prepended to the prompt
- Validation record: 81.67/100 on `tests/e2e_budget_eval.py` (2026-02-28)
- Known gap: `context_retention` scored 33% (pronoun reference, topic switching)

---

## Deep Mode

Deep analysis runs a two-model pipeline:

1. **Deep pre-pass** (OLLAMA_MODEL_DEEP, fallback OLLAMA_MODEL_REASON) builds a 4–6 bullet analytical block from the user message + top RAG hits via a deep-analysis helper (`_run_gx10_deep_analysis()` in `app.py`) and injects it as [Deep Analysis].
2. **Main generation** (OLLAMA_MODEL_CHAT, fallback OLLAMA_MODEL) receives the full prompt including the deep analysis block and produces the final reply.

Triggered when:
- Intent is `risk`, `investing`, or `macro` (at any depth)
- `depth=deep` in the request body
- Dialog router escalates to `deep`
- Fino classifier returns `needs_deep_analysis: true` or `needs_long_reflection: true`
- User sends `!think` prefix

`apollo_deepmode/` (the old controller module) is superseded by this approach and is no longer called in the chat pipeline.

---

## Other Capabilities

- **Autorun supervisor** (`autorun_supervisor.py`) — scheduled background tasks
- **Reflection** (`reflection.py`) — self-review logging
- **Runtime metrics** (`runtime_metrics.py`) — per-route latency/call tracking
- **OCR routes** (`common.ocr_routes`) — PDF/image ingestion via Tesseract
- **Vision routes** (`common.vision_routes`) — image analysis via model
- **Congress governance** — bill tracking, cycle status, voting context injection
- **Streaming** — `POST /chat/stream` emits SSE deltas + final JSON payload
- **HippoRAG 2** (`apollo_hipporag/`) — graph-augmented retrieval; build graph with `build_graph.py`
- **Session memory** (`apollo_memory/`) — persistent user profile auto-extracted from conversation

---

## Key Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | Primary chat |
| POST | `/chat/stream` | Streaming chat (SSE) |
| GET | `/health` | Health check |
| GET | `/meta` | Agent metadata + Ollama status |
| POST | `/rag/search` | RAG query |
| POST | `/rag/write` | Add to RAG |
| GET | `/admin/self_check` | Health + asset checks |
| POST | `/admin/trading_homework/run` | Weekly trading analysis |
| GET | `/congress/status` | Current cycle status |
| POST | `/budget/*` | Budget CRUD |

---

## Starting Apollo

```bash
cd c:/Users/blyth/Desktop/Engineering/Apollo
python app.py
```

Base port defaults to 5010 via `APP_CONFIG.PORT`, but `PORT` in `Apollo/.env` controls the effective runtime host/port (`HOST` + `PORT`). Logs: `./logs/`. ChromaDB: `./chroma_db/`.

Web search and chat commands are **enabled by default** (`SKY_WEB_ENABLED=1`,
`APOLLO_ALLOW_CHAT_TOOLS=1`, `APOLLO_AUTO_WEB_MODE=auto` in `.env`).

## Apollo OCR pytest guardrails

Calendar/agent-launched Apollo OCR tests must use the guarded launcher:

```bash
python tools/run_ocr_pytest_guarded.py -- tests/test_ocr_dual_tool.py tests/test_ocr_phase2_services.py -q
```

Do not run long OCR pytest commands through shell pipelines such as
`pytest ... | tail`. The launcher writes full output to `logs/pytest/`, prints a
short tail after completion, refuses duplicate OCR pytest runs, cleans stale OCR
pytest trees after `APOLLO_OCR_PYTEST_STALE_MINUTES` (default 120), and enforces
`APOLLO_OCR_PYTEST_TIMEOUT_SECONDS` (default 1800). Pytest also has a repo-level
timeout in `pytest.ini` so a stuck model or health probe cannot hang forever.

**Build the HippoRAG knowledge graph** (run once after ingestion, safe to re-run):
```bash
python -m apollo_hipporag.build_graph --rag-dir ./chroma_db
# With LLM-enriched triples (slower):
python -m apollo_hipporag.build_graph --rag-dir ./chroma_db --use-llm
```

---

## Saturation Guard (added 2026-04-13)

When Ollama has more than `APOLLO_OLLAMA_MAX_QUEUE` (default 4) active model slots,
the chat endpoint returns a `model_busy` JSON immediately instead of hanging until the
client times out. The guard calls `/api/ps` with a 2-second probe timeout.

Env vars:
- `APOLLO_OLLAMA_MAX_QUEUE` — max concurrent Ollama slots before busy reply (default: 4)
- `APOLLO_OLLAMA_PROBE_TIMEOUT_SECS` — probe timeout in seconds (default: 6)

---

## What Looks Incomplete But Isn't

| Apparent gap | Reality |
|---|---|
| `apollo/mcp/` is empty | MCP tools loaded from `common/` and `Sky/tools/` at startup |
| `common/` not in this repo | Lives at `../common/`, added to sys.path at boot |
| Only synthetic RAG data | Pipeline ready; real data ingestion is a day-one task |
| No `apollo_rag/retriever.py` etc | Those are stubs; live retrieval is in `common/rag_store.py` |
| `config.py` defaults look wrong | `.env` wins; config.py defaults are never the active config |
| `apollo_financial` not in chat | **Fixed 2026-04-13** — now merged in `gather_hits()` via HippoRAG 2 |
| Chat hangs when Ollama is busy | **Fixed 2026-04-13** — saturation guard returns busy reply |
| `!web` / `!rag` commands disabled | **Fixed 2026-04-14** — `APOLLO_ALLOW_CHAT_TOOLS=1`, `AUTO_WEB_MODE=auto` |
| Natural language doc search missing | **Fixed 2026-04-14** — "search your docs for X", "look in your docs: X" routes to `!rag` flow |
| Apollo says "no live data" with web enabled | **Fixed 2026-04-14** — disclaimer suppressed when web search ran |
| Session memory not persisted | **Fixed 2026-04-14** — `apollo_memory/` auto-extracts profile from chat |
| RAG retrieval flat similarity only | **Fixed 2026-04-14** — HippoRAG 2 (dense→PPR→merge) wired into corpus query |

---

*Last updated: 2026-04-14*
