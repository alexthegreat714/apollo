from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
DEFAULT_TIMEOUT_SEC = 30
DEFAULT_USER_AGENT = "Apollo-OCR97-Study/1.0"


def _workspace_path(raw: str | Path) -> Path:
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (WORKSPACE_ROOT / path).resolve()


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_root_must_be_object:{path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _http_request_json(
    url: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> Dict[str, Any]:
    data = None
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(dict(payload)).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read()
    if not body:
        return {}
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"http_json_root_must_be_object:{url}")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(raw: str) -> str:
    chars = []
    for ch in str(raw).strip().lower():
        if ch.isalnum():
            chars.append(ch)
        else:
            chars.append("_")
    value = "".join(chars).strip("_")
    while "__" in value:
        value = value.replace("__", "_")
    return value or "item"


def _load_manifest(path: Path) -> Dict[str, Any]:
    payload = _load_json(path)
    payload.setdefault("documents", [])
    payload.setdefault("routes", [])
    payload.setdefault("baseline_route", "")
    payload.setdefault("name", path.stem)
    if not isinstance(payload["documents"], list):
        raise ValueError("manifest_documents_must_be_list")
    if not isinstance(payload["routes"], list):
        raise ValueError("manifest_routes_must_be_list")
    return payload


def _load_paper_manifest(path: Path) -> Dict[str, Any]:
    payload = _load_json(path)
    payload.setdefault("papers", [])
    if not isinstance(payload["papers"], list):
        raise ValueError("paper_manifest_papers_must_be_list")
    return payload


def _extract_metrics(result: Dict[str, Any], *, duration_ms: int, checks: Mapping[str, Any]) -> Dict[str, Any]:
    quality = dict(result.get("quality") or {})
    finance_consistency = dict(quality.get("finance_consistency") or {})
    finbert_eval = dict(quality.get("finbert_eval") or {})
    table_reconstruction = dict(quality.get("table_reconstruction") or {})
    semantic_diff = dict(quality.get("semantic_diff") or {})
    score = int(round(float(quality.get("score") or 0.0) * 100.0))
    structure_lane = int(round(float(quality.get("structure_score") or 0.0) * 100.0))
    numeric_lane = int(round(float(quality.get("numeric_fidelity_score") or 0.0) * 100.0))
    finance_lane = int(round(float(finance_consistency.get("score") or 0.0) * 100.0))
    confidence_lane = int(round(float(quality.get("confidence") or 0.0) * 100.0))
    table_rows = int(quality.get("table_rows") or 0)
    table_ok = bool(table_reconstruction.get("ok"))
    table_mode = str(table_reconstruction.get("mode") or "")
    phase2 = dict(result.get("phase2") or {})
    ensemble = dict(phase2.get("ensemble") or {})
    variant_count = int(
        ensemble.get("variant_count")
        or quality.get("variant_count")
        or phase2.get("variant_count")
        or 0
    )
    required_pass = bool(checks.get("contains_all_required"))
    metrics = {
        "ok": bool(result.get("ok")),
        "engine": str(result.get("engine") or ""),
        "route": str(result.get("route") or ""),
        "chars": int(quality.get("chars") or len(str(result.get("markdown") or result.get("text") or ""))),
        "quality_lane": score,
        "structure_lane": structure_lane,
        "numeric_lane": numeric_lane,
        "finance_lane": finance_lane,
        "confidence_lane": confidence_lane,
        "table_rows": table_rows,
        "table_ok": table_ok,
        "table_mode": table_mode or "disabled",
        "semantic_risk_band": str(semantic_diff.get("risk_band") or ""),
        "semantic_change_pct": float(semantic_diff.get("change_pct") or 0.0),
        "finbert_label": str(finbert_eval.get("label") or ""),
        "variant_count": variant_count,
        "required_token_coverage": 100 if required_pass else 0,
        "latency_ms": int(duration_ms),
        "doc_class": str(quality.get("doc_class") or ""),
    }
    return metrics


def _derive_capability_cards(
    metrics: Mapping[str, Any],
    *,
    slice_name: str,
) -> Dict[str, Optional[int]]:
    quality_lane = int(metrics.get("quality_lane") or 0)
    structure_lane = int(metrics.get("structure_lane") or 0)
    numeric_lane = int(metrics.get("numeric_lane") or 0)
    finance_lane = int(metrics.get("finance_lane") or 0)
    table_rows = int(metrics.get("table_rows") or 0)
    table_ok = bool(metrics.get("table_ok"))
    table_bonus = 20 if table_ok else 0
    row_bonus = min(20, table_rows * 2)
    table_lane = min(100, int(round(structure_lane * 0.65 + table_bonus + row_bonus)))
    semantic_lane = min(100, int(round(finance_lane * 0.5 + numeric_lane * 0.25 + structure_lane * 0.25)))
    cards: Dict[str, Optional[int]] = {
        "literal_transcription_fidelity": quality_lane,
        "numeric_fidelity": numeric_lane,
        "table_structure_fidelity": table_lane,
        "layout_preservation": structure_lane,
        "tiny_text_recovery": None,
        "degraded_scan_robustness": None,
        "image_capture_robustness": None,
        "cross_element_semantic_reconstruction": semantic_lane,
        "required_token_coverage": int(metrics.get("required_token_coverage") or 0),
    }
    if slice_name == "tiny_text_high_density_pages":
        cards["tiny_text_recovery"] = quality_lane
    if slice_name in {"scanned_finance_pdfs", "warped_or_degraded_image_captures"}:
        cards["degraded_scan_robustness"] = quality_lane
    if slice_name == "warped_or_degraded_image_captures":
        cards["image_capture_robustness"] = quality_lane
    return cards


def _score_document(cards: Mapping[str, Optional[int]], weights: Mapping[str, Any]) -> int:
    weighted = 0.0
    total = 0.0
    for key, raw_weight in weights.items():
        try:
            weight = float(raw_weight)
        except Exception:
            continue
        if weight <= 0:
            continue
        value = cards.get(str(key))
        if value is None:
            continue
        weighted += float(value) * weight
        total += weight
    if total <= 0:
        return int(cards.get("literal_transcription_fidelity") or 0)
    return int(round(weighted / total))


def _normalize_missing(items: Iterable[Any]) -> List[str]:
    return [str(item).strip() for item in items if str(item).strip()]


def _route_research_links(route: Mapping[str, Any], paper_index: Mapping[str, Any]) -> List[Dict[str, Any]]:
    papers = list(route.get("papers") or [])
    index = {str(item.get("id") or ""): item for item in list(paper_index.get("papers") or []) if isinstance(item, dict)}
    links: List[Dict[str, Any]] = []
    for paper_id in papers:
        item = index.get(str(paper_id))
        if item:
            links.append(item)
        else:
            links.append({"id": str(paper_id), "title": str(paper_id), "source_url": "", "local_pdf": ""})
    return links


def _gateway_health(route: Mapping[str, Any], *, timeout_sec: int) -> Dict[str, Any]:
    execution = dict(route.get("execution") or {})
    health_url = str(execution.get("health_url") or "").strip()
    if not health_url:
        return {"available": False, "reason": "health_url_unset"}
    try:
        payload = _http_request_json(health_url, timeout_sec=timeout_sec)
        ok = bool(payload.get("ok", True))
        return {"available": ok, "reason": "ok" if ok else str(payload.get("error") or "health_not_ok"), "payload": payload}
    except Exception as exc:
        return {"available": False, "reason": f"gateway_health_failed:{type(exc).__name__}:{exc}"}


def _eval_mcp_route(
    doc: Mapping[str, Any],
    route: Mapping[str, Any],
    *,
    timeout_sec: int,
) -> Tuple[Dict[str, Any], int]:
    execution = dict(route.get("execution") or {})
    helper = _workspace_path("Engineering/Apollo/tools/run_ocr_mcp_dual_once.py")
    command = [
        sys.executable,
        str(helper),
        "--path",
        str(_workspace_path(str(doc.get("path") or ""))),
        "--goal",
        str(doc.get("goal") or "OCR97 challenger study extraction"),
        "--engine",
        str(execution.get("engine") or doc.get("engine") or "gb10_auto"),
        "--route-mode",
        str(execution.get("route_mode") or "quality_first"),
        "--consensus",
        "true" if bool(execution.get("consensus", True)) else "false",
        "--use-gateway",
        "true" if bool(execution.get("use_gateway", True)) else "false",
        "--max-pages",
        str(int(execution.get("max_pages") or doc.get("max_pages") or 1)),
        "--max-chars",
        str(int(execution.get("max_chars") or doc.get("max_chars") or 6000)),
    ]
    started = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=max(5, int(timeout_sec)),
        check=False,
    )
    duration_ms = int(round((time.perf_counter() - started) * 1000.0))
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout:
        return {
            "ok": False,
            "error": f"mcp_route_no_stdout:returncode={proc.returncode}",
            "stderr": stderr,
            "command": command,
        }, duration_ms
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"mcp_route_bad_json:{exc}",
            "stderr": stderr,
            "stdout_excerpt": stdout[:400],
            "command": command,
        }, duration_ms
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "mcp_route_json_root_must_be_object",
            "stderr": stderr,
            "command": command,
        }, duration_ms
    if stderr:
        payload.setdefault("stderr", stderr)
    payload.setdefault("command", command)
    return payload, duration_ms


def _eval_gateway_route(
    doc: Mapping[str, Any],
    route: Mapping[str, Any],
    *,
    timeout_sec: int,
) -> Tuple[Dict[str, Any], int]:
    execution = dict(route.get("execution") or {})
    url = str(execution.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "gateway_url_unset"}, 0
    payload = {
        "path": str(_workspace_path(str(doc.get("path") or ""))),
        "goal": str(doc.get("goal") or "OCR97 challenger study extraction"),
        "max_pages": int(execution.get("max_pages") or doc.get("max_pages") or 1),
        "max_chars": int(execution.get("max_chars") or doc.get("max_chars") or 6000),
    }
    started = time.perf_counter()
    result = _http_request_json(url, payload=payload, timeout_sec=timeout_sec)
    duration_ms = int(round((time.perf_counter() - started) * 1000.0))
    return result, duration_ms


def _eval_node_cli_route(
    doc: Mapping[str, Any],
    route: Mapping[str, Any],
    *,
    timeout_sec: int,
) -> Tuple[Dict[str, Any], int]:
    execution = dict(route.get("execution") or {})
    raw_command = execution.get("command") or []
    if isinstance(raw_command, str):
        command = shlex.split(raw_command)
    else:
        command = [str(item) for item in raw_command if str(item).strip()]
    if not command:
        return {"ok": False, "error": "node_cli_command_unset"}, 0
    resolved_command: List[str] = []
    for index, token in enumerate(command):
        if index == 0:
            resolved_command.append(token)
            continue
        maybe_path = _workspace_path(token)
        resolved_command.append(str(maybe_path) if maybe_path.exists() else token)
    resolved_command.extend(["--path", str(_workspace_path(str(doc.get("path") or "")))])
    started = time.perf_counter()
    proc = subprocess.run(
        resolved_command,
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=max(5, int(timeout_sec)),
        check=False,
    )
    duration_ms = int(round((time.perf_counter() - started) * 1000.0))
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout:
        return {
            "ok": False,
            "error": f"node_cli_no_stdout:returncode={proc.returncode}",
            "stderr": stderr,
            "command": resolved_command,
        }, duration_ms
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"node_cli_bad_json:{exc}",
            "stderr": stderr,
            "stdout_excerpt": stdout[:400],
            "command": resolved_command,
        }, duration_ms
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "node_cli_json_root_must_be_object",
            "stderr": stderr,
            "command": resolved_command,
        }, duration_ms
    if stderr:
        payload.setdefault("stderr", stderr)
    payload.setdefault("command", resolved_command)
    return payload, duration_ms


def _eval_document_route(
    doc: Mapping[str, Any],
    route: Mapping[str, Any],
    *,
    timeout_sec: int,
    paper_index: Mapping[str, Any],
) -> Dict[str, Any]:
    path = _workspace_path(str(doc.get("path") or ""))
    if not path.exists():
        return {
            "route_id": str(route.get("id") or ""),
            "document_id": str(doc.get("id") or ""),
            "slice": str(doc.get("slice") or ""),
            "ok": False,
            "status": "file_missing",
            "path": str(path),
            "error": f"file_missing:{path}",
            "research": _route_research_links(route, paper_index),
        }

    execution = dict(route.get("execution") or {})
    route_type = str(route.get("route_type") or "challenger")
    execution_kind = str(execution.get("kind") or "mcp_ocr_dual")
    route_timeout_sec = max(5, int(execution.get("timeout_sec") or timeout_sec))
    base_row: Dict[str, Any] = {
        "route_id": str(route.get("id") or ""),
        "route_label": str(route.get("label") or route.get("id") or ""),
        "route_type": route_type,
        "document_id": str(doc.get("id") or ""),
        "document_label": str(doc.get("label") or doc.get("id") or ""),
        "slice": str(doc.get("slice") or ""),
        "path": str(path),
        "components": list(route.get("components") or []),
        "paired_components": list(route.get("paired_components") or []),
        "papers": list(route.get("papers") or []),
        "research": _route_research_links(route, paper_index),
        "operational_complexity": str(route.get("operational_complexity") or "unknown"),
        "expected_strengths": list(route.get("expected_strengths") or []),
        "literature_claims": list(route.get("literature_claims") or []),
    }

    if execution_kind == "literature_only":
        base_row.update(
            {
                "ok": False,
                "status": "literature_only",
                "error": "empirical_unavailable:literature_only_route",
                "metrics": {},
                "capability_cards": {},
                "checks": {},
            }
        )
        return base_row

    if execution_kind == "gateway_http":
        health = _gateway_health(route, timeout_sec=route_timeout_sec)
        if not health.get("available"):
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_unavailable",
                    "error": str(health.get("reason") or "gateway_unavailable"),
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                    "health": health,
                }
            )
            return base_row
        try:
            result, duration_ms = _eval_gateway_route(doc, route, timeout_sec=route_timeout_sec)
        except Exception as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_failed",
                    "error": f"gateway_eval_failed:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                    "health": health,
                }
            )
            return base_row
        base_row["health"] = health
    elif execution_kind == "node_cli_json":
        try:
            result, duration_ms = _eval_node_cli_route(doc, route, timeout_sec=route_timeout_sec)
        except subprocess.TimeoutExpired as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_failed",
                    "error": f"node_cli_timeout:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                }
            )
            return base_row
        except FileNotFoundError as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_unavailable",
                    "error": f"node_cli_unavailable:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                }
            )
            return base_row
        except Exception as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_failed",
                    "error": f"node_cli_eval_failed:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                }
            )
            return base_row
    else:
        try:
            result, duration_ms = _eval_mcp_route(doc, route, timeout_sec=route_timeout_sec)
        except subprocess.TimeoutExpired as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_failed",
                    "error": f"mcp_timeout:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                }
            )
            return base_row
        except FileNotFoundError as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_unavailable",
                    "error": f"mcp_unavailable:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                }
            )
            return base_row
        except Exception as exc:
            base_row.update(
                {
                    "ok": False,
                    "status": "runtime_failed",
                    "error": f"mcp_eval_failed:{type(exc).__name__}:{exc}",
                    "metrics": {},
                    "capability_cards": {},
                    "checks": {},
                }
            )
            return base_row

    text = str(result.get("markdown") or result.get("text") or "")
    must_contain = _normalize_missing(doc.get("must_contain") or [])
    missing = [token for token in must_contain if token.lower() not in text.lower()]
    checks = {
        "contains_all_required": len(missing) == 0,
        "required_missing": missing,
    }
    metrics = _extract_metrics(result, duration_ms=duration_ms, checks=checks)
    capability_cards = _derive_capability_cards(metrics, slice_name=str(doc.get("slice") or ""))
    score = _score_document(capability_cards, dict(doc.get("capability_weights") or {}))
    base_row.update(
        {
            "ok": bool(metrics.get("ok")) and checks["contains_all_required"],
            "status": "measured",
            "error": str(result.get("error") or ""),
            "checks": checks,
            "metrics": metrics,
            "capability_cards": capability_cards,
            "study_score": score,
            "output_excerpt": text[:600],
        }
    )
    return base_row


def _mean_int(values: Iterable[int]) -> int:
    items = list(values)
    if not items:
        return 0
    return int(round(sum(items) / float(len(items))))


def _summarize_route_slice(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    measured = [row for row in rows if str(row.get("status")) == "measured"]
    capability_names = [
        "literal_transcription_fidelity",
        "numeric_fidelity",
        "table_structure_fidelity",
        "layout_preservation",
        "tiny_text_recovery",
        "degraded_scan_robustness",
        "image_capture_robustness",
        "cross_element_semantic_reconstruction",
        "required_token_coverage",
    ]
    summary = {
        "documents": len(rows),
        "measured_documents": len(measured),
        "ok_documents": sum(1 for row in measured if bool(row.get("ok"))),
        "failed_documents": len(measured) - sum(1 for row in measured if bool(row.get("ok"))),
        "study_score_avg": _mean_int(int(row.get("study_score") or 0) for row in measured),
        "quality_lane_avg": _mean_int(int((row.get("metrics") or {}).get("quality_lane") or 0) for row in measured),
        "structure_lane_avg": _mean_int(int((row.get("metrics") or {}).get("structure_lane") or 0) for row in measured),
        "numeric_lane_avg": _mean_int(int((row.get("metrics") or {}).get("numeric_lane") or 0) for row in measured),
        "latency_ms_avg": _mean_int(int((row.get("metrics") or {}).get("latency_ms") or 0) for row in measured),
        "capability_cards": {},
        "errors": sorted({str(row.get("error") or "") for row in rows if str(row.get("error") or "").strip()}),
    }
    for name in capability_names:
        values = [int((row.get("capability_cards") or {}).get(name) or 0) for row in measured if (row.get("capability_cards") or {}).get(name) is not None]
        summary["capability_cards"][name] = _mean_int(values) if values else None
    return summary


def _recommend_use_mode(*, route_type: str, wins: int, losses: int, measured_docs: int) -> str:
    if measured_docs <= 0:
        return "not_recommended"
    if route_type == "baseline":
        return "baseline"
    if wins >= 3 and losses == 0:
        return "candidate_for_broader_adoption"
    if wins >= 1:
        return "routed_specialist"
    if wins == 0 and losses == 0:
        return "edge_case_challenger"
    return "not_recommended"


def run_benchmark(manifest: Dict[str, Any], *, route_mode: str, max_pages: int, max_chars: int) -> Dict[str, Any]:
    docs = [item for item in list(manifest.get("documents") or []) if isinstance(item, dict)]
    rows: List[Dict[str, Any]] = []
    route = {
        "id": "legacy_default",
        "label": "Legacy OCR route",
        "route_type": "baseline",
        "execution": {
            "kind": "mcp_ocr_dual",
            "engine": "",
            "route_mode": route_mode,
            "consensus": True,
            "use_gateway": True,
            "max_pages": max_pages,
            "max_chars": max_chars,
        },
    }
    paper_index = {"papers": []}
    for doc in docs:
        legacy_doc = dict(doc)
        if not legacy_doc.get("capability_weights"):
            legacy_doc["capability_weights"] = {
                "literal_transcription_fidelity": 0.4,
                "layout_preservation": 0.2,
                "numeric_fidelity": 0.2,
                "cross_element_semantic_reconstruction": 0.1,
                "required_token_coverage": 0.1,
            }
        if not legacy_doc.get("slice"):
            legacy_doc["slice"] = "legacy_regression"
        rows.append(_eval_document_route(legacy_doc, route, timeout_sec=DEFAULT_TIMEOUT_SEC, paper_index=paper_index))
    structure_scores = [int((row.get("metrics") or {}).get("structure_lane") or 0) for row in rows]
    correctness_scores = [int((row.get("metrics") or {}).get("numeric_lane") or 0) for row in rows]
    pass_count = sum(1 for row in rows if bool(row.get("ok")))
    return {
        "ok": pass_count == len(rows) and len(rows) > 0,
        "documents": len(rows),
        "passed_documents": pass_count,
        "failed_documents": len(rows) - pass_count,
        "structure_lane_avg": _mean_int(structure_scores),
        "correctness_lane_avg": _mean_int(correctness_scores),
        "rows": rows,
    }


def run_study(
    manifest: Dict[str, Any],
    *,
    paper_index: Mapping[str, Any],
    timeout_sec: int,
) -> Dict[str, Any]:
    docs = [item for item in list(manifest.get("documents") or []) if isinstance(item, dict)]
    routes = [item for item in list(manifest.get("routes") or []) if isinstance(item, dict)]
    baseline_route_id = str(manifest.get("baseline_route") or "ocr97")
    rows: List[Dict[str, Any]] = []
    for route in routes:
        for doc in docs:
            rows.append(_eval_document_route(doc, route, timeout_sec=timeout_sec, paper_index=paper_index))

    slice_by_route: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    route_summaries: Dict[str, Dict[str, Any]] = {}
    for route in routes:
        route_id = str(route.get("id") or "")
        route_rows = [row for row in rows if str(row.get("route_id") or "") == route_id]
        measured_docs = sum(1 for row in route_rows if str(row.get("status")) == "measured")
        route_summary = {
            "route_id": route_id,
            "route_label": str(route.get("label") or route_id),
            "route_type": str(route.get("route_type") or "challenger"),
            "measured_documents": measured_docs,
            "runtime_unavailable_documents": sum(1 for row in route_rows if str(row.get("status")) == "runtime_unavailable"),
            "study_score_avg": _mean_int(int(row.get("study_score") or 0) for row in route_rows if str(row.get("status")) == "measured"),
            "latency_ms_avg": _mean_int(int((row.get("metrics") or {}).get("latency_ms") or 0) for row in route_rows if str(row.get("status")) == "measured"),
            "research": _route_research_links(route, paper_index),
            "expected_strengths": list(route.get("expected_strengths") or []),
            "literature_claims": list(route.get("literature_claims") or []),
            "operational_complexity": str(route.get("operational_complexity") or "unknown"),
        }
        for slice_name in sorted({str(row.get("slice") or "") for row in route_rows}):
            slice_rows = [row for row in route_rows if str(row.get("slice") or "") == slice_name]
            summary = _summarize_route_slice(slice_rows)
            slice_by_route[route_id][slice_name] = summary
        route_summaries[route_id] = route_summary

    baseline_slices = slice_by_route.get(baseline_route_id, {})
    deltas: List[Dict[str, Any]] = []
    route_findings: List[Dict[str, Any]] = []
    for route in routes:
        route_id = str(route.get("id") or "")
        if route_id == baseline_route_id:
            continue
        wins = 0
        losses = 0
        findings: List[str] = []
        for slice_name, summary in slice_by_route.get(route_id, {}).items():
            baseline_summary = baseline_slices.get(slice_name)
            if not baseline_summary or summary.get("measured_documents", 0) <= 0:
                deltas.append(
                    {
                        "route_id": route_id,
                        "slice": slice_name,
                        "status": "unavailable",
                        "delta_vs_ocr97": None,
                        "judgment": "unavailable",
                        "ocr97_score": baseline_summary.get("study_score_avg") if baseline_summary else None,
                        "challenger_score": None,
                    }
                )
                continue
            baseline_score = int(baseline_summary.get("study_score_avg") or 0)
            challenger_score = int(summary.get("study_score_avg") or 0)
            delta = challenger_score - baseline_score
            if delta >= 3:
                judgment = "win"
                wins += 1
                findings.append(f"{slice_name} (+{delta})")
            elif delta <= -3:
                judgment = "loss"
                losses += 1
            else:
                judgment = "draw"
            deltas.append(
                {
                    "route_id": route_id,
                    "slice": slice_name,
                    "status": "measured",
                    "delta_vs_ocr97": delta,
                    "judgment": judgment,
                    "ocr97_score": baseline_score,
                    "challenger_score": challenger_score,
                    "ocr97_latency_ms": int(baseline_summary.get("latency_ms_avg") or 0),
                    "challenger_latency_ms": int(summary.get("latency_ms_avg") or 0),
                }
            )
        route_summary = route_summaries.get(route_id, {})
        route_summary["wins"] = wins
        route_summary["losses"] = losses
        route_summary["recommended_use_mode"] = _recommend_use_mode(
            route_type=str(route_summary.get("route_type") or "challenger"),
            wins=wins,
            losses=losses,
            measured_docs=int(route_summary.get("measured_documents") or 0),
        )
        route_findings.append(route_summary)

    failure_exemplars = []
    for row in rows:
        if str(row.get("status")) != "measured" or bool(row.get("ok")):
            if str(row.get("status")) not in {"runtime_unavailable", "literature_only"}:
                continue
        failure_exemplars.append(
            {
                "route_id": str(row.get("route_id") or ""),
                "document_id": str(row.get("document_id") or ""),
                "slice": str(row.get("slice") or ""),
                "status": str(row.get("status") or ""),
                "error": str(row.get("error") or ""),
                "missing_tokens": list((row.get("checks") or {}).get("required_missing") or []),
                "study_score": row.get("study_score"),
            }
        )
    failure_exemplars = failure_exemplars[:12]

    return {
        "study_name": str(manifest.get("name") or "ocr97_challenger_study"),
        "generated_at_unix": int(time.time()),
        "baseline_route": baseline_route_id,
        "baseline_label": next((str(route.get("label") or baseline_route_id) for route in routes if str(route.get("id") or "") == baseline_route_id), baseline_route_id),
        "documents": docs,
        "routes": routes,
        "paper_index": paper_index,
        "rows": rows,
        "slice_summaries": slice_by_route,
        "route_summaries": route_summaries,
        "baseline_vs_challenger": deltas,
        "route_findings": route_findings,
        "failure_exemplars": failure_exemplars,
    }


def render_study_markdown(summary: Mapping[str, Any]) -> str:
    baseline_route = str(summary.get("baseline_route") or "ocr97")
    baseline_label = str(summary.get("baseline_label") or baseline_route)
    route_findings = list(summary.get("route_findings") or [])
    deltas = list(summary.get("baseline_vs_challenger") or [])
    paper_index = dict(summary.get("paper_index") or {})
    paper_items = [item for item in list(paper_index.get("papers") or []) if isinstance(item, dict)]
    lines: List[str] = []
    lines.append("# OCR97 Challenger Study")
    lines.append("")
    lines.append(f"Baseline: **{baseline_label}**")
    lines.append("")
    lines.append("## Baseline contract")
    lines.append("")
    lines.append("- `OCR97` is the locked Apollo production OCR path used as the comparison control in this study.")
    lines.append("- Challengers are evaluated as slice-specific alternatives or routed specialists, not global replacements by default.")
    lines.append("- Claims in this report are labeled by source type: local benchmark evidence, literature claim, or engineering inference.")
    lines.append("")
    lines.append("## Headline findings")
    lines.append("")
    if route_findings:
        for route in route_findings:
            label = str(route.get("route_label") or route.get("route_id") or "")
            wins = int(route.get("wins") or 0)
            losses = int(route.get("losses") or 0)
            recommended = str(route.get("recommended_use_mode") or "not_recommended")
            measured_docs = int(route.get("measured_documents") or 0)
            if measured_docs <= 0:
                lines.append(f"- `{label}`: no local measured result in this environment; keep as literature-backed or runtime-pending.")
                continue
            won_slices = [str(item.get("slice") or "") for item in deltas if str(item.get("route_id") or "") == str(route.get("route_id") or "") and str(item.get("judgment") or "") == "win"]
            if won_slices:
                lines.append(f"- `{label}`: beat `{baseline_label}` on {', '.join(won_slices)}; recommended mode `{recommended}`.")
            else:
                lines.append(f"- `{label}`: no measured slice win over `{baseline_label}` in this run; recommended mode `{recommended}`.")
            if losses:
                lines.append(f"- `{label}` fair-trade note: {losses} measured slice losses and average latency {int(route.get('latency_ms_avg') or 0)} ms.")
    else:
        lines.append("- No challenger routes were configured.")
    lines.append("")
    lines.append("## Slice deltas vs OCR97")
    lines.append("")
    lines.append("| Route | Slice | OCR97 | Challenger | Delta | Judgment |")
    lines.append("|------|-------|------:|-----------:|------:|----------|")
    for item in deltas:
        label = next((str(route.get("route_label") or route.get("route_id") or "") for route in route_findings if str(route.get("route_id") or "") == str(item.get("route_id") or "")), str(item.get("route_id") or ""))
        delta = item.get("delta_vs_ocr97")
        delta_text = "" if delta is None else f"{int(delta):+d}"
        lines.append(
            f"| {label} | {str(item.get('slice') or '')} | {item.get('ocr97_score') if item.get('ocr97_score') is not None else ''} | {item.get('challenger_score') if item.get('challenger_score') is not None else ''} | {delta_text} | {str(item.get('judgment') or '')} |"
        )
    lines.append("")
    lines.append("## Fair-trade notes")
    lines.append("")
    lines.append("- Local benchmark evidence in this report comes from the current workspace and runtime only.")
    lines.append("- Routes with `runtime_unavailable` or `literature_only` status are included for planning, but they do not count as empirical wins.")
    lines.append("- If a challenger wins a slice with materially higher latency or operational complexity, it should be routed only where that slice matters.")
    lines.append("")
    lines.append("## Research corpus")
    lines.append("")
    for item in paper_items:
        title = str(item.get("title") or item.get("id") or "")
        published = str(item.get("publish_date") or "")
        source_url = str(item.get("source_url") or "")
        local_pdf = str(item.get("local_pdf") or "")
        note = str(item.get("relevance_note") or "")
        lines.append(f"- `{title}` ({published})")
        if local_pdf:
            lines.append(f"  Local PDF: `{local_pdf}`")
        if source_url:
            lines.append(f"  Source: {source_url}")
        if note:
            lines.append(f"  Relevance: {note}")
    lines.append("")
    lines.append("## Failure exemplars")
    lines.append("")
    failures = list(summary.get("failure_exemplars") or [])
    if not failures:
        lines.append("- No failure exemplars were recorded.")
    else:
        for item in failures:
            lines.append(
                f"- `{item.get('route_id')}` on `{item.get('document_id')}` ({item.get('slice')}): `{item.get('status')}` / `{item.get('error')}`"
            )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def download_papers(
    paper_manifest: Mapping[str, Any],
    *,
    output_root: Path,
    index_output: Path,
    force: bool,
    timeout_sec: int,
) -> Dict[str, Any]:
    from Apollo.model_policy import assert_model_storage_allowed, configure_model_storage_env
    configure_model_storage_env(os.environ)
    assert_model_storage_allowed(extra_paths={
        "ocr_benchmark_output_root": str(output_root),
        "ocr_benchmark_index_output": str(index_output),
    })
    rows = []
    for entry in [item for item in list(paper_manifest.get("papers") or []) if isinstance(item, dict)]:
        local_pdf = output_root / str(entry.get("local_pdf") or f"papers/{_slug(str(entry.get('id') or 'paper'))}.pdf")
        local_pdf.parent.mkdir(parents=True, exist_ok=True)
        status = "downloaded"
        error = ""
        if force or not local_pdf.exists():
            try:
                req = urllib.request.Request(
                    str(entry.get("pdf_url") or ""),
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    local_pdf.write_bytes(resp.read())
            except Exception as exc:
                status = "failed"
                error = f"download_failed:{type(exc).__name__}:{exc}"
        else:
            status = "cached"
        size_bytes = int(local_pdf.stat().st_size) if local_pdf.exists() else 0
        sha256 = _sha256(local_pdf) if local_pdf.exists() else ""
        row = dict(entry)
        row["local_pdf"] = str(local_pdf.relative_to(output_root)).replace("\\", "/")
        row["download_status"] = status
        row["download_error"] = error
        row["size_bytes"] = size_bytes
        row["sha256"] = sha256
        rows.append(row)
    result = {"papers": rows}
    _write_json(index_output, result)
    return result


def _parse_args(argv: List[str]) -> argparse.Namespace:
    if argv and argv[0] not in {"benchmark", "study", "download-papers"}:
        legacy = argparse.ArgumentParser(description="Run Apollo OCR benchmark harness.")
        legacy.add_argument("--manifest", required=True, help="Path to benchmark manifest JSON.")
        legacy.add_argument("--output", default="", help="Optional output JSON path.")
        legacy.add_argument("--route-mode", default="quality_first", choices=["quality_first", "balanced"])
        legacy.add_argument("--max-pages", type=int, default=4)
        legacy.add_argument("--max-chars", type=int, default=20000)
        args = legacy.parse_args(argv)
        args.command = "benchmark"
        return args

    parser = argparse.ArgumentParser(description="Apollo OCR97 benchmark and challenger study harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    benchmark = sub.add_parser("benchmark", help="Run the legacy OCR benchmark flow.")
    benchmark.add_argument("--manifest", required=True, help="Path to benchmark manifest JSON.")
    benchmark.add_argument("--output", default="", help="Optional output JSON path.")
    benchmark.add_argument("--route-mode", default="quality_first", choices=["quality_first", "balanced"])
    benchmark.add_argument("--max-pages", type=int, default=4)
    benchmark.add_argument("--max-chars", type=int, default=20000)

    download = sub.add_parser("download-papers", help="Download the OCR97 research corpus PDFs.")
    download.add_argument("--paper-manifest", required=True, help="Path to paper manifest JSON.")
    download.add_argument("--output-root", required=True, help="Root directory for the local paper cache.")
    download.add_argument("--index-output", required=True, help="Output JSON index path.")
    download.add_argument("--force", action="store_true", help="Redownload PDFs even if cached.")
    download.add_argument("--timeout-sec", type=int, default=60)

    study = sub.add_parser("study", help="Run the OCR97 challenger study.")
    study.add_argument("--manifest", required=True, help="Path to challenger study manifest JSON.")
    study.add_argument("--paper-index", required=True, help="Path to downloaded paper index JSON.")
    study.add_argument("--output-json", required=True, help="Output JSON summary path.")
    study.add_argument("--output-md", default="", help="Optional Markdown report path.")
    study.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    if args.command == "download-papers":
        paper_manifest = _load_paper_manifest(_workspace_path(args.paper_manifest))
        result = download_papers(
            paper_manifest,
            output_root=_workspace_path(args.output_root),
            index_output=_workspace_path(args.index_output),
            force=bool(args.force),
            timeout_sec=max(10, int(args.timeout_sec)),
        )
        print(json.dumps(result, indent=2))
        failed = [item for item in list(result.get("papers") or []) if str(item.get("download_status") or "") == "failed"]
        return 0 if not failed else 2

    if args.command == "study":
        manifest = _load_manifest(_workspace_path(args.manifest))
        paper_index = _load_json(_workspace_path(args.paper_index))
        summary = run_study(manifest, paper_index=paper_index, timeout_sec=max(10, int(args.timeout_sec)))
        _write_json(_workspace_path(args.output_json), summary)
        if str(args.output_md or "").strip():
            md = render_study_markdown(summary)
            output_md = _workspace_path(args.output_md)
            output_md.parent.mkdir(parents=True, exist_ok=True)
            output_md.write_text(md, encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 0

    manifest = _load_manifest(_workspace_path(args.manifest))
    summary = run_benchmark(
        manifest,
        route_mode=str(args.route_mode),
        max_pages=max(1, int(args.max_pages)),
        max_chars=max(1000, int(args.max_chars)),
    )
    out = json.dumps(summary, indent=2)
    if args.output:
        _workspace_path(args.output).write_text(out + "\n", encoding="utf-8")
    print(out)
    return 0 if bool(summary.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
