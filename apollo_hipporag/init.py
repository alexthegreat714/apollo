"""
apollo_hipporag.init

Architecture notes (HippoRAG 2):
  - Ingest/build time:
      Chroma chunk -> entity extraction (regex + optional gx10 LLM) -> (head, relation, tail) triples -> KG JSON
  - Query time:
      query -> dense retrieval -> Personalized PageRank (PPR) expansion over KG -> merged ranked hits

Files:
  - apollo_hipporag/entity_extractor.py: Regex NER + optional gx10 triple extraction (Ollama)
  - apollo_hipporag/graph_store.py: NetworkX DiGraph with JSON persistence
  - apollo_hipporag/ppr.py: Personalized PageRank expansion
  - apollo_hipporag/retriever.py: Dense -> PPR -> merge orchestrator
  - apollo_hipporag/build_graph.py: Batch job to process the existing financial corpus collection

Integration:
  Apollo/app.py loads a Chroma collection for the static financial corpus and attempts
  HippoRetriever.search() first. If HippoRAG is unavailable or errors, Apollo falls back
  to a flat ChromaDB query.
"""

from __future__ import annotations

from apollo_hipporag.retriever import HippoRetriever

__all__ = ["HippoRetriever"]

