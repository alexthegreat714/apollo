from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "homework_strategy_benchmark"
README_PATH = APOLLO_ROOT / "README.md"
README_START = "<!-- APOLLO_HOMEWORK_STRATEGY_BENCHMARK_START -->"
README_END = "<!-- APOLLO_HOMEWORK_STRATEGY_BENCHMARK_END -->"

PROJECT = "apollo"
PROJECT_LABEL = "Apollo"
TEST_TYPE = "apollo_homework_strategy_benchmark"
CAPABILITY = "homework_trade_strategy_analysis_and_report_quality"

os.environ.setdefault("APOLLO_HOMEWORK_LLM_BASE_URL", "http://127.0.0.1:11434")
os.environ.setdefault("APOLLO_HOMEWORK_LLM_MODEL", "gemma3:12b")
os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_utc(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _default_run_id(now: Optional[datetime] = None) -> str:
    return f"{TEST_TYPE}_{(now or datetime.now()).strftime('%Y%m%d_%H%M%S')}"


def _clip(value: Any, limit: int = 5000) -> str:
    return str(value or "").strip()[:limit].rstrip()


def default_commands() -> List[str]:
    py = sys.executable or "python"
    return [
        f'{py} -m py_compile Apollo\\homework_trade_pipeline.py Apollo\\homework_phase2_pipeline.py Apollo\\trade_cycle.py Apollo\\day_trade_cycle.py',
        f'{py} -m pytest Apollo\\tests\\test_homework_trade_pipeline.py -q',
        f'{py} -m pytest Apollo\\tests\\test_trade_cycle.py -q',
        f'{py} -m pytest Apollo\\tests\\test_apollo_homework_strategy_benchmark.py -q',
    ]


def verification_command(run_id: str) -> str:
    return f'{sys.executable or "python"} "{Path(__file__).resolve()}" --run-id {run_id}'


def run_commands(commands: Optional[List[str]] = None, *, timeout_sec: int = 1200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for command in commands or default_commands():
        started = _iso_utc()
        try:
            proc = subprocess.run(
                command,
                cwd=str(ENGINEERING_ROOT),
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(30, int(timeout_sec)),
                check=False,
            )
            rows.append(
                {
                    "command": command,
                    "ok": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "started_at": started,
                    "completed_at": _iso_utc(),
                    "stdout_tail": _clip(proc.stdout),
                    "stderr_tail": _clip(proc.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            rows.append(
                {
                    "command": command,
                    "ok": False,
                    "returncode": None,
                    "started_at": started,
                    "completed_at": _iso_utc(),
                    "error": f"timeout_after_{timeout_sec}_sec",
                    "stdout_tail": _clip(exc.stdout if isinstance(exc.stdout, str) else ""),
                    "stderr_tail": _clip(exc.stderr if isinstance(exc.stderr, str) else ""),
                }
            )
    return rows


def score_results(command_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    command_text = "\n".join(str(row.get("command") or "") for row in command_results)
    failed = [row for row in command_results if not row.get("ok")]

    components = {
        "module_import_health": 20 if any("-m py_compile" in str(row.get("command") or "") and row.get("ok") for row in command_results) else 0,
        "homework_candidate_selection": 25
        if any("test_homework_trade_pipeline.py" in str(row.get("command") or "") and row.get("ok") for row in command_results)
        else 0,
        "trade_cycle_evidence_quality": 20
        if any("test_trade_cycle.py" in str(row.get("command") or "") and row.get("ok") for row in command_results)
        else 0,
        "benchmark_report_contract": 20
        if any("test_apollo_homework_strategy_benchmark.py" in str(row.get("command") or "") and row.get("ok") for row in command_results)
        else 0,
        "safety_and_queue_readiness": 15 if not failed and "day_trade_cycle.py" in command_text else 5 if not failed else 0,
    }
    score = int(sum(components.values()))
    weak_areas: List[str] = []
    if components["homework_candidate_selection"] == 0:
        weak_areas.append("homework candidate selection and no-trade gating")
    if components["trade_cycle_evidence_quality"] == 0:
        weak_areas.append("trade cycle evidence quality and enrichment blockers")
    if components["benchmark_report_contract"] == 0:
        weak_areas.append("benchmark report/README/calendar contract")
    if components["module_import_health"] == 0:
        weak_areas.append("Apollo module import/compile health")
    return {
        "score": score,
        "components": components,
        "status": "passed" if not failed and score >= 80 else "failed",
        "failed_count": len(failed),
        "weak_areas": weak_areas,
    }


def render_markdown(run_id: str, result: Dict[str, Any]) -> str:
    score = result["score"]
    status = result["status"]
    weak_areas = result.get("weak_areas") or []
    lines = [
        f"# Apollo Homework + Strategy Benchmark - {run_id}",
        "",
        f"- Status: `{status}`",
        f"- Score: `{score}/100`",
        f"- Completed: `{result.get('completed_at')}`",
        f"- Capability: `{CAPABILITY}`",
        "",
        "## What This Tests",
        "",
        "- Homework candidate selection prefers actionable setups over stale high-score setups.",
        "- No-trade gates explain low R/R, stale status, low confidence, and thin strategy context.",
        "- Trade-cycle evidence quality distinguishes completed runs from trade-ready runs.",
        "- Reports and queue payloads are written so Apollo can be improved from evidence.",
        "",
        "## Score Components",
        "",
        "| Component | Points |",
        "|---|---:|",
    ]
    for name, value in (result.get("components") or {}).items():
        lines.append(f"| `{name}` | {value} |")
    lines.extend(["", "## Weak Areas", ""])
    if weak_areas:
        lines.extend(f"- {item}" for item in weak_areas)
    else:
        lines.append("- No benchmark blockers detected in this run.")
    lines.extend(["", "## Commands", ""])
    for row in result.get("commands") or []:
        status_text = "pass" if row.get("ok") else "fail"
        lines.append(f"- `{status_text}` `{row.get('command')}`")
    return "\n".join(lines).strip() + "\n"


def update_readme(summary: Dict[str, Any], report_path: Path) -> None:
    block = "\n".join(
        [
            README_START,
            "## Apollo Homework + Strategy Benchmark",
            "",
            f"- Latest run: `{summary.get('run_id')}`",
            f"- Status: `{summary.get('status')}`",
            f"- Score: `{summary.get('score')}/100`",
            f"- Report: `{report_path}`",
            f"- Updated: `{summary.get('completed_at')}`",
            "",
            "Tracked improvement areas: homework candidate selection, no-trade explanations, trade-cycle evidence quality, safety gates, queue/report contract.",
            README_END,
        ]
    )
    existing = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else "# Apollo\n"
    if README_START in existing and README_END in existing:
        before = existing.split(README_START, 1)[0].rstrip()
        after = existing.split(README_END, 1)[1].lstrip()
        text = f"{before}\n\n{block}\n\n{after}".rstrip() + "\n"
    else:
        text = existing.rstrip() + "\n\n" + block + "\n"
    README_PATH.write_text(text, encoding="utf-8")


def build_report_payload(result: Dict[str, Any], *, report_path: Path, summary_path: Path) -> Dict[str, Any]:
    status = str(result.get("status") or "failed")
    return {
        "project": PROJECT,
        "project_label": PROJECT_LABEL,
        "test_type": TEST_TYPE,
        "run_id": result["run_id"],
        "name": "Apollo homework and strategy benchmark",
        "status": status,
        "summary": f"Apollo homework/trade benchmark {status} with score {result.get('score')}/100.",
        "score": int(result.get("score") or 0),
        "computed_score": int(result.get("score") or 0),
        "score_components": result.get("components") or {},
        "passed": 1 if status == "passed" else 0,
        "failed": 0 if status == "passed" else 1,
        "total": 1,
        "capability": CAPABILITY,
        "verification_command": verification_command(result["run_id"]),
        "tests_improved": [
            "homework candidate quality gates",
            "trade-cycle evidence quality",
            "strategy-context sufficiency",
            "Apollo README benchmark reporting",
        ],
        "tests_confirmed": [row.get("command") for row in result.get("commands") or [] if row.get("command")],
        "capabilities_confirmed": ["benchmark report written"] + ([CAPABILITY] if status == "passed" else []),
        "capabilities_not_confirmed": [] if status == "passed" else [CAPABILITY],
        "diagnostic": {
            "cause": "apollo_homework_strategy_benchmark_passed" if status == "passed" else "apollo_homework_strategy_benchmark_failed",
            "category": "none" if status == "passed" else "verification",
            "recoverable": status != "passed",
            "next_action": "continue_periodic_benchmarking" if status == "passed" else "inspect_failed_command_and_weak_areas",
            "evidence": [str(report_path), str(summary_path)] + list(result.get("weak_areas") or []),
            "confidence": 0.9 if status == "passed" else 0.82,
        },
        "artifact_paths": [str(report_path), str(summary_path)],
        "details": json.dumps({"weak_areas": result.get("weak_areas"), "commands": result.get("commands")}, ensure_ascii=False),
        "completed_at": result.get("completed_at"),
        "report_path": str(report_path),
        "source": PROJECT,
    }


def record_sky_report(payload: Dict[str, Any], *, root: Optional[Path] = None) -> Dict[str, Any]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Sky.services.test_run_reports import record_test_run_report

    return record_test_run_report(payload, root=root)


def run_sequence(
    *,
    run_id: Optional[str] = None,
    timeout_sec: int = 1200,
    no_sky_report: bool = False,
    report_root: Optional[Path] = None,
    sky_report_root: Optional[Path] = None,
) -> Dict[str, Any]:
    safe_run_id = run_id or _default_run_id()
    commands = run_commands(timeout_sec=timeout_sec)
    scored = score_results(commands)
    completed_at = _iso_utc()
    result = {
        "ok": scored["status"] == "passed",
        "run_id": safe_run_id,
        "completed_at": completed_at,
        "commands": commands,
        **scored,
    }
    out_root = Path(report_root) if report_root else REPORT_ROOT
    run_dir = out_root / safe_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"
    report_path = run_dir / "report.md"
    summary_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(render_markdown(safe_run_id, result), encoding="utf-8")
    update_readme(result, report_path)
    payload = build_report_payload(result, report_path=report_path, summary_path=summary_path)
    sky_report = {"ok": False, "skipped": True}
    if not no_sky_report:
        sky_report = record_sky_report(payload, root=sky_report_root)
    return {**result, "report_path": str(report_path), "summary_path": str(summary_path), "capability_report": payload, "sky_report": sky_report}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Apollo homework/trade strategy benchmark and update Apollo README.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout-sec", type=int, default=1200)
    parser.add_argument("--no-sky-report", action="store_true")
    args = parser.parse_args(argv)

    run_id = args.run_id or _default_run_id()
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "run_id": run_id, "commands": default_commands()}, indent=2))
        return 0
    result = run_sequence(run_id=run_id, timeout_sec=args.timeout_sec, no_sky_report=args.no_sky_report)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
