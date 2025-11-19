import time
from collections import deque
from typing import Deque

START_TS = time.time()
COUNTS = {"chat": 0, "write": 0, "search": 0, "review": 0, "appendix": 0}
CHAT_LATENCIES: Deque[float] = deque(maxlen=200)
RAG_HITS: Deque[int] = deque(maxlen=200)
RAG_K: Deque[int] = deque(maxlen=200)
DEEPCODER_USAGE: Deque[int] = deque(maxlen=200)
CHAT_DEPTH_COUNTS = {"fast": 0, "normal": 0, "deep": 0}
GEMMA_CLASSIFIER_CALLS = 0
REFLECTION_CALLS = 0
DEEP_REFLECTIONS_LOGGED = 0
AUTORUN_CALLS = 0
EXTERNAL_DOCS_DOWNLOADED = 0
OCR_PAGES_PROCESSED = 0
EXTERNAL_TEXTS_INGESTED = 0


def record_event(name: str) -> None:
    if name in COUNTS:
        COUNTS[name] += 1


def record_reflection_call() -> None:
    global REFLECTION_CALLS
    REFLECTION_CALLS += 1


def record_deep_reflection() -> None:
    global DEEP_REFLECTIONS_LOGGED
    DEEP_REFLECTIONS_LOGGED += 1


def record_autorun_call() -> None:
    global AUTORUN_CALLS
    AUTORUN_CALLS += 1


def record_external_download(count: int = 1) -> None:
    global EXTERNAL_DOCS_DOWNLOADED
    EXTERNAL_DOCS_DOWNLOADED += max(0, int(count))


def record_ocr_pages(count: int) -> None:
    global OCR_PAGES_PROCESSED
    OCR_PAGES_PROCESSED += max(0, int(count))


def record_external_text(count: int = 1) -> None:
    global EXTERNAL_TEXTS_INGESTED
    EXTERNAL_TEXTS_INGESTED += max(0, int(count))


def record_chat(
    latency_ms: float,
    rag_hit_count: int,
    deepcoder_used: bool,
    requested_k: int,
    depth: str,
    classifier_used: bool,
) -> None:
    COUNTS["chat"] += 1
    CHAT_LATENCIES.append(latency_ms)
    RAG_HITS.append(rag_hit_count)
    RAG_K.append(requested_k)
    DEEPCODER_USAGE.append(1 if deepcoder_used else 0)
    if depth not in CHAT_DEPTH_COUNTS:
        depth = "normal"
    CHAT_DEPTH_COUNTS[depth] += 1
    global GEMMA_CLASSIFIER_CALLS
    if classifier_used:
        GEMMA_CLASSIFIER_CALLS += 1


def _percentile(samples, pct: float) -> float:
    if not samples:
        return 0.0
    sorted_vals = sorted(samples)
    index = int(round((pct / 100) * (len(sorted_vals) - 1)))
    return float(sorted_vals[index])


def snapshot() -> dict:
    uptime = time.time() - START_TS
    latencies = list(CHAT_LATENCIES)
    deep_usage = sum(DEEPCODER_USAGE) / len(DEEPCODER_USAGE) if DEEPCODER_USAGE else 0.0
    avg_hits = sum(RAG_HITS) / len(RAG_HITS) if RAG_HITS else 0.0
    k_val = RAG_K[-1] if RAG_K else 0
    return {
        "uptime_s": round(uptime, 2),
        "counts": COUNTS.copy(),
        "latency_ms": {
            "chat_p50": round(_percentile(latencies, 50), 2),
            "chat_p95": round(_percentile(latencies, 95), 2),
        },
        "rag_hit_at_k": {"k": k_val, "avg_hits": round(avg_hits, 2)},
        "deepcoder_usage_rate": round(deep_usage, 3),
        "chat_depth_counts": CHAT_DEPTH_COUNTS.copy(),
        "gemma_classifier_calls": GEMMA_CLASSIFIER_CALLS,
        "reflection_calls": REFLECTION_CALLS,
        "deep_reflections_logged": DEEP_REFLECTIONS_LOGGED,
        "autorun_calls": AUTORUN_CALLS,
        "external_ingestion": {
            "external_docs_downloaded": EXTERNAL_DOCS_DOWNLOADED,
            "ocr_pages_processed": OCR_PAGES_PROCESSED,
            "external_texts_ingested": EXTERNAL_TEXTS_INGESTED,
        },
    }
