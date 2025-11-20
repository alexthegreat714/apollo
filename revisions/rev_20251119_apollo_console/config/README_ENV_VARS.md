# Apollo Environment Variables (Snapshot: 2025-11-19)

Core settings loaded by `config.py` and the console routes:

- `APOLLO_GENERATION_MODEL`: Default reply model (current default: `Fino1-8B.Q6_K`). This flows into the console and chat endpoints.
- `APOLLO_EMBEDDING_MODEL`: Embedding model for RAG + ingestion (must stay `nomic-embed-text` to match 768-dim collections).
- `OLLAMA_HOST` / `OLLAMA_URL`: Ollama base URL (defaults: `http://localhost:11434`); both are supported for backward compatibility.
- `RAG_DIR`: Persisted Chroma path (default `./chroma_db`).
- `RAG_COLLECTION`: Active collection name (default `apollo_financial`; stress-test collections are temporary).
- `RAG_TOP_K`: Default top-k for searches (default `5`).
- `PORT` / `HOST`: Flask server binding (defaults `5010` / `0.0.0.0`).
- `LOG_DIR`: Base log directory (default `./logs`); console logs conversations under `logs/conversations/`.
- `DEEP_MODE_ENABLED`, `DEEP_MODE_MAX_STEPS`, `DEEP_MODE_TIMEOUT`: Controls deep analysis features.
- `TOOLS_ENABLED`, `TAX_YEAR`, `DEFAULT_INFLATION_RATE`: Finance tool defaults consumed by skills.
- `CHUNK_SIZE`, `CHUNK_OVERLAP`, `MAX_FILE_SIZE_MB`: Ingestion chunking and size caps.

Legacy variables (still present in `.env`) routed to the new names:

- `MODEL_NAME` is treated as an alias of `APOLLO_GENERATION_MODEL`.
- `EMBEDDING_MODEL` is deprecated for retrieval/ingest; use `APOLLO_EMBEDDING_MODEL` instead.

Template file captured as `env_template_snapshot.txt` (copied from `.env.template`).
