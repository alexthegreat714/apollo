import json
import os
import time
from datetime import datetime
from pathlib import Path

from .runtime_metrics import record_deep_reflection

REFLECT_LOG = Path(r"C:\Users\blyth\Desktop\Engineering\rag_data\Aegis\logs\reflections.jsonl")
REFLECT_SUMMARY = Path(r"C:\Users\blyth\Desktop\Engineering\rag_data\Aegis\logs\reflection_summary.txt")


def log_reflection(entry: dict) -> None:
    if not entry:
        return
    os.makedirs(REFLECT_LOG.parent, exist_ok=True)
    entry = dict(entry)
    entry.setdefault("timestamp", time.time())
    with open(REFLECT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if entry.get("depth") == "deep":
        record_deep_reflection()


def summarize_reflections(days: int = 1) -> dict:
    cutoff = time.time() - days * 86400
    total = 0
    deep_count = 0
    intents = {}
    os.makedirs(REFLECT_LOG.parent, exist_ok=True)
    if REFLECT_LOG.exists():
        with open(REFLECT_LOG, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp")
                if ts is None or ts < cutoff:
                    continue
                total += 1
                if entry.get("depth") == "deep":
                    deep_count += 1
                intent = entry.get("intent", "unknown")
                intents[intent] = intents.get(intent, 0) + 1
    ratio = round((deep_count / total) * 100, 2) if total else 0.0
    summary = {
        "window_days": days,
        "entries": total,
        "deep_reflections": deep_count,
        "deep_ratio_pct": ratio,
        "intent_distribution": intents,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    with open(REFLECT_SUMMARY, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, indent=2))
    return summary
