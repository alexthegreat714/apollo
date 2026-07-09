from __future__ import annotations

import json
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

_GUARD_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_ocr_pytest_guarded.py"
_SPEC = importlib.util.spec_from_file_location("apollo_ocr_pytest_guarded", _GUARD_PATH)
assert _SPEC and _SPEC.loader
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)


def test_rejects_shell_pipeline_tail() -> None:
    with pytest.raises(guard.GuardrailError):
        guard._validate_pytest_args(["tests/test_ocr_dual_tool.py", "|", "tail", "-40"])


def test_rejects_non_ocr_pytest_args() -> None:
    with pytest.raises(guard.GuardrailError):
        guard._validate_pytest_args(["tests/test_nightly_pipeline.py", "-q"])


def test_default_args_are_ocr_args() -> None:
    guard._validate_pytest_args(guard.DEFAULT_OCR_ARGS)


def test_wrapper_command_is_not_counted_as_pytest_worker() -> None:
    row = {
        "ProcessId": 999,
        "CommandLine": "powershell python Apollo/tools/run_ocr_pytest_guarded.py -- tests/test_ocr_dual_tool.py -q",
    }

    assert not guard._is_matching_apollo_ocr_pytest(row, current_pid=1)


def test_active_lock_refuses_duplicate(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lock_path = tmp_path / "apollo_ocr_pytest.lock.json"
    monkeypatch.setattr(guard, "LOCK_PATH", lock_path)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 1234,
                "started_at": guard._utc_now().isoformat(),
                "log_path": "Apollo/logs/pytest/current.log",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "_pid_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(guard, "_matching_rows", lambda: [])

    with pytest.raises(guard.GuardrailError):
        guard._enforce_single_run(timedelta(minutes=120), cleanup_stale=True)


def test_stale_lock_is_removed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lock_path = tmp_path / "apollo_ocr_pytest.lock.json"
    monkeypatch.setattr(guard, "LOCK_PATH", lock_path)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 4321,
                "started_at": "2020-01-01T00:00:00+00:00",
                "log_path": "Apollo/logs/pytest/stale.log",
            }
        ),
        encoding="utf-8",
    )
    killed: list[int] = []
    monkeypatch.setattr(guard, "_pid_exists", lambda pid: pid == 4321)
    monkeypatch.setattr(guard, "_kill_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(guard, "_matching_rows", lambda: [])

    guard._enforce_single_run(timedelta(minutes=120), cleanup_stale=True)

    assert killed == [4321]
    assert not lock_path.exists()
