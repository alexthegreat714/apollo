# Apollo State Snapshot – 2025-11-19 (Multi-Phase + Console)
Date: 2025-11-19

Apollo is now a local financial RAG + console system running on Ollama (Fino1-8B.Q6_K) with `nomic-embed-text` for embeddings. The experience includes a browser console at `http://localhost:5010/console` plus API endpoints for RAG, skills, monitoring, and governance.

## System Overview
- `app.py` (Flask) exposes RAG, skills, monitor, governance, and console endpoints; console blueprint lives in `apollo_console/routes.py`.
- `apollo_rag` handles collection management, embeddings/retrieval via ChromaDB, and reranking in `retriever.py`; `monitor.py` runs background health checks.
- `apollo_skills` provides portfolio, taxes, macro, risk, and related skills with routing in `apollo_skills/router.py`.
- `apollo_memory` (if enabled) provides short-term store/replay paths.
- Console/web layer serves `templates/console.html` and static assets, backed by `apollo_console/logging.py` for JSONL transcripts.
- Query flow:
  1. Console or API receives the user question.
  2. RAG retriever queries `apollo_financial` in Chroma using `nomic-embed-text` (dim 768).
  3. Top-k docs (metadata includes kind, source, priority, period ranges, etc.) are retrieved.
  4. Retrieved context + user query are sent to `Fino1-8B.Q6_K` via Ollama.
  5. Responses are logged to JSONL and can be summarized/scored by monitor/tests.

## Model Configuration (Post-Migration)
- `APOLLO_GENERATION_MODEL` (default `Fino1-8B.Q6_K`) is the reply model for chat/console.
- `APOLLO_EMBEDDING_MODEL` (default `nomic-embed-text`) is the sole embedding model; embedding dimension is fixed at **768** and collections must match.
- `MODEL_NAME` in `config.py` is an alias of the generation model for backward compatibility.
- Retrieval no longer relies on legacy `EMBEDDING_MODEL` or `OLLAMA_MODEL` for embeddings; `apollo_rag.retriever` calls the embedding model explicitly and uses Ollama at `OLLAMA_URL`/`OLLAMA_HOST`.

## RAG Collections
- Primary collection: `apollo_financial`.
- Approximate document count: ~299 docs (latest run).
- Document kinds: analysis, commentary, education, macro, micro, news, projection, regulation, report, statement.
- Common metadata: `kind`, `source` (stress test / production / future ynab), `priority`, `period_start`, `period_end`, plus tags/extra fields.
- `stress_test` collection exists for tests only.

## Endpoints & Console
- API base: `http://localhost:5010/`
- Key endpoints: `/rag/search`, `/rag/advanced_search`, `/rag/stats`, `/rag/monitor/start`, `/rag/monitor/stop`, `/rag/monitor/status`, `/rag/monitor/check`, `/skills/route`, `/skills/list`, `/api/chat` (console chat), `/api/conversations`, `/api/health`, `/health` (app).
- Console: `http://localhost:5010/console`
  - Left pane: chat with Apollo (RAG-augmented).
  - Right pane: retrieved context docs (kind, source, score).
  - Conversation logging to `logs/conversations/*.jsonl`.

## Test Suite Status (as of 2025-11-19)
- `python apollo_tests/rag_sanity_test.py` → PASSED (5/5 queries).
- `python apollo_tests/multi_insert_test.py` → PASSED (30 documents).
- `python apollo_tests/multi_query_test.py` → Precision ~59% with 16/20 high-precision queries.
- `python apollo_tests/metadata_filter_test.py` → All 6 filter tests PASSED.
- `python apollo_tests/context_quality_test.py` → PASSED; coherence ~0.97, relevance ~0.39.
- `python apollo_tests/background_monitor_test.py` → Config fixed; known “Explain diversification” micro-test failure is content-related (returns news instead of education).
- `python apollo_tests/console_api_test.py` → PASSED (chat endpoint, conversations endpoint, health endpoint, logging).

## How to Run
```bash
cd C:\\Users\\blyth\\Desktop\\Engineering\\Apollo
python app.py
```
Then open `http://localhost:5010/console` in a browser.

Optional: run the test suite
```bash
python apollo_tests\\rag_sanity_test.py
python apollo_tests\\multi_insert_test.py
python apollo_tests\\multi_query_test.py
python apollo_tests\\metadata_filter_test.py
python apollo_tests\\context_quality_test.py
python apollo_tests\\background_monitor_test.py
python apollo_tests\\console_api_test.py
```

## Troubleshooting & Gotchas
- **Embedding dimension mismatch**  
  Symptom: Chroma errors about wrong embedding dimensions or “must provide an embedding function”.  
  Fix: Set `APOLLO_EMBEDDING_MODEL=nomic-embed-text`, delete old collections made with the wrong dimensions, re-ingest (`python delete_apollo_collection.py` then `python apollo_ingest\\ingest_any.py apollo_test_docs\\stress --recursive --collection apollo_financial`).
- **Monitor using wrong retrieval path (FIXED)**  
  Symptom: Background monitor micro tests failing with embedding-function errors while normal RAG works.  
  Cause/Fix: `monitor.py` now calls `apollo_rag.retriever.get_retriever()` and `retriever.search()`; if the error resurfaces, ensure monitor code does not create its own Chroma client.
- **Environment variables not set**  
  Symptom: Using default `EMBEDDING_MODEL` or `OLLAMA_MODEL` accidentally points embeddings to the generation model.  
  Fix: Always set `APOLLO_GENERATION_MODEL=Fino1-8B_Q6_K` and `APOLLO_EMBEDDING_MODEL=nomic-embed-text`; `run_apollo.bat` should export these before starting `app.py`.
- **Background monitor test failures**  
  If `background_monitor_test.py` only fails on “Explain diversification”, treat it as corpus coverage (education docs) rather than an embedding/config bug; improve content or adjust expectations.

## Lessons Learned
- Separate generation and embedding models; never overload a single `MODEL_NAME` for both.
- Lock embedding dimension and collection schema early (768 for `nomic-embed-text`); retrofits are costly.
- A dedicated retriever abstraction (`RAGRetriever` + `get_retriever()`) prevents duplicate Chroma logic and fixes monitor drift.
- Background monitors must share the same retrieval/model path as the main app to avoid config skew.
- A lightweight local console accelerates iteration compared to raw JSON endpoints.
- Keep environment defaults explicit (`APOLLO_*` vars) and provide template snapshots alongside revision archives.
