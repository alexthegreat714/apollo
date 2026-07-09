"""
apollo_hipporag.ppr — Personalized PageRank expansion on the financial KG.

Given a set of seed entities (from dense retrieval), runs PPR on the graph
to find related entities and their chunk IDs.

NetworkX's pagerank() is fast enough for graphs <<100k nodes. We use the
personalization dict to bias the random walk toward seed entities.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Set

import networkx as nx

log = logging.getLogger(__name__)


def run_ppr(
    graph: nx.DiGraph,
    seed_entities: List[str],
    alpha: float = 0.85,
    top_k_entities: int = 20,
) -> List[str]:
    """
    Run Personalized PageRank starting from seed_entities.

    Parameters
    ----------
    graph : nx.DiGraph
        The financial knowledge graph.
    seed_entities : list[str]
        Entity strings (lowercase) to bias the walk toward.
        Entities not in the graph are silently skipped.
    alpha : float
        Damping factor (standard PPR: 0.85).
    top_k_entities : int
        How many top-ranked entities to return.

    Returns
    -------
    list[str]
        Top entities ranked by PPR score, including seeds.
    """
    if graph.number_of_nodes() == 0:
        return []

    # Filter to entities actually in the graph
    valid_seeds = [e for e in seed_entities if e in graph.nodes]
    if not valid_seeds:
        log.debug("[ppr] No seed entities found in graph (tried %d)", len(seed_entities))
        return []

    # Build personalization dict — uniform weight over valid seeds
    seed_weight = 1.0 / len(valid_seeds)
    personalization: Dict[str, float] = {e: seed_weight for e in valid_seeds}

    try:
        scores = nx.pagerank(
            graph,
            alpha=alpha,
            personalization=personalization,
            max_iter=100,
            tol=1e-6,
        )
    except nx.PowerIterationFailedConvergence:
        log.warning("[ppr] PageRank did not converge; using partial scores")
        scores = nx.pagerank(
            graph,
            alpha=alpha,
            personalization=personalization,
            max_iter=50,
            tol=1e-4,
        )

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [entity for entity, _ in ranked[:top_k_entities]]


def entities_to_doc_ids(
    graph: nx.DiGraph,
    entities: List[str],
) -> Set[str]:
    """Return all chunk IDs associated with the given entity list."""
    doc_ids: Set[str] = set()
    for entity in entities:
        if entity in graph.nodes:
            doc_ids.update(graph.nodes[entity].get("doc_ids", set()))
    return doc_ids


def ppr_expand(
    graph: nx.DiGraph,
    seed_entities: List[str],
    alpha: float = 0.85,
    top_k_entities: int = 20,
) -> Set[str]:
    """
    Convenience wrapper: seed entities → PPR → chunk IDs.

    Returns the union of doc_ids for the top PPR-ranked entities.
    """
    if not seed_entities or graph.number_of_nodes() == 0:
        return set()

    top_entities = run_ppr(graph, seed_entities, alpha=alpha, top_k_entities=top_k_entities)
    return entities_to_doc_ids(graph, top_entities)
