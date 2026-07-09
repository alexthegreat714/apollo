import json
import os
import time

from Apollo.apollo_hipporag.graph_store import FinancialKG


def _write_graph(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_graph_store_reloads_and_builds_subgraph(tmp_path):
    graph_path = tmp_path / "financial_kg.json"
    _write_graph(
        graph_path,
        {
            "nodes": {
                "apple": {"doc_ids": ["doc-1", "doc-2"]},
                "tesla": {"doc_ids": ["doc-1"]},
            },
            "edges": [
                {"head": "apple", "relation": "competes_with", "tail": "tesla", "doc_ids": ["doc-1"]},
            ],
        },
    )

    kg = FinancialKG(str(graph_path))
    assert kg.stats()["nodes"] == 2
    view = kg.subgraph_view(limit_nodes=20, query="apple")
    assert view["filters"]["query_match_count"] == 1
    assert any(node["id"] == "apple" for node in view["nodes"])

    time.sleep(0.02)
    _write_graph(
        graph_path,
        {
            "nodes": {
                "apple": {"doc_ids": ["doc-1", "doc-2"]},
                "tesla": {"doc_ids": ["doc-1"]},
                "nvidia": {"doc_ids": ["doc-3"]},
            },
            "edges": [
                {"head": "apple", "relation": "competes_with", "tail": "tesla", "doc_ids": ["doc-1"]},
                {"head": "nvidia", "relation": "supplies", "tail": "tesla", "doc_ids": ["doc-3"]},
            ],
        },
    )
    now = time.time() + 2
    os.utime(graph_path, (now, now))

    assert kg.reload_if_changed() is True
    assert kg.stats()["nodes"] == 3

    centered = kg.subgraph_view(limit_nodes=20, center="tesla", hops=1)
    assert centered["filters"]["center_found"] is True
    assert any(node["id"] == "tesla" and node["center"] for node in centered["nodes"])
    assert centered["stats"]["subgraph_nodes"] >= 2


def test_graph_store_can_remove_doc_ids(tmp_path):
    graph_path = tmp_path / "financial_kg.json"
    _write_graph(
        graph_path,
        {
            "nodes": {
                "apple": {"doc_ids": ["doc-1", "doc-2"]},
                "tesla": {"doc_ids": ["doc-1"]},
                "nvidia": {"doc_ids": ["doc-3"]},
            },
            "edges": [
                {"head": "apple", "relation": "competes_with", "tail": "tesla", "doc_ids": ["doc-1"]},
                {"head": "nvidia", "relation": "supplies", "tail": "apple", "doc_ids": ["doc-3"]},
            ],
        },
    )

    kg = FinancialKG(str(graph_path))
    assert kg.remove_doc_ids({"doc-1"}) is True
    kg.save()

    reloaded = FinancialKG(str(graph_path))
    assert reloaded.stats()["indexed_docs"] == 2
    assert "tesla" not in reloaded.g.nodes
    assert "apple" in reloaded.g.nodes
    assert reloaded.g.nodes["apple"]["doc_ids"] == {"doc-2"}
    assert not reloaded.g.has_edge("apple", "tesla")


def test_graph_store_prunes_low_quality_nodes(tmp_path):
    graph_path = tmp_path / "financial_kg.json"
    _write_graph(
        graph_path,
        {
            "nodes": {
                "and": {"doc_ids": ["doc-1"]},
                "to": {"doc_ids": ["doc-1"]},
                "sec": {"doc_ids": ["doc-2"]},
                "margin": {"doc_ids": ["doc-2"]},
            },
            "edges": [
                {"head": "and", "relation": "related_to", "tail": "sec", "doc_ids": ["doc-1"]},
                {"head": "sec", "relation": "regulates", "tail": "margin", "doc_ids": ["doc-2"]},
            ],
        },
    )

    kg = FinancialKG(str(graph_path))
    assert kg.prune_low_quality_nodes() == 2
    kg.save()

    reloaded = FinancialKG(str(graph_path))
    assert "and" not in reloaded.g.nodes
    assert "to" not in reloaded.g.nodes
    assert "sec" in reloaded.g.nodes
    assert reloaded.g.has_edge("sec", "margin")
