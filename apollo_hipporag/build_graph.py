"""
apollo_hipporag.build_graph — Batch entity extraction for the financial KG.

Reads all chunks from the apollo_financial ChromaDB collection, extracts
knowledge graph triples, and saves the graph as financial_kg.json alongside
the ChromaDB directory.

Usage:
    python -m apollo_hipporag.build_graph [--use-llm] [--limit N]

Options:
    --use-llm   Use gx10 for richer extraction (slower; requires Ollama running)
    --limit N   Process only the first N chunks (for testing)
    --rag-dir   ChromaDB directory (default: ./chroma_db)
    --collection  Collection name (default: apollo_financial)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as __main__ from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_hipporag.entity_extractor import extract_triples
from apollo_hipporag.graph_store import FinancialKG

_FINANCE_RELEVANCE_TOKENS = (
    "trading",
    "trade",
    "margin",
    "pattern day trader",
    "pdt",
    "risk",
    "position",
    "slippage",
    "transaction",
    "volatility",
    "liquidity",
    "stock",
    "equity",
    "account",
    "securities",
    "broker",
    "dealer",
    "market",
    "portfolio",
    "capital",
    "leverage",
    # broader finance terms covering education/retirement/tax corpus chunks
    "investment",
    "invest",
    "fund",
    "dividend",
    "interest",
    "bond",
    "asset",
    "income",
    "return",
    "tax",
    "retirement",
    "savings",
    "allocation",
    "diversification",
    "rebalancing",
    "inflation",
    "ira",
    "401k",
    "roth",
    "hedge",
    "option",
    "etf",
    "index",
    "yield",
    "expense",
    "financial",
    "debt",
    "loan",
    "compound",
    "withdrawal",
)

# Metadata kinds that indicate the chunk is unambiguously from a finance corpus.
_FINANCE_CORPUS_KINDS = frozenset({
    "education", "news", "projection", "report", "regulation",
    "research", "analysis", "filing", "news_feed", "trading_research_raw",
    "sec_filings", "equity_news", "earnings_calendar",
    "fundamentals", "analyst_targets", "macro_snapshot",
})
_QUARANTINE_FILENAME = "financial_quarantine.json"
_QUARANTINE_RETRY_SEC = int(os.getenv("APOLLO_HIPPORAG_QUARANTINE_RETRY_SEC", "21600"))
_QUARANTINE_MAX_ATTEMPTS = int(os.getenv("APOLLO_HIPPORAG_QUARANTINE_MAX_ATTEMPTS", "6"))

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


def _is_finance_relevant(text: str, meta: dict | None) -> bool:
    kind = str((meta or {}).get("kind") or "").lower().strip()
    if kind in _FINANCE_CORPUS_KINDS:
        return True
    title = str((meta or {}).get("title") or "")
    source = str((meta or {}).get("source") or "")
    url = str((meta or {}).get("url") or "")
    hay = f"{title}\n{source}\n{kind}\n{url}\n{text or ''}".lower()
    hits = sum(1 for token in _FINANCE_RELEVANCE_TOKENS if token in hay)
    if hits >= 2:
        return True
    if any(tag in hay for tag in ("finra", "sec.gov", "investor.gov", "cftc", "federal reserve", "regulation t")):
        return True
    return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_quarantine(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_quarantine(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def _quarantine_entry_active(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    cooldown_until = str(entry.get("cooldown_until") or "").strip()
    if not cooldown_until:
        return False
    try:
        dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
    except Exception:
        return False
    return dt.astimezone(timezone.utc) > _utc_now()


def build_graph(
    rag_dir: str,
    collection_name: str,
    use_llm: bool = False,
    llm_only: bool = False,
    limit: int | None = None,
    max_new_chunks: int | None = None,
    batch_size: int = 50,
    llm_model: str = "gx10:latest",
    ollama_url: str = "http://127.0.0.1:11434",
    llm_timeout: int = 30,
    llm_num_predict: int = 300,
) -> dict:
    import chromadb
    from chromadb.config import Settings

    rag_dir = os.path.abspath(rag_dir)
    kg_path = os.path.join(rag_dir, "financial_kg.json")
    quarantine_path = os.path.join(rag_dir, _QUARANTINE_FILENAME)

    print(f"[build_graph] ChromaDB: {rag_dir}")
    print(f"[build_graph] Collection: {collection_name}")
    print(f"[build_graph] KG output: {kg_path}")
    print(f"[build_graph] LLM extraction: {'yes (gx10)' if use_llm else 'no (regex only)'}")
    if use_llm:
        print(f"[build_graph] LLM model: {llm_model}")
        print(f"[build_graph] Ollama URL: {ollama_url}")
        print(f"[build_graph] LLM timeout: {llm_timeout}s")

    client = chromadb.PersistentClient(
        path=rag_dir,
        settings=Settings(allow_reset=False, anonymized_telemetry=False),
    )
    try:
        col = client.get_collection(collection_name)
    except Exception as exc:
        print(f"[build_graph] ERROR: Could not open collection '{collection_name}': {exc}")
        sys.exit(1)

    total = col.count()
    print(f"[build_graph] Total chunks: {total}")

    kg = FinancialKG(kg_path)
    quarantine = _load_quarantine(quarantine_path)
    already_indexed = len(kg._indexed_doc_ids)
    print(f"[build_graph] Already indexed: {already_indexed}")

    # Fetch all IDs so we can page
    all_ids_result = col.get(include=[])
    all_ids: list[str] = all_ids_result.get("ids", [])

    if limit:
        all_ids = all_ids[:limit]

    # Skip already indexed (llm_only mode targets regex-only backlog)
    if llm_only:
        all_new_ids = [id_ for id_ in all_ids if not kg.is_llm_indexed(id_)]
    else:
        all_new_ids = [id_ for id_ in all_ids if not kg.is_indexed(id_)]
    eligible_ids = [doc_id for doc_id in all_new_ids if not _quarantine_entry_active(quarantine.get(doc_id))]
    pending_quarantine_before = max(0, len(all_new_ids) - len(eligible_ids))
    remaining_before = len(eligible_ids)
    new_ids = eligible_ids[: max_new_chunks or len(eligible_ids)]
    print(f"[build_graph] New chunks to process: {remaining_before}")
    if pending_quarantine_before > 0:
        print(f"[build_graph] Deferred by quarantine: {pending_quarantine_before}")
    if max_new_chunks is not None:
        print(f"[build_graph] Step budget: {len(new_ids)} chunks")
    if not all_new_ids:
        print("[build_graph] Nothing to do.")
        stats = kg.stats()
        return {
            "ok": True,
            "rag_dir": rag_dir,
            "collection": collection_name,
            "kg_path": kg_path,
            "use_llm": bool(use_llm),
            "llm_model": llm_model if use_llm else "",
            "total_chunks": total,
            "new_chunks": 0,
            "remaining_before": 0,
            "remaining_after": 0,
            "completed": True,
            "processed": 0,
            "processed_finance": 0,
            "skipped": 0,
            "skipped_non_finance": 0,
            "requeued_low_quality": 0,
            "triples": 0,
            "validated_non_triple_finance": 0,
            "stats": stats,
            "elapsed_sec": 0.0,
            "pending_quarantine": pending_quarantine_before,
        }

    processed = 0
    processed_finance = 0
    skipped = 0
    skipped_non_finance = 0
    requeued_low_quality = 0
    validated_non_triple_finance = 0
    total_triples = 0
    t0 = time.time()

    for batch_start in range(0, len(new_ids), batch_size):
        batch_ids = new_ids[batch_start: batch_start + batch_size]

        result = col.get(ids=batch_ids, include=["documents", "metadatas"])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])

        for doc_id, text, meta in zip(batch_ids, docs, metas):
            if not text or not text.strip():
                skipped += 1
                continue
            if not _is_finance_relevant(text, meta if isinstance(meta, dict) else {}):
                skipped += 1
                skipped_non_finance += 1
                requeued_low_quality += 1
                prev = quarantine.get(doc_id) if isinstance(quarantine.get(doc_id), dict) else {}
                attempts = int(prev.get("attempts") or 0) + 1
                cooldown = max(600, _QUARANTINE_RETRY_SEC * (2 if attempts >= _QUARANTINE_MAX_ATTEMPTS else 1))
                quarantine[doc_id] = {
                    "reason": "non_finance_relevance",
                    "attempts": attempts,
                    "last_seen": _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "cooldown_until": (_utc_now() + timedelta(seconds=cooldown)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                }
                continue

            triples = extract_triples(
                text,
                doc_id,
                use_llm=use_llm,
                model=llm_model,
                ollama_url=ollama_url,
                timeout=llm_timeout,
                num_predict=llm_num_predict,
            )
            if triples:
                kg.add_triples(doc_id, triples, llm=bool(use_llm))
                total_triples += len(triples)
            else:
                if use_llm:
                    kg.mark_llm_indexed(doc_id)
                else:
                    kg._indexed_doc_ids.add(doc_id)
                validated_non_triple_finance += 1

            processed += 1
            processed_finance += 1
            quarantine.pop(doc_id, None)

        # Save checkpoint after each batch
        kg.save()
        _save_quarantine(quarantine_path, quarantine)
        elapsed = time.time() - t0
        pct = (processed + skipped) / len(new_ids) * 100
        rate = processed / elapsed if elapsed > 0 else 0
        print(
            f"  [{pct:5.1f}%] {processed:,} processed, {skipped} skipped, "
            f"{total_triples:,} triples | {rate:.1f} docs/s"
        )

    pruned_low_quality_nodes = kg.prune_low_quality_nodes()
    if pruned_low_quality_nodes:
        kg.save()
        print(f"[build_graph] Pruned low-quality nodes: {pruned_low_quality_nodes}")
    stats = kg.stats()
    print(f"\n[build_graph] Done.")
    print(f"  Nodes: {stats['nodes']:,}")
    print(f"  Edges: {stats['edges']:,}")
    print(f"  Indexed docs: {stats['indexed_docs']:,}")
    print(f"  New triples extracted: {total_triples:,}")
    print(f"  Elapsed: {time.time() - t0:.1f}s")
    all_new_ids_after = [id_ for id_ in all_ids if not kg.is_indexed(id_)]
    eligible_after = [doc_id for doc_id in all_new_ids_after if not _quarantine_entry_active(quarantine.get(doc_id))]
    remaining_after = len(eligible_after)
    pending_quarantine_after = max(0, len(all_new_ids_after) - remaining_after)
    return {
        "ok": True,
        "rag_dir": rag_dir,
        "collection": collection_name,
        "kg_path": kg_path,
        "use_llm": bool(use_llm),
        "llm_model": llm_model if use_llm else "",
        "total_chunks": total,
        "new_chunks": len(new_ids),
        "remaining_before": remaining_before,
        "remaining_after": remaining_after,
        "completed": len(all_new_ids_after) == 0,
        "processed": processed,
        "processed_finance": processed_finance,
        "skipped": skipped,
        "skipped_non_finance": skipped_non_finance,
        "requeued_low_quality": requeued_low_quality,
        "triples": total_triples,
        "validated_non_triple_finance": validated_non_triple_finance,
        "pending_quarantine": pending_quarantine_after,
        "pruned_low_quality_nodes": pruned_low_quality_nodes,
        "stats": stats,
        "elapsed_sec": round(time.time() - t0, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Apollo financial knowledge graph")
    parser.add_argument("--use-llm", action="store_true", help="Use gx10 for LLM extraction")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N chunks")
    parser.add_argument("--max-new-chunks", type=int, default=None, help="Process at most N unindexed chunks this run")
    parser.add_argument("--rag-dir", default="./chroma_db", help="ChromaDB directory")
    parser.add_argument("--collection", default="apollo_financial", help="Collection name")
    parser.add_argument("--batch-size", type=int, default=50, help="Chunks per save cycle")
    parser.add_argument("--llm-only", action="store_true",
        help="Process chunks not yet LLM-indexed (backlog enrichment; use with --use-llm)")
    parser.add_argument("--llm-model", default=os.getenv("APOLLO_HIPPORAG_LLM_MODEL", "gx10:latest"))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--llm-timeout", type=int, default=int(os.getenv("APOLLO_HIPPORAG_LLM_TIMEOUT_SECS", "30")))
    parser.add_argument("--llm-num-predict", type=int, default=int(os.getenv("APOLLO_HIPPORAG_LLM_NUM_PREDICT", "300")))
    args = parser.parse_args()

    result = build_graph(
        rag_dir=args.rag_dir,
        collection_name=args.collection,
        use_llm=args.use_llm,
        llm_only=args.llm_only,
        limit=args.limit,
        max_new_chunks=args.max_new_chunks,
        batch_size=args.batch_size,
        llm_model=args.llm_model,
        ollama_url=args.ollama_url,
        llm_timeout=args.llm_timeout,
        llm_num_predict=args.llm_num_predict,
    )
    print(f"[build_graph] Result: {result}")


if __name__ == "__main__":
    main()
