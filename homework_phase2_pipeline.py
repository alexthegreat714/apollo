from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from Apollo import homework_trade_pipeline, swing_strategy_kb


_APOLLO_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _APOLLO_ROOT.parent
RUNS_ROOT = _APOLLO_ROOT / "logs" / "homework_trade_phase2"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _local_day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _rag_dir() -> Path:
    raw = os.getenv("RAG_DIR", "")
    if raw and not os.path.isabs(raw):
        return (_APOLLO_ROOT / raw).resolve()
    return Path(raw or (_APOLLO_ROOT / "chroma_db")).resolve()


def _collection_name() -> str:
    return str(os.getenv("RAG_COLLECTION") or "apollo_financial").strip() or "apollo_financial"


def rebuild_strategy_graph(max_new_chunks: int = 1000, timeout_sec: int = 1800) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "Apollo.apollo_hipporag.build_graph",
        "--rag-dir",
        str(_rag_dir()),
        "--collection",
        _collection_name(),
        "--max-new-chunks",
        str(max(1, int(max_new_chunks))),
        "--batch-size",
        "25",
    ]
    started_at = _utc_iso()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            text=True,
            capture_output=True,
            timeout=max(60, int(timeout_sec)),
        )
        return {
            "ok": proc.returncode == 0,
            "started_at": started_at,
            "completed_at": _utc_iso(),
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-8000:],
            "stderr_tail": proc.stderr[-8000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "started_at": started_at,
            "completed_at": _utc_iso(),
            "cmd": cmd,
            "error": f"timeout_after_{timeout_sec}_sec",
            "stdout_tail": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-8000:] if isinstance(exc.stderr, str) else "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "started_at": started_at,
            "completed_at": _utc_iso(),
            "cmd": cmd,
            "error": f"{type(exc).__name__}:{exc}",
        }


def _status_from_strategy(ingest: Dict[str, Any], graph: Dict[str, Any]) -> Dict[str, Any]:
    if not ingest.get("ok"):
        status = "strategy_kb_ingest_failed"
    elif graph and not graph.get("ok"):
        status = "strategy_kb_ingested_graph_degraded"
    elif graph:
        status = "strategy_kb_ingested_graph_refreshed"
    else:
        status = "strategy_kb_ingested_graph_skipped"
    return {
        "status": status,
        "ingest_ok": bool(ingest.get("ok")),
        "graph_ok": bool(graph.get("ok")) if graph else None,
        "doc_count": ingest.get("doc_count"),
        "collection_count": ingest.get("collection_count"),
        "rag_dir": ingest.get("rag_dir"),
        "collection": ingest.get("collection"),
    }


def _report_lines(result: Dict[str, Any]) -> list[str]:
    homework = dict(result.get("homework_trade") or {})
    plan = dict(homework.get("trade_plan") or {})
    strategy = dict(result.get("strategy_kb") or {})
    graph = dict(result.get("hipporag_refresh") or {})
    return [
        "# Apollo Homework Phase 2",
        "",
        f"- created_at: `{result.get('created_at')}`",
        f"- run_id: `{result.get('run_id')}`",
        f"- research_only: `true`",
        f"- live_trade_execution: `false`",
        f"- human_approval_required: `true`",
        "",
        "## Strategy KB",
        "",
        f"- status: `{strategy.get('status')}`",
        f"- doc_count: `{strategy.get('doc_count')}`",
        f"- collection_count: `{strategy.get('collection_count')}`",
        f"- graph_ok: `{graph.get('ok')}`",
        f"- graph_returncode: `{graph.get('returncode')}`",
        "",
        "## Homework Decision",
        "",
        f"- ticker: `{plan.get('ticker')}`",
        f"- decision: `{plan.get('decision')}`",
        f"- setup_type: `{plan.get('setup_type')}`",
        f"- score: `{plan.get('score')}`",
        f"- no_trade_reason: `{plan.get('no_trade_reason')}`",
        f"- homework_latest: `{((homework.get('artifacts') or {}).get('latest_path'))}`",
    ]


def write_artifacts(result: Dict[str, Any]) -> Dict[str, str]:
    run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(result.get("run_id") or "apollo_homework_phase2")).strip("_")
    out_dir = RUNS_ROOT / _local_day() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "homework_phase2_result.json"
    report_path = out_dir / "homework_phase2_report.md"
    status_path = out_dir / "status.json"
    latest_path = RUNS_ROOT / "latest_homework_phase2.json"
    _write_json(result_path, result)
    report_path.write_text("\n".join(_report_lines(result)).strip() + "\n", encoding="utf-8")
    status = {
        "ok": result.get("ok"),
        "run_id": result.get("run_id"),
        "created_at": result.get("created_at"),
        "result_path": str(result_path),
        "report_path": str(report_path),
        "homework_latest": ((result.get("homework_trade") or {}).get("artifacts") or {}).get("latest_path"),
    }
    _write_json(status_path, status)
    _write_json(latest_path, {**status, **result})
    return {
        "result_path": str(result_path),
        "report_path": str(report_path),
        "status_path": str(status_path),
        "latest_path": str(latest_path),
    }


def run_phase2(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    run_id = str(payload.get("run_id") or f"apollo_homework_phase2_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    upstream = {
        "task": str(payload.get("upstream_ocr_task") or ""),
        "status": str(payload.get("upstream_ocr_status") or "unknown"),
        "last_result": str(payload.get("upstream_ocr_result") or ""),
        "completed_at": str(payload.get("upstream_ocr_completed_at") or ""),
    }
    try:
        ingest = swing_strategy_kb.ingest_strategy_kb()
    except Exception as exc:
        ingest = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    graph: Dict[str, Any] = {}
    if bool(payload.get("rebuild_graph", True)) and ingest.get("ok"):
        graph = rebuild_strategy_graph(
            max_new_chunks=int(payload.get("graph_max_new_chunks") or 1000),
            timeout_sec=int(payload.get("graph_timeout_sec") or 1800),
        )

    strategy_status = _status_from_strategy(ingest, graph)
    homework = homework_trade_pipeline.build_homework_trade_plan(
        {
            "tickers": str(payload.get("tickers") or ""),
            "notify": bool(payload.get("notify", False)),
            "write_artifacts": True,
            "upstream_ocr_task": upstream["task"],
            "upstream_ocr_status": upstream["status"],
            "upstream_ocr_result": upstream["last_result"],
            "upstream_ocr_completed_at": upstream["completed_at"],
            "strategy_kb_status": strategy_status,
            "strategy_top_k": int(payload.get("strategy_top_k") or 5),
            "use_strategy_kb": True,
        }
    )
    result = {
        "ok": bool(homework.get("ok")) and bool(ingest.get("ok")),
        "run_id": run_id,
        "created_at": _utc_iso(),
        "purpose": "strategy-aware Apollo homework phase after OCR completion",
        "research_only": True,
        "human_approval_required": True,
        "live_trade_execution": False,
        "upstream_ocr": upstream,
        "strategy_kb": strategy_status,
        "strategy_ingest": ingest,
        "hipporag_refresh": graph,
        "homework_trade": homework,
    }
    if bool(payload.get("write_artifacts", True)):
        result["artifacts"] = write_artifacts(result)
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apollo strategy-aware homework phase 2")
    parser.add_argument("cmd", choices=["run"], nargs="?", default="run")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--upstream-ocr-task", default="")
    parser.add_argument("--upstream-ocr-status", default="")
    parser.add_argument("--upstream-ocr-result", default="")
    parser.add_argument("--upstream-ocr-completed-at", default="")
    parser.add_argument("--skip-graph-rebuild", action="store_true")
    parser.add_argument("--graph-max-new-chunks", type=int, default=1000)
    parser.add_argument("--graph-timeout-sec", type=int, default=1800)
    parser.add_argument("--strategy-top-k", type=int, default=5)
    args = parser.parse_args()
    payload: Dict[str, Any] = {
        "tickers": args.tickers,
        "notify": bool(args.notify),
        "upstream_ocr_task": args.upstream_ocr_task,
        "upstream_ocr_status": args.upstream_ocr_status,
        "upstream_ocr_result": args.upstream_ocr_result,
        "upstream_ocr_completed_at": args.upstream_ocr_completed_at,
        "rebuild_graph": not bool(args.skip_graph_rebuild),
        "graph_max_new_chunks": args.graph_max_new_chunks,
        "graph_timeout_sec": args.graph_timeout_sec,
        "strategy_top_k": args.strategy_top_k,
    }
    print(json.dumps(run_phase2(payload), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["rebuild_strategy_graph", "run_phase2", "write_artifacts"]
