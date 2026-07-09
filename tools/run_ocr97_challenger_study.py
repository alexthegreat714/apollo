from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent
if str(ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINEERING_ROOT))

from Apollo.tools import ocr_benchmark_harness as harness
from Apollo.tools.build_ocr97_challenger_fixtures import ensure_fixtures

MANIFEST_PATH = APOLLO_ROOT / "config" / "ocr97_challenger_study_manifest.json"
PAPER_INDEX_PATH = APOLLO_ROOT / "config" / "ocr97_challenger_papers_index.json"
REPORT_ROOT = APOLLO_ROOT / "reports" / "ocr97_challenger_study"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _build_index(summary: Dict[str, Any], *, run_id: str, report_dir: Path) -> Dict[str, Any]:
    findings = list(summary.get("route_findings") or [])
    return {
      "run_id": run_id,
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "report_dir": str(report_dir),
      "report_md": str(report_dir / "report.md"),
      "report_json": str(report_dir / "summary.json"),
      "baseline_route": summary.get("baseline_route"),
      "route_findings": findings,
      "recommended_modes": {
          str(item.get("route_id") or ""): str(item.get("recommended_use_mode") or "")
          for item in findings
      }
    }


def run_challenger_study(*, run_id: str = "", timeout_sec: int = 180) -> Dict[str, Any]:
    ensure_fixtures()
    manifest = harness._load_manifest(MANIFEST_PATH)
    paper_index = _load_json(PAPER_INDEX_PATH)
    resolved_run_id = run_id or f"ocr97_challenger_{_utc_stamp()}"
    report_dir = REPORT_ROOT / resolved_run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = harness.run_study(manifest, paper_index=paper_index, timeout_sec=timeout_sec)
    report_md = harness.render_study_markdown(summary)
    (report_dir / "report.md").write_text(report_md, encoding="utf-8")
    _write_json(report_dir / "summary.json", summary)
    index_payload = _build_index(summary, run_id=resolved_run_id, report_dir=report_dir)
    _write_json(report_dir / "index.json", index_payload)
    _write_json(REPORT_ROOT / "latest_summary.json", summary)
    (REPORT_ROOT / "latest_report.md").write_text(report_md, encoding="utf-8")
    _write_json(REPORT_ROOT / "latest_index.json", index_payload)
    return {
        "ok": True,
        "run_id": resolved_run_id,
        "report_dir": str(report_dir),
        "report_path": str(report_dir / "report.md"),
        "summary_path": str(report_dir / "summary.json"),
        "index_path": str(report_dir / "index.json"),
        "latest_report_path": str(REPORT_ROOT / "latest_report.md"),
        "latest_index_path": str(REPORT_ROOT / "latest_index.json"),
        "route_findings": summary.get("route_findings") or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OCR97 challenger study and write latest report pointers.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout-sec", type=int, default=180)
    args = parser.parse_args(argv)
    payload = run_challenger_study(run_id=str(args.run_id or ""), timeout_sec=max(30, int(args.timeout_sec)))
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
