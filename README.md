# Apollo Financial Assistant

A comprehensive financial AI assistant with advanced RAG retrieval, skills-based analysis, and autonomous governance capabilities.

## Architecture

```
User Query
    │
    ▼
┌─────────────────┐
│ Intent Classifier│
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌──────┐  ┌──────────┐
│ RAG  │  │ Skills   │
│Retriever│  │ Router │
└────┬─┘  └────┬─────┘
     │         │
     ▼         ▼
┌─────────────────┐
│ Memory Replay   │
│ (session context)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ DeepMode        │
│ Controller      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Response +      │
│ RAG Proposals   │
└─────────────────┘

Background:
┌─────────────────┐
│ RAG Monitor     │─────▶ Auto-proposals
└─────────────────┘
```

## Project Structure

```
Apollo/
├── app.py                          # Main Flask application
├── rag_routes.py                   # RAG API endpoints
├── config.py                       # Configuration settings
├── reflection.py                   # Self-reflection capabilities
├── runtime_metrics.py              # Performance metrics
├── autorun_supervisor.py           # Automated task supervision
├── autorun_routes.py               # Automation endpoints
│
├── apollo_rag/                     # Advanced RAG System
│   ├── __init__.py
│   ├── retriever.py                # Domain-weighted retrieval with reranking
│   ├── corpus_expander.py          # Web scraping & OCR expansion
│   └── monitor.py                  # Background health monitoring
│
├── apollo_rag_governance/          # RAG Governance & Approval
│   ├── init.py
│   ├── actions.py                  # Action schemas
│   ├── action_executor.py          # Executes approved actions
│   └── proposal_engine.py          # Generates improvement proposals
│
├── apollo_skills/                  # Financial Skills Layer
│   ├── __init__.py
│   ├── portfolio.py                # Portfolio optimization
│   ├── taxes.py                    # Tax analysis
│   ├── macro.py                    # Macroeconomic modeling
│   ├── risk.py                     # Risk metrics
│   └── router.py                   # Skill routing
│
├── apollo_memory/                  # Session Memory
│   ├── __init__.py
│   ├── short_term_store.py         # Session storage
│   └── replay_retriever.py         # Context replay
│
├── apollo_ingest/                  # Document Ingestion
│   ├── __init__.py
│   ├── embedder.py                 # Embedding generation
│   ├── rag_writer.py               # ChromaDB writer
│   ├── pdf_ingest.py               # PDF processing
│   ├── csv_ingest.py               # CSV processing
│   ├── xlsx_ingest.py              # Excel processing
│   ├── text_ingest.py              # Text processing
│   ├── chunker.py                  # Document chunking
│   ├── loader.py                   # File loading
│   └── ingest_any.py               # Universal ingestion
│
├── apollo_tools/                   # Financial Calculators
│   ├── __init__.py
│   ├── util.py                     # Utilities
│   ├── amort.py                    # Amortization
│   ├── invest.py                   # Investment
│   ├── inflation.py                # Inflation
│   ├── risk.py                     # Risk calculations
│   ├── tax.py                      # Tax calculations
│   ├── rebalance.py                # Portfolio rebalancing
│   └── retirement.py               # Retirement planning
│
├── apollo_deepmode/                # Deep Analysis Modes
│   ├── __init__.py
│   ├── controller.py               # Mode controller
│   ├── risk_mode.py                # Risk analysis mode
│   ├── investing_mode.py           # Investing mode
│   └── macro_mode.py               # Macro analysis mode
│
├── apollo_classifier/              # Intent Classification
│   ├── __init__.py
│   └── classifier.py               # Query classifier
│
├── apollo_schemas/                 # Data Schemas
│   ├── __init__.py
│   └── validators.py               # Schema validation
│
├── apollo_tests/                   # Test Suite
│   ├── multi_insert_test.py        # Bulk insertion test
│   ├── multi_query_test.py         # Query evaluation
│   ├── metadata_filter_test.py     # Metadata filtering
│   ├── context_quality_test.py     # Context quality
│   └── background_monitor_test.py  # Monitor tests
│
└── revisions/                      # Revision History
    └── rev_apollo_expansion/
        └── CHANGELOG.md            # Detailed change log
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
- Action pipeline: pending → approved → applied
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
- 2025-11-19 – rev_20251119_apollo_console – Multi-phase RAG upgrade + console, embedding migration to nomic-embed-text, background monitor fixes, local web console at http://localhost:5010/console. See `revisions/rev_20251119_apollo_console/APOLLO_STATE_2025-11-19.md`.
- 2025-11-18 – rev_apollo_expansion – See `revisions/rev_apollo_expansion/CHANGELOG.md` for detailed changes.

---

Generated: 2025-11-18
