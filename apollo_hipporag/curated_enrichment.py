"""
apollo_hipporag.curated_enrichment — High-quality KG enrichment with:
  - Priority scoring by document type (SEC filings > earnings > news)
  - Recency weighting (recent filings processed first)
  - Semantic dedup (skip triples already well-covered in KG)
  - Confidence gating (LLM self-scores; low-confidence batches excluded)
  - Detailed run log with accepted/rejected examples

Default model: qwen3-vl:32b on GX10 (richer extraction than gemma3:12b)

Usage:
    python -m apollo_hipporag.curated_enrichment [options]
"""
from __future__ import annotations

import argparse
import html as _html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_hipporag.entity_extractor import (
    extract_triples_regex,
    is_valid_entity,
    _ticker_from_doc_id,
)
from apollo_hipporag.graph_store import FinancialKG

log = logging.getLogger(__name__)

for _k, _v in (
    ("CHROMA_ANONYMIZED_TELEMETRY", "FALSE"),
    ("ANONYMIZED_TELEMETRY", "FALSE"),
    ("CHROMA_TELEMETRY_IMPL", "none"),
    ("POSTHOG_DISABLED", "1"),
):
    os.environ.setdefault(_k, _v)
for _n in ("chromadb.telemetry", "chromadb.telemetry.product.posthog", "posthog"):
    _l = logging.getLogger(_n)
    _l.setLevel(logging.CRITICAL)
    _l.propagate = False

# ---------------------------------------------------------------------------
# Priority tiers
# ---------------------------------------------------------------------------

_TIER1_KINDS = frozenset({
    "sec_filings", "filing", "10-k", "10-q", "8-k", "annual_report",
})
_TIER2_KINDS = frozenset({
    "earnings_calendar", "fundamentals", "analyst_targets", "projection",
    "research", "analysis", "report",
})
_TIER3_KINDS = frozenset({
    "equity_news", "news", "news_feed",
})
_TIER4_KINDS = frozenset({
    "education", "regulation", "trading_research_raw", "macro_snapshot",
})

_RECENCY_BONUS = 20
_RECENCY_DAYS = 90
_DEDUP_MIN_EDGE_COUNT = 10  # skip triple if already supported by >=10 docs

_XBRL_MARKERS = (
    "xbrli:", "us-gaap:", "iso4217:", "srt:", "dei:",
    "ifrs-full:", "xbrldi:", "xlink:",
)
_XBRL_TOKEN_THRESHOLD = 0.30
_CIK_RE = re.compile(r"^\d{10}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_xbrl_boilerplate(text: str) -> bool:
    tokens = text.split()
    if not tokens:
        return True
    xbrl_count = sum(
        1 for t in tokens
        if any(t.startswith(m) for m in _XBRL_MARKERS)
        or _CIK_RE.match(t)
        or _ISO_DATE_RE.match(t)
    )
    return (xbrl_count / len(tokens)) > _XBRL_TOKEN_THRESHOLD


def _priority_score(meta: dict, now: datetime) -> int:
    kind = str((meta or {}).get("kind") or "").lower().strip()
    if kind in _TIER1_KINDS:
        score = 100
    elif kind in _TIER2_KINDS:
        score = 60
    elif kind in _TIER3_KINDS:
        score = 30
    elif kind in _TIER4_KINDS:
        score = 10
    else:
        score = 5

    # Recency bonus from metadata date field
    for date_key in ("date", "published_at", "filing_date", "report_date"):
        raw_date = str((meta or {}).get(date_key) or "").strip()
        if not raw_date:
            continue
        try:
            # Accept ISO date or datetime
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (now - dt).days <= _RECENCY_DAYS:
                score += _RECENCY_BONUS
        except Exception:
            pass
        break

    return score


def _is_dedup_covered(kg: FinancialKG, head: str, tail: str) -> bool:
    if not kg.g.has_edge(head, tail):
        return False
    edge_doc_count = len(kg.g[head][tail].get("doc_ids", set()))
    return edge_doc_count >= _DEDUP_MIN_EDGE_COUNT


# ---------------------------------------------------------------------------
# LLM prompt — richer than the standard extractor; asks for confidence
# ---------------------------------------------------------------------------

_CURATED_PROMPT = """\
You are a financial knowledge extraction system. Extract high-quality knowledge \
graph triples from the financial text below.

Rules:
- Extract ONLY clear, factual relationships explicitly stated in the text.
- Omit hedging, speculation, or opinion ("could", "might", "may", "expected to").
- Entities: companies, tickers, financial metrics, rates, instruments, events \
(1-5 words, lowercase). Use specific metric names when available.
- Relations: precise verb phrases (1-4 words, lowercase, e.g. \
"reported_eps_of", "raised_guidance_for", "beat_estimates_by").
- Extract 4-8 triples. Quality over quantity.
- Rate your overall confidence (1-10): 10 = all triples are explicit stated facts.

Return ONLY valid JSON — no explanation, no markdown fences.
Format: {{"triples": [["head", "relation", "tail"], ...], "confidence": 8}}

Text:
{text}"""


def _extract_curated(
    text: str,
    doc_id: str,
    model: str,
    ollama_url: str,
    timeout: int,
    num_predict: int,
) -> Tuple[List[Tuple[str, str, str]], int, str]:
    """
    Call LLM and return (triples, confidence, raw_response).
    Falls back to regex on LLM failure with confidence=0.
    """
    import requests

    clean_text = _html.unescape(text)
    prompt = _CURATED_PROMPT.format(text=clean_text[:1200])
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.05, "num_predict": max(128, num_predict)},
                "think": False,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        # Strip <think>...</think> blocks (qwen3 chain-of-thought)
        raw_clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw_clean = re.sub(r"^```(?:json)?\s*", "", raw_clean, flags=re.M)
        raw_clean = re.sub(r"\s*```$", "", raw_clean, flags=re.M).strip()
        if not raw_clean:
            raise ValueError("empty_response_after_strip")
        parsed = json.loads(raw_clean)
        confidence = min(10, max(0, int(parsed.get("confidence", 0))))
        triples_raw = parsed.get("triples", [])
        triples: List[Tuple[str, str, str]] = []
        for item in triples_raw:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                h, r, t = [str(x).lower().strip() for x in item]
                if h and r and t and h != t and is_valid_entity(h) and is_valid_entity(t):
                    triples.append((h, r, t))
        return triples[:10], confidence, raw[:500]
    except Exception as exc:
        log.debug("[curated] LLM failed for %s: %s — falling back to regex", doc_id, exc)
        fallback = extract_triples_regex(text, doc_id)
        return fallback, 0, f"llm_error:{exc}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_curated_enrichment(
    rag_dir: str,
    collection_name: str = "apollo_financial",
    max_chunks: int = 400,
    batch_size: int = 20,
    model: str = "qwen3-vl:32b",
    ollama_url: str = "http://127.0.0.1:11434",
    llm_timeout: int = 90,
    llm_num_predict: int = 400,
    confidence_threshold: int = 7,
    log_dir: Optional[str] = None,
    max_sample_examples: int = 5,
) -> dict:
    import chromadb
    from chromadb.config import Settings

    rag_dir = os.path.abspath(rag_dir)
    kg_path = os.path.join(rag_dir, "financial_kg.json")
    now_utc = datetime.now(timezone.utc)
    run_id = f"curated_{now_utc.strftime('%Y%m%d_%H%M%S')}"

    if log_dir:
        log_dir = os.path.abspath(log_dir)
        os.makedirs(log_dir, exist_ok=True)
        run_log_path = os.path.join(log_dir, "run_log.json")
    else:
        run_log_path = None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    print(f"[curated] run_id={run_id}")
    print(f"[curated] model={model}  threshold={confidence_threshold}  max_chunks={max_chunks}")
    print(f"[curated] rag_dir={rag_dir}")

    client = chromadb.PersistentClient(
        path=rag_dir,
        settings=Settings(allow_reset=False, anonymized_telemetry=False),
    )
    col = client.get_collection(collection_name)
    total_chunks = col.count()
    print(f"[curated] Total chunks in collection: {total_chunks:,}")

    kg = FinancialKG(kg_path)
    stats_before = kg.stats()
    print(f"[curated] KG before: nodes={stats_before['nodes']:,}  edges={stats_before['edges']:,}  indexed={stats_before['indexed_docs']:,}")

    # Fetch all IDs
    all_ids: List[str] = col.get(include=[]).get("ids", [])

    # Candidate pool: not yet LLM-indexed
    candidate_ids = [i for i in all_ids if not kg.is_llm_indexed(i)]
    print(f"[curated] Candidates (not LLM-indexed): {len(candidate_ids):,}")

    if not candidate_ids:
        print("[curated] Nothing to do — all chunks already LLM-indexed.")
        result = _build_result(
            run_id=run_id, model=model, confidence_threshold=confidence_threshold,
            total_chunks=total_chunks, candidates=0, scheduled=0,
            processed=0, accepted=0, rejected_confidence=0, rejected_dedup=0,
            triples_added=0, elapsed_sec=0.0,
            stats_before=stats_before, stats_after=stats_before,
            samples_accepted=[], samples_rejected=[],
        )
        if run_log_path:
            Path(run_log_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # Score and sort candidates by priority (fetch metadata in batches)
    print("[curated] Scoring candidates by priority...")
    scored: List[Tuple[int, str]] = []
    for i in range(0, len(candidate_ids), 500):
        batch = candidate_ids[i:i + 500]
        fetched = col.get(ids=batch, include=["metadatas"])
        metas = fetched.get("metadatas") or []
        for j, doc_id in enumerate(batch):
            meta = metas[j] if j < len(metas) and isinstance(metas[j], dict) else {}
            score = _priority_score(meta, now_utc)
            scored.append((-score, doc_id))  # negative for descending sort

    scored.sort()
    scheduled_ids = [doc_id for _, doc_id in scored[:max_chunks]]
    print(f"[curated] Scheduled for enrichment: {len(scheduled_ids):,}")

    # Process in batches
    processed = 0
    accepted = 0
    rejected_confidence = 0
    rejected_dedup = 0
    triples_added = 0
    samples_accepted: List[dict] = []
    samples_rejected: List[dict] = []
    t0 = time.time()

    for batch_start in range(0, len(scheduled_ids), batch_size):
        batch_ids = scheduled_ids[batch_start:batch_start + batch_size]
        fetched = col.get(ids=batch_ids, include=["documents", "metadatas"])
        docs = fetched.get("documents") or []
        metas = fetched.get("metadatas") or []

        for j, doc_id in enumerate(batch_ids):
            text = str(docs[j] if j < len(docs) else "")
            meta = metas[j] if j < len(metas) and isinstance(metas[j], dict) else {}
            if not text.strip() or _is_xbrl_boilerplate(text):
                kg.mark_llm_indexed(doc_id)
                processed += 1
                continue

            kind = str(meta.get("kind") or "unknown")
            priority = _priority_score(meta, now_utc)

            triples_raw, confidence, raw_resp = _extract_curated(
                text, doc_id, model, ollama_url, llm_timeout, llm_num_predict,
            )

            # Confidence gate
            if confidence < confidence_threshold and confidence > 0:
                rejected_confidence += 1
                processed += 1
                kg.mark_llm_indexed(doc_id)
                if len(samples_rejected) < max_sample_examples:
                    samples_rejected.append({
                        "doc_id": doc_id,
                        "kind": kind,
                        "priority_score": priority,
                        "confidence": confidence,
                        "reject_reason": "low_confidence",
                        "triples_proposed": [list(t) for t in triples_raw[:3]],
                        "text_excerpt": text[:200],
                    })
                continue

            # Semantic dedup: filter triples already well-covered
            new_triples = []
            deduped = []
            for h, r, t in triples_raw:
                if _is_dedup_covered(kg, h, t):
                    deduped.append((h, r, t))
                else:
                    new_triples.append((h, r, t))

            if not new_triples and deduped:
                rejected_dedup += 1
                processed += 1
                kg.mark_llm_indexed(doc_id)
                if len(samples_rejected) < max_sample_examples:
                    samples_rejected.append({
                        "doc_id": doc_id,
                        "kind": kind,
                        "priority_score": priority,
                        "confidence": confidence,
                        "reject_reason": "all_triples_deduped",
                        "triples_deduped": [list(t) for t in deduped[:3]],
                        "text_excerpt": text[:200],
                    })
                continue

            # Accept
            if new_triples:
                kg.add_triples(doc_id, new_triples, llm=True)
                triples_added += len(new_triples)
            else:
                kg.mark_llm_indexed(doc_id)

            accepted += 1
            processed += 1

            if len(samples_accepted) < max_sample_examples:
                samples_accepted.append({
                    "doc_id": doc_id,
                    "kind": kind,
                    "priority_score": priority,
                    "confidence": confidence,
                    "triples_added": [list(t) for t in new_triples[:5]],
                    "triples_deduped": [list(t) for t in deduped[:2]],
                    "text_excerpt": text[:200],
                })

        # Checkpoint after each batch
        kg.save()
        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        pct = processed / len(scheduled_ids) * 100
        print(
            f"  [{pct:5.1f}%] processed={processed}  accepted={accepted}  "
            f"triples={triples_added:,}  rej_conf={rejected_confidence}  "
            f"rej_dedup={rejected_dedup}  {rate:.1f} docs/s"
        )

    stats_after = kg.stats()
    elapsed_sec = round(time.time() - t0, 2)

    print(f"\n[curated] Done in {elapsed_sec:.0f}s")
    print(f"  Processed: {processed}  Accepted: {accepted}")
    print(f"  Triples added: {triples_added:,}")
    print(f"  KG: nodes={stats_after['nodes']:,}  edges={stats_after['edges']:,}")

    result = _build_result(
        run_id=run_id, model=model, confidence_threshold=confidence_threshold,
        total_chunks=total_chunks, candidates=len(candidate_ids),
        scheduled=len(scheduled_ids), processed=processed, accepted=accepted,
        rejected_confidence=rejected_confidence, rejected_dedup=rejected_dedup,
        triples_added=triples_added, elapsed_sec=elapsed_sec,
        stats_before=stats_before, stats_after=stats_after,
        samples_accepted=samples_accepted, samples_rejected=samples_rejected,
    )

    if run_log_path:
        Path(run_log_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[curated] Run log written: {run_log_path}")

    return result


def _build_result(
    run_id: str, model: str, confidence_threshold: int,
    total_chunks: int, candidates: int, scheduled: int,
    processed: int, accepted: int, rejected_confidence: int, rejected_dedup: int,
    triples_added: int, elapsed_sec: float,
    stats_before: dict, stats_after: dict,
    samples_accepted: list, samples_rejected: list,
) -> dict:
    return {
        "ok": True,
        "run_id": run_id,
        "model": model,
        "confidence_threshold": confidence_threshold,
        "total_chunks": total_chunks,
        "candidates_llm_unindexed": candidates,
        "scheduled": scheduled,
        "processed": processed,
        "accepted": accepted,
        "rejected_low_confidence": rejected_confidence,
        "rejected_dedup": rejected_dedup,
        "triples_added": triples_added,
        "elapsed_sec": elapsed_sec,
        "kg_before": stats_before,
        "kg_after": stats_after,
        "nodes_delta": stats_after.get("nodes", 0) - stats_before.get("nodes", 0),
        "edges_delta": stats_after.get("edges", 0) - stats_before.get("edges", 0),
        "sample_accepted": samples_accepted,
        "sample_rejected": samples_rejected,
        "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Curated Apollo KG enrichment")
    parser.add_argument("--rag-dir", default="./chroma_db")
    parser.add_argument("--collection", default="apollo_financial")
    parser.add_argument("--max-chunks", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--model", default=os.getenv("APOLLO_CURATED_LLM_MODEL", "qwen3-vl:32b"))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--llm-timeout", type=int, default=int(os.getenv("APOLLO_CURATED_LLM_TIMEOUT_SECS", "90")))
    parser.add_argument("--llm-num-predict", type=int, default=400)
    parser.add_argument("--confidence-threshold", type=int,
                        default=int(os.getenv("APOLLO_CURATED_CONFIDENCE_THRESHOLD", "7")))
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()

    result = run_curated_enrichment(
        rag_dir=args.rag_dir,
        collection_name=args.collection,
        max_chunks=args.max_chunks,
        batch_size=args.batch_size,
        model=args.model,
        ollama_url=args.ollama_url,
        llm_timeout=args.llm_timeout,
        llm_num_predict=args.llm_num_predict,
        confidence_threshold=args.confidence_threshold,
        log_dir=args.log_dir,
    )
    print(json.dumps({
        k: result[k] for k in (
            "ok", "run_id", "model", "processed", "accepted",
            "triples_added", "nodes_delta", "edges_delta", "elapsed_sec",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
