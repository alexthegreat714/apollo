from __future__ import annotations

from Apollo import simulation_execution as sim_module
from Apollo import simulation_server as server_mod


def test_simulation_server_execution_approve_captures_context(monkeypatch):
    monkeypatch.setattr(server_mod, "_require_local_token", lambda: None)

    captured: dict[str, object] = {}

    def fake_approve(
        execution_id: str,
        approved_by: str = "",
        approval_notes: str = "",
        approved_from: str = "",
        approved_client: str = "",
        approval_source: str = "",
    ):
        captured.update(
            {
                "execution_id": execution_id,
                "approved_by": approved_by,
                "approved_from": approved_from,
                "approved_client": approved_client,
                "approval_source": approval_source,
                "approval_notes": approval_notes,
            }
        )
        return {"ok": True, "execution_plan": {"execution_id": execution_id}}

    monkeypatch.setattr(sim_module, "approve_simulation_execution_plan", fake_approve)

    client = server_mod.app.test_client()
    response = client.post(
        "/api/simulation/execution/exec_simserver_001/approve",
        headers={
            "X-Apollo-Token": "operator-token-abcde",
            "User-Agent": "pytest-agent/3.0",
        },
        json={"approval_notes": "server-op-ok"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload.get("ok") is True
    assert captured["execution_id"] == "exec_simserver_001"
    assert captured["approved_by"] == "token:operator-tok"
    assert captured["approved_from"] == "127.0.0.1"
    assert captured["approved_client"] == "pytest-agent/3.0"
    assert captured["approval_source"] == "api"
    assert captured["approval_notes"] == "server-op-ok"


def test_simulation_server_execution_submit_captures_context_and_source(monkeypatch):
    monkeypatch.setattr(server_mod, "_require_local_token", lambda: None)

    captured: dict[str, object] = {}

    def fake_submit(
        execution_id: str,
        force_without_approval: bool = False,
        dry_run=None,
        executor=None,
        submitted_by: str = "",
        submitted_from: str = "",
        submitted_client: str = "",
        submission_source: str = "",
    ):
        captured.update(
            {
                "execution_id": execution_id,
                "force_without_approval": force_without_approval,
                "dry_run": dry_run,
                "executor": executor,
                "submitted_by": submitted_by,
                "submitted_from": submitted_from,
                "submitted_client": submitted_client,
                "submission_source": submission_source,
            }
        )
        return {"ok": True, "execution_plan": {"execution_id": execution_id}}

    monkeypatch.setattr(sim_module, "submit_simulation_execution_plan", fake_submit)

    client = server_mod.app.test_client()
    response = client.post(
        "/api/simulation/execution/exec_simserver_002/submit",
        headers={
            "X-Apollo-Token": "operator-token-xyz",
            "User-Agent": "pytest-agent/4.0",
        },
        json={
            "force_without_approval": "true",
            "dry_run": "yes",
            "executor": "alpaca",
            "submitted_by": "explicit-server-operator",
            "submission_source": "scheduler",
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload.get("ok") is True
    assert captured["execution_id"] == "exec_simserver_002"
    assert captured["force_without_approval"] is True
    assert captured["dry_run"] is True
    assert captured["executor"] == "alpaca"
    assert captured["submitted_by"] == "explicit-server-operator"
    assert captured["submitted_from"] == "127.0.0.1"
    assert captured["submitted_client"] == "pytest-agent/4.0"
    assert captured["submission_source"] == "scheduler"


def test_simulation_server_execution_routes_require_local_token(monkeypatch):
    monkeypatch.setattr(server_mod, "_require_local_token", lambda: ({"ok": False, "error": "unauthorized"}, 401))

    client = server_mod.app.test_client()
    response = client.post("/api/simulation/execution/exec_simserver_401/approve")

    assert response.status_code == 401
    assert response.get_json().get("ok") is False
    assert response.get_json().get("error") == "unauthorized"


def test_simulation_server_execution_reconcile_captures_context(monkeypatch):
    monkeypatch.setattr(server_mod, "_require_local_token", lambda: None)

    captured: dict[str, object] = {}

    def fake_reconcile(
        *,
        max_plans=None,
        requested_by: str = "",
        requested_from: str = "",
        requested_client: str = "",
        source: str = "api",
    ):
        captured.update(
            {
                "max_plans": max_plans,
                "requested_by": requested_by,
                "requested_from": requested_from,
                "requested_client": requested_client,
                "source": source,
            }
        )
        return {
            "ok": True,
            "summary": {
                "plans_considered": 0,
                "checked": 0,
                "reconciled": 0,
                "skipped": 0,
                "errors": 0,
            },
            "results": [],
        }

    monkeypatch.setattr(sim_module, "reconcile_simulation_execution_orders", fake_reconcile)

    client = server_mod.app.test_client()
    response = client.post(
        "/api/simulation/execution/reconcile",
        headers={
            "X-Apollo-Token": "operator-token-xyz",
            "User-Agent": "pytest-agent/reconcile-server",
        },
        json={"max_plans": 2, "source": "scheduler"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload.get("ok") is True
    assert captured["max_plans"] == 2
    assert captured["requested_by"] == "token:operator-tok"
    assert captured["requested_from"] == "127.0.0.1"
    assert captured["requested_client"] == "pytest-agent/reconcile-server"
    assert captured["source"] == "scheduler"
