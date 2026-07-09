import json
import logging
import os
import shutil
import sys
import time
import zipfile
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import re
import unicodedata
from difflib import SequenceMatcher
from threading import Lock, Thread
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

try:
    from .config import Config as APP_CONFIG
except ImportError:
    from config import Config as APP_CONFIG
from flask import Flask, jsonify, render_template, request, send_file, Response, stream_with_context, url_for, redirect
from flask_cors import CORS

for _telemetry_key, _telemetry_value in (
    ("CHROMA_ANONYMIZED_TELEMETRY", "FALSE"),
    ("ANONYMIZED_TELEMETRY", "FALSE"),
    ("CHROMA_TELEMETRY_IMPL", "none"),
    ("POSTHOG_DISABLED", "1"),
):
    os.environ.setdefault(_telemetry_key, _telemetry_value)
for _telemetry_logger_name in ("chromadb.telemetry", "chromadb.telemetry.product.posthog", "posthog"):
    _telemetry_logger = logging.getLogger(_telemetry_logger_name)
    _telemetry_logger.setLevel(logging.CRITICAL)
    _telemetry_logger.propagate = False

# Path bootstrap — must precede all common.* imports.
# Engineering/common/ is one level up, shared with Sky, Aegis, Veritas.
# ENGINEERING_ROOT env var overrides the default (mirrors Sky startup convention).
_APOLLO_ROOT = Path(__file__).resolve().parent
_ENGINEERING_ROOT = Path(os.getenv("ENGINEERING_ROOT", str(_APOLLO_ROOT.parent)))
for _p in (str(_ENGINEERING_ROOT), str(_APOLLO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.response_constraints import (
    enforce_short_reply,
    is_short_reply_request,
    single_word_request,
    dedupe_repeated_facts,
)
from common.agent_context_loader import ensure_agent_context
from common.agent_manifest_schema import load_agent_manifest
from common.context_awareness import update_topic_state, should_reference_first
from common.truth_guard import apply_truth_guard
from common.dialogue_broker import DialogueBroker
from common.dialogue_orchestrator import run_dialogue_test
from common.health_schema import build_health_payload
from common.launch_contract import bootstrap_agent_runtime
from common.apollo_runtime_status import (
    detect_apollo_background_command,
    extract_apollo_background_runtime,
    format_apollo_background_runtime,
    is_apollo_background_status_question,
    load_apollo_background_runtime,
)
from common.query_client import query_model
from common.rag_store import AgentRAG
from common.session_facts import (
    apply_session_note,
    is_recall_question,
    is_session_note_request,
    recall_reply,
    session_facts_block,
    is_two_actions_request,
    two_actions_reply,
)

from common.governance.cycle_status import (
    is_congress_status_question,
    fetch_cycle_status,
    format_cycle_status_reply,
)

from Apollo import rag_routes as apollo_rag_routes
from Apollo import nightly_pipeline as apollo_nightly_pipeline
from Apollo import focus_trading as apollo_focus_trading
from Apollo import trading_homework as apollo_trading_homework
from Apollo import homework_trade_pipeline as apollo_homework_trade_pipeline
from Apollo import market_data as apollo_market_data
from Apollo import market_study as apollo_market_study
from Apollo import event_impact as apollo_event_impact
from Apollo import market_forecast_turn as apollo_market_forecast_turn
from Apollo import corpus_bootstrap as apollo_corpus_bootstrap
from Apollo import mcp_routes as apollo_mcp_routes
from Apollo import swing_study as apollo_swing_study
from Apollo import simulation_execution as apollo_simulation_execution
from Apollo import pretrade_gate as apollo_pretrade_gate
from Apollo import daily_report as apollo_daily_report
from apollo_budget.routes import register_budget_routes
from apollo_budget.store import get_store
from apollo_budget.context import build_budget_context
from Apollo.autorun_supervisor import AutoRunSupervisor
from Apollo.reflection import log_reflection, summarize_reflections
from Apollo.runtime_metrics import (
    record_autorun_call,
    record_chat,
    record_reflection_call,
    snapshot as metrics_snapshot,
)
from Apollo.apollo_tools import student_loan_review as apollo_student_loan_review
from common.rag_ingest_policy_context import (
    is_rag_improvement_setup_prompt,
    rag_improvement_setup_reply,
    rag_ingestion_policy_reply_from_state,
    update_code_phrase_state,
)

_CHICAGO_TZ = ZoneInfo("America/Chicago")


def _format_draft_created_at(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local_dt = parsed.astimezone(_CHICAGO_TZ)
    return f"{local_dt.strftime('%Y-%m-%d')} | {local_dt.strftime('%H:%M:%S')} CST"


def _looks_like_congress_message(message: str) -> bool:
    if not message:
        return False
    low = message.lower()
    triggers = (
        "congress",
        "bill",
        "debate",
        "vote",
        "cycle",
        "senate",
        "legislation",
        "enact",
        "ruling",
        "veto",
    )
    return any(t in low for t in triggers)


def _extract_cycle_int(message: str) -> int | None:
    if not message:
        return None
    match = re.search(r"\bcycle\s*(\d+)\b", message, flags=re.I)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    return None


def _extract_bill_titles(message: str) -> list[str]:
    if not message:
        return []
    titles: list[str] = []
    # quoted titles
    for pat in (r"\"([^\"]{4,120})\"", r"“([^”]{4,120})”"):
        for match in re.findall(pat, message):
            title = match.strip()
            if title and title not in titles:
                titles.append(title)
    return titles


def _build_congress_context(message: str, max_bills: int = 4) -> str:
    if not message:
        return ""
    if not _looks_like_congress_message(message):
        return ""
    cycle_int = _extract_cycle_int(message)
    titles = _extract_bill_titles(message)
    bills = ledger.list_bills(limit=300, include_archived=True)
    selected = []
    if cycle_int is not None:
        selected = [b for b in bills if int(b.get("cycle_int") or 0) == cycle_int]
    elif titles:
        lower_titles = [t.lower() for t in titles]
        for bill in bills:
            title = (bill.get("title") or "").lower()
            if any(t in title for t in lower_titles):
                selected.append(bill)
    else:
        # If only "bill/debate/vote" mention, provide latest active bills to avoid "no data" responses
        selected = [b for b in bills if (b.get("status") or "").lower() != "archived"][:max_bills]

    if not selected:
        return ""

    lines = []
    header = "Congress ledger context (local):"
    if cycle_int is not None:
        header = f"Congress ledger context (cycle {cycle_int}):"
    lines.append(header)
    for bill in selected[:max_bills]:
        bill_id = bill.get("bill_id")
        detail = ledger.get_bill(bill_id) or bill
        title = detail.get("title") or "Untitled"
        status = detail.get("status") or "unknown"
        summary = (detail.get("summary") or detail.get("text") or "").strip()
        if summary:
            summary = re.sub(r"\s+", " ", summary)[:240]
        sponsors = detail.get("sponsors") or []
        if not sponsors:
            try:
                sponsors = json.loads(detail.get("sponsors_json") or "[]")
            except Exception:
                sponsors = []
        sponsors_text = ", ".join(sponsors) if sponsors else "n/a"
        lines.append(f"- {title} ({bill_id}) status={status} sponsors={sponsors_text}")
        if summary:
            lines.append(f"  summary: {summary}")
    return "\n".join(lines).strip()


def _load_env_file() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    force_override_keys = {
        "OLLAMA_URL_CHAT",
        "OLLAMA_MODEL_CHAT",
        "OLLAMA_KEEP_ALIVE_CHAT",
        "OLLAMA_URL_DEEP",
        "OLLAMA_MODEL_DEEP",
        "OLLAMA_MODEL_REASON",
        "APOLLO_ALLOW_CHAT_TOOLS",
        "APOLLO_AUTO_WEB_MODE",
        "APOLLO_AUTO_WEB_ASK_COOLDOWN_TURNS",
    }
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        clean_value = value.strip()
        if clean_key in force_override_keys or clean_key.startswith("APOLLO_"):
            os.environ[clean_key] = clean_value
        else:
            os.environ.setdefault(clean_key, clean_value)


_load_env_file()

app = Flask(
    __name__,
    static_url_path="/static",
    static_folder=str(_APOLLO_ROOT / "static"),
    template_folder=str(_APOLLO_ROOT / "templates"),
)

CORS(app)
logging.basicConfig(level=logging.INFO)
broker = DialogueBroker()

AGENT_NAME = "Apollo"
os.environ["AGENT_NAME"] = AGENT_NAME
os.environ["SKY_AGENT_NAME"] = AGENT_NAME
app.config["AGENT_NAME"] = AGENT_NAME
AGENT_RUNTIME = bootstrap_agent_runtime(AGENT_NAME)
AGENT_MANIFEST = load_agent_manifest(AGENT_NAME)
try:
    AGENT_CONTEXT = ensure_agent_context(_ENGINEERING_ROOT, AGENT_NAME, log=False)
except Exception:
    AGENT_CONTEXT = {}
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")
os.environ.setdefault("OLLAMA_URL_CHAT", os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
os.environ.setdefault("OLLAMA_MODEL_CHAT", "gemma3:12b")
os.environ.setdefault("OLLAMA_KEEP_ALIVE_CHAT", "20m")
os.environ.setdefault("OLLAMA_URL_DEEP", os.getenv("OLLAMA_URL_DEEP", "http://127.0.0.1:11434") or "http://127.0.0.1:11434")
os.environ.setdefault("OLLAMA_MODEL_DEEP", os.getenv("OLLAMA_MODEL_DEEP", "gemma4:31b") or "gemma4:31b")
os.environ.setdefault("OLLAMA_MODEL_REASON", os.environ["OLLAMA_MODEL_DEEP"])


def _apollo_chat_base_url() -> str:
    return str(os.getenv("OLLAMA_URL_CHAT") or os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434").strip().rstrip("/")


def _apollo_chat_model() -> str:
    return str(os.getenv("OLLAMA_MODEL_CHAT") or os.getenv("OLLAMA_MODEL") or "gemma3:12b").strip()


def _apollo_chat_keep_alive() -> str:
    return str(os.getenv("OLLAMA_KEEP_ALIVE_CHAT") or os.getenv("OLLAMA_KEEP_ALIVE") or "20m").strip()


def _apollo_deep_base_url() -> str:
    return str(os.getenv("OLLAMA_URL_DEEP") or os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434").strip().rstrip("/")


def _apollo_deep_model() -> str:
    return str(os.getenv("OLLAMA_MODEL_DEEP") or os.getenv("OLLAMA_MODEL_REASON") or "gemma4:31b").strip()


def _loan_review_payload_with_artifacts(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})

    def _decorate_capture(capture: Any) -> Dict[str, Any]:
        row = dict(capture or {}) if isinstance(capture, dict) else {}
        screenshot_path = str(row.get("screenshot_path") or "").strip()
        if screenshot_path:
            row["artifact_url"] = f"/admin/student_loan_review/artifact?path={quote(screenshot_path)}"
        return row

    session = dict(out.get("session") or {}) if isinstance(out.get("session"), dict) else {}
    if session:
        session["last_capture"] = _decorate_capture(session.get("last_capture"))
        session["previous_capture"] = _decorate_capture(session.get("previous_capture"))
        out["session"] = session
    if isinstance(out.get("capture"), dict):
        out["capture"] = _decorate_capture(out.get("capture"))
    return out


OLLAMA_URL = _apollo_chat_base_url()
OLLAMA_MODEL = _apollo_chat_model()
OWUI_URL = str(os.getenv("OWUI_URL", "http://127.0.0.1:3000") or "http://127.0.0.1:3000").strip().rstrip("/")
OWUI_MODEL = str(os.getenv("OWUI_MODEL") or OLLAMA_MODEL).strip()

try:
    from common.model_routes import register_model_routes
    register_model_routes(app)
except Exception as _mr_err:
    print(f"[model_routes] registration failed: {_mr_err}")

try:
    from common.governance.congress_routes import register_congress_routes
    register_congress_routes(app, AGENT_NAME)
except Exception as exc:
    print(f"[{AGENT_NAME}] congress routes registration failed: {exc}")

try:
    from common.gov_ops_control import register_gov_ops_control

    if AGENT_NAME.lower() != "sky":
        register_gov_ops_control(app, AGENT_NAME)
except Exception:
    pass


LOG_DIR = Path(os.getenv("APOLLO_LOGS_DIR", str(Path(__file__).resolve().parent / "logs")))
OCR_UPLOAD_DIR = LOG_DIR / "ocr_uploads"
VISION_UPLOAD_DIR = LOG_DIR / "vision_uploads"
try:
    from common.vision_routes import register_vision_routes
    register_vision_routes(app, AGENT_NAME, VISION_UPLOAD_DIR)
except Exception as exc:
    print(f"[{AGENT_NAME}] vision_routes registration failed: {exc}")
try:
    from common.ocr_routes import register_ocr_routes
    register_ocr_routes(app, AGENT_NAME, OCR_UPLOAD_DIR)
except Exception as exc:
    print(f"[{AGENT_NAME}] ocr_routes registration failed: {exc}")

LOG_DIR.mkdir(parents=True, exist_ok=True)

APOLLO_RAG = AgentRAG("Apollo")

# Financial corpus (apollo_financial ChromaDB) — static training docs.
# Separate from the operational AgentRAG; wired into gather_hits() so the
# chat pipeline can ground answers in the 620+ financial education chunks.
_FINANCIAL_CORPUS_COL = None
_HIPPO_RETRIEVER = None
_FINANCIAL_CORPUS_STATUS = {
    "financial_corpus_ready": False,
    "collection_exists": False,
    "doc_count": 0,
    "real_chunk_count": 0,
    "synthetic_chunk_count": 0,
    "real_ratio": 0.0,
    "bootstrap_state": "init",
    "collection_name": str(os.getenv("RAG_COLLECTION", "apollo_financial")),
}
try:
    import chromadb as _chromadb
    from chromadb.config import Settings as _ChromaSettings
    _fc_dir_raw = os.getenv("RAG_DIR", "")
    if _fc_dir_raw and not os.path.isabs(_fc_dir_raw):
        _fc_dir = str((_APOLLO_ROOT / _fc_dir_raw).resolve())
    else:
        _fc_dir = str(_fc_dir_raw or (_APOLLO_ROOT / "chroma_db"))
    _fc_name = os.getenv("RAG_COLLECTION", "apollo_financial")
    _fc_min_docs = max(0, int(os.getenv("APOLLO_FINANCIAL_CORPUS_MIN_DOCS", "100")))
    _fc_client = _chromadb.PersistentClient(
        path=_fc_dir,
        settings=_ChromaSettings(allow_reset=False, anonymized_telemetry=False),
    )
    _fc_collection = _fc_client.get_or_create_collection(_fc_name)
    _fc_count = int(_fc_collection.count())
    _fc_audit = apollo_corpus_bootstrap.audit_financial_corpus({"collection": _fc_name, "rag_dir": _fc_dir})
    _FINANCIAL_CORPUS_STATUS.update(
        {
            "collection_exists": True,
            "doc_count": _fc_count,
            "real_chunk_count": int((_fc_audit.get("real_chunk_count") or 0) if isinstance(_fc_audit, dict) else 0),
            "synthetic_chunk_count": int((_fc_audit.get("synthetic_chunk_count") or 0) if isinstance(_fc_audit, dict) else 0),
            "real_ratio": float((_fc_audit.get("real_ratio") or 0.0) if isinstance(_fc_audit, dict) else 0.0),
            "min_docs_required": _fc_min_docs,
            "bootstrap_state": "loaded" if _fc_count >= _fc_min_docs else "below_min_docs",
            "collection_name": _fc_name,
            "financial_corpus_ready": _fc_count >= _fc_min_docs,
        }
    )
    if _FINANCIAL_CORPUS_STATUS["financial_corpus_ready"]:
        _FINANCIAL_CORPUS_COL = _fc_collection
        print(f"[{AGENT_NAME}] Financial corpus loaded: {_fc_count} docs ({_fc_name})")
    else:
        print(
            f"[{AGENT_NAME}] Financial corpus present but below readiness threshold: "
            f"{_fc_count} < {_fc_min_docs} docs ({_fc_name})"
        )

    # HippoRAG 2 — graph-augmented retrieval over the financial corpus
    if _FINANCIAL_CORPUS_COL is not None:
        try:
            from apollo_hipporag.retriever import HippoRetriever
            _kg_path = os.path.join(_fc_dir, "financial_kg.json")
            _HIPPO_RETRIEVER = HippoRetriever.load(_FINANCIAL_CORPUS_COL, _kg_path)
            _kg_stats = _HIPPO_RETRIEVER.kg.stats()
            print(f"[{AGENT_NAME}] HippoRAG KG: {_kg_stats['nodes']} nodes, {_kg_stats['edges']} edges, {_kg_stats['indexed_docs']} indexed")
        except Exception as _hippo_exc:
            _FINANCIAL_CORPUS_STATUS["bootstrap_state"] = "hipporag_unavailable"
            print(f"[{AGENT_NAME}] HippoRAG unavailable ({_hippo_exc}); falling back to flat search")

except Exception as _fc_exc:
    _FINANCIAL_CORPUS_STATUS.update(
        {
            "financial_corpus_ready": False,
            "collection_exists": False,
            "doc_count": 0,
            "bootstrap_state": f"error:{type(_fc_exc).__name__}",
        }
    )
    print(f"[{AGENT_NAME}] Financial corpus unavailable ({_fc_exc}); chat will rely on AgentRAG only.")


def _query_financial_corpus(query: str, top_k: int, kinds: list | None = None) -> list:
    """Query apollo_financial via HippoRAG (or flat ChromaDB fallback)."""
    def _prefer_real_public(hits: list[dict]) -> list[dict]:
        ranked: list[dict] = []
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
            provenance = str((meta or {}).get("provenance_class") or "").strip().lower()
            bonus = 0.12 if provenance == "real_public" else (0.03 if provenance == "seed_local" else 0.0)
            try:
                base = float(hit.get("score") or 0.0)
            except Exception:
                base = 0.0
            row = dict(hit)
            row["score"] = round(base + bonus, 4)
            row["real_public"] = provenance == "real_public"
            ranked.append(row)
        ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return ranked[:top_k]

    # HippoRAG path — graph-augmented retrieval
    if _HIPPO_RETRIEVER is not None:
        try:
            return _prefer_real_public(_HIPPO_RETRIEVER.search(query, top_k=top_k))
        except Exception:
            pass  # fall through to flat search

    # Flat ChromaDB fallback
    if _FINANCIAL_CORPUS_COL is None:
        return []
    try:
        where = {"kind": {"$in": kinds}} if kinds else None
        res = _FINANCIAL_CORPUS_COL.query(
            query_texts=[query],
            n_results=min(top_k, _FINANCIAL_CORPUS_COL.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        hits = []
        for text, meta, dist in zip(docs, metas, dists):
            hits.append({
                "text": text or "",
                "score": round(1.0 - float(dist), 4),
                "meta": meta or {},
                "source": "financial_corpus",
            })
        return _prefer_real_public(hits)
    except Exception:
        return []

# Session memory — persistent user profile across conversations.
_MEMORY_STORE = None
try:
    from apollo_memory.store import MemoryStore
    from apollo_memory import extractor as _mem_extractor
    from apollo_memory import context as _mem_context
    _MEMORY_STORE = MemoryStore(APOLLO_RAG)
    print(f"[{AGENT_NAME}] Session memory: online")
except Exception as _mem_exc:
    print(f"[{AGENT_NAME}] Session memory unavailable ({_mem_exc})")

TRACE_DIR = Path(APOLLO_RAG.get_collection_path()) / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)
TRACE_FILE = TRACE_DIR / "chat_traces.jsonl"
SNAPSHOT_GUARD_SECONDS = 5
LAST_ACTIVITY_TS = time.time()

# MCP tool loading — shared tools + Apollo-specific tools.
# Each module self-registers via common.mcp.register() on import.
# Failures are logged but never fatal.
_MCP_TOOL_CANDIDATES = [
    # Shared (Sky) tools
    "Sky.tools.web_mcp_tool",       # → web.search
    "Sky.tools.rag_mcp_tool",       # → rag.*
    "common.vision_tool",           # → vision.*
    # Apollo-specific tools
    "apollo_tools.mcp_calculator",  # → apollo.calculator
    "apollo_budget.mcp_budget_tool",  # → apollo.budget_query
    "apollo_tools.mcp_financial_news",  # → apollo.financial_news
    "Apollo.apollo_tools.mcp_event_impact",  # → apollo.event_*
    "Apollo.apollo_tools.mcp_market_study",  # → apollo.market_*
    "Apollo.apollo_tools.mcp_swing_study",  # → apollo.swing_*
    "Apollo.apollo_tools.mcp_ebay_listing",  # → apollo.ebay_listing
]
_mcp_loaded: list[str] = []
import importlib as _importlib
for _mcp_mod in _MCP_TOOL_CANDIDATES:
    try:
        _importlib.import_module(_mcp_mod)
        _mcp_loaded.append(_mcp_mod)
    except Exception as _mcp_e:
        print(f"[{AGENT_NAME}] MCP tool unavailable ({_mcp_mod}): {_mcp_e}")
if _mcp_loaded:
    print(f"[{AGENT_NAME}] MCP tools loaded: {_mcp_loaded}")

# Wire shared /ingest route for web scrape and OCR ingestion
try:
    from common.ingest_routes import register_ingest_routes
    register_ingest_routes(app, APOLLO_RAG, AGENT_NAME)
except Exception as _ingest_err:
    print(f"[{AGENT_NAME}] ingest_routes registration failed: {_ingest_err}")

try:
    register_budget_routes(app, APOLLO_RAG)
except Exception as _budget_err:
    print(f"[{AGENT_NAME}] budget routes registration failed: {_budget_err}")

try:
    app.register_blueprint(apollo_mcp_routes.mcp_bp)
except Exception as _mcp_route_err:
    print(f"[{AGENT_NAME}] mcp routes registration failed: {_mcp_route_err}")

SESSION_STATE: Dict[str, Dict[str, Any]] = {}


def _state(cid: str) -> Dict[str, Any]:
    return SESSION_STATE.setdefault(cid, {})

# Ensure Apollo's /rag/* endpoints use Apollo's RAG store (not another agent's).
apollo_rag_routes.RAG = APOLLO_RAG

app.register_blueprint(apollo_rag_routes.bp_rag)

# --- autorun blueprint (robust when running as script) ---
try:
    from Apollo import autorun_routes as autorun_bp  # app folder now on sys.path
except Exception as e:
    print(f"[autorun] import failed: {e}")
else:
    try:
        app.register_blueprint(autorun_bp.bp)
        print("[autorun] blueprint registered")
    except Exception as e:
        print(f"[autorun] registration failed: {e}")

supervisor = AutoRunSupervisor()


LOCAL_DEPTH_TAGS = {
    "deep": ["think longer", "consider deeply", "analyze this carefully", "reflect", "walk me through", "full analysis", "detailed breakdown"],
    "fast": ["quick", "tl;dr", "short answer", "just tell me", "summary only", "brief"],
}

# Financial domain keywords
MARKETS_KEYWORDS = ["stock", "market", "price", "ticker", "shares", "crypto", "bitcoin", "forex", "index", "s&p", "nasdaq", "dow", "trading", "bull", "bear"]
PERSONAL_FINANCE_KEYWORDS = ["budget", "savings", "debt", "loan", "credit", "mortgage", "401k", "ira", "emergency fund", "spending", "income"]
RISK_KEYWORDS = ["risk", "volatility", "hedge", "diversify", "exposure", "downside", "var", "drawdown", "beta", "sharpe"]
TAX_KEYWORDS = ["tax", "deduction", "irs", "capital gains", "w-2", "1099", "filing", "refund", "withholding", "bracket"]
INVESTING_KEYWORDS = ["invest", "portfolio", "allocation", "dividend", "etf", "mutual fund", "bond", "yield", "return", "compound"]
MACRO_KEYWORDS = ["gdp", "inflation", "fed", "interest rate", "unemployment", "recession", "cpi", "ppi", "monetary", "fiscal"]

INTENT_VALUES = {"markets", "personal_finance", "risk", "tax", "investing", "macro", "unknown"}

_APP_ROOT = Path(__file__).resolve().parent

# Patterns that signal the query needs live/current data from the web.
_WEB_TRIGGER_RE = re.compile(
    r"\b(today|current|latest|recent|right now|as of|this week|this month|this year|2026"
    r"|search the web|look up online|find online|web search|look it up"
    r"|earnings|announcement|just announced|breaking"
    r"|cpi|ppi|gdp|fed rate|fomc|interest rate|mortgage rate|treasury yield"
    r"|market open|market close|market today|premarket|after.?hours"
    r"|stock price|share price|trading at|crypto price|bitcoin|ethereum"
    r"|inflation rate|unemployment rate|jobs report|nonfarm)\b",
    re.I,
)

# Patterns that signal the user wants Apollo to search its own knowledge base.
_RAG_SEARCH_RE = re.compile(
    r"\b(search your docs|search your knowledge|search your database|search your rag"
    r"|search your files|look in your docs|look in your knowledge"
    r"|what do you have on|what do you know about|what's in your"
    r"|pull from your docs|find in your docs|check your docs"
    r"|from your knowledge base|in your knowledge base)\b",
    re.I,
)


def _web_worthy(user_msg: str, intent: str) -> bool:
    """True when the query likely benefits from live web data."""
    if _WEB_TRIGGER_RE.search(user_msg):
        return True
    if intent in ("markets", "macro") and len(user_msg.strip()) > 20:
        return True
    return False


_EXPLICIT_WEB_RE = re.compile(
    r"^\s*(?:web\s*search|web|search\s+the\s+web|browse\s+the\s+web)\s*[:\-]?\s+(?P<q>.+?)\s*$",
    re.I,
)
_EXPLICIT_RAG_RE = re.compile(
    r"^\s*(?:rag\s*search|rag|search\s+rag"
    r"|search\s+(?:your\s+)?(?:memory|notes|kb|knowledge\s+base|docs|documents|database|files|corpus)"
    r"|look\s+in\s+(?:your\s+)?(?:docs|documents|memory|knowledge|notes|database)"
    r"|check\s+(?:your\s+)?(?:docs|documents|memory|knowledge|notes)"
    r"|pull\s+from\s+(?:your\s+)?(?:docs|documents|knowledge|memory)"
    r"|find\s+in\s+(?:your\s+)?(?:docs|documents|knowledge|memory)"
    r")\s*[:\-]?\s+(?P<q>.+?)\s*$",
    re.I,
)


def _explicit_web_query(message: str) -> str | None:
    if not message:
        return None
    match = _EXPLICIT_WEB_RE.match(message)
    if not match:
        return None
    query = str(match.group("q") or "").strip()
    return query[:220] if query else None


def _explicit_rag_query(message: str) -> str | None:
    if not message:
        return None
    match = _EXPLICIT_RAG_RE.match(message)
    if not match:
        return None
    query = str(match.group("q") or "").strip()
    return query[:220] if query else None


def _refine_web_query(user_msg: str, intent: str) -> str:
    """Produce a focused search query, strip filler, expand abbreviations, anchor to year."""
    q = re.sub(r"\b(please|can you|what is|tell me|i want to know|explain|do you know)\b", "", user_msg, flags=re.I)
    for pat, rep in (
        (r"\bCPI\b", "CPI inflation"),
        (r"\bPPI\b", "PPI producer price index"),
        (r"\bGDP\b", "GDP economic growth"),
        (r"\bFOMC\b", "FOMC Federal Reserve meeting"),
        (r"\bFed\b", "Federal Reserve"),
    ):
        q = re.sub(pat, rep, q)
    q = re.sub(r"\s+", " ", q).strip()
    _YEAR = "2026"
    if _YEAR not in q and re.search(r"\b(current|latest|today|recent|rate|price|yield|forecast|data)\b", q, re.I):
        q = f"{q} {_YEAR}"
    return q[:140].strip()


def _format_web_block(results: list) -> str:
    """Structured web result block with domain attribution for the prompt."""
    if not results:
        return ""
    lines = [f"[Web — {len(results)} result(s) — cite sources in your reply]"]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        snippet = re.sub(r"\s+", " ", (r.get("snippet") or r.get("body") or "")).strip()[:260]
        url = (r.get("url") or "").strip()
        domain = ""
        if url:
            try:
                from urllib.parse import urlparse as _up
                domain = _up(url).netloc.replace("www.", "")
            except Exception:
                pass
        lines.append(f"{i}. {title}" + (f" [{domain}]" if domain else ""))
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


def _merge_tool_hits(*lists: list[dict], max_total: int = 10) -> list[dict]:
    """Merge hit lists, dedupe by leading text, keep best score."""
    seen: dict[str, dict] = {}
    for hits in lists:
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            text = str(hit.get("text") or "").strip()
            key = text[:160]
            if not key:
                continue
            if "source" not in hit:
                hit["source"] = "rag"
            try:
                score = float(hit.get("score") or 0.0)
            except Exception:
                score = 0.0
            prev = seen.get(key)
            if prev is None:
                seen[key] = hit
                continue
            try:
                prev_score = float(prev.get("score") or 0.0)
            except Exception:
                prev_score = 0.0
            if score > prev_score:
                seen[key] = hit
    merged = sorted(seen.values(), key=lambda h: float(h.get("score") or 0.0), reverse=True)
    return merged[: max(1, int(max_total or 10))]


def _rag_dual_search(query: str, top_k: int = 5) -> list[dict]:
    """Search operational AgentRAG + financial corpus, then merge."""
    top_k = max(1, min(int(top_k or 5), 12))
    mem_hits: list[dict] = []
    corpus_hits: list[dict] = []
    try:
        mem_hits = APOLLO_RAG.search(query=query, top_k=top_k).get("results", []) or []
        for hit in mem_hits:
            if isinstance(hit, dict):
                hit.setdefault("source", "operational_rag")
    except Exception:
        mem_hits = []
    try:
        corpus_hits = _query_financial_corpus(query, top_k=top_k, kinds=None) or []
        for hit in corpus_hits:
            if isinstance(hit, dict):
                hit.setdefault("source", hit.get("source") or "financial_corpus")
    except Exception:
        corpus_hits = []
    return _merge_tool_hits(mem_hits, corpus_hits, max_total=top_k * 2)


def _sync_focus_state(note: str = "") -> dict:
    try:
        return apollo_focus_trading.sync_from_pipeline(
            rag=APOLLO_RAG,
            memory_store=_MEMORY_STORE,
            note=note,
        )
    except Exception:
        return apollo_focus_trading.load_state()


def _focus_context_block() -> str:
    state = _sync_focus_state()
    text = str(state.get("discussion_context") or "").strip()
    if not text:
        return ""
    return "[APOLLO_FOCUS_CONTEXT]\n" + text + "\n[/APOLLO_FOCUS_CONTEXT]"


def _read_json_file(path: Path, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _safe_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _apollo_trade_decision_label(proposal: Dict[str, Any]) -> str:
    if not proposal:
        return "no current proposal"
    no_trade = str(proposal.get("no_trade_reason") or "").strip()
    rr = _safe_num(proposal.get("risk_reward_estimate"))
    if no_trade:
        return f"blocked: {no_trade}"
    if rr > 0 and rr < 1.0:
        return f"blocked: reward does not justify risk (R/R {rr:.2f} < 1.00)"
    if str(proposal.get("direction_bias") or "") != "long_bias":
        return f"blocked: direction bias is {proposal.get('direction_bias') or 'unknown'}"
    return "paper candidate: review levels before simulated execution"


def _format_proposal_line(row: Dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "").strip() or "?"
    setup = str(row.get("setup_type") or "").replace("_", " ") or "unknown setup"
    score = row.get("score", row.get("confidence", ""))
    rr = row.get("risk_reward_estimate", "")
    decision = _apollo_trade_decision_label(row)
    return f"- {ticker}: {setup}, score={score}, R/R={rr}, decision={decision}"


def _latest_nightly_ocr_status() -> Dict[str, Any]:
    status = apollo_nightly_pipeline.latest_status() or {}
    run_dir_raw = str(status.get("run_dir") or "").strip()
    if not run_dir_raw:
        return {}
    return _read_json_file(Path(run_dir_raw) / "02_ocr_ingest.json", {})


def _apollo_dashboard_context_block(user_msg: str = "") -> str:
    """Compact local state so Apollo can explain the dashboard, decisions, and plan."""
    low = str(user_msg or "").lower()
    dashboard_terms = (
        "apollo", "dashboard", "swing", "trade", "market", "plan", "decision", "why",
        "simulation", "balance", "invest", "return", "hipporag", "nightly", "learn",
    )
    if low and not any(term in low for term in dashboard_terms):
        return ""

    try:
        latest = apollo_swing_study.latest_swing_study()
    except Exception:
        latest = {}
    proposals = [dict(row) for row in list((latest or {}).get("proposals") or []) if isinstance(row, dict)]
    best = dict((latest or {}).get("best_proposal") or (proposals[0] if proposals else {}))
    market_regime = dict((latest or {}).get("market_regime") or best.get("market_regime") or {})
    best_components = dict(best.get("score_components") or {})

    account = _read_json_file(LOG_DIR / "swing_simulation" / "account_state.json", {})
    pretrade = apollo_pretrade_gate.latest_status() or {}
    daily = apollo_daily_report.latest_daily_report() or {}
    daily_report_payload = dict(daily.get("report") or {})
    nightly = apollo_nightly_pipeline.latest_status() or {}
    nightly_quality = dict(nightly.get("quality") or {})
    stage_scores = dict(nightly_quality.get("stage_scores") or {})
    corpus = dict(nightly.get("corpus") or (nightly.get("stage_details") or {}).get("corpus") or {})
    ocr = _latest_nightly_ocr_status()
    ocr_docs = [dict(row) for row in list(ocr.get("documents") or []) if isinstance(row, dict)]
    news_docs = [row for row in ocr_docs if row.get("news_feed") or str(row.get("source_doc_class") or "") == "news_feed"]
    recent_articles: list[str] = []
    for doc in news_docs[:4]:
        ticker = str(doc.get("ticker") or "").strip()
        for article in list(doc.get("news_articles") or [])[:2]:
            if isinstance(article, dict):
                headline = str(article.get("headline") or "").strip()
                if headline:
                    recent_articles.append(f"{ticker}: {headline[:140]}")

    lines = [
        "[APOLLO_DASHBOARD_CONTEXT]",
        "Purpose: use this local dashboard state when discussing Apollo's UI, swing decisions, market plan, simulation balance, or nightly learning.",
        "Guardrails: paper_sim only; live_trade_execution=false; human approval required; no Alpaca/live broker execution until Apollo proves profitable on live data over repeated paper cycles.",
        f"Swing desk: run_id={latest.get('run_id') or ''}; created_at={latest.get('created_at') or ''}; proposals={len(proposals)}; high_confidence={latest.get('high_confidence')}; blocked_session={latest.get('blocked_session')}.",
        f"Market regime: label={market_regime.get('label') or ''}; score={market_regime.get('score') or ''}; notes={market_regime.get('risk_notes') or []}.",
    ]
    if best:
        lines.extend(
            [
                (
                    "Best setup: "
                    f"ticker={best.get('ticker')}; setup={best.get('setup_type')}; score={best.get('score')}; "
                    f"status={best.get('status')}; confidence={best.get('confidence_label')}; "
                    f"entry={best.get('entry_zone')}; stop={best.get('stop_zone')}; target={best.get('target_zone')}; "
                    f"R/R={best.get('risk_reward_estimate')}; review_by={best.get('review_by_date')}; "
                    f"invalidation={best.get('invalidation_trigger')}; decision={_apollo_trade_decision_label(best)}."
                ),
                (
                    "Decision inputs: "
                    f"trend={best_components.get('trend')}; momentum={best_components.get('momentum')}; "
                    f"relative_strength={best_components.get('relative_strength')}; volume={best_components.get('volume')}; "
                    f"catalyst={best_components.get('catalyst')}; market_regime={best_components.get('market_regime')}; "
                    f"provider_confidence={best_components.get('provider_confidence')}; kg_grounding={best_components.get('kg_grounding')}; "
                    f"event_risk={best.get('earnings_or_event_risk') or {}}."
                ),
            ]
        )
    if proposals:
        lines.append("Top setup queue:")
        lines.extend(_format_proposal_line(row) for row in proposals[:5])

    if account:
        lines.append(
            "Paper simulation account: "
            f"starting_balance=${_safe_num(account.get('starting_balance')):.2f}; "
            f"cash=${_safe_num(account.get('cash_balance')):.2f}; "
            f"invested=${_safe_num(account.get('invested_amount')):.2f}; "
            f"market_value=${_safe_num(account.get('market_value')):.2f}; "
            f"realized_return={account.get('realized_return_pct', 0)}%; "
            f"unrealized_return={account.get('unrealized_return_pct', 0)}%; "
            f"status={account.get('status')}; open_positions={len(list(account.get('open_positions') or []))}."
        )

    if pretrade:
        pre_candidate = dict(pretrade.get("candidate") or {})
        lines.append(
            "Latest daytime pre-trade gate: "
            f"run_id={pretrade.get('run_id') or ''}; checked_at_ct={pretrade.get('checked_at_ct') or ''}; "
            f"trigger={pretrade.get('trigger') or ''}; ticker={pre_candidate.get('ticker') or ''}; "
            f"decision={pretrade.get('decision') or ''}; blocker={pretrade.get('blocker') or 'none'}; "
            f"next_action={pretrade.get('next_action') or ''}; "
            f"headlines_checked={len(list(pretrade.get('headlines') or []))}; "
            f"heavy_daytime_hipporag_refresh={pretrade.get('heavy_daytime_hipporag_refresh')}."
        )
    if daily_report_payload:
        latest_gate = dict(daily_report_payload.get("latest_gate") or {})
        lines.append(
            "Daily report: "
            f"date={daily_report_payload.get('date') or daily.get('date') or ''}; "
            f"path={daily_report_payload.get('markdown_path') or ''}; "
            f"events={len(list(daily_report_payload.get('events') or []))}; "
            f"latest_gate_decision={latest_gate.get('decision') or 'none'}."
        )

    lines.append(
        "Nightly learning: "
        f"run_id={nightly.get('run_id') or ''}; ok={nightly.get('ok')}; stage={nightly.get('current_stage')}; "
        f"quality_gate={nightly_quality.get('gate_pass')}; scores={stage_scores}; "
        f"corpus_total={corpus.get('total_chunks')}; real={corpus.get('real_chunk_count')}; synthetic={corpus.get('synthetic_chunk_count')}; "
        f"latest_ocr_ingested={ocr.get('ingested')}; latest_ocr_errors={list(ocr.get('errors') or [])[:3]}."
    )
    if recent_articles:
        lines.append("Recent parsed news-feed articles:")
        lines.extend(f"- {item}" for item in recent_articles[:6])
    lines.append("Routine schedule: 01:00 CT gathers/ingests articles; 03:15 CT bounded HippoRAG refresh updates the graph; 07:45 CT opens the daily report; 08:45-15:15 CT runs lightweight pre-trade checks as needed; 16:35 CT finalizes the daily report.")
    lines.append("Response rule: when asked what Apollo is doing or why it chose/blocked a setup, cite the best setup, R/R gate, score components, market regime, simulation account, latest pre-trade gate, daily report, and nightly learning state from this block.")
    lines.append("[/APOLLO_DASHBOARD_CONTEXT]")
    return "\n".join(lines)


def _is_focus_question(message: str) -> bool:
    low = (message or "").lower()
    triggers = (
        "focus universe",
        "small universe",
        "what are you focused on",
        "what did you learn",
        "current theme",
        "!focus",
    )
    return any(token in low for token in triggers)


def _is_focus_learn_request(message: str) -> bool:
    low = (message or "").lower().strip()
    triggers = (
        "!learn_focus",
        "learn from latest run",
        "refresh what you learned",
        "sync your focus universe",
        "update your focus universe",
    )
    return any(token in low for token in triggers)


def _is_paper_positions_question(message: str) -> bool:
    low = (message or "").lower()
    triggers = (
        "!paper_positions",
        "paper positions",
        "theoretical positions",
        "theoretical trades",
        "paper trades",
    )
    return any(token in low for token in triggers)


def _parse_paper_trade_request(message: str) -> dict:
    raw = str(message or "").strip()
    match = re.match(
        r"^(?:!paper_trade|!paper|paper trade)\s+(buy|sell|hold|no_trade|close)(?:\s+([A-Za-z.\-]{1,12}))?(?:\s+(.*))?$",
        raw,
        flags=re.I,
    )
    if not match:
        return {}
    action = str(match.group(1) or "").strip().lower()
    ticker = str(match.group(2) or "").strip().upper()
    tail = str(match.group(3) or "").strip()
    confidence = 0.0
    confidence_match = re.search(r"\bconfidence[:=]?\s*(0(?:\.\d+)?|1(?:\.0+)?)\b", tail, flags=re.I)
    if confidence_match:
        try:
            confidence = float(confidence_match.group(1))
        except Exception:
            confidence = 0.0
        tail = re.sub(r"\bconfidence[:=]?\s*(0(?:\.\d+)?|1(?:\.0+)?)\b", "", tail, flags=re.I).strip()
    horizon = ""
    horizon_match = re.search(r"\bhorizon[:=]?\s*([A-Za-z0-9_\- ]{2,40})$", tail, flags=re.I)
    if horizon_match:
        horizon = str(horizon_match.group(1) or "").strip()
        tail = tail[: horizon_match.start()].strip()
    thesis = re.sub(r"^(because|thesis:?)\s+", "", tail, flags=re.I).strip()
    return {
        "action": action,
        "ticker": ticker,
        "thesis": thesis,
        "confidence": confidence,
        "horizon": horizon,
    }


def _apollo_prompt_proof_kind(message: str) -> str:
    low = (message or "").lower()
    if low.startswith(("!market_study", "!market-study", "!market")):
        return "market_study"
    if low.startswith(("!trade_proof", "!trade-proof", "!stock_proof", "!stock-proof")):
        return "trade_proof"
    if any(token in low for token in ("stock trade proof", "trade proof", "stock proof", "homework trade proof")):
        return "trade_proof"
    if any(token in low for token in ("market research", "market study", "run market", "apollo research proof")):
        return "market_study"
    if any(token in low for token in ("swing study", "swing trade study")):
        return "swing_study"
    return ""


def _apollo_prompt_tickers(message: str) -> list[str]:
    raw = str(message or "")
    explicit = re.search(r"\b(?:tickers?|symbols?)\s*[:=]\s*([A-Za-z0-9,.\-\s]{1,120})", raw, flags=re.I)
    if explicit:
        return apollo_market_data.normalize_tickers({"tickers": explicit.group(1)})
    command_tail = re.sub(
        r"^(?:!market_study|!market-study|!market|!trade_proof|!trade-proof|!stock_proof|!stock-proof)\s*",
        "",
        raw.strip(),
        flags=re.I,
    )
    tickers = apollo_market_data.normalize_tickers({"tickers": command_tail})
    return tickers[:12]


def _format_apollo_prompt_proof_reply(kind: str, result: Dict[str, Any]) -> str:
    if kind == "market_study":
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
        best = result.get("best_proposal") if isinstance(result.get("best_proposal"), dict) else {}
        return (
            "Apollo market research proof complete.\n"
            f"run_id={result.get('run_id')} ok={bool(result.get('ok'))} high_confidence={bool(result.get('high_confidence'))}\n"
            f"best={best.get('ticker') or 'none'} {best.get('direction_bias') or ''} confidence={best.get('confidence')}\n"
            f"report={artifacts.get('report_path') or result.get('report_path') or ''}\n"
            "Research-only: no broker order was created."
        )
    if kind == "swing_study":
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
        best = result.get("best_proposal") if isinstance(result.get("best_proposal"), dict) else {}
        return (
            "Apollo swing study proof complete.\n"
            f"run_id={result.get('run_id')} ok={bool(result.get('ok'))}\n"
            f"best={best.get('ticker') or 'none'} {best.get('setup_type') or ''} {best.get('direction_bias') or ''} score={best.get('score')}\n"
            f"report={artifacts.get('report_path') or ''}\n"
            "Research-only: no broker order was created."
        )
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    plan = result.get("trade_plan") if isinstance(result.get("trade_plan"), dict) else {}
    return (
        "Apollo stock trade proof complete.\n"
        f"run_id={result.get('run_id')} ok={bool(result.get('ok'))}\n"
        f"decision={plan.get('decision') or 'unknown'} ticker={plan.get('ticker') or 'none'} score={plan.get('score')}\n"
        f"report={artifacts.get('report_path') or ''}\n"
        "Research-only: human approval is required and live_trade_execution=false."
    )


def _run_apollo_prompt_proof(message: str) -> tuple[str, Dict[str, Any]]:
    kind = _apollo_prompt_proof_kind(message)
    tickers = _apollo_prompt_tickers(message)
    payload: Dict[str, Any] = {"write_artifacts": True, "notify": False, "refresh": True}
    if tickers:
        payload["tickers"] = ",".join(tickers)
    if kind == "market_study":
        result = apollo_market_study.build_market_study(payload)
    elif kind == "swing_study":
        result = apollo_swing_study.build_swing_study(payload)
    elif kind == "trade_proof":
        payload.update(
            {
                "upstream_ocr_task": "prompt_triggered_stock_trade_proof",
                "upstream_ocr_status": "prompt_proof",
                "upstream_ocr_result": "not_required_for_prompt_proof",
                "upstream_ocr_completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        result = apollo_homework_trade_pipeline.build_homework_trade_plan(payload)
    else:
        result = {"ok": False, "error": "unknown_prompt_proof_kind"}
    return kind, result


def _run_gx10_deep_analysis(user_msg: str, intent: str, rag_hits: list) -> str:
    """
    Pre-analysis pass routed to the gx10 model (OLLAMA_MODEL_DEEP).
    Produces a focused analytical block that is injected into the main prompt.
    """
    ctx_lines = [
        f"- {h.get('text', '')[:300]}"
        for h in rag_hits[:6]
        if (h.get("text") or "").strip()
    ]
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
    try:
        return query_model(
            prompt,
            model=_apollo_deep_model(),
            force_ui_chat=False,
            base_url=_apollo_deep_base_url(),
        ).strip()
    except Exception:
        return ""


def _web_search(payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """Call web.search MCP tool. Returns disabled sentinel when SKY_WEB_ENABLED != 1."""
    if os.getenv("SKY_WEB_ENABLED") != "1":
        return {"ok": False, "error": "web_disabled", "results": []}
    try:
        from common import mcp as _mcp
        return _mcp.run("web.search", payload or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc), "results": []}


def _load_kb_personality_points(max_items: int = 6) -> list:
    base_path = Path(
        os.getenv(
            "APOLLO_KB_PERSONALITY_PATH",
            str(_APP_ROOT / "memory" / "kb" / "apollo_personality.md"),
        )
    )
    addendum_path = _APP_ROOT / "memory" / "kb" / "apollo_quality_addendum.md"

    paths = [base_path, addendum_path]
    points: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if raw.startswith("- "):
                    text = raw[2:].strip()
                    if text:
                        points.append(text[:200])
                if len(points) >= max_items:
                    return points[:max_items]
        except Exception:
            continue
    return points[:max_items]


TOOLING_AWARENESS = (
    "Tooling Awareness:\n"
    "- Knowledge base: 620+ financial education chunks (HippoRAG graph-augmented) + operational memory are queried automatically on every response.\n"
    "- To explicitly search docs: say 'search your docs for <topic>', 'look in your docs: <topic>', '!rag <query>', or 'rag search: <query>'.\n"
    "- Web search is ENABLED and fires automatically for current/2026/live data queries. Also available via '!web <query>' or 'web search: <query>'.\n"
    "- OCR available via MCP tool ocr.dual or the /ocr/run upload for images and PDFs.\n"
    "- OCR routing default is GB10-first: PaddleOCR-VL for PDFs/layout-heavy docs, GOT-OCR2 for dense scans/handwriting, and Qwen-VL OCR for semantic cleanup or fallback; local Tesseract/RapidOCR are fallback only.\n"
    "- When OCR quality matters, Apollo should prefer model combinations like PaddleOCR-VL -> Qwen cleanup for finance PDFs and reports.\n"
    "- Vision available via MCP tool vision.process or the /vision/run upload for image understanding.\n"
    "- Budget data available via /budget/* endpoints (imports, categories, transactions, insights).\n"
    "- Trading homework (web+OCR ingest) available via /admin/trading_homework/run.\n"
    "- Background finance pipeline available via /admin/background_pipeline/run and /admin/background_pipeline/schedule; it opportunistically uses GB10 when idle and yields when GB10 is busy.\n"
    "- Nightly finance pipeline remains available via /admin/nightly_pipeline/run for one-shot runs (stages: gather -> OCR/ingest -> HippoRAG -> self_check).\n"
    "- Apollo dashboard context is injected into market chat: Swing Trade Desk, simulation account, nightly learning, parsed news-feed ingest, and HippoRAG refresh schedule.\n"
    "- Autonomous learning cadence: 01:00 CT nightly article/research ingest, 03:15 CT bounded HippoRAG graph refresh, 16:20 CT post-close swing study.\n"
    "- Apollo Focus Universe tracks the current small stock universe chosen from nightly research and can be discussed directly in chat.\n"
    "- Theoretical trading is available as paper trades only: use focus-universe state and paper positions for discussion, not broker execution.\n"
    "- Chat commands (enabled): !web <query>, !rag <query>, !trading_homework <topic>, !focus, !learn_focus, !paper_trade <buy|sell|hold|no_trade|close> <ticker> <thesis>, !paper_positions."
)


FINANCE_QUALITY_POLICY = (
    "[FINANCE_QUALITY]\n"
    "- For current prices, rates, or live signals: web search is enabled — search it rather than saying you don't know. Acknowledge when results are web-sourced snippets vs verified data.\n"
    "- Do not give personalized buy/sell calls or tell the user what specific ticker to trade; provide a decision framework instead.\n"
    "- For day trading topics, always include risk, leverage/margin, taxes, and the U.S. pattern day trader (PDT) rule caveat.\n"
    "- If you present numbers, label them as sourced from context vs illustrative.\n"
    "- If you used web search results, treat snippets/titles as partial evidence; ask for a direct excerpt when precision matters.\n"
    "[/FINANCE_QUALITY]"
)


def _apollo_chat_preprompt(intent: str = "", depth: str = "", *, budget_focus: bool = False) -> str:
    role_summary = str(AGENT_MANIFEST.get("role_summary") or "Finance and budgeting agent.").strip()
    persona_points = _load_kb_personality_points(max_items=5)
    objective = "Maximize value per unit cost and make financial decisions explicit, measurable, and evidence-based."
    context_persona = str((AGENT_CONTEXT or {}).get("persona") or "").strip()
    if context_persona:
        for line in context_persona.splitlines():
            clean = line.strip()
            if clean.lower().startswith("objective:"):
                objective = clean.split(":", 1)[1].strip() or objective
                break
    mode_bits = []
    if intent:
        mode_bits.append(f"intent={intent}")
    if depth:
        mode_bits.append(f"depth={depth}")
    if budget_focus:
        mode_bits.append("budget_focus=on")
    mode_line = ", ".join(mode_bits) if mode_bits else "intent=general"
    persona_block = "\n".join(f"- {point}" for point in persona_points) if persona_points else (
        "- Bottom line first.\n"
        "- Use numbers and tradeoffs.\n"
        "- Stay grounded in budget, market, and risk context."
    )
    return (
        "You are Apollo.\n"
        f"Role: {role_summary}\n"
        f"Objective: {objective}\n"
        "Identity: sound like Apollo, not Sky or any other agent.\n"
        "Voice: sharp, direct, numbers-first, concrete about tradeoffs, lightly dry when appropriate.\n"
        "Behavior: lead with the bottom line, then key drivers, then the recommendation or next step.\n"
        "Use pulled budget/RAG/web evidence when available and keep continuity with prior figures in the conversation.\n"
        "Do not fabricate live data, prices, or certainty. If data is missing, say what is missing.\n"
        f"Current mode: {mode_line}\n"
        "Persona anchors:\n"
        f"{persona_block}"
    )


def _is_time_sensitive_market_question(text: str) -> bool:
    low = (text or "").lower()
    triggers = ("today", "now", "current", "latest", "this week", "pre-market", "after hours", "earnings", "breaking")
    return any(t in low for t in triggers)


_NUMERIC_RE = re.compile(r"(?<![A-Za-z])(?:\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?%)(?![A-Za-z])")


def _contains_numeric_claims(text: str) -> bool:
    return bool(_NUMERIC_RE.search(text or ""))


def _apollo_timeout_for_depth(depth: str) -> int:
    # Ollama default in common/query_client.py is 35s which is too tight for larger models on longer prompts.
    base = int(os.getenv("APOLLO_CHAT_TIMEOUT_SECS", os.getenv("OLLAMA_TIMEOUT_SECS", "120")))
    fast = int(os.getenv("APOLLO_CHAT_FAST_TIMEOUT_SECS", "45"))
    deep = int(os.getenv("APOLLO_CHAT_DEEP_TIMEOUT_SECS", "165"))
    depth_norm = (depth or "").strip().lower()
    if depth_norm == "fast":
        return fast
    if depth_norm == "deep":
        return deep
    return base


def _ollama_queue_depth() -> int:
    """
    Return the number of active connections to Ollama.
    Uses /api/ps (fast, non-blocking). Falls back to 0 on error.
    """
    try:
        r = requests.get(
            f"{os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')}/api/ps",
            timeout=2,
        )
        if r.ok:
            # Ollama /api/ps doesn't expose queue depth directly;
            # count active model slots as a proxy for busyness.
            models = r.json().get("models", [])
            return len(models)
    except Exception:
        pass
    return 0


# Reply returned when the model is saturated — avoids silent hang.
_MODEL_BUSY_REPLY = (
    "The model is currently busy processing other requests. "
    "Please try again in a moment."
)

_OLLAMA_SATURATION_PROBE_TIMEOUT = int(os.getenv("APOLLO_OLLAMA_PROBE_TIMEOUT_SECS", "6"))
_OLLAMA_MAX_QUEUE = int(os.getenv("APOLLO_OLLAMA_MAX_QUEUE", "4"))


def _apply_finance_quality_postprocess(reply: str, user_msg: str, intent: str, hits: list[dict], web_used: bool = False) -> tuple[str, dict]:
    reply = (reply or "").strip()
    low_msg = (user_msg or "").lower()
    flags = {
        "time_sensitive": _is_time_sensitive_market_question(user_msg),
        "day_trading": ("day trade" in low_msg) or ("day trading" in low_msg) or ("scalp" in low_msg),
        "numeric_claims": _contains_numeric_claims(reply),
        "rag_used": bool(hits),
        "web_used": web_used,
    }

    # Don't add finance postprocessing to system-level failure messages; keep them clean.
    failure_markers = (
        "my chat lane stalled",
        "request timed out",
        "all providers/endpoints unreachable",
        "provider error",
    )
    if any(marker in reply.lower() for marker in failure_markers):
        return reply, flags

    if flags["time_sensitive"] and not flags.get("web_used"):
        if "live market data" not in reply.lower() and "web search" not in reply.lower():
            reply = (
                "Note: I searched my knowledge base but don’t have real-time quotes for this — "
                "verify time-sensitive figures with your broker or news feed.\n\n" + reply
            )

    if flags["numeric_claims"] and not flags["rag_used"]:
        reply = (
            "If I mention any numbers below, treat them as illustrative examples unless you provided them; "
            "verify any rates/thresholds with an official source.\n\n" + reply
        )

    if flags["day_trading"]:
        safety = (
            "Day trading is high risk and often net-negative after fees/slippage; use hard risk limits, "
            "be careful with margin/leverage, and consider taxes. If you’re in the U.S., PDT rules may apply "
            "depending on account type/broker.\n"
        )
        if "Day trading is high risk" not in reply:
            reply = reply.rstrip() + "\n\n" + safety

    return reply.strip(), flags


def _truncate_line(text: str, max_chars: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _format_web_results_block(payload: dict, max_items: int = 3) -> str:
    query = str(payload.get("query") or "").strip()
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    lines = []
    if query:
        lines.append(f"Query: {query}")
    for item in results[:max_items]:
        if not isinstance(item, dict):
            continue
        title = _truncate_line(item.get("title") or "", 140)
        snippet = _truncate_line(item.get("snippet") or "", 220)
        url = _truncate_line(item.get("url") or "", 160)
        row = f"- {title} | {url}"
        if snippet:
            row += f" | {snippet}"
        lines.append(row)
    return "\n".join(lines).strip()


_MONEY_RE = re.compile(r"\\$\\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\\.[0-9]+)?)")
_PCT_RE = re.compile(r"([0-9]+(?:\\.[0-9]+)?)\\s*%")


def _parse_first_money(text: str) -> float | None:
    match = _MONEY_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except Exception:
        return None


def _parse_all_money(text: str) -> list[float]:
    values: list[float] = []
    for match in _MONEY_RE.finditer(text or ""):
        try:
            values.append(float(match.group(1).replace(",", "")))
        except Exception:
            continue
    return values


def _parse_first_pct(text: str) -> float | None:
    match = _PCT_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _looks_like_model_timeout(reply: str) -> bool:
    low = (reply or "").lower()
    return (
        "my chat lane stalled" in low
        or "request timed out" in low
        or "all providers/endpoints unreachable" in low
    )


def _fallback_personal_finance_plan(user_msg: str) -> str:
    """
    Deterministic fallback used when the LLM call times out.
    Keep it short and actionable; avoid introducing new factual claims.
    """
    low = (user_msg or "").lower()
    # Extremely lightweight heuristics for the common "net income / fixed expenses / debt" pattern.
    monies = _parse_all_money(user_msg)
    net = monies[0] if len(monies) >= 1 else None
    fixed = monies[1] if len(monies) >= 2 else None
    surplus = (net - fixed) if (net is not None and fixed is not None) else None
    apr = _parse_first_pct(user_msg)

    lines = []
    lines.append("I hit a model timeout, so here’s a short, deterministic 90‑day plan (based only on what you wrote).")
    if surplus is not None:
        lines.append(f"- Estimated monthly surplus: ~${int(round(surplus)):,} (net minus fixed).")
    if apr is not None:
        lines.append(f"- Highest-interest debt mentioned: ~{apr:.1f}% APR (treat as priority #1).")
    lines.append("")
    lines.append("90‑day plan:")
    lines.append("1) Week 1: Freeze new card spend, confirm minimums are auto-paid, list every debt balance + APR, and cut the $400/mo you identified (if real).")
    lines.append("2) Weeks 2–13: Pay the highest APR debt first (avalanche). Keep your emergency fund intact unless you’re facing missed payments/fees.")
    lines.append("3) Emergency fund target: work toward 3–6 months of essential expenses before aggressive investing. (If you’re under that, focus on debt + stability first.)")
    lines.append("4) Investing: pause new investing until the high‑APR debt is contained; then restart with small, automated contributions.")
    lines.append("")
    lines.append("Missing questions (answering these improves the plan):")
    lines.append("- Are card minimums included in fixed expenses? Any promo 0% APR windows?")
    lines.append("- Any employer 401(k) match you’d lose by pausing contributions?")
    lines.append("- Any near-term large expenses (taxes, repairs) that should raise the emergency fund floor?")
    return "\n".join(lines).strip()


def _format_rag_hits_block(payload: dict, max_items: int = 3) -> str:
    query = str(payload.get("query") or "").strip()
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    lines = []
    if query:
        lines.append(f"Query: {query}")
    for hit in results[:max_items]:
        if not isinstance(hit, dict):
            continue
        meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
        source = hit.get("source") or meta.get("source") or meta.get("url") or meta.get("path") or ""
        kind = meta.get("kind") or ""
        text = _truncate_line(hit.get("text") or "", 260)
        label = " / ".join(part for part in (str(kind).strip(), str(source).strip()) if part)
        if label:
            lines.append(f"- {text} ({label})")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines).strip()


def _is_yes_like(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    return low in {"y", "yes", "yep", "yeah", "sure", "ok", "okay", "do it", "go ahead", "please", "sounds good"}


def _is_no_like(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    return low in {"n", "no", "nope", "nah", "don't", "dont", "do not", "skip", "not now"}


def _should_offer_web_search(message: str) -> bool:
    low = (message or "").lower()
    explicit = (
        "look up" in low
        or "search online" in low
        or "google" in low
        or "find sources" in low
        or "cite sources" in low
        or "with sources" in low
    )
    return explicit or _is_time_sensitive_market_question(message)


def _auto_web_decision(st: dict, message: str) -> tuple[str | None, dict]:
    """
    Returns (query, meta). query is None when no auto web action should be taken.
    This uses Sky's action router so cooldown behavior matches the broader platform.
    """
    try:
        from dataclasses import replace
        from Sky.policy.action_router import RouterConfig, TurnContext, decide_actions
    except Exception:
        return None, {"ok": False, "reason": "action_router_unavailable"}

    tool_ctx = st.get("tool_context") if isinstance(st.get("tool_context"), dict) else {}
    last_web_attempt = tool_ctx.get("web_last_turn")
    try:
        last_web_attempt = int(last_web_attempt) if last_web_attempt is not None else None
    except Exception:
        last_web_attempt = None

    last_web_status = tool_ctx.get("web_last_status")
    if last_web_status is not None:
        last_web_status = str(last_web_status)

    turn_index = int(st.get("turn_index") or 0) + 1
    st["turn_index"] = turn_index

    web_enabled = os.getenv("SKY_WEB_ENABLED", "0") == "1"
    cfg = RouterConfig.from_env()
    # Apollo-local knob: allow auto-web decisioning without forcing global env defaults.
    cfg = replace(cfg, auto_web_enabled=True, web_enabled=web_enabled, web_keywords=list(cfg.web_keywords) + ["pdt", "finra"])
    ctx = TurnContext(
        raw_message=message,
        message=message,
        conversation_id=str(st.get("conversation_id") or ""),
        turn_index=turn_index,
        last_think_turn_index=None,
        last_web_attempt_turn_index=last_web_attempt,
        last_web_status=last_web_status,
        last_rag_meta_turn_index=None,
        think_model="",
        deep_model="",
        auto_think_env=None,
        agentic_auto_think_env=None,
        force_think=False,
        think_kind="",
        force_off=False,
        kb_available=True,
        web_enabled=web_enabled,
    )
    decision = decide_actions(ctx, cfg).to_dict()
    for action in decision.get("actions") or []:
        if isinstance(action, dict) and action.get("type") == "web_search":
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            query = str(params.get("query") or "").strip()
            if query:
                return query, {"ok": True, "decision": decision}
    return None, {"ok": True, "decision": decision}

def _looks_like_web_followup(message: str) -> bool:
    low = (message or "").lower()
    triggers = (
        "using the web",
        "web results",
        "web result",
        "sources you just pulled",
        "based on the web",
        "based on those sources",
    )
    return any(t in low for t in triggers)


def _web_followup_reply(web_last: dict) -> str:
    block = _format_web_results_block(web_last, max_items=4)
    if not block:
        return "I don’t have recent web results in this conversation. Run `!web <query>` first."
    # Deterministic summary: we can only summarize what's in the results list.
    return (
        "Here’s what I can safely say from the web results I pulled (titles/snippets only):\n"
        f"{block}\n\n"
        "If you need the *specific* rule page (e.g., PDT / margin requirements), refine the query to include `FINRA 4210` "
        "and/or your broker name, or paste the relevant excerpt here."
    ).strip()

def _dedupe(seq):
    seen = set()
    ordered = []
    for item in seq:
        if item and item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


# --- Task 02: context curation helpers ---


def _format_turns(turns: List[Dict[str, str]], limit: int = 8) -> str:
    if not turns:
        return ""
    lines = []
    for turn in turns[-limit:]:
        role = turn.get("role", "user")
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        prefix = AGENT_NAME if role == "assistant" else "User"
        lines.append(f"{prefix}: {text}")
    return "\n".join(lines)


def _recent_focus(st: Dict[str, Any], max_chars: int = 240) -> str:
    turns = st.get("turns") or []
    if not turns:
        return ""
    last_user = None
    last_assistant = None
    for turn in reversed(turns):
        role = turn.get("role")
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        if role == "user" and last_user is None:
            last_user = text
        elif role == "assistant" and last_assistant is None:
            last_assistant = text
        if last_user and last_assistant:
            break
    lines = []
    if last_user:
        lines.append(f"Last user: {last_user[:max_chars]}")
    if last_assistant:
        lines.append(f"Last {AGENT_NAME}: {last_assistant[:max_chars]}")
    return "\n".join(lines)


def _anaphora_hint(st: Dict[str, Any], user_msg: str, max_chars: int = 400) -> str:
    low = (user_msg or "").lower()
    triggers = (
        "that function",
        "that role",
        "that task",
        "that responsibility",
        "that job",
        "that approach",
        "that process",
        "that method",
        "that category",
        "first category",
        "that transaction",
        "that budget item",
    )
    if not any(t in low for t in triggers):
        return ""
    turns = st.get("turns") or []
    last_assistant = ""
    for turn in reversed(turns):
        if turn.get("role") == "assistant":
            last_assistant = (turn.get("text") or "").strip()
            if last_assistant:
                break
    if not last_assistant:
        return ""
    return (
        "User refers to the last assistant message below. Answer using it; do not ask for clarification. "
        f"Referent (last assistant message): {last_assistant[:max_chars]}"
    )


def _budget_message_focus(store, month: str, user_msg: str, session_state: Dict[str, Any] | None = None) -> str:
    """Extract category/transaction snippets directly related to the current user message."""
    low = (user_msg or "").lower()
    if not low:
        return ""

    try:
        groups = store.get_categories(month).get("groups", [])
        tx = store.get_transactions(month).get("transactions", [])
    except Exception:
        return ""

    mentioned_categories = []
    all_categories = []
    for group in groups:
        for cat in group.get("categories", []):
            all_categories.append(cat)
            name = (cat.get("name") or "").strip()
            if name and name.lower() in low:
                mentioned_categories.append(cat)

    budget_state = session_state.setdefault("budget_focus_state", {}) if isinstance(session_state, dict) else {}
    overspent = sorted(
        [cat for cat in all_categories if float(cat.get("available") or 0) < 0],
        key=lambda cat: float(cat.get("available") or 0),
    )
    if budget_state is not None:
        budget_state["last_overspent_category"] = str((overspent[0].get("name") if overspent else budget_state.get("last_overspent_category")) or "")
        if mentioned_categories:
            budget_state["last_category"] = str(mentioned_categories[0].get("name") or "")

    pronoun_budget_ref = bool(re.search(r"\b(that category|that one|first category|the first one)\b", low))
    if pronoun_budget_ref and not mentioned_categories:
        state_name = str((budget_state.get("last_category") if isinstance(budget_state, dict) else "") or "")
        if not state_name and isinstance(budget_state, dict):
            state_name = str(budget_state.get("last_overspent_category") or "")
        if state_name:
            for cat in all_categories:
                if str(cat.get("name") or "").strip().lower() == state_name.lower():
                    mentioned_categories.append(cat)
                    break

    mentioned_transactions = []
    for row in tx[:80]:
        payee = (row.get("payee") or "").strip()
        date = (row.get("date") or "").strip()
        payee_low = payee.lower()
        payee_tokens = [token for token in re.findall(r"[a-z0-9]+", payee_low) if len(token) >= 4]
        payee_match = payee_low and (payee_low in low or any(token in low for token in payee_tokens))
        date_match = bool(date and date in user_msg)
        if payee_match or date_match:
            mentioned_transactions.append(row)

    if isinstance(budget_state, dict):
        if len(mentioned_categories) >= 2:
            budget_state["compared_categories"] = [str(cat.get("name") or "") for cat in mentioned_categories[:3]]
        if mentioned_transactions:
            row = mentioned_transactions[0]
            budget_state["referenced_transaction"] = {
                "date": str(row.get("date") or ""),
                "payee": str(row.get("payee") or ""),
                "amount": float(row.get("amount") or 0),
                "category": str(row.get("category_name") or ""),
            }
        budget_state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    lines = []
    if mentioned_categories:
        lines.append(
            "Mentioned categories: "
            + "; ".join(
                f"{cat.get('name')} assigned={float(cat.get('assigned') or 0):.2f} "
                f"activity={float(cat.get('activity') or 0):.2f} "
                f"available={float(cat.get('available') or 0):.2f}"
                for cat in mentioned_categories[:4]
            )
            + "."
        )
    if mentioned_transactions:
        lines.append(
            "Mentioned transactions: "
            + "; ".join(
                f"{row.get('date')} {row.get('payee')} {float(row.get('amount') or 0):.2f} "
                f"({row.get('category_name') or 'uncategorized'})"
                for row in mentioned_transactions[:6]
            )
            + "."
        )
    if isinstance(budget_state, dict) and budget_state:
        state_category = str(budget_state.get("last_overspent_category") or "")
        compared = [str(item).strip() for item in list(budget_state.get("compared_categories") or []) if str(item).strip()]
        tx_ref = budget_state.get("referenced_transaction") if isinstance(budget_state.get("referenced_transaction"), dict) else {}
        state_bits = []
        if state_category:
            state_bits.append(f"last_overspent_category={state_category}")
        if compared:
            state_bits.append("compared_categories=" + ", ".join(compared[:3]))
        if tx_ref:
            state_bits.append(
                "referenced_transaction="
                + f"{tx_ref.get('date', '')} {tx_ref.get('payee', '')} {float(tx_ref.get('amount') or 0):.2f}"
            )
        if state_bits:
            lines.append("Budget focus state: " + " | ".join(state_bits) + ".")
    return "\n".join(lines)


def _norm_text__t02(s: str) -> str:
    # Unicode fold → ASCII, drop weird bytes
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace(" to ", "-")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(\d{1,2})[:h\.]?(\d{2})\s*[-–—]?\s*(\d{1,2})[:h\.]?(\d{2})\b", r"\1\2-\3\4", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s


def _token_jaccard(a: str, b: str) -> float:
    aw = set(a.split())
    bw = set(b.split())
    if not aw or not bw:
        return 0.0
    inter = len(aw & bw)
    union = len(aw | bw)
    return inter / union


def _shingles(s: str, n: int = 3) -> set:
    words = s.split()
    if len(words) < n:
        return set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _shingle_jaccard(a: str, b: str, n: int = 3) -> float:
    aw = _shingles(a, n)
    bw = _shingles(b, n)
    if not aw or not bw:
        return 0.0
    inter = len(aw & bw)
    union = len(aw | bw)
    return inter / union


_TIME_RANGE_RE = re.compile(r"\b(\d{1,2})[:h\.]?(\d{2})\s*[-–—]?\s*(\d{1,2})[:h\.]?(\d{2})\b")


def _time_key(raw: str) -> str | None:
    """Extract normalized time range key (e.g., '2330-0300') from text."""
    m = _TIME_RANGE_RE.search(raw)
    if not m:
        return None
    a = f"{int(m.group(1)):02d}{m.group(2)}"
    b = f"{int(m.group(3)):02d}{m.group(4)}"
    return f"{a}-{b}"


def _fix_display(s: str) -> str:
    return (
        s.replace("ΓÇô", "–")
        .replace("â€“", "–")
        .replace("â€”", "—")
        .replace("â€˜", "‘")
        .replace("â€™", "’")
        .replace("â€œ", "“")
        .replace("â€", "”")
    )


def curate_hits__t02(hits, intent: str, char_cap: int = 1500):
    def rank(meta):
        k = (meta or {}).get("kind", "")
        return {"summary": 0, "incident": 1, "schedule": 2}.get(k, 9) if intent != "schedule" else {"schedule": 0, "summary": 1, "incident": 2}.get(k, 9)

    hits_sorted = sorted(hits, key=lambda h: rank((h.get("meta") or {})))
    seen_sigs, seen_norm = set(), []
    seen_times = set()
    curated_texts = []

    for h in hits_sorted:
        meta = h.get("meta") or {}
        sig = (meta.get("extra") or {}).get("topic_signature")
        txt = (h.get("text") or "").strip()
        if not txt:
            continue

        if sig and sig in seen_sigs:
            continue

        norm = _norm_text__t02(txt)

        # General schedule time-based deduplication
        if meta.get("kind") == "schedule":
            tkey = _time_key(txt)
            if tkey and tkey in seen_times:
                continue
            if tkey:
                seen_times.add(tkey)

        if norm in seen_norm:
            continue

        dup = False
        for prev in seen_norm:
            if SequenceMatcher(None, norm, prev).ratio() >= 0.93:
                dup = True
                break
            if _token_jaccard(norm, prev) >= 0.80:
                dup = True
                break
            if _shingle_jaccard(norm, prev, 3) >= 0.70:
                dup = True
                break
        if dup:
            continue

        if sig:
            seen_sigs.add(sig)
        seen_norm.append(norm)
        curated_texts.append(txt)

    out, total = [], 0
    for t in curated_texts:
        add_len = len(t) + 2
        if total + add_len > char_cap:
            break
        out.append(t)
        total += add_len
    return out, total


# --- end Task 02 ---


def detect_local_tags(msg: str) -> dict:
    """
    Stage 1 classifier: quick keyword scan for financial intent/depth.
    """
    text = msg.lower()
    tags = []

    depth = "normal"
    for phrase in LOCAL_DEPTH_TAGS["deep"]:
        if phrase in text:
            depth = "deep"
            tags.append(phrase)
    for phrase in LOCAL_DEPTH_TAGS["fast"]:
        if phrase in text:
            if depth != "deep":
                depth = "fast"
            tags.append(phrase)

    # Financial intent detection
    intent = "unknown"
    for keyword in MARKETS_KEYWORDS:
        if keyword in text:
            intent = "markets"
            tags.append(keyword)
            break
    if intent == "unknown":
        for keyword in PERSONAL_FINANCE_KEYWORDS:
            if keyword in text:
                intent = "personal_finance"
                tags.append(keyword)
                break
    if intent == "unknown":
        for keyword in RISK_KEYWORDS:
            if keyword in text:
                intent = "risk"
                tags.append(keyword)
                break
    if intent == "unknown":
        for keyword in TAX_KEYWORDS:
            if keyword in text:
                intent = "tax"
                tags.append(keyword)
                break
    if intent == "unknown":
        for keyword in INVESTING_KEYWORDS:
            if keyword in text:
                intent = "investing"
                tags.append(keyword)
                break
    if intent == "unknown":
        for keyword in MACRO_KEYWORDS:
            if keyword in text:
                intent = "macro"
                tags.append(keyword)
                break

    return {"intent": intent, "depth": depth, "tags": _dedupe(tags)}


def classify_intent(msg: str) -> str:
    return detect_local_tags(msg)["intent"]


def _parse_classifier_json(raw: str) -> dict:
    if not isinstance(raw, str):
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = raw[start : end + 1]
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError:
        return {}
    intent = (data.get("intent") or "").lower()
    if intent not in INTENT_VALUES:
        intent = "unknown"
    data["intent"] = intent
    data["needs_deep_analysis"] = bool(data.get("needs_deep_analysis"))
    data["needs_long_reflection"] = bool(data.get("needs_long_reflection"))
    data["reflect"] = bool(data.get("reflect"))
    return data


def run_fino_classifier(message: str) -> dict:
    prompt = (
        "You are a financial classifier. Return a JSON object like:\n"
        '{"intent": "markets|personal_finance|risk|tax|investing|macro|unknown",\n'
        ' "needs_deep_analysis": true|false,\n'
        ' "needs_long_reflection": true|false,\n'
        ' "reflect": true|false}\n\n'
        f"Message:\n{message}\n"
    )
    try:
        raw = query_model(prompt)
    except Exception as exc:
        logging.warning("Fino classifier call failed: %s: %s", type(exc).__name__, exc)
        return {}
    return _parse_classifier_json(raw)


def gather_hits(intent: str, message: str, depth: str):
    # Financial intent-based retrieval tuning
    if intent == "markets":
        base_top = 8
        search_top = 12
        kinds = ["news", "report"]
    elif intent == "personal_finance":
        base_top = 6
        search_top = 10
        kinds = ["education", "report"]
    elif intent == "risk":
        base_top = 8
        search_top = 12
        kinds = ["report", "projection"]
    elif intent == "tax":
        base_top = 6
        search_top = 10
        kinds = ["regulation", "education"]
    elif intent == "investing":
        base_top = 8
        search_top = 12
        kinds = ["report", "projection", "education"]
    elif intent == "macro":
        base_top = 8
        search_top = 12
        kinds = ["news", "report", "projection"]
    else:
        base_top = 6
        search_top = 6
        kinds = None

    if depth == "fast":
        top_k = 3
        search_top = max(search_top, 5)
    elif depth == "deep":
        top_k = max(8, base_top)
        search_top = max(search_top, top_k + 2)
    else:
        top_k = base_top

    # User profile hits — always float to top (priority 0.85-0.95)
    profile_hits = []
    if _MEMORY_STORE is not None:
        try:
            profile_hits = _MEMORY_STORE.get_user_context_hits(message, top_k=4)
        except Exception:
            pass

    # Operational memory (session notes, shared facts from prior turns)
    res = APOLLO_RAG.search(query=message, top_k=search_top, kinds=kinds)
    mem_hits = res.get("results", [])

    # Financial education corpus — query by semantic similarity only, no kind filter.
    corpus_hits = _query_financial_corpus(message, top_k=search_top, kinds=None)

    # Merge: profile first, then dedup corpus + memory by leading 120 chars
    seen: dict[str, dict] = {}
    for h in profile_hits + mem_hits + corpus_hits:
        key = (h.get("text") or "")[:120].strip()
        if not key:
            continue
        if key not in seen or (h.get("score") or 0) > (seen[key].get("score") or 0):
            seen[key] = h
    hits = sorted(seen.values(), key=lambda h: h.get("score") or 0, reverse=True)

    # Filter by kind for specific financial intents (corpus hits are unfiltered — OK)
    if intent == "regulation":
        hits = [h for h in hits if (h.get("meta") or {}).get("kind") == "regulation"]

    return hits[:top_k], top_k


def log_trace(
    intent: str,
    depth: str,
    message: str,
    rag_hits: int,
    deep_used: bool,
    latency_ms: float,
    chain: str,
    local_tags: list,
    gemma_classifier_used: bool,
    gemma_classifier_output: dict,
    context_chars: int,
    dedupe_removed: int,
    quality_flags: dict | None = None,
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "intent": intent,
        "depth": depth,
        "local_tags": local_tags,
        "gemma_classifier_used": gemma_classifier_used,
        "gemma_classifier_output": gemma_classifier_output,
        "message": message,
        "rag_hits": rag_hits,
        "gx10_used": deep_used,
        "latency_ms": round(latency_ms, 2),
        "chain": chain,
        "context_chars": context_chars,
        "dedupe_removed": dedupe_removed,
    }
    if quality_flags:
        record["quality_flags"] = dict(quality_flags)
    with open(TRACE_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _can_snapshot() -> bool:
    return (time.time() - LAST_ACTIVITY_TS) >= SNAPSHOT_GUARD_SECONDS


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _chunk_text(text: str, size: int = 28):
    if not text:
        return []
    return [text[i:i + size] for i in range(0, len(text), size)]


def _is_deep_health_request() -> bool:
    return str(request.args.get("deep") or "").strip().lower() in {"1", "true", "yes", "on"}


def _fast_health_payload() -> Dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    corpus_status = dict(_FINANCIAL_CORPUS_STATUS)
    return {
        "status": "ok",
        "app": AGENT_NAME.lower(),
        "agent": AGENT_NAME,
        "ts": ts,
        "startup_phase": "ready",
        "financial_corpus_ready": bool(corpus_status.get("financial_corpus_ready")),
        "collection_exists": bool(corpus_status.get("collection_exists")),
        "doc_count": int(corpus_status.get("doc_count") or 0),
        "real_chunk_count": int(corpus_status.get("real_chunk_count") or 0),
        "synthetic_chunk_count": int(corpus_status.get("synthetic_chunk_count") or 0),
        "real_ratio": float(corpus_status.get("real_ratio") or 0.0),
        "bootstrap_state": str(corpus_status.get("bootstrap_state") or ""),
        # Compatibility keys for existing smoke tests and dashboards. Full GPU/dependency
        # probing is intentionally kept behind /health?deep=1 so watchdog checks stay fast.
        "ui_chat_gpu_backed": None,
        "ui_chat_primary_healthy": None,
        "embedding_gpu_attached": None,
        "deep_health_available": True,
        "deep_health_url": "/health?deep=1",
    }


@app.before_request
def _track_activity():
    global LAST_ACTIVITY_TS
    if request.path == "/health" and not _is_deep_health_request():
        return jsonify(_fast_health_payload())
    if request.endpoint not in {"rag_snapshot", "rag_restore"}:
        LAST_ACTIVITY_TS = time.time()


@app.route("/")
def home():
    override_rev = os.getenv("ASSET_REV", "").strip()
    try:
        mtime_rev = str(int((Path(__file__).resolve().parent / "static" / "apollo_ui.css").stat().st_mtime))
    except Exception:
        mtime_rev = str(int(time.time()))

    runtime_build = ""
    try:
        runtime_build = str(getattr(AGENT_RUNTIME, "build_id", "") or AGENT_RUNTIME.get("build_id", "")).strip()
    except Exception:
        runtime_build = ""

    if override_rev:
        asset_rev = override_rev
    elif runtime_build:
        # Include file mtime to force cache refresh when UI assets change.
        asset_rev = f"{runtime_build}-{mtime_rev}"
    else:
        asset_rev = mtime_rev
    visible_build_seq = runtime_build or "n/a"
    asset_last_edited_display = "unknown"
    try:
        css_mtime = (Path(__file__).resolve().parent / "static" / "apollo_ui.css").stat().st_mtime
        asset_last_edited_display = datetime.fromtimestamp(css_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    static_root = Path(__file__).resolve().parent / "static"

    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _asset_candidates(filename: str) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        suffix = f"?v={asset_rev}" if asset_rev else ""

        def _add(raw: str) -> None:
            if not raw:
                return
            value = raw
            if suffix and "?" not in value:
                value = f"{value}{suffix}"
            if value in seen:
                return
            seen.add(value)
            out.append(value)

        try:
            _add(url_for("static", filename=filename))
        except Exception:
            pass

        _add(f"/static/{filename}")

        forwarded_prefixes = [
            (request.headers.get("X-Forwarded-Prefix") or "").strip(),
            (request.headers.get("X-Script-Name") or "").strip(),
        ]
        for prefix in forwarded_prefixes:
            if prefix:
                clean = "/" + prefix.strip("/")
                _add(f"{clean}/static/{filename}")

        first_segment = request.path.strip("/").split("/")[0] if request.path.strip("/") else ""
        if first_segment and first_segment not in {"static", "health", "meta", "chat", "admin", "budget", "rag", "mcp"}:
            _add(f"/{first_segment}/static/{filename}")

        _add(f"./static/{filename}")
        return out

    inline_css = "\n".join(
        part for part in (_read_text(static_root / "style.css"), _read_text(static_root / "apollo_ui.css")) if part
    )
    inline_js = _read_text(static_root / "apollo_ui.js")
    app_base_path = str(request.script_root or "").rstrip("/")
    if not app_base_path:
        first_segment = request.path.strip("/").split("/")[0] if request.path.strip("/") else ""
        if first_segment and first_segment not in {"static", "health", "meta", "chat", "admin", "budget", "rag", "mcp"}:
            app_base_path = f"/{first_segment}"

    return render_template(
        "index.html",
        agent=AGENT_NAME,
        app_base_path=app_base_path,
        local_token=(os.getenv("APOLLO_LOCAL_TOKEN", "") or os.getenv("SKY_LOCAL_TOKEN", "")),
        asset_rev=asset_rev,
        visible_build_seq=visible_build_seq,
        asset_last_edited_display=asset_last_edited_display,
        style_candidates=_asset_candidates("style.css"),
        ui_style_candidates=_asset_candidates("apollo_ui.css"),
        inline_css=inline_css,
        inline_js=inline_js,
    )


@app.route("/health")
def health():
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    corpus_status = dict(_FINANCIAL_CORPUS_STATUS)
    if _is_deep_health_request():
        return jsonify(build_health_payload(AGENT_NAME, {"status": "ok", "ts": ts, **corpus_status}))
    return jsonify(_fast_health_payload())


@app.route("/meta")
def meta():
    chat_base_url = _apollo_chat_base_url()
    chat_model = _apollo_chat_model()
    deep_base_url = _apollo_deep_base_url()
    deep_model = _apollo_deep_model()
    ollama_ok, ollama_version = False, None
    try:
        r = requests.get(f"{chat_base_url}/api/version", timeout=2)
        if r.ok:
            ollama_ok = True
            ollama_version = r.json().get("version")
    except Exception:
        pass

    return jsonify(
        {
            "agent": AGENT_NAME,
            "provider": LLM_PROVIDER,
            "ollama": {"url": chat_base_url, "model": chat_model, "up": ollama_ok, "version": ollama_version},
            "chat_lane": {"provider": "ollama", "url": chat_base_url, "model": chat_model, "keep_alive": _apollo_chat_keep_alive()},
            "deep_lane": {"provider": "ollama", "url": deep_base_url, "model": deep_model},
            "owui": {"url": OWUI_URL, "model": OWUI_MODEL},
            "hipporag": {
                "enabled": bool(_HIPPO_RETRIEVER is not None),
                "kg_path": str(os.path.join(os.path.abspath(os.getenv("RAG_DIR", str(_APOLLO_ROOT / "chroma_db"))), "financial_kg.json")),
                "kg_stats": (_HIPPO_RETRIEVER.kg.stats() if _HIPPO_RETRIEVER is not None else None),
                "retrieval_path": "hippo_first_fallback_chroma",
                "graph_endpoint": "/admin/hipporag/graph",
            },
            "ocr_policy": {
                "gb10_primary": os.getenv("APOLLO_GB10_OCR_ENABLED", "1") == "1",
                "primary_route": "gb10_auto",
                "layout_model": "gb10_paddleocr_vl",
                "dense_scan_model": "gb10_got_ocr2",
                "cleanup_model": os.getenv("APOLLO_GB10_QWEN_OCR_MODEL", os.getenv("VISION_QWEN25VL_MODEL", "qwen2.5vl:7b")),
                "fallback_cleanup_model": os.getenv("APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL", os.getenv("VISION_QWEN3VL_MODEL", "qwen3-vl:32b")),
                "local_fallbacks": ["rapidocr", "tesseract"],
            },
            "rag_governance": {
                "rag_proposals_enabled": False,
                "auto_changes_allowed": False,
                "requires_confirmation": True,
            },
            "homework": {
                "trading_weekly": True,
                "endpoint": "/admin/trading_homework/run",
            },
            "nightly_pipeline": {
                "enabled": True,
                "run_endpoint": "/admin/nightly_pipeline/run",
                "status_endpoint": "/admin/nightly_pipeline/status",
                "schedule_cli": "python -m Apollo.nightly_pipeline schedule",
            },
            "background_pipeline": {
                "enabled": True,
                "run_endpoint": "/admin/background_pipeline/run",
                "status_endpoint": "/admin/background_pipeline/status",
                "history_endpoint": "/admin/background_pipeline/history",
                "scorecard_endpoint": "/admin/background_pipeline/scorecard",
                "rag_sample_endpoint": "/admin/background_pipeline/rag_sample",
                "schedule_endpoint": "/admin/background_pipeline/schedule",
                "schedule_cli": "python -m Apollo.nightly_pipeline background-schedule",
            },
            "focus_universe": {
                "enabled": True,
                "status_endpoint": "/admin/focus_universe/status",
                "learn_endpoint": "/admin/focus_universe/learn",
                "paper_trade_endpoint": "/admin/focus_universe/paper_trade",
                "events_endpoint": "/admin/focus_universe/events",
            },
            "swing_study": {
                "enabled": True,
                "latest_endpoint": "/admin/swing/latest",
                "run_endpoint": "/admin/swing/run",
                "history_endpoint": "/admin/swing/history",
                "outcomes_endpoint": "/admin/swing/outcomes",
                "provider_health_endpoint": "/admin/swing/provider_health",
                "recompute_outcomes_endpoint": "/admin/swing/recompute_outcomes",
                "proposal_decision_endpoint": "/admin/swing/proposal_decision",
                "schedule_cli": "python -m Apollo.swing_study run",
            },
            "event_impact": {
                "enabled": os.getenv("APOLLO_EVENT_IMPACT_ENABLED", "0") == "1",
                "latest_endpoint": "/admin/event_impact/latest",
                "run_endpoint": "/admin/event_impact/run",
                "history_endpoint": "/admin/event_impact/history",
                "health_endpoint": "/admin/event_impact/health",
                "mcp_tools": [
                    "apollo.event_impact",
                    "apollo.event_swing_study",
                    "apollo.event_watchlist",
                    "apollo.event_impact_health",
                    "apollo.market_forecast_turn",
                    "apollo.market_forecast_health",
                ],
                "broker_prep_only": True,
                "live_trade_execution": False,
            },
            "market_forecast_turn": {
                "enabled": os.getenv("APOLLO_MARKET_FORECAST_ENABLED", "1") == "1",
                "run_endpoint": "/admin/market_forecast_turn/run",
                "latest_endpoint": "/admin/market_forecast_turn/latest",
                "health_endpoint": "/admin/market_forecast_turn/health",
                "ocr97_mode": "only_when_ocr_documents_are_supplied",
                "live_trade_execution": False,
            },
            "background_runtime": load_apollo_background_runtime(),
        }
    )


@app.route("/chat", methods=["POST"])
def chat():
    start = time.perf_counter()
    data = request.get_json(force=True) or {}
    user_msg = ""
    for key in ("message", "prompt", "query"):
        value = data.get(key)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                user_msg = candidate
                break
    if not user_msg:
        return jsonify({"reasoning": "(none)", "reply": "Say something first."})
    apollo_prefix_fast = False
    apollo_prefix_deep = False
    try:
        from Sky.llm.thinker import detect_think_prefix

        _ft, stripped_msg, _think_kind, think_force_off = detect_think_prefix(user_msg)
        if think_force_off and not _ft:
            user_msg = stripped_msg
            apollo_prefix_fast = True
        elif _ft:
            user_msg = stripped_msg
            apollo_prefix_deep = True
    except ImportError:
        pass
    user_msg_display = user_msg
    cid = data.get("conversation_id") or "default"
    st = _state(cid)
    st["conversation_id"] = cid
    turns = st.setdefault("turns", [])
    update_code_phrase_state(st, user_msg)
    if is_rag_improvement_setup_prompt(user_msg):
        setup_reply = rag_improvement_setup_reply(AGENT_NAME)
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": setup_reply})
        st["last_tool_result"] = {"kind": "rag_improvement_setup", "topic": "rag_improvement_setup"}
        st["last_topic"] = "rag_improvement_setup"
        return jsonify({"reasoning": "(rag-improvement-setup)", "reply": setup_reply, "intent": "rag_improvement_setup", "depth": "fast"}), 200
    policy_reply = rag_ingestion_policy_reply_from_state(AGENT_NAME, user_msg, st)
    if policy_reply:
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": policy_reply})
        st["last_tool_result"] = {"kind": "rag_ingest_policy", "topic": "rag_ingest_policy"}
        st["last_topic"] = "rag_ingest_policy"
        return jsonify({"reasoning": "(rag-ingest-policy)", "reply": policy_reply, "intent": "rag_ingest_policy", "depth": "fast"}), 200
    history_text = ""
    requested_model = str(data.get("model") or data.get("llm_model") or data.get("model_override") or "").strip()

    # Phase 3: fast-path simple conversational/evaluation turns before the heavy pipeline.
    # Catches role, persona, code-phrase, two-sentence evaluation, and metadata turns.
    # Must run before background_command / student_loan / focus / proof checks.
    if not apollo_prefix_deep:
        try:
            from common.dialog_router import classify_message
            _dr_pre = classify_message(user_msg, force_bucket="dialog" if apollo_prefix_fast else None)
            if _dr_pre.bucket == "dialog":
                turns = st.setdefault("turns", [])
                _hist = _format_turns(turns, limit=6) or "(none)"
                _sys_pre = _apollo_chat_preprompt("conversation", "fast", budget_focus=False)
                _fast_prompt_pre = f"[RECENT CONVERSATION]\n{_hist}\n\nUser: {user_msg}\nApollo:"
                from common.query_client import query_model_with_meta
                _meta_pre = query_model_with_meta(
                    _fast_prompt_pre,
                    system_prompt=_sys_pre,
                    model=requested_model or _apollo_chat_model() or _dr_pre.model,
                    force_ui_chat=False,
                    base_url=_apollo_chat_base_url(),
                    task_type="qa",
                    timeout_sec=int(os.getenv("APOLLO_DIALOG_TIMEOUT_SECS", "25")),
                    retries=0,
                )
                _reply_pre = str(_meta_pre.get("text") or "")
                turns.append({"role": "user", "text": user_msg})
                turns.append({"role": "assistant", "text": _reply_pre})
                return jsonify({"reasoning": f"(dialog-pre:{_dr_pre.reason})", "reply": _reply_pre, "intent": "qa", "depth": "fast"}), 200
        except Exception:
            pass

    background_command = detect_apollo_background_command(
        user_msg,
        last_topic=str(st.get("last_topic") or ""),
        last_tool_kind=str((st.get("last_tool_result") or {}).get("kind") or ""),
    )
    if _apollo_prompt_proof_kind(user_msg):
        background_command = ""

    if background_command:
        try:
            if background_command == "run":
                result = {"ok": True, "status": apollo_nightly_pipeline.run_background_pipeline_step({})}
                action_line = "Apollo background pipeline step requested."
            elif background_command == "pause":
                apollo_nightly_pipeline.set_pipeline_paused(True)
                result = {"ok": True, "status": apollo_nightly_pipeline.mark_background_control_state("paused")}
                action_line = "Apollo background pipeline paused."
            elif background_command == "resume":
                apollo_nightly_pipeline.set_pipeline_paused(False)
                apollo_nightly_pipeline.set_pipeline_stopped(False)
                result = {"ok": True, "status": apollo_nightly_pipeline.mark_background_control_state("")}
                action_line = "Apollo background pipeline resumed."
            else:
                apollo_nightly_pipeline.set_pipeline_stopped(True)
                result = {"ok": True, "status": apollo_nightly_pipeline.mark_background_control_state("stopped")}
                action_line = "Apollo background pipeline stop requested."
            runtime = extract_apollo_background_runtime(result)
            reply = action_line + "\n" + format_apollo_background_runtime(runtime, label="Apollo")
        except Exception as exc:
            reply = f"Apollo background pipeline {background_command} failed: {type(exc).__name__}: {exc}"
            result = {"ok": False, "error": reply}
        st["last_tool_result"] = {"kind": "apollo_background_runtime", "topic": "apollo_background_runtime", **result}
        st["last_topic"] = "apollo_background_runtime"
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": f"(apollo-background-{background_command})", "reply": reply, "intent": "ops", "depth": "fast"}), 200

    if is_apollo_background_status_question(
        user_msg,
        last_topic=str(st.get("last_topic") or ""),
        last_tool_kind=str((st.get("last_tool_result") or {}).get("kind") or ""),
    ):
        runtime = load_apollo_background_runtime()
        reply = format_apollo_background_runtime(runtime, label="Apollo")
        st["last_tool_result"] = {"kind": "apollo_background_runtime", "topic": "apollo_background_runtime", "status": runtime}
        st["last_topic"] = "apollo_background_runtime"
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": "(apollo-background-runtime)", "reply": reply, "intent": "ops", "depth": "fast"}), 200

    if _is_focus_question(user_msg):
        state = _sync_focus_state()
        reply = apollo_focus_trading.format_focus_summary(state)
        st["last_tool_result"] = {"kind": "apollo_focus_universe", "status": state}
        st["last_topic"] = "apollo_focus_universe"
        st.setdefault("tool_context", {})["focus_last"] = state
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": "(apollo-focus-universe)", "reply": reply, "intent": "markets", "depth": "fast"}), 200

    if _is_focus_learn_request(user_msg):
        state = _sync_focus_state(note="manual_chat_learn")
        reply = "Apollo refreshed its learned focus state.\n" + apollo_focus_trading.format_focus_summary(state)
        st["last_tool_result"] = {"kind": "apollo_focus_learn", "status": state}
        st["last_topic"] = "apollo_focus_learn"
        st.setdefault("tool_context", {})["focus_last"] = state
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": "(apollo-focus-learn)", "reply": reply, "intent": "markets", "depth": "fast"}), 200

    loan_review = apollo_student_loan_review.handle_chat_message(str(st.get("conversation_id") or cid), user_msg)
    if loan_review is not None:
        payload = _loan_review_payload_with_artifacts(loan_review)
        reply = str(payload.get("reply") or "Apollo student-loan review updated.")
        st["last_tool_result"] = {"kind": "apollo_student_loan_review", **payload}
        st["last_topic"] = "apollo_student_loan_review"
        st.setdefault("tool_context", {})["student_loan_review"] = payload.get("session") or {}
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify(
            {
                "reasoning": "(apollo-student-loan-review)",
                "reply": reply,
                "intent": "personal_finance",
                "depth": "fast",
                "loan_review": payload,
            }
        ), 200

    proof_kind = _apollo_prompt_proof_kind(user_msg)
    if proof_kind and os.getenv("APOLLO_ALLOW_CHAT_PROOFS", "1") == "1":
        try:
            proof_kind, result = _run_apollo_prompt_proof(user_msg)
            reply = _format_apollo_prompt_proof_reply(proof_kind, result)
            st["last_tool_result"] = {"kind": f"apollo_{proof_kind}", "result": result}
            st["last_topic"] = f"apollo_{proof_kind}"
            st.setdefault("tool_context", {})["apollo_prompt_proof_last"] = result
            turns.append({"role": "user", "text": user_msg})
            turns.append({"role": "assistant", "text": reply})
            return jsonify({"reasoning": f"(apollo-{proof_kind})", "reply": reply, "intent": "markets", "depth": "fast", "result": result}), 200
        except Exception as exc:
            logging.exception("apollo prompt proof failed")
            reply = f"Apollo proof failed: {type(exc).__name__}: {exc}"
            return jsonify({"reasoning": "(apollo-proof-failed)", "reply": reply, "intent": "markets", "depth": "fast"}), 500

    paper_trade = _parse_paper_trade_request(user_msg)
    if paper_trade:
        result = apollo_focus_trading.submit_paper_trade(
            action=paper_trade.get("action") or "",
            ticker=paper_trade.get("ticker") or "",
            thesis=paper_trade.get("thesis") or "",
            confidence=float(paper_trade.get("confidence") or 0.0),
            horizon=paper_trade.get("horizon") or "",
            rag=APOLLO_RAG,
            memory_store=_MEMORY_STORE,
        )
        if not result.get("ok"):
            reply = f"Paper trade rejected: {result.get('error')}"
        else:
            reply = "Paper trade recorded.\n" + apollo_focus_trading.format_focus_summary(apollo_focus_trading.load_state())
        st["last_tool_result"] = {"kind": "apollo_paper_trade", "result": result}
        st["last_topic"] = "apollo_paper_trade"
        st.setdefault("tool_context", {})["paper_trade_last"] = result
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": "(apollo-paper-trade)", "reply": reply, "intent": "markets", "depth": "fast", "result": result}), 200

    if _is_paper_positions_question(user_msg):
        state = apollo_focus_trading.load_state()
        reply = apollo_focus_trading.format_recent_events(state, limit=5) + "\n\n" + apollo_focus_trading.format_focus_summary(state)
        st["last_tool_result"] = {"kind": "apollo_paper_positions", "status": state}
        st["last_topic"] = "apollo_paper_positions"
        st.setdefault("tool_context", {})["focus_last"] = state
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": "(apollo-paper-positions)", "reply": reply, "intent": "markets", "depth": "fast"}), 200

    # Pending tool confirmation (so user doesn't need literal !web triggers).
    pending = st.get("pending_tool") if isinstance(st.get("pending_tool"), dict) else None
    if pending and pending.get("type") == "web_search":
        if _is_yes_like(user_msg):
            user_msg_display = user_msg
            user_msg = str(pending.get("original_message") or "").strip() or user_msg
            st["auto_web_confirmed"] = True
            try:
                from common import mcp
                mcp.ensure_loaded()
                res = mcp.run("web.search", {"query": str(pending.get("query") or "").strip()})
                payload = {
                    "query": str(pending.get("query") or "").strip(),
                    "ok": bool((res or {}).get("ok")) if isinstance(res, dict) else False,
                    "results": (res or {}).get("results", []) if isinstance(res, dict) else [],
                    "meta": (res or {}).get("meta", {}) if isinstance(res, dict) else {},
                }
                st.setdefault("tool_context", {})["web_last"] = payload
                st.setdefault("tool_context", {})["web_last_turn"] = int(st.get("turn_index") or 0)
                st.setdefault("tool_context", {})["web_last_status"] = (res or {}).get("status") if isinstance(res, dict) else ""
            except Exception as exc:
                st.setdefault("tool_context", {})["web_last_error"] = f"{type(exc).__name__}:{exc}"
            st["pending_tool"] = None
        elif _is_no_like(user_msg):
            user_msg_display = user_msg
            user_msg = str(pending.get("original_message") or "").strip() or user_msg
            st["pending_tool"] = None

    # Explicit command: trigger weekly trading homework (heavy; off by default).
    if user_msg.lower().startswith(("!trading_homework", "!trading-homework", "!homework")):
        if os.getenv("APOLLO_ALLOW_CHAT_HOMEWORK", "0") != "1":
            msg = (
                "Trading homework is available, but chat-triggering is disabled on this server.\n"
                "Run it via POST /admin/trading_homework/run or CLI: python -m Apollo.trading_homework\n"
                "Tip: enable web search with SKY_WEB_ENABLED=1, or drop PDFs into Apollo/logs/ocr_uploads/trading_homework_inbox."
            )
            return jsonify({"reasoning": "(homework:disabled)", "reply": msg, "intent": "admin", "depth": "normal"})
        topic = user_msg.split(" ", 1)[1].strip() if " " in user_msg else "one discretionary stock trade decision per day with overnight holding allowed"
        try:
            result = apollo_trading_homework.trading_homework_run({"topic": topic, "max_articles": 3, "ingest": True}, rag=APOLLO_RAG)
            summary = (
                f"Trading homework complete. overall={((result.get('grade') or {}).get('overall'))} "
                f"report={result.get('report_path')}"
            )
            return jsonify({"reasoning": "(homework:ran)", "reply": summary, "result": result, "intent": "admin", "depth": "normal"})
        except Exception as exc:
            return jsonify({"reasoning": "(homework:failed)", "reply": f"Homework failed: {type(exc).__name__}: {exc}"}), 500

    # Tooling commands (off by default).
    nl_web_query = _explicit_web_query(user_msg)
    nl_rag_query = _explicit_rag_query(user_msg)
    if user_msg.lower().startswith(("!web ", "!rag ")) or nl_web_query or nl_rag_query:
        if os.getenv("APOLLO_ALLOW_CHAT_TOOLS", "0") != "1":
            msg = (
                "Chat tool commands are disabled on this server.\n"
                "Enable with APOLLO_ALLOW_CHAT_TOOLS=1.\n"
                "Web search also requires SKY_WEB_ENABLED=1.\n"
                "Available: !web <query>, !rag <query>, or 'web search: <query>' / 'rag search: <query>'."
            )
            return jsonify({"reasoning": "(tools:disabled)", "reply": msg, "intent": "admin", "depth": "fast"})

        try:
            from common import mcp
        except Exception:
            mcp = None

        if user_msg.lower().startswith("!web ") or nl_web_query:
            query = nl_web_query or user_msg[5:].strip()
            if not query:
                return jsonify({"reasoning": "(web:empty)", "reply": "Usage: !web <query>"}), 200
            if mcp is None:
                return jsonify({"reasoning": "(web:unavailable)", "reply": "MCP unavailable in this runtime."}), 503
            try:
                mcp.ensure_loaded()
                refined = query
                qlow = query.lower()
                if ("pattern day trader" in qlow or "pdt" in qlow) and ("finra" not in qlow and "sec" not in qlow):
                    refined = "FINRA pattern day trader rule margin requirements"
                res = mcp.run("web.search", {"query": refined})
            except Exception as exc:
                return jsonify({"reasoning": "(web:error)", "reply": f"web.search failed: {type(exc).__name__}: {exc}"}), 500
            # If results are clearly off-topic, try one more refinement.
            try:
                urls = [str((item or {}).get("url") or "").lower() for item in (res or {}).get("results", []) if isinstance(item, dict)]
            except Exception:
                urls = []
            if ("pattern day trader" in qlow or "pdt" in qlow) and urls and not any(("finra" in u or "sec" in u or "investor.gov" in u) for u in urls):
                try:
                    res = mcp.run("web.search", {"query": "FINRA pattern day trader rule margin requirements"})
                except Exception:
                    pass
            payload = {
                "query": refined,
                "ok": bool((res or {}).get("ok")) if isinstance(res, dict) else False,
                "results": (res or {}).get("results", []) if isinstance(res, dict) else [],
                "meta": (res or {}).get("meta", {}) if isinstance(res, dict) else {},
            }
            st.setdefault("tool_context", {})["web_last"] = payload
            reply = "[WEB_RESULTS]\n" + (_format_web_results_block(payload) or "(no results)") + "\n[/WEB_RESULTS]"
            turns.append({"role": "user", "text": user_msg})
            turns.append({"role": "assistant", "text": reply})
            return jsonify({"reasoning": "(web:ok)", "reply": reply, "intent": "tools", "depth": "fast", "tool": payload}), 200

        if user_msg.lower().startswith("!rag ") or nl_rag_query:
            query = nl_rag_query or user_msg[5:].strip()
            if not query:
                return jsonify({"reasoning": "(rag:empty)", "reply": "Usage: !rag <query>"}), 200
            try:
                data = _rag_dual_search(query, top_k=5)
            except Exception as exc:
                return jsonify({"reasoning": "(rag:error)", "reply": f"rag.search failed: {type(exc).__name__}: {exc}"}), 500
            payload = {"query": query, "results": data}
            st.setdefault("tool_context", {})["rag_last"] = payload
            reply = "[RAG_HITS]\n" + (_format_rag_hits_block(payload) or "(no hits)") + "\n[/RAG_HITS]"
            turns.append({"role": "user", "text": user_msg})
            turns.append({"role": "assistant", "text": reply})
            return jsonify({"reasoning": "(rag:ok)", "reply": reply, "intent": "tools", "depth": "fast", "tool": payload}), 200

    # Auto web search: decide from context; ask once, don't do it constantly.
    auto_tools_enabled = os.getenv("APOLLO_ALLOW_CHAT_TOOLS", "0") == "1"
    auto_web_mode = os.getenv("APOLLO_AUTO_WEB_MODE", "ask").strip().lower()
    web_enabled = os.getenv("SKY_WEB_ENABLED", "0") == "1"
    if auto_tools_enabled and web_enabled and st.get("pending_tool") is None:
        query, meta = _auto_web_decision(st, user_msg)
        if query and (_should_offer_web_search(user_msg) or meta.get("ok")):
            tool_ctx = st.setdefault("tool_context", {})
            last_ask = tool_ctx.get("web_last_ask_turn")
            try:
                last_ask = int(last_ask) if last_ask is not None else None
            except Exception:
                last_ask = None
            turn_index = int(st.get("turn_index") or 0)
            ask_cooldown = int(os.getenv("APOLLO_AUTO_WEB_ASK_COOLDOWN_TURNS", "6"))
            should_ask = (auto_web_mode != "auto") and not bool(st.get("auto_web_confirmed"))
            if should_ask and (last_ask is None or (turn_index - last_ask) >= ask_cooldown):
                st["pending_tool"] = {"type": "web_search", "query": query, "original_message": user_msg}
                tool_ctx["web_last_ask_turn"] = turn_index
                reply = f"I can do a quick web search to confirm this. Search query: `{query}`. Want me to run it?"
                turns.append({"role": "user", "text": user_msg_display})
                turns.append({"role": "assistant", "text": reply})
                return jsonify({"reasoning": "(web:ask)", "reply": reply, "intent": "tools", "depth": "fast"}), 200
            if auto_web_mode == "auto" or bool(st.get("auto_web_confirmed")):
                try:
                    from common import mcp
                    mcp.ensure_loaded()
                    res = mcp.run("web.search", {"query": query})
                    payload = {
                        "query": query,
                        "ok": bool((res or {}).get("ok")) if isinstance(res, dict) else False,
                        "results": (res or {}).get("results", []) if isinstance(res, dict) else [],
                        "meta": (res or {}).get("meta", {}) if isinstance(res, dict) else {},
                    }
                    tool_ctx["web_last"] = payload
                    tool_ctx["web_last_turn"] = int(st.get("turn_index") or 0)
                    tool_ctx["web_last_status"] = (res or {}).get("status") if isinstance(res, dict) else ""
                except Exception as exc:
                    tool_ctx["web_last_error"] = f"{type(exc).__name__}:{exc}"

    # Deterministic web follow-up: avoid long LLM calls when the user is asking to summarize the last web search.
    tool_ctx = st.get("tool_context") if isinstance(st.get("tool_context"), dict) else {}
    web_last = tool_ctx.get("web_last") if isinstance(tool_ctx.get("web_last"), dict) else None
    if web_last and _looks_like_web_followup(user_msg):
        reply = _web_followup_reply(web_last)
        turns.append({"role": "user", "text": user_msg})
        turns.append({"role": "assistant", "text": reply})
        return jsonify({"reasoning": "(web:followup_fast)", "reply": reply, "intent": "tools", "depth": "fast"}), 200

    # --- Dialog router: fast-path for simple conversational turns ---
    try:
        from common.dialog_router import classify_message
        _dr_force = "dialog" if apollo_prefix_fast else None
        _dr = classify_message(user_msg, force_bucket=_dr_force)
        if _dr.bucket == "dialog":
            _hist = _format_turns(turns, limit=6) or "(none)"
            _dialog_system_prompt = _apollo_chat_preprompt("conversation", "fast", budget_focus=False)
            _fast_prompt = (
                f"[RECENT CONVERSATION]\n{_hist}\n\n"
                f"User: {user_msg}\nApollo:"
            )
            from common.query_client import query_model_with_meta
            _meta = query_model_with_meta(
                _fast_prompt,
                system_prompt=_dialog_system_prompt,
                model=requested_model or _apollo_chat_model() or _dr.model,
                force_ui_chat=False,
                base_url=_apollo_chat_base_url(),
                task_type="qa",
                timeout_sec=int(os.getenv("APOLLO_DIALOG_TIMEOUT_SECS", "60")),
                retries=1,
            )
            _reply = str(_meta.get("text") or "")
            turns.append({"role": "user", "text": user_msg})
            turns.append({"role": "assistant", "text": _reply})
            return jsonify({"reasoning": f"(dialog:{_dr.reason})", "reply": _reply, "intent": "qa", "depth": "fast"})
        if _dr.bucket == "deep":
            apollo_prefix_deep = True
    except Exception:
        pass
    congress_ctx = _build_congress_context(user_msg)
    if congress_ctx:
        user_msg = f"{congress_ctx}\n\nUser request: {user_msg}"
        st["congress_context_used"] = True
    # Extract and persist financial facts from the user message (async, non-blocking)
    if _MEMORY_STORE is not None:
        try:
            raw_msg = str(request.get_json(silent=True, force=True) or {}).get("message", user_msg)
            if _mem_extractor.has_financial_facts(user_msg):
                facts = _mem_extractor.extract_facts(user_msg)
                for field, value in facts.items():
                    if field in ("debts", "goals", "assets"):
                        existing = _MEMORY_STORE.get_profile_field(field) or []
                        merged = (existing if isinstance(existing, list) else [existing]) + (
                            value if isinstance(value, list) else [value]
                        )
                        _MEMORY_STORE.set_profile_field(field, merged)
                    else:
                        _MEMORY_STORE.set_profile_field(field, value)
                if facts:
                    logging.debug("[memory] extracted facts: %s", facts)
        except Exception:
            pass

    if is_session_note_request(user_msg):
        apply_session_note(st, user_msg)
        return jsonify({"reasoning": "(session-note)", "reply": "Got it — I'll keep that in mind for this chat."})
    if is_recall_question(user_msg):
        recall_text = recall_reply(st, user_msg)
        if recall_text:
            return jsonify({"reasoning": "(recall)", "reply": recall_text})
    if is_congress_status_question(user_msg):
        try:
            status = fetch_cycle_status()
            reply_text = format_cycle_status_reply(status, agent_name=AGENT_NAME)
        except Exception as exc:
            reply_text = f"Unable to read congress status ({exc})."
        _record_turn(st, user_msg, reply_text)
        return _respond({"reasoning": "(congress-status)", "reply": reply_text}, reasoning_mode="command")
    short_reply = is_short_reply_request(user_msg)
    word = single_word_request(user_msg)
    if word:
        return jsonify({"reasoning": "(single-word)", "reply": word})

    if is_two_actions_request(user_msg):
        reply_text = two_actions_reply(st)
        return jsonify({"reasoning": "(two-actions)", "reply": reply_text})

    local_scan = detect_local_tags(user_msg)
    local_tags = local_scan.get("tags", [])
    intent = local_scan.get("intent") if local_scan else "unknown"
    if intent not in INTENT_VALUES:
        intent = "unknown"
    depth = local_scan.get("depth") if local_scan else "normal"
    if depth not in {"fast", "normal", "deep"}:
        depth = "normal"

    explicit_intent = (data.get("intent") or "").strip().lower()
    if explicit_intent in INTENT_VALUES:
        intent = explicit_intent
    if (data.get("budget_focus") or data.get("ui_tab") == "budget") and intent == "unknown":
        intent = "personal_finance"
    if intent not in INTENT_VALUES or intent == "unknown":
        intent = "markets"  # Default to markets for financial agent

    explicit_depth = (data.get("depth") or "").strip().lower()
    if explicit_depth in {"fast", "normal", "deep"}:
        depth = explicit_depth
    if apollo_prefix_fast:
        depth = "fast"
    if apollo_prefix_deep:
        depth = "deep"

    budget_focus = bool(data.get("budget_focus")) or (data.get("ui_tab") == "budget")
    history_limit = 24 if (intent == "personal_finance" or budget_focus) else 8
    history_text = _format_turns(turns, limit=history_limit)

    fino_classifier_used = not apollo_prefix_fast
    fino_output = run_fino_classifier(user_msg) if fino_classifier_used else {}
    if fino_output and intent == "unknown":
        intent = fino_output.get("intent", intent)
        if intent not in INTENT_VALUES:
            intent = "unknown"
    if fino_output and fino_output.get("needs_long_reflection"):
        depth = "deep"
    # Financial analysis often benefits from deep mode
    if fino_output and fino_output.get("needs_deep_analysis") and intent in ("risk", "investing", "macro"):
        depth = "deep"

    conversational_fast_path = apollo_prefix_fast or (
        "?" not in user_msg
        and len(user_msg.strip()) <= 48
        and bool(
            re.search(
                r"\b(hi|hey|hello|thanks|thank you|lol|how are you|what are you up to|whatcha up to)\b",
                user_msg.lower(),
            )
        )
    )

    hits, requested_k = ([], 0) if conversational_fast_path else gather_hits(intent, user_msg, depth)
    texts__t02, context_chars__t02 = curate_hits__t02(hits, intent=intent, char_cap=1500)
    ctx_lines = [f"- {_fix_display(t)}" for t in texts__t02]
    dedupe_removed__t02 = max(0, len(hits) - len(texts__t02))
    deep_block = ""
    web_block = ""
    deep_used = False
    # Deep analysis via gx10 for complex financial queries
    allow_deep = (
        (not conversational_fast_path)
        and depth != "fast"
        and (depth == "deep" or intent in ("risk", "investing", "macro"))
    )
    if allow_deep:
        deep_block = _run_gx10_deep_analysis(user_msg, intent, hits)
        deep_used = bool(deep_block)

    # Web search — fires when SKY_WEB_ENABLED=1 and (depth=deep OR query needs live data)
    if not conversational_fast_path and (depth == "deep" or _web_worthy(user_msg, intent)):
        try:
            _wq = _refine_web_query(user_msg, intent)
            _wr = _web_search({"query": _wq, "max_results": 4})
            if isinstance(_wr, dict) and _wr.get("ok") and _wr.get("results"):
                web_block = _format_web_block(_wr["results"])
        except Exception:
            web_block = ""

    budget_month = (data.get("budget_month") or "").strip()
    budget_ctx = ""
    budget_focus_ctx = ""
    if intent == "personal_finance" or budget_focus:
        try:
            budget_store = get_store()
            budget_ctx = build_budget_context(budget_store, budget_month)
            budget_focus_ctx = _budget_message_focus(budget_store, budget_month, user_msg, session_state=st)
        except Exception as exc:
            logging.warning("Budget context build failed: %s", exc)
            budget_ctx = ""
            budget_focus_ctx = ""

    blocks = []
    personality_points = _load_kb_personality_points(max_items=6)
    if personality_points:
        personality_block = "\n".join(f"- {point}" for point in personality_points)
        blocks.append(
            "[AGENT_PERSONALITY]\n"
            "Apply these persona constraints to every reply unless directly overridden by the user:\n"
            f"{personality_block}\n"
            "[/AGENT_PERSONALITY]"
        )
    facts_block = session_facts_block(st)
    if facts_block:
        blocks.append(facts_block)
    focus_block = _focus_context_block()
    if focus_block:
        blocks.append(focus_block)
    dashboard_block = _apollo_dashboard_context_block(user_msg)
    if dashboard_block:
        blocks.append(dashboard_block)

    # Persistent user profile — injected early so the model grounds every answer
    if _MEMORY_STORE is not None:
        try:
            profile_block = _mem_context.build_profile_block_from_store(_MEMORY_STORE)
            if profile_block:
                blocks.append(profile_block)
        except Exception:
            pass

    blocks.append(FINANCE_QUALITY_POLICY)
    blocks.append(TOOLING_AWARENESS)
    tool_ctx = st.get("tool_context") if isinstance(st.get("tool_context"), dict) else {}
    if tool_ctx:
        web_last = tool_ctx.get("web_last") if isinstance(tool_ctx.get("web_last"), dict) else None
        rag_last = tool_ctx.get("rag_last") if isinstance(tool_ctx.get("rag_last"), dict) else None
        if web_last:
            blocks.append("[WEB_CONTEXT]\n" + _format_web_results_block(web_last) + "\n[/WEB_CONTEXT]")
        if rag_last:
            blocks.append("[RAG_CONTEXT]\n" + _format_rag_hits_block(rag_last) + "\n[/RAG_CONTEXT]")
    topic_info = update_topic_state(st, user_msg)
    topic_switch = bool(topic_info.get("topic_switch"))
    reference_block = ""
    recent_focus = _recent_focus(st)
    anaphora_hint = _anaphora_hint(st, user_msg)
    reference_first = should_reference_first(anaphora_hint, topic_switch)
    if recent_focus and not topic_switch:
        reference_block += f"[REFERENCE]\n{recent_focus}\n\n"
    if anaphora_hint:
        reference_block += f"[REFERENCE RESOLUTION]\n{anaphora_hint}\n\n"
    if reference_first:
        ctx_lines = []
        deep_block = ""
        if topic_switch:
            history_text = _format_turns(turns, limit=6) or "(none)"
    model_msg_for_prompt = user_msg
    if reference_first and anaphora_hint:
        model_msg_for_prompt = (
            f"{user_msg}\n\n{anaphora_hint}\n\n"
            "Answer directly using the referent above; do not ask for clarification. Keep it short (2-4 sentences)."
        )
    if reference_block:
        blocks.append(reference_block.rstrip())
    if budget_ctx:
        blocks.append("Budget context:\n" + budget_ctx)
    if budget_focus_ctx:
        blocks.append("Budget focus details:\n" + budget_focus_ctx)
    if history_text:
        blocks.append("Recent conversation:\n" + history_text)
    if ctx_lines:
        blocks.append("Context:\n" + "\n".join(ctx_lines))
    if deep_block:
        blocks.append("[Deep Analysis]\n" + deep_block)
    if web_block:
        blocks.append("[Web]\n" + web_block)
    grounding_instruction = (
        "When context is provided, ground your answer in pulled items already shown above. "
        "Mention at least one concrete pulled item (name/date/amount/source) and do not ask to pull again unless data is missing."
    )
    system_prompt = _apollo_chat_preprompt(intent, depth, budget_focus=budget_focus)
    if depth == "fast":
        instruction = (
            "Instruction: Provide a concise answer (1-2 sentences). Use context only if essential."
            " If uncertain, say what's missing. " + grounding_instruction + "\nUser: "
        )
    elif depth == "deep":
        instruction = (
            "Instruction: Think step-by-step before responding. Use at most 10 bullets, no tables. "
            "Reference context/tool insights and mention unknowns."
            " " + grounding_instruction + "\nUser: "
        )
    else:
        instruction = (
            "Instruction: Answer in 6-10 bullets, no tables. Keep it under ~1800 characters. "
            "Use the context/tool block if it helps."
            " If uncertain, say what's missing. " + grounding_instruction + "\nUser: "
        )
    blocks.append(instruction + model_msg_for_prompt)
    prompt = "\n\n".join(blocks)

    # Saturation guard: if Ollama is already serving too many concurrent requests,
    # return a graceful busy message instead of hanging until the client times out.
    _queue = _ollama_queue_depth()
    if _queue > _OLLAMA_MAX_QUEUE:
        logging.warning("[Apollo] Ollama queue depth %d > %d; returning busy reply.", _queue, _OLLAMA_MAX_QUEUE)
        return jsonify({
            "reply": _MODEL_BUSY_REPLY,
            "intent": intent,
            "depth": depth,
            "reasoning": "(model_busy)",
            "queue_depth": _queue,
        })

    model_override = requested_model or (_apollo_deep_model() if depth == "deep" else "")
    base_url_override = _apollo_deep_base_url() if depth == "deep" and not requested_model else ""
    # Phase 2: cap model timeout to what's left of total request budget so
    # upstream preprocessing can't exhaust the client deadline before the model call.
    _configured_timeout = _apollo_timeout_for_depth(depth)
    _total_budget_sec = max(10, int(os.getenv("APOLLO_CHAT_TOTAL_BUDGET_SEC", "25")))
    _preprocessing_elapsed = time.perf_counter() - start
    _remaining_sec = max(5, _total_budget_sec - _preprocessing_elapsed)
    _effective_timeout = int(min(_configured_timeout, _remaining_sec))
    reply = query_model(
        prompt,
        system_prompt=system_prompt,
        model=model_override or None,
        timeout_sec=_effective_timeout,
        retries=1,
        force_ui_chat=None if depth != "deep" and not requested_model else False,
        base_url=base_url_override or None,
        task_type="qa" if depth == "fast" else "finance_chat",
    )
    if _looks_like_model_timeout(reply) and intent in ("personal_finance", "tax", "investing", "risk"):
        reply = _fallback_personal_finance_plan(user_msg)
    if short_reply:
        reply = enforce_short_reply(reply)
    reply = dedupe_repeated_facts(reply)
    reply = apply_truth_guard(reply)
    reply, quality_flags = _apply_finance_quality_postprocess(reply, user_msg, intent, hits, web_used=bool(web_block))

    latency_ms = (time.perf_counter() - start) * 1000.0
    record_chat(latency_ms, len(hits), deep_used, requested_k, depth, fino_classifier_used)
    chain = f"{intent}:{depth}" + ("->gx10" if deep_used else "") + "->fino"
    log_trace(
        intent,
        depth,
        user_msg,
        len(hits),
        deep_used,
        latency_ms,
        chain,
        local_tags,
        fino_classifier_used,
        fino_output or {},
        context_chars__t02,
        dedupe_removed__t02,
        quality_flags,
    )
    should_reflect = depth == "deep" or ((fino_output or {}).get("reflect") is True)
    if should_reflect:
        entry = {
            "message": user_msg,
            "intent": intent,
            "depth": depth,
            "classifier": fino_output or {},
            "timestamp": time.time(),
        }
        if local_tags:
            entry["local_tags"] = local_tags
        log_reflection(entry)

    turns.append({"role": "user", "text": user_msg})
    turns.append({"role": "assistant", "text": reply})
    if len(turns) > 80:
        del turns[:-80]

    reasoning = "\n".join(blocks[:-1]) if blocks[:-1] else "(none)"
    return jsonify({"reasoning": reasoning, "reply": reply, "intent": intent, "depth": depth})


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    """Server-Sent Events (SSE) wrapper for /chat."""
    result = chat()
    status = 200
    if isinstance(result, tuple):
        payload = result[0]
        if len(result) > 1:
            try:
                status = int(result[1] or 200)
            except Exception:
                status = 200
        if hasattr(payload, "get_json"):
            resp = payload
        else:
            resp = jsonify(payload)
        resp.status_code = status
    elif hasattr(result, "get_json"):
        resp = result
    else:
        resp = jsonify(result)
    try:
        payload = resp.get_json(silent=True) or {}
    except Exception:
        payload = {}
    reply_text = payload.get("reply")
    if not isinstance(reply_text, str):
        try:
            reply_text = resp.get_data(as_text=True)
        except Exception:
            reply_text = ""

    def generate():
        for chunk in _chunk_text(reply_text or ""):
            yield _sse_event("delta", {"delta": chunk})
        yield _sse_event("final", payload or {"reply": reply_text or ""})

    stream_resp = Response(stream_with_context(generate()), mimetype="text/event-stream", status=status)
    stream_resp.headers["Cache-Control"] = "no-cache"
    stream_resp.headers["X-Accel-Buffering"] = "no"
    return stream_resp


@app.route("/metrics")
def metrics():
    return jsonify(metrics_snapshot())


@app.route("/admin/student_loan_review/status", methods=["GET"])
def admin_student_loan_review_status():
    auth = _require_local_token()
    if auth is not None:
        return auth
    conversation_id = str(request.args.get("conversation_id") or "default").strip() or "default"
    payload = _loan_review_payload_with_artifacts(
        {
            "ok": True,
            "session": apollo_student_loan_review.get_session(conversation_id),
        }
    )
    return jsonify(payload), 200


@app.route("/admin/student_loan_review/mode", methods=["POST"])
def admin_student_loan_review_mode():
    auth = _require_local_token()
    if auth is not None:
        return auth
    data = request.get_json(force=True, silent=True) or {}
    conversation_id = str(data.get("conversation_id") or "default").strip() or "default"
    result = apollo_student_loan_review.set_mode(
        conversation_id,
        mode_active=data.get("mode_active") if "mode_active" in data else None,
        capture_paused=data.get("capture_paused") if "capture_paused" in data else None,
        pause_reason=str(data.get("pause_reason") or "") if "pause_reason" in data else None,
        target=str(data.get("target") or "") if data.get("target") else None,
    )
    return jsonify(_loan_review_payload_with_artifacts(result)), 200


@app.route("/admin/student_loan_review/capture", methods=["POST"])
def admin_student_loan_review_capture():
    auth = _require_local_token()
    if auth is not None:
        return auth
    data = request.get_json(force=True, silent=True) or {}
    conversation_id = str(data.get("conversation_id") or "default").strip() or "default"
    result = apollo_student_loan_review.capture_screen(
        conversation_id,
        message=str(data.get("message") or "").strip(),
        compare_last=bool(data.get("compare_last")),
    )
    status = 200 if result.get("ok") else 409 if result.get("error") == "capture_paused" else 503
    return jsonify(_loan_review_payload_with_artifacts(result)), status


@app.route("/admin/student_loan_review/artifact", methods=["GET"])
def admin_student_loan_review_artifact():
    auth = _require_local_token()
    if auth is not None:
        return auth
    path = apollo_student_loan_review.resolve_artifact_path(str(request.args.get("path") or ""))
    if path is None or not path.exists():
        return jsonify({"ok": False, "error": "artifact_not_found"}), 404
    return send_file(str(path))


@app.route("/download/documents", methods=["POST"])
def download_documents():
    return (
        jsonify(
            {
                "ok": False,
                "error": "OCR temporarily disabled for Aegis v7; document ingestion unavailable.",
            }
        ),
        503,
    )


@app.route("/dialogue/test", methods=["GET", "POST"])
def dialogue_test():
    body = request.get_json(silent=True) or {}
    turns = body.get("turns") or request.args.get("turns", type=int) or 5
    result = run_dialogue_test(int(turns))
    return jsonify(result), 200


@app.route("/autorun/demo", methods=["POST"])
def autorun_demo():
    result = supervisor.nightly_demo()
    record_autorun_call()
    return jsonify(result), 200


@app.route("/autorun/status", methods=["GET"])
def autorun_status():
    return jsonify(supervisor.state), 200


@app.route("/dialogue/start", methods=["POST"])
def start_dialogue():
    result = broker.run_dialogue(turns=6)
    return jsonify(result), 200


@app.route("/dialogue/log", methods=["GET"])
def dialogue_log():
    log_path = broker.DIALOGUE_LOG
    if not os.path.exists(log_path):
        return jsonify([])
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        lines = []
    return jsonify(lines[-10:]), 200


@app.route("/reflect", methods=["POST"])
def reflect_now():
    body = request.get_json(silent=True) or {}
    days_val = body.get("days")
    try:
        window_days = max(1, int(days_val))
    except (TypeError, ValueError):
        window_days = 1
    summary = summarize_reflections(window_days)
    record_reflection_call()
    summary = summary or {}
    summary.setdefault("window_days", window_days)
    summary.setdefault("entries", 0)
    summary.setdefault("deep_reflections", 0)
    summary.setdefault("deep_ratio_pct", 0.0)
    summary.setdefault("intent_distribution", {})
    summary.setdefault("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z")
    return jsonify({"ok": True, "summary": summary})


@app.route("/rag/snapshot", methods=["GET"])
def rag_snapshot():
    if not _can_snapshot():
        return jsonify({"error": "Recent activity detected. Pause traffic before snapshot."}), 409
    base_path = Path(APOLLO_RAG.get_collection_path())
    snap_dir = base_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    snapshot_path = snap_dir / f"apollo_snapshot_{timestamp}.zip"
    with zipfile.ZipFile(snapshot_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(base_path):
            for file in files:
                full_path = Path(root) / file
                arcname = full_path.relative_to(base_path)
                zf.write(full_path, arcname)
    return send_file(str(snapshot_path), as_attachment=True, download_name=snapshot_path.name)


@app.route("/rag/restore", methods=["POST"])
def rag_restore():
    return (
        jsonify(
            {
                "ok": False,
                "error": "disabled",
                "message": "RAG restore is disabled because it deletes and replaces Apollo's local collection.",
            }
        ),
        403,
    )
    global APOLLO_RAG, TRACE_DIR, TRACE_FILE
    if not _can_snapshot():
        return jsonify({"error": "Recent activity detected. Pause traffic before restore."}), 409
    body = request.get_json(force=True) or {}
    path = body.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "Snapshot path invalid"}), 400

    try:
        apollo_rag_routes.RAG.client.delete_collection(apollo_rag_routes.RAG.col.name)
    except Exception:
        pass

    base_path = Path(APOLLO_RAG.get_collection_path())
    shutil.rmtree(base_path, ignore_errors=True)
    os.makedirs(base_path, exist_ok=True)
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(base_path)

    APOLLO_RAG = AgentRAG("Apollo")
    apollo_rag_routes.RAG = AgentRAG("Apollo")
    TRACE_DIR = Path(APOLLO_RAG.get_collection_path()) / "traces"
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_FILE = TRACE_DIR / "chat_traces.jsonl"
    return jsonify({"ok": True, "restored": True})


def _self_check_payload(force_weather=False):
    base_dir = Path(__file__).resolve().parent
    missing_assets = []
    if (base_dir / "templates").exists() and not (base_dir / "templates" / "index.html").exists():
        missing_assets.append("templates/index.html")
    if (base_dir / "static").exists() and not (base_dir / "static" / "style.css").exists():
        missing_assets.append("static/style.css")

    health_fn = globals().get("_health_payload")
    if callable(health_fn):
        health_detail = health_fn()
    else:
        import datetime as _dt
        app_name = AGENT_NAME.lower() if "AGENT_NAME" in globals() else "agent"
        health_detail = {
            "status": "ok",
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "app": app_name,
        }

    checks = {
        "health": {"ok": True, "detail": health_detail},
        "assets": {"ok": not missing_assets, "missing": missing_assets},
    }
    weather_fn = globals().get("_get_weather")
    if callable(weather_fn):
        try:
            weather = weather_fn(force=force_weather)
        except TypeError:
            weather = weather_fn()
        checks["weather"] = {
            "ok": bool(weather.get("ok")),
            "error": weather.get("error", ""),
            "source": weather.get("source") or "unknown",
        }
    else:
        checks["weather"] = {"ok": True, "skipped": True, "reason": "no_weather"}
    overall_ok = all(item.get("ok") for item in checks.values())
    return {"ok": overall_ok, "checks": checks}


@app.route("/admin/self_check", methods=["GET", "POST"])
def admin_self_check():
    local_token = os.getenv("SKY_LOCAL_TOKEN", "")
    allow_anon = os.getenv("SKY_ALLOW_ANON", "0") == "1"
    if local_token and not allow_anon:
        token = request.headers.get("X-Sky-Token", "")
        if token != local_token:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    force_weather = request.args.get("force") == "1" or bool(payload.get("force_weather"))
    auto_fix = request.args.get("fix") == "1" or bool(payload.get("fix"))
    result = _self_check_payload(force_weather=force_weather)
    result["fixed"] = False
    result["action"] = ""
    if auto_fix and not result["ok"]:
        restart_fn = globals().get("_trigger_restart_script")
        if callable(restart_fn):
            ok, detail = restart_fn(reason="self_check")
            result["fixed"] = bool(ok)
            result["action"] = "restart_script"
            result["detail"] = detail
        else:
            result["fixed"] = False
            result["action"] = "restart_unavailable"
    status = 200 if result["ok"] else 503
    return jsonify(result), status


@app.route("/admin/ebay/drafts/stats", methods=["GET"])
def admin_ebay_drafts_stats():
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import draft_repeat_summary

    term = request.args.get("term", "").strip()
    include_smoke = request.args.get("include_smoke", "0") == "1"
    result = draft_repeat_summary({"term": term, "include_smoke": include_smoke})
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result), 200


def _ebay_draft_image_proxy_url(raw_url: str) -> str:
    safe_url = quote(str(raw_url).strip())
    return f"/admin/ebay/drafts/image_proxy?url={safe_url}"


def _ebay_draft_image_card(url: str, label: str = "image") -> str:
    safe_url = html.escape(str(url).strip())
    source_url = str(url).strip()
    if not source_url:
        return '<div class="missing-image">Missing image URL</div>'
    proxy = _ebay_draft_image_proxy_url(source_url)
    return f'<a href="{safe_url}" target="_blank" rel="noreferrer"><img src="{proxy}" alt="{html.escape(label)}" loading="lazy" /></a>'


@app.route("/admin/ebay/drafts/image_proxy", methods=["GET"])
def admin_ebay_draft_image_proxy():
    raw_url = str(request.args.get("url") or "").strip()
    if not raw_url:
        svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180'><rect width='320' height='180' fill='#f6f7f9'/><text x='20' y='94' fill='#9aa2ad' font-size='14'>Missing image URL</text></svg>"
        return Response(svg, mimetype="image/svg+xml")

    # Ensure percent-decoding is tolerant for already-escaped inputs.
    try:
        raw_url = request.args.get("url") or ""
        raw_url = str(raw_url)
        raw_url = raw_url.strip()
    except Exception:
        raw_url = str(raw_url)

    if not raw_url.lower().startswith(("http://", "https://")):
        svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180'><rect width='320' height='180' fill='#f6f7f9'/><text x='20' y='94' fill='#9aa2ad' font-size='14'>Unsupported image source</text></svg>"
        return Response(svg, mimetype="image/svg+xml")

    try:
        resp = requests.get(
            raw_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=8,
            allow_redirects=True,
            stream=True,
        )
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if resp.status_code >= 400:
            svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 420 180'><rect width='420' height='180' fill='#f6f7f9'/><text x='20' y='94' fill='#9aa2ad' font-size='14'>Image fetch failed</text></svg>"
            return Response(svg, status=resp.status_code, mimetype="image/svg+xml")
        if content_type and "image/" in content_type.split(";")[0]:
            payload = resp.content
            if len(payload) > 4 * 1024 * 1024:
                svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 420 180'><rect width='420' height='180' fill='#f6f7f9'/><text x='20' y='94' fill='#9aa2ad' font-size='14'>Image too large for preview</text></svg>"
                return Response(svg, mimetype="image/svg+xml")
            return Response(payload, content_type=content_type)
        if any(ext in raw_url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".svg")):
            payload = resp.content
            if payload:
                return Response(payload, content_type="image/jpeg")
        svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 420 180'><rect width='420' height='180' fill='#f6f7f9'/><text x='20' y='94' fill='#9aa2ad' font-size='14'>Remote URL is not an image</text></svg>"
        return Response(svg, mimetype="image/svg+xml")
    except Exception as exc:
        svg = f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 420 180'><rect width='420' height='180' fill='#f6f7f9'/><text x='20' y='80' fill='#9aa2ad' font-size='12'>Image fetch error</text><text x='20' y='102' fill='#9aa2ad' font-size='11'>{html.escape(str(exc))[:130]}</text></svg>"
        return Response(svg, mimetype="image/svg+xml")


@app.route("/admin/ebay/drafts/<draft_id>", methods=["GET"])
def admin_ebay_draft_review(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import load_approval_draft, build_shipping_defaults

    result = load_approval_draft(draft_id)
    if not result.get("ok"):
        return jsonify(result), 404
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    offer = draft.get("offer") if isinstance(draft.get("offer"), dict) else {}
    listing_policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
    price = offer.get("pricingSummary", {}).get("price", {}) if isinstance(offer.get("pricingSummary"), dict) else {}
    price_source = str(offer.get("pricingSummary", {}).get("priceSource") or "").strip() if isinstance(offer.get("pricingSummary"), dict) else ""
    price_inferred = bool(offer.get("pricingSummary", {}).get("priceInferred")) if isinstance(offer.get("pricingSummary"), dict) else False
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    missing = draft.get("missing") if isinstance(draft.get("missing"), list) else []
    image_paths = source.get("image_paths") if isinstance(source.get("image_paths"), list) else []
    attachment_urls = source.get("attachment_urls") if isinstance(source.get("attachment_urls"), list) else []
    listing_image_urls = product.get("imageUrls") if isinstance(product.get("imageUrls"), list) else []
    listing_image_cards = []
    for url in listing_image_urls:
        clean = str(url or "").strip()
        if clean:
            listing_image_cards.append(_ebay_draft_image_card(clean, "Listing image"))
    if not listing_image_cards:
        for url in attachment_urls:
            clean = str(url or "").strip()
            if clean:
                listing_image_cards.append(_ebay_draft_image_card(clean, "Source attachment image"))
    listing_image_markup = "\n".join(listing_image_cards) if listing_image_cards else "<p>none</p>"
    preview_image_urls = [str(url or "").strip() for url in listing_image_urls if str(url or "").strip()]
    if not preview_image_urls:
        preview_image_urls = [str(url or "").strip() for url in attachment_urls if str(url or "").strip()]
    title_raw = str(product.get("title") or "Untitled eBay draft")
    description_raw = str(product.get("description") or "")
    title = html.escape(title_raw)
    title_value = html.escape(title_raw, quote=True)
    description_value = html.escape(description_raw)
    published_url = str(payload.get("published_url") or "")
    published_url_line = ""
    if published_url:
        published_escaped = html.escape(published_url)
        published_url_line = f'<p><a href="{published_escaped}" target="_blank" rel="noreferrer">View published listing</a></p>'
    safe_draft_id = html.escape(str(payload.get("draft_id") or draft_id))
    status_text = html.escape(str(payload.get("status") or "needs_approval"))
    condition_code = str(inventory.get("condition") or "USED_GOOD").strip().upper()
    condition_raw = condition_code.replace("_", " ").title()
    quantity = inventory.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 1)
    listing_format = "Buy It Now"
    shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    if not shipping:
        shipping = build_shipping_defaults(
            {
                "title": product.get("title") or "",
                "description": product.get("description") or "",
            }
        )
    package = shipping.get("package") if isinstance(shipping.get("package"), dict) else {}
    shipping_text = html.escape(f"{str(shipping.get('payer') or 'buyer').title()} pays - {shipping.get('service') or 'USPS Ground Advantage'}")
    package_text = html.escape(
        f"{package.get('length') or '?'} x {package.get('width') or '?'} x {package.get('height') or '?'} "
        f"{str(package.get('unit') or 'INCH').lower()}, {package.get('weight_value') or '?'} "
        f"{str(package.get('weight_unit') or 'POUND').lower()}"
    )
    price_value = html.escape(str(price.get("value") or ""), quote=True)
    currency_value = html.escape(str(price.get("currency") or "USD"), quote=True)
    category_id_value = html.escape(str(offer.get("categoryId") or ""), quote=True)
    category_name = str((draft.get("category_lookup") or {}).get("category_name") or "").strip() if isinstance(draft.get("category_lookup"), dict) else ""
    shipping_payer_value = html.escape(str(shipping.get("payer") or "buyer"), quote=True)
    shipping_service_value = html.escape(str(shipping.get("service") or "USPS Ground Advantage"), quote=True)
    shipping_type_value = html.escape(str(shipping.get("type") or "calculated"), quote=True)
    package_length_value = html.escape(str(package.get("length") or ""), quote=True)
    package_width_value = html.escape(str(package.get("width") or ""), quote=True)
    package_height_value = html.escape(str(package.get("height") or ""), quote=True)
    package_unit_value = html.escape(str(package.get("unit") or "INCH"), quote=True)
    package_weight_value = html.escape(str(package.get("weight_value") or ""), quote=True)
    package_weight_unit_value = html.escape(str(package.get("weight_unit") or "POUND"), quote=True)
    condition_options = "\n".join(
        f'<option value="{code}"{" selected" if code == condition_code else ""}>{label}</option>'
        for code, label in (
            ("USED_GOOD", "Used Good"),
            ("USED_EXCELLENT", "Used Excellent"),
            ("USED_VERY_GOOD", "Used Very Good"),
            ("USED_ACCEPTABLE", "Used Acceptable"),
            ("NEW", "New"),
            ("LIKE_NEW", "Like New"),
        )
    )
    publish_readiness = payload.get("publish_readiness") if isinstance(payload.get("publish_readiness"), dict) else {}
    policy_options = publish_readiness.get("policy_options") if isinstance(publish_readiness.get("policy_options"), dict) else {}

    def _policy_select_options(kind: str, selected_id: str, fallback_label: str) -> str:
        selected_id = str(selected_id or "").strip()
        options = policy_options.get(kind) if isinstance(policy_options.get(kind), list) else []
        seen: set[str] = set()
        rows: list[str] = []
        def _option_text(option: dict[str, Any], default: str) -> str:
            details = option.get("details") if isinstance(option.get("details"), dict) else {}
            summary = str(details.get("summary") or details.get("description") or details.get("status") or "").strip()
            label = str(option.get("label") or default).strip()
            return f"{label} - {summary}" if summary else label

        if selected_id:
            selected_option = next((option for option in options if isinstance(option, dict) and str(option.get("id") or "") == selected_id), {})
            selected_text = _option_text(selected_option, fallback_label) if selected_option else f"{fallback_label} ({selected_id})"
            seen.add(selected_id)
            rows.append(f'<option value="{html.escape(selected_id, quote=True)}" selected>{html.escape(selected_text)}</option>')
        for option in options:
            if not isinstance(option, dict):
                continue
            option_id = str(option.get("id") or "").strip()
            if not option_id or option_id in seen:
                continue
            text = _option_text(option, option_id)
            rows.append(f'<option value="{html.escape(option_id, quote=True)}">{html.escape(text)}</option>')
        if not rows:
            rows.append('<option value="">Run readiness check to load choices</option>')
        return "\n".join(rows)

    fulfillment_policy_value = str(listing_policies.get("fulfillmentPolicyId") or "").strip()
    payment_policy_value = str(listing_policies.get("paymentPolicyId") or "").strip()
    return_policy_value = str(listing_policies.get("returnPolicyId") or "").strip()
    merchant_location_value = str(offer.get("merchantLocationKey") or "").strip()
    fulfillment_options = _policy_select_options("fulfillment", fulfillment_policy_value, "Current fulfillment policy")
    payment_options = _policy_select_options("payment", payment_policy_value, "Current payment policy")
    return_options = _policy_select_options("returns", return_policy_value, "Current return policy")
    location_options = _policy_select_options("locations", merchant_location_value, "Current inventory location")

    readiness_items = []
    for item in publish_readiness.get("blockers") or []:
        readiness_items.append(f'<li><strong>Blocked:</strong> {html.escape(str(item))}</li>')
    for item in publish_readiness.get("warnings") or []:
        readiness_items.append(f'<li><strong>Check:</strong> {html.escape(str(item))}</li>')
    readiness_checked_at = html.escape(str(publish_readiness.get("checked_at") or ""))
    if readiness_items:
        ebay_readiness_markup = (
            '<div class="notice warn"><strong>eBay readiness found items to review.</strong>'
            f'<ul>{"".join(readiness_items)}</ul>'
            f'<p class="hint">Last checked: {readiness_checked_at or "unknown"}. Save another policy below, then recheck.</p>'
            "</div>"
        )
    elif publish_readiness:
        ebay_readiness_markup = (
            '<div class="notice good"><strong>eBay readiness passed.</strong>'
            f'<p class="hint">Last checked: {readiness_checked_at or "unknown"}.</p></div>'
        )
    else:
        ebay_readiness_markup = '<div class="notice"><strong>eBay readiness not checked yet.</strong><p class="hint">Approve or run a readiness check to compare this draft against your real eBay policies.</p></div>'
    if payload.get("last_error"):
        ebay_readiness_markup += f'<div class="notice danger"><strong>Last publish hangup:</strong> {html.escape(str(payload.get("last_error")))}</div>'
    draft_readiness_markup = (
        f'<div class="notice danger"><strong>Needs attention:</strong> {html.escape(", ".join(str(item) for item in missing))}</div>'
        if missing
        else '<div class="notice good"><strong>Ready for review:</strong> no required draft fields are missing.</div>'
    )
    if price_source and price_inferred and str(price.get("value") or "").strip():
        draft_readiness_markup += f'<div class="notice"><strong>Price was inferred:</strong> {html.escape(price_source)} estimated {html.escape(str(price.get("value") or ""))} {html.escape(str(price.get("currency") or "USD"))}. Review before approving.</div>'
    if preview_image_urls:
        main_url = preview_image_urls[0]
        main_photo_markup = (
            f'<a class="main-photo-link" href="{html.escape(main_url)}" target="_blank" rel="noreferrer">'
            f'<img src="{_ebay_draft_image_proxy_url(main_url)}" alt="{html.escape(title_raw)}" loading="lazy" />'
            "</a>"
        )
        thumb_markup = "\n".join(
            f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer"><img src="{_ebay_draft_image_proxy_url(url)}" alt="Listing photo {idx}" loading="lazy" /></a>'
            for idx, url in enumerate(preview_image_urls[:8], start=1)
        )
    else:
        main_photo_markup = '<div class="photo-empty">No listing photo yet</div>'
        thumb_markup = ""
    aspects = product.get("aspects") if isinstance(product.get("aspects"), dict) else {}

    def _aspect_text(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if str(item or "").strip())
        return str(value or "").strip()

    specifics: list[tuple[str, str]] = [
        ("Condition", condition_raw or "Used"),
        ("Buying format", listing_format),
        ("Quantity", str(quantity)),
        ("Shipping", f"{str(shipping.get('payer') or 'buyer').title()} pays"),
        ("Shipping service", str(shipping.get("service") or "USPS Ground Advantage")),
        ("Package estimate", package_text),
    ]
    for key in ("Brand", "MPN", "Type", "Model", "Color", "Material", "Size"):
        value = _aspect_text(aspects.get(key))
        if value:
            specifics.append((key, value))
    for key, value in aspects.items():
        if str(key) in {"Brand", "MPN", "Type", "Model", "Color", "Material", "Size"}:
            continue
        clean = _aspect_text(value)
        if clean:
            specifics.append((str(key), clean))
    specifics_markup = "\n".join(
        f'<div class="specific"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'
        for label, value in specifics
        if str(value or "").strip()
    )
    response = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0b1220; --panel:#111c2e; --panel2:#16243a; --text:#e8f0f8; --muted:#9db0c3; --line:rgba(255,255,255,.12); --accent:#75d8a0; --warn:#f7c56b; --danger:#ff9a9a; }}
    body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--text); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 56px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    h2 {{ font-size: 17px; margin: 24px 0 8px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .listing-grid {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .field {{ min-width: 0; overflow-wrap: anywhere; }}
    .label {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    label.row-label {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin: 18px 0 8px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    input.title-input, textarea.title-input, textarea.description-input, textarea.prompt-input, input.field-input, select.field-input {{ width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 8px; background: #0f1726; color: var(--text); padding: 12px 13px; font: inherit; line-height: 1.45; }}
    textarea.title-input {{ min-height: 142px; resize: vertical; font-size: 24px; font-weight: 800; }}
    textarea.description-input {{ min-height: 180px; resize: vertical; }}
    textarea.prompt-input {{ min-height: 120px; resize: vertical; margin-top: 8px; }}
    a {{ color: #9fd0ff; }}
    .image-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }}
    .image-grid a {{ display: block; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #0f1726; }}
    .image-grid img {{ display: block; width: 100%; aspect-ratio: 1 / 1; object-fit: cover; }}
    .status-card {{ border-color: rgba(117,216,160,.28); background: linear-gradient(180deg, rgba(117,216,160,.10), rgba(17,28,46,.96)); }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
    button {{ border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; background:#1a2a42; color:var(--text); font-weight:700; cursor:pointer; }}
    button.small {{ padding: 6px 9px; font-size: 12px; }}
    button.primary {{ background: var(--accent); color:#04110c; border-color:rgba(117,216,160,.65); }}
    .hint {{ color: var(--muted); font-size: 13px; line-height: 1.45; }}
    .pill {{ display:inline-block; border:1px solid var(--line); border-radius:999px; padding:3px 8px; color:var(--muted); font-size:12px; }}
    .topline {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap; }}
    .price {{ font-size: 24px; font-weight: 800; color: var(--accent); }}
    .notice {{ border:1px solid var(--line); border-radius:8px; padding:12px; margin-top:14px; }}
    .notice.good {{ border-color: rgba(117,216,160,.35); background: rgba(117,216,160,.10); }}
    .notice.warn {{ border-color: rgba(247,197,107,.42); background: rgba(247,197,107,.10); }}
    .notice.danger {{ border-color: rgba(255,154,154,.35); background: rgba(255,154,154,.10); }}
    .notice ul {{ margin:8px 0 0; padding-left:20px; }}
    .back-link {{ display:inline-block; margin-bottom:16px; color:#9fd0ff; }}
    .editor-actions {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; }}
    .draft-top {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:14px; }}
    .draft-title {{ margin:0; font-size:24px; }}
    .listing-shell {{ display:grid; grid-template-columns: minmax(0, 1.45fr) minmax(330px, .75fr); gap:22px; align-items:start; }}
    .gallery {{ display:grid; grid-template-columns:72px minmax(0, 1fr); gap:12px; }}
    .thumbs {{ display:flex; flex-direction:column; gap:10px; }}
    .thumbs a {{ display:block; border:1px solid var(--line); border-radius:8px; background:#0f1726; overflow:hidden; }}
    .thumbs img {{ display:block; width:100%; aspect-ratio:1 / 1; object-fit:cover; }}
    .main-photo {{ min-height:460px; display:flex; align-items:center; justify-content:center; background:#0f1726; border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    .main-photo-link {{ display:flex; width:100%; min-height:460px; align-items:center; justify-content:center; }}
    .main-photo img {{ width:100%; height:100%; max-height:620px; object-fit:contain; background:#0f1726; }}
    .photo-empty {{ color:var(--muted); }}
    .purchase-card {{ position:sticky; top:18px; }}
    .format-pill {{ display:inline-block; margin: 8px 0 10px; border:1px solid rgba(117,216,160,.42); background:rgba(117,216,160,.11); color:#bdf1d0; border-radius:999px; padding:4px 10px; font-size:13px; font-weight:700; }}
    .buy-box-row {{ display:grid; grid-template-columns:110px minmax(0, 1fr); gap:12px; padding:10px 0; border-top:1px solid rgba(255,255,255,.08); }}
    .buy-box-row span {{ color:var(--muted); }}
    .approve-wide {{ width:100%; margin-top:16px; font-size:16px; padding:13px 14px; }}
    .about-panel {{ margin-top:22px; padding:0; overflow:hidden; }}
    .about-tab {{ display:inline-block; border-right:1px solid var(--line); border-bottom:1px solid var(--panel); padding:14px 20px; color:#9fd0ff; font-weight:800; background:#0f1726; }}
    .about-inner {{ padding:20px; }}
    .specifics-grid {{ display:grid; grid-template-columns:repeat(2, minmax(240px, 1fr)); gap:10px 42px; }}
    .specific {{ display:grid; grid-template-columns:140px minmax(0, 1fr); gap:12px; min-width:0; }}
    .specific span {{ color:var(--muted); }}
    .specific strong {{ font-weight:600; overflow-wrap:anywhere; }}
    .edit-grid {{ display:grid; grid-template-columns:repeat(2, minmax(220px, 1fr)); gap:14px; margin-top:14px; }}
    .purchase-card .edit-grid {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }}
    .edit-grid .wide {{ grid-column:1 / -1; }}
    .mini-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:10px; }}
    .field-label {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; margin-bottom:6px; }}
    .description-card {{ margin-top:22px; }}
    @media (max-width: 850px) {{
      .listing-shell {{ grid-template-columns:1fr; }}
      .gallery {{ grid-template-columns:1fr; }}
      .thumbs {{ flex-direction:row; overflow-x:auto; order:2; }}
      .thumbs a {{ width:66px; flex:0 0 auto; }}
      .purchase-card {{ position:static; }}
      .specifics-grid {{ grid-template-columns:1fr; }}
      .main-photo, .main-photo-link {{ min-height:320px; }}
    }}
    dialog {{ width:min(620px, calc(100vw - 32px)); border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--text); padding:18px; }}
    dialog::backdrop {{ background:rgba(0,0,0,.55); }}
  </style>
</head>
<body>
  <main>
    <div class="draft-top">
      <a class="back-link" href="/admin/ebay/drafts">Back to drafts</a>
      <span class="pill">{status_text}</span>
    </div>
    {published_url_line}
    <form id="draftTextForm" action="/admin/ebay/drafts/{safe_draft_id}/text" method="post"></form>

    <section class="listing-shell">
      <div class="gallery">
        <div class="thumbs">{thumb_markup}</div>
        <div class="main-photo">{main_photo_markup}</div>
      </div>
      <aside class="panel purchase-card">
        <label class="row-label" for="draftTitle">
          <span>Title</span>
          <button class="small" type="button" onclick="openRewriteDialog('title')">Ask Apollo</button>
        </label>
        <textarea id="draftTitle" class="title-input" form="draftTextForm" name="title" maxlength="80">{title}</textarea>
        <span class="format-pill">{listing_format}</span>
        <div class="edit-grid">
          <div>
            <label class="field-label" for="draftPrice">Price</label>
            <input id="draftPrice" class="field-input" form="draftTextForm" name="price" value="{price_value}" inputmode="decimal" />
          </div>
          <div>
            <label class="field-label" for="draftCurrency">Currency</label>
            <input id="draftCurrency" class="field-input" form="draftTextForm" name="currency" value="{currency_value}" maxlength="8" />
          </div>
          <div>
            <label class="field-label" for="draftCondition">Condition</label>
            <select id="draftCondition" class="field-input" form="draftTextForm" name="condition">{condition_options}</select>
          </div>
          <div>
            <label class="field-label" for="draftQuantity">Quantity</label>
            <input id="draftQuantity" class="field-input" form="draftTextForm" name="quantity" value="{html.escape(str(quantity), quote=True)}" inputmode="numeric" />
          </div>
        </div>
        <div class="buy-box-row"><span>Shipping</span><strong>{shipping_text}</strong></div>
        <div class="buy-box-row"><span>Package</span><strong>{package_text}</strong></div>
        {draft_readiness_markup}
        <form action="/admin/ebay/drafts/{safe_draft_id}/approve" method="post">
          <button class="primary approve-wide" type="submit">Approve</button>
        </form>
        <form action="/admin/ebay/drafts/{safe_draft_id}/check" method="post">
          <button class="approve-wide" type="submit">Check eBay readiness</button>
        </form>
        {ebay_readiness_markup}
      </aside>
    </section>

    <section class="panel about-panel">
      <div class="about-tab">About this item</div>
      <div class="about-inner">
        <p class="hint">Draft preview. This is not posted to eBay yet.</p>
        <h2>Item specifics</h2>
        <div class="specifics-grid">{specifics_markup}</div>
      </div>
    </section>

    <section class="panel description-card">
      <h2>Listing Setup</h2>
      <div class="edit-grid">
        <div>
          <label class="field-label" for="draftCategory">Category ID{f' ({html.escape(category_name)})' if category_name else ''}</label>
          <input id="draftCategory" class="field-input" form="draftTextForm" name="category_id" value="{category_id_value}" />
        </div>
        <div>
          <label class="field-label" for="draftShippingPayer">Shipping payer</label>
          <select id="draftShippingPayer" class="field-input" form="draftTextForm" name="shipping_payer">
            <option value="buyer"{" selected" if str(shipping.get("payer") or "buyer").lower() == "buyer" else ""}>Buyer pays</option>
            <option value="seller"{" selected" if str(shipping.get("payer") or "").lower() == "seller" else ""}>Seller pays</option>
          </select>
        </div>
        <div>
          <label class="field-label" for="draftShippingService">Shipping service</label>
          <input id="draftShippingService" class="field-input" form="draftTextForm" name="shipping_service" value="{shipping_service_value}" />
        </div>
        <div>
          <label class="field-label" for="draftShippingType">Shipping type</label>
          <select id="draftShippingType" class="field-input" form="draftTextForm" name="shipping_type">
            <option value="calculated"{" selected" if str(shipping.get("type") or "calculated").lower() == "calculated" else ""}>Calculated</option>
            <option value="flat"{" selected" if str(shipping.get("type") or "").lower() == "flat" else ""}>Flat</option>
            <option value="free"{" selected" if str(shipping.get("type") or "").lower() == "free" else ""}>Free</option>
          </select>
        </div>
        <div>
          <label class="field-label" for="draftFulfillmentPolicy">eBay shipping policy</label>
          <select id="draftFulfillmentPolicy" class="field-input" form="draftTextForm" name="fulfillment_policy_id">{fulfillment_options}</select>
        </div>
        <div>
          <label class="field-label" for="draftPaymentPolicy">eBay payment policy</label>
          <select id="draftPaymentPolicy" class="field-input" form="draftTextForm" name="payment_policy_id">{payment_options}</select>
        </div>
        <div>
          <label class="field-label" for="draftReturnPolicy">eBay return policy</label>
          <select id="draftReturnPolicy" class="field-input" form="draftTextForm" name="return_policy_id">{return_options}</select>
        </div>
        <div>
          <label class="field-label" for="draftMerchantLocation">eBay inventory location</label>
          <select id="draftMerchantLocation" class="field-input" form="draftTextForm" name="merchant_location_key">{location_options}</select>
        </div>
        <div class="wide">
          <label class="field-label">Package estimate</label>
          <div class="mini-grid">
            <input class="field-input" form="draftTextForm" name="package_length" value="{package_length_value}" placeholder="Length" inputmode="decimal" />
            <input class="field-input" form="draftTextForm" name="package_width" value="{package_width_value}" placeholder="Width" inputmode="decimal" />
            <input class="field-input" form="draftTextForm" name="package_height" value="{package_height_value}" placeholder="Height" inputmode="decimal" />
            <input class="field-input" form="draftTextForm" name="package_unit" value="{package_unit_value}" placeholder="Unit" />
            <input class="field-input" form="draftTextForm" name="package_weight_value" value="{package_weight_value}" placeholder="Weight" inputmode="decimal" />
            <input class="field-input" form="draftTextForm" name="package_weight_unit" value="{package_weight_unit_value}" placeholder="Weight unit" />
          </div>
        </div>
      </div>
      <div class="editor-actions">
        <button class="primary" form="draftTextForm" type="submit">Save listing setup</button>
      </div>
    </section>

    <section class="panel description-card">
      <label class="row-label" for="draftDescription">
        <span>Description</span>
        <button class="small" type="button" onclick="openRewriteDialog('description')">Ask Apollo</button>
      </label>
      <textarea id="draftDescription" class="description-input" form="draftTextForm" name="description">{description_value}</textarea>
      <div class="editor-actions">
        <button class="primary" form="draftTextForm" type="submit">Save edits</button>
      </div>
    </section>

    <dialog id="rewriteDialog">
      <form action="/admin/ebay/drafts/{safe_draft_id}/rewrite_text" method="post" onsubmit="copyDraftFieldsForRewrite()">
        <input type="hidden" id="rewriteField" name="field" value="description" />
        <input type="hidden" id="rewriteCurrentTitle" name="current_title" value="" />
        <input type="hidden" id="rewriteCurrentDescription" name="current_description" value="" />
        <h2 id="rewriteTitle">Ask Apollo</h2>
        <p class="hint">Tell Apollo what to change. It will rewrite the selected field and save it back into this draft.</p>
        <textarea class="prompt-input" name="guidance" placeholder="Example: make it warmer, mention buyer pays shipping, and keep it concise"></textarea>
        <div class="editor-actions">
          <button class="primary" type="submit">Rewrite selected field</button>
          <button type="button" onclick="document.getElementById('rewriteDialog').close()">Cancel</button>
        </div>
      </form>
    </dialog>
    <script>
      function copyDraftFieldsForRewrite() {{
        document.getElementById('rewriteCurrentTitle').value = document.getElementById('draftTitle').value;
        document.getElementById('rewriteCurrentDescription').value = document.getElementById('draftDescription').value;
      }}
      function openRewriteDialog(field) {{
        document.getElementById('rewriteField').value = field;
        document.getElementById('rewriteTitle').textContent = field === 'title' ? 'Ask Apollo to rewrite the title' : 'Ask Apollo to rewrite the description';
        copyDraftFieldsForRewrite();
        document.getElementById('rewriteDialog').showModal();
      }}
    </script>
  </main>
</body>
</html>"""
    return Response(response, mimetype="text/html")


def _apollo_ebay_rewrite_field(field: str, title: str, description: str, guidance: str) -> str:
    field = "title" if str(field or "").strip().lower() == "title" else "description"
    guidance = str(guidance or "").strip()
    if not guidance:
        guidance = "Improve it for a clear, accurate eBay listing."
    if field == "title":
        prompt = (
            "You are Apollo helping revise an eBay listing title.\n"
            "Return only the new title, no quotes, no markdown, no explanation.\n"
            "Keep it accurate to the item, natural for eBay search, and 80 characters or fewer.\n\n"
            f"Current title: {title}\n"
            f"Current description: {description[:900]}\n"
            f"User guidance: {guidance}\n"
            "New title:"
        )
        raw = str(query_model(prompt) or "").strip()
        cleaned = re.sub(r"^new title:\s*", "", raw, flags=re.I).strip().strip('"').strip("'")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:80].strip() or title[:80].strip()

    prompt = (
        "You are Apollo helping revise an eBay listing description.\n"
        "Return only the listing description text, no markdown heading and no explanation.\n"
        "Keep it truthful, concise, buyer-facing, and consistent with the current item details.\n"
        "Do not invent defects, brands, quantities, dimensions, or accessories not provided.\n\n"
        f"Current title: {title}\n"
        f"Current description: {description[:1800]}\n"
        f"User guidance: {guidance}\n"
        "New description:"
    )
    raw = str(query_model(prompt) or "").strip()
    cleaned = re.sub(r"^new description:\s*", "", raw, flags=re.I).strip()
    return cleaned[:4000].strip() or description.strip()


@app.route("/admin/ebay/drafts/<draft_id>/text", methods=["POST"])
def admin_ebay_draft_update_text(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import update_approval_draft_text

    payload = request.get_json(force=True, silent=True) if request.is_json else None
    if not isinstance(payload, dict):
        payload = {key: value for key, value in request.form.items()}
    payload["draft_id"] = draft_id
    payload["source"] = payload.get("source") or "operator"
    result = update_approval_draft_text(payload)
    if not request.is_json:
        return redirect(f"/admin/ebay/drafts/{quote(str(draft_id), safe='')}")
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/admin/ebay/drafts/<draft_id>/rewrite_text", methods=["POST"])
def admin_ebay_draft_rewrite_text(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import load_approval_draft, update_approval_draft_text

    payload = request.get_json(force=True, silent=True) if request.is_json else None
    if not isinstance(payload, dict):
        payload = {key: value for key, value in request.form.items()}
    field = "title" if str(payload.get("field") or "").strip().lower() == "title" else "description"
    read = load_approval_draft(draft_id)
    if not read.get("ok"):
        if not request.is_json:
            return redirect(f"/admin/ebay/drafts/{quote(str(draft_id), safe='')}")
        return jsonify(read), 404
    record = read.get("payload") if isinstance(read.get("payload"), dict) else {}
    draft = record.get("draft") if isinstance(record.get("draft"), dict) else {}
    inventory = draft.get("inventory_item") if isinstance(draft.get("inventory_item"), dict) else {}
    product = inventory.get("product") if isinstance(inventory.get("product"), dict) else {}
    current_title = str(payload.get("current_title") or product.get("title") or "")
    current_description = str(payload.get("current_description") or product.get("description") or "")
    guidance = str(payload.get("guidance") or "")
    rewritten = _apollo_ebay_rewrite_field(field, current_title, current_description, guidance)
    update_payload = {
        "draft_id": draft_id,
        "source": "apollo_rewrite",
        "guidance": guidance,
        "title": current_title,
        "description": current_description,
    }
    update_payload[field] = rewritten
    result = update_approval_draft_text(update_payload)
    if not request.is_json:
        return redirect(f"/admin/ebay/drafts/{quote(str(draft_id), safe='')}")
    status = 200 if result.get("ok") else 400
    return jsonify({**result, "rewritten_field": field, "rewritten_text": rewritten}), status


@app.route("/admin/ebay/drafts", methods=["GET"])
def admin_ebay_drafts_list():
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import list_approval_drafts

    status_filter = str(request.args.get("status") or "").strip().lower()
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    include_smoke = request.args.get("include_smoke", "0") == "1"
    search_query = str(request.args.get("q") or request.args.get("term") or "").strip()
    result = list_approval_drafts({"limit": limit, "include_smoke": include_smoke, "include_all": bool(search_query)})
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "draft_list_failed"}), 500

    drafts = result.get("drafts") if isinstance(result.get("drafts"), list) else []
    if status_filter:
        drafts = [item for item in drafts if str(item.get("status") or "").lower() == status_filter]
    if search_query:
        needle = search_query.lower()

        def _draft_search_hit(item: Dict[str, Any]) -> bool:
            values = [
                item.get("draft_id"),
                item.get("status"),
                item.get("created_at"),
                item.get("title"),
                item.get("sku"),
                item.get("price"),
                item.get("currency"),
                item.get("category_id"),
                "ready" if item.get("ready_to_publish") else "not ready",
                "test draft" if item.get("is_smoke") else "",
            ]
            haystack = " ".join(str(value or "") for value in values).lower()
            return needle in haystack

        drafts = [item for item in drafts if _draft_search_hit(item)]

    def _drafts_href(**updates: Any) -> str:
        params: Dict[str, Any] = {
            "limit": limit,
            "status": status_filter,
            "q": search_query,
            "include_smoke": "1" if include_smoke else "",
        }
        params.update(updates)
        parts = []
        for key, value in params.items():
            text = str(value or "").strip()
            if text:
                parts.append(f"{quote(str(key), safe='')}={quote(text, safe='')}")
        return "/admin/ebay/drafts" + (f"?{'&'.join(parts)}" if parts else "")

    draft_rows = []
    for item in drafts:
        draft_id = str(item.get("draft_id") or "")
        safe = html.escape(draft_id)
        draft_url_id = quote(draft_id, safe="")
        raw_status = str(item.get("status") or "").strip()
        status_words = [part.lower() for part in raw_status.replace("-", "_").split("_") if part]
        status_label = " ".join(status_words)
        if status_label:
            status_label = status_label[0].upper() + status_label[1:]
        status = html.escape(status_label or raw_status)
        created = html.escape(_format_draft_created_at(item.get("created_at") or ""))
        title = html.escape(str(item.get("title") or "Untitled draft"))
        sku = html.escape(str(item.get("sku") or ""))
        price_source = str(item.get("price_source") or "").strip()
        price_value = f"{item.get('price') or ''} {item.get('currency') or ''}".strip()
        if price_source and str(item.get("price") or "").strip():
            price_suffix = " inferred" if item.get("price_inferred") else " set"
            price = html.escape(f"{price_value} ({price_source}{price_suffix})")
        else:
            price = html.escape(price_value)
        ready = "yes" if item.get("ready_to_publish") else "no"
        review = f"/admin/ebay/drafts/{draft_url_id}"
        artifact = f"/admin/ebay/drafts/{draft_url_id}/artifact"
        delete_action = f"/admin/ebay/drafts/{draft_url_id}/delete"
        published_url = str(item.get("published_url") or "")
        published_link = ""
        if published_url:
            published_escaped = html.escape(published_url)
            published_link = f'<a href="{published_escaped}" target="_blank" rel="noreferrer">live</a>'
        preview_images = item.get("preview_images") if isinstance(item.get("preview_images"), list) else []
        preview_markup = "".join(_ebay_draft_image_card(str(url or "").strip(), "Draft preview") for url in preview_images[:3] if str(url or "").strip())
        if not preview_markup:
            preview_markup = '<span class="thumb-empty">no image</span>'
        actions_markup = (
            f'<div class="actions"><a class="action-link primary" href="{review}" title="Open the approval screen for this draft.">Review</a>'
            f'<a class="action-link" href="{artifact}" title="Open the source JSON and saved draft data.">Source</a>'
            f'<form class="inline-action" action="{delete_action}" method="post" onsubmit="return confirm(\'Delete local draft {safe}?\');">'
            f'<button type="submit" title="Delete this local Apollo draft record.">Delete</button></form></div>'
        )
        draft_rows.append(
            f"<tr><td data-label=\"Draft ID\" class=\"draft-id\">{safe}</td>"
            f"<td data-label=\"Preview\" class=\"preview-cell\"><div class=\"thumb-row\">{preview_markup}</div></td>"
            f"<td data-label=\"Status\" class=\"status-cell\"><span class=\"status-pill\">{status}</span></td>"
            f"<td data-label=\"Created\" class=\"created-cell\">{created}</td>"
            f"<td data-label=\"Title\" class=\"title-cell\">{title}</td>"
            f"<td data-label=\"SKU\" class=\"sku-cell\">{sku}</td>"
            f"<td data-label=\"Price\">{price}</td>"
            f"<td data-label=\"Ready\">{ready}</td>"
            f"<td data-label=\"Actions\">{actions_markup}</td>"
            f"<td data-label=\"Published\">{published_link}</td></tr>"
        )
    rows_markup = "\n".join(draft_rows) if draft_rows else "<tr><td colspan=\"10\">No drafts yet.</td></tr>"
    selected_filter_words = [part.lower() for part in str(status_filter or "").replace("-", "_").split("_") if part]
    selected_filter_label = " ".join(selected_filter_words)
    if selected_filter_label:
        selected_filter_label = selected_filter_label[0].upper() + selected_filter_label[1:]
    selected_filter = f'<span class="status-pill">{html.escape(selected_filter_label or "all")}</span>'
    term_view = html.escape(search_query) if search_query else "all"
    search_value = html.escape(search_query)
    needs_href = html.escape(_drafts_href(status="needs_approval"))
    all_href = html.escape(_drafts_href(status=""))
    smoke_href = html.escape(_drafts_href(include_smoke="1"))
    hide_smoke_href = html.escape(_drafts_href(include_smoke=""))
    mug_href = html.escape("/admin/ebay/drafts/stats?term=mug")
    clear_href = html.escape(_drafts_href(q="", status="", include_smoke=""))
    response = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Apollo eBay Drafts</title>
    <style>
    :root {{ color-scheme: dark; --bg:#080e19; --panel:#101a2b; --panel2:#17253a; --panel3:#0d1625; --text:#eef5fb; --muted:#9fb2c5; --line:rgba(255,255,255,.12); --accent:#75d8a0; --link:#9fd0ff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--text); }}
    main {{ width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    h1 {{ margin: 0 0 10px; font-size: clamp(28px, 3vw, 38px); letter-spacing: 0; }}
    .meta {{ display: flex; gap: 14px; color: var(--muted); flex-wrap: wrap; }}
    .toolbar {{ margin: 16px 0 12px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
    .toolbar a, .toolbar button, .action-link {{ border: 1px solid var(--line); border-radius: 999px; padding: 7px 11px; color: var(--link); background: rgba(255,255,255,.035); text-decoration: none; font-size: 14px; line-height: 1; }}
    .toolbar a:hover, .action-link:hover {{ background: rgba(159,208,255,.12); }}
    .search-form {{ margin: 14px 0; display: grid; grid-template-columns: minmax(220px, 420px) auto auto; gap: 8px; align-items: center; }}
    .search-form input {{ width: 100%; min-height: 38px; border: 1px solid var(--line); border-radius: 999px; background: #0b1422; color: var(--text); padding: 0 14px; font: inherit; }}
    .search-form button {{ min-height: 38px; border: 1px solid rgba(117,216,160,.38); border-radius: 999px; background: rgba(117,216,160,.12); color: #d8ffe6; padding: 0 14px; cursor: pointer; }}
    .legend {{ color: var(--muted); font-size: 13px; line-height: 1.45; margin: 0 0 14px; max-width: 960px; }}
    .table-wrap {{ width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }}
    table {{ width: 100%; min-width: 1020px; border-collapse: collapse; background: var(--panel); table-layout: fixed; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: top; }}
    th {{ background: var(--panel2); font-size: 13px; text-transform: uppercase; color: var(--muted); }}
    a {{ color: var(--link); }}
    .status-cell {{ min-width: 0; }}
    .status-pill {{ display: inline-flex; align-items: center; justify-content: center; max-width: 92px; min-height: 28px; border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font-size: 12px; line-height: 1.15; text-align: center; white-space: normal; overflow-wrap: anywhere; }}
    .hint {{ color: var(--muted); font-size: 13px; line-height: 1.45; max-width: 820px; }}
    .thumb-row {{ display:flex; gap:8px; flex-wrap:wrap; min-width: 88px; }}
    .thumb-row a {{ display:block; }}
    .thumb-row img {{ width:56px; height:56px; object-fit:cover; border-radius:8px; border:1px solid rgba(255,255,255,.12); background:#0a1018; }}
    .thumb-empty {{ color: var(--muted); font-size: 12px; }}
    .actions {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
    .action-link.primary {{ color: #dcfce7; border-color: rgba(117,216,160,.35); }}
    .inline-action {{ display:inline; margin: 0; }}
    .inline-action button {{ border:1px solid rgba(255,255,255,.18); border-radius:999px; background:#1a2230; color:#f3f6fb; padding:7px 10px; cursor:pointer; line-height:1; }}
    .draft-id {{ overflow-wrap: anywhere; }}
    .title-cell, .sku-cell {{ overflow-wrap: anywhere; }}
    .created-cell {{ overflow-wrap: anywhere; line-height: 1.28; }}
    th:nth-child(1) {{ width: 26%; }}
    th:nth-child(2) {{ width: 12%; }}
    th:nth-child(3) {{ width: 9%; }}
    th:nth-child(4) {{ width: 12%; }}
    th:nth-child(5) {{ width: 16%; }}
    th:nth-child(6) {{ width: 13%; }}
    th:nth-child(7) {{ width: 8%; }}
    th:nth-child(8) {{ width: 6%; }}
    th:nth-child(9) {{ width: 12%; }}
    th:nth-child(10) {{ width: 8%; }}
    @media (max-width: 760px) {{
      main {{ width: calc(100vw - 20px); padding-top: 18px; }}
      .search-form {{ grid-template-columns: 1fr; }}
      .table-wrap {{ border: 0; background: transparent; overflow: visible; }}
      table, thead, tbody, tr, td {{ display: block; width: 100%; min-width: 0; }}
      table {{ background: transparent; }}
      thead {{ display: none; }}
      tr {{ margin: 0 0 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); overflow: hidden; }}
      td {{ display: grid; grid-template-columns: 92px 1fr; gap: 10px; padding: 9px 10px; }}
      td::before {{ content: attr(data-label); color: var(--muted); font-size: 12px; text-transform: uppercase; }}
      .thumb-row {{ min-width: 0; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>Apollo eBay Drafts</h1>
    <p class="hint">Review opens the approval page. Approval marks the draft as ready for the publish queue; it does not post to eBay. Live publishing still requires Apollo's separate eBay mutation and publish gates.</p>
    <div class="meta">
      <div>Total rows: {len(drafts)}</div>
      <div>Filter: {selected_filter}</div>
      <div>Term: {term_view}</div>
    </div>
    <form class="search-form" method="get" action="/admin/ebay/drafts">
      <input type="hidden" name="limit" value="{limit}">
      <input type="hidden" name="status" value="{html.escape(status_filter)}">
      <input type="hidden" name="include_smoke" value="{"1" if include_smoke else ""}">
      <input name="q" value="{search_value}" placeholder="Search drafts, status, title, SKU, price...">
      <button type="submit">Search</button>
      <a class="action-link" href="{clear_href}">Clear</a>
    </form>
    <div class="toolbar"><a href="{needs_href}">Needs approval</a><a href="{all_href}">All drafts</a><a href="{smoke_href}">Show test drafts</a><a href="{hide_smoke_href}">Hide test drafts</a><a href="{mug_href}">Coffee mug duplicate check</a></div>
    <p class="legend">Source opens the saved draft data behind the row. Test drafts are local smoke-test records. The coffee mug duplicate check is a helper for repeated mug listings.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Draft ID</th>
            <th>Preview</th>
            <th>Status</th>
            <th>Created</th>
            <th>Title</th>
            <th>SKU</th>
            <th>Price</th>
            <th>Ready</th>
            <th>Actions</th>
            <th>Published</th>
          </tr>
        </thead>
        <tbody>{rows_markup}</tbody>
      </table>
    </div>
  </main>
</body>
</html>"""
    return Response(response, mimetype="text/html")


@app.route("/admin/ebay/drafts/<draft_id>/approve", methods=["POST"])
def admin_ebay_draft_approve(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import approve_approval_draft

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
        for key, value in request.form.items():
            payload[key] = value
    payload["draft_id"] = draft_id
    result = approve_approval_draft(payload)
    if not request.is_json:
        return redirect(f"/admin/ebay/drafts/{quote(str(draft_id), safe='')}")
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/admin/ebay/drafts/<draft_id>/delete", methods=["POST"])
def admin_ebay_draft_delete(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import delete_approval_draft

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
        for key, value in request.form.items():
            payload[key] = value
    payload["draft_id"] = draft_id
    result = delete_approval_draft(payload)
    if not request.is_json:
        return redirect("/admin/ebay/drafts")
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/admin/ebay/drafts/<draft_id>/check", methods=["POST"])
def admin_ebay_draft_check(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import refresh_approval_draft_readiness

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
        for key, value in request.form.items():
            payload[key] = value
    payload["draft_id"] = draft_id
    result = refresh_approval_draft_readiness(payload)
    if not request.is_json:
        return redirect(f"/admin/ebay/drafts/{quote(str(draft_id), safe='')}")
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/admin/ebay/drafts/<draft_id>/publish", methods=["POST"])
def admin_ebay_draft_publish(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import PUBLISH_CONFIRM_PHRASE, publish_approval_draft

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
        for key, value in request.form.items():
            payload[key] = value
    payload["draft_id"] = draft_id
    payload.setdefault("operator_confirmed", True)
    payload.setdefault("confirm_phrase", PUBLISH_CONFIRM_PHRASE)
    if isinstance(payload.get("operator_confirmed"), str):
        payload["operator_confirmed"] = payload["operator_confirmed"].strip().lower() in {"1", "true", "yes", "on"}
    result = publish_approval_draft(payload)
    if not request.is_json and result.get("ok"):
        return redirect(f"/admin/ebay/drafts/{quote(str(draft_id), safe='')}")
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/admin/ebay/drafts/cleanup_smoke", methods=["POST"])
def admin_ebay_drafts_cleanup_smoke():
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import EBAY_APPROVAL_DRAFT_DIR, _is_smoke_draft

    dry_run = request.args.get("dry_run", "1") == "1"
    removed: list[str] = []
    skipped: list[str] = []
    if not EBAY_APPROVAL_DRAFT_DIR.exists():
        return jsonify({"ok": True, "dry_run": dry_run, "removed": removed, "removed_count": 0, "skipped_count": 0})

    for draft_dir in [p for p in EBAY_APPROVAL_DRAFT_DIR.iterdir() if p.is_dir()]:
        draft_id = str(draft_dir.name)
        title = ""
        json_path = draft_dir / "draft.json"
        if json_path.exists():
            try:
                payload_data = json.loads(json_path.read_text(encoding="utf-8"))
                draft = payload_data.get("draft", {})
                inventory = draft.get("inventory_item", {}) if isinstance(draft, dict) else {}
                product = inventory.get("product", {}) if isinstance(inventory, dict) else {}
                title = str(product.get("title") or "")
            except Exception:
                title = ""

        if not _is_smoke_draft(draft_id, title):
            skipped.append(draft_id)
            continue

        if dry_run:
            removed.append(draft_id)
            continue

        try:
            shutil.rmtree(draft_dir)
            removed.append(draft_id)
        except Exception:
            skipped.append(draft_id)

    return jsonify(
        {
            "ok": True,
            "dry_run": dry_run,
            "removed": removed,
            "removed_count": len(removed),
            "skipped_count": len(skipped),
            "message": "Re-run with ?dry_run=0 to delete files." if dry_run else "Smoke drafts removed.",
        }
    )


@app.route("/admin/ebay/drafts/<draft_id>/artifact", methods=["GET"])
def admin_ebay_draft_artifact(draft_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth

    from Apollo.apollo_tools.ebay_listing import load_approval_draft

    result = load_approval_draft(draft_id)
    if not result.get("ok"):
        return jsonify(result), 404
    markdown_path = Path(str(result.get("markdown_path") or ""))
    if not markdown_path.exists():
        return jsonify({"ok": False, "error": "artifact_not_found", "draft_id": draft_id}), 404
    try:
        return Response(markdown_path.read_text(encoding="utf-8"), mimetype="text/markdown")
    except Exception as exc:
        return jsonify({"ok": False, "error": "artifact_read_failed", "detail": str(exc), "draft_id": draft_id}), 500


@app.route("/admin/trading_homework/run", methods=["POST"])
def admin_trading_homework_run():
    local_token = os.getenv("SKY_LOCAL_TOKEN", "") or os.getenv("APOLLO_LOCAL_TOKEN", "")
    allow_anon = os.getenv("SKY_ALLOW_ANON", "0") == "1"
    if local_token and not allow_anon:
        token = request.headers.get("X-Apollo-Token", "") or request.headers.get("X-Sky-Token", "")
        if token != local_token and (request.headers.get("Authorization", "").strip().lower() != f"bearer {local_token}".lower()):
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    try:
        result = apollo_trading_homework.trading_homework_run(payload, rag=APOLLO_RAG)
        return jsonify(result), 200
    except Exception as exc:
        logging.exception("trading_homework failed")
        return jsonify({"ok": False, "error": f"trading_homework_failed:{type(exc).__name__}:{exc}"}), 500


_HIPPORAG_GRAPH_RUNS: Dict[str, Dict[str, Any]] = {}
_BACKGROUND_PIPELINE_RUN_LOCK = Lock()
_BACKGROUND_PIPELINE_RUN_STATE: Dict[str, Any] = {
    "status": "idle",
    "started_at": "",
    "finished_at": "",
    "worker_name": "",
    "payload": {},
    "last_result": {},
    "last_error": "",
}


def _background_pipeline_state_snapshot() -> Dict[str, Any]:
    with _BACKGROUND_PIPELINE_RUN_LOCK:
        payload = dict(_BACKGROUND_PIPELINE_RUN_STATE)
        if isinstance(payload.get("payload"), dict):
            payload["payload"] = dict(payload["payload"])
        if isinstance(payload.get("last_result"), dict):
            payload["last_result"] = dict(payload["last_result"])
        return payload


def _hipporag_graph_path() -> str:
    return os.path.join(os.path.abspath(os.getenv("RAG_DIR", str(_APOLLO_ROOT / "chroma_db"))), "financial_kg.json")


def _load_live_hipporag_graph():
    if _HIPPO_RETRIEVER is not None:
        try:
            _HIPPO_RETRIEVER.kg.reload_if_changed()
        except Exception:
            pass
        return _HIPPO_RETRIEVER.kg
    try:
        from apollo_hipporag.graph_store import FinancialKG

        return FinancialKG(_hipporag_graph_path())
    except Exception:
        return None


def _require_local_token() -> Any:
    local_token = os.getenv("SKY_LOCAL_TOKEN", "") or os.getenv("APOLLO_LOCAL_TOKEN", "")
    allow_anon = os.getenv("SKY_ALLOW_ANON", "0") == "1"
    if not local_token or allow_anon:
        return None
    token = request.headers.get("X-Apollo-Token", "") or request.headers.get("X-Sky-Token", "")
    if token == local_token:
        return None
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower() == f"bearer {local_token}".lower():
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def _simulation_request_context() -> Dict[str, str]:
    token = request.headers.get("X-Apollo-Token", "") or request.headers.get("X-Sky-Token", "")
    token_hint = token[:12] if token else ""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    remote_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else (request.remote_addr or "")
    return {
        "remote_ip": remote_ip.strip(),
        "user_agent": request.headers.get("User-Agent", "").strip(),
        "token_hint": token_hint,
    }


@app.route("/admin/hipporag/build_graph/run", methods=["POST"])
def admin_hipporag_build_graph_run():
    auth = _require_local_token()
    if auth is not None:
        return auth

    payload = request.get_json(force=True, silent=True) or {}
    use_llm = bool(payload.get("use_llm", False))
    limit = payload.get("limit")
    batch_size = payload.get("batch_size")

    try:
        limit_int = int(limit) if limit is not None else None
    except Exception:
        limit_int = None
    try:
        batch_size_int = int(batch_size) if batch_size is not None else 50
    except Exception:
        batch_size_int = 50
    batch_size_int = max(5, min(batch_size_int, 200))

    rag_dir = os.path.abspath(os.getenv("RAG_DIR", str(_APOLLO_ROOT / "chroma_db")))
    collection = os.getenv("RAG_COLLECTION", "apollo_financial")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    _HIPPORAG_GRAPH_RUNS[run_id] = {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "use_llm": use_llm,
        "limit": limit_int,
        "batch_size": batch_size_int,
        "rag_dir": rag_dir,
        "collection": collection,
        "error": "",
        "finished_at": "",
    }

    def _worker() -> None:
        try:
            from apollo_hipporag.build_graph import build_graph

            build_graph(
                rag_dir=rag_dir,
                collection_name=collection,
                use_llm=use_llm,
                limit=limit_int,
                batch_size=batch_size_int,
            )
            if _HIPPO_RETRIEVER is not None:
                try:
                    _HIPPO_RETRIEVER.kg.reload_if_changed()
                except Exception:
                    pass
            _HIPPORAG_GRAPH_RUNS[run_id]["status"] = "ok"
        except Exception as exc:
            _HIPPORAG_GRAPH_RUNS[run_id]["status"] = "error"
            _HIPPORAG_GRAPH_RUNS[run_id]["error"] = f"{type(exc).__name__}:{exc}"
        finally:
            _HIPPORAG_GRAPH_RUNS[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    Thread(target=_worker, daemon=True, name=f"hipporag-build-{run_id}").start()

    return jsonify({"ok": True, "run_id": run_id, "status": _HIPPORAG_GRAPH_RUNS[run_id]}), 202


@app.route("/admin/hipporag/build_graph/status", methods=["GET"])
def admin_hipporag_build_graph_status():
    auth = _require_local_token()
    if auth is not None:
        return auth
    run_id = (request.args.get("run_id") or "").strip()
    if run_id:
        row = _HIPPORAG_GRAPH_RUNS.get(run_id)
        return jsonify({"ok": bool(row is not None), "run_id": run_id, "status": row}), (200 if row else 404)
    # Default: return the most recent 10 runs.
    keys = sorted(_HIPPORAG_GRAPH_RUNS.keys(), reverse=True)[:10]
    return jsonify({"ok": True, "runs": [_HIPPORAG_GRAPH_RUNS[k] for k in keys]}), 200


@app.route("/admin/hipporag/graph", methods=["GET"])
def admin_hipporag_graph():
    auth = _require_local_token()
    if auth is not None:
        return auth

    def _int_arg(name: str, default: int) -> int:
        raw = (request.args.get(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except Exception:
            return default

    kg = _load_live_hipporag_graph()
    if kg is None:
        return jsonify({"ok": False, "error": "hipporag_graph_unavailable"}), 500

    center = (request.args.get("center") or "").strip()
    query = (request.args.get("q") or "").strip()
    payload = kg.subgraph_view(
        limit_nodes=_int_arg("limit_nodes", 120),
        min_degree=_int_arg("min_degree", 1),
        center=center,
        query=query,
        hops=_int_arg("hops", 1),
    )
    payload["ok"] = True
    payload["graph_path"] = _hipporag_graph_path()
    payload["available"] = bool(payload.get("stats", {}).get("nodes", 0) or os.path.exists(_hipporag_graph_path()))
    payload["fingerprint"] = "|".join(
        [
            payload.get("updated_at") or "none",
            str(payload.get("stats", {}).get("nodes", 0)),
            str(payload.get("stats", {}).get("edges", 0)),
            str(payload.get("stats", {}).get("subgraph_nodes", 0)),
            str(payload.get("stats", {}).get("subgraph_edges", 0)),
            payload.get("filters", {}).get("center", ""),
            payload.get("filters", {}).get("query", ""),
        ]
    )
    return jsonify(payload), 200


@app.route("/admin/nightly_pipeline/run", methods=["POST"])
def admin_nightly_pipeline_run():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    try:
        result = apollo_nightly_pipeline.start_background_run(payload)
        return jsonify(result), 202
    except Exception as exc:
        logging.exception("nightly_pipeline_run failed")
        return jsonify({"ok": False, "error": f"nightly_pipeline_run_failed:{type(exc).__name__}:{exc}"}), 500


@app.route("/admin/nightly_pipeline/status", methods=["GET"])
def admin_nightly_pipeline_status():
    auth = _require_local_token()
    if auth is not None:
        return auth
    run_id = str(request.args.get("run_id") or "").strip()
    if run_id:
        row = apollo_nightly_pipeline.background_status(run_id)
        return jsonify({"ok": bool(row), "run_id": run_id, "status": row}), (200 if row else 404)
    return jsonify({"ok": True, "status": apollo_nightly_pipeline.latest_status()}), 200


@app.route("/admin/background_pipeline/run", methods=["POST"])
def admin_background_pipeline_run():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    lock_state = apollo_nightly_pipeline.background_lock_state(clear_stale=True)
    latest = apollo_nightly_pipeline.latest_status()
    with _BACKGROUND_PIPELINE_RUN_LOCK:
        worker_running = _BACKGROUND_PIPELINE_RUN_STATE.get("status") == "running"
    if worker_running:
        return jsonify(
            {
                "ok": True,
                "accepted": False,
                "already_running": True,
                "worker": _background_pipeline_state_snapshot(),
                "lock": lock_state,
                "status": latest,
            }
        ), 202
    if bool(lock_state.get("present")):
        return jsonify(
            {
                "ok": False,
                "accepted": False,
                "error": "nightly_lock_present",
                "lock": lock_state,
                "status": latest,
            }
        ), 409

    run_payload = dict(payload)
    worker_name = f"apollo-background-step-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with _BACKGROUND_PIPELINE_RUN_LOCK:
        _BACKGROUND_PIPELINE_RUN_STATE.update(
            {
                "status": "running",
                "started_at": started_at,
                "finished_at": "",
                "worker_name": worker_name,
                "payload": dict(run_payload),
                "last_result": {},
                "last_error": "",
            }
        )

    def _worker() -> None:
        try:
            result = apollo_nightly_pipeline.run_background_pipeline_step(run_payload)
            worker_status = "ok" if bool(result.get("ok")) else ("waiting" if bool(result.get("waiting")) else "error")
            with _BACKGROUND_PIPELINE_RUN_LOCK:
                _BACKGROUND_PIPELINE_RUN_STATE.update(
                    {
                        "status": worker_status,
                        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "last_result": dict(result),
                        "last_error": "",
                    }
                )
        except Exception as exc:
            logging.exception("background_pipeline_run worker failed")
            with _BACKGROUND_PIPELINE_RUN_LOCK:
                _BACKGROUND_PIPELINE_RUN_STATE.update(
                    {
                        "status": "error",
                        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "last_result": {},
                        "last_error": f"{type(exc).__name__}:{exc}",
                    }
                )

    Thread(target=_worker, daemon=True, name=worker_name).start()
    return jsonify({"ok": True, "accepted": True, "worker": _background_pipeline_state_snapshot(), "lock": lock_state, "status": latest}), 202


@app.route("/admin/background_pipeline/status", methods=["GET"])
def admin_background_pipeline_status():
    auth = _require_local_token()
    if auth is not None:
        return auth
    status = apollo_nightly_pipeline.latest_status()
    return jsonify({"ok": True, "status": status}), 200


@app.route("/admin/corpus/bootstrap", methods=["POST"])
def admin_corpus_bootstrap():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    result = apollo_corpus_bootstrap.bootstrap_real_public_corpus(payload)
    if result.get("ok"):
        audit = dict(result.get("audit") or {})
        _FINANCIAL_CORPUS_STATUS.update(
            {
                "collection_exists": bool(audit.get("total_chunks", 0) or _FINANCIAL_CORPUS_STATUS.get("collection_exists")),
                "doc_count": int(audit.get("total_chunks") or _FINANCIAL_CORPUS_STATUS.get("doc_count") or 0),
                "real_chunk_count": int(audit.get("real_chunk_count") or 0),
                "synthetic_chunk_count": int(audit.get("synthetic_chunk_count") or 0),
                "real_ratio": float(audit.get("real_ratio") or 0.0),
                "bootstrap_state": "real_public_bootstrapped",
            }
        )
    status_code = 200 if result.get("ok") else 500
    return jsonify(result), status_code


@app.route("/admin/corpus/audit", methods=["GET"])
def admin_corpus_audit():
    auth = _require_local_token()
    if auth is not None:
        return auth
    top_k = max(1, min(int(request.args.get("top_k") or 8), 20))
    payload = {
        "top_k_sources": top_k,
        "collection": str(request.args.get("collection") or os.getenv("RAG_COLLECTION", "apollo_financial")).strip(),
    }
    audit = apollo_corpus_bootstrap.audit_financial_corpus(payload)
    if audit.get("ok"):
        _FINANCIAL_CORPUS_STATUS.update(
            {
                "collection_exists": bool(audit.get("total_chunks", 0) or _FINANCIAL_CORPUS_STATUS.get("collection_exists")),
                "doc_count": int(audit.get("total_chunks") or _FINANCIAL_CORPUS_STATUS.get("doc_count") or 0),
                "real_chunk_count": int(audit.get("real_chunk_count") or 0),
                "synthetic_chunk_count": int(audit.get("synthetic_chunk_count") or 0),
                "real_ratio": float(audit.get("real_ratio") or 0.0),
            }
        )
    return jsonify(audit), (200 if audit.get("ok") else 500)


@app.route("/admin/background_pipeline/rag_sample", methods=["POST"])
def admin_background_pipeline_rag_sample():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    latest = apollo_nightly_pipeline.latest_status() or {}
    pointer_valid = bool(((latest.get("run_meta") or {}).get("current_pointer_valid")))
    if not pointer_valid:
        return jsonify({"ok": False, "error": "current_run_pointer_invalid"}), 409
    gather = (latest.get("stages") or {}).get("gather") or {}
    default_query = str(payload.get("query") or gather.get("topic") or "daily stock decision strategy with overnight holding risk management").strip()
    query = default_query or "daily stock decision strategy with overnight holding risk management"
    top_k = max(1, min(int(payload.get("top_k") or 5), 12))
    try:
        hits = _rag_dual_search(query=query, top_k=top_k)
    except Exception as exc:
        logging.exception("background_pipeline_rag_sample failed")
        return jsonify({"ok": False, "error": f"rag_sample_failed:{type(exc).__name__}:{exc}"}), 500
    compact_hits = []
    for hit in hits[:top_k]:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("text") or "").strip()
        compact_hits.append(
            {
                "score": float(hit.get("score") or 0.0),
                "source": str(hit.get("source") or "rag"),
                "title": str((hit.get("meta") or {}).get("title") or (hit.get("meta") or {}).get("source") or ""),
                "url": str((hit.get("meta") or {}).get("url") or ""),
                "snippet": (text[:420] + "...") if len(text) > 420 else text,
            }
        )
    return jsonify({"ok": True, "query": query, "top_k": top_k, "hits": compact_hits, "status_ref": {"run_id": latest.get("run_id"), "current_stage": latest.get("current_stage")}}), 200


@app.route("/admin/background_pipeline/scorecard", methods=["GET"])
def admin_background_pipeline_scorecard():
    auth = _require_local_token()
    if auth is not None:
        return auth
    window = max(1, min(int(request.args.get("window") or 7), 30))
    payload = apollo_nightly_pipeline.scorecard(window=window)
    return jsonify({"ok": True, "scorecard": payload}), 200


@app.route("/admin/background_pipeline/schedule", methods=["POST"])
def admin_background_pipeline_schedule():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    try:
        keep_nightly = bool(payload.get("keep_nightly", False))
        result = apollo_nightly_pipeline.schedule_background_run(payload, disable_nightly=not keep_nightly)
        return jsonify(result), 200
    except Exception as exc:
        logging.exception("background_pipeline_schedule failed")
        return jsonify({"ok": False, "error": f"background_pipeline_schedule_failed:{type(exc).__name__}:{exc}"}), 500


@app.route("/admin/background_pipeline/pause", methods=["POST"])
def admin_background_pipeline_pause():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    paused = bool(payload.get("paused", True))
    apollo_nightly_pipeline.set_pipeline_paused(paused)
    status = apollo_nightly_pipeline.mark_background_control_state("paused" if paused else "")
    return jsonify({"ok": True, "paused": paused, "status": status}), 200


@app.route("/admin/background_pipeline/stop", methods=["POST"])
def admin_background_pipeline_stop():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    apollo_nightly_pipeline.set_pipeline_stopped(True)
    status = apollo_nightly_pipeline.mark_background_control_state("stopped")
    if payload.get("reset"):
        # Clear current-run artifacts so the next tick restarts cleanly from gather.
        try:
            run_dir = apollo_nightly_pipeline._background_run_dir({})
            reset_names = {
                "status.json",
                "config.json",
                "summary.md",
                "pipeline_report.md",
                "00_focus_universe.json",
                "00_preflight.log",
                "01_data_gather.json",
                "01_data_gather.log",
                "01_gather.json",
                "01_gather.log",
                "02_ocr_ingest.json",
                "02_ocr_ingest.log",
                "03_hipporag.json",
                "03_hipporag.log",
                "04_self_check.json",
                "04_self_check.log",
            }
            for name in reset_names:
                target = run_dir / name
                if target.exists():
                    target.unlink()
            latest_status_path = apollo_nightly_pipeline.LATEST_STATUS_PATH
            if latest_status_path.exists():
                latest_status_path.unlink()
            nightly_lock = apollo_nightly_pipeline.LOCK_PATH
            if nightly_lock.exists():
                nightly_lock.unlink()
        except Exception:
            pass
    return jsonify({"ok": True, "stopped": True, "status": status}), 200


@app.route("/admin/background_pipeline/resume", methods=["POST"])
def admin_background_pipeline_resume():
    auth = _require_local_token()
    if auth is not None:
        return auth
    apollo_nightly_pipeline.set_pipeline_paused(False)
    apollo_nightly_pipeline.set_pipeline_stopped(False)
    status = apollo_nightly_pipeline.mark_background_control_state("")
    return jsonify({"ok": True, "resumed": True, "status": status}), 200


@app.route("/admin/background_pipeline/history", methods=["GET"])
def admin_background_pipeline_history():
    auth = _require_local_token()
    if auth is not None:
        return auth
    limit = max(1, min(int(request.args.get("limit") or 200), 1000))
    payload = apollo_nightly_pipeline.list_background_run_history(limit=limit)
    payload["ok"] = True
    return jsonify(payload), 200


@app.route("/admin/focus_universe/status", methods=["GET"])
def admin_focus_universe_status():
    auth = _require_local_token()
    if auth is not None:
        return auth
    state = apollo_focus_trading.load_state()
    return jsonify({"ok": True, "status": state}), 200


@app.route("/admin/focus_universe/learn", methods=["POST"])
def admin_focus_universe_learn():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(silent=True) or {}
    note = str(payload.get("note") or payload.get("reason") or "manual_admin_learn").strip()
    try:
        state = _sync_focus_state(note=note)
        return jsonify({"ok": True, "status": state}), 200
    except Exception as exc:
        logging.exception("focus_universe_learn failed")
        return jsonify({"ok": False, "error": f"focus_universe_learn_failed:{type(exc).__name__}:{exc}"}), 500


@app.route("/admin/focus_universe/paper_trade", methods=["POST"])
def admin_focus_universe_paper_trade():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(silent=True) or {}
    result = apollo_focus_trading.submit_paper_trade(
        action=str(payload.get("action") or "").strip(),
        ticker=str(payload.get("ticker") or "").strip(),
        thesis=str(payload.get("thesis") or "").strip(),
        confidence=float(payload.get("confidence") or 0.0),
        horizon=str(payload.get("horizon") or "").strip(),
        note=str(payload.get("note") or "").strip(),
        rag=APOLLO_RAG,
        memory_store=_MEMORY_STORE,
    )
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/admin/focus_universe/events", methods=["GET"])
def admin_focus_universe_events():
    auth = _require_local_token()
    if auth is not None:
        return auth
    limit = max(1, min(int(request.args.get("limit") or 20), 200))
    state = apollo_focus_trading.load_state()
    events = [row for row in list(state.get("events") or []) if isinstance(row, dict)]
    return jsonify(
        {
            "ok": True,
            "events": events[-limit:],
            "focus": dict(state.get("focus") or {}),
            "discussion_context": str(state.get("discussion_context") or ""),
        }
    ), 200


@app.route("/admin/swing/latest", methods=["GET"])
def admin_swing_latest():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = apollo_swing_study.latest_swing_study()
    payload["ok"] = True
    payload["state"] = apollo_swing_study.load_swing_state()
    return jsonify(payload), 200


@app.route("/admin/swing/run", methods=["POST"])
def admin_swing_run():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    try:
        result = apollo_swing_study.build_swing_study(payload)
        return jsonify(result), 200
    except Exception as exc:
        logging.exception("swing_run failed")
        return jsonify({"ok": False, "error": f"swing_run_failed:{type(exc).__name__}:{exc}"}), 500


@app.route("/admin/swing/history", methods=["GET"])
def admin_swing_history():
    auth = _require_local_token()
    if auth is not None:
        return auth
    limit = max(1, min(int(request.args.get("limit") or 20), 100))
    root = apollo_swing_study.RUNS_ROOT
    rows: List[Dict[str, Any]] = []
    try:
        for path in sorted(root.glob("*/*/swing_study.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "run_id": str(payload.get("run_id") or path.parent.name),
                    "created_at": str(payload.get("created_at") or ""),
                    "high_confidence": bool(payload.get("high_confidence")),
                    "best_ticker": str((payload.get("best_proposal") or {}).get("ticker") or ""),
                    "proposal_count": len(list(payload.get("proposals") or [])),
                    "path": str(path),
                }
            )
    except Exception:
        rows = []
    return jsonify({"ok": True, "runs": rows}), 200


@app.route("/admin/apollo/pretrade/status", methods=["GET"])
def admin_apollo_pretrade_status():
    auth = _require_local_token()
    if auth is not None:
        return auth
    status = apollo_pretrade_gate.latest_status()
    report = apollo_daily_report.get_daily_report(request.args.get("date") or None)
    return jsonify({"ok": True, "status": status, "daily_report": {"date": report.get("date"), "paths": report.get("paths")}}), 200


@app.route("/admin/apollo/pretrade/check", methods=["POST"])
def admin_apollo_pretrade_check():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    payload.setdefault("trigger", "api")
    payload.setdefault("force", True)
    result = apollo_pretrade_gate.run_pretrade_gate(payload)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/admin/apollo/daily_report", methods=["GET"])
def admin_apollo_daily_report():
    auth = _require_local_token()
    if auth is not None:
        return auth
    result = apollo_daily_report.get_daily_report(request.args.get("date") or None)
    return jsonify(result), (200 if result.get("ok") else 404)


@app.route("/admin/apollo/daily_report/finalize", methods=["POST"])
def admin_apollo_daily_report_finalize():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    result = apollo_daily_report.finalize_daily_report(payload.get("date") or None, note=str(payload.get("note") or ""))
    return jsonify({"ok": True, "report": result}), 200


def _schedule_file_preview(path: Path, max_chars: int = 6000) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... truncated ..."
        return {
            "name": path.name,
            "path": str(path),
            "kind": path.suffix.lower().lstrip(".") or "file",
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
            "text": text,
        }
    except Exception as exc:
        return {"name": path.name, "path": str(path), "error": str(exc)}


def _schedule_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _schedule_run_title(run_dir: Path, status: Dict[str, Any]) -> str:
    return str(
        status.get("run_id")
        or status.get("id")
        or status.get("cycle_id")
        or run_dir.name
    )


def _schedule_collect_runs(root: Path, status_names: List[str], limit: int = 20) -> List[Dict[str, Any]]:
    if not root.exists():
        return []
    candidates: List[Path] = []
    for name in status_names:
        candidates.extend(root.rglob(name))
    unique = sorted(set(candidates), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    runs: List[Dict[str, Any]] = []
    for status_path in unique[:limit]:
        run_dir = status_path.parent
        status = _schedule_json(status_path)
        files: List[Path] = [status_path]
        for pattern in ("*.md", "*.log", "*.stdout.log", "*.stderr.log", "*.txt", "*.json"):
            for path in sorted(run_dir.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                if path not in files:
                    files.append(path)
        previews = [_schedule_file_preview(path) for path in files[:8]]
        ok_value = status.get("ok")
        if ok_value is None:
            ok_value = status.get("completed") or status.get("trade_ready") or status.get("high_confidence")
        runs.append(
            {
                "run_id": _schedule_run_title(run_dir, status),
                "run_dir": str(run_dir),
                "mtime": datetime.fromtimestamp(status_path.stat().st_mtime).astimezone().isoformat(),
                "ok": ok_value,
                "summary": {
                    "current_stage": status.get("current_stage"),
                    "score": status.get("score") or status.get("overall_score"),
                    "best_ticker": (status.get("best_proposal") or {}).get("ticker") if isinstance(status.get("best_proposal"), dict) else status.get("best_ticker"),
                    "candidate": status.get("candidate"),
                    "blockers": status.get("blockers"),
                    "error": status.get("error"),
                },
                "files": previews,
            }
        )
    return runs


@app.route("/admin/apollo/schedule_runs", methods=["GET"])
def admin_apollo_schedule_runs():
    auth = _require_local_token()
    if auth is not None:
        return auth
    requested = str(request.args.get("job") or "").strip().lower()
    limit = max(1, min(int(request.args.get("limit") or 20), 50))
    jobs = [
        {
            "id": "nightly",
            "time": "01:00 CT",
            "title": "Nightly pipeline",
            "task_names": ["ApolloNightlyPipeline0100"],
            "root": LOG_DIR / "nightly",
            "status_names": ["status.json", "latest_status.json"],
            "description": "OCR/RAG ingestion and background research refresh.",
        },
        {
            "id": "trade_cycle",
            "time": "04:00 CT",
            "title": "Trade-cycle prep",
            "task_names": ["ApolloTradeCycle"],
            "root": LOG_DIR / "trade_cycle",
            "status_names": ["status.json"],
            "description": "Candidate context and paper-only trade-cycle readiness artifacts.",
        },
        {
            "id": "gate_check",
            "time": "06:15 CT",
            "title": "Overnight gate check",
            "task_names": ["ApolloOvernightGateCheck0615"],
            "root": LOG_DIR / "nightly",
            "status_names": ["latest_status.json", "status.json"],
            "description": "Apollo, Aegis, provider, and guard-state readiness.",
        },
        {
            "id": "market_study",
            "time": "07:30 CT",
            "title": "Market study",
            "task_names": ["ApolloMarketStudy0730"],
            "root": LOG_DIR / "market_study",
            "status_names": ["status.json", "market_study.json", "result.json"],
            "description": "Setup scoring and market-study refresh.",
        },
        {
            "id": "homework_trade",
            "time": "07:40 CT",
            "title": "Homework trade proof",
            "task_names": ["ApolloHomeworkTradeProof0740"],
            "root": LOG_DIR / "homework_trade",
            "status_names": ["status.json", "latest_homework_trade.json"],
            "description": "Daily paper-only homework-trade proof; writes plan/report/status artifacts and requires human approval for any action.",
            "commands": [
                {
                    "label": "Run homework trade proof",
                    "command": f'{sys.executable} -m Apollo.homework_trade_pipeline run',
                },
            ],
        },
        {
            "id": "daily_report_open",
            "time": "07:45 CT",
            "title": "Open daily report",
            "task_names": ["ApolloDailyReportOpen0745"],
            "root": LOG_DIR / "daily_reports",
            "status_names": ["apollo_daily_report.json", "latest_apollo_daily_report.json"],
            "description": "Create today's paper-only report with overnight learning, account state, and planned checks.",
            "commands": [
                {
                    "label": "Open report",
                    "command": f'{sys.executable} -m Apollo.daily_report open',
                },
            ],
        },
        {
            "id": "day_trade",
            "time": "08:15 CT",
            "title": "Simulated day-trade rehearsal",
            "task_names": [],
            "root": LOG_DIR / "day_trade_cycle",
            "status_names": ["status.json"],
            "description": "Queued paper_sim lifecycle test; Alpaca-paper only when credentials exist.",
            "commands": [
                {
                    "label": "Sky calendar runner",
                    "command": f'{sys.executable} "{_ENGINEERING_ROOT / "Sky" / "tools" / "run_apollo_day_trade_cycle_test.py"}" --run-id <run_id>',
                },
                {
                    "label": "Sky calendar scheduler",
                    "command": f'{sys.executable} "{_ENGINEERING_ROOT / "Sky" / "tools" / "schedule_apollo_day_trade_cycle_test.py"}" --date <yyyy-mm-dd> --time 08:15',
                },
                {
                    "label": "Direct Apollo rehearsal",
                    "command": f'{sys.executable} -m Apollo.day_trade_cycle --run-id <run_id>',
                },
            ],
        },
        {
            "id": "pretrade_gate",
            "time": "08:45-15:15 CT",
            "title": "Pre-trade gate",
            "task_names": ["ApolloPreTradePlanner"],
            "root": LOG_DIR / "pretrade_gate",
            "status_names": ["status.json", "latest_status.json"],
            "description": "Lightweight quote/news check before paper_sim actions; exits quickly when no action is due.",
            "commands": [
                {
                    "label": "Manual pre-trade check",
                    "command": f'{sys.executable} -m Apollo.pretrade_gate check --trigger manual --force',
                },
                {
                    "label": "Planner check",
                    "command": f'{sys.executable} -m Apollo.pretrade_gate check --trigger planner',
                },
            ],
        },
        {
            "id": "market_observation",
            "time": "08:30-15:00 CT",
            "title": "Market-hours observation",
            "task_names": [],
            "root": LOG_DIR / "swing_simulation",
            "status_names": ["status.json", "simulation.json", "result.json"],
            "description": "Candidate observation and simulated tracking during market hours.",
        },
        {
            "id": "swing_study",
            "time": "16:20 CT",
            "title": "Post-close swing study",
            "task_names": ["ApolloSwingStudy1620"],
            "root": apollo_swing_study.RUNS_ROOT,
            "status_names": ["swing_study.json", "latest_swing_study.json"],
            "description": "Post-close setup ranking for the Swing Trade Desk.",
        },
        {
            "id": "daily_report_final",
            "time": "16:35 CT",
            "title": "Finalize daily report",
            "task_names": ["ApolloDailyReportFinalize1635"],
            "root": LOG_DIR / "daily_reports",
            "status_names": ["apollo_daily_report.json", "latest_apollo_daily_report.json"],
            "description": "Finalize account, pre-trade gate, blockers, and next-day paper plan in Apollo's daily report.",
            "commands": [
                {
                    "label": "Finalize report",
                    "command": f'{sys.executable} -m Apollo.daily_report finalize',
                },
            ],
        },
        {
            "id": "ledger_review",
            "time": "End of day",
            "title": "Ledger and report review",
            "task_names": [],
            "root": LOG_DIR / "day_trade_cycle",
            "status_names": ["status.json"],
            "description": "Outcome, blocker, paper-order, and readiness-report review.",
        },
    ]
    for job in jobs:
        root = Path(job["root"])
        job["root"] = str(root)
        job["runs"] = _schedule_collect_runs(root, list(job["status_names"]), limit=limit)
        job["latest"] = job["runs"][0] if job["runs"] else None
    if requested:
        jobs = [job for job in jobs if job["id"] == requested]
    return jsonify({"ok": True, "jobs": jobs}), 200


@app.route("/admin/swing/outcomes", methods=["GET"])
def admin_swing_outcomes():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = {"limit": request.args.get("limit") or 100, "ticker": request.args.get("ticker") or ""}
    return jsonify(apollo_swing_study.swing_outcomes(payload)), 200


@app.route("/admin/swing/provider_health", methods=["GET"])
def admin_swing_provider_health():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = {"ticker": request.args.get("ticker") or ""}
    return jsonify(apollo_swing_study.swing_provider_health(payload)), 200


@app.route("/admin/swing/recompute_outcomes", methods=["POST"])
def admin_swing_recompute_outcomes():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    result = apollo_swing_study.recompute_outcomes(payload)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/admin/swing/proposal_decision", methods=["POST"])
def admin_swing_proposal_decision():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    result = apollo_swing_study.proposal_decision(payload)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/admin/event_impact/run", methods=["POST"])
def admin_event_impact_run():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    try:
        result = apollo_event_impact.build_event_impact(payload)
        return jsonify(result), 200
    except Exception as exc:
        logging.exception("event_impact_run failed")
        return jsonify({"ok": False, "error": f"event_impact_run_failed:{type(exc).__name__}:{exc}"}), 500


@app.route("/admin/event_impact/latest", methods=["GET"])
def admin_event_impact_latest():
    auth = _require_local_token()
    if auth is not None:
        return auth
    return jsonify(apollo_event_impact.latest_event_impact()), 200


@app.route("/admin/event_impact/history", methods=["GET"])
def admin_event_impact_history():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = {"limit": request.args.get("limit") or 20}
    return jsonify(apollo_event_impact.event_impact_history(payload)), 200


@app.route("/admin/event_impact/health", methods=["GET"])
def admin_event_impact_health():
    auth = _require_local_token()
    if auth is not None:
        return auth
    return jsonify(apollo_event_impact.event_impact_health()), 200


@app.route("/admin/market_forecast_turn/run", methods=["POST"])
def admin_market_forecast_turn_run():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    try:
        result = apollo_market_forecast_turn.build_market_forecast_turn(payload)
        return jsonify(result), 200
    except Exception as exc:
        logging.exception("market_forecast_turn_run failed")
        return jsonify({"ok": False, "error": f"market_forecast_turn_failed:{type(exc).__name__}:{exc}"}), 500


@app.route("/admin/market_forecast_turn/latest", methods=["GET"])
def admin_market_forecast_turn_latest():
    auth = _require_local_token()
    if auth is not None:
        return auth
    return jsonify(apollo_market_forecast_turn.latest_market_forecast_turn()), 200


@app.route("/admin/market_forecast_turn/health", methods=["GET"])
def admin_market_forecast_turn_health():
    auth = _require_local_token()
    if auth is not None:
        return auth
    return jsonify(apollo_market_forecast_turn.market_forecast_health()), 200


# ── OCR Dashboard ─────────────────────────────────────────────────────────────

_HW_PROFILE_CACHE: dict = {}

_TIER_CONFIGS: dict = {
    "tier1": {
        "label": "Full stack",
        "vram_req": "≥20 GB GPU",
        "grade_estimate": "97",
        "primary_model": "qwen3-vl:32b",
        "fallback_model": "qwen2.5vl:7b",
        "gb10_ocr_enabled": True,
        "engines": {
            "got_ocr2": "required", "trocr": "required", "deplot": "required",
            "paddleocr": "required", "mineru": "required",
        },
        "flags": {
            "AEGIS_DEPLOT_ENABLE": "1", "AEGIS_TROCR_ENABLE": "1",
            "AEGIS_QWEN_PRECLASSIFY_ENABLE": "1", "AEGIS_SCALE_ENSEMBLE_WORKERS": "4",
            "APOLLO_OCR_SURYA_COLUMN_SPLIT_ENABLED": "1",
        },
    },
    "tier2": {
        "label": "Mid-range",
        "vram_req": "8–19 GB GPU + 16 GB RAM",
        "grade_estimate": "88–91",
        "primary_model": "qwen2.5vl:7b",
        "fallback_model": "qwen2.5vl:3b",
        "gb10_ocr_enabled": True,
        "engines": {
            "got_ocr2": "required", "trocr": "required", "deplot": "required",
            "paddleocr": "required", "mineru": "required",
        },
        "flags": {
            "AEGIS_DEPLOT_ENABLE": "1", "AEGIS_TROCR_ENABLE": "1",
            "AEGIS_QWEN_PRECLASSIFY_ENABLE": "0", "AEGIS_SCALE_ENSEMBLE_WORKERS": "2",
            "APOLLO_OCR_SURYA_COLUMN_SPLIT_ENABLED": "0",
        },
    },
    "tier2_lite": {
        "label": "Lite",
        "vram_req": "4–7 GB GPU  or  ≥32 GB CPU",
        "grade_estimate": "80–84",
        "primary_model": "qwen2.5vl:3b",
        "fallback_model": "",
        "gb10_ocr_enabled": True,
        "engines": {
            "got_ocr2": "required", "trocr": "required", "deplot": "required",
            "paddleocr": "required", "mineru": "disabled",
        },
        "flags": {
            "AEGIS_DEPLOT_ENABLE": "1", "AEGIS_TROCR_ENABLE": "1",
            "AEGIS_QWEN_PRECLASSIFY_ENABLE": "0", "AEGIS_SCALE_ENSEMBLE_WORKERS": "1",
            "APOLLO_OCR_SURYA_COLUMN_SPLIT_ENABLED": "0",
        },
    },
    "tier3": {
        "label": "CPU / CI",
        "vram_req": "CPU only — no GPU required",
        "grade_estimate": "55–62",
        "primary_model": "",
        "fallback_model": "",
        "gb10_ocr_enabled": False,
        "engines": {
            "got_ocr2": "required", "trocr": "required", "deplot": "required",
            "paddleocr": "required", "mineru": "disabled",
        },
        "flags": {
            "AEGIS_DEPLOT_ENABLE": "1", "AEGIS_TROCR_ENABLE": "1",
            "AEGIS_QWEN_PRECLASSIFY_ENABLE": "0", "AEGIS_SCALE_ENSEMBLE_WORKERS": "1",
            "APOLLO_OCR_SURYA_COLUMN_SPLIT_ENABLED": "0",
        },
    },
}


def _get_hw_profile() -> dict:
    if _HW_PROFILE_CACHE:
        return _HW_PROFILE_CACHE
    try:
        import shutil
        import subprocess
        import platform

        def _nvidia_vram_gb():
            if not shutil.which("nvidia-smi"):
                return 0, ""
            try:
                mem = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                    text=True, timeout=8
                ).strip().splitlines()
                name = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    text=True, timeout=8
                ).strip().splitlines()
                return max(int(v.strip()) for v in mem if v.strip()) // 1024, (name[0].strip() if name else "GPU")
            except Exception:
                return 0, ""

        import psutil
        ram_gb = psutil.virtual_memory().total // (1024 ** 3)
        vram_gb, gpu_name = _nvidia_vram_gb()

        if vram_gb >= 20:
            tier = "tier1"
        elif vram_gb >= 8 and ram_gb >= 16:
            tier = "tier2"
        elif vram_gb >= 4 or ram_gb >= 32:
            tier = "tier2_lite"
        else:
            tier = "tier3"

        tier_labels = {
            "tier1": "Full stack",
            "tier2": "Mid-range",
            "tier2_lite": "Lite",
            "tier3": "CPU / CI",
        }
        grade_est = {"tier1": "97", "tier2": "88–91", "tier2_lite": "80–84", "tier3": "55–62"}

        _HW_PROFILE_CACHE.update({
            "vram_gb": vram_gb,
            "gpu_name": gpu_name,
            "ram_gb": ram_gb,
            "tier": tier,
            "tier_label": tier_labels.get(tier, tier),
            "grade_estimate": grade_est.get(tier, "?"),
            "os": platform.system(),
        })
    except Exception as exc:
        _HW_PROFILE_CACHE.update({"tier": "unknown", "error": str(exc)})
    return _HW_PROFILE_CACHE


def _probe_gateway_engine(gateway_url: str, path: str) -> dict:
    try:
        r = requests.get(f"{gateway_url.rstrip('/')}{path}", timeout=3)
        if r.status_code == 200:
            return r.json()
        return {"ok": False, "error": f"http_{r.status_code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _probe_ollama(ollama_url: str, model: str) -> dict:
    try:
        r = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=3)
        if r.status_code != 200:
            return {"ok": False, "status": "unreachable"}
        names = [m.get("name", "") for m in r.json().get("models", [])]
        present = any(model == n or model.split(":")[0] in n for n in names)
        return {"ok": present, "status": "loaded" if present else "not_pulled", "all_models": names}
    except Exception as exc:
        return {"ok": False, "status": "unreachable", "error": str(exc)}


@app.route("/admin/ocr/status", methods=["GET"])
def admin_ocr_status():
    gateway_url = os.getenv("APOLLO_GB10_OCR_GATEWAY_URL", "http://127.0.0.1:5221")
    ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_ocr_url = os.getenv("APOLLO_GB10_QWEN_OLLAMA_URL", "http://127.0.0.1:11435")

    hw = _get_hw_profile()

    # Tier simulation: override models and hw context when ?sim_tier= is provided
    sim_tier = request.args.get("sim_tier", "").strip()
    if sim_tier and sim_tier in _TIER_CONFIGS:
        tier_cfg = _TIER_CONFIGS[sim_tier]
        hw_context = {
            "tier": sim_tier,
            "tier_label": tier_cfg["label"],
            "grade_estimate": tier_cfg["grade_estimate"],
            "vram_req": tier_cfg["vram_req"],
            "simulated": True,
            # still pass real hw for reference
            "actual_tier": hw.get("tier"),
            "actual_vram_gb": hw.get("vram_gb"),
            "actual_ram_gb": hw.get("ram_gb"),
            "actual_gpu_name": hw.get("gpu_name"),
            "os": hw.get("os"),
        }
        primary_model = tier_cfg["primary_model"]
        fallback_model = tier_cfg["fallback_model"]
    else:
        tier_cfg = _TIER_CONFIGS.get(hw.get("tier", ""), {})
        hw_context = hw
        primary_model = os.getenv("APOLLO_GB10_QWEN_OCR_MODEL", "")
        fallback_model = os.getenv("APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL", "")
        sim_tier = None

    engines = {
        "got_ocr2":    _probe_gateway_engine(gateway_url, "/ocr/got/health"),
        "trocr":       _probe_gateway_engine(gateway_url, "/ocr/handwriting/health"),
        "deplot":      _probe_gateway_engine(gateway_url, "/ocr/chart/health"),
        "paddleocr":   _probe_gateway_engine(gateway_url, "/ocr/paddle/health"),
        "mineru":      _probe_gateway_engine(gateway_url, "/ocr/mineru/health"),
    }

    qwen_primary = _probe_ollama(ollama_ocr_url, primary_model) if primary_model else {"ok": False, "status": "not_configured"}
    qwen_fallback = _probe_ollama(ollama_url, fallback_model) if fallback_model else {"ok": False, "status": "not_configured"}

    try:
        pipeline_r = requests.get(f"http://127.0.0.1:{os.getenv('PORT', '5218')}/admin/background_pipeline/status", timeout=2)
        pipeline = pipeline_r.json() if pipeline_r.status_code == 200 else {"ok": False}
    except Exception:
        pipeline = {"ok": False, "error": "unreachable"}

    from runtime_metrics import snapshot as metrics_snap
    metrics = metrics_snap()

    return jsonify({
        "ts": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "hardware": hw_context,
        "sim_mode": bool(sim_tier),
        "tier_config": {
            "engines": tier_cfg.get("engines", {}),
            "flags": tier_cfg.get("flags", {}),
            "primary_model": tier_cfg.get("primary_model", primary_model),
            "fallback_model": tier_cfg.get("fallback_model", fallback_model),
            "gb10_ocr_enabled": tier_cfg.get("gb10_ocr_enabled", True),
        },
        "engines": engines,
        "qwen": {
            "primary": {"model": primary_model, **qwen_primary},
            "fallback": {"model": fallback_model, **qwen_fallback},
        },
        "pipeline": pipeline,
        "metrics": metrics,
    })


@app.route("/admin/ocr/dashboard", methods=["GET"])
def admin_ocr_dashboard():
    hw = _get_hw_profile()
    return render_template("ocr_dashboard.html", hw=hw, agent=AGENT_NAME)


# ── end OCR Dashboard ──────────────────────────────────────────────────────────

# ── Simulation Dashboard ───────────────────────────────────────────────────────


@app.route("/simulation", methods=["GET"])
def simulation_dashboard():
    return render_template("simulation_dashboard.html", agent=AGENT_NAME)


@app.route("/api/simulation/run", methods=["POST"])
def api_simulation_run():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        from Apollo.swing_simulation import run_simulation, run_simulation_from_latest_homework
        if payload.get("ticker") and payload.get("entry_zone"):
            result = run_simulation(payload, lookback_days=int(payload.get("lookback_days") or 90), save=True)
        else:
            result = run_simulation_from_latest_homework(save=True)
        if result is None:
            return jsonify({"ok": False, "error": "no_candidate"}), 404
        # Strip trace for response (large), keep candles for chart
        resp = {k: v for k, v in result.items() if k != "trace"}
        return jsonify({"ok": True, "simulation": resp}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/prepare", methods=["POST"])
def api_simulation_prepare():
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(apollo_simulation_execution.create_simulation_execution_plan(payload)), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution", methods=["GET"])
def api_simulation_execution_list():
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        try:
            limit = int(request.args.get("limit", "50"))
        except Exception:
            limit = 50
        return jsonify(apollo_simulation_execution.list_simulation_execution_plans(limit=limit)), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>", methods=["GET"])
def api_simulation_execution_get(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        plan = apollo_simulation_execution.get_simulation_execution_plan(execution_id)
        if not plan:
            return jsonify({"ok": False, "error": "plan_not_found"}), 404
        return jsonify({"ok": True, "execution_plan": plan}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>/approve", methods=["POST"])
def api_simulation_execution_approve(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    context = _simulation_request_context()
    approved_by = payload.get("approved_by", "")
    if not approved_by and context.get("token_hint"):
        approved_by = f"token:{context['token_hint']}"
    try:
        result = apollo_simulation_execution.approve_simulation_execution_plan(
            execution_id=execution_id,
            approved_by=approved_by,
            approval_notes=payload.get("approval_notes", ""),
            approved_from=context.get("remote_ip", ""),
            approved_client=context.get("user_agent", ""),
            approval_source="api",
        )
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>/submit", methods=["POST"])
def api_simulation_execution_submit(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    payload = request.get_json(force=True, silent=True) or {}
    context = _simulation_request_context()
    submitted_by = payload.get("submitted_by", "")
    if not submitted_by and context.get("token_hint"):
        submitted_by = f"token:{context['token_hint']}"
    force_without_approval = str(payload.get("force_without_approval", "false")).strip().lower() in {
        "1", "true", "yes", "on", "y", "t"
    }
    dry_run = payload.get("dry_run")
    if isinstance(dry_run, str):
        dry_run = dry_run.strip().lower() in {"1", "true", "yes", "on", "y", "t"}
    try:
        result = apollo_simulation_execution.submit_simulation_execution_plan(
            execution_id=execution_id,
            force_without_approval=force_without_approval,
            dry_run=dry_run,
            executor=payload.get("executor"),
            submitted_by=submitted_by,
            submitted_from=context.get("remote_ip", ""),
            submitted_client=context.get("user_agent", ""),
            submission_source=payload.get("submission_source", "api"),
        )
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>/status", methods=["POST"])
def api_simulation_execution_status(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution as apollo_simulation_execution

        result = apollo_simulation_execution.sync_simulation_execution_plan_with_broker(execution_id=execution_id)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/reconcile", methods=["POST"])
def api_simulation_execution_reconcile():
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        payload = request.get_json(force=True, silent=True) or {}
        context = _simulation_request_context()
        requested_by = str(payload.get("requested_by", "") or "").strip()
        if not requested_by and context.get("token_hint"):
            requested_by = f"token:{context['token_hint']}"

        max_plans = payload.get("max_plans")
        try:
            max_plans_int = int(max_plans) if max_plans is not None else None
        except Exception:
            max_plans_int = None

        result = apollo_simulation_execution.reconcile_simulation_execution_orders(
            max_plans=max_plans_int,
            requested_by=requested_by,
            requested_from=context.get("remote_ip", ""),
            requested_client=context.get("user_agent", ""),
            source=str(payload.get("source") or "api"),
        )
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/latest", methods=["GET"])
def api_simulation_latest():
    try:
        from Apollo.swing_simulation import get_latest_simulation
        sim = get_latest_simulation()
        if sim is None:
            return jsonify({"ok": False, "error": "no_simulation_yet"}), 404
        resp = {k: v for k, v in sim.items() if k != "trace"}
        return jsonify({"ok": True, "simulation": resp}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/account", methods=["GET"])
def api_simulation_account():
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo.swing_simulation import get_simulation_account

        latest = apollo_swing_study.latest_swing_study()
        return jsonify(get_simulation_account(latest_study=latest, save_report=True)), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/list", methods=["GET"])
def api_simulation_list():
    try:
        from Apollo.swing_simulation import list_simulations
        sims = list_simulations(limit=50)
        return jsonify({"ok": True, "simulations": [
            {k: v for k, v in s.items() if k not in ("trace", "candles")}
            for s in sims
        ]}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/live/<ticker>", methods=["GET"])
def api_simulation_live(ticker: str):
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
        hist = t.history(period="5d")
        candles = []
        for ts, row in hist.iterrows():
            candles.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        live_price = candles[-1]["close"] if candles else None
        return jsonify({"ok": True, "ticker": ticker.upper(), "live_price": live_price, "candles": candles}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/homework/latest", methods=["GET"])
def api_homework_latest():
    try:
        from Apollo.homework_trade_pipeline import RUNS_ROOT
        latest_path = RUNS_ROOT / "latest_homework_trade.json"
        if not latest_path.exists():
            return jsonify({"ok": False, "error": "no_homework_run_yet"}), 404
        data = json.loads(latest_path.read_text(encoding="utf-8"))
        plan = data.get("trade_plan") or {}
        study = data.get("swing_study") or {}
        proposals = [
            {k: v for k, v in p.items() if k not in ("source_links",)}
            for p in list(study.get("proposals") or [])[:10]
        ]
        return jsonify({
            "ok": True,
            "run_id": data.get("run_id"),
            "created_at": data.get("created_at"),
            "trade_plan": plan,
            "proposals": proposals,
            "market_regime": study.get("market_regime"),
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── end Simulation Dashboard ───────────────────────────────────────────────────


def _resolve_port(default: int = 5010) -> int:
    for key in ("AGENT_PORT", "APOLLO_PORT", "PORT"):
        raw = os.getenv(key)
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return default


if __name__ == "__main__":
    debug = os.getenv("GOV_DEBUG", "0") == "1" or os.getenv("SKY_DEBUG", "0") == "1"
    debug = debug or APP_CONFIG.DEBUG
    port = _resolve_port(APP_CONFIG.PORT)
    host = os.getenv("HOST", APP_CONFIG.HOST)
    logging.info("Apollo route map ready: swing_routes=%s", [rule.rule for rule in app.url_map.iter_rules() if "swing" in rule.rule])
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)

