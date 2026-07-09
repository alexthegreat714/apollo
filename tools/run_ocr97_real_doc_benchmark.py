from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent
DEFAULT_MANIFEST = APOLLO_ROOT / "config" / "ocr97_real_documents_manifest.json"
DEFAULT_REPORT_ROOT = APOLLO_ROOT / "reports" / "ocr97_real_docs"
DEFAULT_CACHE_ROOT = Path(os.getenv("APOLLO_OCR97_SOURCE_CACHE_ROOT", r"D:\ApolloModels\benchmarks\ocr97_real_docs\source_cache"))
README_PATH = APOLLO_ROOT / "README_OCR97.md"
README_START = "<!-- OCR97_REAL_DOC_BENCHMARK_START -->"
README_END = "<!-- OCR97_REAL_DOC_BENCHMARK_END -->"
GX10_OLLAMA_URL = "http://127.0.0.1:11434"
GX10_OCR_MODEL = "qwen3-vl:32b"
GX10_CHAT_MODEL = "gemma3:12b"

os.environ.setdefault("APOLLO_GB10_QWEN_OLLAMA_URL", GX10_OLLAMA_URL)
os.environ.setdefault("APOLLO_GB10_QWEN_OCR_MODEL", GX10_OCR_MODEL)
os.environ.setdefault("APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL", GX10_OCR_MODEL)
os.environ.setdefault("VISION_OLLAMA_URL", GX10_OLLAMA_URL)
os.environ.setdefault("VISION_QWEN3VL_MODEL", GX10_OCR_MODEL)
os.environ.setdefault("OLLAMA_MODEL", GX10_CHAT_MODEL)

if str(ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINEERING_ROOT))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _slug_filename(doc: Dict[str, Any]) -> str:
    doc_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(doc.get("id") or "document")).strip("_")
    suffix = ".pdf"
    url = str(doc.get("url") or "")
    for candidate in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        if url.lower().split("?", 1)[0].endswith(candidate):
            suffix = candidate
            break
    return f"{doc_id}{suffix}"


def load_manifest(path: Path) -> Dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    docs = manifest.get("documents")
    if not isinstance(docs, list) or not docs:
        raise ValueError("manifest_documents_required")
    for doc in docs:
        if not isinstance(doc, dict) or not doc.get("id") or not doc.get("url"):
            raise ValueError("document_id_and_url_required")
    return manifest


def download_document(doc: Dict[str, Any], cache_root: Path, *, force: bool = False) -> Tuple[Path, Dict[str, Any]]:
    from Apollo.model_policy import assert_model_storage_allowed, configure_model_storage_env
    configure_model_storage_env(os.environ)
    assert_model_storage_allowed(extra_paths={"ocr97_source_cache": str(cache_root)})
    cache_root.mkdir(parents=True, exist_ok=True)
    out_path = cache_root / _slug_filename(doc)
    if out_path.exists() and out_path.stat().st_size > 0 and not force:
        return out_path, {
            "ok": True,
            "cached": True,
            "path": str(out_path),
            "bytes": out_path.stat().st_size,
            "sha256": _sha256(out_path),
        }

    request = urllib.request.Request(
        str(doc["url"]),
        headers={
            "User-Agent": "Apollo OCR97 real-document benchmark/1.0",
            "Accept": "application/pdf,image/*,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
    if len(data) < 1024:
        raise RuntimeError(f"download_too_small:{doc.get('id')}")
    out_path.write_bytes(data)
    return out_path, {
        "ok": True,
        "cached": False,
        "path": str(out_path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_ocr97_on_document(path: Path, doc: Dict[str, Any], *, max_chars: int, route_mode: str) -> Dict[str, Any]:
    from common.ocr_dual_tool import ocr_dual

    return ocr_dual(
        {
            "path": str(path),
            "goal": str(doc.get("goal") or doc.get("title") or "Extract text from this document."),
            "engine": "auto",
            "route_mode": route_mode,
            "max_pages": int(doc.get("max_pages") or 5),
            "max_chars": max_chars,
            "consensus": True,
            "model_requirement": {
                "provider": "gx10_ollama",
                "url": os.getenv("APOLLO_GB10_QWEN_OLLAMA_URL", GX10_OLLAMA_URL),
                "model": os.getenv("APOLLO_GB10_QWEN_OCR_MODEL", GX10_OCR_MODEL),
                "offline_model_only": True,
            },
        }
    )


def run_baseline_on_document(path: Path, doc: Dict[str, Any], *, max_chars: int) -> Dict[str, Any]:
    """Run the pre-OCR97 local OCR lane used as a comparison control."""
    from common.ocr_dual_tool import ocr_dual

    return ocr_dual(
        {
            "path": str(path),
            "goal": str(doc.get("goal") or doc.get("title") or "Extract text from this document."),
            "engine": str(doc.get("baseline_engine") or "tesseract"),
            "route_mode": "balanced",
            "max_pages": int(doc.get("max_pages") or 5),
            "max_chars": max_chars,
            "consensus": False,
            "gb10_enabled": False,
            "use_gateway": False,
        }
    )


def _result_text(result: Dict[str, Any]) -> str:
    for key in ("markdown", "text", "raw_text"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _quality_score(result: Dict[str, Any]) -> float:
    quality = result.get("quality")
    if isinstance(quality, dict):
        raw = quality.get("score", quality.get("overall", quality.get("confidence")))
        try:
            score = float(raw)
            return score * 100.0 if score <= 1.0 else score
        except Exception:
            pass
    try:
        conf = float(result.get("confidence"))
        return conf * 100.0 if conf <= 1.0 else conf
    except Exception:
        return 0.0


def _contains_number(text: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent|million|billion|trillion|dollars?)?\b", text, re.I))


def _latency_ms(result: Dict[str, Any]) -> int:
    for key in ("latency_ms", "duration_ms", "elapsed_ms"):
        try:
            raw = result.get(key)
            if raw is not None:
                return int(float(raw))
        except Exception:
            pass
    attempts = result.get("attempts")
    if isinstance(attempts, list):
        total = 0.0
        found = False
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            try:
                total += float(attempt.get("latency_ms") or 0.0)
                found = True
            except Exception:
                pass
        if found:
            return int(total)
    return 0


def _timeout_like(result: Dict[str, Any]) -> bool:
    haystack = " ".join(
        str(result.get(key) or "")
        for key in ("error", "fallback_reason", "reason")
    ).lower()
    if "timeout" in haystack or "timed out" in haystack:
        return True
    attempts = result.get("attempts")
    if isinstance(attempts, list):
        return any("timeout" in str(item).lower() or "timed out" in str(item).lower() for item in attempts)
    return False


def score_document(doc: Dict[str, Any], result: Dict[str, Any], download: Dict[str, Any]) -> Dict[str, Any]:
    text = _result_text(result)
    text_lc = text.lower()
    expected_terms = [str(t) for t in doc.get("expected_terms") or []]
    expected_regex = [str(t) for t in doc.get("expected_regex") or []]

    term_hits = [term for term in expected_terms if term.lower() in text_lc]
    regex_hits = []
    regex_misses = []
    for pattern in expected_regex:
        try:
            if re.search(pattern, text, re.I):
                regex_hits.append(pattern)
            else:
                regex_misses.append(pattern)
        except re.error:
            regex_misses.append(pattern)

    expected_total = max(1, len(expected_terms) + len(expected_regex))
    expected_hit_count = len(term_hits) + len(regex_hits)
    token_score = round((expected_hit_count / expected_total) * 100.0, 2)
    min_chars = int(doc.get("min_chars") or 1000)
    char_score = min(100.0, (len(text) / max(1, min_chars)) * 100.0)
    quality_score = max(0.0, min(100.0, _quality_score(result)))
    numeric_score = 100.0 if _contains_number(text) else 35.0
    ok_score = 100.0 if result.get("ok") else 0.0
    final_score = round(
        (token_score * 0.45)
        + (char_score * 0.20)
        + (quality_score * 0.15)
        + (numeric_score * 0.10)
        + (ok_score * 0.10),
        2,
    )

    missing_terms = [term for term in expected_terms if term not in term_hits]
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "kind": doc.get("kind"),
        "ok": bool(result.get("ok")),
        "score": final_score,
        "token_score": token_score,
        "char_count": len(text),
        "char_score": round(char_score, 2),
        "quality_score": round(quality_score, 2),
        "numeric_score": numeric_score,
        "engine": result.get("engine"),
        "latency_ms": _latency_ms(result),
        "timeout_like": _timeout_like(result),
        "route_mode": result.get("route_mode"),
        "download": download,
        "found_terms": term_hits,
        "missing_terms": missing_terms,
        "found_regex": regex_hits,
        "missing_regex": regex_misses,
        "document_features": result.get("document_features"),
        "visual_controls_count": len(result.get("visual_controls") or []) if isinstance(result.get("visual_controls"), list) else 0,
        "charts_count": len(result.get("charts") or []) if isinstance(result.get("charts"), list) else 0,
        "figures_count": len(result.get("figures") or []) if isinstance(result.get("figures"), list) else 0,
        "error": result.get("error") or "",
    }


def compare_ocr97_to_baseline(row: Dict[str, Any], baseline_row: Dict[str, Any]) -> Dict[str, Any]:
    score_delta = round(float(row.get("score") or 0) - float(baseline_row.get("score") or 0), 2)
    latency_delta_ms = int(row.get("latency_ms") or 0) - int(baseline_row.get("latency_ms") or 0)
    timeout_like = bool(row.get("timeout_like") or baseline_row.get("timeout_like"))
    if timeout_like:
        judgment = "inconclusive_timeout"
    elif score_delta >= 7:
        judgment = "ocr97_better"
    elif score_delta <= -7:
        judgment = "baseline_better"
    else:
        judgment = "similar"
    return {
        "id": row.get("id"),
        "ocr97_score": row.get("score"),
        "baseline_score": baseline_row.get("score"),
        "score_delta": score_delta,
        "ocr97_engine": row.get("engine"),
        "baseline_engine": baseline_row.get("engine"),
        "ocr97_latency_ms": row.get("latency_ms"),
        "baseline_latency_ms": baseline_row.get("latency_ms"),
        "latency_delta_ms": latency_delta_ms,
        "timeout_like": timeout_like,
        "judgment": judgment,
    }


def summarize_comparison(comparisons: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(comparisons)
    decisive = [row for row in rows if row.get("judgment") != "inconclusive_timeout"]
    timeout_count = sum(1 for row in rows if row.get("timeout_like"))
    better = sum(1 for row in decisive if row.get("judgment") == "ocr97_better")
    worse = sum(1 for row in decisive if row.get("judgment") == "baseline_better")
    similar = sum(1 for row in decisive if row.get("judgment") == "similar")
    avg_delta = round(sum(float(row.get("score_delta") or 0) for row in decisive) / max(1, len(decisive)), 2)
    if not decisive:
        verdict = "comparison_inconclusive_timeouts"
    elif better > worse and avg_delta > 0:
        verdict = "ocr97_better_on_decisive_docs"
    elif worse > better and avg_delta < 0:
        verdict = "baseline_better_on_decisive_docs"
    else:
        verdict = "mixed_or_similar"
    return {
        "document_count": len(rows),
        "decisive_count": len(decisive),
        "timeout_count": timeout_count,
        "ocr97_better_count": better,
        "baseline_better_count": worse,
        "similar_count": similar,
        "average_decisive_delta": avg_delta,
        "verdict": verdict,
    }


def summarize_scores(rows: Iterable[Dict[str, Any]], *, score_floor: float) -> Dict[str, Any]:
    scored = list(rows)
    average = round(sum(float(row.get("score") or 0.0) for row in scored) / max(1, len(scored)), 2)
    minimum = round(min((float(row.get("score") or 0.0) for row in scored), default=0.0), 2)
    failures = [row for row in scored if not row.get("ok")]
    if average >= score_floor and minimum >= 80 and not failures:
        verdict = "defends_current_97_with_real_document_evidence"
        recommended_score = 97
    elif average >= 88 and minimum >= 75:
        verdict = "supports_90s_score_but_needs_rescore_from_97"
        recommended_score = 92
    elif average >= 82:
        verdict = "capability_present_but_score_should_drop_until_gaps_close"
        recommended_score = 86
    else:
        verdict = "real_document_evidence_does_not_defend_current_score"
        recommended_score = 78
    return {
        "average_score": average,
        "minimum_score": minimum,
        "document_count": len(scored),
        "failure_count": len(failures),
        "score_floor_to_defend_97": score_floor,
        "verdict": verdict,
        "recommended_score": recommended_score,
    }


def render_markdown(
    manifest: Dict[str, Any],
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    run_id: str,
    comparison_summary: Dict[str, Any] | None = None,
    comparisons: List[Dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"# OCR97 Real Document Benchmark - {run_id}",
        "",
        f"- Corpus: `{manifest.get('name')}`",
        f"- Documents: {summary['document_count']}",
        f"- Average score: {summary['average_score']}",
        f"- Minimum score: {summary['minimum_score']}",
        f"- Verdict: `{summary['verdict']}`",
        f"- Recommended OCR97 score: {summary['recommended_score']}/100",
        f"- OCR97 vs old OCR: `{(comparison_summary or {}).get('verdict', 'not_run')}`",
        "",
        "| Document | Score | OK | Engine | Missing Evidence |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        missing = ", ".join((row.get("missing_terms") or [])[:3] + (row.get("missing_regex") or [])[:2])
        lines.append(
            f"| {row.get('id')} | {row.get('score')} | {row.get('ok')} | {row.get('engine') or 'unknown'} | {missing or 'none'} |"
        )
    if comparison_summary:
        lines.extend([
            "",
            "## OCR97 vs Old OCR Comparison",
            "",
            f"- Verdict: `{comparison_summary.get('verdict')}`",
            f"- Decisive docs: {comparison_summary.get('decisive_count')}/{comparison_summary.get('document_count')}",
            f"- OCR97 better: {comparison_summary.get('ocr97_better_count')}",
            f"- Old OCR better: {comparison_summary.get('baseline_better_count')}",
            f"- Similar: {comparison_summary.get('similar_count')}",
            f"- Timeout/inconclusive: {comparison_summary.get('timeout_count')}",
            f"- Average decisive score delta: {comparison_summary.get('average_decisive_delta')}",
            "",
            "| Document | OCR97 | Old OCR | Delta | Latency Delta ms | Judgment |",
            "|---|---:|---:|---:|---:|---|",
        ])
        for item in comparisons or []:
            lines.append(
                f"| {item.get('id')} | {item.get('ocr97_score')} | {item.get('baseline_score')} | "
                f"{item.get('score_delta')} | {item.get('latency_delta_ms')} | {item.get('judgment')} |"
            )
    lines.extend(["", "## Document Details", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row.get('id')}",
                "",
                f"- Title: {row.get('title')}",
                f"- Kind: {row.get('kind')}",
                f"- Score: {row.get('score')}",
                f"- Characters extracted: {row.get('char_count')}",
                f"- Latency: {row.get('latency_ms', 0)} ms",
                f"- Timeout-like: {row.get('timeout_like', False)}",
                f"- Found terms: {', '.join(row.get('found_terms') or []) or 'none'}",
                f"- Missing terms: {', '.join(row.get('missing_terms') or []) or 'none'}",
                f"- Visual controls detected: {row.get('visual_controls_count')}",
                f"- Charts detected: {row.get('charts_count')}",
                f"- Figures detected: {row.get('figures_count')}",
                f"- Source cache: `{(row.get('download') or {}).get('path', '')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def update_readme(report_md: str, summary: Dict[str, Any], run_id: str, report_path: Path) -> None:
    block = "\n".join(
        [
            README_START,
            "## Real Document Capability Evidence",
            "",
            f"**Latest run:** `{run_id}`",
            f"**Average real-document score:** `{summary['average_score']}`",
            f"**Minimum document score:** `{summary['minimum_score']}`",
            f"**Verdict:** `{summary['verdict']}`",
            f"**Recommended OCR97 score:** `{summary['recommended_score']}/100`",
            "",
            f"Full report: `{report_path}`",
            "",
            "This section is generated by `tools/run_ocr97_real_doc_benchmark.py` from public, real-world documents.",
            README_END,
        ]
    )
    existing = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else "# OCR97\n"
    pattern = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if pattern.search(existing):
        updated = pattern.sub(lambda _match: block, existing)
    else:
        updated = existing.rstrip() + "\n\n" + block + "\n"
    README_PATH.write_text(updated, encoding="utf-8")


def run_benchmark(
    *,
    manifest_path: Path,
    report_root: Path,
    cache_root: Path,
    update_readme_flag: bool,
    download_only: bool,
    force_download: bool,
    route_mode: str,
    max_chars: int,
    compare_baseline: bool,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    run_id = f"ocr97_real_docs_{_utc_now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = report_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    baseline_rows: List[Dict[str, Any]] = []
    comparisons: List[Dict[str, Any]] = []
    raw_results: Dict[str, Any] = {}
    for doc in manifest["documents"]:
        try:
            local_path, download = download_document(doc, cache_root, force=force_download)
            if download_only:
                result = {"ok": True, "engine": "download_only", "text": "download only"}
            else:
                start = time.perf_counter()
                result = run_ocr97_on_document(local_path, doc, max_chars=max_chars, route_mode=route_mode)
                result.setdefault("latency_ms", int((time.perf_counter() - start) * 1000))
            raw_results[str(doc["id"])] = result
            row = score_document(doc, result, download)
            if compare_baseline and not download_only:
                try:
                    baseline_start = time.perf_counter()
                    baseline_result = run_baseline_on_document(local_path, doc, max_chars=max_chars)
                    baseline_result.setdefault("latency_ms", int((time.perf_counter() - baseline_start) * 1000))
                    raw_results[f"{doc['id']}::baseline"] = baseline_result
                    baseline_row = score_document(doc, baseline_result, download)
                except Exception as exc:
                    baseline_row = {
                        "id": doc.get("id"),
                        "title": doc.get("title"),
                        "kind": doc.get("kind"),
                        "ok": False,
                        "score": 0,
                        "error": str(exc),
                        "download": download,
                        "found_terms": [],
                        "missing_terms": doc.get("expected_terms") or [],
                        "found_regex": [],
                        "missing_regex": doc.get("expected_regex") or [],
                        "latency_ms": 0,
                        "timeout_like": "timeout" in str(exc).lower(),
                    }
                baseline_rows.append(baseline_row)
                comparisons.append(compare_ocr97_to_baseline(row, baseline_row))
        except Exception as exc:
            row = {
                "id": doc.get("id"),
                "title": doc.get("title"),
                "kind": doc.get("kind"),
                "ok": False,
                "score": 0,
                "error": str(exc),
                "download": {},
                "found_terms": [],
                "missing_terms": doc.get("expected_terms") or [],
                "found_regex": [],
                "missing_regex": doc.get("expected_regex") or [],
                "latency_ms": 0,
                "timeout_like": "timeout" in str(exc).lower() or "timed out" in str(exc).lower(),
            }
        rows.append(row)

    score_floor = float(manifest.get("score_floor_to_defend_97") or 90)
    summary = summarize_scores(rows, score_floor=score_floor)
    comparison_summary = summarize_comparison(comparisons) if comparisons else {"verdict": "not_run", "document_count": 0}
    payload = {
        "ok": summary["failure_count"] == 0,
        "run_id": run_id,
        "created_at": _utc_now().isoformat().replace("+00:00", "Z"),
        "manifest": str(manifest_path),
        "model_requirement": {
            "provider": "gx10_ollama",
            "url": os.getenv("APOLLO_GB10_QWEN_OLLAMA_URL", GX10_OLLAMA_URL),
            "model": os.getenv("APOLLO_GB10_QWEN_OCR_MODEL", GX10_OCR_MODEL),
            "offline_model_only": True,
            "source_under_test": "common.ocr_dual_tool.ocr_dual",
        },
        "summary": summary,
        "comparison_summary": comparison_summary,
        "documents": rows,
        "baseline_documents": baseline_rows,
        "comparisons": comparisons,
    }
    report_md = render_markdown(manifest, rows, summary, run_id, comparison_summary, comparisons)
    report_path = run_dir / "report.md"
    summary_path = run_dir / "summary.json"
    raw_path = run_dir / "raw_ocr_results.json"
    report_path.write_text(report_md, encoding="utf-8")
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_path.write_text(json.dumps(raw_results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    payload["artifacts"] = {"report": str(report_path), "summary": str(summary_path), "raw": str(raw_path)}

    if update_readme_flag and not download_only:
        update_readme(report_md, summary, run_id, report_path)

    return payload


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OCR97 against real public documents and update README evidence.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--update-readme", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--route-mode", default="quality_first")
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--skip-baseline", action="store_true", help="Do not run the old OCR comparison control.")
    args = parser.parse_args(argv)

    payload = run_benchmark(
        manifest_path=args.manifest,
        report_root=args.report_root,
        cache_root=args.cache_root,
        update_readme_flag=args.update_readme,
        download_only=args.download_only,
        force_download=args.force_download,
        route_mode=args.route_mode,
        max_chars=args.max_chars,
        compare_baseline=not args.skip_baseline,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
