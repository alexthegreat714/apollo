from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest

from Apollo import simulation_execution


def _set_root(monkeypatch, tmp_path: Path):
    execution_root = tmp_path / "execution"
    plans_dir = execution_root / "plans"
    queue_path = execution_root / "plan_queue.json"
    submit_window = execution_root / "submission_rate_windows.json"
    plans_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_ROOT", execution_root)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_PLANS", plans_dir)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_QUEUE", queue_path)
    monkeypatch.setattr(simulation_execution, "SIMULATION_EXECUTION_SUBMIT_WINDOW", submit_window)
    monkeypatch.setenv("APOLLO_PRETRADE_GATE_ON_SUBMIT", "0")
    monkeypatch.setenv("APOLLO_PAPER_PORTFOLIO_SIZE", "100")
    monkeypatch.setenv("APOLLO_SIM_SIMULATED_BUYING_POWER", "50000")


def _fake_plan_payload(sim_id: str, ticker: str, entry_zone: str, stop_zone: str, target_zone: str, run_id: str = "run_submit_2"):
    return {
        "simulation": {
            "sim_id": sim_id,
            "ticker": ticker,
            "entry_zone": entry_zone,
            "stop_zone": stop_zone,
            "target_zone": target_zone,
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": run_id,
        }
    }


def test_create_plan_from_inline_simulation(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = {
        "notes": "paper flow smoke",
        "simulation": {
            "sim_id": "sim_test_001",
            "ticker": "AVGO",
            "entry_zone": "420.00-430.00",
            "stop_zone": "380.00",
            "target_zone": "470.00",
            "status": "ready",
            "setup_type": "trend_continuation",
            "score": 88.1,
            "confidence_label": "medium",
            "run_id": "run_inline",
        },
    }

    result = simulation_execution.create_simulation_execution_plan(payload)
    assert result.get("ok") is True
    plan = result["execution_plan"]

    assert plan["status"] == "draft"
    assert plan["execution_mode"] == "simulation"
    assert plan["simulation"]["sim_id"] == "sim_test_001"
    assert plan["risk"]["proposed_qty"] == 22
    assert plan["broker"]["mode"] == "simulation_only"
    assert plan["readiness"]["ready_for_execution"] is True
    assert plan["execution"]["entry"] == 425.0
    assert plan["execution"]["stop"] == 380.0
    assert "execution_realism_score" in plan["execution_realism"]

    execution_id = plan["execution_id"]
    stored = simulation_execution.get_simulation_execution_plan(execution_id)
    assert stored is not None
    assert stored["simulation"]["ticker"] == "AVGO"

    listed = simulation_execution.list_simulation_execution_plans()
    assert listed["ok"] is True
    assert listed["count"] >= 1
    ids = {row["execution_id"] for row in listed["plans"]}
    assert execution_id in ids


def test_create_short_plan_preserves_direction_and_risk_geometry(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = {
        "simulation": {
            "sim_id": "sim_short_001",
            "ticker": "BAD",
            "entry_zone": "99-101",
            "stop_zone": "105",
            "target_zone": "90",
            "direction_bias": "short_bias",
            "status": "ready",
            "setup_type": "event_dislocation",
            "run_id": "run_short",
        },
    }

    result = simulation_execution.create_simulation_execution_plan(payload)
    assert result["ok"] is True
    plan = result["execution_plan"]

    assert plan["simulation"]["direction_bias"] == "short_bias"
    assert plan["execution"]["direction_bias"] == "short_bias"
    assert plan["risk"]["direction_bias"] == "short_bias"
    assert plan["risk"]["risk_per_share"] == 5.0
    assert plan["risk"]["proposed_qty"] == 50
    assert plan["risk"]["paper_portfolio_size"] == 100.0
    assert plan["readiness"]["ready_for_execution"] is True

    order_plan = simulation_execution._build_broker_order_plan(
        execution_id=plan["execution_id"],
        ticker=plan["ticker"],
        execution=plan["execution"],
        risk=plan["risk"],
        provider="paper_sim",
        simulation=plan["simulation"],
    )
    assert order_plan["side"] == "sell"
    assert order_plan["direction_bias"] == "short_bias"
    assert [order["side"] for order in order_plan["orders"]] == ["sell", "buy", "buy"]
    assert [order["label"] for order in order_plan["orders"]] == ["entry_limit", "protective_stop", "profit_taker"]


def test_execution_realism_warns_on_low_liquidity_large_order():
    liquid = simulation_execution._execution_realism_profile(entry_price=100.0, qty=100, avg_volume=5_000_000)
    thin = simulation_execution._execution_realism_profile(entry_price=100.0, qty=2_000, avg_volume=500_000)

    assert thin["execution_realism_score"] < liquid["execution_realism_score"]
    assert "partial_fill_risk_high" in thin["warnings"] or "partial_fill_risk_medium" in thin["warnings"]
    assert thin["estimated_slippage_bps"] > liquid["estimated_slippage_bps"]


def test_create_plan_from_latest_simulation_falls_back_and_records_queue(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    calls = []

    def fake_get_latest():
        calls.append("latest")
        return {
            "sim_id": "sim_latest_001",
            "ticker": "MSFT",
            "entry_zone": "500-520",
            "stop_zone": "450",
            "target_zone": "570",
            "status": "ready",
            "setup_type": "base_breakout",
            "run_id": "run_latest",
        }

    monkeypatch.setattr(simulation_execution.swing_simulation, "load_simulation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(simulation_execution.swing_simulation, "get_latest_simulation", fake_get_latest)

    result = simulation_execution.create_simulation_execution_plan({})
    assert result["ok"] is True
    plan = result["execution_plan"]
    assert plan["simulation"]["ticker"] == "MSFT"
    assert plan["simulation"]["sim_id"] == "sim_latest_001"
    assert calls == ["latest"]

    queue = simulation_execution._read_json(simulation_execution.SIMULATION_EXECUTION_QUEUE, [])
    assert isinstance(queue, list)
    assert plan["execution_id"] in queue
    # second call should keep most recent id at front after dedupe
    again = simulation_execution.create_simulation_execution_plan({})
    assert again["ok"] is True
    queue = simulation_execution._read_json(simulation_execution.SIMULATION_EXECUTION_QUEUE, [])
    assert queue[0] == again["execution_plan"]["execution_id"]


def test_create_plan_rejects_missing_simulation(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)

    monkeypatch.setattr(simulation_execution.swing_simulation, "load_simulation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(simulation_execution.swing_simulation, "get_latest_simulation", lambda: None)

    result = simulation_execution.create_simulation_execution_plan({})
    assert result["ok"] is False
    assert result["error"] == "no_simulation_found"


def test_approve_plan_records_approval_metadata(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = {
        "simulation": {
            "sim_id": "sim_approve_001",
            "ticker": "MSFT",
            "entry_zone": "420-440",
            "stop_zone": "390",
            "target_zone": "470",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_approve",
        },
        "notes": "approve me",
    }
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]

    result = simulation_execution.approve_simulation_execution_plan(
        execution_id=execution_id,
        approved_by="ops",
        approval_notes="go",
        approved_from="192.0.2.1",
        approved_client="unit-test",
        approval_source="api",
    )
    assert result["ok"] is True
    plan = result["execution_plan"]
    assert plan["status"] == "approved"
    assert plan["approval"]["approved"] is True
    assert plan["approval"]["approved_by"] == "ops"
    assert plan["approval"]["approval_notes"] == "go"
    assert plan["approval"]["approved_from"] == "192.0.2.1"
    assert plan["approval"]["approved_client"] == "unit-test"
    assert plan["approval"]["approval_source"] == "api"
    assert plan["approval"]["approved_at"]


def test_submit_plan_blocks_force_without_approval_by_default(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)

    payload = {
        "simulation": {
            "sim_id": "sim_submit_force_blocked",
            "ticker": "AMD",
            "entry_zone": "190-210",
            "stop_zone": "175",
            "target_zone": "240",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_submit_force",
        }
    }
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]

    result = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        force_without_approval=True,
        submitted_by="ops",
        submitted_from="198.51.100.1",
        submitted_client="pytest",
    )
    assert result["ok"] is False
    assert result["error"] == "force_without_approval_disabled"


def test_submit_plan_allows_force_without_approval_when_enabled(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_ALLOW_FORCE_WITHOUT_APPROVAL", "1")
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = {
        "simulation": {
            "sim_id": "sim_submit_force_enabled",
            "ticker": "MSFT",
            "entry_zone": "420-440",
            "stop_zone": "390",
            "target_zone": "470",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_submit_force_enabled",
        }
    }
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]

    result = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        force_without_approval=True,
        submitted_by="ops",
        submitted_from="198.51.100.2",
        submitted_client="pytest",
    )
    assert result["ok"] is True


def test_submit_plan_requires_approval_by_default(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)

    payload = {
        "simulation": {
            "sim_id": "sim_submit_001",
            "ticker": "NVDA",
            "entry_zone": "700-740",
            "stop_zone": "650",
            "target_zone": "800",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_submit",
        }
    }
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "approval_required"

    plan = simulation_execution.get_simulation_execution_plan(execution_id)
    assert plan["status"] == "draft"
    assert plan["broker"]["order_submitted"] is False


def test_submit_plan_generates_paper_order_after_approval(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = {
        "simulation": {
            "sim_id": "sim_submit_002",
            "ticker": "AMD",
            "entry_zone": "190-210",
            "stop_zone": "175",
            "target_zone": "240",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_submit_2",
        }
    }
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]

    simulation_execution.approve_simulation_execution_plan(
        execution_id=execution_id,
        approved_by="ops",
    )
    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is True

    plan = result["execution_plan"]
    assert plan["status"] == "submitted"
    assert plan["broker"]["order_submitted"] is True
    assert plan["broker"]["fill_status"] == "submitted"
    assert len(plan["broker"]["order_ids"]) == 3
    assert plan["broker"]["order_ids"][0] == f"paper_sim_{execution_id}_1"
    assert plan["broker"]["order_ids"][1] == f"paper_sim_{execution_id}_2"
    assert plan["broker"]["order_ids"][2] == f"paper_sim_{execution_id}_3"
    assert plan["broker"]["provider"] == "paper_sim"
    assert plan["broker"]["mode"] == "paper_simulation"
    order_plan = plan["broker"]["order_plan"]
    assert order_plan["direction_bias"] == "long_bias"
    assert len(order_plan["orders"]) == 3
    order_types = [str(item.get("order_type")) for item in order_plan["orders"]]
    assert order_types == ["limit", "stop_market", "limit"]
    planned_qty = int(plan["risk"]["proposed_qty"])
    assert all(int(item.get("qty") or 0) == planned_qty for item in order_plan["orders"])
    assert order_plan["orders"][0]["label"] == "entry_limit"
    assert order_plan["orders"][1]["label"] == "protective_stop"
    assert order_plan["orders"][2]["label"] == "profit_taker"

    refreshed = simulation_execution.get_simulation_execution_plan(execution_id)
    assert refreshed["submission"]["executor"] == "paper_sim"
    assert refreshed["submission"]["dry_run"] is False
    assert refreshed["submission"]["source"] == "api"


def test_submit_plan_enforces_pretrade_gate_by_default(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_PRETRADE_GATE_ON_SUBMIT", "1")
    monkeypatch.delenv("APOLLO_PRETRADE_GATE_ENFORCE", raising=False)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    def fake_gate(plan, force=True):
        return {
            "ok": True,
            "run_id": "gate_block",
            "decision": "blocked_by_news",
            "blocker": "negative headline",
            "next_action": "pause",
            "checked_at": "2026-06-17T12:00:00Z",
            "checked_at_ct": "07:00 CST",
        }

    from Apollo import pretrade_gate

    monkeypatch.setattr(pretrade_gate, "run_pre_submit_gate", fake_gate)

    created = simulation_execution.create_simulation_execution_plan(
        _fake_plan_payload(
            sim_id="sim_gate_default",
            ticker="AMD",
            entry_zone="190-210",
            stop_zone="175",
            target_zone="240",
            run_id="run_gate_default",
        )
    )
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)

    assert result["ok"] is False
    assert result["error"] == "pretrade_gate_blocked"
    assert result["decision"] == "blocked_by_news"
    assert result["reason"] == "negative headline"


def test_submit_plan_calls_paper_sim_adapter(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    calls = {
        "init": 0,
        "submit": 0,
        "status": 0,
    }

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            calls["init"] += 1

        def submit_order(self, order):
            calls["submit"] += 1
            order_id = str(order.get("order_id") or "")
            return {"ok": True, "order_id": order_id, "broker_order_id": f"fake_{order_id}", "status": "submitted"}

        def get_order_status(self, order_id):
            calls["status"] += 1
            return {"ok": True, "order_id": order_id, "status": "submitted"}

        def get_positions(self):
            return {"ok": True, "positions": []}

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_003",
        ticker="CRM",
        entry_zone="300-320",
        stop_zone="285",
        target_zone="390",
        run_id="run_submit_3",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is True
    assert calls["init"] == 1
    assert calls["submit"] == 3

    plan = result["execution_plan"]
    submissions = plan["broker"].get("order_submissions") or []
    assert len(submissions) == 3
    assert all(item.get("ok") for item in submissions)


def test_submit_plan_dry_run_skips_broker_submit(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    calls = {"init": 0}

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            calls["init"] += 1

        def submit_order(self, order):
            raise AssertionError("should not call submit_order during dry run")

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_004",
        ticker="MSFT",
        entry_zone="420-440",
        stop_zone="390",
        target_zone="480",
        run_id="run_submit_4",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id, dry_run=True)
    assert result["ok"] is True
    plan = result["execution_plan"]
    assert plan["broker"]["order_submitted"] is False
    assert plan["broker"]["fill_status"] == "dry_run_not_submitted"
    assert calls["init"] == 0


def test_submit_plan_records_submission_source(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = _fake_plan_payload(
        sim_id="sim_submit_source",
        ticker="TSM",
        entry_zone="150-165",
        stop_zone="140",
        target_zone="190",
        run_id="run_submit_source",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        dry_run=True,
        submission_source="scheduler",
    )
    assert result["ok"] is True
    refreshed = result["execution_plan"]
    assert refreshed["submission"]["source"] == "scheduler"


def test_submit_plan_records_submission_metadata_on_tracking(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            pass

        def submit_order(self, order):
            order_id = str(order.get("order_id") or "")
            return {
                "ok": True,
                "order_id": order_id,
                "broker_order_id": f"tracker_{order_id}",
                "status": "accepted",
            }

        def get_order_status(self, order_id):
            return {"ok": True, "order_id": str(order_id), "status": "accepted", "broker_order_id": str(order_id)}

        def get_positions(self):
            return {"ok": True, "positions": []}

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_tracking",
        ticker="MSFT",
        entry_zone="420-440",
        stop_zone="390",
        target_zone="470",
        run_id="run_submit_tracking",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        submission_source="scheduler",
        submitted_by="TraderJoe",
        submitted_from="198.51.100.10",
        submitted_client="unit",
    )
    assert result["ok"] is True

    plan = result["execution_plan"]
    tracking = plan["broker"]["order_tracking"]
    assert isinstance(tracking, dict)
    assert len(tracking) == 3

    actor = "user:traderjoe"
    assert all(str(record.get("submission_source")) == "scheduler" for record in tracking.values())
    assert all(str(record.get("submission_actor")) == actor for record in tracking.values())

    submissions = plan["broker"]["order_submissions"]
    assert len(submissions) == 3
    assert all(item.get("submission_source") == "scheduler" for item in submissions)
    assert all(item.get("submission_actor") == actor for item in submissions)


def test_submission_history_appends_with_source_transitions(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            self.submitted = False

        def submit_order(self, order):
            self.submitted = True
            order_id = str(order.get("order_id") or "")
            return {
                "ok": True,
                "order_id": order_id,
                "broker_order_id": f"repeat_{order_id}",
                "status": "accepted",
            }

        def get_order_status(self, order_id):
            return {
                "ok": True,
                "order_id": str(order_id),
                "status": "accepted",
                "broker_order_id": str(order_id),
            }

        def get_positions(self):
            return {"ok": True, "positions": []}

    broker = FakePaperBroker()
    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": broker,
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_history",
        ticker="NVDA",
        entry_zone="700-740",
        stop_zone="650",
        target_zone="800",
        run_id="run_submit_history",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    first = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        submission_source="api",
        submitted_by="ops",
        submitted_from="198.51.100.50",
        submitted_client="unit",
    )
    assert first["ok"] is True
    first_plan = first["execution_plan"]
    history = first_plan.get("submission_history") or []
    assert len(history) == 1
    assert history[-1]["source"] == "api"
    assert history[-1]["status"] == "submitted"

    second = simulation_execution.submit_simulation_execution_plan(
        execution_id=execution_id,
        submission_source="scheduler",
        submitted_by="ops",
        submitted_from="198.51.100.50",
        submitted_client="unit",
    )
    assert second["ok"] is True
    assert second.get("message") == "already_submitted"
    second_plan = second["execution_plan"]
    history = second_plan.get("submission_history") or []
    assert len(history) == 2
    assert history[-2]["source"] == "api"
    assert history[-1]["source"] == "scheduler"
    assert history[-1]["status"] == "already_submitted"
    assert history[-1]["submission_actor"] == "user:ops"
def test_submit_plan_rejects_unsupported_provider(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "bogus_broker")
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload = _fake_plan_payload(
        sim_id="sim_submit_005",
        ticker="AMZN",
        entry_zone="180-200",
        stop_zone="160",
        target_zone="230",
        run_id="run_submit_5",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "broker_client_init_failed"


def test_normalize_broker_provider_supports_alpaca_aliases():
    assert simulation_execution._normalize_broker_provider("alpaca") == "alpaca"
    assert simulation_execution._normalize_broker_provider("alpaca-paper") == "alpaca"
    assert simulation_execution._normalize_broker_provider("paper_alpaca") == "alpaca"


def test_submit_plan_rejects_alpaca_without_credentials(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "alpaca")
    monkeypatch.delenv("APOLLO_ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("APOLLO_ALPACA_PAPER_KEY", raising=False)
    monkeypatch.delenv("APOLLO_ALPACA_PAPER_API_SECRET", raising=False)
    monkeypatch.delenv("APOLLO_ALPACA_PAPER_SECRET", raising=False)

    payload = _fake_plan_payload(
        sim_id="sim_submit_010",
        ticker="AAPL",
        entry_zone="180-200",
        stop_zone="160",
        target_zone="230",
        run_id="run_submit_10",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "broker_client_init_failed"
    assert "alpaca_credentials_missing" in result["reason"]


def test_submit_plan_uses_alpaca_broker_provider(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "alpaca_paper")
    monkeypatch.setenv("APOLLO_ALPACA_PAPER_API_KEY", "test_alpaca_key")
    monkeypatch.setenv("APOLLO_ALPACA_PAPER_API_SECRET", "test_alpaca_secret")
    monkeypatch.setenv("APOLLO_ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.test")

    calls = []

    class FakeResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        calls.append((method, url))
        if method == "GET" and "/v2/positions" in url:
            return FakeResponse(200, [])
        if method == "GET" and "/v2/account" in url:
            return FakeResponse(200, {"buying_power": "25000"})
        if method == "POST" and "/v2/orders" in url:
            return FakeResponse(200, {
                "id": f"broker_{len(calls)}",
                "status": "accepted",
                "filled_qty": "0",
                "filled_avg_price": "0",
            })
        raise AssertionError(f"Unexpected request: {method} {url}")

    monkeypatch.setattr(simulation_execution.requests, "request", fake_request)

    payload = _fake_plan_payload(
        sim_id="sim_submit_011",
        ticker="NVDA",
        entry_zone="700-740",
        stop_zone="650",
        target_zone="800",
        run_id="run_submit_11",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is True
    plan = result["execution_plan"]
    assert plan["broker"]["provider"] == "alpaca"
    assert len(plan["broker"]["order_submissions"]) == 3
    assert all(item.get("ok") for item in plan["broker"]["order_submissions"])
    assert len([call for call in calls if call[0] == "POST"]) == 3


def test_submit_plan_blocks_live_mode_without_live_flag(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_EXECUTION_MODE", "live")
    monkeypatch.setenv("APOLLO_SIM_LIVE_ENABLED", "0")
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "alpaca")
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_ALPACA_LIVE_API_KEY", "test_live_key")
    monkeypatch.setenv("APOLLO_ALPACA_LIVE_API_SECRET", "test_live_secret")

    payload = _fake_plan_payload(
        sim_id="sim_submit_012",
        ticker="AMD",
        entry_zone="95-105",
        stop_zone="85",
        target_zone="135",
        run_id="run_submit_12",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "broker_client_init_failed"
    assert result["reason"] == "live_execution_disabled"


def test_submit_plan_rejects_live_mode_without_live_credentials(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_EXECUTION_MODE", "live")
    monkeypatch.setenv("APOLLO_SIM_LIVE_ENABLED", "1")
    monkeypatch.setenv("APOLLO_SIM_BROKER_PROVIDER", "alpaca")
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    # Paper keys exist to ensure we don't accidentally fallback to them in live mode.
    monkeypatch.setenv("APOLLO_ALPACA_PAPER_API_KEY", "paper_key")
    monkeypatch.setenv("APOLLO_ALPACA_PAPER_API_SECRET", "paper_secret")
    monkeypatch.delenv("APOLLO_ALPACA_LIVE_API_KEY", raising=False)
    monkeypatch.delenv("APOLLO_ALPACA_LIVE_API_SECRET", raising=False)

    payload = _fake_plan_payload(
        sim_id="sim_submit_013",
        ticker="MSFT",
        entry_zone="420-440",
        stop_zone="390",
        target_zone="480",
        run_id="run_submit_13",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "broker_client_init_failed"
    assert "alpaca_credentials_missing:live" in result["reason"]


def test_submit_plan_reuses_tracking_to_skip_duplicate_submissions(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_BROKER_MAX_RETRIES", "3")
    monkeypatch.setenv("APOLLO_SIM_BROKER_RETRY_WINDOW_SEC", "300")

    calls = {"submit": 0, "status": 0}

    class StatefulFakePaperBroker:
        def __init__(self, *args, **kwargs):
            self.submissions = {}

        def submit_order(self, order):
            calls["submit"] += 1
            order_id = str(order.get("order_id") or "")
            self.submissions[order_id] = "submitted"
            return {
                "ok": True,
                "order_id": order_id,
                "broker_order_id": f"live_{order_id}",
                "status": "accepted",
            }

        def get_order_status(self, order_id):
            calls["status"] += 1
            order_id = str(order_id)
            return {
                "ok": True,
                "order_id": order_id,
                "status": "accepted",
                "broker_order_id": self.submissions.get(order_id, f"live_{order_id}"),
            }

        def get_positions(self):
            return {"ok": True, "positions": []}

    fake = StatefulFakePaperBroker()
    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": fake,
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_014",
        ticker="GOOGL",
        entry_zone="120-130",
        stop_zone="110",
        target_zone="150",
        run_id="run_submit_14",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    first = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert first["ok"] is True
    assert calls["submit"] == 3
    assert first["execution_plan"]["broker"]["order_submitted"] is True

    second = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert second["ok"] is True
    assert second.get("message") == "already_submitted"
    assert calls["submit"] == 3
    assert second["execution_plan"]["broker"]["order_submissions"] == []
    assert set(second["execution_plan"]["broker"]["order_decisions"].values()) == {"skip"}


def test_submit_plan_rate_limits_repeated_operators(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_MIN_SECONDS_BETWEEN_SUBMISSIONS", "120")
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    payload_a = {
        "simulation": {
            "sim_id": "sim_rate_a",
            "ticker": "MSFT",
            "entry_zone": "420-440",
            "stop_zone": "390",
            "target_zone": "470",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_rate_a",
        }
    }
    payload_b = {
        "simulation": {
            "sim_id": "sim_rate_b",
            "ticker": "AMD",
            "entry_zone": "190-210",
            "stop_zone": "175",
            "target_zone": "240",
            "status": "ready",
            "setup_type": "trend_continuation",
            "run_id": "run_rate_b",
        }
    }
    created_a = simulation_execution.create_simulation_execution_plan(payload_a)
    created_b = simulation_execution.create_simulation_execution_plan(payload_b)
    a_id = created_a["execution_plan"]["execution_id"]
    b_id = created_b["execution_plan"]["execution_id"]

    simulation_execution.approve_simulation_execution_plan(execution_id=a_id, approved_by="ops")
    first = simulation_execution.submit_simulation_execution_plan(
        execution_id=a_id,
        submitted_by="ops",
        submitted_from="198.51.100.50",
        submitted_client="unit",
    )
    assert first["ok"] is True

    simulation_execution.approve_simulation_execution_plan(execution_id=b_id, approved_by="ops")
    second = simulation_execution.submit_simulation_execution_plan(
        execution_id=b_id,
        submitted_by="ops",
        submitted_from="198.51.100.50",
        submitted_client="unit",
    )
    assert second["ok"] is False
    assert second["error"] == "submission_rate_limited"
    assert second["details"]["error"] == "submission_rate_limited"


def test_submit_plan_retries_stale_orders_within_retry_policy(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_BROKER_MAX_RETRIES", "3")
    monkeypatch.setenv("APOLLO_SIM_BROKER_RETRY_WINDOW_SEC", "0")
    monkeypatch.setenv("APOLLO_SIM_STALE_ORDER_SECONDS", "60")

    calls = {"submit": 0, "status": 0}

    class StatefulFakePaperBroker:
        def __init__(self, *args, **kwargs):
            self.submissions = {}

        def submit_order(self, order):
            calls["submit"] += 1
            order_id = str(order.get("order_id") or "")
            self.submissions[order_id] = "accepted"
            return {
                "ok": True,
                "order_id": order_id,
                "broker_order_id": f"broker_{order_id}",
                "status": "accepted",
            }

        def get_order_status(self, order_id):
            calls["status"] += 1
            order_id = str(order_id)
            return {
                "ok": True,
                "order_id": order_id,
                "status": "accepted",
                "broker_order_id": self.submissions.get(order_id, f"broker_{order_id}"),
            }

        def get_positions(self):
            return {"ok": True, "positions": []}

    fake = StatefulFakePaperBroker()
    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": fake,
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_015",
        ticker="AAPL",
        entry_zone="160-175",
        stop_zone="145",
        target_zone="200",
        run_id="run_submit_15",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    first = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert first["ok"] is True
    assert calls["submit"] == 3
    assert calls["status"] == 0

    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    plan = simulation_execution.get_simulation_execution_plan(execution_id)
    tracking = plan["broker"]["order_tracking"]
    for record in tracking.values():
        if isinstance(record, dict):
            record["last_attempt_at"] = stale_time
    simulation_execution._save_plan(plan)

    second = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert second["ok"] is True
    assert calls["status"] > 0
    assert calls["submit"] > 3


def test_sync_plan_status_collects_broker_states(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    calls = {"status": 0}

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            pass

        def submit_order(self, order):
            order_id = str(order.get("order_id") or "")
            return {"ok": True, "order_id": order_id, "broker_order_id": order_id, "status": "submitted"}

        def get_order_status(self, order_id):
            calls["status"] += 1
            return {
                "ok": True,
                "order_id": order_id,
                "status": "filled",
                "filled": 1,
                "filled_avg_price": 410.0,
            }

        def get_positions(self):
            return {"ok": True, "positions": []}

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_006",
        ticker="AAPL",
        entry_zone="180-200",
        stop_zone="160",
        target_zone="230",
        run_id="run_submit_6",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")
    submit = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert submit["ok"] is True

    sync = simulation_execution.sync_simulation_execution_plan_with_broker(execution_id=execution_id)
    assert sync["ok"] is True
    refreshed = sync["execution_plan"]
    assert calls["status"] == 3
    assert refreshed["broker"]["fill_status"] == "filled"
    assert len(refreshed["broker"].get("order_statuses") or []) == 3


def test_submit_plan_rejects_open_position_cap(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_MAX_OPEN_POSITIONS", "1")

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            pass

        def submit_order(self, order):
            order_id = str(order.get("order_id") or "")
            return {"ok": True, "order_id": order_id, "broker_order_id": order_id, "status": "submitted"}

        def get_order_status(self, order_id):
            return {"ok": True, "order_id": order_id, "status": "submitted"}

        def get_positions(self):
            return {
                "ok": True,
                "positions": [
                    {"ticker": "NVDA", "side": "buy", "status": "open", "qty": 10, "entry_price": 100.0},
                ],
            }

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_007",
        ticker="MSFT",
        entry_zone="100-120",
        stop_zone="80",
        target_zone="150",
        run_id="run_submit_7",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "risk_control_open_position_limit"
    assert result["details"]["max_open_positions"] == 1
    assert result["details"]["open_positions"] == 1


def test_submit_plan_rejects_duplicate_ticker_same_side(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            pass

        def submit_order(self, order):
            order_id = str(order.get("order_id") or "")
            return {"ok": True, "order_id": order_id, "broker_order_id": order_id, "status": "submitted"}

        def get_order_status(self, order_id):
            return {"ok": True, "order_id": order_id, "status": "submitted"}

        def get_positions(self):
            return {
                "ok": True,
                "positions": [
                    {"ticker": "AMD", "side": "buy", "status": "open", "qty": 4, "entry_price": 90.0},
                ],
            }

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_008",
        ticker="AMD",
        entry_zone="95-105",
        stop_zone="85",
        target_zone="140",
        run_id="run_submit_8",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "risk_control_duplicate_side"
    assert result["details"]["ticker"] == "AMD"
    assert result["details"]["side"] == "buy"


def test_submit_plan_rejects_insufficient_buying_power(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_SIMULATED_BUYING_POWER", "100")

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            pass

        def submit_order(self, order):
            order_id = str(order.get("order_id") or "")
            return {"ok": True, "order_id": order_id, "broker_order_id": order_id, "status": "submitted"}

        def get_order_status(self, order_id):
            return {"ok": True, "order_id": order_id, "status": "submitted"}

        def get_positions(self):
            return {"ok": True, "positions": []}

        def available_buying_power(self):
            return 100.0

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_submit_009",
        ticker="NVDA",
        entry_zone="20-22",
        stop_zone="10",
        target_zone="30",
        run_id="run_submit_9",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    result = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert result["ok"] is False
    assert result["error"] == "risk_control_insufficient_buying_power"
    assert result["details"]["ticker"] == "NVDA"
    assert result["details"]["requested_notional"] > result["details"]["available_buying_power"]


def test_sync_plan_with_filled_entry_updates_focus_trading_state(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    state_path = tmp_path / "focus_state" / "focus_trading_state.json"
    monkeypatch.setenv("APOLLO_FOCUS_TRADING_STATE_PATH", str(state_path))

    class FakePaperBroker:
        def __init__(self, *args, **kwargs):
            pass

        def submit_order(self, order):
            order_id = str(order.get("order_id") or "")
            return {"ok": True, "order_id": order_id, "broker_order_id": order_id, "status": "submitted"}

        def get_order_status(self, order_id):
            order_id = str(order_id)
            payload = {
                "ok": True,
                "order_id": order_id,
                "status": "submitted",
                "filled": 0,
            }
            if order_id.endswith("_1"):
                payload["status"] = "filled"
                payload["filled"] = 4
                payload["filled_avg_price"] = 412.5
            return payload

        def get_positions(self):
            return {"ok": True, "positions": []}

    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": FakePaperBroker(),
    )

    payload = _fake_plan_payload(
        sim_id="sim_sync_001",
        ticker="MSFT",
        entry_zone="395-405",
        stop_zone="360",
        target_zone="460",
        run_id="run_sync_1",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    submit = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert submit["ok"] is True

    sync = simulation_execution.sync_simulation_execution_plan_with_broker(execution_id=execution_id)
    assert sync["ok"] is True
    refreshed = sync["execution_plan"]
    focus_sync = refreshed["broker"]["focus_sync"]
    assert focus_sync["ok"] is True
    assert focus_sync["updated"] is True
    assert focus_sync["ticker"] == "MSFT"
    assert focus_sync["execution_id"] == execution_id

    import json

    state = json.loads(state_path.read_text(encoding="utf-8"))
    positions = [row for row in state.get("paper_positions", []) if isinstance(row, dict)]
    assert len(positions) == 1
    row = positions[0]
    assert row["ticker"] == "MSFT"
    assert row["stance"] == "buy"
    assert row["status"] == "paper_open"
    assert row["entry_order_id"].endswith(f"_{execution_id}_1")
    assert row["entry_order_provider_status"] == "filled"
    assert row["fill_price"] == 412.5
    assert row["filled_avg_price"] == 412.5


def test_reconcile_in_active_plan_retries_stale_orders(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", "1000")
    monkeypatch.setenv("APOLLO_SIM_MAX_POSITION_QTY", "50")
    monkeypatch.setenv("APOLLO_SIM_MIN_POSITION_QTY", "1")
    monkeypatch.setenv("APOLLO_SIM_BROKER_MAX_RETRIES", "3")
    monkeypatch.setenv("APOLLO_SIM_BROKER_RETRY_WINDOW_SEC", "0")
    monkeypatch.setenv("APOLLO_SIM_STALE_ORDER_SECONDS", "60")

    calls = {"submit": 0}

    class StatefulFakePaperBroker:
        def __init__(self, *args, **kwargs):
            self.submissions = {}

        def submit_order(self, order):
            calls["submit"] += 1
            order_id = str(order.get("order_id") or "")
            broker_order_id = f"broker_{order_id}"
            self.submissions[order_id] = broker_order_id
            return {
                "ok": True,
                "order_id": order_id,
                "broker_order_id": broker_order_id,
                "status": "accepted",
            }

        def get_order_status(self, order_id):
            order_id = str(order_id)
            return {
                "ok": True,
                "order_id": order_id,
                "status": "accepted",
                "broker_order_id": self.submissions.get(order_id, f"broker_{order_id}"),
            }

        def get_positions(self):
            return {"ok": True, "positions": []}

    fake = StatefulFakePaperBroker()
    monkeypatch.setattr(
        simulation_execution,
        "_build_broker_client",
        lambda provider, execution_root=None, execution_mode="paper": fake,
    )

    payload = _fake_plan_payload(
        sim_id="sim_reconcile_001",
        ticker="AAPL",
        entry_zone="160-175",
        stop_zone="145",
        target_zone="200",
        run_id="run_reconcile_1",
    )
    created = simulation_execution.create_simulation_execution_plan(payload)
    execution_id = created["execution_plan"]["execution_id"]
    simulation_execution.approve_simulation_execution_plan(execution_id=execution_id, approved_by="ops")

    first = simulation_execution.submit_simulation_execution_plan(execution_id=execution_id)
    assert first["ok"] is True
    assert calls["submit"] == 3

    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    plan = simulation_execution.get_simulation_execution_plan(execution_id)
    tracking = plan["broker"]["order_tracking"]
    for record in tracking.values():
        if isinstance(record, dict):
            record["last_attempt_at"] = stale_time
    simulation_execution._save_plan(plan)

    reconciled = simulation_execution.reconcile_simulation_execution_orders(max_plans=1, requested_by="ops")
    assert reconciled["ok"] is True
    assert reconciled["summary"]["plans_considered"] == 1
    assert reconciled["summary"]["reconciled"] == 3
    assert reconciled["summary"]["checked"] == 3
    assert reconciled["summary"]["errors"] == 0
    assert calls["submit"] == 6


def test_reconcile_skips_when_lock_in_use(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)

    stale_lock = {"owner": 99999, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "created_at_epoch": datetime.now(timezone.utc).timestamp()}
    simulation_execution._write_json(simulation_execution.SIMULATION_EXECUTION_RECONCILE_LOCK, stale_lock)

    result = simulation_execution.reconcile_simulation_execution_orders(max_plans=1, requested_by="ops")
    assert result["ok"] is False
    assert result["error"] == "reconcile_lock_in_use"


def test_reconcile_clears_stale_lock_and_runs(tmp_path, monkeypatch):
    _set_root(monkeypatch, tmp_path)
    monkeypatch.setenv("APOLLO_SIM_RECONCILE_LOCK_STALE_SECONDS", "30")

    stale_lock = {
        "owner": 99999,
        "created_at": (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
        "created_at_epoch": (datetime.now(timezone.utc) - timedelta(seconds=120)).timestamp(),
    }
    simulation_execution._write_json(simulation_execution.SIMULATION_EXECUTION_RECONCILE_LOCK, stale_lock)

    result = simulation_execution.reconcile_simulation_execution_orders(max_plans=1, requested_by="ops")
    assert result["ok"] is True
    assert result["lock"]["stale_lock_cleared"] is True
    assert result["summary"]["plans_considered"] == 0
