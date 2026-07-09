from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests
from PIL import Image, ImageDraw

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None

import common.ocr_dual_tool as ocr_dual_tool
from common.ocr_dual_tool import (
    _finance_consistency_checks,
    _finbert_eval_signal,
    _phase2_preprocessing_status,
    _phase2_verification_status,
    _tableformer_or_lgpma_reconstruct,
    ocr_dual,
)


ENGINEERING_ROOT = Path(__file__).resolve().parents[2]
APOLLO_ROOT = Path(__file__).resolve().parents[1]
OCR_TOOL_PATH = ENGINEERING_ROOT / "common" / "ocr_dual_tool.py"
README_PATH = APOLLO_ROOT / "README.md"
ARTIFACT_ROOT = ENGINEERING_ROOT / "artifacts" / "ocr_phase2"
DEFAULT_FINBERT_URL = "http://127.0.0.1:5221/ocr/finbert/eval"
DEFAULT_TABLEFORMER_URL = "http://127.0.0.1:5221/ocr/table/reconstruct"
DEFAULT_DOCUNET_URL = "http://127.0.0.1:5221/ocr/docunet/rectify"
DEFAULT_REALESRGAN_URL = "http://127.0.0.1:5221/ocr/realesrgan/upscale"
DEFAULT_TEST_COMMAND = [
    "pytest",
    "Apollo/tests/test_ocr_dual_tool.py",
    "Apollo/tests/test_nightly_pipeline.py",
    "Apollo/tests/test_ocr_phase2_truth_gate.py",
    "Apollo/tests/test_ocr_phase2_services.py",
    "-q",
]

SYMBOL_PATTERNS = {
    "_preprocess_variants": r"def\s+_preprocess_variants\s*\(",
    "_line_vote_majority": r"def\s+_line_vote_majority\s*\(",
    "_merge_region_retry_fragments": r"def\s+_merge_region_retry_fragments\s*\(",
    "_finance_consistency_checks": r"def\s+_finance_consistency_checks\s*\(",
    "_finbert_eval_signal": r"def\s+_finbert_eval_signal\s*\(",
    "_tableformer_or_lgpma_reconstruct": r"def\s+_tableformer_or_lgpma_reconstruct\s*\(",
    "_sauvola_binarize": r"def\s+_sauvola_binarize\s*\(",
    "_clahe_enhance": r"def\s+_clahe_enhance\s*\(",
    "_stack_column_parts": r"def\s+_stack_column_parts\s*\(",
    "_split_columns_surya": r"def\s+_split_columns_surya\s*\(",
    "APOLLO_OCR_DOCUNET_URL": r"APOLLO_OCR_DOCUNET_URL",
    "APOLLO_OCR_REALESRGAN_URL": r"APOLLO_OCR_REALESRGAN_URL",
    "APOLLO_OCR_FINBERT_URL": r"APOLLO_OCR_FINBERT_URL",
    "APOLLO_OCR_TABLEFORMER_URL": r"APOLLO_OCR_TABLEFORMER_URL",
    "APOLLO_OCR_LGPMA_URL": r"APOLLO_OCR_LGPMA_URL",
    "confidence_map_region_retry": r"confidence_map_region_retry",
    "stopping_criteria_small_improvement": r"stopping_criteria_small_improvement",
    "strict_literal": r"strict_literal",
    "numeric_focus": r"numeric_focus",
}


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_ocr_tool() -> tuple[str, int]:
    text = OCR_TOOL_PATH.read_text(encoding="utf-8")
    return text, len(text.splitlines())


def _symbol_presence(source_text: str) -> Dict[str, bool]:
    return {name: bool(re.search(pattern, source_text)) for name, pattern in SYMBOL_PATTERNS.items()}


def _item(classification: str, *, basis: Iterable[str], notes: str = "") -> Dict[str, Any]:
    return {
        "classification": classification,
        "basis": list(basis),
        "notes": notes,
    }


def _bind_live_service_defaults() -> None:
    ocr_dual_tool.DEFAULT_OCR_DOCUNET_URL = str(os.getenv("APOLLO_OCR_DOCUNET_URL", DEFAULT_DOCUNET_URL)).strip()
    ocr_dual_tool.DEFAULT_OCR_REALESRGAN_URL = str(os.getenv("APOLLO_OCR_REALESRGAN_URL", DEFAULT_REALESRGAN_URL)).strip()
    ocr_dual_tool.DEFAULT_OCR_FINBERT_URL = str(os.getenv("APOLLO_OCR_FINBERT_URL", DEFAULT_FINBERT_URL)).strip()
    ocr_dual_tool.DEFAULT_OCR_TABLEFORMER_URL = str(os.getenv("APOLLO_OCR_TABLEFORMER_URL", DEFAULT_TABLEFORMER_URL)).strip()
    ocr_dual_tool.DEFAULT_OCR_LGPMA_URL = str(os.getenv("APOLLO_OCR_LGPMA_URL", "")).strip()


def _build_items(symbols: Dict[str, bool], table_source_path: str = "") -> Dict[str, Dict[str, Any]]:
    _bind_live_service_defaults()
    preprocessing = _phase2_preprocessing_status()
    finance_checks = _finance_consistency_checks(
        "Assets 100\nLiabilities 60\nEquity 40\nAllocation 50%\nCash 50%",
        tables=[{"rows": [{"cells": ["Assets", "100"]}, {"cells": ["Liabilities", "60"]}, {"cells": ["Equity", "40"]}]}],
    )
    finbert_eval = _finbert_eval_signal("Balance sheet with margin, equity, and liquidity checks.")
    table_reconstruction = _tableformer_or_lgpma_reconstruct(
        "| Assets | 100 |\n| Liabilities | 60 |\n| Equity | 40 |",
        source_path=table_source_path,
    )
    verification = _phase2_verification_status(finance_checks, finbert_eval, table_reconstruction)
    finbert_classification = str((verification.get("finbert") or {}).get("classification") or "service_hook_only")
    table_classification = str((verification.get("table_reconstruction") or {}).get("classification") or "service_hook_only")
    return {
        "preprocessing.deskew": _item(
            preprocessing["deskew"]["classification"] if symbols["_preprocess_variants"] else "not_started",
            basis=["_preprocess_variants", "_deskew_image"],
            notes="OpenCV deskew path feeds preprocessing variants.",
        ),
        "preprocessing.sauvola": _item(
            "implemented_literal" if symbols["_sauvola_binarize"] else "not_started",
            basis=["_sauvola_binarize", "_adaptive_binarize"],
            notes="Adaptive binarization is backed by an explicit Sauvola implementation.",
        ),
        "preprocessing.clahe": _item(
            "implemented_literal" if symbols["_clahe_enhance"] else "not_started",
            basis=["_clahe_enhance", "_adaptive_binarize"],
            notes="CLAHE is a separate preprocessing stage before Sauvola.",
        ),
        "preprocessing.surya_layout": _item(
            preprocessing["surya_layout"]["classification"],
            basis=["_split_columns_surya", "_split_columns"],
            notes="Surya is primary when available; heuristic split remains the fallback.",
        ),
        "preprocessing.page_split_reading_order": _item(
            "implemented_literal" if symbols["_stack_column_parts"] else "not_started",
            basis=["_stack_column_parts", "_preprocess_variants"],
            notes="Detected columns are stacked in reading order instead of stitched horizontally.",
        ),
        "preprocessing.docunet": _item(
            preprocessing["docunet"]["classification"],
            basis=["APOLLO_OCR_DOCUNET_URL"],
            notes=(
                "DocUNet is backed by the live Aegis Paddle UVDoc service."
                if preprocessing["docunet"]["classification"] == "implemented_literal"
                else "DocUNet remains an explicit worker/service hook until a healthy backend is live-tested."
            ),
        ),
        "preprocessing.realesrgan": _item(
            preprocessing["realesrgan"]["classification"],
            basis=["APOLLO_OCR_REALESRGAN_URL"],
            notes=(
                "Real-ESRGAN is backed by the live Aegis super-resolution service."
                if preprocessing["realesrgan"]["classification"] == "implemented_literal"
                else "Real-ESRGAN remains an explicit worker/service hook until a healthy backend is live-tested."
            ),
        ),
        "ensemble.majority_vote": _item(
            "implemented_literal" if symbols["_line_vote_majority"] else "not_started",
            basis=["_line_vote_majority", "_run_policy_route"],
            notes="Consensus voting operates over normalized candidate outputs.",
        ),
        "ensemble.self_consistency_sampling": _item(
            "implemented_literal" if symbols["strict_literal"] and symbols["numeric_focus"] else "not_started",
            basis=["_gb10_qwen_ocr", "strict_literal", "numeric_focus"],
            notes="Qwen sampling is explicit and feeds the vote winner only when quality improves or holds.",
        ),
        "ensemble.confidence_map_region_retry": _item(
            "implemented_literal" if symbols["_merge_region_retry_fragments"] and symbols["confidence_map_region_retry"] else "not_started",
            basis=["_merge_region_retry_fragments", "confidence_map_region_retry", "_crop_regions_for_retry"],
            notes="Low-confidence spans are retried and merged back with region provenance.",
        ),
        "verification.finance_consistency": _item(
            "implemented_literal" if symbols["_finance_consistency_checks"] else "not_started",
            basis=["_finance_consistency_checks"],
            notes="Deterministic financial-math checks run on text and reconstructed tables.",
        ),
        "verification.finbert_verifier": _item(
            finbert_classification,
            basis=["_finbert_eval_signal", "APOLLO_OCR_FINBERT_URL"],
            notes=(
                "FinBERT is wired through the live Aegis evaluation service."
                if finbert_classification == "implemented_literal"
                else "FinBERT stays evaluation-only; heuristic proxy is not counted as literal backend parity."
            ),
        ),
        "verification.table_reconstruction": _item(
            table_classification,
            basis=["_tableformer_or_lgpma_reconstruct", "APOLLO_OCR_TABLEFORMER_URL", "APOLLO_OCR_LGPMA_URL"],
            notes=(
                "Table reconstruction is backed by the live Aegis Table Transformer service."
                if table_classification == "implemented_literal"
                else "TableFormer/LGPMA stay service-hook-only until a live backend returns structured tables."
            ),
        ),
    }


def _warm_preprocessing_services() -> None:
    image = Image.new("RGB", (96, 48), "white")
    draw = ImageDraw.Draw(image)
    draw.text((8, 14), "A1", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    payload = {"image_b64": base64.b64encode(buffer.getvalue()).decode("ascii"), "outscale": 2.0}
    for url in (
        str(os.getenv("APOLLO_OCR_DOCUNET_URL", DEFAULT_DOCUNET_URL)).strip(),
        str(os.getenv("APOLLO_OCR_REALESRGAN_URL", DEFAULT_REALESRGAN_URL)).strip(),
    ):
        if not url:
            continue
        body = dict(payload)
        if "docunet" in url:
            body.pop("outscale", None)
        try:
            requests.post(url, json=body, timeout=120)
        except Exception:
            continue


def parse_pytest_output(stdout: str, stderr: str = "") -> Dict[str, Any]:
    combined = "\n".join(part for part in [stdout, stderr] if part)
    passed = 0
    match = re.search(r"(\d+)\s+passed", combined)
    if match:
        passed = int(match.group(1))
    warnings_match = re.search(r"(\d+)\s+warning", combined)
    return {
        "passed": passed,
        "warnings": int(warnings_match.group(1)) if warnings_match else 0,
        "raw_output": combined.strip(),
    }


def run_pytest_command(command: List[str] | None = None) -> Dict[str, Any]:
    cmd = list(command or DEFAULT_TEST_COMMAND)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINEERING_ROOT)
    proc = subprocess.run(
        cmd,
        cwd=str(ENGINEERING_ROOT),
        capture_output=True,
        text=True,
        env=env,
        shell=False,
    )
    parsed = parse_pytest_output(proc.stdout, proc.stderr)
    return {
        "command": " ".join(cmd),
        "returncode": int(proc.returncode),
        **parsed,
    }


def build_truth_matrix(pytest_result: Dict[str, Any] | None = None, table_source_path: str = "") -> Dict[str, Any]:
    source_text, line_count = _read_ocr_tool()
    symbols = _symbol_presence(source_text)
    items = _build_items(symbols, table_source_path=table_source_path)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(ENGINEERING_ROOT),
        "ocr_tool": {
            "path": str(OCR_TOOL_PATH),
            "line_count": line_count,
            "symbols": symbols,
        },
        "items": items,
        "pytest": dict(pytest_result or {}),
    }


def find_unsupported_readme_claims(readme_text: str, matrix: Dict[str, Any], validation_artifact_relpath: str = "") -> List[str]:
    issues: List[str] = []
    lower = str(readme_text or "").lower()
    items = dict(matrix.get("items") or {})
    non_literal = [name for name, item in items.items() if str(item.get("classification") or "") != "implemented_literal"]
    if "phase 2 complete" in lower and non_literal:
        issues.append("phase2_complete_claim_without_literal_backing")
    if validation_artifact_relpath and validation_artifact_relpath.replace("\\", "/").lower() not in lower.replace("\\", "/"):
        issues.append("validation_artifact_path_missing")
    if "artifacts/ocr_phase2/phase2_truth_matrix.json" not in lower.replace("\\", "/"):
        issues.append("truth_matrix_path_missing")
    line_checks = {
        "docunet": "preprocessing.docunet",
        "realesrgan": "preprocessing.realesrgan",
        "finbert": "verification.finbert_verifier",
        "tableformer": "verification.table_reconstruction",
        "lgpma": "verification.table_reconstruction",
    }
    for raw_line in str(readme_text or "").splitlines():
        line = raw_line.strip().lower()
        if not line:
            continue
        for token, item_name in line_checks.items():
            if token in line and any(marker in line for marker in ("implemented_literal", "fully implemented", "complete", "completed")):
                if str((items.get(item_name) or {}).get("classification") or "") != "implemented_literal":
                    issues.append(f"unsupported_claim:{item_name}")
    return issues


def render_readme_section(matrix: Dict[str, Any], validation_artifact_relpath: str) -> str:
    pytest_result = dict(matrix.get("pytest") or {})
    items = dict(matrix.get("items") or {})
    ocr_tool = dict(matrix.get("ocr_tool") or {})
    sorted_items = sorted(items.items())
    remaining = [name for name, item in sorted_items if str(item.get("classification") or "") != "implemented_literal"]
    lines = [
        "## OCR Phase 2 Baseline And Truth Gate (2026-04-20)",
        "",
        "This section is generated from fresh validation artifacts, not from narrative claims.",
        "",
        f"- Truth matrix: `artifacts/ocr_phase2/phase2_truth_matrix.json`",
        f"- Validation artifact: `{validation_artifact_relpath.replace(os.sep, '/')}`",
        f"- OCR tool path: `{ocr_tool.get('path')}`",
        f"- OCR tool line count: `{ocr_tool.get('line_count')}`",
        f"- Pytest command: `{pytest_result.get('command', '')}`",
        f"- Pytest result: `{pytest_result.get('passed', 0)} passed`",
        "",
        "### Phase 2 Capability Matrix",
        "",
        "| Item | Classification | Notes |",
        "| --- | --- | --- |",
    ]
    for name, item in sorted_items:
        lines.append(f"| `{name}` | `{item.get('classification')}` | {item.get('notes', '')} |")
    lines.extend(
        [
            "",
            "### Remaining Deferred Or Non-Literal Items",
            "",
        ]
    )
    if remaining:
        lines.extend([f"- `{name}`" for name in remaining])
    else:
        lines.append("- none")
    return "\n".join(lines).strip() + "\n"


def _write_text_pdf(path: Path, lines: List[str]) -> None:
    if fitz is None:  # pragma: no cover
        raise RuntimeError("PyMuPDF required for validation PDFs")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72
    for line in lines:
        page.insert_text((54, y), line, fontsize=11, fontname="cour")
        y += 14
    doc.save(str(path))
    doc.close()


def _write_scanned_pdf(path: Path) -> None:
    if fitz is None:  # pragma: no cover
        raise RuntimeError("PyMuPDF required for validation PDFs")
    image = Image.new("L", (1700, 2200), 255)
    draw = ImageDraw.Draw(image)
    rows = [
        "SEC MARGIN EXECUTION REVIEW",
        "Assets 100",
        "Liabilities 60",
        "Equity 40",
        "Risk 12%",
        "Liquidity 88%",
        "Table: Margin | 25000 |",
    ]
    y = 160
    for row in rows:
        draw.text((120, y), row, fill=0)
        y += 170
    image = image.rotate(1.8, expand=False, fillcolor=255)
    png_path = path.with_suffix(".png")
    image.save(png_path)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, filename=str(png_path))
    doc.save(str(path))
    doc.close()


def _create_validation_docs(doc_dir: Path) -> Dict[str, Path]:
    doc_dir.mkdir(parents=True, exist_ok=True)
    digital = doc_dir / "phase2_digital_finance.pdf"
    scanned = doc_dir / "phase2_scanned_finance.pdf"
    table_dense = doc_dir / "phase2_table_dense_finance.pdf"
    table_probe = doc_dir / "phase2_table_probe.png"
    _write_text_pdf(
        digital,
        [
            "Phase 2 Digital Finance Validation",
            "Assets 100",
            "Liabilities 60",
            "Equity 40",
            "Allocation 50%",
            "Cash 50%",
            "Revenue 120",
            "Expenses 80",
        ],
    )
    _write_text_pdf(
        table_dense,
        [
            "Quarterly Trading Table",
            "| Account | Value | Share |",
            "| Cash | 40 | 40% |",
            "| Equity | 35 | 35% |",
            "| Bonds | 25 | 25% |",
            "Assets 100",
            "Liabilities 60",
            "Equity 40",
        ],
    )
    _write_scanned_pdf(scanned)
    probe_image = Image.new("RGB", (220, 120), "white")
    probe_draw = ImageDraw.Draw(probe_image)
    probe_draw.rectangle((10, 10, 210, 110), outline="black", width=2)
    probe_draw.line((110, 10, 110, 110), fill="black", width=2)
    probe_draw.line((10, 60, 210, 60), fill="black", width=2)
    probe_draw.text((25, 28), "Assets", fill="black")
    probe_draw.text((135, 28), "100", fill="black")
    probe_draw.text((25, 76), "Liabilities", fill="black")
    probe_draw.text((145, 76), "60", fill="black")
    probe_image.save(table_probe)
    return {"digital_pdf": digital, "scanned_pdf": scanned, "table_dense_finance": table_dense, "table_probe_image": table_probe}


def _run_live_validation_case(name: str, path: Path) -> Dict[str, Any]:
    goal_map = {
        "digital_pdf": "Extract finance text and preserve numeric fidelity.",
        "scanned_pdf": "Read the scanned finance page and preserve balance-sheet numbers.",
        "table_dense_finance": "Extract the finance table and preserve the table structure.",
    }
    engine_map = {
        "digital_pdf": "native_pdf_text",
        "scanned_pdf": "tesseract",
        "table_dense_finance": "native_pdf_text",
    }
    payload = {
        "path": str(path),
        "goal": goal_map.get(name, "Extract finance content."),
        "engine": engine_map.get(name, "native_pdf_text"),
        "route_mode": "quality_first",
        "max_chars": 2400,
        "max_pages": 1,
        "consensus": True,
        "use_gateway": False,
    }
    original_docunet = ocr_dual_tool.DEFAULT_OCR_DOCUNET_URL
    original_realesrgan = ocr_dual_tool.DEFAULT_OCR_REALESRGAN_URL
    ocr_dual_tool.DEFAULT_OCR_DOCUNET_URL = ""
    ocr_dual_tool.DEFAULT_OCR_REALESRGAN_URL = ""
    try:
        result = ocr_dual(payload)
    finally:
        ocr_dual_tool.DEFAULT_OCR_DOCUNET_URL = original_docunet
        ocr_dual_tool.DEFAULT_OCR_REALESRGAN_URL = original_realesrgan
    return {
        "name": name,
        "path": str(path),
        "ok": bool(result.get("ok")),
        "engine": str(result.get("engine") or ""),
        "fallback_reason": str(result.get("fallback_reason") or result.get("error") or ""),
        "phase2": dict(result.get("phase2") or {}),
        "quality": dict(result.get("quality") or {}),
    }


def _render_validation_markdown(matrix: Dict[str, Any], validation_artifact_relpath: str, live_cases: List[Dict[str, Any]], sample_dir: Path) -> str:
    pytest_result = dict(matrix.get("pytest") or {})
    ocr_tool = dict(matrix.get("ocr_tool") or {})
    items = dict(matrix.get("items") or {})
    remaining = [name for name, item in sorted(items.items()) if str(item.get("classification") or "") != "implemented_literal"]
    lines = [
        f"# Apollo OCR Phase 2 Validation ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')})",
        "",
        f"- Validation artifact path: `{validation_artifact_relpath.replace(os.sep, '/')}`",
        f"- Truth matrix path: `artifacts/ocr_phase2/phase2_truth_matrix.json`",
        f"- OCR tool path: `{ocr_tool.get('path')}`",
        f"- OCR tool line count: `{ocr_tool.get('line_count')}`",
        f"- Pytest command: `{pytest_result.get('command', '')}`",
        f"- Pytest pass count: `{pytest_result.get('passed', 0)}`",
        f"- Sample docs directory: `{sample_dir}`",
        "",
        "## Live Validation Cases",
        "",
    ]
    for case in live_cases:
        phase2 = dict(case.get("phase2") or {})
        quality = dict(case.get("quality") or {})
        lines.extend(
            [
                f"### {case.get('name')}",
                f"- path: `{case.get('path')}`",
                f"- ok: `{case.get('ok')}`",
                f"- engine: `{case.get('engine')}`",
                f"- fallback_reason: `{case.get('fallback_reason')}`",
                f"- phase2 passes: `{phase2.get('passes', 0)}`",
                f"- phase2 vote_mode: `{((phase2.get('ensemble') or {}).get('vote_mode') or '')}`",
                f"- verification score: `{((phase2.get('verification') or {}).get('verification_score') or '')}`",
                f"- quality score: `{quality.get('score', 0.0)}`",
                "",
            ]
        )
    lines.extend(["## Remaining Deferred Items", ""])
    lines.extend([f"- `{name}`" for name in remaining] or ["- none"])
    return "\n".join(lines).strip() + "\n"


def write_phase2_validation_artifacts(pytest_result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    pytest_summary = dict(pytest_result or run_pytest_command())
    sample_dir = ARTIFACT_ROOT / f"sample_docs_{_utc_ts()}"
    docs = _create_validation_docs(sample_dir)
    _warm_preprocessing_services()
    matrix = build_truth_matrix(pytest_summary, table_source_path=str(docs["table_probe_image"]))
    live_cases = [_run_live_validation_case(name, path) for name, path in docs.items() if name != "table_probe_image"]
    matrix["live_validation"] = live_cases
    truth_matrix_path = ARTIFACT_ROOT / "phase2_truth_matrix.json"
    truth_matrix_path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    validation_name = f"phase2_validation_{_utc_ts()}.md"
    validation_path = ARTIFACT_ROOT / validation_name
    validation_relpath = str(Path("artifacts") / "ocr_phase2" / validation_name)
    validation_path.write_text(
        _render_validation_markdown(matrix, validation_relpath, live_cases, sample_dir),
        encoding="utf-8",
    )
    return {
        "truth_matrix_path": truth_matrix_path,
        "validation_path": validation_path,
        "validation_relpath": validation_relpath,
        "matrix": matrix,
    }


def main() -> int:
    artifact_info = write_phase2_validation_artifacts()
    print(json.dumps(
        {
            "ok": True,
            "truth_matrix_path": str(artifact_info["truth_matrix_path"]),
            "validation_path": str(artifact_info["validation_path"]),
            "pytest_passed": int((artifact_info["matrix"].get("pytest") or {}).get("passed") or 0),
            "line_count": int(((artifact_info["matrix"].get("ocr_tool") or {}).get("line_count") or 0)),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
