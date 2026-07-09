from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from Apollo.apollo.optional_deps import MissingOptionalDependencyError, require_matplotlib_pyplot


_APOLLO_ROOT = Path(__file__).resolve().parent
DEFAULT_KG_PATH = _APOLLO_ROOT / "chroma_db" / "financial_kg.json"
PLOTS_ROOT = _APOLLO_ROOT / "logs" / "plots"


def _today() -> str:
    return datetime.now().date().isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _node_rows(nodes: Any, limit: int = 20) -> List[Tuple[str, int]]:
    rows: List[Tuple[str, int]] = []
    if isinstance(nodes, dict):
        for name, payload in nodes.items():
            if isinstance(payload, dict):
                degree = int(payload.get("degree") or len(list(payload.get("doc_ids") or [])))
            else:
                degree = 0
            rows.append((str(name), degree))
    elif isinstance(nodes, list):
        for item in nodes:
            if isinstance(item, dict):
                name = str(item.get("id") or item.get("name") or item.get("label") or "")
                degree = int(item.get("degree") or len(list(item.get("doc_ids") or [])))
                if name:
                    rows.append((name, degree))
    return sorted(rows, key=lambda row: row[1], reverse=True)[:limit]


def _edge_rows(edges: Any, limit: int = 20) -> List[Tuple[str, int]]:
    rows: List[Tuple[str, int]] = []
    if isinstance(edges, list):
        for item in edges:
            if not isinstance(item, dict):
                continue
            label = f"{item.get('head') or ''} -{item.get('relation') or 'related_to'}-> {item.get('tail') or ''}".strip()
            weight = int(item.get("weight") or len(list(item.get("doc_ids") or [])) or 1)
            rows.append((label, weight))
    return sorted(rows, key=lambda row: row[1], reverse=True)[:limit]


def _ticker_counts(nodes: Any) -> List[Tuple[str, int]]:
    tickers = Counter()
    names: Iterable[str]
    if isinstance(nodes, dict):
        names = [str(name) for name in nodes.keys()]
    elif isinstance(nodes, list):
        names = [str((item or {}).get("id") or (item or {}).get("name") or "") for item in nodes if isinstance(item, dict)]
    else:
        names = []
    for name in names:
        token = name.strip().upper()
        if 1 <= len(token) <= 5 and token.isalpha():
            tickers[token] += 1
    return tickers.most_common(20)


def _bar_plot(rows: List[Tuple[str, int]], path: Path, title: str) -> bool:
    if not rows:
        return False
    try:
        plt = require_matplotlib_pyplot()
    except MissingOptionalDependencyError:
        return False
    labels = [row[0][:32] for row in rows]
    values = [row[1] for row in rows]
    height = max(4, min(9, 0.35 * len(rows) + 1.5))
    fig, ax = plt.subplots(figsize=(10, height))
    ax.barh(labels[::-1], values[::-1], color="#2f6f9f")
    ax.set_title(title)
    ax.set_xlabel("count")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True


def write_graph_artifacts(
    kg_path: Optional[Path | str] = None,
    *,
    report_date: Optional[str] = None,
    out_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    graph_path = Path(kg_path or DEFAULT_KG_PATH)
    day = str(report_date or _today())[:10]
    out_dir = Path(out_root or PLOTS_ROOT) / day / datetime.now().strftime("graph_%H%M%S")
    graph = _read_json(graph_path)
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    top_nodes = _node_rows(nodes, limit=20)
    top_edges = _edge_rows(edges, limit=20)
    top_tickers = _ticker_counts(nodes)
    node_count = len(nodes) if isinstance(nodes, (dict, list)) else 0
    edge_count = len(edges) if isinstance(edges, list) else 0

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "graph_summary.md"
    lines = [
        "# Apollo HippoRAG Graph Artifact Summary",
        "",
        f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Source KG: `{graph_path}`",
        f"- Nodes: `{node_count}`",
        f"- Edges: `{edge_count}`",
        "",
        "## Top Entities",
        "",
    ]
    lines.extend([f"- {name}: {degree}" for name, degree in top_nodes] or ["- No entities found."])
    lines.extend(["", "## Top Edges", ""])
    lines.extend([f"- {label}: {weight}" for label, weight in top_edges] or ["- No edges found."])
    lines.extend(["", "## Top Ticker-Like Entities", ""])
    lines.extend([f"- {ticker}: {count}" for ticker, count in top_tickers] or ["- No ticker-like entities found."])
    summary_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    pngs: List[str] = []
    if _bar_plot(top_nodes[:15], out_dir / "top_entities.png", "Top Apollo KG entities"):
        pngs.append(str(out_dir / "top_entities.png"))
    if _bar_plot(top_edges[:15], out_dir / "top_edges.png", "Top Apollo KG edges"):
        pngs.append(str(out_dir / "top_edges.png"))
    if _bar_plot(top_tickers[:15], out_dir / "top_tickers.png", "Top ticker-like KG entities"):
        pngs.append(str(out_dir / "top_tickers.png"))

    status = {
        "ok": bool(graph),
        "graph_path": str(graph_path),
        "artifact_dir": str(out_dir),
        "summary_path": str(summary_path),
        "png_paths": pngs,
        "node_count": node_count,
        "edge_count": edge_count,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "matplotlib_available": bool(pngs),
    }
    (out_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    latest_path = Path(out_root or PLOTS_ROOT) / "latest_graph_artifacts.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status
