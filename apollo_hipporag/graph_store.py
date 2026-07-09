"""
apollo_hipporag.graph_store — NetworkX knowledge graph with JSON persistence.

Graph structure:
  Nodes: financial entity strings (lowercase)
    Attributes: doc_ids (set of chunk IDs containing this entity)
  Edges: directed (head → tail)
    Attributes: relation (str), doc_ids (set of chunk IDs where edge appears)

Persisted as a JSON file alongside chroma_db for fast incremental updates.
The graph is small enough (<<100k nodes for 620 docs) to keep fully in memory.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

try:
    from .entity_extractor import is_valid_entity
except Exception:
    try:
        from apollo_hipporag.entity_extractor import is_valid_entity
    except Exception:
        def is_valid_entity(entity: str) -> bool:  # type: ignore[no-redef]
            return bool(str(entity or "").strip())

log = logging.getLogger(__name__)

Triple = Tuple[str, str, str]


class FinancialKG:
    """
    Directed financial knowledge graph backed by NetworkX.

    Parameters
    ----------
    graph_path : str
        Path to the JSON file used for persistence (created if missing).
    """

    def __init__(self, graph_path: str) -> None:
        self.path = graph_path
        self.g: nx.DiGraph = nx.DiGraph()
        # All chunk IDs that have been processed (regex or LLM)
        self._indexed_doc_ids: Set[str] = set()
        # Subset that were processed specifically with the LLM extractor
        self._llm_indexed_doc_ids: Set[str] = set()
        self._last_loaded_mtime: Optional[float] = None
        self._load(reset=True)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self, reset: bool = False) -> None:
        if reset:
            self.g = nx.DiGraph()
            self._indexed_doc_ids = set()
            self._llm_indexed_doc_ids = set()
        if not os.path.exists(self.path):
            self._last_loaded_mtime = None
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            # Nodes
            for node, attrs in data.get("nodes", {}).items():
                doc_ids = set(attrs.get("doc_ids", []))
                self.g.add_node(node, doc_ids=doc_ids)
                self._indexed_doc_ids.update(doc_ids)
            # Edges
            for edge in data.get("edges", []):
                h, r, t = edge["head"], edge["relation"], edge["tail"]
                doc_ids = set(edge.get("doc_ids", []))
                if self.g.has_edge(h, t):
                    self.g[h][t]["doc_ids"].update(doc_ids)
                else:
                    self.g.add_edge(h, t, relation=r, doc_ids=doc_ids)
            # LLM-enriched doc IDs (separate from regex-only indexed)
            self._llm_indexed_doc_ids = set(data.get("llm_indexed_ids") or [])
            self._last_loaded_mtime = os.path.getmtime(self.path)
            log.info(
                "[hipporag] Graph loaded: %d nodes, %d edges, %d indexed docs (%d llm)",
                self.g.number_of_nodes(), self.g.number_of_edges(),
                len(self._indexed_doc_ids), len(self._llm_indexed_doc_ids),
            )
        except Exception as exc:
            log.warning("[hipporag] Failed to load graph from %s: %s", self.path, exc)

    def save(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            nodes = {
                n: {"doc_ids": list(d.get("doc_ids", set()))}
                for n, d in self.g.nodes(data=True)
            }
            edges = [
                {
                    "head": h,
                    "tail": t,
                    "relation": d.get("relation", "related_to"),
                    "doc_ids": list(d.get("doc_ids", set())),
                }
                for h, t, d in self.g.edges(data=True)
            ]
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({
                    "nodes": nodes,
                    "edges": edges,
                    "llm_indexed_ids": sorted(self._llm_indexed_doc_ids),
                }, fh, ensure_ascii=False)
            log.debug("[hipporag] Graph saved: %d nodes, %d edges, %d llm-indexed",
                      len(nodes), len(edges), len(self._llm_indexed_doc_ids))
        except Exception as exc:
            log.warning("[hipporag] Failed to save graph: %s", exc)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_triples(self, doc_id: str, triples: List[Triple], llm: bool = False) -> None:
        """Add (head, relation, tail) triples from a document chunk."""
        if not triples:
            return
        for head, relation, tail in triples:
            head, tail = head.lower().strip(), tail.lower().strip()
            if not head or not tail or head == tail:
                continue
            if not is_valid_entity(head) or not is_valid_entity(tail):
                continue
            # Node bookkeeping
            if not self.g.has_node(head):
                self.g.add_node(head, doc_ids=set())
            if not self.g.has_node(tail):
                self.g.add_node(tail, doc_ids=set())
            self.g.nodes[head]["doc_ids"].add(doc_id)
            self.g.nodes[tail]["doc_ids"].add(doc_id)
            # Edge
            if self.g.has_edge(head, tail):
                self.g[head][tail]["doc_ids"].add(doc_id)
            else:
                self.g.add_edge(head, tail, relation=relation, doc_ids={doc_id})
        self._indexed_doc_ids.add(doc_id)
        if llm:
            self._llm_indexed_doc_ids.add(doc_id)

    def is_llm_indexed(self, doc_id: str) -> bool:
        """Return True if this chunk has already been processed by the LLM extractor."""
        return doc_id in self._llm_indexed_doc_ids

    def mark_llm_indexed(self, doc_id: str) -> None:
        """Mark a chunk as LLM-processed without adding triples (e.g. LLM returned empty)."""
        self._indexed_doc_ids.add(doc_id)
        self._llm_indexed_doc_ids.add(doc_id)

    def prune_low_quality_nodes(self) -> int:
        """Remove noisy entities already persisted before stricter validation."""
        removed = 0
        for node_name in list(self.g.nodes):
            if is_valid_entity(str(node_name)):
                continue
            self.g.remove_node(node_name)
            removed += 1
        return removed

    def remove_doc_ids(self, doc_ids: Set[str]) -> bool:
        """Remove all references to the given chunk IDs from the graph."""
        target_ids = {str(doc_id).strip() for doc_id in set(doc_ids or set()) if str(doc_id).strip()}
        if not target_ids:
            return False

        changed = False

        for node_name in list(self.g.nodes):
            node_doc_ids = set(self.g.nodes[node_name].get("doc_ids", set()))
            remaining = node_doc_ids - target_ids
            if remaining != node_doc_ids:
                changed = True
                if remaining:
                    self.g.nodes[node_name]["doc_ids"] = remaining
                else:
                    self.g.remove_node(node_name)

        for head, tail in list(self.g.edges):
            edge_doc_ids = set(self.g[head][tail].get("doc_ids", set()))
            remaining = edge_doc_ids - target_ids
            if remaining != edge_doc_ids:
                changed = True
                if remaining:
                    self.g[head][tail]["doc_ids"] = remaining
                else:
                    self.g.remove_edge(head, tail)

        remaining_indexed = self._indexed_doc_ids - target_ids
        if remaining_indexed != self._indexed_doc_ids:
            self._indexed_doc_ids = remaining_indexed
            changed = True

        remaining_llm = self._llm_indexed_doc_ids - target_ids
        if remaining_llm != self._llm_indexed_doc_ids:
            self._llm_indexed_doc_ids = remaining_llm
            changed = True

        return changed

    def is_indexed(self, doc_id: str) -> bool:
        return doc_id in self._indexed_doc_ids

    def reload_if_changed(self) -> bool:
        """Reload the graph from disk when the JSON file has changed."""
        if not os.path.exists(self.path):
            if self.g.number_of_nodes() or self.g.number_of_edges() or self._indexed_doc_ids:
                self.g = nx.DiGraph()
                self._indexed_doc_ids = set()
                self._llm_indexed_doc_ids = set()
                self._last_loaded_mtime = None
                return True
            return False
        try:
            current_mtime = os.path.getmtime(self.path)
        except OSError:
            return False
        if self._last_loaded_mtime is None or current_mtime > (self._last_loaded_mtime + 1e-9):
            self._load(reset=True)
            return True
        return False

    def last_updated_iso(self) -> str:
        if self._last_loaded_mtime is None:
            if not os.path.exists(self.path):
                return ""
            try:
                self._last_loaded_mtime = os.path.getmtime(self.path)
            except OSError:
                return ""
        return datetime.fromtimestamp(self._last_loaded_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def subgraph_view(
        self,
        limit_nodes: int = 120,
        min_degree: int = 1,
        center: str = "",
        query: str = "",
        hops: int = 1,
    ) -> Dict:
        """
        Return a filtered graph payload suitable for a live UI.

        The payload is derived from the persisted graph on demand, so the
        visualization stays current as HippoRAG rebuilds or new chunks land.
        """
        self.reload_if_changed()
        graph = self.g
        limit_nodes = max(10, min(int(limit_nodes or 120), 400))
        min_degree = max(0, min(int(min_degree or 1), 20))
        hops = max(1, min(int(hops or 1), 3))

        def _doc_count(node_name: str) -> int:
            return len(graph.nodes[node_name].get("doc_ids", set()))

        def _node_score(node_name: str) -> float:
            return float(graph.degree(node_name)) + (_doc_count(node_name) * 0.2)

        def _neighbors(node_name: str) -> Set[str]:
            return set(graph.predecessors(node_name)) | set(graph.successors(node_name))

        eligible = [node for node in graph.nodes if graph.degree(node) >= min_degree]
        query_norm = query.strip().lower()
        center_norm = center.strip().lower()
        selected: Set[str] = set()
        undirected = graph.to_undirected(as_view=True)
        match_nodes: List[str] = []
        center_found = bool(center_norm and center_norm in graph.nodes)

        if center_found:
            selected = set(nx.single_source_shortest_path_length(undirected, center_norm, cutoff=hops).keys())
            ranked = sorted(selected, key=lambda node: (0 if node == center_norm else 1, -_node_score(node), node))
            selected = set(ranked[:limit_nodes])
        elif query_norm:
            match_nodes = [
                node for node in eligible
                if query_norm in node.lower()
            ]
            ranked_matches = sorted(match_nodes, key=lambda node: (-_node_score(node), node))[:max(1, min(12, limit_nodes // 4))]
            selected.update(ranked_matches)
            for node in ranked_matches:
                selected.update(sorted(_neighbors(node), key=lambda item: (-_node_score(item), item))[: max(4, limit_nodes // 10)])
                if len(selected) >= limit_nodes:
                    break
        if not selected:
            ranked_nodes = sorted(eligible, key=lambda node: (-_node_score(node), node))
            selected = set(ranked_nodes[:limit_nodes])

        if len(selected) > limit_nodes:
            selected = set(sorted(selected, key=lambda node: (-_node_score(node), node))[:limit_nodes])

        if center_found and center_norm not in selected:
            weakest = min(selected, key=_node_score) if selected else None
            if weakest is not None:
                selected.discard(weakest)
            selected.add(center_norm)

        subgraph = graph.subgraph(selected).copy()
        nodes = []
        for node in sorted(subgraph.nodes, key=lambda item: (-subgraph.degree(item), item)):
            attrs = subgraph.nodes[node]
            doc_ids = sorted(attrs.get("doc_ids", set()))
            nodes.append(
                {
                    "id": node,
                    "label": node,
                    "doc_count": len(doc_ids),
                    "degree": int(subgraph.degree(node)),
                    "in_degree": int(subgraph.in_degree(node)),
                    "out_degree": int(subgraph.out_degree(node)),
                    "sample_doc_ids": doc_ids[:8],
                    "neighbors": sorted(_neighbors(node))[:12],
                    "matched": bool(query_norm and query_norm in node.lower()),
                    "center": bool(center_found and node == center_norm),
                }
            )
        edges = []
        for head, tail, attrs in subgraph.edges(data=True):
            doc_ids = sorted(attrs.get("doc_ids", set()))
            edges.append(
                {
                    "source": head,
                    "target": tail,
                    "relation": attrs.get("relation", "related_to"),
                    "doc_count": len(doc_ids),
                    "sample_doc_ids": doc_ids[:6],
                }
            )

        stats = self.stats()
        stats["subgraph_nodes"] = len(nodes)
        stats["subgraph_edges"] = len(edges)

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": stats,
            "updated_at": self.last_updated_iso(),
            "filters": {
                "limit_nodes": limit_nodes,
                "min_degree": min_degree,
                "center": center_norm,
                "center_found": center_found,
                "query": query_norm,
                "query_match_count": len(match_nodes),
                "hops": hops,
            },
        }

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def doc_ids_for_entities(self, entities: List[str]) -> Set[str]:
        """Return all chunk IDs that contain at least one of the given entities."""
        doc_ids: Set[str] = set()
        for entity in entities:
            ent = entity.lower().strip()
            if ent in self.g.nodes:
                doc_ids.update(self.g.nodes[ent].get("doc_ids", set()))
        return doc_ids

    def stats(self) -> Dict:
        return {
            "nodes": self.g.number_of_nodes(),
            "edges": self.g.number_of_edges(),
            "indexed_docs": len(self._indexed_doc_ids),
            "llm_indexed_docs": len(self._llm_indexed_doc_ids),
        }
