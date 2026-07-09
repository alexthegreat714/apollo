from __future__ import annotations

import json

from Apollo import graph_artifacts


def test_graph_artifact_writer_creates_summary_and_status(tmp_path):
    kg_path = tmp_path / "financial_kg.json"
    kg_path.write_text(
        json.dumps(
            {
                "nodes": {
                    "NVDA": {"doc_ids": ["doc1", "doc2"]},
                    "semiconductors": {"doc_ids": ["doc1"]},
                },
                "edges": [
                    {"head": "NVDA", "relation": "supplies", "tail": "semiconductors", "doc_ids": ["doc1", "doc2"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = graph_artifacts.write_graph_artifacts(kg_path=kg_path, report_date="2026-05-20", out_root=tmp_path / "plots")

    assert result["ok"] is True
    assert result["node_count"] == 2
    assert result["edge_count"] == 1
    assert "graph_summary.md" in result["summary_path"]
    assert (tmp_path / "plots" / "latest_graph_artifacts.json").exists()
    assert "NVDA" in (tmp_path / "plots" / "2026-05-20").glob("*/graph_summary.md").__next__().read_text(encoding="utf-8")
