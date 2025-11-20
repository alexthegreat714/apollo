import json
import logging
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
import re
import unicodedata
from difflib import SequenceMatcher

import requests
from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS

# ensure local imports work when running as a script
import os as _local_os, sys as _local_sys
_local_sys.path.insert(0, _local_os.path.dirname(__file__))

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common.deepcoder import run as deepcoder_run
from common.dialogue_broker import DialogueBroker
from common.dialogue_orchestrator import run_dialogue_test
from common.query_client import query_model
from common.rag_store import AgentRAG
from Apollo import rag_routes as apollo_rag_routes
from Apollo.autorun_supervisor import AutoRunSupervisor
from Apollo.reflection import log_reflection, summarize_reflections
from apollo_rag_governance.action_executor import get_executor, ActionExecutor
from apollo_rag_governance.proposal_engine import RAGProposalEngine
from Apollo.runtime_metrics import (
    record_autorun_call,
    record_chat,
    record_reflection_call,
    snapshot as metrics_snapshot,
)


def _load_env_file() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()

app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")
CORS(app)
logging.basicConfig(level=logging.INFO)
broker = DialogueBroker()

AGENT_NAME = "Apollo"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
# Use APOLLO_GENERATION_MODEL for the answering LLM
OLLAMA_MODEL = os.getenv("APOLLO_GENERATION_MODEL", "Fino1-8B.Q6_K")
OWUI_URL = os.getenv("OWUI_URL", "http://127.0.0.1:3000")
OWUI_MODEL = os.getenv("OWUI_MODEL", "Fino1-8B.Q6_K")

APOLLO_RAG = AgentRAG("Apollo")
TRACE_DIR = Path(APOLLO_RAG.get_collection_path()) / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)
TRACE_FILE = TRACE_DIR / "chat_traces.jsonl"
SNAPSHOT_GUARD_SECONDS = 5
LAST_ACTIVITY_TS = time.time()

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

# --- console blueprint (Phase 7) ---
try:
    from apollo_console.routes import bp_console
    app.register_blueprint(bp_console)
    print("[console] blueprint registered")
except Exception as e:
    print(f"[console] registration failed: {e}")

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

def _dedupe(seq):
    seen = set()
    ordered = []
    for item in seq:
        if item and item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


# --- Task 02: context curation helpers ---
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
    data["needs_deepcoder"] = bool(data.get("needs_deepcoder"))
    data["needs_long_reflection"] = bool(data.get("needs_long_reflection"))
    data["reflect"] = bool(data.get("reflect"))
    return data


def run_fino_classifier(message: str) -> dict:
    prompt = (
        "You are a financial classifier. Return a JSON object like:\n"
        '{"intent": "markets|personal_finance|risk|tax|investing|macro|unknown",\n'
        ' "needs_deepcoder": true|false,\n'
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

    res = APOLLO_RAG.search(query=message, top_k=search_top, kinds=kinds)
    hits = res.get("results", [])
    # Filter by source for specific financial intents
    if intent == "regulation":
        hits = [
            h
            for h in hits
            if (h.get("meta") or {}).get("kind") == "regulation"
        ]
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
) -> None:
    record = {
        "ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        "intent": intent,
        "depth": depth,
        "local_tags": local_tags,
        "gemma_classifier_used": gemma_classifier_used,
        "gemma_classifier_output": gemma_classifier_output,
        "message": message,
        "rag_hits": rag_hits,
        "deepcoder_used": deep_used,
        "latency_ms": round(latency_ms, 2),
        "chain": chain,
        "context_chars": context_chars,
        "dedupe_removed": dedupe_removed,
    }
    with open(TRACE_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _can_snapshot() -> bool:
    return (time.time() - LAST_ACTIVITY_TS) >= SNAPSHOT_GUARD_SECONDS


@app.before_request
def _track_activity():
    global LAST_ACTIVITY_TS
    if request.endpoint not in {"rag_snapshot", "rag_restore"}:
        LAST_ACTIVITY_TS = time.time()


@app.route("/")
def home():
    return render_template("index.html", agent=AGENT_NAME)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.utcnow().isoformat() + "Z"})


@app.route("/meta")
def meta():
    ollama_ok, ollama_version = False, None
    try:
        r = requests.get(f"{OLLAMA_URL}/api/version", timeout=2)
        if r.ok:
            ollama_ok = True
            ollama_version = r.json().get("version")
    except Exception:
        pass

    return jsonify(
        {
            "agent": AGENT_NAME,
            "provider": LLM_PROVIDER,
            "ollama": {"url": OLLAMA_URL, "model": OLLAMA_MODEL, "up": ollama_ok, "version": ollama_version},
            "owui": {"url": OWUI_URL, "model": OWUI_MODEL},
            "rag_governance": {
                "rag_proposals_enabled": True,
                "auto_changes_allowed": False,
                "requires_confirmation": True,
            },
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
    if intent not in INTENT_VALUES or intent == "unknown":
        intent = "markets"  # Default to markets for financial agent

    explicit_depth = (data.get("depth") or "").strip().lower()
    if explicit_depth in {"fast", "normal", "deep"}:
        depth = explicit_depth

    fino_classifier_used = True
    fino_output = run_fino_classifier(user_msg)
    if fino_output and intent == "unknown":
        intent = fino_output.get("intent", intent)
        if intent not in INTENT_VALUES:
            intent = "unknown"
    if fino_output and fino_output.get("needs_long_reflection"):
        depth = "deep"
    # Financial analysis often benefits from deep mode
    if fino_output and fino_output.get("needs_deepcoder") and intent in ("risk", "investing", "macro"):
        depth = "deep"

    hits, requested_k = gather_hits(intent, user_msg, depth)
    texts__t02, context_chars__t02 = curate_hits__t02(hits, intent=intent, char_cap=1500)
    ctx_lines = [f"- {_fix_display(t)}" for t in texts__t02]
    dedupe_removed__t02 = max(0, len(hits) - len(texts__t02))
    tool_block = ""
    deep_used = False
    # Enable deep analysis for complex financial intents
    allow_deepcoder = depth != "fast" and (depth == "deep" or intent in ("risk", "investing", "macro"))
    if allow_deepcoder:
        tool_block = deepcoder_run(user_msg, intent, hits)
        deep_used = bool(tool_block)

    blocks = []
    if ctx_lines:
        blocks.append("Context:\n" + "\n".join(ctx_lines))
    if tool_block:
        blocks.append("[DeepCoder]\n" + tool_block)
    if depth == "fast":
        instruction = (
            "Instruction: Provide a concise answer (1-2 sentences). Use context only if essential."
            " If uncertain, say what's missing.\nUser: "
        )
    elif depth == "deep":
        instruction = (
            "Instruction: Think step-by-step before responding. Reference context/tool insights and mention unknowns."
            "\nUser: "
        )
    else:
        instruction = (
            "Instruction: Answer briefly (3-6 sentences). Use the context/tool block if it helps."
            " If uncertain, say what's missing.\nUser: "
        )
    blocks.append(instruction + user_msg)
    prompt = "\n\n".join(blocks)
    reply = query_model(prompt)

    latency_ms = (time.perf_counter() - start) * 1000.0
    record_chat(latency_ms, len(hits), deep_used, requested_k, depth, fino_classifier_used)
    chain = f"{intent}:{depth}" + ("->deepcoder" if deep_used else "") + "->fino"
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

    reasoning = "\n".join(blocks[:-1]) if blocks[:-1] else "(none)"
    return jsonify({"reasoning": reasoning, "reply": reply, "intent": intent, "depth": depth})


@app.route("/metrics")
def metrics():
    return jsonify(metrics_snapshot())


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



# =============================================================================
# RAG GOVERNANCE ENDPOINTS (Phase 2)
# =============================================================================

@app.route("/rag/proposals", methods=["GET"])
def rag_proposals():
    """Get all pending governance proposals."""
    executor = get_executor()
    proposals = executor.get_pending_proposals()
    return jsonify({"ok": True, "proposals": proposals})


@app.route("/rag/proposals", methods=["POST"])
def rag_create_proposal():
    """Generate a new governance proposal from current stats."""
    engine = RAGProposalEngine()
    
    # You can pass stats in the request body
    body = request.get_json(silent=True) or {}
    if body.get("retrieval_stats"):
        engine.evaluate_retrieval_stats(body["retrieval_stats"])
    if body.get("metadata_distribution"):
        engine.evaluate_metadata_distribution(body["metadata_distribution"])
    
    proposal = engine.generate_action_proposal()
    
    # Store in executor
    executor = get_executor()
    proposal_id = executor.store_proposal(proposal)
    
    return jsonify({
        "ok": True, 
        "proposal_id": proposal_id,
        "proposal": proposal.to_dict()
    })


@app.route("/rag/approve", methods=["POST"])
def rag_approve():
    """Approve a specific action within a proposal."""
    body = request.get_json(silent=True) or {}
    proposal_id = body.get("proposal_id")
    action_id = body.get("action_id")
    
    if not proposal_id or not action_id:
        return jsonify({"ok": False, "error": "proposal_id and action_id required"}), 400
    
    executor = get_executor()
    success = executor.approve_action(proposal_id, action_id)
    
    if success:
        return jsonify({"ok": True, "message": f"Action {action_id} approved"})
    else:
        return jsonify({"ok": False, "error": "Action not found"}), 404


@app.route("/rag/reject", methods=["POST"])
def rag_reject():
    """Reject a specific action within a proposal."""
    body = request.get_json(silent=True) or {}
    proposal_id = body.get("proposal_id")
    action_id = body.get("action_id")
    
    if not proposal_id or not action_id:
        return jsonify({"ok": False, "error": "proposal_id and action_id required"}), 400
    
    executor = get_executor()
    success = executor.reject_action(proposal_id, action_id)
    
    if success:
        return jsonify({"ok": True, "message": f"Action {action_id} rejected"})
    else:
        return jsonify({"ok": False, "error": "Action not found"}), 404


@app.route("/rag/apply", methods=["POST"])
def rag_apply():
    """Execute all approved actions in a proposal."""
    body = request.get_json(silent=True) or {}
    proposal_id = body.get("proposal_id")
    
    if not proposal_id:
        return jsonify({"ok": False, "error": "proposal_id required"}), 400
    
    executor = get_executor()
    results = executor.execute_approved_actions(proposal_id)
    
    return jsonify({
        "ok": True,
        "proposal_id": proposal_id,
        "results": results
    })


@app.route("/rag/delete_proposal", methods=["POST"])
def rag_delete_proposal():
    """Delete a proposal entirely."""
    body = request.get_json(silent=True) or {}
    proposal_id = body.get("proposal_id")
    
    if not proposal_id:
        return jsonify({"ok": False, "error": "proposal_id required"}), 400
    
    executor = get_executor()
    success = executor.delete_proposal(proposal_id)
    
    if success:
        return jsonify({"ok": True, "message": f"Proposal {proposal_id} deleted"})
    else:
        return jsonify({"ok": False, "error": "Proposal not found"}), 404



# =============================================================================
# CORPUS EXPANSION ENDPOINTS (Phase 4)
# =============================================================================

@app.route("/rag/expand", methods=["POST"])
def rag_expand():
    """Expand corpus from a URL."""
    from apollo_rag.corpus_expander import get_expander
    
    body = request.get_json(silent=True) or {}
    url = body.get("url") or body.get("query")
    
    if not url:
        return jsonify({"ok": False, "error": "url or query required"}), 400
    
    expander = get_expander()
    
    if url.startswith("http"):
        result = expander.expand_from_url(url)
    else:
        # Treat as topic for auto-expansion
        result = expander.expand_auto(url)
    
    return jsonify({"ok": True, **result})


@app.route("/rag/expand/pdf", methods=["POST"])
def rag_expand_pdf():
    """Expand corpus from a PDF using OCR."""
    from apollo_rag.corpus_expander import get_expander
    
    body = request.get_json(silent=True) or {}
    pdf_path = body.get("path")
    
    if not pdf_path:
        return jsonify({"ok": False, "error": "path required"}), 400
    
    expander = get_expander()
    result = expander.expand_from_pdf(pdf_path)
    
    return jsonify({"ok": True, **result})


@app.route("/rag/expand/auto", methods=["POST"])
def rag_expand_auto():
    """Auto-expand corpus for a topic."""
    from apollo_rag.corpus_expander import get_expander
    
    body = request.get_json(silent=True) or {}
    topic = body.get("topic")
    
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400
    
    expander = get_expander()
    result = expander.expand_auto(topic)
    
    return jsonify({"ok": True, **result})


# =============================================================================
# BACKGROUND MONITOR ENDPOINTS (Phase 6)
# =============================================================================

@app.route("/rag/monitor/start", methods=["POST"])
def rag_monitor_start():
    """Start the background RAG monitor."""
    from apollo_rag.monitor import start_background_monitor
    
    body = request.get_json(silent=True) or {}
    interval = body.get("interval_minutes", 10)
    
    monitor = start_background_monitor(interval)
    
    return jsonify({
        "ok": True,
        "message": f"Monitor started with {interval} minute interval"
    })


@app.route("/rag/monitor/stop", methods=["POST"])
def rag_monitor_stop():
    """Stop the background RAG monitor."""
    from apollo_rag.monitor import get_monitor
    
    monitor = get_monitor()
    monitor.stop()
    
    return jsonify({"ok": True, "message": "Monitor stopped"})


@app.route("/rag/monitor/status", methods=["GET"])
def rag_monitor_status():
    """Get monitor status and last report."""
    from apollo_rag.monitor import get_monitor
    
    monitor = get_monitor()
    last_report = monitor.get_last_report()
    
    return jsonify({
        "ok": True,
        "running": monitor._running,
        "last_report": last_report
    })


@app.route("/rag/monitor/check", methods=["POST"])
def rag_monitor_check():
    """Run a health check manually."""
    from apollo_rag.monitor import get_monitor
    
    monitor = get_monitor()
    report = monitor.run_health_check()
    
    return jsonify({"ok": True, **report})


# =============================================================================
# SKILL ROUTING ENDPOINT (Phase 3)
# =============================================================================

@app.route("/skills/route", methods=["POST"])
def skills_route():
    """Route a query through the skill system."""
    from apollo_skills.router import route_skill
    
    body = request.get_json(silent=True) or {}
    query = body.get("query", "")
    intent = body.get("intent", "investing")
    context = body.get("context", "")
    user_data = body.get("user_data", {})
    
    if not query:
        return jsonify({"ok": False, "error": "query required"}), 400
    
    result = route_skill(intent, query, context, user_data)
    
    return jsonify({"ok": True, **result})


@app.route("/skills/list", methods=["GET"])
def skills_list():
    """List available skills."""
    from apollo_skills.router import get_router
    
    router = get_router()
    skills = router.list_skills()
    
    return jsonify({"ok": True, "skills": skills})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=False, threaded=True)
