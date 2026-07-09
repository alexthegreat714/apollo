from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATE_SCRIPT = ROOT / "watchdog" / "scheduler_state.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def _decision(**overrides):
    if not POWERSHELL:
        pytest.skip("PowerShell is required for scheduler policy tests")
    values = {
        "ManifestState": "running",
        "HeartbeatAgeMinutes": 1,
        "LockStale": "$false",
        "ProcessExists": "$true",
        "ProcessIdentityMatches": "$true",
        "TaskState": "Running",
    }
    values.update(overrides)
    command = (
        f". '{STATE_SCRIPT}'; "
        "Get-ApolloSchedulerDecision "
        f"-ManifestState '{values['ManifestState']}' "
        f"-HeartbeatAgeMinutes {values['HeartbeatAgeMinutes']} "
        f"-LockStale {values['LockStale']} "
        f"-ProcessExists {values['ProcessExists']} "
        f"-ProcessIdentityMatches {values['ProcessIdentityMatches']} "
        f"-TaskState '{values['TaskState']}' | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip())


def test_fresh_long_running_process_is_consistent():
    result = _decision()

    assert result == {"classification": "active", "hung": False, "may_terminate": False, "consistent": True}


def test_stale_heartbeat_requires_stale_lock_before_termination():
    lock_fresh = _decision(HeartbeatAgeMinutes=30, LockStale="$false")
    lock_stale = _decision(HeartbeatAgeMinutes=30, LockStale="$true")

    assert lock_fresh["classification"] == "active"
    assert lock_fresh["may_terminate"] is False
    assert lock_stale["classification"] == "hung_exact_process"
    assert lock_stale["may_terminate"] is True


def test_dead_and_reused_pids_never_authorize_termination():
    dead = _decision(ProcessExists="$false", ProcessIdentityMatches="$false")
    reused = _decision(ProcessExists="$true", ProcessIdentityMatches="$false")

    assert dead["classification"] == "abandoned"
    assert dead["may_terminate"] is False
    assert reused["classification"] == "abandoned_identity_mismatch"
    assert reused["may_terminate"] is False


def test_terminal_success_and_failure_are_consistent_with_ready_task():
    success = _decision(ManifestState="succeeded", ProcessExists="$false", ProcessIdentityMatches="$false", TaskState="Ready")
    failure = _decision(ManifestState="failed", ProcessExists="$false", ProcessIdentityMatches="$false", TaskState="Ready")

    assert success["consistent"] is True
    assert failure["consistent"] is True


def test_atomic_writer_and_command_identity(tmp_path):
    if not POWERSHELL:
        pytest.skip("PowerShell is required for scheduler policy tests")
    target = tmp_path / "manifest.json"
    command = (
        f". '{STATE_SCRIPT}'; "
        "$identity = Test-ApolloCommandIdentity "
        "-CommandLine 'python -m Apollo.nightly_pipeline run --payload-file payload_1.json' "
        "-ExpectedTokens @('-m Apollo.nightly_pipeline','payload_1.json'); "
        f"Write-ApolloJsonAtomic -Path '{target}' -Payload @{{ ok = $identity; state = 'succeeded'; pipeline_exit_code = 0 }}"
    )
    subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command], check=True)

    assert json.loads(target.read_text(encoding="utf-8-sig"))["ok"] is True
    assert not list(tmp_path.glob("*.tmp.*"))


def test_runner_enforces_exact_pid_recovery_and_retry_configuration():
    runner = (ROOT / "watchdog" / "run_apollo_nightly_daily.ps1").read_text(encoding="utf-8")
    setup = (ROOT / "watchdog" / "setup_nightly_task.ps1").read_text(encoding="utf-8")

    assert "taskkill.exe /PID ([int]$previous.child_pid) /T /F" in runner
    assert '"pipeline_exit_code"' in runner
    assert '"succeeded"' in runner and '"failed"' in runner
    assert "-RestartCount 2" in setup
    assert "-RestartMinutes 15" in setup
