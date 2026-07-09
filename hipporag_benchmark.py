from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_QUERIES = [
    {
        "query": "swing trading pullback risk management stop loss",
        "expected_terms": ["risk", "stop", "pullback", "swing"],
    },
    {
        "query": "breakout volume confirmation and relative strength",
        "expected_terms": ["breakout", "volume", "relative", "strength"],
    },
    {
        "query": "market regime risk off position sizing",
        "expected_terms": ["market", "regime", "risk", "position"],
    },
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _score_hits(hits: Iterable[Dict[str, Any]], expected_terms: Iterable[str]) -> float:
    terms = [str(term or "").strip().lower() for term in expected_terms if str(term or "").strip()]
    if not terms:
        return 0.0
    haystack = "\n".join(str(hit.get("text") or "") for hit in hits).lower()
    matched = sum(1 for term in terms if term in haystack)
    return round(matched / len(terms), 4)


def _graph_stats(graph_path: Optional[Path]) -> Dict[str, Any]:
    if not graph_path or not graph_path.exists():
        return {"available": False, "nodes": 0, "edges": 0, "fact_edges": 0}
    try:
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
        nodes = list(payload.get("nodes") or [])
        edges = list(payload.get("edges") or [])
        fact_edges = sum(1 for row in edges if isinstance(row, dict) and row.get("relation") == "has_fact")
        return {
            "available": True,
            "nodes": len(nodes),
            "edges": len(edges),
            "fact_edges": fact_edges,
            "graph_path": str(graph_path),
        }
    except Exception as exc:
        return {"available": False, "nodes": 0, "edges": 0, "fact_edges": 0, "error": str(exc)}


def run_benchmark(
    *,
    retriever: Any = None,
    queries: Optional[List[Dict[str, Any]]] = None,
    graph_path: Optional[Path] = None,
    top_k: int = 8,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    query_set = list(queries or DEFAULT_QUERIES)
    for item in query_set:
        query = str(item.get("query") or "").strip()
        expected = list(item.get("expected_terms") or [])
        dense_hits: List[Dict[str, Any]] = []
        full_hits: List[Dict[str, Any]] = []
        graph_hits: List[Dict[str, Any]] = []
        if retriever is not None:
            dense_fn = getattr(retriever, "_dense_query", None)
            search_fn = getattr(retriever, "search", None)
            fetch_fn = getattr(retriever, "_fetch_by_ids", None)
            try:
                if callable(dense_fn):
                    dense_hits = list(dense_fn(query, top_k))
            except Exception:
                dense_hits = []
            try:
                if callable(search_fn):
                    full_hits = list(search_fn(query, top_k=top_k))
            except Exception:
                full_hits = []
            try:
                kg = getattr(retriever, "kg", None)
                graph = getattr(kg, "g", None)
                if graph is not None and callable(fetch_fn):
                    from Apollo.apollo_hipporag.entity_extractor import extract_entities_regex
                    from Apollo.apollo_hipporag.ppr import ppr_expand

                    seed_text = "\n".join(str(hit.get("text") or "") for hit in dense_hits[:3])
                    seeds = extract_entities_regex(seed_text)
                    graph_ids = list(ppr_expand(graph, seeds, top_k_entities=20))
                    graph_hits = list(fetch_fn(graph_ids[:top_k], boost=0.0))
            except Exception:
                graph_hits = []
        dense_score = _score_hits(dense_hits, expected)
        full_score = _score_hits(full_hits, expected)
        graph_score = _score_hits(graph_hits, expected)
        rows.append(
            {
                "query": query,
                "expected_terms": expected,
                "dense_score": dense_score,
                "dense_graph_score": full_score,
                "graph_only_score": graph_score,
                "dense_hit_count": len(dense_hits),
                "dense_graph_hit_count": len(full_hits),
                "graph_only_hit_count": len(graph_hits),
                "graph_added_value": round(full_score - dense_score, 4),
            }
        )
    dense_avg = round(sum(row["dense_score"] for row in rows) / max(1, len(rows)), 4)
    full_avg = round(sum(row["dense_graph_score"] for row in rows) / max(1, len(rows)), 4)
    graph_avg = round(sum(row["graph_only_score"] for row in rows) / max(1, len(rows)), 4)
    mode = "graph_augmented" if full_avg >= dense_avg else "supporting_context_only"
    return {
        "ok": True,
        "completed_at": _utc_iso(),
        "query_count": len(rows),
        "dense_only_avg": dense_avg,
        "dense_graph_avg": full_avg,
        "graph_only_avg": graph_avg,
        "hipporag_label": mode,
        "fail_soft": mode == "supporting_context_only",
        "rows": rows,
        "kg_health": _graph_stats(graph_path),
    }


def load_live_retriever() -> tuple[Any, Optional[Path]]:
    apollo_root = Path(__file__).resolve().parent
    if str(apollo_root) not in sys.path:
        sys.path.insert(0, str(apollo_root))
    from Apollo.corpus_live_ingest import _DEFAULT_COLLECTION, _DEFAULT_RAG_DIR, _get_client

    graph_path = Path(_DEFAULT_RAG_DIR) / "financial_kg.json"
    client = _get_client(_DEFAULT_RAG_DIR)
    collection = client.get_or_create_collection(_DEFAULT_COLLECTION)
    from Apollo.apollo_hipporag.retriever import HippoRetriever

    return HippoRetriever.load(collection, str(graph_path)), graph_path


def run_live_benchmark() -> Dict[str, Any]:
    try:
        retriever, graph_path = load_live_retriever()
        return run_benchmark(retriever=retriever, graph_path=graph_path)
    except Exception as exc:
        return {
            "ok": True,
            "completed_at": _utc_iso(),
            "skipped": True,
            "reason": f"live_retriever_unavailable:{type(exc).__name__}:{exc}",
            "hipporag_label": "supporting_context_only",
            "fail_soft": True,
            "rows": [],
            "kg_health": _graph_stats(None),
        }
