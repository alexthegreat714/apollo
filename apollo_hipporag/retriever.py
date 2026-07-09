"""
apollo_hipporag.retriever — HippoRAG 2 retrieval orchestrator.

Pipeline (query time):
  1. Dense retrieval     — ChromaDB top-k hits
  2. Entity extraction   — pull entity strings from top hits' text
  3. PPR expansion       — walk the KG from those seed entities
  4. KG-expanded fetch   — pull any additional chunks surfaced by PPR
  5. Merge + re-rank     — combine dense + KG chunks, score, deduplicate

The result is a ranked list of hit dicts identical in shape to what
_query_financial_corpus() already returns, so it's a drop-in replacement.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apollo_hipporag.entity_extractor import extract_entities_regex
from apollo_hipporag.graph_store import FinancialKG
from apollo_hipporag.ppr import ppr_expand

log = logging.getLogger(__name__)

# Boost applied to KG-expanded chunks that weren't in the dense top-k
_KG_BOOST = 0.05


class HippoRetriever:
    """
    HippoRAG 2 retriever wrapping a ChromaDB collection + FinancialKG.

    Parameters
    ----------
    chroma_col : chromadb.Collection
        The apollo_financial ChromaDB collection.
    kg : FinancialKG
        The loaded knowledge graph.
    """

    def __init__(self, chroma_col: Any, kg: FinancialKG) -> None:
        self.col = chroma_col
        self.kg = kg

    @classmethod
    def load(cls, chroma_col: Any, graph_path: str) -> "HippoRetriever":
        """Construct from a ChromaDB collection + path to the KG JSON file."""
        kg = FinancialKG(graph_path)
        return cls(chroma_col, kg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 8) -> List[Dict]:
        """
        Run the full HippoRAG 2 pipeline and return ranked hits.

        Each hit dict:
            text   : str
            score  : float  (0-1, higher = better)
            meta   : dict
            source : "hipporag"
        """
        self.kg.reload_if_changed()
        # Step 1: dense retrieval
        dense_hits = self._dense_query(query, top_k=top_k)
        if not dense_hits:
            return []

        # Step 2: extract seed entities from top dense hits
        top_texts = [h["text"] for h in dense_hits[:min(5, len(dense_hits))]]
        seed_entities = self._collect_entities(top_texts)

        # Step 3: PPR expansion → additional doc IDs
        kg_doc_ids = ppr_expand(self.kg.g, seed_entities, top_k_entities=30)

        # Step 4: fetch KG-expanded chunks not already in dense results
        dense_ids = {h["meta"].get("id", "") for h in dense_hits}
        new_ids = kg_doc_ids - dense_ids
        kg_hits = self._fetch_by_ids(list(new_ids), boost=_KG_BOOST) if new_ids else []

        # Step 5: merge and re-rank
        merged = self._merge(dense_hits, kg_hits, top_k=top_k)
        log.debug(
            "[hipporag] query=%r dense=%d kg_expanded=%d merged=%d",
            query[:60], len(dense_hits), len(kg_hits), len(merged),
        )
        return merged

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dense_query(self, query: str, top_k: int) -> List[Dict]:
        """Query ChromaDB and return hit dicts."""
        try:
            count = self.col.count()
            if count == 0:
                return []
            n = min(top_k, count)
            res = self.col.query(
                query_texts=[query],
                n_results=n,
                include=["documents", "distances", "metadatas"],
            )
            docs = res.get("documents", [[]])[0]
            dists = res.get("distances", [[]])[0]
            metas = res.get("metadatas", [[]])[0]
            hits = []
            for text, dist, meta in zip(docs, dists, metas):
                if not text:
                    continue
                hits.append({
                    "text": text,
                    "score": round(max(0.0, 1.0 - dist), 4),
                    "meta": meta or {},
                    "source": "hipporag",
                })
            return hits
        except Exception as exc:
            log.warning("[hipporag] Dense query failed: %s", exc)
            return []

    def _collect_entities(self, texts: List[str]) -> List[str]:
        """Extract and deduplicate entities from a list of text passages."""
        seen = set()
        entities = []
        for text in texts:
            for ent in extract_entities_regex(text):
                if ent not in seen:
                    seen.add(ent)
                    entities.append(ent)
        return entities

    def _fetch_by_ids(self, doc_ids: List[str], boost: float = 0.0) -> List[Dict]:
        """Fetch specific chunks from ChromaDB by their IDs."""
        if not doc_ids:
            return []
        try:
            res = self.col.get(
                ids=doc_ids,
                include=["documents", "metadatas"],
            )
            docs = res.get("documents", [])
            metas = res.get("metadatas", [])
            hits = []
            for text, meta in zip(docs, metas):
                if not text:
                    continue
                hits.append({
                    "text": text,
                    "score": round(boost, 4),
                    "meta": meta or {},
                    "source": "hipporag_kg",
                })
            return hits
        except Exception as exc:
            log.debug("[hipporag] ID fetch failed (%s); skipping KG expansion", exc)
            return []

    def _merge(
        self,
        dense: List[Dict],
        kg_extra: List[Dict],
        top_k: int,
    ) -> List[Dict]:
        """Deduplicate by leading 120 chars, keep highest score, sort desc."""
        seen: Dict[str, Dict] = {}
        for hit in dense + kg_extra:
            key = (hit.get("text") or "")[:120].strip()
            if not key:
                continue
            if key not in seen or (hit.get("score") or 0) > (seen[key].get("score") or 0):
                seen[key] = hit
        ranked = sorted(seen.values(), key=lambda h: h.get("score") or 0, reverse=True)
        return ranked[:top_k]
