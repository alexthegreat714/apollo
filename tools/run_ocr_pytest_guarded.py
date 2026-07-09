"""Guarded launcher for Apollo OCR pytest runs.

This wrapper is intended for calendar/agent-launched OCR tests. It prevents
duplicate OCR pytest trees, refuses shell-pipeline style arguments, writes full
output to a log file, and kills the pytest process tree on timeout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APOLLO_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = APOLLO_ROOT / "logs" / "process_locks"
LOG_DIR = APOLLO_ROOT / "logs" / "pytest"
REPORT_DIR = APOLLO_ROOT / "reports" / "ocr97_pytest"
LOCK_PATH = LOCK_DIR / "apollo_ocr_pytest.lock.json"
DEFAULT_STALE_MINUTES = int(os.getenv("APOLLO_OCR_PYTEST_STALE_MINUTES", "120"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("APOLLO_OCR_PYTEST_TIMEOUT_SECONDS", "1800"))
DEFAULT_TAIL_LINES = int(os.getenv("APOLLO_OCR_PYTEST_TAIL_LINES", "60"))
DEFAULT_OCR_ARGS = [
    "tests/test_ocr_bootstrap.py",
    "tests/test_ocr_dual_tool.py",
    "tests/test_ocr_phase2_services.py",
    "tests/test_ocr_benchmark_harness.py",
    "-q",
]
PIPELINE_TOKENS = ("|", "||", "&&", ";", "`", "$(", ">", "<")


class GuardrailError(RuntimeError):
    """Raised when a requested run violates the launcher guardrails."""


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_pytest_args(args: list[str]) -> None:
    joined = " ".join(args).lower()
    for token in args:
        if any(bad in token for bad in PIPELINE_TOKENS):
            raise GuardrailError(
                "Refusing Apollo OCR pytest run with shell pipeline/control token. "
                "Use this wrapper's log output instead of pytest ... | tail."
            )
        if token.lower() == "tail" or token.lower().startswith("tail "):
            raise GuardrailError(
                "Refusing Apollo OCR pytest run with tail in the command. "
                "Full output is written to Apollo/logs/pytest and tailed after completion."
            )
    if "pytest" in joined and any(bad in joined for bad in PIPELINE_TOKENS):
        raise GuardrailError("Refusing piped pytest command string.")
    if "tail" in joined and "test_ocr" in joined:
        raise GuardrailError("Refusing OCR pytest command that shells through tail.")
    if not any("test_ocr" in arg.lower() or arg.lower() == "-m" for arg in args):
        raise GuardrailError(
            "This guard is only for Apollo OCR pytest runs. Pass OCR test files, "
            "or use normal pytest for unrelated tests."
        )


def _load_lock() -> dict[str, Any] | None:
    if not LOCK_PATH.exists():
        return None
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pid": None, "started_at": None, "corrupt": True}


def _write_lock(payload: dict[str, Any]) -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _remove_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def _process_rows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'pytest' -or $_.CommandLine -match 'run_ocr_pytest_guarded' } | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate | "
        "ConvertTo-Json -Compress"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        cwd=str(APOLLO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=_creationflags(),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    return []


def _is_matching_apollo_ocr_pytest(row: dict[str, Any], current_pid: int = os.getpid()) -> bool:
    pid = int(row.get("ProcessId") or 0)
    if pid == current_pid:
        return False
    name = str(row.get("Name") or "").lower()
    cmd = str(row.get("CommandLine") or "").lower()
    if "run_ocr_pytest_guarded" in cmd:
        return False
    if not (
        name in {"python.exe", "python", "py.exe", "py", "pytest.exe", "pytest"}
        or re.search(r"\\(?:python|py|pytest)(?:\.exe)?(?:\"|\s|$)", cmd)
    ):
        return False
    if "pytest" not in cmd:
        return False
    if "apollo" not in cmd:
        return False
    if "test_ocr" not in cmd and "ocr97" not in cmd:
        return False
    return True


def _row_started_at(row: dict[str, Any]) -> datetime | None:
    value = row.get("CreationDate")
    if not value:
        return None
    text = str(value)
    if text.startswith("/Date("):
        try:
            millis = int(text.split("(", 1)[1].split(")", 1)[0].split("+", 1)[0])
        except (IndexError, ValueError):
            return None
        return datetime.fromtimestamp(millis / 1000, timezone.utc)
    return _parse_dt(text)


def _pid_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -First 1 | ConvertTo-Json -Compress"],
            cwd=str(APOLLO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=_creationflags(),
        )
        return bool(proc.stdout.strip())
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_tree(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            cwd=str(APOLLO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=_creationflags(),
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _tail_file(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-max(lines, 0) :])


def _parse_pytest_summary(text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "warnings": 0,
        "timeout": False,
        "raw_summary": "",
    }
    for line in reversed(text.splitlines()):
        clean = line.strip()
        if not clean:
            continue
        if "TIMEOUT after" in clean:
            summary["timeout"] = True
        if re.search(r"\b(?:passed|failed|errors?|skipped|warnings?)\b", clean) and "=" not in clean:
            summary["raw_summary"] = clean
            for key in ("passed", "failed", "errors", "skipped", "warnings"):
                match = re.search(rf"(\d+)\s+{key.rstrip('s')}s?\b", clean)
                if match:
                    summary[key] = int(match.group(1))
            break
    if "TIMEOUT after" in text:
        summary["timeout"] = True
    return summary


def _write_report(
    *,
    started_at: datetime,
    completed_at: datetime,
    cmd: list[str],
    exit_code: int,
    log_path: Path,
    tail_lines: int,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = started_at.strftime("%Y%m%d_%H%M%S")
    report_json = REPORT_DIR / f"ocr97_pytest_{run_id}.json"
    report_md = REPORT_DIR / f"ocr97_pytest_{run_id}.md"
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    except OSError:
        log_text = ""
    parsed = _parse_pytest_summary(log_text)
    tail = _tail_file(log_path, tail_lines) if log_path.exists() else ""
    payload = {
        "run_id": run_id,
        "status": status,
        "ok": exit_code == 0 and status == "completed",
        "exit_code": exit_code,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "command": cmd,
        "log_path": str(log_path),
        "report_md": str(report_md),
        "report_json": str(report_json),
        "summary": parsed,
        "error": error,
    }
    report_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# OCR97 Pytest Guarded Run",
        "",
        f"- Status: `{status}`",
        f"- Exit code: `{exit_code}`",
        f"- Started: `{payload['started_at']}`",
        f"- Completed: `{payload['completed_at']}`",
        f"- Duration seconds: `{payload['duration_seconds']}`",
        f"- Log: `{log_path}`",
        f"- JSON: `{report_json}`",
        f"- Command: `{' '.join(cmd)}`",
        "",
        "## Summary",
        "",
        f"- Passed: `{parsed.get('passed')}`",
        f"- Failed: `{parsed.get('failed')}`",
        f"- Errors: `{parsed.get('errors')}`",
        f"- Skipped: `{parsed.get('skipped')}`",
        f"- Warnings: `{parsed.get('warnings')}`",
        f"- Timeout: `{parsed.get('timeout')}`",
        f"- Raw summary: `{parsed.get('raw_summary') or ''}`",
    ]
    if error:
        lines.extend(["", "## Error", "", error])
    if tail:
        lines.extend(["", f"## Last {tail_lines} Log Lines", "", "```text", tail[-12000:], "```"])
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def _matching_rows() -> list[dict[str, Any]]:
    return [row for row in _process_rows() if _is_matching_apollo_ocr_pytest(row)]


def _enforce_single_run(stale_after: timedelta, cleanup_stale: bool) -> None:
    now = _utc_now()
    lock = _load_lock()
    if lock:
        pid = int(lock.get("pid") or 0)
        started_at = _parse_dt(lock.get("started_at"))
        is_stale = not started_at or now - started_at > stale_after
        if _pid_exists(pid) and not is_stale:
            raise GuardrailError(
                f"Refusing duplicate Apollo OCR pytest run. Existing lock pid={pid}, "
                f"started_at={lock.get('started_at')}, log={lock.get('log_path')}."
            )
        if _pid_exists(pid) and is_stale and cleanup_stale:
            _kill_tree(pid)
        if is_stale or not _pid_exists(pid):
            _remove_lock()

    active_rows = _matching_rows()
    stale_rows: list[dict[str, Any]] = []
    live_rows: list[dict[str, Any]] = []
    for row in active_rows:
        started_at = _row_started_at(row)
        if started_at and now - started_at > stale_after:
            stale_rows.append(row)
        else:
            live_rows.append(row)
    if stale_rows and cleanup_stale:
        for row in stale_rows:
            _kill_tree(int(row.get("ProcessId") or 0))
        live_rows = _matching_rows()
    if live_rows:
        first = live_rows[0]
        raise GuardrailError(
            "Refusing duplicate Apollo OCR pytest run. Existing matching process "
            f"pid={first.get('ProcessId')} command={first.get('CommandLine')!r}."
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Apollo OCR pytest with process guardrails.")
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--tail-lines", type=int, default=DEFAULT_TAIL_LINES)
    parser.add_argument("--no-cleanup-stale", action="store_true")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parsed = _build_parser().parse_args(argv)
    pytest_args = list(parsed.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    if not pytest_args:
        pytest_args = list(DEFAULT_OCR_ARGS)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    log_path = LOG_DIR / f"apollo_ocr_pytest_{started_at.strftime('%Y%m%d_%H%M%S')}.log"
    try:
        _validate_pytest_args(pytest_args)
        _enforce_single_run(
            stale_after=timedelta(minutes=max(parsed.stale_minutes, 1)),
            cleanup_stale=not parsed.no_cleanup_stale,
        )
    except GuardrailError as exc:
        message = f"[apollo-ocr-pytest-guard] {exc}"
        print(message, file=sys.stderr)
        log_path.write_text(message + "\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", *pytest_args]
        report = _write_report(
            started_at=started_at,
            completed_at=_utc_now(),
            cmd=cmd,
            exit_code=75,
            log_path=log_path,
            tail_lines=parsed.tail_lines,
            status="guardrail_failed",
            error=str(exc),
        )
        print(f"[apollo-ocr-pytest-guard] report: {report['report_md']}", file=sys.stderr)
        return 75

    cmd = [sys.executable, "-m", "pytest", *pytest_args]
    _write_lock(
        {
            "pid": os.getpid(),
            "started_at": started_at.isoformat(),
            "command": cmd,
            "log_path": str(log_path),
            "timeout_seconds": parsed.timeout_seconds,
        }
    )
    print(f"[apollo-ocr-pytest-guard] running: {' '.join(cmd)}")
    print(f"[apollo-ocr-pytest-guard] log: {log_path}")
    proc: subprocess.Popen[str] | None = None
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"$ {' '.join(cmd)}\n")
            log.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=str(APOLLO_ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=_creationflags(),
            )
            try:
                code = proc.wait(timeout=max(parsed.timeout_seconds, 1))
            except subprocess.TimeoutExpired:
                log.write(f"\nTIMEOUT after {parsed.timeout_seconds}s; killing pytest process tree pid={proc.pid}\n")
                log.flush()
                _kill_tree(proc.pid)
                code = 124
        tail = _tail_file(log_path, parsed.tail_lines)
        if tail:
            print(f"[apollo-ocr-pytest-guard] last {parsed.tail_lines} log lines:")
            print(tail)
        report = _write_report(
            started_at=started_at,
            completed_at=_utc_now(),
            cmd=cmd,
            exit_code=int(code),
            log_path=log_path,
            tail_lines=parsed.tail_lines,
            status="timeout" if int(code) == 124 else "completed",
        )
        print(f"[apollo-ocr-pytest-guard] report: {report['report_md']}")
        return int(code)
    finally:
        _remove_lock()


if __name__ == "__main__":
    raise SystemExit(main())
