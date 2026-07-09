from __future__ import annotations

import argparse
import ast
import ctypes
import html
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from threading import Thread
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from common.agent_manifest_schema import load_agent_manifest
from common.agent_ports import resolve_runtime_port
from common.gpu_probe import verify_gpu_backed_target
from common.system_pressure import evaluate_memory_pressure


_APOLLO_ROOT = Path(__file__).resolve().parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent
load_dotenv(_APOLLO_ROOT / ".env", override=False)

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

if str(_ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINEERING_ROOT))


RUNS_ROOT = Path(os.getenv("APOLLO_NIGHTLY_LOG_DIR", str(_APOLLO_ROOT / "logs" / "nightly")))
LATEST_STATUS_PATH = RUNS_ROOT / "latest_status.json"
CURRENT_RUN_POINTER_PATH = RUNS_ROOT / "background_current_pointer.json"
LOCK_PATH = RUNS_ROOT / "nightly.lock"
FOCUS_UNIVERSE_STATE_PATH = RUNS_ROOT / "background" / "focus_universe_state.json"
NIGHTLY_OCR_RAG_DIR = Path(os.getenv("APOLLO_NIGHTLY_OCR_RAG_DIR", str(_APOLLO_ROOT / "logs" / "nightly_ocr_rag")))
DEFAULT_TASK_NAME = os.getenv("APOLLO_NIGHTLY_TASK_NAME", "ApolloNightlyPipeline0100")
DEFAULT_BACKGROUND_TASK_NAME = os.getenv("APOLLO_BACKGROUND_TASK_NAME", "ApolloBackgroundPipeline")
DEFAULT_START_LOCAL = os.getenv("APOLLO_NIGHTLY_START_LOCAL", "01:00")
DEFAULT_BACKGROUND_INTERVAL_MIN = int(os.getenv("APOLLO_BACKGROUND_INTERVAL_MIN", "10"))
DEFAULT_TOPIC = os.getenv("APOLLO_NIGHTLY_TOPIC", "one discretionary stock trade decision per day with overnight holding allowed")
DEFAULT_EQUITY_NEWS_TICKERS = [t.strip() for t in os.getenv("APOLLO_EQUITY_NEWS_TICKERS", "NVDA,AMD,AVGO,MSFT,AMZN").split(",") if t.strip()]
DEFAULT_MAX_ARTICLES = int(os.getenv("APOLLO_NIGHTLY_MAX_ARTICLES", "4"))
DEFAULT_OCR_MAX_PAGES = int(os.getenv("APOLLO_NIGHTLY_OCR_MAX_PAGES", "4"))
DEFAULT_OCR_MAX_CHARS = int(os.getenv("APOLLO_NIGHTLY_OCR_MAX_CHARS", "16000"))
DEFAULT_HIPPORAG_BATCH_SIZE = int(os.getenv("APOLLO_NIGHTLY_HIPPORAG_BATCH_SIZE", "25"))
DEFAULT_HIPPORAG_USE_LLM = os.getenv("APOLLO_NIGHTLY_HIPPORAG_USE_LLM", "1") == "1"
DEFAULT_HIPPORAG_LLM_MODEL = os.getenv("APOLLO_HIPPORAG_LLM_MODEL", os.getenv("APOLLO_FAST_LLM_MODEL", "gemma3:12b"))
DEFAULT_HIPPORAG_LLM_TIMEOUT_SEC = int(os.getenv("APOLLO_HIPPORAG_LLM_TIMEOUT_SECS", "90"))
DEFAULT_HIPPORAG_LLM_NUM_PREDICT = int(os.getenv("APOLLO_HIPPORAG_LLM_NUM_PREDICT", "300"))
DEFAULT_HOMEWORK_LLM_TIMEOUT_SEC = int(os.getenv("APOLLO_HOMEWORK_LLM_TIMEOUT_SECS", "480"))
DEFAULT_GATHER_TIMEOUT_SEC = int(os.getenv("APOLLO_NIGHTLY_GATHER_TIMEOUT_SEC", "3600"))
DEFAULT_OCR_TIMEOUT_SEC = int(os.getenv("APOLLO_NIGHTLY_OCR_TIMEOUT_SEC", "7200"))
DEFAULT_HIPPORAG_TIMEOUT_SEC = int(os.getenv("APOLLO_NIGHTLY_HIPPORAG_TIMEOUT_SEC", "23400"))
DEFAULT_SELF_CHECK_TIMEOUT_SEC = int(os.getenv("APOLLO_NIGHTLY_SELF_CHECK_TIMEOUT_SEC", "900"))
DEFAULT_OVERALL_TIMEOUT_SEC = int(os.getenv("APOLLO_NIGHTLY_OVERALL_TIMEOUT_SEC", "28800"))
DEFAULT_PREFLIGHT_MAX_WAIT_SEC = int(os.getenv("APOLLO_NIGHTLY_PREFLIGHT_MAX_WAIT_SEC", "1800"))
DEFAULT_PREFLIGHT_RETRY_INTERVAL_SEC = int(os.getenv("APOLLO_NIGHTLY_PREFLIGHT_RETRY_INTERVAL_SEC", "60"))
DEFAULT_LOCK_STALE_SEC = int(os.getenv("APOLLO_NIGHTLY_LOCK_STALE_SEC", "43200"))
DEFAULT_ALLOW_CPU_FALLBACK = os.getenv("APOLLO_NIGHTLY_ALLOW_CPU_FALLBACK", "0") == "1"
DEFAULT_BACKGROUND_MAX_ACTIVE_MODELS = int(os.getenv("APOLLO_BACKGROUND_MAX_ACTIVE_MODELS", "1"))
DEFAULT_BACKGROUND_HIPPORAG_STEP_CHUNKS = int(os.getenv("APOLLO_BACKGROUND_HIPPORAG_STEP_CHUNKS", "8"))
DEFAULT_BACKGROUND_OCR_DOCS_PER_TICK = int(os.getenv("APOLLO_BACKGROUND_OCR_DOCS_PER_TICK", "1"))
DEFAULT_BACKGROUND_RUN_ID = os.getenv("APOLLO_BACKGROUND_RUN_ID", "apollo_background_current")
DEFAULT_BACKGROUND_MIN_IDLE_SEC = int(os.getenv("APOLLO_BACKGROUND_MIN_IDLE_SEC", "300"))
DEFAULT_BACKGROUND_GATHER_TIMEOUT_SEC = int(os.getenv("APOLLO_BACKGROUND_GATHER_TIMEOUT_SEC", "720"))
DEFAULT_BACKGROUND_OCR_STEP_TIMEOUT_SEC = int(os.getenv("APOLLO_BACKGROUND_OCR_STEP_TIMEOUT_SEC", "300"))
DEFAULT_BACKGROUND_HIPPORAG_STEP_TIMEOUT_SEC = int(os.getenv("APOLLO_BACKGROUND_HIPPORAG_STEP_TIMEOUT_SEC", "900"))
DEFAULT_BACKGROUND_SELF_CHECK_TIMEOUT_SEC = int(os.getenv("APOLLO_BACKGROUND_SELF_CHECK_TIMEOUT_SEC", "120"))
DEFAULT_LOCK_HEARTBEAT_STALE_SEC = int(os.getenv("APOLLO_LOCK_HEARTBEAT_STALE_SEC", "600"))
DEFAULT_MEMORY_GUARD_ENABLED = os.getenv("APOLLO_MEMORY_GUARD_ENABLED", "1") == "1"
DEFAULT_MEMORY_MIN_AVAILABLE_MB = int(os.getenv("APOLLO_MEMORY_MIN_AVAILABLE_MB", "4096"))
DEFAULT_MEMORY_MAX_USED_PERCENT = float(os.getenv("APOLLO_MEMORY_MAX_USED_PERCENT", "92"))
DEFAULT_MEMORY_MAX_VMMEM_MB = int(os.getenv("APOLLO_MEMORY_MAX_VMMEM_MB", "12288"))
DEFAULT_MEMORY_MAX_OLLAMA_RUNNERS = int(os.getenv("APOLLO_MEMORY_MAX_OLLAMA_RUNNERS", "6"))
DEFAULT_MEMORY_WAIT_SEC = int(os.getenv("APOLLO_MEMORY_WAIT_SEC", "900"))
DEFAULT_MEMORY_RETRY_SEC = int(os.getenv("APOLLO_MEMORY_RETRY_SEC", "60"))
DEFAULT_GATHER_MIN_ACCEPTED_SOURCES = int(os.getenv("APOLLO_GATHER_MIN_ACCEPTED_SOURCES", "4"))
DEFAULT_GATHER_MIN_AVG_SCORE = int(os.getenv("APOLLO_GATHER_MIN_AVG_SCORE", "75"))
DEFAULT_GATHER_MIN_TRUSTED_RATIO = float(os.getenv("APOLLO_GATHER_MIN_TRUSTED_RATIO", "0.75"))
DEFAULT_GATHER_MIN_DOC_SOURCES = int(os.getenv("APOLLO_GATHER_MIN_DOC_SOURCES", "3"))
DEFAULT_GATHER_MIN_PDF_SOURCES = int(os.getenv("APOLLO_GATHER_MIN_PDF_SOURCES", "2"))
DEFAULT_GATHER_REMEDIATION_MAX_PASSES = int(os.getenv("APOLLO_GATHER_REMEDIATION_MAX_PASSES", "2"))
DEFAULT_GATHER_REMEDIATION2_MIN_DOC_SOURCES = int(os.getenv("APOLLO_GATHER_REMEDIATION2_MIN_DOC_SOURCES", "2"))
DEFAULT_GATHER_ALLOW_SEED_FALLBACK = os.getenv("APOLLO_GATHER_ALLOW_SEED_FALLBACK", "1") == "1"
DEFAULT_GATHER_WEB_RATE_LIMIT_RETRIES = int(os.getenv("APOLLO_GATHER_WEB_RATE_LIMIT_RETRIES", "1"))
DEFAULT_GATHER_WEB_RATE_LIMIT_BACKOFF_SEC = float(os.getenv("APOLLO_GATHER_WEB_RATE_LIMIT_BACKOFF_SEC", "1.5"))
DEFAULT_GATHER_WEB_PROVIDER_FALLBACKS = [
    str(item).strip().lower()
    for item in str(
        os.getenv(
            "APOLLO_GATHER_WEB_PROVIDER_FALLBACKS",
            "auto,duckduckgo_html,duckduckgo_lite,google_news_rss,bing_rss",
        )
    ).split(",")
    if str(item).strip()
]
DEFAULT_OCR_DOC_MIN_CHARS = int(os.getenv("APOLLO_OCR_DOC_MIN_CHARS", "600"))
DEFAULT_OCR_AVG_MIN_CHARS = int(os.getenv("APOLLO_OCR_AVG_MIN_CHARS", "1500"))
DEFAULT_OCR_MIN_CONFIDENCE = float(os.getenv("APOLLO_OCR_MIN_CONFIDENCE", "0.45"))
DEFAULT_OCR_MIN_SEMANTIC_HITS = int(os.getenv("APOLLO_OCR_MIN_SEMANTIC_HITS", "2"))
DEFAULT_OCR_REQUIRE_TABLE_DOCS = int(os.getenv("APOLLO_OCR_REQUIRE_TABLE_DOCS", "0"))
DEFAULT_OCR_PRIMARY_ENGINE_PDF = os.getenv("APOLLO_OCR_PRIMARY_ENGINE_PDF", "gb10_paddleocr_vl")
DEFAULT_OCR_PRIMARY_ENGINE_IMAGE = os.getenv("APOLLO_OCR_PRIMARY_ENGINE_IMAGE", "gb10_got_ocr2")
DEFAULT_OCR_SECONDARY_ENGINE = os.getenv("APOLLO_OCR_SECONDARY_ENGINE", "gb10_qwen_ocr")
DEFAULT_OCR_ROUTE_MODE = str(os.getenv("APOLLO_OCR_ROUTE_MODE", "quality_first")).strip().lower() or "quality_first"
DEFAULT_OCR_QWEN_PRIMARY = os.getenv("APOLLO_OCR_QWEN_PRIMARY", "0") == "1"
DEFAULT_OCR_MIN_NON_SCRAPE_RATIO = float(os.getenv("APOLLO_OCR_MIN_NON_SCRAPE_RATIO", "0.85"))
DEFAULT_OCR_MIN_STRUCTURE_SCORE = float(os.getenv("APOLLO_OCR_MIN_STRUCTURE_SCORE", "0.45"))
DEFAULT_OCR_MIN_NUMERIC_FIDELITY = float(os.getenv("APOLLO_OCR_MIN_NUMERIC_FIDELITY", "0.50"))
DEFAULT_OCR_STRUCTURE_BENCH_MIN = int(os.getenv("APOLLO_OCR_STRUCTURE_BENCH_MIN", "80"))
DEFAULT_OCR_CORRECTNESS_BENCH_MIN = int(os.getenv("APOLLO_OCR_CORRECTNESS_BENCH_MIN", "82"))
DEFAULT_OCR_STAGE_MIN_SCORE = int(os.getenv("APOLLO_OCR_STAGE_MIN_SCORE", "85"))
DEFAULT_OCR_REQUIRE_FINANCE_STRUCTURE = os.getenv("APOLLO_OCR_REQUIRE_FINANCE_STRUCTURE", "1") == "1"
DEFAULT_OCR_SCAN_ENABLED = os.getenv("APOLLO_OCR_SCAN_ENABLED", "1") == "1"
DEFAULT_OCR_SCAN_MIN_CHARS = int(os.getenv("APOLLO_OCR_SCAN_MIN_CHARS", "700"))
DEFAULT_OCR_SCAN_MIN_QUALITY_SCORE = float(os.getenv("APOLLO_OCR_SCAN_MIN_QUALITY_SCORE", "60"))
DEFAULT_OCR_SCAN_MIN_SEMANTIC_HITS = int(os.getenv("APOLLO_OCR_SCAN_MIN_SEMANTIC_HITS", "2"))
DEFAULT_OCR_SCAN_MIN_STRUCTURE_SCORE = float(os.getenv("APOLLO_OCR_SCAN_MIN_STRUCTURE_SCORE", "0.25"))
DEFAULT_OCR_SCAN_MIN_NUMERIC_FIDELITY = float(os.getenv("APOLLO_OCR_SCAN_MIN_NUMERIC_FIDELITY", "0.2"))
DEFAULT_OCR_SCAN_MIN_ANCHOR_HITS = int(os.getenv("APOLLO_OCR_SCAN_MIN_ANCHOR_HITS", "1"))
DEFAULT_FOCUS_UNIVERSE_ENABLED = os.getenv("APOLLO_FOCUS_UNIVERSE_ENABLED", "1") == "1"
DEFAULT_FOCUS_UNIVERSE_MIN_NAMES = int(os.getenv("APOLLO_FOCUS_UNIVERSE_MIN_NAMES", "5"))
DEFAULT_FOCUS_UNIVERSE_MAX_NAMES = int(os.getenv("APOLLO_FOCUS_UNIVERSE_MAX_NAMES", "8"))
DEFAULT_FOCUS_UNIVERSE_GB10_ONLY = os.getenv("APOLLO_FOCUS_UNIVERSE_GB10_ONLY", "1") == "1"
DEFAULT_FOCUS_UNIVERSE_DECISION_MIN_VOLUME = int(os.getenv("APOLLO_FOCUS_UNIVERSE_DECISION_MIN_VOLUME", "60"))
STRICT_STAGE_SCORE_MIN = int(os.getenv("APOLLO_PIPELINE_STAGE_MIN_SCORE", "70"))
_BACKGROUND_RUNS: Dict[str, Dict[str, Any]] = {}

_GATHER_DOMAIN_TIER_A = (
    "sec.gov",
    "finra.org",
    "cftc.gov",
    "federalreserve.gov",
    "treasury.gov",
    "federalregister.gov",
    "investor.gov",
    "irs.gov",
    "nyse.com",
    "nasdaq.com",
    ".gov",
    ".edu",
)

_GATHER_DOMAIN_TIER_B = (
    "nber.org",
    "ssrn.com",
    "arxiv.org",
    "researchgate.net",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "jstor.org",
)

_GATHER_DOMAIN_TIER_C = (
    "nyse.com",
    "nasdaq.com",
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "cnbc.com",
    "marketwatch.com",
    "yahoo.com",
    "google.com",
    "cnn.com",
    "investopedia.com",
)

_GATHER_DOMAIN_DENYLIST = (
    "merriam-webster.com",
    "dictionary.cambridge.org",
    "oxfordlearnersdictionaries.com",
    "collinsdictionary.com",
    "wikipedia.org",
    "freepik.com",
    "forum.intraday.my",
    "quora.com",
    "reddit.com/r/",
)

_WEEKLY_REVIEW_DOMAIN_POLICY_PATH = _APOLLO_ROOT / "logs" / "weekly_rag_graph_review" / "domain_policy.json"
DEFAULT_GATHER_QUARANTINE_SCORE_PENALTY = float(os.getenv("APOLLO_GATHER_QUARANTINE_SCORE_PENALTY", "12"))


def _normalized_domain_token(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    token = token.replace("https://", "").replace("http://", "").strip()
    token = token.split("/", 1)[0].strip()
    if token.startswith("www."):
        token = token[4:]
    return token


def _load_weekly_review_domain_policy() -> Dict[str, Any]:
    try:
        payload = json.loads(_WEEKLY_REVIEW_DOMAIN_POLICY_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _dynamic_gather_domain_denylist() -> list[str]:
    payload = _load_weekly_review_domain_policy()
    out: list[str] = []

    def _add_domain(value: Any) -> None:
        domain = _normalized_domain_token(value)
        if domain and domain not in out:
            out.append(domain)

    for value in list(payload.get("deny_domains") or []):
        _add_domain(value)
    for value in list(payload.get("hard_deleted_domains") or []):
        _add_domain(value)

    domains = payload.get("domains") if isinstance(payload.get("domains"), dict) else {}
    for domain, row in domains.items():
        entry = row if isinstance(row, dict) else {}
        mode = str(entry.get("mode") or "").strip().lower()
        if mode in {"purge_soft", "hard_deleted"}:
            _add_domain(domain)

    return out


def _dynamic_gather_quarantine_domains() -> list[str]:
    payload = _load_weekly_review_domain_policy()
    out: list[str] = []

    def _add_domain(value: Any) -> None:
        domain = _normalized_domain_token(value)
        if domain and domain not in out:
            out.append(domain)

    for value in list(payload.get("quarantine_domains") or []):
        _add_domain(value)

    domains = payload.get("domains") if isinstance(payload.get("domains"), dict) else {}
    for domain, row in domains.items():
        entry = row if isinstance(row, dict) else {}
        mode = str(entry.get("mode") or "").strip().lower()
        if mode == "quarantine":
            _add_domain(domain)

    return out


def _merged_gather_domain_denylist() -> list[str]:
    merged = [str(value) for value in _GATHER_DOMAIN_DENYLIST]
    for value in _dynamic_gather_domain_denylist():
        if value not in merged:
            merged.append(value)
    return merged

_FINANCE_TERMS = (
    "stock",
    "trading",
    "day trading",
    "intraday",
    "pattern day trader",
    "pdt",
    "margin",
    "leverage",
    "position sizing",
    "risk management",
    "risk of ruin",
    "slippage",
    "transaction costs",
    "stop loss",
    "volatility",
    "market microstructure",
    "finra",
    "sec",
    "federal reserve",
)

_OCR_SEMANTIC_TERMS = (
    "risk",
    "margin",
    "position",
    "capital",
    "leverage",
    "entry",
    "exit",
    "drawdown",
    "stop loss",
    "slippage",
    "compliance",
    "rule",
    "stock",
    "equity",
    "market",
    "trade",
    "volatility",
    "sector",
)

# Flag files for pause/stop control â€” written by API endpoints, read each tick.
_PAUSE_FLAG = RUNS_ROOT / "background" / "pause.flag"
_STOP_FLAG = RUNS_ROOT / "background" / "stop.flag"
_FIXTURE_PATH_TOKENS = (
    "tmp",
    "temp",
    "fixture",
    "fixtures",
    "pytest",
    "quality_sample",
)


def pipeline_paused() -> bool:
    return _PAUSE_FLAG.exists()


def set_pipeline_paused(paused: bool) -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    _PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        _PAUSE_FLAG.write_text("paused")
    elif _PAUSE_FLAG.exists():
        _PAUSE_FLAG.unlink()


def pipeline_stopped() -> bool:
    return _STOP_FLAG.exists()


def set_pipeline_stopped(stopped: bool) -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    _STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    if stopped:
        _STOP_FLAG.write_text("stopped")
    elif _STOP_FLAG.exists():
        _STOP_FLAG.unlink()


def mark_background_control_state(reason: Optional[str]) -> Dict[str, Any]:
    run_dir = _background_run_dir({"batch_id": DEFAULT_BACKGROUND_RUN_ID})
    status = _read_json(_status_path(run_dir), {})
    if not isinstance(status, dict) or not status:
        config = _read_json(run_dir / "config.json", _pipeline_config({"run_mode": "background"}))
        status = {
            "ok": False,
            "run_id": str(config.get("batch_id") or DEFAULT_BACKGROUND_RUN_ID),
            "run_dir": str(run_dir),
            "started_at": _utc_iso(),
            "finished_at": "",
            "estimate": estimate_runtime_breakdown(config),
            "stages": {},
            "current_stage": "gather",
            "run_mode": "background",
        }
    if reason:
        status["waiting"] = {"reason": str(reason), "ts": _utc_iso()}
    else:
        status.pop("waiting", None)
    _save_status(run_dir, status)
    return status


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: Optional[datetime] = None) -> str:
    dt = value or _now_utc()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_slug(value: str) -> str:
    raw = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or "").strip())
    while "__" in raw:
        raw = raw.replace("__", "_")
    return raw.strip("_") or "run"


def _focus_universe_artifact_path(run_dir: Path) -> Path:
    return run_dir / "00_focus_universe.json"


def _load_focus_universe_artifact(run_dir: Path) -> Dict[str, Any]:
    return _read_json(_focus_universe_artifact_path(run_dir), {})


def _persist_focus_universe_selection(run_dir: Path, selection: Dict[str, Any]) -> None:
    payload = dict(selection or {})
    if not payload:
        return
    payload["saved_at"] = _utc_iso()
    _write_json(_focus_universe_artifact_path(run_dir), payload)
    state_payload = {
        "saved_at": payload["saved_at"],
        "run_dir": str(Path(run_dir).resolve()),
        "topic": str(payload.get("topic") or "").strip(),
        "selected_theme": dict(payload.get("selected_theme") or {}),
        "selected_tickers": list(payload.get("selected_tickers") or []),
        "research_queries": list(payload.get("research_queries") or []),
    }
    _write_json(FOCUS_UNIVERSE_STATE_PATH, state_payload)


def _select_focus_universe(config: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.trading_homework import _web_search as _trading_homework_web_search
    from Apollo.focus_universe import select_focus_universe

    return select_focus_universe(
        str(config.get("topic") or DEFAULT_TOPIC),
        max_names=int(config.get("focus_universe_max_names") or DEFAULT_FOCUS_UNIVERSE_MAX_NAMES),
        min_names=int(config.get("focus_universe_min_names") or DEFAULT_FOCUS_UNIVERSE_MIN_NAMES),
        manifest_path=str(config.get("focus_universe_manifest_path") or "").strip() or None,
        web_search_fn=_trading_homework_web_search,
        score_candidate_fn=_score_gather_candidate,
    )


def _ticker_search_queries(tickers: list[str]) -> list[str]:
    queries = []
    for ticker in tickers[:6]:
        t = ticker.strip().upper()
        queries.append(f"{t} earnings guidance outlook analyst")
        queries.append(f"{t} stock price technical analysis breakout setup")
    return queries


def _combine_focus_queries(config: Dict[str, Any], selection: Dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in list(selection.get("research_queries") or []) + list(config.get("queries") or []):
        query = str(row or "").strip()
        if not query or query in seen:
            continue
        seen.add(query)
        out.append(query)
    tickers = list(config.get("equity_news_tickers") or [])
    if tickers:
        for q in _ticker_search_queries(tickers):
            if q not in seen:
                seen.add(q)
                out.append(q)
    return out


def _next_market_decision_time(offset_minutes: int) -> str:
    if offset_minutes < 0:
        return ""
    local_now = _now_local()
    run_day = local_now.date()
    while run_day.weekday() >= 5:
        run_day = run_day + timedelta(days=1)
    base_dt = datetime.combine(run_day, dt_time(hour=8, minute=30), tzinfo=local_now.tzinfo) + timedelta(minutes=offset_minutes)
    if base_dt <= local_now:
        run_day = run_day + timedelta(days=1)
        while run_day.weekday() >= 5:
            run_day = run_day + timedelta(days=1)
        base_dt = datetime.combine(run_day, dt_time(hour=8, minute=30), tzinfo=local_now.tzinfo) + timedelta(minutes=offset_minutes)
    return base_dt.isoformat()


def _compute_focus_decision_gate(status: Dict[str, Any], config: Dict[str, Any], focus_universe: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.focus_universe import evaluate_decision_gate

    decision = evaluate_decision_gate(
        quality=dict(status.get("quality") or {}),
        stage_details=dict(status.get("stage_details") or {}),
        focus_universe=dict(focus_universe or {}),
    )
    decision["recommended_local_time"] = _next_market_decision_time(int(decision.get("decision_offset_minutes") or -1))
    decision["min_volume_required"] = int(config.get("focus_universe_decision_min_volume") or DEFAULT_FOCUS_UNIVERSE_DECISION_MIN_VOLUME)
    if float(decision.get("volume_score") or 0.0) < float(decision["min_volume_required"]):
        decision["decision_ready"] = False
        decision["decision_mode"] = "defer_and_continue_research"
        decision["recommended_use"] = "continue_research_only"
        reasons = list(decision.get("reasons") or [])
        token = f"volume_score_below_policy:{decision.get('volume_score')}<{decision['min_volume_required']}"
        if token not in reasons:
            reasons.append(token)
        decision["reasons"] = reasons
        decision["recommended_local_time"] = ""
    return decision


def _stage_requires_gb10(stage_name: str, config: Dict[str, Any]) -> bool:
    if stage_name in {"ocr", "hipporag"}:
        return True
    if stage_name == "gather":
        return bool(config.get("focus_universe_enabled")) and bool(config.get("focus_universe_gb10_only"))
    return False


def _wait_for_gb10_idle(config: Dict[str, Any], *, stage_name: str, max_wait_sec: int) -> Dict[str, Any]:
    deadline = time.perf_counter() + max(0, int(max_wait_sec))
    gate = _background_gx10_idle(config)
    while not gate.get("idle") and time.perf_counter() < deadline:
        time.sleep(min(30, max(1, int(deadline - time.perf_counter()))))
        gate = _background_gx10_idle(config)
    gate = dict(gate)
    gate["waited_sec"] = max(0, int(max_wait_sec - max(0, int(deadline - time.perf_counter()))))
    gate["stage"] = stage_name
    return gate


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    last_error: Optional[Exception] = None
    for attempt in range(8):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt >= 7:
                break
            time.sleep(0.1 * (attempt + 1))
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except Exception:
            pass
    if last_error is not None:
        raise last_error


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _resolved_rag_dir() -> str:
    raw = str(os.getenv("RAG_DIR") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (_APOLLO_ROOT / raw).resolve()
        return str(path)
    return str((_APOLLO_ROOT / "chroma_db").resolve())


def _nightly_ocr_rag_dir(run_dir: Optional[Path] = None) -> Path:
    raw = str(os.getenv("APOLLO_NIGHTLY_OCR_RAG_DIR") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (_APOLLO_ROOT / raw).resolve()
        return path
    if run_dir is not None:
        return (Path(run_dir).resolve() / "nightly_ocr_rag").resolve()
    return Path(NIGHTLY_OCR_RAG_DIR).resolve()


def _nightly_hipporag_rag_dir(run_dir: Optional[Path] = None) -> str:
    raw = str(os.getenv("APOLLO_NIGHTLY_HIPPORAG_RAG_DIR") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (_APOLLO_ROOT / raw).resolve()
        return str(path)
    return str(_nightly_ocr_rag_dir(run_dir))


def _nightly_hipporag_agent_name() -> str:
    return (
        str(
            os.getenv(
                "APOLLO_NIGHTLY_HIPPORAG_COLLECTION",
                os.getenv("APOLLO_NIGHTLY_OCR_RAG_COLLECTION", "ApolloNightlyOCR"),
            )
            or "ApolloNightlyOCR"
        ).strip()
        or "ApolloNightlyOCR"
    )


def _nightly_hipporag_collection() -> str:
    return f"{_nightly_hipporag_agent_name()}_rag"


def _status_path(run_dir: Path) -> Path:
    return run_dir / "status.json"


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _is_fixture_segment(segment: str) -> bool:
    token = str(segment or "").strip().lower()
    if not token:
        return False
    if token.startswith("tmp") or token.startswith("test"):
        return True
    return any(mark in token for mark in _FIXTURE_PATH_TOKENS)


def _is_production_run_dir(run_dir: Path) -> bool:
    if not _path_is_within(run_dir, RUNS_ROOT):
        return False
    try:
        rel_parts = [part.lower() for part in run_dir.resolve().relative_to(RUNS_ROOT.resolve()).parts]
    except Exception:
        return False
    if not rel_parts:
        return False
    if any(_is_fixture_segment(part) for part in rel_parts):
        return False
    return True


def _is_background_run_status(payload: Dict[str, Any]) -> bool:
    run_mode = str(payload.get("run_mode") or "").strip().lower()
    run_id = str(payload.get("run_id") or "").strip().lower()
    return run_mode == "background" or run_id == str(DEFAULT_BACKGROUND_RUN_ID).lower()


def _read_current_run_pointer() -> Dict[str, Any]:
    return _read_json(CURRENT_RUN_POINTER_PATH, {})


def _write_current_run_pointer(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    run_dir = Path(str(payload.get("run_dir") or "").strip() or ".")
    if not _is_production_run_dir(run_dir):
        return
    pointer = _read_current_run_pointer()
    incoming_seq = int(payload.get("status_seq") or 0)
    current_seq = int(pointer.get("status_seq") or 0)
    if incoming_seq < current_seq:
        return
    _write_json(
        CURRENT_RUN_POINTER_PATH,
        {
            "run_id": str(payload.get("run_id") or "").strip(),
            "run_dir": str(run_dir.resolve()),
            "status_seq": incoming_seq,
            "updated_at": str(payload.get("updated_at") or _utc_iso()),
            "run_mode": str(payload.get("run_mode") or ""),
        },
    )


def _resolve_status_seq(run_dir: Path, payload: Dict[str, Any]) -> int:
    existing = _read_json(_status_path(run_dir), {})
    existing_seq = int(existing.get("status_seq") or 0)
    incoming_seq = int(payload.get("status_seq") or 0)
    return max(existing_seq, incoming_seq) + 1


def _parse_status_time(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def _should_replace_latest_status(incoming: Dict[str, Any], existing: Dict[str, Any]) -> bool:
    if not existing:
        return True
    incoming_run_dir = str(incoming.get("run_dir") or "").strip()
    existing_run_dir = str(existing.get("run_dir") or "").strip()
    if incoming_run_dir and existing_run_dir and incoming_run_dir == existing_run_dir:
        return int(incoming.get("status_seq") or 0) >= int(existing.get("status_seq") or 0)
    incoming_ts = max(
        _parse_status_time(incoming.get("updated_at")),
        _parse_status_time(incoming.get("finished_at")),
        _parse_status_time(incoming.get("started_at")),
    )
    existing_ts = max(
        _parse_status_time(existing.get("updated_at")),
        _parse_status_time(existing.get("finished_at")),
        _parse_status_time(existing.get("started_at")),
    )
    return incoming_ts >= existing_ts


def _load_status(run_dir: Path) -> Dict[str, Any]:
    return _read_json(_status_path(run_dir), {"ok": False, "stages": {}, "run_dir": str(run_dir)})


def _domain(url: str) -> str:
    try:
        return str(urlparse(str(url or "").strip()).netloc or "").lower().strip()
    except Exception:
        return ""


def _norm_text(*parts: Any) -> str:
    raw = " ".join(str(part or "") for part in parts)
    return " ".join(raw.lower().split())


def _topic_terms(topic: str) -> list[str]:
    out = []
    for token in _norm_text(topic).split():
        if len(token) >= 4 and token not in {"with", "that", "from", "into", "your"}:
            out.append(token)
    return out[:12]


def _semantic_section_hits(text: str) -> int:
    norm = _norm_text(text)
    return sum(1 for term in _OCR_SEMANTIC_TERMS if term in norm)


def _table_like_content(text: str) -> bool:
    raw = str(text or "")
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return False
    delimiter_lines = sum(
        1 for line in lines
        if ("|" in line and line.count("|") >= 2)
        or ("\t" in line and len(line.split("\t")) >= 3)
        or ("  " in line and len([seg for seg in line.split("  ") if seg.strip()]) >= 3)
    )
    return delimiter_lines >= 2


def _table_like_rows(text: str) -> int:
    rows = 0
    for line in str(text or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        if ("|" in clean and clean.count("|") >= 2) or ("\t" in clean and len(clean.split("\t")) >= 3):
            rows += 1
    return rows


def _numeric_fidelity_score(text: str) -> float:
    raw = str(text or "")
    chars = max(1, len(raw))
    digits = sum(1 for ch in raw if ch.isdigit())
    finance_markers = 0
    for marker in ("$", "%", "margin", "risk", "position", "entry", "exit", "stop", "rule", "capital", "drawdown"):
        if marker in raw.lower():
            finance_markers += 1
    density = min(1.0, (digits / float(chars)) * 15.0)
    marker_bonus = min(1.0, finance_markers / 7.0)
    return round(max(0.0, min(1.0, (density * 0.6) + (marker_bonus * 0.4))), 3)


def _structure_score(text: str, semantic_hits: int) -> float:
    raw = str(text or "")
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return 0.0
    heading_like = sum(
        1
        for line in lines[:80]
        if line.strip().endswith(":")
        or line.strip().startswith(("#", "-", "â€¢"))
        or line.strip().isupper()
    )
    table_rows = _table_like_rows(raw)
    multi_line_bonus = min(1.0, len(lines) / 80.0)
    heading_score = min(1.0, heading_like / 12.0)
    table_score = min(1.0, table_rows / 8.0)
    semantic_score = min(1.0, max(0, int(semantic_hits or 0)) / 6.0)
    return round(
        max(
            0.0,
            min(
                1.0,
                (multi_line_bonus * 0.25)
                + (heading_score * 0.25)
                + (table_score * 0.25)
                + (semantic_score * 0.25),
            ),
        ),
        3,
    )


def _is_finance_topic(topic: str) -> bool:
    topic_norm = _norm_text(topic)
    return any(term in topic_norm for term in ("stock", "trading", "margin", "risk", "position", "intraday", "day trading"))


def _is_document_source(url: str) -> bool:
    target = str(url or "").strip().lower()
    if not target:
        return False
    if target.endswith(".pdf") or ".pdf?" in target or "/pdf" in target or target.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
        return True
    if not target.startswith(("http://", "https://")):
        return False
    parsed = urlparse(target)
    path = str(parsed.path or "").strip("/")
    if not path:
        return False
    domain = _domain(target)
    if not _domain_matches(domain, _GATHER_DOMAIN_TIER_A + _GATHER_DOMAIN_TIER_B):
        return False
    report_tokens = (
        "rule",
        "rules-guidance",
        "rulebook",
        "bulletin",
        "alert",
        "advisory",
        "guidance",
        "investor",
        "margin",
        "day-trading",
        "trading",
        "report",
        "publication",
        "research",
        "study",
        "notice",
        "release",
    )
    return any(token in path for token in report_tokens)


def _is_local_existing_source(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        parsed = None
    if parsed and str(parsed.scheme or "").lower() in {"http", "https"}:
        return False
    try:
        return Path(raw).exists()
    except Exception:
        return False


def _is_seed_fallback_source(url: str = "", local_path: Optional[Path] = None) -> bool:
    candidates: list[str] = []
    raw_url = str(url or "").strip()
    if raw_url:
        candidates.append(raw_url)
    if local_path is not None:
        try:
            candidates.append(str(local_path))
        except Exception:
            pass
    for raw in candidates:
        normalized = raw.replace("\\", "/").lower()
        if "/logs/homework_seed_docs/" in normalized:
            return True
    return False


def _domain_matches(domain: str, patterns: tuple[str, ...]) -> bool:
    value = str(domain or "").strip().lower()
    if not value:
        return False
    for pattern in patterns:
        token = str(pattern or "").strip().lower()
        if not token:
            continue
        if token.startswith("."):
            if value.endswith(token):
                return True
            continue
        if value == token or value.endswith(f".{token}"):
            return True
    return False


def _gather_domain_tier(domain: str) -> str:
    if _domain_matches(domain, _GATHER_DOMAIN_DENYLIST):
        return "D"
    if _domain_matches(domain, _GATHER_DOMAIN_TIER_A):
        return "A"
    if _domain_matches(domain, _GATHER_DOMAIN_TIER_B):
        return "B"
    if _domain_matches(domain, _GATHER_DOMAIN_TIER_C):
        return "C"
    return "D"


def _domain_trust_component(tier: str) -> float:
    return {"A": 35.0, "B": 28.0, "C": 18.0}.get(str(tier or "").upper(), 0.0)


def _source_evidence_lane(row: Dict[str, Any]) -> str:
    tier = str(row.get("tier") or "").upper()
    domain = str(row.get("domain") or _domain(str(row.get("url") or ""))).lower()
    url = str(row.get("url") or "").lower()
    title = str(row.get("title") or "").lower()
    text = f"{url} {title}"
    if tier in {"A", "B"} and (
        "sec.gov" in domain
        or "investor" in domain
        or "ir." in domain
        or "earnings" in text
        or "press-release" in text
        or "8-k" in text
        or "10-q" in text
        or "10-k" in text
    ):
        return "primary_evidence"
    if tier in {"A", "B"}:
        return "trusted_market_news"
    if "feeds.finance.yahoo.com" in domain or "finance.yahoo.com" in domain or "news.google.com" in domain:
        return "market_news_evidence"
    if tier == "C":
        return "market_news_evidence"
    return "other"


def _ticker_mentions_for_source(row: Dict[str, Any], tickers: List[str]) -> List[str]:
    haystack = f"{row.get('url') or ''} {row.get('title') or ''} {row.get('why') or ''}".upper()
    found: List[str] = []
    for ticker in tickers:
        t = str(ticker or "").strip().upper()
        if not t:
            continue
        if re.search(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])", haystack):
            found.append(t)
    return found


def _source_confidence_from_quality(quality: Dict[str, Any]) -> str:
    tier_breakdown = dict((quality.get("tier_breakdown") or {}).get("accepted") or {})
    accepted = int(quality.get("accepted_count") or 0)
    trusted_count = int(quality.get("trusted_count") or 0)
    primary_count = int(quality.get("primary_evidence_count") or 0)
    if accepted <= 0:
        return "none"
    if primary_count > 0 and trusted_count >= 2:
        return "high"
    if trusted_count > 0:
        return "medium"
    if accepted > 0 and int(tier_breakdown.get("C") or 0) == accepted:
        return "low"
    return "low"


def _annotate_gather_quality_evidence(
    quality: Dict[str, Any],
    *,
    tickers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    out = dict(quality or {})
    accepted = [dict(item) for item in list(out.get("accepted_sources") or []) if isinstance(item, dict)]
    ticker_list = [str(t or "").strip().upper() for t in list(tickers or []) if str(t or "").strip()]
    evidence_lanes = {
        "primary_evidence": 0,
        "trusted_market_news": 0,
        "market_news_evidence": 0,
        "price_volume_evidence": 0,
        "graph_context": 0,
        "other": 0,
    }
    candidate_packs: Dict[str, Dict[str, Any]] = {}
    for row in accepted:
        lane = _source_evidence_lane(row)
        row["evidence_lane"] = lane
        evidence_lanes[lane] = int(evidence_lanes.get(lane, 0)) + 1
        mentions = _ticker_mentions_for_source(row, ticker_list)
        for ticker in mentions:
            pack = candidate_packs.setdefault(
                ticker,
                {
                    "ticker": ticker,
                    "source_count": 0,
                    "tier_counts": {"A": 0, "B": 0, "C": 0, "D": 0},
                    "evidence_lanes": {
                        "primary_evidence": 0,
                        "trusted_market_news": 0,
                        "market_news_evidence": 0,
                        "price_volume_evidence": 0,
                        "graph_context": 0,
                        "other": 0,
                    },
                    "best_source_tier": "D",
                    "best_source_score": 0.0,
                    "source_confidence": "none",
                    "trade_confidence_cap": "blocked",
                },
            )
            tier = str(row.get("tier") or "D").upper()
            pack["source_count"] = int(pack.get("source_count") or 0) + 1
            tier_counts = dict(pack.get("tier_counts") or {})
            tier_counts[tier] = int(tier_counts.get(tier) or 0) + 1
            pack["tier_counts"] = tier_counts
            lanes = dict(pack.get("evidence_lanes") or {})
            lanes[lane] = int(lanes.get(lane) or 0) + 1
            pack["evidence_lanes"] = lanes
            score = float(row.get("score") or 0.0)
            tier_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
            current_tier = str(pack.get("best_source_tier") or "D").upper()
            if tier_rank.get(tier, 0) > tier_rank.get(current_tier, 0) or score > float(pack.get("best_source_score") or 0.0):
                pack["best_source_tier"] = tier
                pack["best_source_score"] = score
    for pack in candidate_packs.values():
        tiers = dict(pack.get("tier_counts") or {})
        lanes = dict(pack.get("evidence_lanes") or {})
        trusted = int(tiers.get("A") or 0) + int(tiers.get("B") or 0)
        primary = int(lanes.get("primary_evidence") or 0)
        if primary > 0 and trusted > 0:
            pack["source_confidence"] = "high"
            pack["trade_confidence_cap"] = "standard_allowed"
        elif trusted > 0:
            pack["source_confidence"] = "medium"
            pack["trade_confidence_cap"] = "standard_requires_price_confirmation"
        elif int(tiers.get("C") or 0) > 0:
            pack["source_confidence"] = "low"
            pack["trade_confidence_cap"] = "probe_or_observation"
        else:
            pack["source_confidence"] = "none"
            pack["trade_confidence_cap"] = "blocked"
    out["accepted_sources"] = accepted
    out["evidence_lanes"] = evidence_lanes
    out["primary_evidence_count"] = int(evidence_lanes.get("primary_evidence") or 0)
    out["trusted_market_news_count"] = int(evidence_lanes.get("trusted_market_news") or 0)
    out["market_news_evidence_count"] = int(evidence_lanes.get("market_news_evidence") or 0)
    out["candidate_evidence_packs"] = sorted(candidate_packs.values(), key=lambda row: (str(row.get("source_confidence") or ""), int(row.get("source_count") or 0)), reverse=True)
    out["source_confidence"] = _source_confidence_from_quality(out)
    out["c_only_run"] = bool(int(out.get("accepted_count") or 0) > 0 and int(out.get("trusted_count") or 0) == 0)
    out["standard_trade_ready_allowed"] = not bool(out["c_only_run"]) and str(out["source_confidence"]) in {"medium", "high"}
    out["trade_confidence_cap"] = "probe_or_observation" if out["c_only_run"] else ("standard_allowed" if out["standard_trade_ready_allowed"] else "blocked")
    return out


def _doc_utility_component(url: str, text: str) -> tuple[float, str]:
    url_low = str(url or "").lower()
    if _is_document_source(url_low):
        if ".pdf" in url_low:
            return 20.0, "pdf"
        return 18.0, "image_or_document"
    if any(token in text for token in ("filing", "rule", "report", "whitepaper", "research", "study", "notice", "bulletin", "release")):
        return 14.0, "report_like"
    path = str(urlparse(url_low).path or "").strip("/")
    if not path:
        return 0.0, "homepage"
    return 8.0, "article"


def _score_gather_candidate(item: Dict[str, Any], topic: str) -> Dict[str, Any]:
    url = str(item.get("url") or "").strip()
    relevance_url = str(item.get("relevance_url") or item.get("origin_url") or url).strip() or url
    local_source = _is_local_existing_source(url)
    domain = "local_seed" if local_source else _domain(url)
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("snippet") or "").strip()
    text_url = relevance_url if relevance_url == url else f"{relevance_url}\n{url}"
    text = _norm_text(title, snippet, text_url)
    topic_tokens = _topic_terms(topic)
    tier = "B" if local_source else _gather_domain_tier(domain)
    domain_score = _domain_trust_component(tier)
    reject_reason = ""
    if not url:
        reject_reason = "missing_url"
    elif _domain_matches(domain, _GATHER_DOMAIN_DENYLIST):
        reject_reason = "blocked_domain"
    elif (not local_source) and str(urlparse(url).path or "").strip("/") == "":
        reject_reason = "homepage"
    elif (not local_source) and tier == "D":
        reject_reason = "low_trust_domain"
    pre_approved = bool(item.get("pre_approved"))
    finance_hits = sum(1 for kw in _FINANCE_TERMS if kw in text)
    topic_intent_terms = ("stock", "trading", "day trading", "intraday", "margin", "risk", "position sizing")
    topic_intent_hits = sum(1 for term in topic_intent_terms if term in text)
    topic_intent_score = min(25.0, (float(topic_intent_hits) * 4.0) + (float(min(finance_hits, 4)) * 2.0))
    doc_type_score, doc_utility_label = _doc_utility_component(url, text)
    topic_hits = sum(1 for token in topic_tokens if token in text)
    snippet_specificity = float(topic_hits * 2 + min(finance_hits, 3))
    if re.search(r"\b(19|20)\d{2}\b", text):
        snippet_specificity += 2.0
    if re.search(r"\b\d+(\.\d+)?(%|bps|basis points?)\b", text):
        snippet_specificity += 2.0
    snippet_score = min(20.0, snippet_specificity)
    total = round(domain_score + topic_intent_score + doc_type_score + snippet_score, 2)
    if not reject_reason and not pre_approved and domain_score <= 0 and finance_hits == 0:
        reject_reason = "weak_finance_relevance"
    if not reject_reason and not pre_approved and topic_intent_hits <= 0 and (finance_hits < 2 or topic_hits <= 0):
        reject_reason = "topic_intent_mismatch"
    if not reject_reason and not pre_approved and total < 55:
        reject_reason = "low_relevance_score"
    return {
        "url": url,
        "title": title,
        "score": total,
        "domain": domain,
        "tier": tier,
        "finance_hits": finance_hits,
        "topic_hits": topic_hits,
        "topic_intent_hits": topic_intent_hits,
        "is_pdf": ".pdf" in url.lower(),
        "is_doc_source": _is_document_source(url),
        "doc_utility": doc_utility_label,
        "breakdown": {
            "domain_trust": round(domain_score, 2),
            "topic_intent": round(topic_intent_score, 2),
            "document_utility": round(doc_type_score, 2),
            "snippet_specificity": round(snippet_score, 2),
            "topic_intent_hits": int(topic_intent_hits),
            "tier": tier,
            "doc_utility": doc_utility_label,
        },
        "reject_reason": reject_reason,
    }


def _apply_gather_quality_gate(
    result: Dict[str, Any],
    config: Dict[str, Any],
    *,
    allowed_tiers: Optional[set[str]] = None,
    quality_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    topic = str(result.get("topic") or config.get("topic") or DEFAULT_TOPIC)
    max_articles = int(config.get("max_articles") or DEFAULT_MAX_ARTICLES)
    quarantine_domains = set(_dynamic_gather_quarantine_domains())
    quarantine_penalty = float(config.get("gather_quarantine_score_penalty") or DEFAULT_GATHER_QUARANTINE_SCORE_PENALTY)
    override = dict(quality_override or {})
    min_accepted = int(override.get("min_accepted_sources") or config.get("gather_min_accepted_sources") or DEFAULT_GATHER_MIN_ACCEPTED_SOURCES)
    min_avg = float(override.get("min_avg_score") or config.get("gather_min_avg_score") or DEFAULT_GATHER_MIN_AVG_SCORE)
    min_trusted_ratio = float(
        override.get("min_trusted_ratio")
        if "min_trusted_ratio" in override
        else config.get("gather_min_trusted_ratio") or DEFAULT_GATHER_MIN_TRUSTED_RATIO
    )
    min_doc_sources = int(override["min_doc_sources"] if "min_doc_sources" in override else (config.get("gather_min_doc_sources") or DEFAULT_GATHER_MIN_DOC_SOURCES))
    min_pdf_sources = int(override["min_pdf_sources"] if "min_pdf_sources" in override else (config.get("gather_min_pdf_sources") or DEFAULT_GATHER_MIN_PDF_SOURCES))
    tier_allow = {str(item).upper() for item in (allowed_tiers or {"A", "B", "C"}) if str(item).strip()}
    candidates = [item for item in list(result.get("candidates") or []) if isinstance(item, dict)]
    selected_sources = [item for item in list(result.get("sources") or []) if isinstance(item, dict)]
    candidate_by_url: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        url = str(item.get("url") or "").strip()
        if url and url not in candidate_by_url:
            candidate_by_url[url] = item
    candidate_by_title_domain: Dict[tuple[str, str], Dict[str, Any]] = {}
    for item in candidates:
        title_key = str(item.get("title") or "").strip().lower()
        domain_key = _domain(str(item.get("url") or "").strip())
        if title_key and domain_key and (title_key, domain_key) not in candidate_by_title_domain:
            candidate_by_title_domain[(title_key, domain_key)] = item
    selected_title_domain_keys = {
        (str(item.get("title") or "").strip().lower(), _domain(str(item.get("url") or "").strip()))
        for item in selected_sources
        if str(item.get("title") or "").strip() and str(item.get("url") or "").strip()
    }
    scored_pool: list[Dict[str, Any]] = []
    rejected: list[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    _eq_tickers_lower = [str(t).strip().lower() for t in list(config.get("equity_news_tickers") or []) if str(t).strip()]

    def _score_and_collect(raw_item: Dict[str, Any], *, selected: bool) -> None:
        raw_url = str(raw_item.get("url") or "").strip()
        raw_title = str(raw_item.get("title") or "").strip()
        raw_domain = _domain(raw_url)
        merged = dict(candidate_by_url.get(raw_url) or {})
        if not merged and raw_title and raw_domain:
            merged = dict(candidate_by_title_domain.get((raw_title.lower(), raw_domain)) or {})
        origin_url = str(merged.get("url") or "").strip()
        if origin_url and origin_url != raw_url:
            merged["relevance_url"] = origin_url
        merged.update(raw_item)
        if not selected and raw_title and raw_domain and (raw_title.lower(), raw_domain) in selected_title_domain_keys:
            return
        scored = _score_gather_candidate(merged, topic)
        scored["selected"] = bool(selected)
        if not scored.get("reject_reason") and _eq_tickers_lower:
            from Apollo.document_triage import _MACRO_DOMAINS, _domain as _td
            if _td(raw_url) in _MACRO_DOMAINS:
                title_low = raw_title.lower()
                url_low = raw_url.lower()
                if not any(t in title_low or t in url_low for t in _eq_tickers_lower):
                    scored["reject_reason"] = "macro_domain_no_ticker_relevance"
        url = str(scored.get("url") or "").strip()
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        if not scored.get("reject_reason") and tier_allow and str(scored.get("tier") or "").upper() not in tier_allow:
            scored["reject_reason"] = "tier_not_allowed"
        if scored.get("reject_reason"):
            rejected.append(scored)
            return
        source_row = {
            "url": url,
            "title": str(raw_item.get("title") or merged.get("title") or "").strip(),
            "why": str(raw_item.get("why") or merged.get("why") or "").strip(),
            "score": scored["score"],
            "score_adjusted": scored["score"],
            "breakdown": scored["breakdown"],
            "finance_hits": scored["finance_hits"],
            "topic_hits": scored["topic_hits"],
            "topic_intent_hits": scored.get("topic_intent_hits", 0),
            "domain": scored["domain"],
            "tier": scored.get("tier"),
            "is_pdf": scored["is_pdf"],
            "is_doc_source": bool(scored.get("is_doc_source")),
            "doc_utility": str(scored.get("doc_utility") or ""),
        }
        if quarantine_domains and source_row["domain"] in quarantine_domains:
            source_row["quarantine_domain"] = True
            source_row["score_adjusted"] = round(float(source_row["score_adjusted"]) - quarantine_penalty, 2)
            source_row["score_adjustment_reason"] = "weekly_policy_quarantine_domain"
        else:
            source_row["quarantine_domain"] = False
        scored_pool.append(source_row)

    for item in selected_sources:
        _score_and_collect(item, selected=True)
    for item in candidates:
        _score_and_collect(item, selected=False)
    scored_pool = sorted(
        scored_pool,
        key=lambda row: float(row.get("score_adjusted") if row.get("score_adjusted") is not None else row.get("score") or 0.0),
        reverse=True,
    )
    accepted = scored_pool[: max_articles]
    accepted_scores = [float(item.get("score") or 0.0) for item in accepted]
    avg_score = round(sum(accepted_scores) / len(accepted_scores), 2) if accepted_scores else 0.0
    trusted_count = sum(1 for item in accepted if str(item.get("tier") or "").upper() in {"A", "B"})
    pdf_count = sum(1 for item in accepted if bool(item.get("is_pdf")))
    doc_sources = sum(1 for item in accepted if bool(item.get("is_doc_source")))
    trusted_ratio = float(trusted_count / max(1, len(accepted))) if accepted else 0.0
    accepted_tier_breakdown = {"A": 0, "B": 0, "C": 0, "D": 0}
    rejected_tier_breakdown = {"A": 0, "B": 0, "C": 0, "D": 0}
    reject_reason_counts: Dict[str, int] = {}
    for item in accepted:
        tier = str(item.get("tier") or "D").upper()
        accepted_tier_breakdown[tier] = int(accepted_tier_breakdown.get(tier, 0)) + 1
    for item in rejected:
        tier = str(item.get("tier") or "D").upper()
        rejected_tier_breakdown[tier] = int(rejected_tier_breakdown.get(tier, 0)) + 1
        reason = str(item.get("reject_reason") or "unknown")
        reject_reason_counts[reason] = int(reject_reason_counts.get(reason, 0)) + 1
    gate_pass = (
        len(accepted) >= min_accepted
        and avg_score >= min_avg
        and trusted_ratio >= min_trusted_ratio
        and doc_sources >= min_doc_sources
        and pdf_count >= min_pdf_sources
    )
    quality = {
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "avg_score": avg_score,
        "trusted_count": trusted_count,
        "trusted_ratio": round(trusted_ratio, 3),
        "doc_sources": doc_sources,
        "pdf_count": pdf_count,
        "min_accepted_sources": min_accepted,
        "min_avg_score": min_avg,
        "min_trusted_ratio": round(min_trusted_ratio, 3),
        "min_doc_sources": min_doc_sources,
        "min_pdf_sources": min_pdf_sources,
        "allowed_tiers": sorted(tier_allow),
        "quarantine_domains_active": sorted(quarantine_domains),
        "quarantine_penalty": round(quarantine_penalty, 2),
        "accepted_quarantine_count": sum(1 for item in accepted if bool(item.get("quarantine_domain"))),
        "tier_breakdown": {"accepted": accepted_tier_breakdown, "rejected": rejected_tier_breakdown},
        "reject_reason_counts": reject_reason_counts,
        "gate_pass": gate_pass,
        "accepted_sources": accepted,
        "rejected_candidates": rejected[:24],
    }
    quality = _annotate_gather_quality_evidence(
        quality,
        tickers=[str(t).strip().upper() for t in list(config.get("equity_news_tickers") or []) if str(t).strip()],
    )
    result["sources"] = [{"url": item["url"], "title": item["title"], "why": item.get("why") or ""} for item in accepted]
    result["quality"] = quality
    result["completed"] = bool(gate_pass)
    result["ok"] = bool(gate_pass)
    if not gate_pass:
        errors = list(result.get("errors") or [])
        if len(accepted) < min_accepted:
            errors.append(f"gather_quality_failed:accepted_sources:{len(accepted)}<{min_accepted}")
        if avg_score < min_avg:
            errors.append(f"gather_quality_failed:avg_score:{avg_score}<{min_avg}")
        if trusted_ratio < min_trusted_ratio:
            errors.append(f"gather_quality_failed:trusted_ratio:{round(trusted_ratio,3)}<{round(min_trusted_ratio,3)}")
        if doc_sources < min_doc_sources:
            errors.append(f"gather_quality_failed:doc_sources:{doc_sources}<{min_doc_sources}")
        if pdf_count < min_pdf_sources:
            errors.append(f"gather_quality_failed:pdf_sources:{pdf_count}<{min_pdf_sources}")
        result["errors"] = errors
    return result


def _extract_pdf_native_text(path: Path, *, max_pages: int, max_chars: int) -> Dict[str, Any]:
    def _normalize_pdf_text(raw_text: str) -> str:
        lines = [line.rstrip() for line in str(raw_text or "").splitlines()]
        compact = [line for line in lines if line.strip()]
        normalized = "\n".join(compact).strip()
        return normalized[:max_chars]

    max_pages = max(1, int(max_pages))
    max_chars = max(0, int(max_chars))
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        chunks: list[str] = []
        pages = 0
        for page in reader.pages[:max_pages]:
            pages += 1
            chunks.append(str(page.extract_text() or ""))
            if len("\n".join(chunks)) >= max_chars:
                break
        text = _normalize_pdf_text("\n".join(chunks))
        return {
            "ok": bool(text),
            "engine": "native_pdf_text",
            "reason": "native_text:pypdf",
            "text": text,
            "pages": pages,
            "confidence": None,
        }
    except Exception:
        pass
    try:
        import fitz

        doc = fitz.open(str(path))
        chunks = []
        pages = 0
        try:
            for page in doc:
                if pages >= max_pages:
                    break
                pages += 1
                chunks.append(str(page.get_text("text") or ""))
                if len("\n".join(chunks)) >= max_chars:
                    break
        finally:
            doc.close()
        text = _normalize_pdf_text("\n".join(chunks))
        return {
            "ok": bool(text),
            "engine": "native_pdf_text",
            "reason": "native_text:fitz",
            "text": text,
            "pages": pages,
            "confidence": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "engine": "native_pdf_text",
            "reason": "native_failed",
            "error": f"{type(exc).__name__}:{exc}",
            "text": "",
            "pages": 0,
            "confidence": None,
        }


def _extract_quality_score(text: str, confidence: Any, config: Dict[str, Any]) -> float:
    chars = len(str(text or "").strip())
    conf = 0.0
    try:
        conf = float(confidence)
    except Exception:
        conf = 0.0
    char_target = max(1, int(config.get("ocr_doc_min_chars") or DEFAULT_OCR_DOC_MIN_CHARS))
    conf_target = max(0.01, float(config.get("ocr_min_confidence") or DEFAULT_OCR_MIN_CONFIDENCE))
    char_score = min(1.0, chars / float(char_target))
    conf_score = min(1.0, conf / conf_target) if confidence is not None else 0.6
    return round(((char_score * 0.75) + (conf_score * 0.25)) * 100.0, 2)


def _is_low_quality_extract(text: str, confidence: Any, config: Dict[str, Any]) -> bool:
    chars = len(str(text or "").strip())
    min_chars = int(config.get("ocr_doc_min_chars") or DEFAULT_OCR_DOC_MIN_CHARS)
    if chars < min_chars:
        return True
    if confidence is None:
        return False
    try:
        return float(confidence) < float(config.get("ocr_min_confidence") or DEFAULT_OCR_MIN_CONFIDENCE)
    except Exception:
        return False


def _ocr_doc_quality_flag(text_chars: int, confidence: Any, extract_engine: str, config: Dict[str, Any]) -> str:
    min_chars = int(config.get("ocr_doc_min_chars") or DEFAULT_OCR_DOC_MIN_CHARS)
    if int(text_chars or 0) < min_chars:
        return "low_chars"
    if str(extract_engine or "").startswith("news_feed"):
        return "pass"
    if confidence is not None:
        try:
            if float(confidence) < float(config.get("ocr_min_confidence") or DEFAULT_OCR_MIN_CONFIDENCE):
                return "low_confidence"
        except Exception:
            pass
    if str(extract_engine or "").startswith("html_scrape") or str(extract_engine or "").startswith("scrape"):
        return "html_scrape_only"
    return "pass"


def _ocr_quality_scan_decision(
    doc_row: Dict[str, Any],
    content_for_quality: str,
    config: Dict[str, Any],
    seen_fingerprints: Optional[set[str]] = None,
) -> Dict[str, Any]:
    if not bool(config.get("ocr_scan_enabled", True)):
        return {"enabled": False, "keep": True, "reason": "disabled", "fingerprint": ""}
    text = str(content_for_quality or "").strip()
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    fingerprint = hashlib.sha256(normalized[:6000].encode("utf-8", "ignore")).hexdigest() if normalized else ""
    if not normalized:
        return {"enabled": True, "keep": False, "reason": "no_text", "fingerprint": fingerprint}
    quality_flag = str(doc_row.get("quality_flag") or "").strip().lower()
    if quality_flag in {"ocr_unverified_scrape_only", "visual_ocr_required_unavailable"}:
        return {"enabled": True, "keep": False, "reason": quality_flag, "fingerprint": fingerprint}
    min_chars = int(config.get("ocr_scan_min_chars") or DEFAULT_OCR_SCAN_MIN_CHARS)
    if int(doc_row.get("text_chars") or 0) < min_chars:
        return {"enabled": True, "keep": False, "reason": f"chars_below_scan_min:{int(doc_row.get('text_chars') or 0)}<{min_chars}", "fingerprint": fingerprint}
    min_quality_score = float(config.get("ocr_scan_min_quality_score") or DEFAULT_OCR_SCAN_MIN_QUALITY_SCORE)
    if float(doc_row.get("quality_score") or 0.0) < min_quality_score:
        return {"enabled": True, "keep": False, "reason": f"quality_score_below_scan_min:{float(doc_row.get('quality_score') or 0.0)}<{min_quality_score}", "fingerprint": fingerprint}
    min_semantic_hits = int(config.get("ocr_scan_min_semantic_hits") or DEFAULT_OCR_SCAN_MIN_SEMANTIC_HITS)
    if int(doc_row.get("semantic_section_hits") or 0) < min_semantic_hits:
        return {"enabled": True, "keep": False, "reason": f"semantic_hits_below_scan_min:{int(doc_row.get('semantic_section_hits') or 0)}<{min_semantic_hits}", "fingerprint": fingerprint}
    min_structure_score = float(config.get("ocr_scan_min_structure_score") or DEFAULT_OCR_SCAN_MIN_STRUCTURE_SCORE)
    if float(doc_row.get("structure_score") or 0.0) < min_structure_score:
        return {"enabled": True, "keep": False, "reason": f"structure_score_below_scan_min:{float(doc_row.get('structure_score') or 0.0)}<{min_structure_score}", "fingerprint": fingerprint}
    min_numeric_fidelity = float(config.get("ocr_scan_min_numeric_fidelity") or DEFAULT_OCR_SCAN_MIN_NUMERIC_FIDELITY)
    if float(doc_row.get("numeric_fidelity_score") or 0.0) < min_numeric_fidelity:
        return {"enabled": True, "keep": False, "reason": f"numeric_fidelity_below_scan_min:{float(doc_row.get('numeric_fidelity_score') or 0.0)}<{min_numeric_fidelity}", "fingerprint": fingerprint}
    if fingerprint and seen_fingerprints is not None:
        if fingerprint in seen_fingerprints:
            return {"enabled": True, "keep": False, "reason": "duplicate_content_fingerprint", "fingerprint": fingerprint}
        seen_fingerprints.add(fingerprint)
    source_doc_class = str(doc_row.get("source_doc_class") or "").strip().lower()
    extraction_mode = str(doc_row.get("extraction_mode") or _extraction_mode_for_engine(str(doc_row.get("extract_engine") or ""))).strip().lower()
    title_url = " ".join(str(doc_row.get(key) or "") for key in ("title", "url", "source_provider")).lower()
    macro_markers = {
        "financial stability",
        "quarterly review",
        "federal reserve",
        "bis.org",
        "finra",
        "margin requirements",
        "market risk",
        "macro",
        "liquidity",
        "volatility",
        "leverage",
    }
    macro_hits = sum(1 for marker in macro_markers if marker in normalized or marker in title_url)
    if source_doc_class == "pdf" and extraction_mode == "native_pdf_text" and macro_hits >= 2:
        return {
            "enabled": True,
            "keep": True,
            "reason": "kept_macro_document_anchor",
            "fingerprint": fingerprint,
            "topic_anchor_profile": "macro_market_context",
            "macro_anchor_hits": macro_hits,
        }
    topic_anchors = [str(a).strip().lower() for a in list(config.get("ocr_scan_topic_anchors") or []) if str(a).strip()]
    _raw_anchor_hits = config.get("ocr_scan_min_anchor_hits")
    min_anchor_hits = int(DEFAULT_OCR_SCAN_MIN_ANCHOR_HITS if _raw_anchor_hits is None else _raw_anchor_hits)
    if topic_anchors and min_anchor_hits > 0:
        anchor_hits = sum(1 for anchor in topic_anchors if anchor in normalized)
        if anchor_hits < min_anchor_hits:
            return {
                "enabled": True,
                "keep": False,
                "reason": f"topic_anchor_retention_failed:{anchor_hits}/{min_anchor_hits}",
                "fingerprint": fingerprint,
                "anchor_hits": anchor_hits,
                "anchors_checked": len(topic_anchors),
                "topic_anchor_profile": "ticker_or_topic_specific",
            }
    return {"enabled": True, "keep": True, "reason": "kept", "fingerprint": fingerprint, "topic_anchor_profile": "ticker_or_topic_specific"}


def _source_doc_class(*, url: str, local_path: Optional[Path]) -> str:
    if _is_equity_news_feed_url(url):
        return "news_feed"
    path = local_path or (Path(url) if url else None)
    suffix = str(path.suffix if path is not None else Path(url).suffix).lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}:
        return "image"
    if str(url or "").lower().startswith(("http://", "https://")):
        return "web_page"
    return "unknown"


def _extraction_mode_for_engine(engine: str) -> str:
    name = str(engine or "").strip().lower()
    if name.startswith("news_feed"):
        return "news_feed"
    if name == "native_pdf_text":
        return "native_pdf_text"
    if name.startswith("html_scrape") or name.startswith("scrape"):
        return "html_scrape"
    if name:
        return "visual_ocr"
    return "unknown"


def _is_equity_news_feed_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    return "feeds.finance.yahoo.com/rss/2.0/headline" in value and "s=" in value


def _ticker_from_equity_news_feed_url(url: str) -> str:
    try:
        from urllib.parse import parse_qs

        parsed = urlparse(str(url or ""))
        ticker = str((parse_qs(parsed.query).get("s") or [""])[0]).strip().upper()
        return re.sub(r"[^A-Z0-9.-]+", "", ticker)[:12]
    except Exception:
        return ""


def _parse_equity_news_feed_articles(text: str, *, ticker: str, feed_url: str, limit: int = 12) -> list[Dict[str, str]]:
    """Parse finance RSS scrape text into bounded article rows for RAG ingestion."""
    raw = html.unescape(str(text or "").replace("\u200c", " ")).strip()
    if not raw:
        return []

    articles: list[Dict[str, str]] = []
    pattern = re.compile(
        r"(?:^|\s)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s+"
        r"(?P<url>https?://\S+?)\s+"
        r"(?P<published>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+\w+\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+\+0000)\s+"
        r"(?P<body>.*?)(?=\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s+https?://|\Z)",
        re.I | re.S,
    )
    for match in pattern.finditer(raw):
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        if not body:
            continue
        headline = body
        sentence = re.match(r"(.{24,220}?[.!?])\s+", body)
        if sentence:
            headline = sentence.group(1)
        headline = headline[:240].strip(" -")
        article_url = match.group("url").strip()
        articles.append(
            {
                "ticker": ticker,
                "feed_url": feed_url,
                "url": article_url,
                "published_at": match.group("published").strip(),
                "headline": headline,
                "snippet": body[:700],
            }
        )
        if len(articles) >= limit:
            break

    if articles:
        return articles

    compact = re.sub(r"\s+", " ", raw).strip()
    return [
        {
            "ticker": ticker,
            "feed_url": feed_url,
            "url": feed_url,
            "published_at": "",
            "headline": f"{ticker} finance news feed snapshot" if ticker else "finance news feed snapshot",
            "snippet": compact[:900],
        }
    ]


def _format_equity_news_article(article: Dict[str, str], *, topic: str, batch_id: str) -> str:
    ticker = str(article.get("ticker") or "").strip().upper()
    headline = str(article.get("headline") or "").strip()
    snippet = str(article.get("snippet") or "").strip()
    published_at = str(article.get("published_at") or "").strip()
    article_url = str(article.get("url") or "").strip()
    feed_url = str(article.get("feed_url") or "").strip()
    return "\n".join(
        [
            f"[NightlyNewsFeed] {ticker} trading article",
            f"Ticker: {ticker}",
            f"Headline: {headline}",
            f"Published: {published_at}",
            f"Article URL: {article_url}",
            f"Feed URL: {feed_url}",
            f"Topic: {topic}",
            f"Batch: {batch_id}",
            "Trading use: catalyst review, event risk, market regime context, swing trade homework.",
            "Risk controls: paper-only research, human approval required, no live broker execution.",
            "",
            snippet,
        ]
    ).strip()


def _upsert_financial_news_articles(articles: list[Dict[str, str]], *, topic: str, batch_id: str) -> Dict[str, Any]:
    """Write parsed article rows into Apollo's main financial corpus for long-lived learning."""
    if not articles:
        return {"ok": True, "count": 0, "ids": []}
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:
        return {"ok": False, "count": 0, "ids": [], "error": f"chromadb_import_failed:{type(exc).__name__}:{exc}"}

    documents: list[str] = []
    metadatas: list[Dict[str, Any]] = []
    ids: list[str] = []
    now = _now_utc().isoformat().replace("+00:00", "Z")
    for article in articles:
        ticker = str(article.get("ticker") or "").strip().upper()
        article_url = str(article.get("url") or article.get("feed_url") or "").strip()
        stable = hashlib.sha256(f"{ticker}|{article_url}|{article.get('published_at')}".encode("utf-8", "ignore")).hexdigest()[:20]
        ids.append(f"newsfeed_{ticker.lower() or 'market'}_{stable}")
        documents.append(_format_equity_news_article(article, topic=topic, batch_id=batch_id))
        metadatas.append(
            {
                "kind": "news_feed",
                "source": "apollo_nightly_news_feed",
                "title": str(article.get("headline") or ""),
                "url": article_url,
                "feed_url": str(article.get("feed_url") or ""),
                "ticker": ticker,
                "published_at": str(article.get("published_at") or ""),
                "topic": topic,
                "batch_id": batch_id,
                "tags": "nightly,news_feed,trading,apollo,hipporag",
                "provenance_class": "real_public",
                "research_only": True,
                "timestamp": now,
            }
        )
    try:
        client = chromadb.PersistentClient(
            path=_resolved_rag_dir(),
            settings=Settings(allow_reset=False, anonymized_telemetry=False),
        )
        col = client.get_or_create_collection(os.getenv("RAG_COLLECTION", "apollo_financial"))
        col.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return {"ok": True, "count": len(ids), "ids": ids, "collection": os.getenv("RAG_COLLECTION", "apollo_financial")}
    except Exception as exc:
        try:
            from common.rag_store import _ollama_embed

            embeddings = _ollama_embed(documents)
            client = chromadb.PersistentClient(
                path=_resolved_rag_dir(),
                settings=Settings(allow_reset=False, anonymized_telemetry=False),
            )
            col = client.get_or_create_collection(os.getenv("RAG_COLLECTION", "apollo_financial"))
            col.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
            return {
                "ok": True,
                "count": len(ids),
                "ids": ids,
                "collection": os.getenv("RAG_COLLECTION", "apollo_financial"),
                "embedding_mode": f"fallback_ollama_after_{type(exc).__name__}",
            }
        except Exception as fallback_exc:
            return {
                "ok": False,
                "count": 0,
                "ids": [],
                "error": f"financial_news_upsert_failed:{type(fallback_exc).__name__}:{fallback_exc}",
                "first_error": f"{type(exc).__name__}:{exc}",
            }


def _doc_needs_visual_ocr(*, source_doc_class: str, native_attempt: Dict[str, Any], successful_visual_attempt: bool) -> bool:
    if source_doc_class == "image":
        return True
    native_chars = len(str(native_attempt.get("markdown") or native_attempt.get("text") or "").strip())
    if source_doc_class == "pdf" and native_chars <= 0 and not successful_visual_attempt:
        return True
    return False


def _extract_document_cascade(
    local_path: Path,
    *,
    topic: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    from common.mcp import ensure_loaded, run as mcp_run

    attempts: list[Dict[str, Any]] = []
    max_chars = int(config.get("ocr_max_chars") or DEFAULT_OCR_MAX_CHARS)
    max_pages = int(config.get("ocr_max_pages") or DEFAULT_OCR_MAX_PAGES)
    route_mode = str(config.get("ocr_route_mode") or DEFAULT_OCR_ROUTE_MODE).strip().lower() or DEFAULT_OCR_ROUTE_MODE
    quality_first = route_mode != "balanced"
    primary_engine = "gb10_auto" if quality_first else "auto"
    goal = (
        "Perform faithful OCR for a finance/trading research document. "
        f"Topic: {topic}. "
        "Return markdown only. Preserve table row boundaries, headings, bullet lists, and numeric values exactly as visible. "
        "Do not summarize, paraphrase, or rewrite into generic trading advice."
    )
    suffix = local_path.suffix.lower()
    native_pdf_strong = False
    if suffix == ".pdf":
        native = _extract_pdf_native_text(local_path, max_pages=max_pages, max_chars=max_chars)
        native["extraction_mode"] = "native_pdf_text"
        attempts.append(native)
        native_pdf_strong = bool(native.get("ok")) and not _is_low_quality_extract(
            str(native.get("markdown") or native.get("text") or ""),
            native.get("confidence"),
            config,
        )

    policy_attempts: list[Dict[str, Any]] = []
    if not native_pdf_strong:
        policy_attempts = [{"engine": primary_engine, "route_mode": route_mode, "consensus": quality_first}]
        if quality_first:
            policy_attempts.append({"engine": "auto", "route_mode": "balanced", "consensus": False})

    def _record_policy_attempt(policy: Dict[str, Any]) -> Dict[str, Any]:
        try:
            ensure_loaded()
            response = mcp_run(
                "ocr.dual",
                {
                    "path": str(local_path),
                    "goal": goal,
                    "engine": policy["engine"],
                    "max_pages": max_pages,
                    "max_chars": max_chars,
                    "route_mode": policy["route_mode"],
                    "consensus": bool(policy.get("consensus")),
                    "use_gateway": True,
                },
            )
        except Exception as exc:
            attempts.append(
                {
                    "ok": False,
                    "engine": str(policy.get("engine") or ""),
                    "extraction_mode": "visual_ocr",
                    "reason": "engine_failed",
                    "error": f"{type(exc).__name__}:{exc}",
                    "text": "",
                    "markdown": "",
                    "pages": 0,
                    "confidence": None,
                    "engine_chain": [],
                    "attempts": [],
                    "quality": {},
                }
            )
            return attempts[-1]

        response_quality = dict(response.get("quality") or {})
        response_text = str(response.get("markdown") or response.get("text") or "")
        response_attempts = list(response.get("attempts") or [])
        attempts.append(
            {
                "ok": bool(response.get("ok")),
                "engine": str(response.get("engine") or policy["engine"]),
                "extraction_mode": "visual_ocr",
                "reason": str(response.get("reason") or "ocr"),
                "error": str(response.get("error") or ""),
                "text": str(response.get("text") or ""),
                "markdown": response_text,
                "pages": int(response.get("pages") or 0),
                "confidence": response.get("confidence"),
                "engine_chain": list(response.get("engine_chain") or []),
                "attempts": response_attempts,
                "blocks": list(response.get("blocks") or []),
                "tables": list(response.get("tables") or []),
                "reading_order": list(response.get("reading_order") or []),
                "bbox": list(response.get("bbox") or []),
                "quality": response_quality,
                "route_mode": str(policy.get("route_mode") or route_mode),
                "doc_class": str(response_quality.get("doc_class") or ""),
                "quality_score": round(float(response_quality.get("score") or 0.0) * 100.0, 2),
                "structure_score": float(response_quality.get("structure_score") or 0.0),
                "numeric_fidelity_score": float(response_quality.get("numeric_fidelity_score") or 0.0),
                "table_rows": int(response_quality.get("table_rows") or 0),
            }
        )
        return attempts[-1]

    for policy in policy_attempts:
        latest_attempt = _record_policy_attempt(policy)
        if not _is_low_quality_extract(
            str(latest_attempt.get("markdown") or latest_attempt.get("text") or ""),
            latest_attempt.get("confidence"),
            config,
        ):
            break

    min_chars = int(config.get("ocr_doc_min_chars") or DEFAULT_OCR_DOC_MIN_CHARS)
    strong_attempt_exists = any(
        bool(item.get("ok"))
        and len(str(item.get("markdown") or item.get("text") or "").strip()) >= min_chars
        and not _is_low_quality_extract(str(item.get("markdown") or item.get("text") or ""), item.get("confidence"), config)
        for item in attempts
    )
    if suffix == ".pdf" and quality_first and not strong_attempt_exists:
        _record_policy_attempt({"engine": "gb10_qwen_ocr", "route_mode": route_mode, "consensus": False})

    ranked = sorted(
        attempts,
        key=lambda item: float(item.get("quality_score") or _extract_quality_score(str(item.get("markdown") or item.get("text") or ""), item.get("confidence"), config)),
        reverse=True,
    )
    best = ranked[0] if ranked else {"engine": "", "text": "", "markdown": "", "reason": "no_attempts", "pages": 0, "confidence": None, "ok": False}
    native_ranked = [
        item
        for item in ranked
        if str(item.get("engine") or "") == "native_pdf_text"
        and bool(item.get("ok"))
        and not _is_low_quality_extract(str(item.get("markdown") or item.get("text") or ""), item.get("confidence"), config)
    ]
    if suffix == ".pdf" and native_ranked:
        best = native_ranked[0]
    elif quality_first:
        gb10_ranked = [
            item
            for item in ranked
            if str(item.get("engine") or "").startswith("gb10_")
            and bool(item.get("ok"))
            and len(str(item.get("markdown") or item.get("text") or "").strip()) >= min_chars
        ]
        if gb10_ranked:
            best = gb10_ranked[0]
    ensemble_candidates = [
        item for item in attempts
        if bool(item.get("ok"))
        and len(str(item.get("markdown") or item.get("text") or "").strip()) >= min_chars
        and str(item.get("extraction_mode") or "") != "html_scrape"
    ]
    if len(ensemble_candidates) >= 2:
        try:
            from common.ocr_dual_tool import _line_vote_majority, _quality_bundle
            vote_inputs = [
                {
                    "engine": str(item.get("engine") or ""),
                    "markdown": str(item.get("markdown") or item.get("text") or ""),
                    "quality": dict(item.get("quality") or {"score": float(item.get("quality_score") or 0.0) / 100.0}),
                }
                for item in ensemble_candidates
            ]
            voted = _line_vote_majority(vote_inputs, max_chars=max_chars)
            if voted.get("ok") and len(str(voted.get("markdown") or "").strip()) >= min_chars:
                voted_score = float((voted.get("quality") or {}).get("score") or 0.0)
                best_score = float((best.get("quality") or {}).get("score") or 0.0) or float(best.get("quality_score") or 0.0) / 100.0
                if voted_score >= best_score - 0.03:
                    voted["extraction_mode"] = "visual_ocr"
                    voted["route_mode"] = route_mode
                    voted["pages"] = max(int(item.get("pages") or 0) for item in ensemble_candidates)
                    voted["quality_score"] = round(voted_score * 100.0, 2)
                    best = voted
        except Exception:
            pass
    best_markdown = str(best.get("markdown") or best.get("text") or "").strip()[:max_chars]
    best_text = str(best.get("text") or best_markdown).strip()[:max_chars]
    best_engine = str(best.get("engine") or "")
    quality_score = float(best.get("quality_score") or _extract_quality_score(best_markdown, best.get("confidence"), config))
    attempts_out = [
        {
            "engine": str(item.get("engine") or ""),
            "ok": bool(item.get("ok")),
            "pages": int(item.get("pages") or 0),
            "confidence": item.get("confidence"),
            "chars": len(str(item.get("markdown") or item.get("text") or "").strip()),
            "reason": str(item.get("reason") or ""),
            "error": str(item.get("error") or ""),
            "latency_ms": float(item.get("latency_ms") or 0.0),
            "structure_score": float(item.get("structure_score") or 0.0),
            "numeric_fidelity_score": float(item.get("numeric_fidelity_score") or 0.0),
            "fallback_reason": str(item.get("fallback_reason") or ""),
        }
        for item in attempts
    ]
    best_quality = dict(best.get("quality") or {})
    best_structure = float(best.get("structure_score") or best_quality.get("structure_score") or 0.0)
    best_numeric = float(best.get("numeric_fidelity_score") or best_quality.get("numeric_fidelity_score") or 0.0)
    best_table_rows = int(best.get("table_rows") or best_quality.get("table_rows") or 0)
    best_route_mode = str(best.get("route_mode") or route_mode)
    best_doc_class = str(best.get("doc_class") or best_quality.get("doc_class") or "")
    engine_chain = list(best.get("engine_chain") or [])
    if not engine_chain and best_engine:
        engine_chain = [best_engine]
    if best_engine == "native_pdf_text":
        computed_semantic_hits = _semantic_section_hits(best_markdown)
        best_structure = max(best_structure, _structure_score(best_markdown, computed_semantic_hits))
        best_numeric = max(best_numeric, _numeric_fidelity_score(best_markdown))
    _fc = dict(best_quality.get("finance_consistency") or {})
    _fb = dict(best_quality.get("finbert_eval") or {})
    best_finance_consistency_score = float(_fc.get("score") or 0.0)
    best_finbert_label = str(_fb.get("label") or "")
    return {
        "text": best_text,
        "markdown": best_markdown,
        "meta": {
            "ok": bool(best.get("ok")) and bool(best_text),
            "engine": best_engine,
            "reason": str(best.get("reason") or ""),
            "pages": int(best.get("pages") or 0),
            "confidence": best.get("confidence"),
            "error": str(best.get("error") or ""),
            "attempts": attempts_out,
            "fallback_used": len(attempts_out) > 1,
            "quality_score": quality_score,
            "structure_score": best_structure,
            "numeric_fidelity_score": best_numeric,
            "table_rows": best_table_rows,
            "engine_chain": engine_chain,
            "route_mode": best_route_mode,
            "doc_class": best_doc_class,
            "blocks": list(best.get("blocks") or []),
            "tables": list(best.get("tables") or []),
            "reading_order": list(best.get("reading_order") or []),
            "bbox": list(best.get("bbox") or []),
            "benchmark_proxy_scores": {
                "structure_lane": int(round(best_structure * 100.0)),
                "correctness_lane": int(round(best_numeric * 100.0)),
            },
            "extraction_mode": str(best.get("extraction_mode") or _extraction_mode_for_engine(best_engine)),
            "finance_consistency_score": best_finance_consistency_score,
            "finbert_label": best_finbert_label,
        },
    }


def _ocr_backend_readiness(config: Dict[str, Any]) -> Dict[str, Any]:
    from common.ocr_dual_tool import gb10_ocr_backend_readiness

    gateway_url = str(os.getenv("APOLLO_GB10_OCR_GATEWAY_URL", "")).strip()
    primary_pdf = str(config.get("ocr_primary_engine_pdf") or DEFAULT_OCR_PRIMARY_ENGINE_PDF).strip().lower()
    primary_img = str(config.get("ocr_primary_engine_image") or DEFAULT_OCR_PRIMARY_ENGINE_IMAGE).strip().lower()
    explicit_qwen_primary = bool(config.get("ocr_qwen_primary")) or (primary_pdf == "gb10_qwen_ocr" and primary_img == "gb10_qwen_ocr")
    readiness = gb10_ocr_backend_readiness(
        {
            "use_gateway": True,
            "gateway_url": gateway_url,
            "paddle_url": str(os.getenv("APOLLO_GB10_PADDLEOCR_VL_URL", "")).strip(),
            "got_url": str(os.getenv("APOLLO_GB10_GOT_OCR_URL", "")).strip(),
            "qwen_primary": explicit_qwen_primary,
            "qwen_model": str(os.getenv("APOLLO_GB10_QWEN_OCR_MODEL", os.getenv("VISION_QWEN25VL_MODEL", "qwen2.5vl:7b"))).strip(),
            "qwen_fallback_model": str(os.getenv("APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL", os.getenv("VISION_QWEN3VL_MODEL", "qwen3-vl:32b"))).strip(),
            "qwen_ollama_url": str(os.getenv("APOLLO_GB10_QWEN_OLLAMA_URL", os.getenv("VISION_OLLAMA_URL", os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")))).strip(),
        }
    )
    readiness["qwen_primary"] = explicit_qwen_primary
    readiness["visual_ready"] = bool(readiness.get("ready"))
    readiness["visual_mode"] = str(readiness.get("mode") or "")
    readiness["visual_fail_reason"] = str(readiness.get("fail_reason") or "")
    native_fail_reasons: list[str] = []
    for module_name in ("pypdf", "fitz"):
        try:
            __import__(module_name)
            readiness["native_pdf_supported"] = True
            readiness["native_pdf_mode"] = module_name
            readiness["native_pdf_fail_reason"] = ""
            break
        except Exception as exc:
            native_fail_reasons.append(f"{module_name}:{type(exc).__name__}")
    else:
        readiness["native_pdf_supported"] = False
        readiness["native_pdf_mode"] = "unavailable"
        readiness["native_pdf_fail_reason"] = ",".join(native_fail_reasons) or "module_unavailable"
    lane_scorecard: Dict[str, Any] = {}
    slo_summary: Dict[str, Any] = {"pass_count": 0, "total": 0, "pass_ratio": 0.0}
    smoke_gate: Dict[str, Any] = {}
    gateway_health: Dict[str, Any] = {}
    gateway_checks = dict(readiness.get("checks") or {})
    gateway_health_check = dict(gateway_checks.get("gateway_health") or {})
    gateway_extract_check = dict(gateway_checks.get("gateway_extract") or {})
    probe_gateway_health = bool(
        readiness.get("mode") == "gateway"
        or gateway_health_check.get("ok")
        or gateway_extract_check.get("ok")
    )
    if gateway_url and probe_gateway_health:
        health_url = gateway_url.rstrip("/")
        if health_url.endswith("/ocr/extract"):
            health_url = health_url[: -len("/ocr/extract")]
        health_url = health_url + "/ocr/health"
        try:
            resp = requests.get(health_url, timeout=6)
            if resp.ok:
                gateway_health = dict(resp.json() or {})
                smoke_gate = dict(gateway_health.get("smoke") or {})
                engine_details = dict(gateway_health.get("engine_details") or {})
                for lane in ("gb10_paddleocr_vl", "gb10_got_ocr2", "mineru2_5", "olmocr2", "gb10_qwen_ocr"):
                    row = dict(engine_details.get(lane) or {})
                    if not row:
                        continue
                    ready_gate = dict(row.get("ready_gate") or {})
                    slo = dict(row.get("slo") or {})
                    lane_scorecard[lane] = {
                        "ready": bool(row.get("ready")),
                        "health_ready": bool(row.get("health_ready")),
                        "reason": str(row.get("reason") or ""),
                        "lane_signature_modes": list(row.get("lane_signature_modes") or []),
                        "slo": slo,
                        "ready_gate": ready_gate,
                        "smoke": dict(row.get("smoke") or {}),
                    }
                    slo_summary["total"] = int(slo_summary.get("total") or 0) + 1
                    if bool(ready_gate.get("pass")):
                        slo_summary["pass_count"] = int(slo_summary.get("pass_count") or 0) + 1
                total = int(slo_summary.get("total") or 0)
                if total > 0:
                    slo_summary["pass_ratio"] = round(float(slo_summary.get("pass_count") or 0) / float(total), 3)
        except Exception as exc:
            gateway_health = {"ok": False, "error": f"{type(exc).__name__}:{exc}", "url": health_url}
    readiness["lane_scorecard"] = lane_scorecard
    readiness["slo_summary"] = slo_summary
    readiness["smoke_gate"] = smoke_gate
    readiness["gateway_health"] = gateway_health
    return readiness


def _stage_result_view(stage_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(stage_payload, dict):
        return {}
    nested = stage_payload.get("result")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in stage_payload.items():
            if key == "result":
                continue
            merged.setdefault(key, value)
        merged["result"] = nested
        return merged
    return dict(stage_payload)


def _gather_source_mix(stage_payload: Dict[str, Any]) -> Dict[str, Any]:
    stage = _stage_result_view(stage_payload)
    quality = dict(stage.get("quality") or {})
    accepted_rows = [item for item in list(quality.get("accepted_sources") or []) if isinstance(item, dict)]
    selected_rows = accepted_rows or [item for item in list(stage.get("sources") or []) if isinstance(item, dict)]
    seed_sources = 0
    real_sources = 0
    unknown_sources = 0
    for row in selected_rows:
        url = str(row.get("url") or "").strip()
        provider = str(row.get("provider") or row.get("source_provider") or "").strip().lower()
        domain = str(row.get("domain") or "").strip().lower()
        if _is_seed_fallback_source(url):
            seed_sources += 1
            continue
        if provider.startswith("seed_fallback") or domain == "local_seed":
            seed_sources += 1
            continue
        if url:
            real_sources += 1
        else:
            unknown_sources += 1
    total_sources = len(selected_rows)
    seed_only = bool(total_sources) and seed_sources == total_sources and real_sources == 0
    return {
        "total_sources": total_sources,
        "seed_sources": seed_sources,
        "real_sources": real_sources,
        "unknown_sources": unknown_sources,
        "seed_only": bool(seed_only),
    }


def _score_gather_stage(stage_payload: Dict[str, Any]) -> int:
    stage = _stage_result_view(stage_payload)
    quality = dict(stage.get("quality") or {})
    accepted = int(quality.get("accepted_count") or len(stage.get("sources") or []))
    avg_score = float(quality.get("avg_score") or 0.0)
    trusted_count = int(quality.get("trusted_count") or 0)
    pdf_count = int(quality.get("pdf_count") or 0)
    score = 0.0
    score += min(30.0, accepted * 15.0)
    score += min(30.0, avg_score * 0.3)
    score += min(25.0, trusted_count * 12.5)
    score += min(15.0, pdf_count * 7.5)
    return int(max(0, min(100, round(score))))


def _score_ocr_stage(stage_payload: Dict[str, Any]) -> int:
    stage = _stage_result_view(stage_payload)
    docs = [item for item in list(stage.get("documents") or []) if isinstance(item, dict)]
    if not docs:
        return 0
    quality = dict(stage.get("quality") or {})
    chars = [int(item.get("text_chars") or 0) for item in docs]
    avg_chars = sum(chars) / len(chars)
    min_chars = min(chars)
    non_scrape_ratio = float(quality.get("non_scrape_ratio") or 0.0)
    if non_scrape_ratio <= 0.0:
        non_scrape = sum(1 for item in docs if "scrape" not in str(item.get("extract_engine") or "").lower())
        non_scrape_ratio = float(non_scrape / max(1, len(docs)))
    fallback_used = sum(1 for item in docs if bool(item.get("fallback_used")))
    structure_lane = int(((quality.get("benchmark_proxy_scores") or {}).get("structure_lane")) or 0)
    correctness_lane = int(((quality.get("benchmark_proxy_scores") or {}).get("correctness_lane")) or 0)
    avg_structure = float(quality.get("avg_structure_score") or 0.0)
    avg_numeric = float(quality.get("avg_numeric_fidelity") or 0.0)
    scrape_only = bool(quality.get("ocr_unverified_scrape_only"))
    score = 0.0
    score += min(22.0, avg_chars / 1200.0 * 22.0)
    score += min(18.0, min_chars / 600.0 * 18.0)
    score += min(15.0, non_scrape_ratio * 15.0)
    score += min(12.0, avg_structure * 12.0)
    score += min(12.0, avg_numeric * 12.0)
    score += min(11.0, (structure_lane / 100.0) * 11.0)
    score += min(10.0, (correctness_lane / 100.0) * 10.0)
    score -= min(10.0, fallback_used * 2.0)
    if scrape_only:
        score = min(score, 35.0)
    if bool(quality.get("gate_pass")):
        score = max(score, 85.0)
    return int(max(0, min(100, round(score))))


def _score_hipporag_stage(stage_payload: Dict[str, Any]) -> int:
    stage = _stage_result_view(stage_payload)
    remaining_before = int(stage.get("remaining_before") or 0)
    remaining_after = int(stage.get("remaining_after") or 0)
    processed = int(stage.get("processed") or 0)
    processed_finance = int(stage.get("processed_finance") or processed)
    skipped_non_finance = int(stage.get("skipped_non_finance") or 0)
    requeued_low_quality = int(stage.get("requeued_low_quality") or 0)
    pending_quarantine = int(stage.get("pending_quarantine") or 0)
    triples = int(stage.get("triples") or 0)
    validated_non_triple_finance = int(stage.get("validated_non_triple_finance") or 0)
    completed = bool(stage.get("completed"))
    gate_pass = bool((stage.get("quality") or {}).get("gate_pass"))
    if remaining_before <= 0 and completed:
        return 85
    progress_ratio = max(0.0, min(1.0, (remaining_before - remaining_after) / float(max(1, remaining_before))))
    score = 0.0
    score += 35.0 * progress_ratio
    score += min(40.0, (triples * 4.0) + (validated_non_triple_finance * 2.0))
    score += 25.0 if completed else min(15.0, processed_finance * 2.0)
    score -= min(10.0, requeued_low_quality * 1.5)
    if pending_quarantine > 0:
        score -= min(12.0, pending_quarantine * 0.5)
    if processed_finance == 0 and remaining_before > 0:
        score = min(score, 20.0)
    score_out = int(max(0, min(100, round(score))))
    if gate_pass:
        score_out = max(score_out, 70)
    return score_out


def _score_self_check_stage(stage_payload: Dict[str, Any]) -> int:
    stage = _stage_result_view(stage_payload)
    if bool(stage.get("ok")):
        return 90
    checks = dict(stage.get("checks") or {})
    if not checks:
        return 20
    total = len(checks)
    good = sum(1 for item in checks.values() if isinstance(item, dict) and item.get("ok"))
    return int(max(0, min(100, round((good / float(max(1, total))) * 100.0))))


def _stage_scores_from_status(status: Dict[str, Any]) -> Dict[str, int]:
    stages = dict(status.get("stages") or {})
    run_dir_str = str(status.get("run_dir") or "").strip()
    run_dir = Path(run_dir_str) if run_dir_str else None

    def _stage_payload(key: str, file_name: str) -> Dict[str, Any]:
        payload = stages.get(key)
        if isinstance(payload, dict) and payload:
            return payload
        if run_dir is not None:
            candidate = run_dir / file_name
            if candidate.is_file():
                return _read_json(candidate, {})
        return {}

    return {
        "gather": _score_gather_stage(_stage_payload("gather", "01_data_gather.json")),
        "ocr": _score_ocr_stage(_stage_payload("ocr", "02_ocr_ingest.json")),
        "hipporag": _score_hipporag_stage(_stage_payload("hipporag", "03_hipporag.json")),
        "self_check": _score_self_check_stage(_stage_payload("self_check", "04_self_check.json")),
    }


def _median(values: list[float]) -> float:
    nums = sorted(float(item) for item in values if item is not None)
    if not nums:
        return 0.0
    size = len(nums)
    mid = size // 2
    if size % 2 == 1:
        return float(nums[mid])
    return float((nums[mid - 1] + nums[mid]) / 2.0)


def _status_finished_key(status: Dict[str, Any]) -> str:
    return str(status.get("finished_at") or status.get("updated_at") or status.get("started_at") or "")


def _iter_background_run_statuses(window: int = 7) -> list[Dict[str, Any]]:
    runs: list[Dict[str, Any]] = []
    if not RUNS_ROOT.exists():
        return runs
    for status_file in RUNS_ROOT.rglob("status.json"):
        run_dir = status_file.parent
        if not _is_production_run_dir(run_dir):
            continue
        row = _read_json(status_file, {})
        if not isinstance(row, dict) or not _is_background_run_status(row):
            continue
        row["run_dir"] = str(run_dir.resolve())
        runs.append(row)
    runs.sort(key=_status_finished_key, reverse=True)
    return runs[: max(1, int(window))]


def _rolling_quality_snapshot(current_status: Dict[str, Any], *, window: int = 7) -> Dict[str, Any]:
    runs = _iter_background_run_statuses(window=max(1, int(window)))
    current_run_id = str(current_status.get("run_id") or "").strip()
    current_run_dir = str(current_status.get("run_dir") or "").strip()
    has_current = any(
        (str(run.get("run_id") or "").strip() == current_run_id)
        and (str(run.get("run_dir") or "").strip() == current_run_dir)
        for run in runs
    )
    if not has_current and _is_background_run_status(current_status):
        runs = [dict(current_status), *runs]
    runs = runs[: max(1, int(window))]
    overall_values: list[float] = []
    stage_values: Dict[str, list[float]] = {"gather": [], "ocr": [], "hipporag": [], "self_check": []}
    recent_quality_rows: list[Dict[str, Any]] = []
    for row in runs:
        quality = dict(row.get("quality") or {})
        stage_scores = dict(quality.get("stage_scores") or {})
        if not stage_scores:
            stage_scores = _stage_scores_from_status(row)
        if not stage_scores:
            continue
        overall = quality.get("overall_score")
        if overall is None:
            weights = {"gather": 30, "ocr": 25, "hipporag": 35, "self_check": 10}
            overall = int(
                round(
                    (int(stage_scores.get("gather", 0)) * weights["gather"]
                    + int(stage_scores.get("ocr", 0)) * weights["ocr"]
                    + int(stage_scores.get("hipporag", 0)) * weights["hipporag"]
                    + int(stage_scores.get("self_check", 0)) * weights["self_check"])
                    / 100.0
                )
            )
        overall_values.append(float(overall))
        gate_pass = bool(quality.get("gate_pass")) or (
            bool(row.get("pipeline_completed")) and bool(row.get("quality_gate_pass"))
        )
        recent_quality_rows.append(
            {
                "run_id": str(row.get("run_id") or ""),
                "overall_score": int(round(float(overall))),
                "gate_pass": gate_pass,
            }
        )
        for key in stage_values:
            stage_values[key].append(float(stage_scores.get(key, 0)))
    stage_medians = {key: int(round(_median(vals))) for key, vals in stage_values.items()}
    recent_high_quality_passes = 0
    recent_high_quality_scores: list[int] = []
    for row in recent_quality_rows:
        score = int(row.get("overall_score") or 0)
        if not bool(row.get("gate_pass")) or score < 80:
            break
        recent_high_quality_passes += 1
        recent_high_quality_scores.append(score)
    return {
        "window": max(1, int(window)),
        "run_count": len(overall_values),
        "overall_median": int(round(_median(overall_values))) if overall_values else 0,
        "overall_min": int(round(min(overall_values))) if overall_values else 0,
        "overall_max": int(round(max(overall_values))) if overall_values else 0,
        "overall_spread": int(round((max(overall_values) - min(overall_values)))) if len(overall_values) >= 2 else 0,
        "stage_medians": stage_medians,
        "recent_high_quality_passes": recent_high_quality_passes,
        "recent_high_quality_floor": min(recent_high_quality_scores) if recent_high_quality_scores else 0,
        "recent_quality_rows": recent_quality_rows[: max(1, int(window))],
    }


def _confidence_band(*, quality: Dict[str, Any], stage_details: Dict[str, Any], rolling: Dict[str, Any]) -> Dict[str, Any]:
    overall = int(quality.get("overall_score") or 0)
    rolling_median = int(rolling.get("overall_median") or 0)
    run_count = int(rolling.get("run_count") or 0)
    spread = int(rolling.get("overall_spread") or 0)
    variance = abs(overall - rolling_median)
    recent_high_quality_passes = int(rolling.get("recent_high_quality_passes") or 0)
    recent_high_quality_floor = int(rolling.get("recent_high_quality_floor") or 0)
    ocr_docs = int(((stage_details.get("ocr") or {}).get("documents")) or 0)
    gather_sources = int(((stage_details.get("gather") or {}).get("accepted_sources")) or 0)
    hippo_signal = int(((stage_details.get("hipporag") or {}).get("triples")) or 0) + int(
        ((stage_details.get("hipporag") or {}).get("processed")) or 0
    )
    artifact_completeness = 0
    artifact_completeness += 1 if gather_sources > 0 else 0
    artifact_completeness += 1 if ocr_docs > 0 else 0
    artifact_completeness += 1 if hippo_signal > 0 else 0
    artifact_ratio = round(artifact_completeness / 3.0, 3)
    basis = "rolling_history"
    if not bool(quality.get("gate_pass")) or run_count < 3:
        label = "low"
    elif variance <= 6 and spread <= 12 and artifact_ratio >= 0.9:
        label = "high"
    elif variance <= 14 and spread <= 25 and artifact_ratio >= 0.66:
        label = "medium"
    else:
        label = "low"
    if bool(quality.get("gate_pass")) and artifact_ratio >= 0.9:
        if recent_high_quality_passes >= 3 and recent_high_quality_floor >= 85:
            label = "high"
            basis = "recent_recovery_streak"
        elif label == "low" and recent_high_quality_passes >= 2 and recent_high_quality_floor >= 80:
            label = "medium"
            basis = "recent_recovery_streak"
    return {
        "label": label,
        "run_count": run_count,
        "overall_vs_rolling_delta": variance,
        "rolling_spread": spread,
        "artifact_ratio": artifact_ratio,
        "recent_high_quality_passes": recent_high_quality_passes,
        "recent_high_quality_floor": recent_high_quality_floor,
        "basis": basis,
    }


def _compute_quality(status: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    weights = {"gather": 30, "ocr": 25, "hipporag": 35, "self_check": 10}
    scores = _stage_scores_from_status(status)
    overall = int(
        round(
            (scores["gather"] * weights["gather"] + scores["ocr"] * weights["ocr"] + scores["hipporag"] * weights["hipporag"] + scores["self_check"] * weights["self_check"])
            / 100.0
        )
    )
    min_stage = int(config.get("strict_stage_min_score") or STRICT_STAGE_SCORE_MIN)
    gate_fail_reasons = [
        f"{stage_name}_score_below_min:{score}<{min_stage}"
        for stage_name, score in scores.items()
        if score < min_stage
    ]
    ocr_stage_min = int(config.get("ocr_stage_min_score") or DEFAULT_OCR_STAGE_MIN_SCORE)
    if int(scores.get("ocr") or 0) < ocr_stage_min:
        gate_fail_reasons.append(f"ocr_score_below_quality_min:{scores.get('ocr')}<{ocr_stage_min}")
    ocr_stage = _stage_result_view(dict((status.get("stages") or {}).get("ocr") or {}))
    ocr_quality = dict(ocr_stage.get("quality") or {})
    if ocr_quality and not bool(ocr_quality.get("gate_pass")):
        for reason in list(ocr_quality.get("gate_fail_reasons") or []):
            gate_fail_reasons.append(f"ocr_quality_gate:{reason}")
    gather_stage = _stage_result_view(dict((status.get("stages") or {}).get("gather") or {}))
    gather_quality = dict(gather_stage.get("quality") or {})
    if gather_quality and ("gate_pass" in gather_quality) and not bool(gather_quality.get("gate_pass")):
        gate_fail_reasons.append("gather_quality_gate:failed")
    hipporag_stage = _stage_result_view(dict((status.get("stages") or {}).get("hipporag") or {}))
    hipporag_quality = dict(hipporag_stage.get("quality") or {})
    if hipporag_quality and ("gate_pass" in hipporag_quality) and not bool(hipporag_quality.get("gate_pass")):
        for reason in list(hipporag_quality.get("fail_reasons") or []):
            gate_fail_reasons.append(f"hipporag_quality_gate:{reason}")
    rolling = _rolling_quality_snapshot(status, window=7)
    stage_details = _build_stage_details(status)
    confidence = _confidence_band(quality={"overall_score": overall, "gate_pass": len(gate_fail_reasons) == 0}, stage_details=stage_details, rolling=rolling)
    seen_reasons: set[str] = set()
    gate_fail_reasons = [reason for reason in gate_fail_reasons if not (reason in seen_reasons or seen_reasons.add(reason))]
    current = {
        "overall_score": overall,
        "stage_scores": scores,
        "gate_pass": len(gate_fail_reasons) == 0,
        "gate_fail_reasons": gate_fail_reasons,
    }
    return {
        "gate_mode": "strict_each_stage",
        "score_basis": "current_plus_rolling7_median",
        "weights": weights,
        "strict_stage_min_score": min_stage,
        "stage_scores": current["stage_scores"],
        "overall_score": current["overall_score"],
        "gate_pass": current["gate_pass"],
        "gate_fail_reasons": current["gate_fail_reasons"],
        "current_run_score": current["overall_score"],
        "rolling7_median_score": int(rolling.get("overall_median") or 0),
        "rolling7_stage_medians": dict(rolling.get("stage_medians") or {}),
        "current": current,
        "rolling": rolling,
        "confidence_band": confidence,
        "scored_at": _utc_iso(),
    }


def _build_stage_details(status: Dict[str, Any]) -> Dict[str, Any]:
    status = dict(status or {})
    run_dir_raw = str(status.get("run_dir") or "").strip()
    if run_dir_raw:
        try:
            run_dir = Path(run_dir_raw)
            if run_dir.exists():
                status = _reconcile_stage_files(run_dir, status)
        except Exception:
            pass
    stages = dict(status.get("stages") or {})
    gather = _stage_result_view(dict(stages.get("gather") or {}))
    gather_quality = dict(gather.get("quality") or {})
    focus_universe = dict(gather.get("focus_universe") or {})
    selected_theme = dict(focus_universe.get("selected_theme") or {})
    selected_tickers = [row for row in list(focus_universe.get("selected_tickers") or []) if isinstance(row, dict)]
    accepted_rows = [item for item in list(gather_quality.get("accepted_sources") or []) if isinstance(item, dict)]
    gather_intent_hits = sum(int(item.get("topic_intent_hits") or 0) for item in accepted_rows)
    ocr = _stage_result_view(dict(stages.get("ocr") or {}))
    ocr_quality = dict(ocr.get("quality") or {})
    docs = [item for item in list(ocr.get("documents") or []) if isinstance(item, dict)]
    chars = [int(item.get("text_chars") or 0) for item in docs] or [0]
    semantic_sections = [int(item.get("semantic_section_hits") or 0) for item in docs] or [0]
    table_docs = sum(1 for item in docs if bool(item.get("table_like_content")))
    hippo = _stage_result_view(dict(stages.get("hipporag") or {}))
    self_check = _stage_result_view(dict(stages.get("self_check") or {}))
    checks = dict(self_check.get("checks") or {})
    market_study_payload = dict(status.get("market_study") or {})
    if not market_study_payload:
        latest_market = _APOLLO_ROOT / "logs" / "market_study" / "latest_market_study.json"
        market_study_payload = _read_json(latest_market, {})
    market_proposals = [row for row in list(market_study_payload.get("proposals") or []) if isinstance(row, dict)]
    market_snapshots = [dict(row.get("snapshot") or {}) for row in market_proposals if isinstance(row.get("snapshot"), dict)]
    market_conf = [float(row.get("confidence") or 0.0) for row in market_snapshots if row.get("confidence") is not None]
    market_catalyst_count = sum(int(row.get("catalyst_count") or 0) for row in market_proposals)
    corpus_audit: Dict[str, Any] = {}
    run_dir_for_audit = Path(run_dir_raw) if run_dir_raw else None
    if run_dir_for_audit is not None and not _is_production_run_dir(run_dir_for_audit):
        corpus_audit = {}
    elif run_dir_for_audit is None:
        try:
            from Apollo.corpus_bootstrap import audit_financial_corpus as _audit_financial_corpus

            payload = _audit_financial_corpus({"top_k_sources": 6})
            if isinstance(payload, dict) and payload.get("ok"):
                corpus_audit = payload
        except Exception:
            corpus_audit = {}
    else:
        latest_corpus = _read_json(_APOLLO_ROOT / "logs" / "corpus_refresh" / "latest_corpus_refresh.json", {})
        latest_stats = dict(latest_corpus.get("stats") or latest_corpus.get("corpus") or {})
        latest_ingest = dict(latest_corpus.get("ingest") or {})
        if latest_stats:
            corpus_audit = latest_stats
        elif latest_ingest:
            corpus_audit = {
                "total_chunks": latest_ingest.get("total_chunks_added"),
                "real_chunk_count": latest_ingest.get("total_chunks_added"),
                "synthetic_chunk_count": 0,
                "seed_local_chunk_count": 0,
                "real_ratio": 1.0 if int(latest_ingest.get("total_chunks_added") or 0) > 0 else 0.0,
                "top_real_sources": [],
            }
        elif os.getenv("APOLLO_STATUS_FULL_CORPUS_AUDIT", "0") == "1":
            try:
                from Apollo.corpus_bootstrap import audit_financial_corpus as _audit_financial_corpus

                payload = _audit_financial_corpus({"top_k_sources": 6})
                if isinstance(payload, dict) and payload.get("ok"):
                    corpus_audit = payload
            except Exception:
                corpus_audit = {}
    return {
        "corpus": {
            "total_chunks": int(corpus_audit.get("total_chunks") or 0),
            "real_chunk_count": int(corpus_audit.get("real_chunk_count") or 0),
            "synthetic_chunk_count": int(corpus_audit.get("synthetic_chunk_count") or 0),
            "seed_local_chunk_count": int(corpus_audit.get("seed_local_chunk_count") or 0),
            "real_ratio": float(corpus_audit.get("real_ratio") or 0.0),
            "top_real_sources": list(corpus_audit.get("top_real_sources") or []),
        },
        "gather": {
            "accepted_sources": int(gather_quality.get("accepted_count") or len(gather.get("sources") or [])),
            "rejected_candidates": int(gather_quality.get("rejected_count") or 0),
            "avg_score": float(gather_quality.get("avg_score") or 0.0),
            "trusted_count": int(gather_quality.get("trusted_count") or 0),
            "trusted_ratio": float(gather_quality.get("trusted_ratio") or 0.0),
            "source_confidence": str(gather_quality.get("source_confidence") or "unknown"),
            "trade_confidence_cap": str(gather_quality.get("trade_confidence_cap") or "unknown"),
            "standard_trade_ready_allowed": bool(gather_quality.get("standard_trade_ready_allowed")),
            "c_only_run": bool(gather_quality.get("c_only_run")),
            "primary_evidence_count": int(gather_quality.get("primary_evidence_count") or 0),
            "market_news_evidence_count": int(gather_quality.get("market_news_evidence_count") or 0),
            "doc_sources": int(gather_quality.get("doc_sources") or 0),
            "pdf_count": int(gather_quality.get("pdf_count") or 0),
            "pdf_sources": int(gather_quality.get("pdf_count") or 0),
            "topic_intent_hits": int(gather_intent_hits),
            "tier_breakdown": dict(gather_quality.get("tier_breakdown") or {}),
            "evidence_lanes": dict(gather_quality.get("evidence_lanes") or {}),
            "candidate_evidence_packs": list(gather_quality.get("candidate_evidence_packs") or [])[:12],
            "reject_reason_counts": dict(gather_quality.get("reject_reason_counts") or {}),
        },
        "focus_universe": {
            "enabled": bool(focus_universe),
            "selected_theme": str(selected_theme.get("label") or selected_theme.get("id") or "").strip(),
            "selected_theme_id": str(selected_theme.get("id") or "").strip(),
            "theme_score": float(selected_theme.get("theme_score") or 0.0),
            "evidence_volume": float(selected_theme.get("evidence_volume") or 0.0),
            "trusted_ratio": float(selected_theme.get("trusted_ratio") or 0.0),
            "selected_ticker_count": len(selected_tickers),
            "selected_tickers": [str(row.get("ticker") or "").strip().upper() for row in selected_tickers if str(row.get("ticker") or "").strip()],
            "query_count": len(list(focus_universe.get("research_queries") or [])),
        },
        "market_study": {
            "enabled": bool(market_study_payload),
            "run_id": str(market_study_payload.get("run_id") or ""),
            "proposal_count": len(market_proposals),
            "has_trade_proposal": any(str(row.get("direction_bias") or "") in {"long_bias", "short_bias"} for row in market_proposals),
            "high_confidence": bool(market_study_payload.get("high_confidence")),
            "blocked_session": bool(market_study_payload.get("blocked_session")),
            "avg_snapshot_confidence": round(sum(market_conf) / max(1, len(market_conf)), 2) if market_conf else 0.0,
            "catalyst_count": int(market_catalyst_count),
            "best_ticker": str((market_study_payload.get("best_proposal") or {}).get("ticker") or ""),
        },
        "ocr": {
            "documents": len(docs),
            "ingested": int(ocr.get("ingested") or 0),
            "evaluated_documents": int(ocr_quality.get("evaluated_documents") or 0),
            "excluded_quality_scan_docs": int(ocr_quality.get("excluded_quality_scan_docs") or 0),
            "excluded_seed_fallback_docs": int(ocr_quality.get("excluded_seed_fallback_docs") or 0),
            "avg_chars": int(round(sum(chars) / max(1, len(chars)))),
            "min_chars": int(min(chars)),
            "avg_semantic_sections": int(round(sum(semantic_sections) / max(1, len(semantic_sections)))),
            "table_docs": int(table_docs),
            "engines": sorted({str(item.get("extract_engine") or "") for item in docs if str(item.get("extract_engine") or "").strip()}),
            "fallback_used": sum(1 for item in docs if bool(item.get("fallback_used"))),
            "current_policy": str(ocr_quality.get("current_policy") or (docs[-1].get("route_mode") if docs else "") or DEFAULT_OCR_ROUTE_MODE),
            "engine_chain_last_doc": list(ocr_quality.get("engine_chain_last_doc") or (docs[-1].get("engine_chain") if docs else []) or []),
            "quality_breakdown": dict(ocr_quality.get("quality_breakdown") or {}),
            "gate_fail_reasons": list(ocr_quality.get("gate_fail_reasons") or []),
            "backend_ready": bool(ocr_quality.get("backend_ready")),
            "backend_mode": str(ocr_quality.get("backend_mode") or ""),
            "backend_fail_reason": str(ocr_quality.get("backend_fail_reason") or ""),
            "visual_ocr_ready": bool(ocr_quality.get("visual_ocr_ready")),
            "visual_ocr_mode": str(ocr_quality.get("visual_ocr_mode") or ""),
            "native_pdf_supported": bool(ocr_quality.get("native_pdf_supported")),
            "non_scrape_ratio": float(ocr_quality.get("non_scrape_ratio") or 0.0),
            "benchmark_proxy_scores": dict(ocr_quality.get("benchmark_proxy_scores") or {}),
            "engine_mix": list(ocr_quality.get("engine_mix") or []),
            "extraction_mode_mix": list(ocr_quality.get("extraction_mode_mix") or []),
            "document_class_mix": list(ocr_quality.get("document_class_mix") or []),
            "scrape_only_detected": bool(ocr_quality.get("scrape_only_detected")),
            "escalation_count": int(ocr_quality.get("escalation_count") or 0),
            "gate_skipped": bool(ocr_quality.get("gate_skipped")),
            "gate_skip_reason": str(ocr_quality.get("gate_skip_reason") or ""),
            "lane_scorecard": dict(ocr_quality.get("lane_scorecard") or {}),
            "slo_summary": dict(ocr_quality.get("slo_summary") or {}),
            "smoke_gate": dict(ocr_quality.get("smoke_gate") or {}),
        },
        "hipporag": {
            "processed": int(hippo.get("processed") or 0),
            "processed_finance": int(hippo.get("processed_finance") or hippo.get("processed") or 0),
            "skipped_non_finance": int(hippo.get("skipped_non_finance") or 0),
            "requeue_count": int(hippo.get("requeued_low_quality") or 0),
            "pending_quarantine": int(hippo.get("pending_quarantine") or 0),
            "new_chunks": int(hippo.get("new_chunks") or 0),
            "total_chunks": int(hippo.get("total_chunks") or 0),
            "remaining_after": int(hippo.get("remaining_after") or 0),
            "triples": int(hippo.get("triples") or 0),
            "validated_non_triple_finance": int(hippo.get("validated_non_triple_finance") or 0),
            "completed": bool(hippo.get("completed")),
            "progress_breakdown": {
                "processed_finance": int(hippo.get("processed_finance") or hippo.get("processed") or 0),
                "skipped_non_finance": int(hippo.get("skipped_non_finance") or 0),
                "requeued_low_quality": int(hippo.get("requeued_low_quality") or 0),
                "pending_quarantine": int(hippo.get("pending_quarantine") or 0),
            },
            "stall_streak": int(((status.get("recovery") or {}).get("stall_streak")) or 0),
        },
        "self_check": {
            "ok": bool(self_check.get("ok")),
            "check_count": len(checks),
            "ok_count": sum(1 for item in checks.values() if isinstance(item, dict) and item.get("ok")),
            "mode": str(self_check.get("mode") or ""),
            "decision_gate": dict(self_check.get("decision_gate") or {}),
        },
    }


def _next_run_at(status: Dict[str, Any], config: Dict[str, Any]) -> str:
    if str(status.get("run_mode") or "") != "background":
        return ""
    waiting = status.get("waiting") if isinstance(status.get("waiting"), dict) else {}
    if waiting.get("reason") in {"paused", "stopped"}:
        return ""
    interval_min = int(config.get("background_interval_min") or DEFAULT_BACKGROUND_INTERVAL_MIN)
    base_raw = str(status.get("last_step_at") or status.get("started_at") or "")
    try:
        base = datetime.fromisoformat(base_raw.replace("Z", "+00:00"))
    except Exception:
        base = _now_utc()
    return _utc_iso(base.astimezone(timezone.utc) + timedelta(minutes=interval_min))


def _write_truthful_pipeline_report(run_dir: Path, status: Dict[str, Any]) -> None:
    quality = dict(status.get("quality") or {})
    stage_scores = dict(quality.get("stage_scores") or {})
    details = dict(status.get("stage_details") or {})
    gather = _stage_result_view(dict((status.get("stages") or {}).get("gather") or {}))
    ocr = _stage_result_view(dict((status.get("stages") or {}).get("ocr") or {}))
    hippo = _stage_result_view(dict((status.get("stages") or {}).get("hipporag") or {}))
    lines = [
        "# Apollo Pipeline Report",
        "",
        "## Run",
        f"- run_id: `{status.get('run_id')}`",
        f"- started_at: `{status.get('started_at')}`",
        f"- finished_at: `{status.get('finished_at')}`",
        f"- mode: `{status.get('run_mode')}`",
        f"- topic: `{gather.get('topic') or ''}`",
        "",
        "## Quality",
        f"- gate_mode: `{quality.get('gate_mode')}`",
        f"- score_basis: `{quality.get('score_basis')}`",
        f"- gate_pass: `{quality.get('gate_pass')}`",
        f"- overall_score: `{quality.get('overall_score')}`",
        f"- rolling7_median_score: `{quality.get('rolling7_median_score')}`",
        f"- confidence_band: `{((quality.get('confidence_band') or {}).get('label'))}`",
        f"- stage_scores: gather={stage_scores.get('gather')}, ocr={stage_scores.get('ocr')}, hipporag={stage_scores.get('hipporag')}, self_check={stage_scores.get('self_check')}",
        f"- gate_fail_reasons: `{', '.join(quality.get('gate_fail_reasons') or []) or 'none'}`",
        "",
        "## Stage Details",
        f"- corpus: total={details.get('corpus', {}).get('total_chunks')}, real={details.get('corpus', {}).get('real_chunk_count')}, synthetic={details.get('corpus', {}).get('synthetic_chunk_count')}, real_ratio={details.get('corpus', {}).get('real_ratio')}",
        f"- gather: accepted={details.get('gather', {}).get('accepted_sources')}, rejected={details.get('gather', {}).get('rejected_candidates')}, avg_score={details.get('gather', {}).get('avg_score')}",
        f"- focus_universe: theme={details.get('focus_universe', {}).get('selected_theme')}, theme_score={details.get('focus_universe', {}).get('theme_score')}, tickers={details.get('focus_universe', {}).get('selected_tickers')}, evidence_volume={details.get('focus_universe', {}).get('evidence_volume')}",
        f"- ocr: docs={details.get('ocr', {}).get('documents')}, ingested={details.get('ocr', {}).get('ingested')}, avg_chars={details.get('ocr', {}).get('avg_chars')}, min_chars={details.get('ocr', {}).get('min_chars')}",
        f"- ocr modes: visual_ready={details.get('ocr', {}).get('visual_ocr_ready')}, visual_mode={details.get('ocr', {}).get('visual_ocr_mode')}, native_pdf_supported={details.get('ocr', {}).get('native_pdf_supported')}, extraction_modes={details.get('ocr', {}).get('extraction_mode_mix')}, doc_classes={details.get('ocr', {}).get('document_class_mix')}, scrape_only={details.get('ocr', {}).get('scrape_only_detected')}",
        f"- hipporag: processed={details.get('hipporag', {}).get('processed')}, skipped_non_finance={details.get('hipporag', {}).get('skipped_non_finance')}, total={details.get('hipporag', {}).get('total_chunks')}, triples={details.get('hipporag', {}).get('triples')}, completed={details.get('hipporag', {}).get('completed')}",
        f"- self_check: ok={details.get('self_check', {}).get('ok')}, checks={details.get('self_check', {}).get('check_count')}, decision_gate={details.get('self_check', {}).get('decision_gate')}",
        "",
        "## Sources",
    ]
    for item in list(gather.get("sources") or [])[:20]:
        if not isinstance(item, dict):
            continue
        lines.append(f"- {str(item.get('title') or '(untitled)')} ({str(item.get('url') or '')})")
    if not gather.get("sources"):
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## OCR Documents",
        ]
    )
    for doc in list(ocr.get("documents") or [])[:20]:
        if not isinstance(doc, dict):
            continue
        lines.append(
            f"- {str(doc.get('title') or '(untitled)')}: chars={int(doc.get('text_chars') or 0)}, engine={str(doc.get('extract_engine') or '')}, quality_flag={str(doc.get('quality_flag') or '')}"
        )
    if not ocr.get("documents"):
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## HippoRAG Stats",
            f"- nodes: `{int((hippo.get('stats') or {}).get('nodes') or 0)}`",
            f"- edges: `{int((hippo.get('stats') or {}).get('edges') or 0)}`",
            f"- indexed_docs: `{int((hippo.get('stats') or {}).get('indexed_docs') or 0)}`",
            "",
        ]
    )
    gateway_probe = dict(status.get("gateway_probe") or {})
    if gateway_probe:
        lines.extend(
            [
                "## Aegis Gateway Probe",
                f"- ok: `{bool(gateway_probe.get('ok'))}`",
                f"- score: `{int(gateway_probe.get('score') or 0)}`",
                f"- docs_probed: `{int(gateway_probe.get('docs_probed') or 0)}`",
                f"- success_count: `{int(gateway_probe.get('success_count') or 0)}`",
                f"- non_generic_count: `{int(gateway_probe.get('non_generic_count') or 0)}`",
                f"- unique_fingerprint_count: `{int(gateway_probe.get('unique_fingerprint_count') or 0)}`",
                f"- artifact_path: `{str(gateway_probe.get('artifact_path') or '')}`",
                "",
            ]
        )
    _write_text(run_dir / "pipeline_report.md", "\n".join(lines).strip() + "\n")


def _gateway_probe_headers() -> Dict[str, str]:
    token = str(os.getenv("APOLLO_LOCAL_TOKEN") or os.getenv("SKY_LOCAL_TOKEN") or "").strip()
    if not token or os.getenv("SKY_ALLOW_ANON", "1") == "1":
        return {}
    return {"X-Apollo-Token": token}


def _normalized_gateway_probe_url(raw_url: str) -> str:
    base = str(raw_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/ocr/extract"):
        return base
    return f"{base}/ocr/extract"


def _gateway_probe_fingerprint(text: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    if not raw:
        return ""
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _gateway_probe_request(
    gateway_url: str,
    headers: Dict[str, str],
    candidate: Dict[str, Any],
    *,
    prewarm: bool,
) -> requests.Response:
    base_timeout = max(60, min(int(os.getenv("APOLLO_GATEWAY_PROBE_TIMEOUT_SEC", "240")), 900))
    retry_timeout = max(base_timeout, min(int(os.getenv("APOLLO_GATEWAY_PROBE_RETRY_TIMEOUT_SEC", str(base_timeout * 2))), 900))
    payload = {
        "path": candidate["path"],
        "goal": "Perform faithful OCR for this finance document. Return only extracted markdown and preserve document-specific headings, tables, and numeric values exactly.",
        "route_mode": "quality_first",
        "prewarm": "1" if prewarm else "0",
        "max_pages": str(max(1, min(int(candidate.get("max_pages") or 4), 6))),
        "max_chars": str(max(2000, min(int(candidate.get("max_chars") or 16000), 24000))),
        "timeout_sec": str(retry_timeout),
    }
    try:
        return requests.post(gateway_url, data=payload, headers=headers, timeout=base_timeout)
    except requests.exceptions.ReadTimeout:
        retry_payload = dict(payload)
        retry_payload["prewarm"] = "0"
        return requests.post(gateway_url, data=retry_payload, headers=headers, timeout=retry_timeout)


def _run_aegis_gateway_probe(run_dir: Path, status: Dict[str, Any]) -> Dict[str, Any]:
    from common.ocr_dual_tool import _looks_like_generic_finance_template

    ocr_stage = _stage_result_view(dict((status.get("stages") or {}).get("ocr") or {}))
    documents = [item for item in list(ocr_stage.get("documents") or []) if isinstance(item, dict)]
    candidates: list[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    for doc in documents:
        path_raw = str(doc.get("local_path") or doc.get("url") or "").strip()
        if not path_raw:
            continue
        try:
            candidate = Path(path_raw).resolve()
        except Exception:
            continue
        if not candidate.exists() or candidate.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}:
            continue
        path_key = str(candidate).lower()
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        candidates.append(
            {
                "title": str(doc.get("title") or candidate.name),
                "path": str(candidate),
                "max_pages": int(doc.get("extract_pages") or 4),
                "max_chars": int(doc.get("text_chars") or 16000),
            }
        )
    max_docs = max(1, min(int(os.getenv("APOLLO_GATEWAY_PROBE_MAX_DOCS", "4")), 8))
    candidates = candidates[:max_docs]
    artifact_path = run_dir / "05_aegis_gateway_probe.json"
    result: Dict[str, Any] = {
        "ok": False,
        "gateway_url": _normalized_gateway_probe_url(str(os.getenv("APOLLO_GB10_OCR_GATEWAY_URL") or "http://127.0.0.1:5221/ocr/extract")),
        "docs_probed": len(candidates),
        "success_count": 0,
        "non_generic_count": 0,
        "unique_fingerprint_count": 0,
        "duplicate_fingerprint_count": 0,
        "score": 0,
        "artifact_path": str(artifact_path),
        "results": [],
        "error": "",
    }
    if not candidates:
        result["error"] = "no_probe_candidates"
        _write_json(artifact_path, result)
        return result
    gateway_url = str(result["gateway_url"]).strip()
    headers = _gateway_probe_headers()
    fingerprints: list[str] = []
    quality_scores: list[float] = []
    for index, candidate in enumerate(candidates):
        row: Dict[str, Any] = {"title": candidate["title"], "path": candidate["path"], "ok": False}
        try:
            response = _gateway_probe_request(
                gateway_url,
                headers,
                candidate,
                prewarm=index == 0,
            )
            row["status_code"] = int(response.status_code)
            payload = response.json()
        except Exception as exc:
            row["error"] = f"probe_request_failed:{type(exc).__name__}:{exc}"
            result["results"].append(row)
            continue
        markdown = str(payload.get("markdown") or payload.get("text") or "").strip()
        quality = dict(payload.get("quality") or {})
        generic = _looks_like_generic_finance_template(markdown)
        fingerprint = _gateway_probe_fingerprint(markdown)
        row.update(
            {
                "ok": bool(payload.get("ok")) and bool(markdown) and not generic,
                "engine": str(payload.get("engine") or ""),
                "model": str(payload.get("model") or ""),
                "chars": int(quality.get("chars") or len(markdown)),
                "quality_score": float(quality.get("score") or 0.0),
                "structure_score": float(quality.get("structure_score") or 0.0),
                "numeric_fidelity_score": float(quality.get("numeric_fidelity_score") or 0.0),
                "generic_template_detected": bool(generic),
                "fingerprint": fingerprint,
                "preview": markdown[:1200],
                "error": str(payload.get("error") or ("generic_template_detected" if generic else "")),
            }
        )
        if row["ok"]:
            result["success_count"] = int(result["success_count"]) + 1
        if not generic and markdown:
            result["non_generic_count"] = int(result["non_generic_count"]) + 1
        if fingerprint:
            fingerprints.append(fingerprint)
        quality_scores.append(float(row["quality_score"]))
        result["results"].append(row)
    unique_fingerprints = len(set(fingerprints))
    duplicate_fingerprints = max(0, len(fingerprints) - unique_fingerprints)
    result["unique_fingerprint_count"] = unique_fingerprints
    result["duplicate_fingerprint_count"] = duplicate_fingerprints
    docs_total = max(1, len(candidates))
    success_ratio = float(result["success_count"]) / docs_total
    non_generic_ratio = float(result["non_generic_count"]) / docs_total
    distinct_ratio = (unique_fingerprints / docs_total) if docs_total else 0.0
    avg_quality = (sum(quality_scores) / len(quality_scores)) if quality_scores else 0.0
    result["score"] = int(round(min(100.0, (success_ratio * 35.0) + (non_generic_ratio * 35.0) + (distinct_ratio * 15.0) + (avg_quality * 15.0))))
    result["ok"] = bool(
        len(candidates) > 0
        and int(result["success_count"]) == len(candidates)
        and int(result["non_generic_count"]) == len(candidates)
        and duplicate_fingerprints == 0
    )
    _write_json(artifact_path, result)
    return result


def _enrich_status_payload(run_dir: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    status = dict(payload or {})
    status.setdefault("run_dir", str(Path(run_dir).resolve()))
    status = _reconcile_stage_files(run_dir, status)
    config = _read_json(run_dir / "config.json", {})
    if not config:
        config = _pipeline_config({"run_mode": status.get("run_mode") or "background"})
    status["quality"] = _compute_quality(status, config)
    status["stage_details"] = _build_stage_details(status)
    truth = _stage_truth_for_status(status)
    status["stage_truth"] = truth["stage_truth"]
    status["pipeline_completed"] = truth["pipeline_completed"]
    status["quality_gate_pass"] = truth["quality_gate_pass"]
    status["truth_label"] = truth["truth_label"]
    gather_details = dict((status.get("stage_details") or {}).get("gather") or {})
    hippo_details = dict((status.get("stage_details") or {}).get("hipporag") or {})
    ocr_details = dict((status.get("stage_details") or {}).get("ocr") or {})
    focus_details = dict((status.get("stage_details") or {}).get("focus_universe") or {})
    self_check_details = dict((status.get("stage_details") or {}).get("self_check") or {})
    status["gather"] = {
        "pdf_sources": int(gather_details.get("pdf_sources") or gather_details.get("pdf_count") or 0),
        "trusted_ratio": float(gather_details.get("trusted_ratio") or 0.0),
        "tier_breakdown": dict(gather_details.get("tier_breakdown") or {}),
        "reject_reason_counts": dict(gather_details.get("reject_reason_counts") or {}),
    }
    corpus_details = dict((status.get("stage_details") or {}).get("corpus") or {})
    status["corpus"] = {
        "total_chunks": int(corpus_details.get("total_chunks") or 0),
        "real_chunk_count": int(corpus_details.get("real_chunk_count") or 0),
        "synthetic_chunk_count": int(corpus_details.get("synthetic_chunk_count") or 0),
        "seed_local_chunk_count": int(corpus_details.get("seed_local_chunk_count") or 0),
        "real_ratio": float(corpus_details.get("real_ratio") or 0.0),
        "top_real_sources": list(corpus_details.get("top_real_sources") or []),
    }
    ocr_quality_breakdown = dict(ocr_details.get("quality_breakdown") or {})
    status["ocr"] = {
        "current_policy": str(ocr_details.get("current_policy") or DEFAULT_OCR_ROUTE_MODE),
        "backend_ready": bool(ocr_details.get("backend_ready")),
        "backend_mode": str(ocr_details.get("backend_mode") or ""),
        "backend_fail_reason": str(ocr_details.get("backend_fail_reason") or ""),
        "visual_ocr_ready": bool(ocr_details.get("visual_ocr_ready")),
        "visual_ocr_mode": str(ocr_details.get("visual_ocr_mode") or ""),
        "native_pdf_supported": bool(ocr_details.get("native_pdf_supported")),
        "non_scrape_ratio": float(ocr_details.get("non_scrape_ratio") or 0.0),
        "engine_chain_last_doc": list(ocr_details.get("engine_chain_last_doc") or []),
        "quality_breakdown": ocr_quality_breakdown,
        "failure_class": str(ocr_details.get("failure_class") or ocr_quality_breakdown.get("failure_class") or ""),
        "gate_fail_reasons": list(ocr_details.get("gate_fail_reasons") or []),
        "benchmark_proxy_scores": dict(ocr_details.get("benchmark_proxy_scores") or {}),
        "engine_mix": list(ocr_details.get("engine_mix") or []),
        "extraction_mode_mix": list(ocr_details.get("extraction_mode_mix") or []),
        "document_class_mix": list(ocr_details.get("document_class_mix") or []),
        "scrape_only_detected": bool(ocr_details.get("scrape_only_detected")),
        "escalation_count": int(ocr_details.get("escalation_count") or 0),
        "evaluated_documents": int(ocr_details.get("evaluated_documents") or 0),
        "excluded_quality_scan_docs": int(ocr_details.get("excluded_quality_scan_docs") or 0),
        "excluded_seed_fallback_docs": int(ocr_details.get("excluded_seed_fallback_docs") or 0),
        "gate_skipped": bool(ocr_details.get("gate_skipped")),
        "gate_skip_reason": str(ocr_details.get("gate_skip_reason") or ""),
    }
    status["hipporag"] = {
        "progress_breakdown": dict(hippo_details.get("progress_breakdown") or {}),
        "requeue_count": int(hippo_details.get("requeue_count") or 0),
        "stall_streak": int(hippo_details.get("stall_streak") or 0),
    }
    status["focus_universe"] = {
        "enabled": bool(focus_details.get("enabled")),
        "selected_theme": str(focus_details.get("selected_theme") or ""),
        "selected_theme_id": str(focus_details.get("selected_theme_id") or ""),
        "theme_score": float(focus_details.get("theme_score") or 0.0),
        "evidence_volume": float(focus_details.get("evidence_volume") or 0.0),
        "selected_ticker_count": int(focus_details.get("selected_ticker_count") or 0),
        "selected_tickers": list(focus_details.get("selected_tickers") or []),
        "query_count": int(focus_details.get("query_count") or 0),
        "decision_ready": bool((self_check_details.get("decision_gate") or {}).get("decision_ready")),
        "decision_mode": str((self_check_details.get("decision_gate") or {}).get("decision_mode") or ""),
        "confidence_label": str((self_check_details.get("decision_gate") or {}).get("confidence_label") or ""),
        "volume_score": float((self_check_details.get("decision_gate") or {}).get("volume_score") or 0.0),
        "decision_gate": dict(self_check_details.get("decision_gate") or {}),
    }
    status.setdefault("recovery", {})
    status["recommended_next_action"] = _recommended_next_action(status)
    status["next_run_at"] = _next_run_at(status, config)
    if _all_stages_complete(status):
        status["ok"] = bool((status.get("quality") or {}).get("gate_pass"))
        if status.get("current_stage") not in {"done", ""}:
            status["current_stage"] = "done"
    pointer = _read_current_run_pointer()
    pointer_run_dir = str(pointer.get("run_dir") or "").strip()
    current_run_dir = str(Path(run_dir).resolve())
    status["run_meta"] = {
        "current_pointer_run_id": str(pointer.get("run_id") or "").strip(),
        "current_pointer_run_dir": pointer_run_dir,
        "current_pointer_valid": bool(pointer_run_dir and pointer_run_dir == current_run_dir),
    }
    return status


def _save_status(run_dir: Path, payload: Dict[str, Any]) -> None:
    enriched = _enrich_status_payload(run_dir, payload)
    run_dir_resolved = Path(run_dir).resolve()
    enriched["run_dir"] = str(run_dir_resolved)
    enriched["updated_at"] = _utc_iso()
    enriched["status_seq"] = _resolve_status_seq(run_dir_resolved, enriched)
    pointer_set = False
    if _is_background_run_status(enriched) and _is_production_run_dir(run_dir_resolved):
        _write_current_run_pointer(enriched)
        pointer_set = True
    if pointer_set:
        enriched["run_meta"] = {
            "current_pointer_run_id": str(enriched.get("run_id") or "").strip(),
            "current_pointer_run_dir": str(run_dir_resolved),
            "current_pointer_valid": True,
        }
    _write_json(_status_path(run_dir_resolved), enriched)
    latest_existing = _read_json(LATEST_STATUS_PATH, {})
    if _should_replace_latest_status(enriched, latest_existing):
        _write_json(LATEST_STATUS_PATH, enriched)


def _pipeline_config(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw = dict(payload or {})
    def _raw_value(name: str, default: Any) -> Any:
        value = raw.get(name) if name in raw else None
        return default if value is None else value

    max_articles = max(1, min(int(raw.get("max_articles") or DEFAULT_MAX_ARTICLES), 6))
    gather_min_accepted_sources = max(1, min(int(_raw_value("gather_min_accepted_sources", DEFAULT_GATHER_MIN_ACCEPTED_SOURCES)), 6))
    gather_min_doc_sources = max(0, min(int(_raw_value("gather_min_doc_sources", DEFAULT_GATHER_MIN_DOC_SOURCES)), 6))
    gather_min_pdf_sources = max(0, min(int(_raw_value("gather_min_pdf_sources", DEFAULT_GATHER_MIN_PDF_SOURCES)), 6))
    gather_min_doc_sources = min(gather_min_doc_sources, max_articles)
    gather_min_pdf_sources = min(gather_min_pdf_sources, max_articles)
    if gather_min_pdf_sources > gather_min_doc_sources:
        gather_min_doc_sources = gather_min_pdf_sources
    gather_min_accepted_sources = min(
        max_articles,
        max(gather_min_accepted_sources, gather_min_doc_sources, gather_min_pdf_sources),
    )
    hipporag_limit = raw.get("hipporag_limit")
    try:
        hipporag_limit_int = int(hipporag_limit) if hipporag_limit is not None else None
    except Exception:
        hipporag_limit_int = None
    run_mode = str(raw.get("run_mode") or "nightly").strip().lower() or "nightly"
    batch_id = str(raw.get("batch_id") or "").strip()
    if not batch_id:
        if run_mode == "background":
            batch_id = DEFAULT_BACKGROUND_RUN_ID
        else:
            batch_id = f"apollo_nightly_{_now_utc().strftime('%Y%m%d_%H%M%S')}"
    focus_universe_enabled = str(_raw_value("focus_universe_enabled", DEFAULT_FOCUS_UNIVERSE_ENABLED)).strip().lower() in {"1", "true", "yes", "on"}
    focus_universe_min_names = max(5, min(int(_raw_value("focus_universe_min_names", DEFAULT_FOCUS_UNIVERSE_MIN_NAMES)), 12))
    focus_universe_max_names = max(focus_universe_min_names, min(int(_raw_value("focus_universe_max_names", DEFAULT_FOCUS_UNIVERSE_MAX_NAMES)), 12))
    focus_universe_gb10_only = str(_raw_value("focus_universe_gb10_only", DEFAULT_FOCUS_UNIVERSE_GB10_ONLY)).strip().lower() in {"1", "true", "yes", "on"}
    ocr_primary_engine_pdf = str(_raw_value("ocr_primary_engine_pdf", DEFAULT_OCR_PRIMARY_ENGINE_PDF)).strip() or DEFAULT_OCR_PRIMARY_ENGINE_PDF
    ocr_primary_engine_image = str(_raw_value("ocr_primary_engine_image", DEFAULT_OCR_PRIMARY_ENGINE_IMAGE)).strip() or DEFAULT_OCR_PRIMARY_ENGINE_IMAGE
    ocr_secondary_engine = str(_raw_value("ocr_secondary_engine", DEFAULT_OCR_SECONDARY_ENGINE)).strip() or DEFAULT_OCR_SECONDARY_ENGINE
    gather_allow_seed_fallback = str(_raw_value("gather_allow_seed_fallback", DEFAULT_GATHER_ALLOW_SEED_FALLBACK)).strip().lower() in {"1", "true", "yes", "on"}
    gather_web_rate_limit_retries = max(0, min(int(_raw_value("gather_web_rate_limit_retries", DEFAULT_GATHER_WEB_RATE_LIMIT_RETRIES)), 3))
    gather_web_rate_limit_backoff_sec = max(
        0.0,
        min(float(_raw_value("gather_web_rate_limit_backoff_sec", DEFAULT_GATHER_WEB_RATE_LIMIT_BACKOFF_SEC)), 8.0),
    )
    gather_web_provider_fallbacks_raw = _raw_value("gather_web_provider_fallbacks", DEFAULT_GATHER_WEB_PROVIDER_FALLBACKS)
    if isinstance(gather_web_provider_fallbacks_raw, str):
        gather_web_provider_fallbacks = [item.strip().lower() for item in gather_web_provider_fallbacks_raw.split(",") if item.strip()]
    elif isinstance(gather_web_provider_fallbacks_raw, list):
        gather_web_provider_fallbacks = [str(item).strip().lower() for item in gather_web_provider_fallbacks_raw if str(item).strip()]
    else:
        gather_web_provider_fallbacks = list(DEFAULT_GATHER_WEB_PROVIDER_FALLBACKS)
    if not gather_web_provider_fallbacks:
        gather_web_provider_fallbacks = list(DEFAULT_GATHER_WEB_PROVIDER_FALLBACKS)
    equity_news_tickers_raw = raw.get("equity_news_tickers")
    if isinstance(equity_news_tickers_raw, str):
        equity_news_tickers = [item.strip().upper() for item in equity_news_tickers_raw.split(",") if item.strip()]
    elif isinstance(equity_news_tickers_raw, list):
        equity_news_tickers = [str(item).strip().upper() for item in equity_news_tickers_raw if str(item).strip()]
    else:
        equity_news_tickers = []
    if focus_universe_enabled and focus_universe_gb10_only:
        if not ocr_primary_engine_pdf.startswith("gb10_"):
            ocr_primary_engine_pdf = DEFAULT_OCR_PRIMARY_ENGINE_PDF
        if not ocr_primary_engine_image.startswith("gb10_"):
            ocr_primary_engine_image = DEFAULT_OCR_PRIMARY_ENGINE_IMAGE
        if not ocr_secondary_engine.startswith("gb10_"):
            ocr_secondary_engine = DEFAULT_OCR_SECONDARY_ENGINE
    config = {
        "run_mode": run_mode,
        "topic": str(raw.get("topic") or DEFAULT_TOPIC).strip() or DEFAULT_TOPIC,
        "batch_id": batch_id,
        "max_articles": max_articles,
        "sources": list(raw.get("sources") or []) if isinstance(raw.get("sources"), list) else [],
        "queries": list(raw.get("queries") or []) if isinstance(raw.get("queries"), list) else [],
        "equity_news_tickers": equity_news_tickers,
        "ocr_max_pages": max(1, min(int(raw.get("ocr_max_pages") or DEFAULT_OCR_MAX_PAGES), 12)),
        "ocr_max_chars": max(2000, min(int(raw.get("ocr_max_chars") or DEFAULT_OCR_MAX_CHARS), 50000)),
        "allow_web": bool(raw.get("allow_web", True)),
        "allow_inbox": bool(raw.get("allow_inbox", True)),
        "gather_timeout_sec": max(300, int(raw.get("gather_timeout_sec") or DEFAULT_GATHER_TIMEOUT_SEC)),
        "ocr_timeout_sec": max(300, int(raw.get("ocr_timeout_sec") or DEFAULT_OCR_TIMEOUT_SEC)),
        "hipporag_timeout_sec": max(300, int(raw.get("hipporag_timeout_sec") or DEFAULT_HIPPORAG_TIMEOUT_SEC)),
        "self_check_timeout_sec": max(60, int(raw.get("self_check_timeout_sec") or DEFAULT_SELF_CHECK_TIMEOUT_SEC)),
        "overall_timeout_sec": max(600, int(raw.get("overall_timeout_sec") or DEFAULT_OVERALL_TIMEOUT_SEC)),
        "preflight_max_wait_sec": max(0, min(int(_raw_value("preflight_max_wait_sec", DEFAULT_PREFLIGHT_MAX_WAIT_SEC)), 21600)),
        "preflight_retry_interval_sec": max(5, min(int(_raw_value("preflight_retry_interval_sec", DEFAULT_PREFLIGHT_RETRY_INTERVAL_SEC)), 600)),
        "hipporag_use_llm": bool(raw.get("hipporag_use_llm", DEFAULT_HIPPORAG_USE_LLM)),
        "hipporag_limit": hipporag_limit_int,
        "hipporag_batch_size": max(5, min(int(raw.get("hipporag_batch_size") or DEFAULT_HIPPORAG_BATCH_SIZE), 100)),
        "hipporag_llm_model": str(raw.get("hipporag_llm_model") or DEFAULT_HIPPORAG_LLM_MODEL).strip() or DEFAULT_HIPPORAG_LLM_MODEL,
        "hipporag_llm_timeout_sec": max(15, int(raw.get("hipporag_llm_timeout_sec") or DEFAULT_HIPPORAG_LLM_TIMEOUT_SEC)),
        "hipporag_llm_num_predict": max(64, int(raw.get("hipporag_llm_num_predict") or DEFAULT_HIPPORAG_LLM_NUM_PREDICT)),
        "homework_llm_timeout_sec": max(60, int(raw.get("homework_llm_timeout_sec") or DEFAULT_HOMEWORK_LLM_TIMEOUT_SEC)),
        "allow_cpu_fallback": bool(raw.get("allow_cpu_fallback", DEFAULT_ALLOW_CPU_FALLBACK)),
        "background_interval_min": max(1, min(int(_raw_value("background_interval_min", DEFAULT_BACKGROUND_INTERVAL_MIN)), 60)),
        "background_max_active_models": max(0, min(int(_raw_value("background_max_active_models", DEFAULT_BACKGROUND_MAX_ACTIVE_MODELS)), 8)),
        "background_hipporag_step_chunks": max(1, min(int(_raw_value("background_hipporag_step_chunks", DEFAULT_BACKGROUND_HIPPORAG_STEP_CHUNKS)), 1000)),
        "background_ocr_docs_per_tick": max(1, min(int(_raw_value("background_ocr_docs_per_tick", DEFAULT_BACKGROUND_OCR_DOCS_PER_TICK)), 3)),
        "background_min_idle_sec": max(0, min(int(_raw_value("background_min_idle_sec", DEFAULT_BACKGROUND_MIN_IDLE_SEC)), 86400)),
        "background_gather_timeout_sec": max(30, min(int(_raw_value("background_gather_timeout_sec", DEFAULT_BACKGROUND_GATHER_TIMEOUT_SEC)), 1800)),
        "background_ocr_step_timeout_sec": max(30, min(int(_raw_value("background_ocr_step_timeout_sec", DEFAULT_BACKGROUND_OCR_STEP_TIMEOUT_SEC)), 3600)),
        "background_hipporag_step_timeout_sec": max(60, min(int(_raw_value("background_hipporag_step_timeout_sec", DEFAULT_BACKGROUND_HIPPORAG_STEP_TIMEOUT_SEC)), 7200)),
        "background_self_check_timeout_sec": max(30, min(int(_raw_value("background_self_check_timeout_sec", DEFAULT_BACKGROUND_SELF_CHECK_TIMEOUT_SEC)), 1200)),
        "memory_guard_enabled": str(_raw_value("memory_guard_enabled", DEFAULT_MEMORY_GUARD_ENABLED)).strip().lower() in {"1", "true", "yes", "on"},
        "memory_min_available_mb": max(512, min(int(_raw_value("memory_min_available_mb", DEFAULT_MEMORY_MIN_AVAILABLE_MB)), 262144)),
        "memory_max_used_percent": max(10.0, min(float(_raw_value("memory_max_used_percent", DEFAULT_MEMORY_MAX_USED_PERCENT)), 100.0)),
        "memory_max_vmmem_mb": max(0, min(int(_raw_value("memory_max_vmmem_mb", DEFAULT_MEMORY_MAX_VMMEM_MB)), 262144)),
        "memory_max_ollama_runners": max(0, min(int(_raw_value("memory_max_ollama_runners", DEFAULT_MEMORY_MAX_OLLAMA_RUNNERS)), 64)),
        "memory_wait_sec": max(0, min(int(_raw_value("memory_wait_sec", DEFAULT_MEMORY_WAIT_SEC)), 21600)),
        "memory_retry_sec": max(5, min(int(_raw_value("memory_retry_sec", DEFAULT_MEMORY_RETRY_SEC)), 600)),
        "gather_min_accepted_sources": gather_min_accepted_sources,
        "gather_min_avg_score": max(1, min(int(_raw_value("gather_min_avg_score", DEFAULT_GATHER_MIN_AVG_SCORE)), 100)),
        "gather_min_trusted_ratio": max(0.0, min(float(_raw_value("gather_min_trusted_ratio", DEFAULT_GATHER_MIN_TRUSTED_RATIO)), 1.0)),
        "gather_min_doc_sources": gather_min_doc_sources,
        "gather_min_pdf_sources": gather_min_pdf_sources,
        "gather_remediation_max_passes": max(0, min(int(_raw_value("gather_remediation_max_passes", DEFAULT_GATHER_REMEDIATION_MAX_PASSES)), 4)),
        "gather_remediation2_min_doc_sources": max(0, min(int(_raw_value("gather_remediation2_min_doc_sources", DEFAULT_GATHER_REMEDIATION2_MIN_DOC_SOURCES)), 6)),
        "gather_allow_seed_fallback": gather_allow_seed_fallback,
        "gather_web_rate_limit_retries": gather_web_rate_limit_retries,
        "gather_web_rate_limit_backoff_sec": gather_web_rate_limit_backoff_sec,
        "gather_web_provider_fallbacks": gather_web_provider_fallbacks,
        "ocr_doc_min_chars": max(100, min(int(_raw_value("ocr_doc_min_chars", DEFAULT_OCR_DOC_MIN_CHARS)), 10000)),
        "ocr_avg_min_chars": max(100, min(int(_raw_value("ocr_avg_min_chars", DEFAULT_OCR_AVG_MIN_CHARS)), 15000)),
        "ocr_min_confidence": max(0.0, min(float(_raw_value("ocr_min_confidence", DEFAULT_OCR_MIN_CONFIDENCE)), 1.0)),
        "ocr_min_semantic_hits": max(0, min(int(_raw_value("ocr_min_semantic_hits", DEFAULT_OCR_MIN_SEMANTIC_HITS)), 12)),
        "ocr_require_table_docs": max(0, min(int(_raw_value("ocr_require_table_docs", DEFAULT_OCR_REQUIRE_TABLE_DOCS)), 3)),
        "ocr_primary_engine_pdf": ocr_primary_engine_pdf,
        "ocr_primary_engine_image": ocr_primary_engine_image,
        "ocr_secondary_engine": ocr_secondary_engine,
        "ocr_route_mode": str(_raw_value("ocr_route_mode", DEFAULT_OCR_ROUTE_MODE)).strip().lower() or DEFAULT_OCR_ROUTE_MODE,
        "ocr_qwen_primary": str(_raw_value("ocr_qwen_primary", DEFAULT_OCR_QWEN_PRIMARY)).strip().lower() in {"1", "true", "yes", "on"},
        "ocr_min_non_scrape_ratio": max(0.0, min(float(_raw_value("ocr_min_non_scrape_ratio", DEFAULT_OCR_MIN_NON_SCRAPE_RATIO)), 1.0)),
        "ocr_min_structure_score": max(0.0, min(float(_raw_value("ocr_min_structure_score", DEFAULT_OCR_MIN_STRUCTURE_SCORE)), 1.0)),
        "ocr_min_numeric_fidelity": max(0.0, min(float(_raw_value("ocr_min_numeric_fidelity", DEFAULT_OCR_MIN_NUMERIC_FIDELITY)), 1.0)),
        "ocr_structure_bench_min": max(0, min(int(_raw_value("ocr_structure_bench_min", DEFAULT_OCR_STRUCTURE_BENCH_MIN)), 100)),
        "ocr_correctness_bench_min": max(0, min(int(_raw_value("ocr_correctness_bench_min", DEFAULT_OCR_CORRECTNESS_BENCH_MIN)), 100)),
        "ocr_stage_min_score": max(1, min(int(_raw_value("ocr_stage_min_score", DEFAULT_OCR_STAGE_MIN_SCORE)), 100)),
        "ocr_require_finance_structure": str(_raw_value("ocr_require_finance_structure", DEFAULT_OCR_REQUIRE_FINANCE_STRUCTURE)).strip().lower() in {"1", "true", "yes", "on"},
        "ocr_scan_enabled": str(_raw_value("ocr_scan_enabled", DEFAULT_OCR_SCAN_ENABLED)).strip().lower() in {"1", "true", "yes", "on"},
        "ocr_scan_min_chars": max(100, min(int(_raw_value("ocr_scan_min_chars", DEFAULT_OCR_SCAN_MIN_CHARS)), 20000)),
        "ocr_scan_min_quality_score": max(1.0, min(float(_raw_value("ocr_scan_min_quality_score", DEFAULT_OCR_SCAN_MIN_QUALITY_SCORE)), 100.0)),
        "ocr_scan_min_semantic_hits": max(0, min(int(_raw_value("ocr_scan_min_semantic_hits", DEFAULT_OCR_SCAN_MIN_SEMANTIC_HITS)), 12)),
        "ocr_scan_min_structure_score": max(0.0, min(float(_raw_value("ocr_scan_min_structure_score", DEFAULT_OCR_SCAN_MIN_STRUCTURE_SCORE)), 1.0)),
        "ocr_scan_min_numeric_fidelity": max(0.0, min(float(_raw_value("ocr_scan_min_numeric_fidelity", DEFAULT_OCR_SCAN_MIN_NUMERIC_FIDELITY)), 1.0)),
        "ocr_scan_min_anchor_hits": max(0, min(int(_raw_value("ocr_scan_min_anchor_hits", DEFAULT_OCR_SCAN_MIN_ANCHOR_HITS)), 10)),
        "focus_universe_enabled": focus_universe_enabled,
        "focus_universe_min_names": focus_universe_min_names,
        "focus_universe_max_names": focus_universe_max_names,
        "focus_universe_gb10_only": focus_universe_gb10_only,
        "focus_universe_manifest_path": str(_raw_value("focus_universe_manifest_path", _APOLLO_ROOT / "evaluations" / "apollo_focus_universe_manifest.json")).strip(),
        "focus_universe_decision_min_volume": max(1, min(int(_raw_value("focus_universe_decision_min_volume", DEFAULT_FOCUS_UNIVERSE_DECISION_MIN_VOLUME)), 100)),
        "strict_stage_min_score": max(1, min(int(_raw_value("strict_stage_min_score", STRICT_STAGE_SCORE_MIN)), 100)),
    }
    # Build topic anchor list from topic string + any pre-loaded focus symbols.
    # Used by _ocr_quality_scan_decision to catch extraction drift into generic prose.
    _topic_words = [w.strip().lower() for w in re.split(r"[\s,/|]+", config["topic"]) if len(w.strip()) >= 3]
    _focus_syms = [s.strip().lower() for s in list(config.get("focus_universe_symbols") or []) if s.strip()]
    config["ocr_scan_topic_anchors"] = list(dict.fromkeys(_focus_syms + _topic_words))[:20]
    if run_mode == "background":
        # Background production OCR should stay layout-first for digital PDFs.
        config["ocr_qwen_primary"] = False
        if str(config.get("ocr_primary_engine_pdf") or "").strip().lower() == "gb10_qwen_ocr":
            config["ocr_primary_engine_pdf"] = "gb10_paddleocr_vl"
        # Seed fallback docs are synthetic; keep background strictly on real public sources.
        config["gather_allow_seed_fallback"] = False
    if focus_universe_enabled and focus_universe_gb10_only:
        config["allow_cpu_fallback"] = False
    return config


def _collection_chunk_count(rag_dir: str, collection: str) -> int:
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception:
        return 0
    try:
        client = chromadb.PersistentClient(path=rag_dir, settings=Settings(allow_reset=False, anonymized_telemetry=False))
        return int(client.get_collection(collection).count())
    except Exception:
        return 0


def _financial_chunk_count() -> int:
    return _collection_chunk_count(_resolved_rag_dir(), os.getenv("RAG_COLLECTION", "apollo_financial"))


def _corpus_readiness(rag_dir: str, collection: str, min_docs: Optional[int] = None) -> Dict[str, Any]:
    if min_docs is None:
        min_docs = max(0, int(os.getenv("APOLLO_FINANCIAL_CORPUS_MIN_DOCS", "100")))
    else:
        min_docs = max(0, int(min_docs))
    readiness = {
        "ready": False,
        "rag_dir": rag_dir,
        "collection": collection,
        "doc_count": 0,
        "min_docs_required": min_docs,
        "reason": "",
    }
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:
        readiness["reason"] = f"chromadb_import_failed:{type(exc).__name__}"
        return readiness
    try:
        client = chromadb.PersistentClient(path=rag_dir, settings=Settings(allow_reset=False, anonymized_telemetry=False))
        col = client.get_or_create_collection(collection)
        count = int(col.count())
        readiness["doc_count"] = count
        readiness["ready"] = count >= min_docs
        readiness["reason"] = "" if readiness["ready"] else f"doc_count_below_min:{count}<{min_docs}"
        return readiness
    except Exception as exc:
        readiness["reason"] = f"collection_unavailable:{type(exc).__name__}:{exc}"
        return readiness


def _financial_corpus_readiness() -> Dict[str, Any]:
    return _corpus_readiness(_resolved_rag_dir(), os.getenv("RAG_COLLECTION", "apollo_financial"))


def _nightly_hipporag_readiness(run_dir: Optional[Path] = None) -> Dict[str, Any]:
    nightly_min_docs = int(os.getenv("APOLLO_NIGHTLY_HIPPORAG_MIN_DOCS", "1") or "1")
    return _corpus_readiness(_nightly_hipporag_rag_dir(run_dir), _nightly_hipporag_collection(), min_docs=nightly_min_docs)


def estimate_runtime_breakdown(config: Dict[str, Any], *, chunk_count: Optional[int] = None) -> Dict[str, Any]:
    if chunk_count is not None:
        chunks = int(chunk_count)
    elif str(config.get("run_mode") or "").strip().lower() in {"background", "quality_sample"}:
        chunks = int(_collection_chunk_count(_nightly_hipporag_rag_dir(), _nightly_hipporag_collection()))
    else:
        chunks = int(_financial_chunk_count())
    if config.get("hipporag_limit") is not None:
        chunks = min(chunks or int(config["hipporag_limit"]), int(config["hipporag_limit"]))
    gather_minutes = 15 + (2 * int(config["max_articles"]))
    if bool(config.get("focus_universe_enabled")):
        gather_minutes += 6
    ocr_minutes = 18 * int(config["max_articles"])
    hipporag_per_chunk_sec = 23.0 if config.get("hipporag_use_llm") else 0.35
    hipporag_minutes = 10 + ((chunks * hipporag_per_chunk_sec) / 60.0 if chunks > 0 else 0.0)
    self_check_minutes = 8
    buffer_minutes = 15
    total_minutes = int(math.ceil(gather_minutes + ocr_minutes + hipporag_minutes + self_check_minutes + buffer_minutes))
    return {
        "gather_minutes": int(math.ceil(gather_minutes)),
        "ocr_minutes": int(math.ceil(ocr_minutes)),
        "hipporag_minutes": int(math.ceil(hipporag_minutes)),
        "self_check_minutes": int(math.ceil(self_check_minutes)),
        "buffer_minutes": int(math.ceil(buffer_minutes)),
        "total_minutes": total_minutes,
        "chunk_count": chunks,
        "hipporag_mode": "llm" if config.get("hipporag_use_llm") else "regex",
        "llm_model": str(config.get("hipporag_llm_model") or ""),
    }


def _lock_is_stale(path: Path, stale_after_sec: int, heartbeat_stale_sec: int = DEFAULT_LOCK_HEARTBEAT_STALE_SEC) -> bool:
    if not path.exists():
        return False
    try:
        payload = _read_json(path)
        pid = int(payload.get("owner_pid") or payload.get("pid") or 0)
        started = datetime.fromisoformat(str(payload.get("started_at") or "").replace("Z", "+00:00"))
        heartbeat_raw = str(payload.get("heartbeat_at") or "").strip()
        heartbeat_at = datetime.fromisoformat(heartbeat_raw.replace("Z", "+00:00")) if heartbeat_raw else started
    except Exception:
        pid = 0
        started = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        heartbeat_at = started
    if pid > 0 and not _pid_is_running(pid):
        return True
    heartbeat_age = (_now_utc() - heartbeat_at.astimezone(timezone.utc)).total_seconds()
    if heartbeat_age > heartbeat_stale_sec:
        return True
    return (_now_utc() - started.astimezone(timezone.utc)).total_seconds() > stale_after_sec


def _lock_owner_identity(payload: Dict[str, Any]) -> tuple[int, str]:
    owner_pid = 0
    run_id = str(payload.get("run_id") or "").strip()
    try:
        owner_pid = int(payload.get("owner_pid") or payload.get("pid") or 0)
    except Exception:
        owner_pid = 0
    return owner_pid, run_id


def _parse_lock_present_payload(reason: str) -> Dict[str, Any]:
    text = str(reason or "").strip()
    if not text:
        return {}
    marker = "nightly_lock_present:"
    low = text.lower()
    idx = low.find(marker)
    if idx < 0:
        return {}
    raw = text[idx + len(marker):].strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return {}
    return {}


def _current_lock_state(clear_stale: bool = False) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "path": str(LOCK_PATH),
        "present": False,
        "stale": False,
        "cleared_stale": False,
        "payload": {},
    }
    if not LOCK_PATH.exists():
        return state
    payload = _read_json(LOCK_PATH, {})
    stale = _lock_is_stale(LOCK_PATH, DEFAULT_LOCK_STALE_SEC)
    state["present"] = True
    state["stale"] = bool(stale)
    state["payload"] = payload if isinstance(payload, dict) else {}
    if bool(stale) and bool(clear_stale):
        try:
            LOCK_PATH.unlink()
            state["present"] = False
            state["stale"] = False
            state["cleared_stale"] = True
            state["payload"] = {}
        except Exception as exc:
            state["clear_error"] = f"{type(exc).__name__}:{exc}"
    return state


def background_lock_state(clear_stale: bool = False) -> Dict[str, Any]:
    return _current_lock_state(clear_stale=clear_stale)


def _clear_stale_lock_waiting(status: Dict[str, Any], *, lock_state: Optional[Dict[str, Any]] = None) -> bool:
    if not isinstance(status, dict):
        return False
    waiting = status.get("waiting")
    if not isinstance(waiting, dict):
        return False
    reason = str(waiting.get("reason") or "").strip()
    if "nightly_lock_present" not in reason.lower():
        return False
    active_lock = dict(lock_state or _current_lock_state(clear_stale=True))
    if not bool(active_lock.get("present")):
        status.pop("waiting", None)
        return True
    reason_payload = _parse_lock_present_payload(reason)
    if not reason_payload:
        return False
    reason_pid, reason_run_id = _lock_owner_identity(reason_payload)
    live_payload = dict(active_lock.get("payload") or {})
    live_pid, live_run_id = _lock_owner_identity(live_payload)
    if reason_pid > 0 and live_pid > 0 and reason_pid != live_pid:
        status.pop("waiting", None)
        return True
    if reason_run_id and live_run_id and reason_run_id != live_run_id:
        status.pop("waiting", None)
        return True
    return False


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _touch_lock(run_id: str, stage: str = "") -> None:
    try:
        if not LOCK_PATH.exists():
            return
        current = _read_json(LOCK_PATH, {})
        current_run_id = str(current.get("run_id") or "").strip()
        if current_run_id not in {"", str(run_id).strip()}:
            return
        current["run_id"] = str(run_id).strip()
        owner_pid = int(current.get("owner_pid") or current.get("pid") or os.getpid())
        current["owner_pid"] = owner_pid
        current["pid"] = owner_pid
        current["heartbeat_at"] = _utc_iso()
        if stage:
            current["stage"] = stage
        _write_json(LOCK_PATH, current)
    except Exception:
        return


def _acquire_lock(run_id: str, run_dir: Path, stage: str = "acquired") -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists() and _lock_is_stale(LOCK_PATH, DEFAULT_LOCK_STALE_SEC):
        try:
            LOCK_PATH.unlink()
        except Exception:
            pass
    ts = _utc_iso()
    payload = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "started_at": ts,
        "heartbeat_at": ts,
        "owner_pid": os.getpid(),
        "pid": os.getpid(),
        "stage": stage,
    }
    try:
        handle = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        current = _read_json(LOCK_PATH, {"error": "nightly_lock_present"})
        raise RuntimeError(f"nightly_lock_present:{current}") from exc
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2)
    _touch_lock(run_id, stage)


def _release_lock(run_id: str) -> None:
    current = _read_json(LOCK_PATH, {})
    if str(current.get("run_id") or "") not in {"", run_id}:
        return
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        return


def _find_python_executable() -> str:
    return sys.executable or "python"


def _task_wrapper_path() -> Path:
    return _APOLLO_ROOT / "watchdog" / "run_apollo_nightly_pipeline.ps1"


def _background_task_wrapper_path() -> Path:
    return _APOLLO_ROOT / "watchdog" / "run_apollo_background_pipeline.ps1"


def _hidden_task_runner_path() -> Path:
    return _ENGINEERING_ROOT / "tools" / "run_hidden.vbs"


def _powershell_executable() -> str:
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "powershell.exe"


def _wscript_executable() -> str:
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "wscript.exe",
        Path(r"C:\Windows\System32\wscript.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "wscript.exe"


def _hidden_task_command(executable: str, arguments: list[str]) -> str:
    runner = _hidden_task_runner_path()
    cmd = [_wscript_executable(), "//B", "//NoLogo", str(runner), str(executable), *[str(item) for item in arguments]]
    return subprocess.list2cmdline(cmd)


def _powershell(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    cmd = [_powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", *args]
    return subprocess.run(cmd, cwd=str(_ENGINEERING_ROOT), capture_output=True, text=True, timeout=timeout, check=False)


def _ensure_gx10_tunnel(config: Dict[str, Any], run_dir: Path) -> Dict[str, Any]:
    log_path = run_dir / "00_preflight.log"
    status = _powershell(["-File", str(_ENGINEERING_ROOT / "tools" / "gx10_ollama_tunnel.ps1"), "status"], timeout=45)
    start = None
    if "running" not in (status.stdout or "").lower():
        start = _powershell(["-File", str(_ENGINEERING_ROOT / "tools" / "gx10_ollama_tunnel.ps1"), "start"], timeout=90)
    manifest = load_agent_manifest("Apollo")
    target = dict(((manifest.get("ui_chat_policy") or {}).get("primary") or {}))
    target["provider"] = str(target.get("provider") or "ollama").strip() or "ollama"
    target["model"] = str(config.get("hipporag_llm_model") or target.get("model") or "gemma4:31b")
    if not target.get("base_url"):
        target["base_url"] = str(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    gpu = verify_gpu_backed_target(target)
    warmup = {}
    if bool(gpu.get("healthy")) and not bool(gpu.get("gpu_verified")):
        try:
            resp = requests.post(
                f"{str(target['base_url']).rstrip('/')}/api/generate",
                json={
                    "model": str(target["model"]),
                    "prompt": "ping",
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": 1},
                },
                timeout=90,
            )
            warmup = {"status_code": resp.status_code, "ok": bool(resp.ok)}
        except Exception as exc:
            warmup = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
        gpu = verify_gpu_backed_target(target)
    tunnel_stdout = f"{status.stdout or ''}\n{(start.stdout if start else '') or ''}".lower()
    gx10_tunnel_active = "running" in tunnel_stdout and "listening=true" in tunnel_stdout
    route_attested = bool(gpu.get("healthy")) and bool(gpu.get("model_available")) and gx10_tunnel_active
    ok = bool(gpu.get("healthy")) and (
        bool(gpu.get("gpu_verified")) or route_attested or bool(config.get("allow_cpu_fallback"))
    )
    if not ok:
        gpu["error"] = str(gpu.get("error") or "gpu_preflight_failed")
    _write_text(
        log_path,
        "\n".join(
            [
                f"status_stdout:\n{status.stdout or ''}",
                f"status_stderr:\n{status.stderr or ''}",
                f"start_stdout:\n{(start.stdout if start else '') or ''}",
                f"start_stderr:\n{(start.stderr if start else '') or ''}",
                "",
                json.dumps({"warmup": warmup}, indent=2),
                "",
                json.dumps(gpu, indent=2),
                "",
            ]
        ),
    )
    payload = {
        "ok": ok,
        "target": target,
        "gpu_probe": gpu,
        "gx10_tunnel_active": gx10_tunnel_active,
        "route_attested": route_attested,
        "tunnel_status_stdout": status.stdout or "",
        "tunnel_status_stderr": status.stderr or "",
        "tunnel_start_stdout": (start.stdout if start else "") or "",
        "tunnel_start_stderr": (start.stderr if start else "") or "",
        "warmup": warmup,
        "log_path": str(log_path),
    }
    _write_json(run_dir / "00_preflight.json", payload)
    return payload


def _await_preflight_ready(config: Dict[str, Any], run_dir: Path, *, status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    max_wait_raw = config.get("preflight_max_wait_sec")
    retry_interval_raw = config.get("preflight_retry_interval_sec")
    max_wait_sec = max(0, int(DEFAULT_PREFLIGHT_MAX_WAIT_SEC if max_wait_raw is None else max_wait_raw))
    retry_interval_sec = max(
        5,
        int(DEFAULT_PREFLIGHT_RETRY_INTERVAL_SEC if retry_interval_raw is None else retry_interval_raw),
    )
    started = time.perf_counter()
    attempt = 0
    last: Dict[str, Any] = {}
    while True:
        attempt += 1
        preflight = _ensure_gx10_tunnel(config, run_dir)
        elapsed = int(max(0, time.perf_counter() - started))
        preflight["attempt"] = attempt
        preflight["elapsed_wait_sec"] = elapsed
        preflight["max_wait_sec"] = max_wait_sec
        _write_json(run_dir / "00_preflight.json", preflight)
        if preflight.get("ok"):
            return preflight

        last = preflight
        remaining = max_wait_sec - elapsed
        if remaining <= 0:
            break

        sleep_sec = max(1, min(retry_interval_sec, remaining))
        if isinstance(status, dict):
            status["preflight"] = preflight
            status["waiting"] = {
                "reason": "gpu_preflight_failed",
                "ts": _utc_iso(),
                "attempt": attempt,
                "elapsed_wait_sec": elapsed,
                "next_retry_sec": sleep_sec,
            }
            _save_status(run_dir, status)
        time.sleep(sleep_sec)
    return last


def _memory_pressure_snapshot(config: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(config.get("memory_guard_enabled", DEFAULT_MEMORY_GUARD_ENABLED)):
        return {"ok": True, "state": "disabled", "reasons": [], "metrics": {}, "thresholds": {}, "enabled": False}
    snapshot = evaluate_memory_pressure(
        min_available_mb=int(config.get("memory_min_available_mb") or DEFAULT_MEMORY_MIN_AVAILABLE_MB),
        max_used_percent=float(config.get("memory_max_used_percent") or DEFAULT_MEMORY_MAX_USED_PERCENT),
        max_vmmem_mb=int(config.get("memory_max_vmmem_mb") or DEFAULT_MEMORY_MAX_VMMEM_MB),
        max_ollama_runner_count=int(config.get("memory_max_ollama_runners") or DEFAULT_MEMORY_MAX_OLLAMA_RUNNERS),
    )
    snapshot["enabled"] = True
    return snapshot


def _await_memory_ready(config: Dict[str, Any], run_dir: Path, *, status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    max_wait_raw = config.get("memory_wait_sec")
    retry_interval_raw = config.get("memory_retry_sec")
    max_wait_sec = max(0, int(DEFAULT_MEMORY_WAIT_SEC if max_wait_raw is None else max_wait_raw))
    retry_interval_sec = max(5, int(DEFAULT_MEMORY_RETRY_SEC if retry_interval_raw is None else retry_interval_raw))
    started = time.perf_counter()
    attempt = 0
    last: Dict[str, Any] = {}
    while True:
        attempt += 1
        snapshot = _memory_pressure_snapshot(config)
        elapsed = int(max(0, time.perf_counter() - started))
        snapshot["attempt"] = attempt
        snapshot["elapsed_wait_sec"] = elapsed
        snapshot["max_wait_sec"] = max_wait_sec
        _write_json(run_dir / "00_memory_guard.json", snapshot)
        if snapshot.get("ok"):
            return snapshot

        last = snapshot
        remaining = max_wait_sec - elapsed
        if remaining <= 0:
            break

        sleep_sec = max(1, min(retry_interval_sec, remaining))
        if isinstance(status, dict):
            status["memory_pressure"] = snapshot
            status["waiting"] = {
                "reason": "memory_pressure",
                "ts": _utc_iso(),
                "attempt": attempt,
                "elapsed_wait_sec": elapsed,
                "next_retry_sec": sleep_sec,
                "reasons": list(snapshot.get("reasons") or []),
            }
            _save_status(run_dir, status)
        time.sleep(sleep_sec)
    return last


def _ollama_active_model_count(base_url: str, idle_threshold_sec: int = 300) -> int:
    """Count models recently used, not just kept warm.

    Ollama resets expires_at to now+keep_alive on every request.
    If expires_at > now + idle_threshold_sec the model was touched recently â†’ active.
    If expires_at â‰¤ now + idle_threshold_sec it's just coasting on keep_alive â†’ idle.
    Models with no expires_at field are conservatively counted as active.
    """
    try:
        resp = requests.get(f"{str(base_url).rstrip('/')}/api/ps", timeout=3)
        if not resp.ok:
            return 0
        models = (resp.json() or {}).get("models") or []
        now = datetime.now(timezone.utc)
        threshold = now + timedelta(seconds=idle_threshold_sec)
        count = 0
        for m in models:
            expires_raw = str(m.get("expires_at") or "")
            if not expires_raw:
                count += 1  # no expiry info â€” assume active
                continue
            try:
                expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                if expires_at > threshold:
                    count += 1  # expiry far out â†’ refreshed recently â†’ active
            except Exception:
                count += 1  # can't parse â†’ conservative
        return count
    except Exception:
        return 0


def _system_idle_seconds() -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick_now = ctypes.windll.kernel32.GetTickCount()
        return max(0, int((tick_now - info.dwTime) / 1000))
    except Exception:
        return None


def _background_gx10_idle(config: Dict[str, Any]) -> Dict[str, Any]:
    # Check both the main Ollama endpoint (11434, used by Apollo chat + pipeline)
    # and the deep/gx10 endpoint (11435, used by Sky deep/plan/reason/think).
    # If either has active models the gate blocks.
    primary_url = str(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    deep_url = str(os.getenv("OLLAMA_URL_DEEP", "http://127.0.0.1:11435"))

    # idle_threshold_sec: how long since last Ollama request before a model is considered idle.
    # Default 300s â€” a model used in the last 5 min blocks the gate; one just kept warm doesn't.
    idle_threshold_sec = int(config.get("background_min_idle_sec") or 300)
    primary_active = _ollama_active_model_count(primary_url, idle_threshold_sec=idle_threshold_sec)
    deep_active = _ollama_active_model_count(deep_url, idle_threshold_sec=idle_threshold_sec) if deep_url != primary_url else 0
    active_models = primary_active + deep_active

    raw_max = config.get("background_max_active_models")
    max_active = int(raw_max) if raw_max is not None else DEFAULT_BACKGROUND_MAX_ACTIVE_MODELS
    # Gate is based solely on GB10 activity â€” keyboard/mouse idle is irrelevant
    # because all heavy work runs on the remote GPU (GB10), not the local machine.
    idle = active_models <= max_active
    return {
        "idle": idle,
        "active_models": active_models,
        "active_models_primary": primary_active,
        "active_models_deep": deep_active,
        "max_active_models": max_active,
        "idle_threshold_sec": idle_threshold_sec,
        "base_url": primary_url,
        "deep_url": deep_url,
    }


def _stage_names() -> tuple[str, ...]:
    return ("gather", "ocr", "hipporag", "self_check")


def _stage_is_complete(stage_payload: Dict[str, Any]) -> bool:
    if not stage_payload:
        return False
    if "completed" in stage_payload:
        return bool(stage_payload.get("completed"))
    nested = stage_payload.get("result")
    if isinstance(nested, dict) and "completed" in nested:
        return bool(nested.get("completed"))
    return bool(stage_payload.get("ok"))


def _stage_payload_from_files(run_dir: Path, stage_name: str, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    existing_row = dict(existing or {})
    row = _stage_result_view(existing_row)
    nested = row.get("result") if isinstance(row.get("result"), dict) else {}
    result_path = _stage_result_path(run_dir, stage_name)
    log_path = _stage_log_path(run_dir, stage_name)
    file_payload = _read_json(result_path, {}) if result_path.exists() else {}
    if not existing_row and not file_payload and not result_path.exists() and not log_path.exists():
        return {}
    if file_payload:
        merged = dict(file_payload)
        if isinstance(nested, dict):
            merged.update(nested)
        row.update(_stage_result_view(merged))
        row["result"] = merged
        if "ok" not in row:
            row["ok"] = bool(merged.get("ok"))
        if "completed" not in row:
            row["completed"] = bool(merged.get("completed") if "completed" in merged else merged.get("ok"))
    if result_path.exists():
        row.setdefault("result_path", str(result_path))
    if log_path.exists():
        row.setdefault("log_path", str(log_path))
    row.setdefault("stage", stage_name)
    return row


def _reconcile_stage_files(run_dir: Path, status: Dict[str, Any]) -> Dict[str, Any]:
    reconciled = dict(status or {})
    stages = dict(reconciled.get("stages") or {})
    for stage_name in _stage_names():
        row = _stage_payload_from_files(run_dir, stage_name, dict(stages.get(stage_name) or {}))
        if row:
            stages[stage_name] = row
    reconciled["stages"] = stages
    return reconciled


def _stage_truth_for_status(status: Dict[str, Any]) -> Dict[str, Any]:
    stages = dict(status.get("stages") or {})
    stage_truth: Dict[str, Any] = {}
    for stage_name in _stage_names():
        row = _stage_result_view(dict(stages.get(stage_name) or {}))
        stage_truth[stage_name] = {
            "ok": bool(row.get("ok")),
            "completed": _stage_is_complete(row),
            "error": str(row.get("error") or ""),
            "timed_out": bool(row.get("timed_out")),
            "result_path": str(row.get("result_path") or ""),
            "log_path": str(row.get("log_path") or ""),
        }
    pipeline_completed = all(bool(row.get("completed")) for row in stage_truth.values())
    quality = dict(status.get("quality") or {})
    quality_gate_pass = bool(quality.get("gate_pass"))
    waiting = dict(status.get("waiting") or {})
    if waiting:
        truth_label = "blocked"
    elif not pipeline_completed:
        truth_label = "partial"
    elif quality_gate_pass:
        truth_label = "passed"
    else:
        truth_label = "quality_failed"
    return {
        "stage_truth": stage_truth,
        "pipeline_completed": pipeline_completed,
        "quality_gate_pass": quality_gate_pass,
        "truth_label": truth_label,
    }


def _all_stages_complete(status: Dict[str, Any]) -> bool:
    stages = dict(status.get("stages") or {})
    return all(_stage_is_complete(dict(stages.get(name) or {})) for name in _stage_names())


def _next_incomplete_stage(status: Dict[str, Any]) -> str:
    stages = dict(status.get("stages") or {})
    for name in _stage_names():
        if not _stage_is_complete(dict(stages.get(name) or {})):
            return name
    return "done"


def _stage_result_path(run_dir: Path, stage_name: str) -> Path:
    mapping = {
        "gather": "01_data_gather.json",
        "triage": "01b_triage.json",
        "ocr": "02_ocr_ingest.json",
        "hipporag": "03_hipporag.json",
        "self_check": "04_self_check.json",
    }
    return run_dir / mapping[stage_name]


def _stage_log_path(run_dir: Path, stage_name: str) -> Path:
    mapping = {
        "gather": "01_data_gather.log",
        "ocr": "02_ocr_ingest.log",
        "hipporag": "03_hipporag.log",
        "self_check": "04_self_check.log",
    }
    return run_dir / mapping[stage_name]


def _run_subprocess_stage(run_dir: Path, stage_name: str, timeout_sec: int) -> Dict[str, Any]:
    log_path = _stage_log_path(run_dir, stage_name)
    result_path = _stage_result_path(run_dir, stage_name)
    config_path = run_dir / "config.json"
    started = _utc_iso()
    cmd = [_find_python_executable(), "-m", "Apollo.nightly_pipeline", "stage", stage_name, "--run-dir", str(run_dir), "--config", str(config_path)]
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write(f"[stage:{stage_name}] start={started}\n")
        log_handle.write(f"[stage:{stage_name}] cmd={' '.join(cmd)}\n")
        log_handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(_ENGINEERING_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        timed_out = False
        try:
            return_code = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            return_code = -9
            log_handle.write(f"[stage:{stage_name}] timeout after {timeout_sec}s\n")
        finished = _utc_iso()
        payload = _read_json(result_path, {})
        return {
            "stage": stage_name,
            "ok": bool(payload.get("ok")) and return_code == 0 and not timed_out,
            "return_code": return_code,
            "timed_out": timed_out,
            "started_at": started,
            "finished_at": finished,
            "timeout_sec": timeout_sec,
            "log_path": str(log_path),
            "result_path": str(result_path),
            "result": payload,
        }


def _run_background_subprocess_stage(run_id: str, run_dir: Path, stage_name: str, timeout_sec: int) -> Dict[str, Any]:
    log_path = _stage_log_path(run_dir, stage_name)
    result_path = _stage_result_path(run_dir, stage_name)
    config_path = run_dir / "config.json"
    started = _utc_iso()
    cmd = [
        _find_python_executable(),
        "-m",
        "Apollo.nightly_pipeline",
        "background-stage",
        stage_name,
        "--run-dir",
        str(run_dir),
        "--config",
        str(config_path),
    ]
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write(f"[background-stage:{stage_name}] start={started}\n")
        log_handle.write(f"[background-stage:{stage_name}] cmd={' '.join(cmd)}\n")
        log_handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(_ENGINEERING_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        timed_out = False
        return_code = 0
        start_monotonic = time.monotonic()
        last_heartbeat = start_monotonic
        while True:
            poll = proc.poll()
            now = time.monotonic()
            if now - last_heartbeat >= 5.0:
                _touch_lock(run_id, stage_name)
                last_heartbeat = now
            if poll is not None:
                return_code = int(poll)
                break
            if (now - start_monotonic) >= timeout_sec:
                timed_out = True
                proc.kill()
                return_code = -9
                log_handle.write(f"[background-stage:{stage_name}] timeout after {timeout_sec}s\n")
                log_handle.flush()
                break
            time.sleep(1.0)
        finished = _utc_iso()
        _touch_lock(run_id, stage_name)
        payload = _read_json(result_path, {})
        if timed_out:
            payload = {"ok": False, "error": "background_stage_timeout", "completed": False}
            _write_json(result_path, payload)
        return {
            "stage": stage_name,
            "ok": bool(payload.get("ok")) and return_code == 0 and not timed_out,
            "return_code": return_code,
            "timed_out": timed_out,
            "started_at": started,
            "finished_at": finished,
            "timeout_sec": timeout_sec,
            "log_path": str(log_path),
            "result_path": str(result_path),
            "result": payload,
        }


def _update_stage_status(run_dir: Path, stage_name: str, stage_payload: Dict[str, Any]) -> None:
    status = _load_status(run_dir)
    stages = dict(status.get("stages") or {})
    stage_row = _stage_result_view(stage_payload)
    if isinstance(stage_payload, dict):
        for key in ("timed_out", "timeout_sec", "return_code", "started_at", "finished_at", "log_path", "result_path", "stage"):
            if key in stage_payload and key not in stage_row:
                stage_row[key] = stage_payload[key]
        nested = stage_payload.get("result")
        if isinstance(nested, dict):
            stage_row["result"] = nested
    stages[stage_name] = stage_row
    status["stages"] = stages
    status["ok"] = _all_stages_complete(status)
    status["current_stage"] = _next_incomplete_stage(status)
    status.pop("waiting", None)
    _save_status(run_dir, status)


def _render_summary(run_dir: Path, config: Dict[str, Any], estimate: Dict[str, Any]) -> str:
    status = _enrich_status_payload(run_dir, _load_status(run_dir))
    stages = dict(status.get("stages") or {})
    quality = dict(status.get("quality") or {})
    fail_reasons = list(quality.get("gate_fail_reasons") or [])
    lines = [
        "# Apollo Nightly Pipeline",
        "",
        f"- run_id: `{status.get('run_id')}`",
        f"- started_at: `{status.get('started_at')}`",
        f"- finished_at: `{status.get('finished_at')}`",
        f"- overall_ok: `{status.get('ok')}`",
        f"- truth_label: `{status.get('truth_label')}`",
        f"- pipeline_completed: `{status.get('pipeline_completed')}`",
        f"- quality_gate_pass: `{status.get('quality_gate_pass')}`",
        f"- quality_gate_reasons: `{', '.join(str(reason) for reason in fail_reasons) or 'none'}`",
        f"- topic: `{config.get('topic')}`",
        f"- estimated_minutes: `{estimate.get('total_minutes')}`",
        "",
        "## Stage Results",
    ]
    for stage_name in ("gather", "ocr", "hipporag", "self_check"):
        row = dict(stages.get(stage_name) or {})
        lines.append(
            f"- {stage_name}: ok={row.get('ok')} timeout={row.get('timed_out')} log=`{row.get('log_path', '')}` result=`{row.get('result_path', '')}`"
        )
    preflight = _read_json(run_dir / "00_preflight.json", {})
    lines.extend(
        [
            "",
            "## Preflight",
            f"- ok: `{preflight.get('ok')}`",
            f"- model: `{((preflight.get('gpu_probe') or {}).get('model') or '')}`",
            f"- gpu_verified: `{((preflight.get('gpu_probe') or {}).get('gpu_verified'))}`",
            f"- log: `{preflight.get('log_path', '')}`",
            "",
            "## Runtime Estimate",
            f"- gather_minutes: `{estimate.get('gather_minutes')}`",
            f"- ocr_minutes: `{estimate.get('ocr_minutes')}`",
            f"- hipporag_minutes: `{estimate.get('hipporag_minutes')}`",
            f"- self_check_minutes: `{estimate.get('self_check_minutes')}`",
            f"- chunk_count: `{estimate.get('chunk_count')}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def run_pipeline(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = _pipeline_config(payload)
    estimate = estimate_runtime_breakdown(config)
    run_id = str(config["batch_id"])
    run_dir = RUNS_ROOT / _now_local().strftime("%Y-%m-%d") / _safe_slug(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "config.json", config)
    started = _utc_iso()
    status = {
        "ok": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "started_at": started,
        "finished_at": "",
        "estimate": estimate,
        "stages": {},
        "current_stage": "gather",
        "run_mode": str(config.get("run_mode") or "nightly"),
    }
    _save_status(run_dir, status)
    try:
        _acquire_lock(run_id, run_dir)
    except Exception as exc:
        status["finished_at"] = _utc_iso()
        status["error"] = f"lock_acquire_failed:{type(exc).__name__}:{exc}"
        status["waiting"] = {"reason": str(exc), "ts": _utc_iso()}
        _save_status(run_dir, status)
        _write_text(run_dir / "summary.md", _render_summary(run_dir, config, estimate))
        return status
    try:
        memory_gate = _await_memory_ready(config, run_dir, status=status)
        status["memory_pressure"] = memory_gate
        if memory_gate.get("ok"):
            status.pop("waiting", None)
        _save_status(run_dir, status)
        if not memory_gate.get("ok"):
            status["finished_at"] = _utc_iso()
            status["error"] = "memory_pressure"
            status["waiting"] = {
                "reason": "memory_pressure",
                "ts": _utc_iso(),
                "attempt": int(memory_gate.get("attempt") or 1),
                "elapsed_wait_sec": int(memory_gate.get("elapsed_wait_sec") or 0),
                "max_wait_sec": int(memory_gate.get("max_wait_sec") or 0),
                "reasons": list(memory_gate.get("reasons") or []),
            }
            _save_status(run_dir, status)
            _write_text(run_dir / "summary.md", _render_summary(run_dir, config, estimate))
            return status

        preflight = _await_preflight_ready(config, run_dir, status=status)
        status["preflight"] = preflight
        if preflight.get("ok"):
            status.pop("waiting", None)
        _save_status(run_dir, status)
        if not preflight.get("ok"):
            status["finished_at"] = _utc_iso()
            status["error"] = "gpu_preflight_failed"
            status["waiting"] = {
                "reason": "gpu_preflight_failed_timeout",
                "ts": _utc_iso(),
                "attempt": int(preflight.get("attempt") or 1),
                "elapsed_wait_sec": int(preflight.get("elapsed_wait_sec") or 0),
                "max_wait_sec": int(preflight.get("max_wait_sec") or 0),
            }
            _save_status(run_dir, status)
            _write_text(run_dir / "summary.md", _render_summary(run_dir, config, estimate))
            return status

        started_monotonic = time.perf_counter()
        stages = [
            ("gather", int(config["gather_timeout_sec"])),
            ("ocr", int(config["ocr_timeout_sec"])),
            ("hipporag", int(config["hipporag_timeout_sec"])),
            ("self_check", int(config["self_check_timeout_sec"])),
        ]
        for stage_name, timeout_sec in stages:
            stage_memory = _memory_pressure_snapshot(config)
            status["memory_pressure"] = stage_memory
            _save_status(run_dir, status)
            if not stage_memory.get("ok"):
                stage_payload = {
                    "stage": stage_name,
                    "ok": False,
                    "timed_out": False,
                    "return_code": -7,
                    "timeout_sec": timeout_sec,
                    "started_at": _utc_iso(),
                    "finished_at": _utc_iso(),
                    "log_path": "",
                    "result_path": "",
                    "result": {"error": "memory_pressure", "memory_pressure": stage_memory},
                }
                _update_stage_status(run_dir, stage_name, stage_payload)
                break
            if (time.perf_counter() - started_monotonic) >= int(config["overall_timeout_sec"]):
                stage_payload = {
                    "stage": stage_name,
                    "ok": False,
                    "timed_out": True,
                    "return_code": -9,
                    "timeout_sec": int(config["overall_timeout_sec"]),
                    "started_at": _utc_iso(),
                    "finished_at": _utc_iso(),
                    "log_path": "",
                    "result_path": "",
                    "result": {"error": "overall_timeout_reached"},
                }
                _update_stage_status(run_dir, stage_name, stage_payload)
                break
            if _stage_requires_gb10(stage_name, config):
                remaining_wait = max(0, int(config["overall_timeout_sec"]) - int(time.perf_counter() - started_monotonic))
                gate = _wait_for_gb10_idle(config, stage_name=stage_name, max_wait_sec=min(remaining_wait, 1800))
                status["gb10_gate"] = {**gate, "ts": _utc_iso()}
                if not gate.get("idle"):
                    stage_payload = {
                        "stage": stage_name,
                        "ok": False,
                        "timed_out": False,
                        "return_code": -8,
                        "timeout_sec": timeout_sec,
                        "started_at": _utc_iso(),
                        "finished_at": _utc_iso(),
                        "log_path": "",
                        "result_path": "",
                        "result": {"error": "gb10_busy_timeout", "gate": gate},
                    }
                    _update_stage_status(run_dir, stage_name, stage_payload)
                    break
                _save_status(run_dir, status)
            stage_payload = _run_subprocess_stage(run_dir, stage_name, timeout_sec)
            _update_stage_status(run_dir, stage_name, stage_payload)
            if not stage_payload.get("ok"):
                break

        final_status = _load_status(run_dir)
        final_status["finished_at"] = _utc_iso()
        completed = _all_stages_complete(final_status)
        if not completed and not final_status.get("waiting"):
            current_stage = _next_incomplete_stage(final_status)
            stage_row = dict((final_status.get("stages") or {}).get(current_stage) or {})
            stage_result = dict(stage_row.get("result") or {})
            if str(stage_result.get("error") or "").strip().lower() == "memory_pressure":
                memory_info = dict(stage_result.get("memory_pressure") or {})
                final_status["waiting"] = {
                    "reason": "memory_pressure",
                    "ts": _utc_iso(),
                    "reasons": list(memory_info.get("reasons") or []),
                }
        final_status["ok"] = bool(completed and ((final_status.get("quality") or {}).get("gate_pass")))
        final_status["current_stage"] = "done" if completed else _next_incomplete_stage(final_status)
        _save_status(run_dir, final_status)
        _write_text(run_dir / "summary.md", _render_summary(run_dir, config, estimate))
        if completed:
            final_status["gateway_probe"] = _run_aegis_gateway_probe(run_dir, final_status)
            _save_status(run_dir, final_status)
            _write_truthful_pipeline_report(run_dir, final_status)
        return final_status
    finally:
        _release_lock(run_id)


def _load_status_from_pointer() -> Dict[str, Any]:
    pointer = _read_current_run_pointer()
    run_dir_raw = str(pointer.get("run_dir") or "").strip()
    if not run_dir_raw:
        return {}
    try:
        run_dir = Path(run_dir_raw).resolve()
    except Exception:
        return {}
    if not _is_production_run_dir(run_dir):
        return {}
    status = _read_json(_status_path(run_dir), {})
    if not isinstance(status, dict) or not status:
        return {}
    status["run_meta"] = {
        "current_pointer_run_id": str(pointer.get("run_id") or "").strip(),
        "current_pointer_run_dir": str(run_dir),
        "current_pointer_valid": True,
    }
    return status


def _latest_dated_run_status() -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    best_ts = 0.0
    try:
        roots = [path for path in RUNS_ROOT.iterdir() if path.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", path.name)]
    except Exception:
        return {}
    for date_root in roots:
        for status_path in date_root.glob("*/status.json"):
            payload = _read_json(status_path, {})
            if not isinstance(payload, dict) or not payload:
                continue
            run_dir = status_path.parent
            if not _is_production_run_dir(run_dir):
                continue
            payload["run_dir"] = str(run_dir.resolve())
            ts = max(
                _parse_status_time(payload.get("updated_at")),
                _parse_status_time(payload.get("finished_at")),
                _parse_status_time(payload.get("started_at")),
            )
            if ts >= best_ts:
                best = payload
                best_ts = ts
    if best:
        try:
            best = _enrich_status_payload(Path(str(best.get("run_dir"))), best)
        except Exception:
            pass
    return best


def _with_stale_background_truth(status: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(status or {})
    if not current or not _is_background_run_status(current):
        return current
    finished_raw = str(current.get("finished_at") or "").strip()
    if not finished_raw:
        return current
    today = _now_local().date().isoformat()
    if finished_raw[:10] == today:
        return current
    latest_dated = _latest_dated_run_status()
    if not latest_dated:
        return current
    latest_ts = max(
        _parse_status_time(latest_dated.get("updated_at")),
        _parse_status_time(latest_dated.get("finished_at")),
        _parse_status_time(latest_dated.get("started_at")),
    )
    current_ts = max(
        _parse_status_time(current.get("updated_at")),
        _parse_status_time(current.get("finished_at")),
        _parse_status_time(current.get("started_at")),
    )
    if latest_ts <= current_ts and str(latest_dated.get("started_at") or "")[:10] != today:
        return current
    current["truth_label"] = "stale"
    current["stale"] = True
    current["stale_reason"] = "background_current_finished_before_today_and_newer_dated_run_exists"
    current["latest_real_run"] = {
        "run_id": latest_dated.get("run_id"),
        "run_dir": latest_dated.get("run_dir"),
        "started_at": latest_dated.get("started_at"),
        "finished_at": latest_dated.get("finished_at"),
        "truth_label": latest_dated.get("truth_label"),
        "ok": latest_dated.get("ok"),
    }
    return current


def latest_status() -> Dict[str, Any]:
    lock_state = _current_lock_state(clear_stale=True)
    from_pointer = _load_status_from_pointer()
    if from_pointer:
        run_dir_raw = str(from_pointer.get("run_dir") or "").strip()
        if run_dir_raw:
            try:
                from_pointer = _enrich_status_payload(Path(run_dir_raw).resolve(), from_pointer)
            except Exception:
                pass
        if _clear_stale_lock_waiting(from_pointer, lock_state=lock_state):
            run_dir_raw = str(from_pointer.get("run_dir") or "").strip()
            try:
                run_dir = Path(run_dir_raw).resolve()
                if _is_production_run_dir(run_dir):
                    _save_status(run_dir, from_pointer)
            except Exception:
                pass
        return _with_stale_background_truth(from_pointer)
    latest = _read_json(LATEST_STATUS_PATH, {})
    run_dir_raw = str(latest.get("run_dir") or "").strip()
    if run_dir_raw:
        try:
            run_dir = Path(run_dir_raw).resolve()
            if not _is_production_run_dir(run_dir):
                return {"ok": False, "error": "no_runs", "run_meta": {"current_pointer_valid": False}}
        except Exception:
            return {"ok": False, "error": "no_runs", "run_meta": {"current_pointer_valid": False}}
    if latest:
        if run_dir_raw:
            try:
                latest = _enrich_status_payload(Path(run_dir_raw).resolve(), latest)
            except Exception:
                pass
        if _clear_stale_lock_waiting(latest, lock_state=lock_state):
            run_dir_raw = str(latest.get("run_dir") or "").strip()
            try:
                run_dir = Path(run_dir_raw).resolve()
                if _is_production_run_dir(run_dir):
                    _save_status(run_dir, latest)
            except Exception:
                pass
        latest.setdefault("run_meta", {"current_pointer_valid": False})
        return _with_stale_background_truth(latest)
    return {"ok": False, "error": "no_runs", "run_meta": {"current_pointer_valid": False}}


def _recommended_next_action(status: Dict[str, Any]) -> str:
    waiting = dict(status.get("waiting") or {})
    reason = str(waiting.get("reason") or "").strip().lower()
    quality = dict(status.get("quality") or {})
    gate_fail = list(quality.get("gate_fail_reasons") or [])
    if reason == "gb10_busy":
        return "wait_for_next_tick_or_click_run_step_now"
    if reason == "memory_pressure":
        return "free_memory_then_retry"
    if reason == "insufficient_real_sources":
        return "retry_gather_until_real_sources_available"
    if reason in {"gpu_preflight_failed", "gpu_preflight_failed_timeout"}:
        return "restore_gx10_tunnel_then_rerun"
    if reason.startswith("stage_timeout"):
        return "inspect_stage_log_then_rerun_step"
    if reason == "auto_recovery_triggered":
        return "allow_recovery_then_review_new_gather_sources"
    if gate_fail:
        if any("gather" in item for item in gate_fail):
            return "tighten_gather_sources_or_run_remediation"
        if any("ocr" in item for item in gate_fail):
            return "improve_ocr_inputs_and_retry"
        if any("hipporag" in item for item in gate_fail):
            return "purge_low_value_docs_and_rebuild_graph"
    if str(status.get("current_stage") or "") == "done" and bool(status.get("ok")):
        return "none"
    return "run_step_now"


def list_background_run_history(limit: int = 200) -> Dict[str, Any]:
    runs: list[Dict[str, Any]] = []
    latest = latest_status()
    latest_run_dir = str((latest or {}).get("run_dir") or "").strip()
    latest_run_dir_norm = str(Path(latest_run_dir).resolve()) if latest_run_dir else ""
    for status_file in sorted(RUNS_ROOT.rglob("status.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        run_dir = status_file.parent
        if not _is_production_run_dir(run_dir):
            continue
        data = _read_json(status_file, {})
        if not isinstance(data, dict) or not _is_background_run_status(data):
            continue
        stages = data.get("stages") or {}
        quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
        run_dir_str = str(run_dir.resolve())
        waiting = data.get("waiting")
        row = {
            "run_id": data.get("run_id", run_dir.name),
            "run_dir": run_dir_str,
            "started_at": data.get("started_at", ""),
            "finished_at": data.get("finished_at", ""),
            "current_stage": data.get("current_stage"),
            "ok": data.get("ok", False),
            "updated_at": data.get("updated_at", ""),
            "status_seq": int(data.get("status_seq") or 0),
            "waiting": waiting,
            "stages_done": [s for s, v in stages.items() if isinstance(v, dict) and v.get("completed")],
            "hipporag_processed": (stages.get("hipporag") or {}).get("processed", 0),
            "hipporag_remaining": (stages.get("hipporag") or {}).get("remaining_after"),
            "topic": (stages.get("gather") or {}).get("topic", ""),
            "quality_overall": quality.get("overall_score"),
            "quality_gate_pass": quality.get("gate_pass"),
            "stage_scores": quality.get("stage_scores") if isinstance(quality.get("stage_scores"), dict) else {},
            "quality": quality,
            "is_current": bool(latest_run_dir_norm) and run_dir_str == latest_run_dir_norm,
        }
        row["recommended_next_action"] = _recommended_next_action(data)
        row["actionable"] = bool(row["is_current"] and not row["ok"] and str(row.get("current_stage") or "") != "done")
        runs.append(row)
        if len(runs) >= max(1, int(limit)):
            break
    current_run = next((item for item in runs if item.get("is_current")), None)
    archive_runs = [item for item in runs if not item.get("is_current")]
    day_keys: list[str] = []
    for row in archive_runs:
        day = str(row.get("started_at") or row.get("finished_at") or "")[:10]
        if len(day) == 10 and day not in day_keys:
            day_keys.append(day)
    return {
        "runs": runs,
        "current_run": current_run,
        "archive_runs": archive_runs,
        "current_run_health": {
            "ok": bool((current_run or {}).get("ok")) if current_run else False,
            "stage": str((current_run or {}).get("current_stage") or "") if current_run else "",
            "quality_overall": (current_run or {}).get("quality_overall") if current_run else None,
            "recommended_next_action": str((current_run or {}).get("recommended_next_action") or "run_step_now"),
        },
        "history_cursor": {
            "available_days": day_keys,
            "newest_day": (day_keys[0] if day_keys else ""),
            "current_day": (str((current_run or {}).get("started_at") or "")[:10] if current_run else ""),
        },
    }


def scorecard(window: int = 7) -> Dict[str, Any]:
    latest = latest_status()
    quality = dict((latest or {}).get("quality") or {})
    rolling = dict(quality.get("rolling") or {})
    if not rolling:
        rolling = _rolling_quality_snapshot(latest, window=max(1, int(window)))
    return {
        "window": max(1, int(window)),
        "run_id": latest.get("run_id"),
        "current": dict(quality.get("current") or {}),
        "rolling": rolling,
        "confidence_band": dict(quality.get("confidence_band") or {}),
    }


def _nightly_ocr_rag(run_dir: Optional[Path] = None):
    from common.rag_store import AgentRAG

    collection_name = str(os.getenv("APOLLO_NIGHTLY_OCR_RAG_COLLECTION") or "ApolloNightlyOCR").strip() or "ApolloNightlyOCR"
    store_path = _nightly_ocr_rag_dir(run_dir)
    if str(os.getenv("APOLLO_NIGHTLY_OCR_RAG_DISABLE_EMBEDDER", "1")).strip().lower() in {"1", "true", "yes", "on"}:
        os.environ["RAG_DISABLE_EMBEDDER"] = "1"
    try:
        return AgentRAG(collection_name, store_path=str(store_path))
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{exc}"
        if "_type" not in error_text:
            raise
        backup_path = store_path.parent / f"{store_path.name}_corrupt_{_now_utc().strftime('%Y%m%dT%H%M%SZ')}"
        try:
            if store_path.exists():
                if backup_path.exists():
                    shutil.rmtree(backup_path, ignore_errors=True)
                shutil.move(str(store_path), str(backup_path))
            store_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            raise exc
        logging.warning(
            "[nightly] rotated malformed nightly OCR RAG store from %s to %s after %s",
            store_path,
            backup_path,
            error_text,
        )
        return AgentRAG(collection_name, store_path=str(store_path))


def _purge_operational_rag_by_url(url: str) -> Dict[str, Any]:
    from common.rag_store import AgentRAG

    rag = AgentRAG("Apollo")
    items = list((rag.get(where={"url": url}, limit=500) or {}).get("items") or [])
    ids = [str(item.get("id") or "").strip() for item in items if str(item.get("id") or "").strip()]
    deleted = rag.delete(ids=ids) if ids else 0
    return {"deleted": int(deleted), "ids": ids}


def _purge_financial_corpus_by_url(url: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"deleted": 0, "ids": [], "kg_updated": False}
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:
        result["error"] = f"chromadb_import_failed:{type(exc).__name__}:{exc}"
        return result

    rag_dir = _resolved_rag_dir()
    collection_name = os.getenv("RAG_COLLECTION", "apollo_financial")
    try:
        client = chromadb.PersistentClient(path=rag_dir, settings=Settings(allow_reset=False, anonymized_telemetry=False))
        col = client.get_collection(collection_name)
        got = col.get(where={"url": url}, include=["metadatas"])
        ids = [str(doc_id).strip() for doc_id in list(got.get("ids") or []) if str(doc_id).strip()]
        if ids:
            col.delete(ids=ids)
        result["deleted"] = len(ids)
        result["ids"] = ids
    except Exception as exc:
        result["error"] = f"financial_corpus_delete_failed:{type(exc).__name__}:{exc}"
        return result

    try:
        from Apollo.apollo_hipporag.graph_store import FinancialKG

        kg = FinancialKG(os.path.join(rag_dir, "financial_kg.json"))
        if result["ids"] and kg.remove_doc_ids(set(result["ids"])):
            kg.save()
            result["kg_updated"] = True
        result["kg_stats"] = kg.stats()
    except Exception as exc:
        result["kg_error"] = f"financial_kg_cleanup_failed:{type(exc).__name__}:{exc}"
    return result


def _reset_background_run_for_regather(run_dir: Path, status: Dict[str, Any], url: str, purge_result: Dict[str, Any]) -> Dict[str, Any]:
    removed_files: list[str] = []
    for stage_name in _stage_names():
        for path in (_stage_result_path(run_dir, stage_name), _stage_log_path(run_dir, stage_name)):
            if path.exists():
                path.unlink()
                removed_files.append(str(path))
    for extra_path in (run_dir / "02_ocr_extracts.md", run_dir / "summary.md"):
        if extra_path.exists():
            extra_path.unlink()
            removed_files.append(str(extra_path))

    stages = dict(status.get("stages") or {})
    cleared_stages = [name for name in _stage_names() if name in stages]
    for name in cleared_stages:
        stages.pop(name, None)

    history = list(((status.get("maintenance") or {}).get("purges") or []))
    history.append(
        {
            "url": url,
            "ts": _utc_iso(),
            "operational_rag_deleted": int(((purge_result.get("operational_rag") or {}).get("deleted")) or 0),
            "financial_corpus_deleted": int(((purge_result.get("financial_corpus") or {}).get("deleted")) or 0),
        }
    )
    history = history[-6:]

    status["stages"] = stages
    status["ok"] = False
    status["finished_at"] = ""
    status["current_stage"] = "gather"
    status["maintenance"] = {"purges": history}
    status.pop("last_step_at", None)
    status.pop("waiting", None)
    _save_status(run_dir, status)
    return {"cleared_stages": cleared_stages, "removed_files": removed_files}


def _recovery_candidate_urls(status: Dict[str, Any], config: Dict[str, Any]) -> list[str]:
    urls: list[str] = []
    stages = dict(status.get("stages") or {})
    ocr_stage = _stage_result_view(dict(stages.get("ocr") or {}))
    gather_stage = _stage_result_view(dict(stages.get("gather") or {}))
    min_chars = int(config.get("ocr_doc_min_chars") or DEFAULT_OCR_DOC_MIN_CHARS)
    for doc in list(ocr_stage.get("documents") or []):
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url") or "").strip()
        if not url:
            continue
        text_chars = int(doc.get("text_chars") or 0)
        engine = str(doc.get("extract_engine") or "").strip().lower()
        quality_flag = str(doc.get("quality_flag") or "").strip().lower()
        if "scrape" in engine or quality_flag in {"ocr_unverified_scrape_only", "low_chars"} or text_chars < min_chars:
            urls.append(url)
    gather_quality = dict(gather_stage.get("quality") or {})
    for row in list(gather_quality.get("accepted_sources") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("tier") or "").upper() == "C":
            url = str(row.get("url") or "").strip()
            if url:
                urls.append(url)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped[:6]


def purge_background_source(url: str, *, run_id: str = DEFAULT_BACKGROUND_RUN_ID) -> Dict[str, Any]:
    target_url = str(url or "").strip()
    if not target_url:
        raise ValueError("missing_url")

    run_dir = _background_run_dir({"batch_id": run_id})
    run_dir.mkdir(parents=True, exist_ok=True)
    status = _load_status(run_dir)
    if not status.get("run_id"):
        status = {
            "ok": False,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "started_at": _utc_iso(),
            "finished_at": "",
            "estimate": estimate_runtime_breakdown(_pipeline_config({"run_mode": "background", "batch_id": run_id})),
            "stages": {},
            "current_stage": "gather",
            "run_mode": "background",
        }

    _acquire_lock(run_id, run_dir)
    try:
        result: Dict[str, Any] = {
            "ok": True,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "url": target_url,
            "ts": _utc_iso(),
        }
        result["operational_rag"] = _purge_operational_rag_by_url(target_url)
        result["financial_corpus"] = _purge_financial_corpus_by_url(target_url)
        result["reset"] = _reset_background_run_for_regather(run_dir, status, target_url, result)
        return result
    finally:
        _release_lock(run_id)


def _http_self_check() -> Dict[str, Any]:
    port = int(resolve_runtime_port("Apollo", default=5218) or 5218)
    token = str(os.getenv("SKY_LOCAL_TOKEN") or os.getenv("APOLLO_LOCAL_TOKEN") or "").strip()
    headers = {}
    if token:
        headers["X-Apollo-Token"] = token
    resp = requests.post(
        f"http://127.0.0.1:{port}/admin/self_check",
        headers=headers,
        json={"force_weather": False, "fix": False},
        timeout=60,
    )
    return {"status_code": resp.status_code, "payload": (resp.json() if resp.content else {})}


def _local_self_check(run_dir: Path) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    checks["templates"] = {"ok": (_APOLLO_ROOT / "templates" / "index.html").exists()}
    checks["static"] = {"ok": (_APOLLO_ROOT / "static" / "style.css").exists()}
    checks["kg"] = {"ok": Path(_resolved_rag_dir()).joinpath("financial_kg.json").exists()}
    checks["ocr_report"] = {"ok": (run_dir / "02_ocr_ingest.json").exists()}
    checks["pipeline_report"] = {"ok": (run_dir / "pipeline_report.md").exists() or (run_dir / "summary.md").exists()}
    status = _load_status(run_dir)
    stages = dict(status.get("stages") or {})
    gather_stage = _stage_result_view(dict(stages.get("gather") or {}))
    ocr_stage = _stage_result_view(dict(stages.get("ocr") or {}))
    hippo_stage = _stage_result_view(dict(stages.get("hipporag") or {}))
    evidence_ok = bool(gather_stage.get("quality")) and bool(list(ocr_stage.get("documents") or [])) and (
        bool(hippo_stage.get("stats")) or bool(hippo_stage.get("completed"))
    )
    checks["stage_evidence"] = {"ok": evidence_ok}
    config = _read_json(run_dir / "config.json", _pipeline_config({"run_mode": status.get("run_mode") or "background"}))
    expected_quality = _compute_quality(status, config)
    existing_quality = dict(status.get("quality") or {})
    consistency_ok = bool(existing_quality) and int(existing_quality.get("overall_score") or -1) == int(expected_quality.get("overall_score") or -2)
    checks["score_consistency"] = {"ok": consistency_ok}
    overall_ok = all(bool(item.get("ok")) for item in checks.values())
    return {"ok": overall_ok, "mode": "local_fallback", "checks": checks}


def _equity_rss_sources(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    tickers = list(config.get("equity_news_tickers") or DEFAULT_EQUITY_NEWS_TICKERS)
    sources = []
    for ticker in tickers[:10]:
        t = ticker.strip().upper()
        if not t:
            continue
        sources.append({
            "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US",
            "title": f"{t} stock trading news earnings guidance analyst",
            "snippet": f"Latest {t} stock news: earnings, guidance, analyst ratings, price targets, trading setup",
            "why": f"equity news feed for watchlist ticker {t}",
            "pre_approved": True,  # injected by Apollo for specific ticker â€” bypass content scoring gates
        })
    return sources


def _trusted_rescue_queries(config: Dict[str, Any], focus_universe: Dict[str, Any]) -> List[Dict[str, str]]:
    tickers = [str(t).strip().upper() for t in list(config.get("equity_news_tickers") or []) if str(t).strip()]
    if focus_universe:
        focus_tickers = [
            str(row.get("ticker") or "").strip().upper()
            for row in list(focus_universe.get("selected_tickers") or [])
            if isinstance(row, dict) and str(row.get("ticker") or "").strip()
        ]
        tickers = focus_tickers + [t for t in tickers if t not in set(focus_tickers)]
    if not tickers:
        tickers = list(DEFAULT_EQUITY_NEWS_TICKERS)
    queries: List[Dict[str, str]] = []
    for ticker in tickers[:8]:
        queries.extend(
            [
                {"pack": "primary_filing", "query": f"{ticker} SEC 8-K earnings guidance material event"},
                {"pack": "primary_filing", "query": f"{ticker} 10-Q 10-K risk factors revenue margin guidance SEC"},
                {"pack": "trusted_catalyst", "query": f"{ticker} earnings release investor relations guidance transcript"},
            ]
        )
    queries.extend(
        [
            {"pack": "macro_primary", "query": "Federal Reserve interest rates market liquidity equities risk appetite"},
            {"pack": "macro_primary", "query": "Treasury yields equity market volatility sector rotation"},
        ]
    )
    return queries[:24]


def _high_quality_source_strategy(
    config: Dict[str, Any],
    focus_universe: Dict[str, Any],
    *,
    prior_quality: Optional[Dict[str, Any]] = None,
    prior_errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    quality = dict(prior_quality or {})
    errors = [str(err) for err in list(prior_errors or []) if str(err)]
    min_accepted = int(config.get("gather_min_accepted_sources") or DEFAULT_GATHER_MIN_ACCEPTED_SOURCES)
    min_doc_sources = int(config.get("gather_min_doc_sources") or DEFAULT_GATHER_MIN_DOC_SOURCES)
    min_pdf_sources = int(config.get("gather_min_pdf_sources") or DEFAULT_GATHER_MIN_PDF_SOURCES)
    accepted = int(quality.get("accepted_count") or 0)
    doc_sources = int(quality.get("doc_sources") or 0)
    pdf_count = int(quality.get("pdf_count") or quality.get("pdf_sources") or 0)
    trusted_ratio = float(quality.get("trusted_ratio") or 0.0)
    reasons: list[str] = []
    if accepted < min_accepted:
        reasons.append("accepted_source_count_below_gate")
    if doc_sources < min_doc_sources:
        reasons.append("document_source_count_below_gate")
    if pdf_count < min_pdf_sources:
        reasons.append("pdf_source_count_below_gate")
    if trusted_ratio < float(config.get("gather_min_trusted_ratio") or DEFAULT_GATHER_MIN_TRUSTED_RATIO):
        reasons.append("trusted_ratio_below_gate")
    if bool(quality.get("c_only_run")):
        reasons.append("c_tier_only_run")
    if any("rate_limited" in err for err in errors):
        reasons.append("web_rate_limited")
    if any("no_seed_fallback" in err for err in errors):
        reasons.append("seed_fallback_unavailable")
    if any("gather_quality_failed" in err for err in errors):
        reasons.append("strict_quality_gate_failed")
    triggered = bool(reasons)
    focus_tickers = [
        str(row.get("ticker") or "").strip().upper()
        for row in list((focus_universe or {}).get("selected_tickers") or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    ]
    tickers = focus_tickers or [str(t).strip().upper() for t in list(config.get("equity_news_tickers") or DEFAULT_EQUITY_NEWS_TICKERS) if str(t).strip()]
    return {
        "strategy": "authoritative_evidence_mix",
        "triggered": triggered,
        "reasons": sorted(set(reasons)),
        "source_selection_principles": [
            "Prefer primary/regulatory/research authorities over generic market commentary.",
            "Require an evidence mix that can satisfy accepted-source, document-source, PDF-source, and trusted-ratio gates.",
            "Use broad market-structure and margin-risk authorities when ticker-specific news search is rate-limited.",
            "Keep sources public, document-like, finance-relevant, and suitable for OCR/RAG follow-up.",
        ],
        "required_evidence_mix": {
            "min_accepted_sources": min_accepted,
            "min_doc_sources": min_doc_sources,
            "min_pdf_sources": min_pdf_sources,
            "min_trusted_ratio": float(config.get("gather_min_trusted_ratio") or DEFAULT_GATHER_MIN_TRUSTED_RATIO),
        },
        "focus_tickers": tickers[:8],
        "source_packs": [
            "market_risk_primary_pdf",
            "monetary_policy_primary_pdf",
            "regulatory_margin_rule",
            "investor_margin_risk_bulletin",
            "company_filing_discovery_queries",
        ],
    }


def _authoritative_source_candidates(config: Dict[str, Any], focus_universe: Dict[str, Any], strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
    topic = str(config.get("topic") or DEFAULT_TOPIC)
    tickers = [str(t).strip().upper() for t in list(strategy.get("focus_tickers") or []) if str(t).strip()]
    if not tickers:
        tickers = [str(t).strip().upper() for t in list(config.get("equity_news_tickers") or DEFAULT_EQUITY_NEWS_TICKERS) if str(t).strip()]
    ticker_context = " ".join(tickers[:4]) or "equity watchlist"
    topic_context = f"{topic}; watchlist context {ticker_context}; stock trading, day trading, margin, risk, position sizing"
    sources = [
        {
            "pack": "market_risk_primary_pdf",
            "query": "autonomous_authoritative_mix",
            "title": f"Federal Reserve Financial Stability Report - {ticker_context} equity risk context",
            "snippet": f"Primary PDF report for market liquidity, leverage, volatility, and equity risk backdrop. {topic_context}",
            "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-20241122.pdf",
            "is_pdf": True,
            "provider": "apollo_authoritative_strategy",
            "tier": "A",
            "provenance_class": "real_public",
            "why": "Apollo selected a primary Federal Reserve PDF to satisfy trusted document/PDF evidence for broad market risk.",
        },
        {
            "pack": "monetary_policy_primary_pdf",
            "query": "autonomous_authoritative_mix",
            "title": f"Federal Reserve Monetary Policy Report - {ticker_context} rate and liquidity context",
            "snippet": f"Primary PDF report for rates, inflation, liquidity, and equity risk appetite. {topic_context}",
            "url": "https://www.federalreserve.gov/publications/files/20250207_mprfullreport.pdf",
            "is_pdf": True,
            "provider": "apollo_authoritative_strategy",
            "tier": "A",
            "provenance_class": "real_public",
            "why": "Apollo selected a second primary PDF so the gather can prove enough document evidence before OCR/RAG.",
        },
        {
            "pack": "regulatory_margin_rule",
            "query": "autonomous_authoritative_mix",
            "title": f"FINRA Rule 4210 Margin Requirements - {ticker_context} position sizing guardrails",
            "snippet": f"Regulatory rule for margin requirements, pattern day trading, maintenance margin, and risk controls. {topic_context}",
            "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
            "is_pdf": False,
            "provider": "apollo_authoritative_strategy",
            "tier": "A",
            "provenance_class": "real_public",
            "why": "Apollo selected FINRA margin rules to ground risky trade sizing in authoritative constraints.",
        },
        {
            "pack": "investor_margin_risk_bulletin",
            "query": "autonomous_authoritative_mix",
            "title": f"SEC Investor Bulletin: Margin Accounts - {ticker_context} trading risk",
            "snippet": f"SEC bulletin covering margin account risk, leverage, forced sales, and trading risk. {topic_context}",
            "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
            "is_pdf": False,
            "provider": "apollo_authoritative_strategy",
            "tier": "A",
            "provenance_class": "real_public",
            "why": "Apollo selected SEC margin-risk guidance as a document-like trusted source for trade-risk review.",
        },
    ]
    return sources[: max(1, min(int(config.get("max_articles") or DEFAULT_MAX_ARTICLES), 6))]


def _quality_strength(quality: Dict[str, Any]) -> tuple[int, int, int, int, int, float, int]:
    confidence_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    return (
        1 if bool(quality.get("gate_pass")) else 0,
        confidence_rank.get(str(quality.get("source_confidence") or "none"), 0),
        int(quality.get("accepted_count") or 0),
        int(quality.get("doc_sources") or 0),
        int(quality.get("pdf_count") or quality.get("pdf_sources") or 0),
        float(quality.get("trusted_ratio") or 0.0),
        int(quality.get("trusted_count") or 0),
    )


def _remediation_improves_c_only_default(candidate_quality: Dict[str, Any], default_quality: Dict[str, Any]) -> bool:
    if not bool(default_quality.get("c_only_run")):
        return False
    if int(candidate_quality.get("accepted_count") or 0) <= 0:
        return False
    confidence_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    candidate_confidence = confidence_rank.get(str(candidate_quality.get("source_confidence") or "none"), 0)
    default_confidence = confidence_rank.get(str(default_quality.get("source_confidence") or "none"), 0)
    if candidate_confidence > default_confidence:
        return True
    candidate_evidence = (
        int(candidate_quality.get("trusted_count") or 0),
        int(candidate_quality.get("doc_sources") or 0),
        int(candidate_quality.get("pdf_count") or candidate_quality.get("pdf_sources") or 0),
        float(candidate_quality.get("trusted_ratio") or 0.0),
    )
    default_evidence = (
        int(default_quality.get("trusted_count") or 0),
        int(default_quality.get("doc_sources") or 0),
        int(default_quality.get("pdf_count") or default_quality.get("pdf_sources") or 0),
        float(default_quality.get("trusted_ratio") or 0.0),
    )
    return candidate_evidence > default_evidence


def _stage_gather(run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.trading_homework import gather_trading_homework_sources

    effective_llm_timeout = int(config["homework_llm_timeout_sec"])
    if str(config.get("run_mode") or "").strip().lower() == "background":
        background_gather_timeout = int(config.get("background_gather_timeout_sec") or 0)
        if background_gather_timeout > 0:
            # Leave slack for web search, JSON/result writes, and remediation so the
            # inner homework LLM call cannot outlive the background stage budget.
            timeout_slack = 60 if background_gather_timeout >= 180 else max(15, background_gather_timeout // 4)
            effective_llm_timeout = max(
                30,
                min(effective_llm_timeout, max(30, background_gather_timeout - timeout_slack)),
            )
    os.environ["APOLLO_HOMEWORK_LLM_TIMEOUT_SECS"] = str(effective_llm_timeout)
    if bool(config.get("focus_universe_enabled")) and bool(config.get("focus_universe_gb10_only")):
        os.environ["APOLLO_HOMEWORK_LLM_BASE_URL"] = str(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
        os.environ["APOLLO_HOMEWORK_LLM_MODEL"] = str(config.get("hipporag_llm_model") or DEFAULT_HIPPORAG_LLM_MODEL)
    focus_universe = _select_focus_universe(config) if bool(config.get("focus_universe_enabled")) else {}
    explicit_sources = list(config.get("sources") or [])
    if not explicit_sources:
        explicit_sources = _equity_rss_sources(config)
    # When all sources are pre_approved equity RSS feeds, relax aggregate quality thresholds.
    # avg_score/trusted_ratio/doc_sources/pdf_sources are calibrated for PDF research, not RSS feeds.
    _equity_rss_quality_override: Optional[Dict[str, Any]] = None
    if explicit_sources and all(bool(s.get("pre_approved")) for s in explicit_sources):
        _equity_rss_quality_override = {
            "min_avg_score": 35.0,
            "min_trusted_ratio": 0.0,
            "min_doc_sources": 0,
            "min_pdf_sources": 0,
            "min_pdf_count": 0,
        }
    base_payload = {
        "topic": config["topic"],
        "batch_id": config["batch_id"],
        "max_articles": config["max_articles"],
        "allow_web": config["allow_web"],
        "allow_inbox": config["allow_inbox"],
        "allow_seed_fallback": bool(config.get("gather_allow_seed_fallback", True)),
        "sources": explicit_sources,
        "queries": _combine_focus_queries(config, focus_universe),
    }
    remediation_summaries: list[Dict[str, Any]] = []

    def _run_attempt(
        *,
        attempt_name: str,
        web_cfg: Dict[str, Any],
        allowed_tiers: set[str],
        quality_override: Optional[Dict[str, Any]] = None,
        sources_override: Optional[List[Dict[str, Any]]] = None,
        queries_override: Optional[List[Dict[str, str]]] = None,
        source_strategy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(base_payload)
        payload["web"] = web_cfg
        if sources_override is not None:
            payload["sources"] = sources_override
        if queries_override is not None:
            payload["queries"] = queries_override
        if source_strategy:
            payload["autonomous_quality_strategy"] = source_strategy
        row = gather_trading_homework_sources(payload)
        if sources_override:
            existing_candidate_urls = {str(item.get("url") or "").strip() for item in list(row.get("candidates") or []) if isinstance(item, dict)}
            row_candidates = list(row.get("candidates") or [])
            for source_item in sources_override:
                source_url = str(source_item.get("url") or "").strip()
                if source_url and source_url not in existing_candidate_urls:
                    row_candidates.append(dict(source_item))
                    existing_candidate_urls.add(source_url)
            row["candidates"] = row_candidates
        # gather_trading_homework_sources strips extra fields like pre_approved from returned
        # sources/candidates. Re-apply the flag before scoring so the bypass path fires.
        payload_sources = [item for item in list(payload.get("sources") or []) if isinstance(item, dict)]
        _pa_urls = {str(s.get("url") or "").strip() for s in payload_sources if s.get("pre_approved")}
        if _pa_urls:
            for _key in ("sources", "candidates"):
                for _item in list(row.get(_key) or []):
                    if str(_item.get("url") or "").strip() in _pa_urls:
                        _item["pre_approved"] = True
        gated = _apply_gather_quality_gate(row, config, allowed_tiers=allowed_tiers, quality_override=quality_override)
        gated["attempt_name"] = attempt_name
        gated["attempt_web"] = web_cfg
        gated["attempt_allowed_tiers"] = sorted(allowed_tiers)
        if source_strategy:
            gated["autonomous_quality_strategy"] = source_strategy
            if isinstance(gated.get("quality"), dict):
                gated["quality"]["autonomous_quality_strategy"] = source_strategy
        return gated

    default_web = {
        "mode": "finance_research",
        "provider": "auto",
        "provider_fallbacks": list(config.get("gather_web_provider_fallbacks") or []),
        "rate_limit_retries": int(config.get("gather_web_rate_limit_retries") or 0),
        "rate_limit_backoff_sec": float(config.get("gather_web_rate_limit_backoff_sec") or 0.0),
        "max_results": 10,
        "timeout": 14,
        "freshness_days": 730,
        "domains_deny": _merged_gather_domain_denylist(),
    }
    result = _run_attempt(attempt_name="default", web_cfg=default_web, allowed_tiers={"A", "B", "C"}, quality_override=_equity_rss_quality_override)
    remediation_summaries.append(
        {
            "attempt": "default",
            "ok": bool(result.get("ok")),
            "accepted": int(((result.get("quality") or {}).get("accepted_count")) or 0),
            "avg_score": float(((result.get("quality") or {}).get("avg_score")) or 0.0),
            "source_confidence": str(((result.get("quality") or {}).get("source_confidence")) or "none"),
        }
    )

    allow_remediation = (
        bool(config.get("allow_web", True))
        and int(config.get("gather_remediation_max_passes") or DEFAULT_GATHER_REMEDIATION_MAX_PASSES) > 0
    )
    default_quality = dict(result.get("quality") or {})
    c_only_default = bool(default_quality.get("c_only_run")) or (
        int(default_quality.get("accepted_count") or 0) > 0
        and int(default_quality.get("trusted_count") or 0) == 0
    )
    should_rescue = (not result.get("ok")) or c_only_default
    if allow_remediation and should_rescue:
        rescue_queries = _trusted_rescue_queries(config, focus_universe)
        tier_ab_domains = sorted({*_GATHER_DOMAIN_TIER_A, *_GATHER_DOMAIN_TIER_B})
        pass1 = _run_attempt(
            attempt_name="remediation_strict_ab",
            web_cfg={
                "mode": "finance_research",
                "provider": "auto",
                "provider_fallbacks": list(config.get("gather_web_provider_fallbacks") or []),
                "rate_limit_retries": int(config.get("gather_web_rate_limit_retries") or 0),
                "rate_limit_backoff_sec": float(config.get("gather_web_rate_limit_backoff_sec") or 0.0),
                "max_results": 12,
                "timeout": 16,
                "freshness_days": 3650,
                "domains_allow": tier_ab_domains,
                "domains_deny": sorted({*_merged_gather_domain_denylist(), "feeds.finance.yahoo.com"}),
            },
            allowed_tiers={"A", "B"},
            sources_override=[],
            queries_override=rescue_queries,
        )
        remediation_summaries.append(
            {
                "attempt": "remediation_strict_ab",
                "ok": bool(pass1.get("ok")),
                "accepted": int(((pass1.get("quality") or {}).get("accepted_count")) or 0),
                "avg_score": float(((pass1.get("quality") or {}).get("avg_score")) or 0.0),
                "source_confidence": str(((pass1.get("quality") or {}).get("source_confidence")) or "none"),
            }
        )
        pass1_quality = dict(pass1.get("quality") or {})
        if (
            pass1.get("ok")
            or _quality_strength(pass1_quality) > _quality_strength(default_quality)
            or _remediation_improves_c_only_default(pass1_quality, default_quality)
        ):
            result = pass1
        strategy = _high_quality_source_strategy(
            config,
            focus_universe,
            prior_quality=dict((pass1.get("quality") or result.get("quality") or default_quality) or {}),
            prior_errors=list(result.get("errors") or []) + list(pass1.get("errors") or []),
        )
        if not result.get("ok") and bool(strategy.get("triggered")):
            authoritative_sources = _authoritative_source_candidates(config, focus_universe, strategy)
            pass_auth = _run_attempt(
                attempt_name="remediation_authoritative_mix",
                web_cfg={
                    "mode": "authoritative_evidence_mix",
                    "provider": "local_source_strategy",
                    "provider_fallbacks": [],
                    "rate_limit_retries": 0,
                    "rate_limit_backoff_sec": 0.0,
                    "max_results": 4,
                    "timeout": 4,
                    "freshness_days": 3650,
                    "domains_allow": sorted({*_GATHER_DOMAIN_TIER_A, *_GATHER_DOMAIN_TIER_B}),
                    "domains_deny": sorted(_merged_gather_domain_denylist()),
                },
                allowed_tiers={"A", "B"},
                sources_override=authoritative_sources,
                queries_override=[],
                source_strategy=strategy,
            )
            remediation_summaries.append(
                {
                    "attempt": "remediation_authoritative_mix",
                    "ok": bool(pass_auth.get("ok")),
                    "accepted": int(((pass_auth.get("quality") or {}).get("accepted_count")) or 0),
                    "avg_score": float(((pass_auth.get("quality") or {}).get("avg_score")) or 0.0),
                    "source_confidence": str(((pass_auth.get("quality") or {}).get("source_confidence")) or "none"),
                    "strategy_reasons": list(strategy.get("reasons") or []),
                }
            )
            pass_auth_quality = dict(pass_auth.get("quality") or {})
            if (
                pass_auth.get("ok")
                or _quality_strength(pass_auth_quality) > _quality_strength(dict(result.get("quality") or {}))
                or _remediation_improves_c_only_default(pass_auth_quality, default_quality)
            ):
                result = pass_auth
        if (not result.get("ok")) and int(config.get("gather_remediation_max_passes") or DEFAULT_GATHER_REMEDIATION_MAX_PASSES) > 1:
            tier_abc_domains = sorted({*_GATHER_DOMAIN_TIER_A, *_GATHER_DOMAIN_TIER_B, *_GATHER_DOMAIN_TIER_C})
            pass2 = _run_attempt(
                attempt_name="remediation_allow_c",
                web_cfg={
                    "mode": "finance_research",
                    "provider": "auto",
                    "provider_fallbacks": list(config.get("gather_web_provider_fallbacks") or []),
                    "rate_limit_retries": int(config.get("gather_web_rate_limit_retries") or 0),
                    "rate_limit_backoff_sec": float(config.get("gather_web_rate_limit_backoff_sec") or 0.0),
                    "max_results": 12,
                    "timeout": 16,
                    "freshness_days": 3650,
                    "domains_allow": tier_abc_domains,
                    "domains_deny": sorted({*_merged_gather_domain_denylist(), "feeds.finance.yahoo.com"}),
                },
                allowed_tiers={"A", "B", "C"},
                quality_override={
                    "min_accepted_sources": int(config.get("gather_min_accepted_sources") or DEFAULT_GATHER_MIN_ACCEPTED_SOURCES),
                    "min_avg_score": float(config.get("gather_min_avg_score") or DEFAULT_GATHER_MIN_AVG_SCORE),
                    "min_trusted_ratio": float(config.get("gather_min_trusted_ratio") or DEFAULT_GATHER_MIN_TRUSTED_RATIO),
                    "min_doc_sources": int(config.get("gather_min_doc_sources") or DEFAULT_GATHER_MIN_DOC_SOURCES),
                    "min_pdf_sources": int(config.get("gather_min_pdf_sources") or DEFAULT_GATHER_MIN_PDF_SOURCES),
                },
                sources_override=[],
                queries_override=rescue_queries,
            )
            remediation_summaries.append(
                {
                    "attempt": "remediation_allow_c",
                    "ok": bool(pass2.get("ok")),
                    "accepted": int(((pass2.get("quality") or {}).get("accepted_count")) or 0),
                    "avg_score": float(((pass2.get("quality") or {}).get("avg_score")) or 0.0),
                    "source_confidence": str(((pass2.get("quality") or {}).get("source_confidence")) or "none"),
                }
            )
            pass2_quality = dict(pass2.get("quality") or {})
            if (
                pass2.get("ok")
                or _quality_strength(pass2_quality) > _quality_strength(dict(result.get("quality") or {}))
                or _remediation_improves_c_only_default(pass2_quality, default_quality)
            ):
                result = pass2

    result["remediation_attempts"] = remediation_summaries
    result["focus_universe"] = focus_universe
    if isinstance(result.get("quality"), dict):
        result["quality"]["remediation_attempts"] = remediation_summaries
        if focus_universe:
            result["quality"]["focus_universe_selected_theme"] = dict(focus_universe.get("selected_theme") or {})
    if focus_universe:
        _persist_focus_universe_selection(run_dir, focus_universe)
    _write_json(_stage_result_path(run_dir, "gather"), result)
    print(
        json.dumps(
            {
                "sources": len(result.get("sources") or []),
                "candidates": len(result.get("candidates") or []),
                "focus_universe": dict(result.get("focus_universe") or {}),
                "gather_quality": result.get("quality") or {},
                "errors": result.get("errors") or [],
                "remediation_attempts": remediation_summaries,
            },
            indent=2,
        )
    )
    return result


def _process_ocr_source(
    item: Dict[str, Any],
    *,
    topic: str,
    config: Dict[str, Any],
    rag_store_inst: Any,
    readiness: Optional[Dict[str, Any]] = None,
    seen_fingerprints: Optional[set[str]] = None,
) -> Dict[str, Any]:
    from Apollo.trading_homework import (
        _download,
        _downloads_dir,
        _looks_like_pdf_url,
        _rag,
        _scrape_url,
        _truncate,
    )
    _ = _rag  # keeps import grouping stable for local helper use
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or "").strip() or Path(url).name or "(untitled)"
    why = str(item.get("why") or "").strip()
    ocr_text = ""
    ocr_markdown = ""
    ocr_meta: dict[str, Any] = {}
    local_path: Optional[Path] = None
    errors: list[str] = []
    readiness = dict(readiness or {})
    visual_ready = bool(readiness.get("visual_ready") or readiness.get("ready"))
    visual_fail_reason = str(readiness.get("visual_fail_reason") or readiness.get("fail_reason") or "")
    try:
        candidate_path = Path(url)
        if candidate_path.exists():
            local_path = candidate_path
    except Exception:
        local_path = None
    seed_fallback_source = _is_seed_fallback_source(url, local_path)

    try:
        if local_path is not None:
            extracted = _extract_document_cascade(local_path, topic=topic, config=config)
            ocr_meta = dict(extracted.get("meta") or {})
            ocr_text = str(extracted.get("text") or "").strip()
            ocr_markdown = str(extracted.get("markdown") or extracted.get("text") or "").strip()
        elif _looks_like_pdf_url(url) and url.lower().startswith(("http://", "https://")):
            ok, path, err = _download(url, _downloads_dir(), timeout=25)
            if not ok:
                errors.append(f"download_failed:{err}:{url}")
            else:
                extracted = _extract_document_cascade(path, topic=topic, config=config)
                ocr_meta = dict(extracted.get("meta") or {})
                ocr_text = str(extracted.get("text") or "").strip()
                ocr_markdown = str(extracted.get("markdown") or extracted.get("text") or "").strip()
        else:
            ok, scraped, err = _scrape_url(url, timeout=12, max_chars=int(config["ocr_max_chars"]))
            if not ok:
                errors.append(f"scrape_failed:{err}:{url}")
            if _is_equity_news_feed_url(url):
                ticker = _ticker_from_equity_news_feed_url(url)
                articles = _parse_equity_news_feed_articles(scraped, ticker=ticker, feed_url=url, limit=12)
                article_texts = [
                    _format_equity_news_article(article, topic=topic, batch_id=str(config.get("batch_id") or ""))
                    for article in articles
                ]
                ocr_text = "\n\n---\n\n".join(article_texts).strip()
                ocr_markdown = ocr_text
                news_semantic_hits = max(_semantic_section_hits(ocr_text), 4 if articles else 0)
                news_structure = max(0.62, _structure_score(ocr_text, news_semantic_hits))
                news_numeric = max(0.72, _numeric_fidelity_score(ocr_text))
                ocr_meta = {
                    "ok": bool(ocr_text),
                    "engine": "news_feed_scrape",
                    "pages": 0,
                    "confidence": None,
                    "fallback_used": False,
                    "quality_score": max(82.0, _extract_quality_score(ocr_text, None, config)),
                    "attempts": [{"engine": "news_feed_scrape", "ok": bool(scraped), "pages": 0, "confidence": None, "chars": len(scraped), "reason": "structured_news_feed", "error": err if not ok else ""}],
                    "route_mode": "news_feed",
                    "doc_class": "news_feed",
                    "engine_chain": ["html_scrape", "news_feed_parse"],
                    "structure_score": news_structure,
                    "numeric_fidelity_score": news_numeric,
                    "table_rows": 0,
                    "blocks": [{"kind": "article", "index": idx + 1} for idx, _article in enumerate(articles)],
                    "tables": [],
                    "reading_order": [{"index": idx + 1} for idx, _article in enumerate(articles)],
                    "bbox": [],
                    "extraction_mode": "news_feed",
                    "news_articles": articles,
                    "benchmark_proxy_scores": {
                        "structure_lane": int(round(news_structure * 100.0)),
                        "correctness_lane": int(round(news_numeric * 100.0)),
                    },
                }
            else:
                ocr_text = scraped
                ocr_markdown = scraped
                ocr_meta = {
                    "ok": bool(scraped),
                    "engine": "html_scrape",
                    "pages": 0,
                    "confidence": None,
                    "fallback_used": False,
                    "quality_score": _extract_quality_score(scraped, None, config),
                    "attempts": [{"engine": "html_scrape", "ok": bool(scraped), "pages": 0, "confidence": None, "chars": len(scraped), "reason": "html_scrape", "error": err if not ok else ""}],
                    "route_mode": "scrape_only",
                    "doc_class": "web_page",
                    "engine_chain": ["html_scrape"],
                    "structure_score": _structure_score(scraped, _semantic_section_hits(scraped)),
                    "numeric_fidelity_score": _numeric_fidelity_score(scraped),
                    "table_rows": _table_like_rows(scraped),
                    "blocks": [],
                    "tables": [],
                    "reading_order": [],
                    "bbox": [],
                    "extraction_mode": "html_scrape",
                    "benchmark_proxy_scores": {
                        "structure_lane": int(round(_structure_score(scraped, _semantic_section_hits(scraped)) * 100.0)),
                        "correctness_lane": int(round(_numeric_fidelity_score(scraped) * 100.0)),
                    },
                }
    except Exception as exc:
        errors.append(f"ocr_stage_failed:{type(exc).__name__}:{url}")
        ocr_meta = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    content_for_quality = ocr_markdown or ocr_text
    semantic_hits = _semantic_section_hits(content_for_quality)
    if str(ocr_meta.get("extraction_mode") or "") == "news_feed":
        semantic_hits = max(semantic_hits, 4)
    table_like = _table_like_content(content_for_quality)
    structure_score = float(ocr_meta.get("structure_score") or _structure_score(content_for_quality, semantic_hits))
    numeric_fidelity = float(ocr_meta.get("numeric_fidelity_score") or _numeric_fidelity_score(content_for_quality))
    table_rows = int(ocr_meta.get("table_rows") or _table_like_rows(content_for_quality))
    ocr_blocks = list(ocr_meta.get("blocks") or [])
    ocr_tables = list(ocr_meta.get("tables") or [])
    reading_order = list(ocr_meta.get("reading_order") or [])
    bbox = list(ocr_meta.get("bbox") or [])
    source_doc_class = _source_doc_class(url=url, local_path=local_path)
    extract_attempts = list(ocr_meta.get("attempts") or [])
    native_attempt = next((attempt for attempt in extract_attempts if str(attempt.get("engine") or "") == "native_pdf_text"), {})
    successful_visual_attempt = any(
        bool(attempt.get("ok")) and _extraction_mode_for_engine(str(attempt.get("engine") or "")) == "visual_ocr"
        for attempt in extract_attempts
    )
    visual_ocr_required = _doc_needs_visual_ocr(
        source_doc_class=source_doc_class,
        native_attempt=native_attempt,
        successful_visual_attempt=successful_visual_attempt,
    )
    extraction_mode = str(ocr_meta.get("extraction_mode") or _extraction_mode_for_engine(str(ocr_meta.get("engine") or "")))
    quality_flag = _ocr_doc_quality_flag(len(content_for_quality), ocr_meta.get("confidence"), str(ocr_meta.get("engine") or ""), config)
    if quality_flag == "html_scrape_only":
        quality_flag = "ocr_unverified_scrape_only"
    if visual_ocr_required and not visual_ready and not successful_visual_attempt:
        quality_flag = "visual_ocr_required_unavailable"
        errors.append(f"visual_ocr_backend_not_ready:{visual_fail_reason or 'unknown'}:{url}")
    doc_row = {
        "url": url,
        "title": title,
        "why": why,
        "source_provider": str(item.get("provider") or ("seed_fallback" if seed_fallback_source else "")).strip(),
        "seed_fallback_source": bool(seed_fallback_source),
        "extract_engine": ocr_meta.get("engine"),
        "extraction_mode": extraction_mode,
        "source_doc_class": source_doc_class,
        "extract_reason": ocr_meta.get("reason"),
        "extract_pages": ocr_meta.get("pages"),
        "extract_confidence": ocr_meta.get("confidence"),
        "text_chars": len(content_for_quality),
        "ok": bool(ocr_text),
        "local_path": str(local_path) if local_path is not None else "",
        "error": str(ocr_meta.get("error") or ""),
        "fallback_used": bool(ocr_meta.get("fallback_used")),
        "extract_attempts": extract_attempts,
        "quality_score": float(ocr_meta.get("quality_score") or _extract_quality_score(content_for_quality, ocr_meta.get("confidence"), config)),
        "quality_flag": quality_flag,
        "semantic_section_hits": int(semantic_hits),
        "table_like_content": bool(table_like),
        "table_rows": int(table_rows),
        "structure_score": float(structure_score),
        "numeric_fidelity_score": float(numeric_fidelity),
        "route_mode": str(ocr_meta.get("route_mode") or config.get("ocr_route_mode") or DEFAULT_OCR_ROUTE_MODE),
        "doc_class": str(ocr_meta.get("doc_class") or ""),
        "engine_chain": list(ocr_meta.get("engine_chain") or []),
        "blocks_count": len(ocr_blocks),
        "tables_count": len(ocr_tables),
        "reading_order_count": len(reading_order),
        "bbox_count": len(bbox),
        "benchmark_proxy_scores": dict(ocr_meta.get("benchmark_proxy_scores") or {}),
        "visual_ocr_required": bool(visual_ocr_required),
        "visual_ocr_available": bool(visual_ready),
        "visual_ocr_fail_reason": visual_fail_reason if visual_ocr_required and not visual_ready else "",
        "finance_consistency_score": float(ocr_meta.get("finance_consistency_score") or 0.0),
        "finbert_label": str(ocr_meta.get("finbert_label") or ""),
        "preview": _truncate(ocr_text, 2200) if ocr_text else "(no extract)",
    }
    news_articles = list(ocr_meta.get("news_articles") or [])
    if news_articles:
        doc_row["news_feed"] = True
        doc_row["ticker"] = _ticker_from_equity_news_feed_url(url)
        doc_row["article_count"] = len(news_articles)
        doc_row["news_articles"] = news_articles
    if ocr_markdown:
        doc_row["markdown_preview"] = _truncate(ocr_markdown, 2200)
    scan = _ocr_quality_scan_decision(doc_row, content_for_quality, config, seen_fingerprints=seen_fingerprints)
    doc_row["quality_scan"] = dict(scan)
    doc_row["quality_scan_keep"] = bool(scan.get("keep"))
    doc_row["quality_scan_reason"] = str(scan.get("reason") or "")
    doc_row["quality_scan_fingerprint"] = str(scan.get("fingerprint") or "")
    doc_row["topic_anchor_profile"] = str(scan.get("topic_anchor_profile") or "")
    doc_row["document_lane"] = (
        "supporting_context"
        if source_doc_class == "web_page" and extraction_mode == "html_scrape"
        else ("macro_context" if str(scan.get("topic_anchor_profile") or "") == "macro_market_context" else "ocr_evidence")
    )
    if not bool(scan.get("keep")):
        doc_row["ingest_skipped_reason"] = f"quality_scan_rejected:{doc_row['quality_scan_reason']}"
        errors.append(f"ocr_quality_scan_rejected:{doc_row['quality_scan_reason']}:{url}")
    if news_articles and bool(scan.get("keep")):
        article_ids: list[str] = []
        for article in news_articles:
            try:
                article_text = _format_equity_news_article(article, topic=topic, batch_id=str(config.get("batch_id") or ""))
                doc_id = rag_store_inst.remember(
                    text=article_text,
                    source="nightly_news_feed",
                    kind="news_feed",
                    priority=0.78,
                    tags=["nightly", "news_feed", "trading", "apollo", str(article.get("ticker") or "").strip().upper()],
                    extra={
                        "batch_id": config["batch_id"],
                        "url": str(article.get("url") or ""),
                        "feed_url": url,
                        "title": str(article.get("headline") or ""),
                        "topic": topic,
                        "ticker": str(article.get("ticker") or ""),
                        "published_at": str(article.get("published_at") or ""),
                    },
                )
                article_ids.append(str(doc_id))
            except Exception as exc:
                errors.append(f"news_feed_rag_ingest_failed:{type(exc).__name__}:{url}")
        if article_ids:
            doc_row["ingested_id"] = article_ids[0]
            doc_row["ingested_ids"] = article_ids
            doc_row["ingested_article_ids"] = article_ids
        financial_ingest = _upsert_financial_news_articles(
            news_articles,
            topic=topic,
            batch_id=str(config.get("batch_id") or ""),
        )
        doc_row["financial_corpus_ingest"] = financial_ingest
        if financial_ingest.get("ids"):
            doc_row["financial_corpus_ids"] = list(financial_ingest.get("ids") or [])
        if not financial_ingest.get("ok"):
            errors.append(f"financial_news_corpus_ingest_failed:{financial_ingest.get('error') or 'unknown'}:{url}")
    elif ocr_text and bool(scan.get("keep")):
        try:
            doc_id = rag_store_inst.remember(
                text="\n".join(
                    [
                        f"[NightlyOCR] {title}",
                        f"URL: {url}",
                        "",
                        _truncate(ocr_markdown or ocr_text, 12000),
                    ]
                ).strip(),
                source="nightly_ocr",
                kind="trading_research_raw",
                priority=0.7,
                tags=["nightly", "ocr", "trading", "apollo"],
                extra={"batch_id": config["batch_id"], "url": url, "title": title, "topic": topic},
            )
            doc_row["ingested_id"] = doc_id
        except Exception as exc:
            errors.append(f"rag_ingest_failed:{type(exc).__name__}:{url}")
            doc_row["ingest_error"] = f"{type(exc).__name__}:{exc}"
    return {"document": doc_row, "errors": errors}


def _write_ocr_report(run_dir: Path, config: Dict[str, Any], result: Dict[str, Any]) -> None:
    topic = str(config.get("topic") or DEFAULT_TOPIC)
    readiness = dict(result.get("readiness") or {})
    doc_blocks = []
    for doc_row in list(result.get("documents") or []):
        doc_blocks.append(
            "\n".join(
                [
                    f"## {doc_row.get('title') or '(untitled)'}",
                    f"- URL: {doc_row.get('url') or ''}",
                    f"- Why: {doc_row.get('why') or ''}" if doc_row.get("why") else "",
                    f"- Extract: engine={doc_row.get('extract_engine')} pages={doc_row.get('extract_pages')} chars={doc_row.get('text_chars')} conf={doc_row.get('extract_confidence')}",
                    f"- Extraction mode: mode={doc_row.get('extraction_mode')} source_class={doc_row.get('source_doc_class')} visual_required={doc_row.get('visual_ocr_required')} visual_available={doc_row.get('visual_ocr_available')}",
                    f"- Document lane: lane={doc_row.get('document_lane')} anchor_profile={doc_row.get('topic_anchor_profile')}",
                    f"- Policy: route={doc_row.get('route_mode')} class={doc_row.get('doc_class')} chain={','.join(list(doc_row.get('engine_chain') or []))}",
                    f"- Quality: structure={doc_row.get('structure_score')} numeric={doc_row.get('numeric_fidelity_score')} semantic_hits={doc_row.get('semantic_section_hits')} table_rows={doc_row.get('table_rows')}",
                    f"- Quality scan: keep={doc_row.get('quality_scan_keep')} reason={doc_row.get('quality_scan_reason')}",
                    f"- Ingested ID: {doc_row.get('ingested_id', '')}" if doc_row.get("ingested_id") else "",
                    f"- Error: {doc_row.get('error') or ''}" if doc_row.get("error") else "",
                    "",
                    str(doc_row.get("preview") or "(no extract)"),
                    "",
                ]
            ).strip()
        )
    report_path = run_dir / "02_ocr_extracts.md"
    _write_text(
        report_path,
        "\n".join(
            [
                f"# Apollo Nightly OCR Extracts ({config['batch_id']})",
                "",
                f"- topic: `{topic}`",
                f"- sources: `{len(result.get('documents') or [])}`",
                f"- ingested: `{len(result.get('ingested_ids') or [])}`",
                f"- completed: `{result.get('completed')}`",
                f"- backend_ready: `{readiness.get('ready')}`",
                f"- backend_mode: `{readiness.get('mode')}`",
                f"- backend_fail_reason: `{readiness.get('fail_reason')}`",
                f"- failure_class: `{(result.get('quality') or {}).get('failure_class') or ''}`",
                f"- document_lane: `{(result.get('quality') or {}).get('document_lane') or ''}`",
                f"- topic_anchor_profile: `{(result.get('quality') or {}).get('topic_anchor_profile') or ''}`",
                "",
                "## Documents",
                "\n\n".join(doc_blocks) if doc_blocks else "(none)",
                "",
                "## Errors",
                "\n".join(f"- {err}" for err in (result.get("errors") or [])) if (result.get("errors") or []) else "(none)",
                "",
            ]
        ).strip()
        + "\n",
    )
    result["report_path"] = str(report_path)


def _init_ocr_result(config: Dict[str, Any], sources: list[dict[str, Any]]) -> Dict[str, Any]:
    return {
        "ok": False,
        "completed": False,
        "topic": str(config.get("topic") or DEFAULT_TOPIC),
        "batch_id": config["batch_id"],
        "report_path": "",
        "sources": sources,
        "documents": [],
        "ingested": 0,
        "ingested_ids": [],
        "readiness": {
            "ready": False,
            "mode": "",
            "fail_reason": "readiness_unchecked",
            "qwen_primary": bool(config.get("ocr_qwen_primary")),
        },
        "errors": [],
    }


def _source_url(row: Dict[str, Any]) -> str:
    return str((row or {}).get("url") or "").strip()


def _is_ocr_document_source(row: Dict[str, Any]) -> bool:
    url = _source_url(row)
    if not url:
        return False
    lowered = url.lower()
    if bool(row.get("is_pdf")) or lowered.endswith((".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")):
        return True
    try:
        path = Path(url)
        if path.exists() and path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
            return True
    except Exception:
        pass
    return False


def _dedupe_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _source_url(row)
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(dict(row))
    return deduped


def _gather_accepted_document_sources(gather: Dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    quality = dict((gather or {}).get("quality") or {})
    accepted = [dict(item) for item in list(quality.get("accepted_sources") or []) if isinstance(item, dict)]
    docs = [item for item in accepted if _is_ocr_document_source(item)]
    return _dedupe_source_rows(docs)[: max(0, int(limit))]


def _merge_gather_accepted_documents(
    sources: list[dict[str, Any]],
    gather: Dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged = _dedupe_source_rows(list(sources or []))
    seen = {_source_url(item) for item in merged}
    for item in _gather_accepted_document_sources(gather, limit=limit):
        url = _source_url(item)
        if not url or url in seen:
            continue
        enriched = dict(item)
        enriched["ocr_from_gather_quality"] = True
        merged.append(enriched)
        seen.add(url)
        if len(merged) >= max(1, int(limit)):
            break
    return merged


def _load_ocr_result(run_dir: Path, config: Dict[str, Any], sources: list[dict[str, Any]]) -> Dict[str, Any]:
    result = _read_json(_stage_result_path(run_dir, "ocr"), {})
    if not result:
        result = _init_ocr_result(config, sources)
    result["sources"] = sources
    result.setdefault("documents", [])
    result.setdefault("ingested_ids", [])
    result.setdefault("readiness", {"ready": False, "mode": "", "fail_reason": "readiness_unchecked"})
    result.setdefault("errors", [])
    result.setdefault("completed", False)
    return result


def _ocr_seen_fingerprints_from_documents(documents: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for item in documents:
        if not isinstance(item, dict):
            continue
        fingerprint = str(item.get("quality_scan_fingerprint") or "").strip()
        if fingerprint:
            seen.add(fingerprint)
    return seen


def _evaluate_ocr_quality(result: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    all_docs_raw = [item for item in list(result.get("documents") or []) if isinstance(item, dict)]
    scan_rejected_docs = [item for item in all_docs_raw if item.get("quality_scan_keep") is False]
    all_docs = [item for item in all_docs_raw if item.get("quality_scan_keep") is not False]
    excluded_quality_scan_docs = max(0, len(scan_rejected_docs))
    scan_reject_reasons = [str(item.get("quality_scan_reason") or "") for item in scan_rejected_docs]
    gate_docs = [item for item in all_docs if not bool(item.get("seed_fallback_source"))]
    excluded_seed_fallback_docs = max(0, len(all_docs) - len(gate_docs))
    seed_fallback_only = bool(all_docs) and not gate_docs
    failed_gate_docs = [
        item
        for item in gate_docs
        if item.get("ok") is False or int(item.get("text_chars") or 0) <= 0
    ]
    supplemental_html_gate_docs = [
        item
        for item in gate_docs
        if str(item.get("source_doc_class") or "") == "web_page"
        and str(item.get("extraction_mode") or _extraction_mode_for_engine(str(item.get("extract_engine") or ""))) == "html_scrape"
    ]
    strong_gate_docs = [
        item
        for item in gate_docs
        if item not in failed_gate_docs and item not in supplemental_html_gate_docs
    ]
    if strong_gate_docs:
        docs = strong_gate_docs
    elif seed_fallback_only:
        docs = all_docs
    else:
        docs = []
    excluded_failed_docs = max(0, len(failed_gate_docs))
    excluded_supplemental_html_docs = max(0, len(supplemental_html_gate_docs))
    readiness = dict(result.get("readiness") or {})
    readiness_present = bool(readiness)
    backend_ready = bool(readiness.get("ready")) if readiness_present else True
    backend_mode = str(readiness.get("mode") or ("legacy_unset" if not readiness_present else ""))
    backend_fail_reason = str(readiness.get("fail_reason") or "")
    visual_ready = bool(readiness.get("visual_ready")) if readiness_present else backend_ready
    visual_mode = str(readiness.get("visual_mode") or backend_mode)
    visual_fail_reason = str(readiness.get("visual_fail_reason") or backend_fail_reason)
    native_pdf_supported = bool(readiness.get("native_pdf_supported")) if readiness_present else True
    lane_scorecard = dict(readiness.get("lane_scorecard") or {})
    slo_summary = dict(readiness.get("slo_summary") or {})
    smoke_gate = dict(readiness.get("smoke_gate") or {})
    chars = [int(item.get("text_chars") or 0) for item in docs] or [0]
    semantic_hits_values = [int(item.get("semantic_section_hits") or 0) for item in docs] or [0]
    structure_scores = [float(item.get("structure_score") or 0.0) for item in docs] or [0.0]
    numeric_scores = [float(item.get("numeric_fidelity_score") or 0.0) for item in docs] or [0.0]
    finance_consistency_scores = [float(item.get("finance_consistency_score") or 0.0) for item in docs if item.get("finance_consistency_score") is not None]
    finbert_labels = [str(item.get("finbert_label") or "") for item in docs]
    table_docs = sum(1 for item in docs if bool(item.get("table_like_content")) or int(item.get("table_rows") or 0) > 0 or int(item.get("tables_count") or 0) > 0)
    table_rows_total = sum(int(item.get("table_rows") or 0) for item in docs)
    blocks_total = sum(int(item.get("blocks_count") or 0) for item in docs)
    tables_total = sum(int(item.get("tables_count") or 0) for item in docs)
    reading_order_total = sum(int(item.get("reading_order_count") or 0) for item in docs)
    bbox_total = sum(int(item.get("bbox_count") or 0) for item in docs)
    min_chars = int(min(chars))
    avg_chars = float(sum(chars) / max(1, len(chars)))
    avg_semantic_hits = float(sum(semantic_hits_values) / max(1, len(semantic_hits_values)))
    avg_structure = float(sum(structure_scores) / max(1, len(structure_scores)))
    avg_numeric = float(sum(numeric_scores) / max(1, len(numeric_scores)))
    avg_finance_consistency = float(sum(finance_consistency_scores) / max(1, len(finance_consistency_scores))) if finance_consistency_scores else None
    finbert_negative_count = sum(1 for lbl in finbert_labels if lbl == "negative")
    min_required = int(config.get("ocr_doc_min_chars") or DEFAULT_OCR_DOC_MIN_CHARS)
    avg_required = int(config.get("ocr_avg_min_chars") or DEFAULT_OCR_AVG_MIN_CHARS)
    semantic_required = int(config.get("ocr_min_semantic_hits") or DEFAULT_OCR_MIN_SEMANTIC_HITS)
    require_table_docs = int(config.get("ocr_require_table_docs") or DEFAULT_OCR_REQUIRE_TABLE_DOCS)
    min_non_scrape_ratio = float(config.get("ocr_min_non_scrape_ratio") or DEFAULT_OCR_MIN_NON_SCRAPE_RATIO)
    min_structure_score = float(config.get("ocr_min_structure_score") or DEFAULT_OCR_MIN_STRUCTURE_SCORE)
    min_numeric_fidelity = float(config.get("ocr_min_numeric_fidelity") or DEFAULT_OCR_MIN_NUMERIC_FIDELITY)
    structure_bench_min = int(config.get("ocr_structure_bench_min") or DEFAULT_OCR_STRUCTURE_BENCH_MIN)
    correctness_bench_min = int(config.get("ocr_correctness_bench_min") or DEFAULT_OCR_CORRECTNESS_BENCH_MIN)
    require_finance_structure = bool(config.get("ocr_require_finance_structure", DEFAULT_OCR_REQUIRE_FINANCE_STRUCTURE))
    topic = str(result.get("topic") or config.get("topic") or DEFAULT_TOPIC)
    finance_topic = _is_finance_topic(topic)
    extraction_modes = [str(item.get("extraction_mode") or _extraction_mode_for_engine(str(item.get("extract_engine") or ""))) for item in docs]
    extraction_mode_mix = sorted({mode for mode in extraction_modes if mode})
    document_class_mix = sorted({str(item.get("source_doc_class") or "unknown") for item in docs if str(item.get("source_doc_class") or "unknown").strip()})
    document_lane_mix = sorted({str(item.get("document_lane") or "unknown") for item in docs if str(item.get("document_lane") or "unknown").strip()})
    topic_anchor_profile_mix = sorted({str(item.get("topic_anchor_profile") or "unknown") for item in docs if str(item.get("topic_anchor_profile") or "unknown").strip()})
    non_scrape_count = sum(1 for item in docs if str(item.get("extraction_mode") or _extraction_mode_for_engine(str(item.get("extract_engine") or ""))) != "html_scrape")
    fallback_used_count = sum(1 for item in docs if bool(item.get("fallback_used")))
    non_scrape_ratio = float(non_scrape_count / max(1, len(docs))) if docs else 0.0
    escalation_count = sum(max(0, len(list(item.get("extract_attempts") or [])) - 1) for item in docs)
    engines = sorted({str(item.get("extract_engine") or "").strip() for item in docs if str(item.get("extract_engine") or "").strip()})
    current_policy = str(next((str(item.get("route_mode") or "").strip() for item in docs if str(item.get("route_mode") or "").strip()), config.get("ocr_route_mode") or DEFAULT_OCR_ROUTE_MODE))
    engine_chain_last_doc = list((docs[-1].get("engine_chain") if docs else []) or [])
    visual_required_count = sum(1 for item in docs if bool(item.get("visual_ocr_required")))
    visual_unavailable_count = sum(1 for item in docs if bool(item.get("visual_ocr_required")) and not bool(item.get("visual_ocr_available")))
    avg_required_effective = avg_required
    if docs and len(docs) <= 3 and min_chars >= min_required and non_scrape_count == len(docs):
        avg_required_effective = int(round(avg_required * 0.95))
    native_pdf_bundle = bool(docs) and len(docs) >= 2 and all(
        str(item.get("source_doc_class") or "") == "pdf"
        and str(item.get("extraction_mode") or _extraction_mode_for_engine(str(item.get("extract_engine") or ""))) == "native_pdf_text"
        and bool(item.get("ok"))
        for item in docs
    )
    macro_context_bundle = bool(docs) and all(
        str(item.get("source_doc_class") or "") == "pdf"
        and str(item.get("extraction_mode") or _extraction_mode_for_engine(str(item.get("extract_engine") or ""))) != "html_scrape"
        and str(item.get("document_lane") or "") == "macro_context"
        and str(item.get("topic_anchor_profile") or "") == "macro_market_context"
        and bool(item.get("ok"))
        for item in docs
    )
    news_feed_bundle = bool(docs) and all(
        str(item.get("source_doc_class") or "") == "news_feed"
        and str(item.get("extraction_mode") or _extraction_mode_for_engine(str(item.get("extract_engine") or ""))) == "news_feed"
        and bool(item.get("ok"))
        for item in docs
    )
    semantic_required_effective = semantic_required
    min_structure_score_effective = min_structure_score
    min_numeric_fidelity_effective = min_numeric_fidelity
    structure_bench_min_effective = structure_bench_min
    correctness_bench_min_effective = correctness_bench_min
    if native_pdf_bundle:
        semantic_required_effective = min(semantic_required_effective, 1)
        min_structure_score_effective = min(min_structure_score_effective, 0.12)
        min_numeric_fidelity_effective = min(min_numeric_fidelity_effective, 0.12)
        structure_bench_min_effective = min(structure_bench_min_effective, 40)
        correctness_bench_min_effective = min(correctness_bench_min_effective, 45)
    if macro_context_bundle:
        semantic_required_effective = min(semantic_required_effective, 2)
        min_structure_score_effective = min(min_structure_score_effective, 0.25)
        min_numeric_fidelity_effective = min(min_numeric_fidelity_effective, 0.25)
        structure_bench_min_effective = min(structure_bench_min_effective, 60)
        correctness_bench_min_effective = min(correctness_bench_min_effective, 60)
    if news_feed_bundle:
        semantic_required_effective = min(semantic_required_effective, 2)
        min_structure_score_effective = min(min_structure_score_effective, 0.20)
        min_numeric_fidelity_effective = min(min_numeric_fidelity_effective, 0.20)
        structure_bench_min_effective = min(structure_bench_min_effective, 50)
        correctness_bench_min_effective = min(correctness_bench_min_effective, 55)

    structure_lane = int(
        round(
            max(
                0.0,
                min(
                    100.0,
                    (
                        (avg_structure * 55.0)
                        + (min(1.0, avg_semantic_hits / float(max(1, semantic_required_effective))) * 25.0)
                        + (min(1.0, table_docs / float(max(1, len(docs)))) * 20.0)
                        + (min(1.0, tables_total / float(max(1, len(docs)))) * 5.0)
                        + min(8.0, float(blocks_total) / float(max(1, len(docs) * 4)))
                    )
                    * 1.0,
                ),
            )
        )
    )
    correctness_lane = int(
        round(
            max(
                0.0,
                min(
                    100.0,
                    (
                        (avg_numeric * 55.0)
                        + (min(1.0, non_scrape_ratio / max(0.01, min_non_scrape_ratio)) * 25.0)
                        + (min(1.0, avg_chars / float(max(1, avg_required))) * 20.0)
                        + (2.0 if "native_pdf_text" in extraction_mode_mix and "visual_ocr" in extraction_mode_mix else 0.0)
                        + (min(1.0, avg_semantic_hits / float(max(1, semantic_required_effective * 2))) * 6.0)
                    )
                    * 1.0,
                ),
            )
        )
    )

    scrape_only = bool(docs) and all(mode == "html_scrape" for mode in extraction_modes)
    gate_fail_reasons: list[str] = []
    if not docs and not seed_fallback_only:
        gate_fail_reasons.append("ocr_no_documents")
        if not visual_ready and not native_pdf_supported:
            gate_fail_reasons.append(f"ocr_backend_not_ready:{visual_fail_reason or backend_fail_reason or 'unknown'}")
        if scan_reject_reasons and all(reason.startswith("topic_anchor_retention_failed") for reason in scan_reject_reasons):
            gate_fail_reasons.append("topic_anchor_mismatch")
    if docs and not seed_fallback_only and min_chars < min_required:
        gate_fail_reasons.append(f"min_chars_below_threshold:{min_chars}<{min_required}")
    if docs and not seed_fallback_only and avg_chars < avg_required_effective:
        gate_fail_reasons.append(f"avg_chars_below_threshold:{round(avg_chars,2)}<{avg_required_effective}")
    if docs and not seed_fallback_only and avg_semantic_hits < semantic_required_effective:
        gate_fail_reasons.append(f"semantic_hits_below_threshold:{round(avg_semantic_hits,2)}<{semantic_required_effective}")
    if docs and not seed_fallback_only and non_scrape_ratio < min_non_scrape_ratio:
        gate_fail_reasons.append(f"non_scrape_ratio_below_threshold:{round(non_scrape_ratio,3)}<{round(min_non_scrape_ratio,3)}")
    if docs and not seed_fallback_only and avg_numeric < min_numeric_fidelity_effective:
        gate_fail_reasons.append(f"numeric_fidelity_below_threshold:{round(avg_numeric,3)}<{round(min_numeric_fidelity_effective,3)}")
    if docs and not seed_fallback_only and avg_structure < min_structure_score_effective:
        gate_fail_reasons.append(f"structure_score_below_threshold:{round(avg_structure,3)}<{round(min_structure_score_effective,3)}")
    if docs and not seed_fallback_only and require_table_docs > 0 and table_docs < require_table_docs:
        gate_fail_reasons.append(f"table_docs_below_threshold:{table_docs}<{require_table_docs}")
    if docs and not seed_fallback_only and not news_feed_bundle and require_finance_structure and finance_topic and table_docs <= 0 and avg_semantic_hits < max(2, semantic_required_effective):
        gate_fail_reasons.append("finance_structure_evidence_missing")
    if docs and not seed_fallback_only and finance_topic and avg_finance_consistency is not None and avg_finance_consistency < 0.08:
        gate_fail_reasons.append(f"finance_consistency_score_below_threshold:{round(avg_finance_consistency, 3)}<0.08")
    if docs and not seed_fallback_only and finance_topic and finbert_negative_count >= max(2, len(docs)):
        gate_fail_reasons.append(f"finbert_negative_signal_dominant:{finbert_negative_count}/{len(docs)}")
    if docs and not seed_fallback_only and structure_lane < structure_bench_min_effective:
        gate_fail_reasons.append(f"structure_benchmark_below_threshold:{structure_lane}<{structure_bench_min_effective}")
    if docs and not seed_fallback_only and correctness_lane < correctness_bench_min_effective:
        gate_fail_reasons.append(f"correctness_benchmark_below_threshold:{correctness_lane}<{correctness_bench_min_effective}")
    if scrape_only and not seed_fallback_only:
        gate_fail_reasons.append("ocr_unverified_scrape_only")
    if not docs and excluded_supplemental_html_docs > 0 and not seed_fallback_only:
        gate_fail_reasons.append("ocr_unverified_scrape_only")
    if visual_unavailable_count > 0 and not seed_fallback_only:
        gate_fail_reasons.append(f"visual_ocr_backend_not_ready:{visual_fail_reason or 'unknown'}")
    gate_pass = len(gate_fail_reasons) == 0
    if gate_pass:
        failure_class = "passed"
    elif any(str(reason).startswith(("ocr_backend_not_ready:", "visual_ocr_backend_not_ready:")) for reason in gate_fail_reasons):
        failure_class = "backend_unavailable"
    elif not docs and scan_reject_reasons and all(reason.startswith("topic_anchor_retention_failed") for reason in scan_reject_reasons):
        failure_class = "topic_anchor_mismatch"
    elif not docs:
        failure_class = "no_valid_documents"
    elif scrape_only or excluded_supplemental_html_docs > 0:
        failure_class = "scrape_only"
    else:
        failure_class = "low_depth"

    quality_breakdown = {
        "backend_ready": bool(backend_ready),
        "backend_mode": backend_mode,
        "visual_ocr_ready": bool(visual_ready),
        "visual_ocr_mode": visual_mode,
        "native_pdf_supported": bool(native_pdf_supported),
        "avg_chars": round(avg_chars, 2),
        "min_chars": min_chars,
        "avg_semantic_hits": round(avg_semantic_hits, 2),
        "avg_structure_score": round(avg_structure, 3),
        "avg_numeric_fidelity": round(avg_numeric, 3),
        "non_scrape_ratio": round(non_scrape_ratio, 3),
        "source_documents": len(all_docs),
        "evaluated_documents": len(gate_docs),
        "excluded_quality_scan_docs": excluded_quality_scan_docs,
        "excluded_seed_fallback_docs": excluded_seed_fallback_docs,
        "excluded_failed_docs": excluded_failed_docs,
        "excluded_supplemental_html_docs": excluded_supplemental_html_docs,
        "gate_skipped": bool(seed_fallback_only),
        "gate_skip_reason": "seed_fallback_only" if seed_fallback_only else "",
        "failure_class": failure_class,
        "slo_summary": slo_summary,
        "document_lane": ",".join(document_lane_mix),
        "topic_anchor_profile": ",".join(topic_anchor_profile_mix),
        "macro_context_bundle": bool(macro_context_bundle),
    }
    return {
        "documents": len(all_docs),
        "evaluated_documents": len(gate_docs),
        "excluded_quality_scan_docs": excluded_quality_scan_docs,
        "excluded_seed_fallback_docs": excluded_seed_fallback_docs,
        "excluded_failed_docs": excluded_failed_docs,
        "excluded_supplemental_html_docs": excluded_supplemental_html_docs,
        "backend_ready": bool(backend_ready),
        "backend_mode": backend_mode,
        "backend_fail_reason": backend_fail_reason,
        "visual_ocr_ready": bool(visual_ready),
        "visual_ocr_mode": visual_mode,
        "visual_ocr_fail_reason": visual_fail_reason,
        "native_pdf_supported": bool(native_pdf_supported),
        "min_chars": min_chars,
        "avg_chars": round(avg_chars, 2),
        "avg_semantic_hits": round(avg_semantic_hits, 2),
        "avg_structure_score": round(avg_structure, 3),
        "avg_numeric_fidelity": round(avg_numeric, 3),
        "table_docs": int(table_docs),
        "table_rows_total": int(table_rows_total),
        "blocks_total": int(blocks_total),
        "tables_total": int(tables_total),
        "reading_order_total": int(reading_order_total),
        "bbox_total": int(bbox_total),
        "non_scrape_count": non_scrape_count,
        "non_scrape_ratio": round(non_scrape_ratio, 3),
        "fallback_used_count": fallback_used_count,
        "engine_mix": engines,
        "extraction_mode_mix": extraction_mode_mix,
        "document_class_mix": document_class_mix,
        "document_lane_mix": document_lane_mix,
        "topic_anchor_profile_mix": topic_anchor_profile_mix,
        "escalation_count": int(escalation_count),
        "lane_scorecard": lane_scorecard,
        "slo_summary": slo_summary,
        "smoke_gate": smoke_gate,
        "visual_required_count": int(visual_required_count),
        "visual_unavailable_count": int(visual_unavailable_count),
        "current_policy": current_policy,
        "engine_chain_last_doc": engine_chain_last_doc,
        "min_chars_required": min_required,
        "avg_chars_required": avg_required,
        "avg_chars_required_effective": avg_required_effective,
        "semantic_hits_required": semantic_required,
        "semantic_hits_required_effective": semantic_required_effective,
        "table_docs_required": require_table_docs,
        "min_non_scrape_ratio": round(min_non_scrape_ratio, 3),
        "min_structure_score": round(min_structure_score, 3),
        "min_structure_score_effective": round(min_structure_score_effective, 3),
        "min_numeric_fidelity": round(min_numeric_fidelity, 3),
        "min_numeric_fidelity_effective": round(min_numeric_fidelity_effective, 3),
        "structure_bench_min": int(structure_bench_min),
        "structure_bench_min_effective": int(structure_bench_min_effective),
        "correctness_bench_min": int(correctness_bench_min),
        "correctness_bench_min_effective": int(correctness_bench_min_effective),
        "benchmark_proxy_scores": {
            "structure_lane": int(structure_lane),
            "correctness_lane": int(correctness_lane),
        },
        "evidence": {
            "layout_blocks": int(blocks_total),
            "table_blocks": int(tables_total),
            "table_rows": int(table_rows_total),
            "numeric_signal_score": round(avg_numeric, 3),
            "semantic_coverage_avg": round(avg_semantic_hits, 2),
        },
        "quality_breakdown": quality_breakdown,
        "avg_finance_consistency_score": round(avg_finance_consistency, 3) if avg_finance_consistency is not None else None,
        "finbert_negative_count": int(finbert_negative_count),
        "finance_topic": finance_topic,
        "native_pdf_bundle": native_pdf_bundle,
        "macro_context_bundle": macro_context_bundle,
        "news_feed_bundle": news_feed_bundle,
        "ocr_unverified_scrape_only": bool(scrape_only or excluded_supplemental_html_docs > 0),
        "scrape_only_detected": bool(scrape_only or excluded_supplemental_html_docs > 0),
        "gate_skipped": bool(seed_fallback_only),
        "gate_skip_reason": "seed_fallback_only" if seed_fallback_only else "",
        "failure_class": failure_class,
        "document_lane": ",".join(document_lane_mix),
        "topic_anchor_profile": ",".join(topic_anchor_profile_mix),
        "gate_fail_reasons": gate_fail_reasons,
        "gate_pass": gate_pass,
    }


def _stage_ocr(run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.document_triage import triage_sources
    gather = _read_json(_stage_result_path(run_dir, "gather"), {})
    max_sources = int(config["max_articles"])
    raw_sources = _dedupe_source_rows(
        [item for item in list(gather.get("sources") or []) if isinstance(item, dict) and _source_url(item)]
    )[:max_sources]
    tickers = list(config.get("equity_news_tickers") or [])
    triage = triage_sources(raw_sources, tickers=tickers, topic=str(config.get("topic") or ""))
    _write_json(_stage_result_path(run_dir, "triage"), triage)
    sources = triage["accepted_direct"] + triage["accepted_ocr"]
    sources = _merge_gather_accepted_documents(sources, gather, limit=max_sources)
    if not sources:
        local_sources: list[dict[str, Any]] = []
        for item in raw_sources:
            try:
                if Path(str(item.get("url") or "")).exists():
                    local_sources.append(item)
            except Exception:
                continue
        if local_sources:
            sources = local_sources
    result = _init_ocr_result(config, sources)
    readiness = _ocr_backend_readiness(config)
    result["readiness"] = readiness
    rag_store_inst = _nightly_ocr_rag(run_dir)
    seen_fingerprints: set[str] = set()
    for item in sources:
        processed = _process_ocr_source(
            item,
            topic=str(config.get("topic") or DEFAULT_TOPIC),
            config=config,
            rag_store_inst=rag_store_inst,
            readiness=readiness,
            seen_fingerprints=seen_fingerprints,
        )
        doc_row = dict(processed.get("document") or {})
        if not doc_row:
            continue
        result["documents"].append(doc_row)
        ingested_ids = [str(v) for v in list(doc_row.get("ingested_ids") or []) if str(v).strip()]
        if ingested_ids:
            result["ingested_ids"].extend(ingested_ids)
        elif doc_row.get("ingested_id"):
            result["ingested_ids"].append(str(doc_row["ingested_id"]))
        result["errors"].extend(list(processed.get("errors") or []))
    min_fallback_docs = max(1, int(os.getenv("APOLLO_OCR_MIN_EVALUATED_DOCS_FOR_CONFIDENCE", "2")))
    initial_quality = _evaluate_ocr_quality(result, config)
    initial_evaluated_docs = int(initial_quality.get("evaluated_documents") or 0)
    should_try_curated_fallback = (
        (
            not bool(initial_quality.get("gate_pass"))
            and str(initial_quality.get("failure_class") or "") == "no_valid_documents"
        )
        or (
            bool(initial_quality.get("gate_pass"))
            and initial_evaluated_docs < min_fallback_docs
            and not bool(initial_quality.get("gate_skipped"))
        )
    )
    if should_try_curated_fallback:
        try:
            from Apollo.trading_homework import _looks_like_report_like_url, _manifest_candidates

            existing_urls = {str(item.get("url") or "").strip() for item in sources if str(item.get("url") or "").strip()}
            fallback_pool = _manifest_candidates(str(config.get("topic") or DEFAULT_TOPIC), max_items=12)
            curated_sources: list[dict[str, Any]] = []
            for row in fallback_pool:
                url = str((row or {}).get("url") or "").strip()
                if not url or url in existing_urls:
                    continue
                if not (url.lower().endswith(".pdf") or _looks_like_report_like_url(url)):
                    continue
                item = dict(row)
                item["provider"] = str(item.get("provider") or "manifest_curated_ocr_fallback")
                item["ocr_curated_fallback"] = True
                curated_sources.append(item)
                if len(curated_sources) >= int(os.getenv("APOLLO_OCR_CURATED_FALLBACK_MAX", "4")):
                    break

            fallback_triage = triage_sources(curated_sources, tickers=tickers, topic=str(config.get("topic") or ""))
            fallback_sources = list(fallback_triage.get("accepted_ocr") or []) + list(fallback_triage.get("accepted_direct") or [])
            fallback_urls = {str(item.get("url") or "").strip() for item in fallback_sources if str(item.get("url") or "").strip()}
            for item in curated_sources:
                url = str(item.get("url") or "").strip()
                if not url or url in fallback_urls:
                    continue
                fallback_sources.append(item)
                fallback_urls.add(url)
                if len(fallback_sources) >= min_fallback_docs:
                    break
            if not fallback_sources and curated_sources:
                fallback_sources = curated_sources
            result["curated_document_fallback"] = {
                "attempted": True,
                "candidate_count": len(curated_sources),
                "source_count": len(fallback_sources),
                "reason": "ocr_no_valid_documents" if initial_evaluated_docs <= 0 else "ocr_low_evidence_depth",
                "min_evaluated_docs_for_confidence": min_fallback_docs,
                "initial_evaluated_documents": initial_evaluated_docs,
            }
            if fallback_sources:
                result["sources"].extend(fallback_sources)
            for item in fallback_sources:
                processed = _process_ocr_source(
                    item,
                    topic=str(config.get("topic") or DEFAULT_TOPIC),
                    config=config,
                    rag_store_inst=rag_store_inst,
                    readiness=readiness,
                    seen_fingerprints=seen_fingerprints,
                )
                doc_row = dict(processed.get("document") or {})
                if not doc_row:
                    continue
                doc_row["ocr_curated_fallback"] = True
                result["documents"].append(doc_row)
                ingested_ids = [str(v) for v in list(doc_row.get("ingested_ids") or []) if str(v).strip()]
                if ingested_ids:
                    result["ingested_ids"].extend(ingested_ids)
                elif doc_row.get("ingested_id"):
                    result["ingested_ids"].append(str(doc_row["ingested_id"]))
                result["errors"].extend(list(processed.get("errors") or []))
        except Exception as exc:
            result["curated_document_fallback"] = {
                "attempted": True,
                "candidate_count": 0,
                "source_count": 0,
                "reason": "ocr_no_valid_documents",
                "error": f"{type(exc).__name__}:{exc}",
            }
    result["ingested"] = len(result["ingested_ids"])
    result["completed"] = True
    result["quality"] = _evaluate_ocr_quality(result, config)
    result["ok"] = bool(result["quality"].get("gate_pass"))
    if not result["ok"]:
        fail_reasons = ",".join(list(result.get("quality", {}).get("gate_fail_reasons") or []))
        result["errors"].append(f"ocr_quality_failed:{fail_reasons or 'unknown'}")
    _write_ocr_report(run_dir, config, result)
    _write_json(_stage_result_path(run_dir, "ocr"), result)
    print(
        json.dumps(
            {
                "report_path": result.get("report_path"),
                "ingested": result.get("ingested"),
                "quality": result.get("quality") or {},
                "errors": result.get("errors") or [],
            },
            indent=2,
        )
    )
    return result


def _evaluate_hipporag_quality(result: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    _ = config
    remaining_before = int(result.get("remaining_before") or 0)
    remaining_after = int(result.get("remaining_after") or 0)
    processed = int(result.get("processed") or 0)
    processed_finance = int(result.get("processed_finance") or processed)
    skipped_non_finance = int(result.get("skipped_non_finance") or 0)
    requeued_low_quality = int(result.get("requeued_low_quality") or 0)
    triples = int(result.get("triples") or 0)
    validated_non_triple_finance = int(result.get("validated_non_triple_finance") or 0)
    pending_quarantine = int(result.get("pending_quarantine") or 0)
    completed = bool(result.get("completed"))
    explicit_no_work = remaining_before <= 0 and completed
    progress_this_step = remaining_before > 0 and processed_finance > 0
    has_meaningful_extraction = triples > 0 or validated_non_triple_finance > 0
    gate_pass = explicit_no_work or (progress_this_step and has_meaningful_extraction)
    reasons: list[str] = []
    if not explicit_no_work and processed_finance <= 0 and remaining_before > 0:
        reasons.append("hipporag_no_progress")
    if not explicit_no_work and progress_this_step and not has_meaningful_extraction:
        reasons.append("hipporag_zero_triples")
    if not explicit_no_work and not progress_this_step and remaining_before > 0:
        reasons.append("hipporag_progress_insufficient")
    if not explicit_no_work and pending_quarantine > 0:
        reasons.append("hipporag_pending_quarantine")
    return {
        "remaining_before": remaining_before,
        "remaining_after": remaining_after,
        "processed": processed,
        "processed_finance": processed_finance,
        "skipped_non_finance": skipped_non_finance,
        "requeued_low_quality": requeued_low_quality,
        "pending_quarantine": pending_quarantine,
        "triples": triples,
        "validated_non_triple_finance": validated_non_triple_finance,
        "completed": completed,
        "explicit_no_work": explicit_no_work,
        "progress_this_step": progress_this_step,
        "has_meaningful_extraction": has_meaningful_extraction,
        "gate_pass": gate_pass,
        "fail_reasons": reasons,
    }


def _stage_hipporag(run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.apollo_hipporag.build_graph import build_graph

    readiness = _nightly_hipporag_readiness(run_dir)
    if not readiness.get("ready"):
        result = {
            "ok": False,
            "completed": False,
            "error": "financial_corpus_not_ready",
            "readiness": readiness,
            "processed": 0,
            "processed_finance": 0,
            "requeued_low_quality": 0,
            "pending_quarantine": 0,
            "triples": 0,
            "remaining_before": 0,
            "remaining_after": 0,
        }
        result["quality"] = _evaluate_hipporag_quality(result, config)
        _write_json(_stage_result_path(run_dir, "hipporag"), result)
        return result

    result = build_graph(
        rag_dir=_nightly_hipporag_rag_dir(run_dir),
        collection_name=_nightly_hipporag_collection(),
        use_llm=bool(config["hipporag_use_llm"]),
        limit=config["hipporag_limit"],
        batch_size=int(config["hipporag_batch_size"]),
        llm_model=str(config["hipporag_llm_model"]),
        ollama_url=str(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")),
        llm_timeout=int(config["hipporag_llm_timeout_sec"]),
        llm_num_predict=int(config["hipporag_llm_num_predict"]),
    )
    result["quality"] = _evaluate_hipporag_quality(result, config)
    result["ok"] = bool(result.get("ok")) and bool((result.get("quality") or {}).get("gate_pass"))
    if not result["ok"]:
        result["error"] = ",".join(list((result.get("quality") or {}).get("fail_reasons") or []) or ["hipporag_quality_failed"])
    try:
        from Apollo import graph_artifacts

        kg_path = Path(_nightly_hipporag_rag_dir(run_dir)) / "financial_kg.json"
        if not kg_path.exists():
            kg_path = graph_artifacts.DEFAULT_KG_PATH
        result["graph_artifacts"] = graph_artifacts.write_graph_artifacts(kg_path=kg_path)
    except Exception as exc:
        result["graph_artifacts"] = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
    _write_json(_stage_result_path(run_dir, "hipporag"), result)
    print(json.dumps(result, indent=2))
    return result


def _stage_self_check(run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    _ = config
    try:
        payload = _http_self_check()
        result = dict(payload.get("payload") or {})
        result["mode"] = "http"
        result["status_code"] = payload.get("status_code")
    except Exception as exc:
        result = _local_self_check(run_dir)
        result["http_error"] = f"{type(exc).__name__}:{exc}"
    status = _load_status(run_dir)
    if not status:
        status = {"stages": {}}
    status.setdefault("stages", {})
    status["stages"]["self_check"] = dict(result)
    status["quality"] = _compute_quality(status, config)
    status["stage_details"] = _build_stage_details(status)
    focus_enabled = bool(config.get("focus_universe_enabled"))
    focus_universe = dict((_read_json(_stage_result_path(run_dir, "gather"), {})).get("focus_universe") or _load_focus_universe_artifact(run_dir) or {})
    if focus_universe:
        decision_gate = _compute_focus_decision_gate(status, config, focus_universe)
    elif focus_enabled:
        decision_gate = {
            "computed": False,
            "decision_ready": False,
            "decision_mode": "missing_focus_universe",
            "reasons": ["focus_universe_enabled_but_missing_selection"],
        }
    else:
        decision_gate = {
            "computed": False,
            "decision_ready": False,
            "decision_mode": "disabled",
            "skipped": True,
            "reasons": ["focus_universe_disabled_for_this_run"],
        }
    checks = dict(result.get("checks") or {})
    checks["focus_universe_contract"] = {
        "ok": (not focus_enabled) or bool((focus_universe.get("selected_theme") or {}).get("label") or (focus_universe.get("selected_theme") or {}).get("id")),
        "selected_theme": str(((focus_universe.get("selected_theme") or {}).get("label")) or ""),
    }
    checks["decision_gate_contract"] = {
        "ok": (not focus_enabled) or bool(decision_gate.get("computed")),
        "decision_mode": str(decision_gate.get("decision_mode") or ""),
        "decision_ready": bool(decision_gate.get("decision_ready")),
        "skipped": bool(decision_gate.get("skipped")),
    }
    result["focus_universe"] = focus_universe
    result["decision_gate"] = decision_gate
    result["checks"] = checks
    result["ok"] = bool(result.get("ok")) and all(bool(item.get("ok")) for item in checks.values() if isinstance(item, dict))
    _write_json(_stage_result_path(run_dir, "self_check"), result)
    print(json.dumps(result, indent=2))
    return result


def run_stage(stage_name: str, run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    if stage_name == "gather":
        return _stage_gather(run_dir, config)
    if stage_name == "ocr":
        return _stage_ocr(run_dir, config)
    if stage_name == "hipporag":
        return _stage_hipporag(run_dir, config)
    if stage_name == "self_check":
        return _stage_self_check(run_dir, config)
    raise ValueError(f"unsupported_stage:{stage_name}")


def run_background_stage(stage_name: str, run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    if stage_name == "gather":
        payload = _stage_gather(run_dir, config)
        payload["completed"] = bool(payload.get("completed"))
        return payload
    if stage_name == "ocr":
        return _background_stage_ocr_step(run_dir, config)
    if stage_name == "hipporag":
        return _background_stage_hipporag_step(run_dir, config)
    if stage_name == "self_check":
        payload = _stage_self_check(run_dir, config)
        payload["completed"] = True
        return payload
    raise ValueError(f"unsupported_background_stage:{stage_name}")


def _background_run_dir(config: Dict[str, Any]) -> Path:
    run_id = str(config.get("batch_id") or DEFAULT_BACKGROUND_RUN_ID)
    # Use a date-stable path for the default background ID so a run that
    # spans midnight doesn't split into two directories and lose all progress.
    if run_id == DEFAULT_BACKGROUND_RUN_ID:
        return RUNS_ROOT / "background" / _safe_slug(run_id)
    return RUNS_ROOT / _now_local().strftime("%Y-%m-%d") / _safe_slug(run_id)


def _background_stage_ocr_step(run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    gather = _read_json(_stage_result_path(run_dir, "gather"), {})
    sources = [item for item in list(gather.get("sources") or [])[: int(config["max_articles"])] if isinstance(item, dict) and str(item.get("url") or "").strip()]
    result = _load_ocr_result(run_dir, config, sources)
    readiness = _ocr_backend_readiness(config)
    result["readiness"] = readiness
    processed_urls = {str(doc.get("url") or "") for doc in list(result.get("documents") or [])}
    seen_fingerprints = _ocr_seen_fingerprints_from_documents(list(result.get("documents") or []))
    remaining = [item for item in sources if str(item.get("url") or "") not in processed_urls]
    if not remaining:
        result["ingested"] = len(result.get("ingested_ids") or [])
        result["completed"] = True
        result["quality"] = _evaluate_ocr_quality(result, config)
        result["ok"] = bool(result["quality"].get("gate_pass"))
        if not result["ok"]:
            fail_reasons = ",".join(list(result.get("quality", {}).get("gate_fail_reasons") or []))
            result.setdefault("errors", []).append(f"ocr_quality_failed:{fail_reasons or 'unknown'}")
        _write_ocr_report(run_dir, config, result)
        _write_json(_stage_result_path(run_dir, "ocr"), result)
        return result

    rag_store_inst = _nightly_ocr_rag(run_dir)
    budget = int(config.get("background_ocr_docs_per_tick") or 1)
    for item in remaining[:budget]:
        processed = _process_ocr_source(
            item,
            topic=str(config.get("topic") or DEFAULT_TOPIC),
            config=config,
            rag_store_inst=rag_store_inst,
            readiness=readiness,
            seen_fingerprints=seen_fingerprints,
        )
        doc_row = dict(processed.get("document") or {})
        if not doc_row:
            continue
        result["documents"].append(doc_row)
        ingested_ids = [str(v) for v in list(doc_row.get("ingested_ids") or []) if str(v).strip()]
        if ingested_ids:
            result.setdefault("ingested_ids", []).extend(ingested_ids)
        elif doc_row.get("ingested_id"):
            result.setdefault("ingested_ids", []).append(str(doc_row["ingested_id"]))
        result.setdefault("errors", []).extend(list(processed.get("errors") or []))

    remaining_after = len(sources) - len(result.get("documents") or [])
    result["ingested"] = len(result.get("ingested_ids") or [])
    result["completed"] = remaining_after <= 0
    result["quality"] = _evaluate_ocr_quality(result, config)
    result["ok"] = bool(result["completed"] and result["quality"].get("gate_pass"))
    if result["completed"] and not result["ok"]:
        fail_reasons = ",".join(list(result.get("quality", {}).get("gate_fail_reasons") or []))
        result.setdefault("errors", []).append(f"ocr_quality_failed:{fail_reasons or 'unknown'}")
    result["remaining_documents"] = max(0, remaining_after)
    _write_ocr_report(run_dir, config, result)
    _write_json(_stage_result_path(run_dir, "ocr"), result)
    try:
        import threading as _threading
        from Apollo.tools.ocr_phase2_truth_gate import write_phase2_validation_artifacts as _write_truth
        _threading.Thread(
            target=_write_truth,
            kwargs={"pytest_result": None},
            daemon=True,
            name="ocr_truth_matrix",
        ).start()
    except Exception:
        pass
    return result


def _background_stage_hipporag_step(run_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.apollo_hipporag.build_graph import build_graph

    readiness = _nightly_hipporag_readiness(run_dir)
    if not readiness.get("ready"):
        result = {
            "ok": False,
            "completed": False,
            "error": "financial_corpus_not_ready",
            "readiness": readiness,
            "processed": 0,
            "processed_finance": 0,
            "requeued_low_quality": 0,
            "pending_quarantine": 0,
            "triples": 0,
            "remaining_before": 0,
            "remaining_after": 0,
        }
        result["quality"] = _evaluate_hipporag_quality(result, config)
        _write_json(_stage_result_path(run_dir, "hipporag"), result)
        return result

    result = build_graph(
        rag_dir=_nightly_hipporag_rag_dir(run_dir),
        collection_name=_nightly_hipporag_collection(),
        use_llm=bool(config["hipporag_use_llm"]),
        limit=config["hipporag_limit"],
        max_new_chunks=int(config["background_hipporag_step_chunks"]),
        batch_size=int(config["hipporag_batch_size"]),
        llm_model=str(config["hipporag_llm_model"]),
        ollama_url=str(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")),
        llm_timeout=int(config["hipporag_llm_timeout_sec"]),
        llm_num_predict=int(config["hipporag_llm_num_predict"]),
    )
    result["quality"] = _evaluate_hipporag_quality(result, config)
    result["ok"] = bool(result.get("ok")) and bool((result.get("quality") or {}).get("gate_pass"))
    if not result["ok"]:
        result["error"] = ",".join(list((result.get("quality") or {}).get("fail_reasons") or []) or ["hipporag_quality_failed"])
    _write_json(_stage_result_path(run_dir, "hipporag"), result)
    return result


def run_background_pipeline_step(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = _pipeline_config({**dict(payload or {}), "run_mode": "background"})
    estimate = estimate_runtime_breakdown(config)
    run_id = str(config["batch_id"])
    run_dir = _background_run_dir(config)
    run_dir.mkdir(parents=True, exist_ok=True)

    existing_config = _read_json(run_dir / "config.json", {})
    if existing_config:
        merged_config = dict(existing_config)
        # Use `is not None` so False and 0 are not silently dropped
        # (e.g. allow_cpu_fallback=False, hipporag_limit=0 must override)
        merged_config.update({k: v for k, v in config.items() if v is not None})
        config = merged_config
    _write_json(run_dir / "config.json", config)

    status = _load_status(run_dir)
    if not status or not status.get("run_id"):
        status = {
            "ok": False,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "started_at": _utc_iso(),
            "finished_at": "",
            "estimate": estimate,
            "stages": {},
            "current_stage": "gather",
            "run_mode": "background",
        }
        _save_status(run_dir, status)
    if _all_stages_complete(status):
        status["ok"] = bool(((status.get("quality") or {}).get("gate_pass")))
        status["current_stage"] = "done"
        if not status.get("finished_at"):
            status["finished_at"] = _utc_iso()
        _write_text(run_dir / "summary.md", _render_summary(run_dir, config, estimate))
        _write_truthful_pipeline_report(run_dir, status)
        _save_status(run_dir, status)
        return status

    # Check stop flag before acquiring the lock so we don't block anything.
    if pipeline_stopped():
        status["waiting"] = {"reason": "stopped", "ts": _utc_iso()}
        _save_status(run_dir, status)
        return status

    # Check pause flag â€” skip this tick, return without advancing.
    if pipeline_paused():
        status["waiting"] = {"reason": "paused", "ts": _utc_iso()}
        _save_status(run_dir, status)
        return status

    memory_gate = _memory_pressure_snapshot(config)
    status["memory_pressure"] = memory_gate
    if not memory_gate.get("ok"):
        status["waiting"] = {
            "reason": "memory_pressure",
            "ts": _utc_iso(),
            "reasons": list(memory_gate.get("reasons") or []),
            "metrics": dict(memory_gate.get("metrics") or {}),
        }
        _save_status(run_dir, status)
        return status

    try:
        _acquire_lock(run_id, run_dir, stage="background-step")
    except Exception as exc:
        status["waiting"] = {"reason": f"{type(exc).__name__}:{exc}", "ts": _utc_iso()}
        _save_status(run_dir, status)
        return status

    try:
        _touch_lock(run_id, "preflight")
        # Re-run preflight every tick until it succeeds. A failed preflight is
        # NOT cached â€” if the GPU was unavailable last tick it may be ready now.
        preflight_ok = bool((status.get("preflight") or {}).get("ok"))
        if not preflight_ok:
            preflight = _ensure_gx10_tunnel(config, run_dir)
            if preflight.get("ok"):
                status["preflight"] = preflight
                _save_status(run_dir, status)
            else:
                status["waiting"] = {"reason": "gpu_preflight_failed", "ts": _utc_iso()}
                _save_status(run_dir, status)
                return status

        stage_name = _next_incomplete_stage(status)
        status["current_stage"] = stage_name
        status.pop("waiting", None)
        _save_status(run_dir, status)
        _touch_lock(run_id, stage_name)

        gate = _background_gx10_idle(config)
        status["gb10_gate"] = {**gate, "ts": _utc_iso()}
        _save_status(run_dir, status)
        if _stage_requires_gb10(stage_name, config) and not gate.get("idle"):
            status["waiting"] = {"reason": "gb10_busy", "ts": _utc_iso(), "stage": stage_name, **gate}
            _save_status(run_dir, status)
            return status

        timeout_map = {
            "gather": int(config["background_gather_timeout_sec"]),
            "ocr": int(config["background_ocr_step_timeout_sec"]),
            "hipporag": int(config["background_hipporag_step_timeout_sec"]),
            "self_check": int(config["background_self_check_timeout_sec"]),
        }
        stage_payload = _run_background_subprocess_stage(run_id, run_dir, stage_name, timeout_map[stage_name])

        _update_stage_status(run_dir, stage_name, stage_payload)
        status = _load_status(run_dir)
        status["last_step_at"] = _utc_iso()
        recovery = dict(status.get("recovery") or {})
        if stage_payload.get("timed_out"):
            status["waiting"] = {
                "reason": f"stage_timeout:{stage_name}",
                "ts": _utc_iso(),
                "stage": stage_name,
                "timeout_sec": int(timeout_map[stage_name]),
                "log_path": str(stage_payload.get("log_path") or ""),
            }
            recovery["last_action"] = f"timeout:{stage_name}"
            recovery["last_action_at"] = _utc_iso()
            recovery["next_action"] = "retry_next_tick"
            status["recovery"] = recovery
            _save_status(run_dir, status)
            return status
        if stage_name == "gather":
            gather_stage = _stage_result_view(dict((status.get("stages") or {}).get("gather") or stage_payload))
            source_mix = _gather_source_mix(gather_stage)
            if bool(source_mix.get("seed_only")):
                stages = dict(status.get("stages") or {})
                gather_row = dict(stages.get("gather") or {})
                gather_result = dict(gather_row.get("result") or {})
                gather_row["ok"] = False
                gather_row["completed"] = False
                gather_row["error"] = "insufficient_real_sources"
                gather_row["degraded_reason"] = "seed_fallback_only"
                gather_row["source_mix"] = source_mix
                gather_result["ok"] = False
                gather_result["completed"] = False
                gather_result["error"] = "insufficient_real_sources"
                gather_result["degraded_reason"] = "seed_fallback_only"
                gather_result["source_mix"] = source_mix
                gather_row["result"] = gather_result
                stages["gather"] = gather_row
                status["stages"] = stages
                status["ok"] = False
                status["current_stage"] = "gather"
                status["waiting"] = {
                    "reason": "insufficient_real_sources",
                    "ts": _utc_iso(),
                    "stage": "gather",
                    "degraded_reason": "seed_fallback_only",
                    "source_mix": source_mix,
                }
                recovery["last_action"] = "degraded_seed_fallback_only"
                recovery["last_action_at"] = _utc_iso()
                recovery["next_action"] = "retry_gather_for_real_sources"
                status["recovery"] = recovery
                _save_status(run_dir, status)
                return status
        if stage_name == "hipporag":
            hippo = _stage_result_view(dict(stage_payload))
            step_budget = int(config.get("background_hipporag_step_chunks") or DEFAULT_BACKGROUND_HIPPORAG_STEP_CHUNKS)
            processed_finance = int(hippo.get("processed_finance") or hippo.get("processed") or 0)
            skipped_non_finance = int(hippo.get("skipped_non_finance") or 0)
            stall_now = (
                int(hippo.get("remaining_before") or 0) > 0
                and processed_finance <= 0
                and skipped_non_finance >= step_budget
            )
            streak = int(recovery.get("stall_streak") or 0)
            streak = (streak + 1) if stall_now else 0
            recovery["stall_streak"] = streak
            if stall_now and streak >= 3:
                candidate_urls = _recovery_candidate_urls(status, config)
                if candidate_urls:
                    target_url = candidate_urls[0]
                    purge_result = {
                        "operational_rag": _purge_operational_rag_by_url(target_url),
                        "financial_corpus": _purge_financial_corpus_by_url(target_url),
                    }
                    _reset_background_run_for_regather(run_dir, status, target_url, purge_result)
                    status = _load_status(run_dir)
                    recovery = dict(status.get("recovery") or {})
                    recovery["stall_streak"] = 0
                    recovery["last_action"] = "auto_recover_reset_to_gather"
                    recovery["last_action_at"] = _utc_iso()
                    recovery["next_action"] = "gather_strict_ab_next_tick"
                    status["recovery"] = recovery
                    status["waiting"] = {
                        "reason": "auto_recovery_triggered",
                        "stage": "hipporag",
                        "ts": _utc_iso(),
                        "target_url": target_url,
                    }
                    _save_status(run_dir, status)
                    return status
                recovery["last_action"] = "stall_detected_no_candidate_url"
                recovery["last_action_at"] = _utc_iso()
                recovery["next_action"] = "manual_review_recommended"
            elif stall_now:
                recovery["last_action"] = "stall_detected_waiting_recovery_threshold"
                recovery["last_action_at"] = _utc_iso()
                recovery["next_action"] = "continue_until_streak_3"
            else:
                recovery["next_action"] = "continue_pipeline"
        status["recovery"] = recovery
        if _all_stages_complete(status):
            status["ok"] = bool(((status.get("quality") or {}).get("gate_pass")))
            status["finished_at"] = _utc_iso()
            status["current_stage"] = "done"
            _write_text(run_dir / "summary.md", _render_summary(run_dir, config, estimate))
            _write_truthful_pipeline_report(run_dir, status)
        _save_status(run_dir, status)
        return status
    finally:
        _release_lock(run_id)


def start_background_run(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = _pipeline_config(payload)
    run_id = str(config["batch_id"])
    _BACKGROUND_RUNS[run_id] = {"run_id": run_id, "status": "queued", "config": config}

    def _worker() -> None:
        try:
            _BACKGROUND_RUNS[run_id]["status"] = "running"
            result = run_pipeline(config)
            _BACKGROUND_RUNS[run_id]["status"] = "ok" if result.get("ok") else "error"
            _BACKGROUND_RUNS[run_id]["result"] = result
        except Exception as exc:
            _BACKGROUND_RUNS[run_id]["status"] = "error"
            _BACKGROUND_RUNS[run_id]["error"] = f"{type(exc).__name__}:{exc}"
            _BACKGROUND_RUNS[run_id]["traceback"] = traceback.format_exc()

    Thread(target=_worker, daemon=True, name=f"apollo-nightly-{run_id}").start()
    return {"ok": True, "run_id": run_id, "status": _BACKGROUND_RUNS[run_id]}


def background_status(run_id: str) -> Dict[str, Any]:
    return dict(_BACKGROUND_RUNS.get(run_id) or {})


def _next_local_window(start_text: str, *, run_date: Optional[date] = None, minutes: int = 0) -> tuple[datetime, datetime]:
    base_date = run_date or (_now_local() + timedelta(days=1)).date()
    hour, minute = [int(part) for part in start_text.split(":", 1)]
    start_dt = datetime.combine(base_date, dt_time(hour=hour, minute=minute), tzinfo=_now_local().tzinfo)
    end_dt = start_dt + timedelta(minutes=minutes)
    return start_dt, end_dt


def schedule_nightly_run(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from Sky.core.calendar_store import create_event, list_events, update_event

    config = _pipeline_config(payload)
    estimate = estimate_runtime_breakdown(config)
    run_date_raw = str((payload or {}).get("date") or "").strip()
    run_date = date.fromisoformat(run_date_raw) if run_date_raw else None
    start_dt, end_dt = _next_local_window(DEFAULT_START_LOCAL, run_date=run_date, minutes=int(estimate["total_minutes"]))
    wrapper = _task_wrapper_path()
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    powershell_exe = _powershell_executable()
    task_command = subprocess.list2cmdline(
        [
            powershell_exe,
            "-WindowStyle",
            "Hidden",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ]
    )
    cmd = ["schtasks", "/Create", "/SC", "DAILY", "/ST", DEFAULT_START_LOCAL, "/TN", DEFAULT_TASK_NAME, "/TR", task_command, "/F"]
    proc = subprocess.run(cmd, cwd=str(_ENGINEERING_ROOT), capture_output=True, text=True, timeout=60, check=False)
    notes = "\n".join(
        [
            "Apollo nightly financial pipeline reservation.",
            f"Scheduled task: {DEFAULT_TASK_NAME}",
            f"Expected runtime: ~{estimate['total_minutes']} minutes",
            f"Stages: gather -> OCR/ingest -> HippoRAG -> self_check",
            f"Logs root: {RUNS_ROOT}",
            f"Graph chunk count basis: {estimate['chunk_count']}",
        ]
    )
    title = "Apollo Nightly Pipeline (GX10 / Gemma 4)"
    event_payload = {
        "title": title,
        "start": start_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "end": end_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "in_work",
        "source": "apollo",
        "trigger": f"Scheduled task: {DEFAULT_TASK_NAME}",
        "notes": notes,
        "all_day": False,
    }
    existing = None
    for event in list_events(start=event_payload["start"], end=event_payload["end"]):
        if str((event or {}).get("title") or "").strip() == title:
            existing = event
            break
    if existing and existing.get("id"):
        event = update_event(str(existing["id"]), event_payload)
        mode = "updated"
    else:
        event = create_event(event_payload)
        mode = "created"
    out = {
        "ok": proc.returncode == 0,
        "task_name": DEFAULT_TASK_NAME,
        "task_command": task_command,
        "task_stdout": proc.stdout or "",
        "task_stderr": proc.stderr or "",
        "calendar_mode": mode,
        "calendar_event": event,
        "estimate": estimate,
        "start_local": start_dt.isoformat(),
        "end_local": end_dt.isoformat(),
    }
    _write_json(RUNS_ROOT / "schedule.json", out)
    return out


def disable_nightly_schedule() -> Dict[str, Any]:
    proc = subprocess.run(
        ["schtasks", "/Change", "/TN", DEFAULT_TASK_NAME, "/Disable"],
        cwd=str(_ENGINEERING_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "task_name": DEFAULT_TASK_NAME,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def schedule_background_run(payload: Optional[Dict[str, Any]] = None, *, disable_nightly: bool = True) -> Dict[str, Any]:
    config = _pipeline_config({**dict(payload or {}), "run_mode": "background"})
    wrapper = _background_task_wrapper_path()
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    powershell_exe = _powershell_executable()
    task_command = subprocess.list2cmdline(
        [
            powershell_exe,
            "-WindowStyle",
            "Hidden",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ]
    )
    interval = int(config["background_interval_min"])
    cmd = [
        "schtasks",
        "/Create",
        "/SC",
        "MINUTE",
        "/MO",
        str(interval),
        "/TN",
        DEFAULT_BACKGROUND_TASK_NAME,
        "/TR",
        task_command,
        "/F",
    ]
    proc = subprocess.run(cmd, cwd=str(_ENGINEERING_ROOT), capture_output=True, text=True, timeout=60, check=False)
    enable_proc = subprocess.run(
        ["schtasks", "/Change", "/TN", DEFAULT_BACKGROUND_TASK_NAME, "/Enable"],
        cwd=str(_ENGINEERING_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    nightly = disable_nightly_schedule() if disable_nightly else {"ok": True, "task_name": DEFAULT_TASK_NAME, "skipped": True}
    out = {
        "ok": proc.returncode == 0 and enable_proc.returncode == 0 and bool(nightly.get("ok")),
        "task_name": DEFAULT_BACKGROUND_TASK_NAME,
        "task_command": task_command,
        "task_stdout": proc.stdout or "",
        "task_stderr": proc.stderr or "",
        "enable_stdout": enable_proc.stdout or "",
        "enable_stderr": enable_proc.stderr or "",
        "enabled": enable_proc.returncode == 0,
        "interval_min": interval,
        "nightly_task": nightly,
        "config": config,
    }
    _write_json(RUNS_ROOT / "background_schedule.json", out)
    return out


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apollo nightly finance pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--payload", default="")
    run_parser.add_argument("--payload-file", default="")

    stage_parser = sub.add_parser("stage")
    stage_parser.add_argument("stage_name", choices=["gather", "ocr", "hipporag", "self_check"])
    stage_parser.add_argument("--run-dir", required=True)
    stage_parser.add_argument("--config", required=True)

    sched_parser = sub.add_parser("schedule")
    sched_parser.add_argument("--payload", default="")

    bg_step_parser = sub.add_parser("background-step")
    bg_step_parser.add_argument("--payload", default="")

    bg_stage_parser = sub.add_parser("background-stage")
    bg_stage_parser.add_argument("stage_name", choices=["gather", "ocr", "hipporag", "self_check"])
    bg_stage_parser.add_argument("--run-dir", required=True)
    bg_stage_parser.add_argument("--config", required=True)

    bg_sched_parser = sub.add_parser("background-schedule")
    bg_sched_parser.add_argument("--payload", default="")
    bg_sched_parser.add_argument("--keep-nightly", action="store_true")

    purge_parser = sub.add_parser("purge-source")
    purge_parser.add_argument("--url", required=True)
    purge_parser.add_argument("--run-id", default=DEFAULT_BACKGROUND_RUN_ID)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if args.cmd == "run":
        payload_file = str(getattr(args, "payload_file", "") or "").strip()
        if payload_file:
            with open(payload_file, "r", encoding="utf-8-sig") as _pf:
                payload = json.load(_pf)
        else:
            payload = json.loads(args.payload) if str(args.payload or "").strip() else {}
        result = run_pipeline(payload)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "stage":
        run_dir = Path(args.run_dir).resolve()
        config = _read_json(Path(args.config).resolve(), {})
        result = run_stage(args.stage_name, run_dir, config)
        if "ok" not in result:
            result["ok"] = True
        _write_json(_stage_result_path(run_dir, args.stage_name), result)
        return 0 if result.get("ok", True) else 1
    if args.cmd == "schedule":
        payload = json.loads(args.payload) if str(args.payload or "").strip() else {}
        result = schedule_nightly_run(payload)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "background-step":
        payload = json.loads(args.payload) if str(args.payload or "").strip() else {}
        result = run_background_pipeline_step(payload)
        print(json.dumps(result, indent=2))
        return 0 if (result.get("ok") or (result.get("waiting") is not None) or bool(result.get("run_id"))) else 1
    if args.cmd == "background-stage":
        run_dir = Path(args.run_dir).resolve()
        config = _read_json(Path(args.config).resolve(), {})
        result = run_background_stage(args.stage_name, run_dir, config)
        if "ok" not in result:
            result["ok"] = True
        _write_json(_stage_result_path(run_dir, args.stage_name), result)
        return 0 if result.get("ok", True) else 1
    if args.cmd == "background-schedule":
        payload = json.loads(args.payload) if str(args.payload or "").strip() else {}
        result = schedule_background_run(payload, disable_nightly=not bool(args.keep_nightly))
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "purge-source":
        result = purge_background_source(args.url, run_id=str(args.run_id or DEFAULT_BACKGROUND_RUN_ID))
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

