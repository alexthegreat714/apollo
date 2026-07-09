from __future__ import annotations

from pathlib import Path

from Apollo import daily_report


def _patch_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(daily_report, "_APOLLO_ROOT", tmp_path / "Apollo")
    monkeypatch.setattr(daily_report, "REPORT_ROOT", tmp_path / "daily_reports")
    monkeypatch.setattr(daily_report, "README_PATH", tmp_path / "Apollo" / "README.md")
    monkeypatch.setenv("APOLLO_DAILY_BALANCE_NOTIFY", "0")
    monkeypatch.setattr(
        daily_report,
        "_load_latest_nightly",
        lambda: {
            "run_id": "nightly_unit",
            "ok": False,
            "current_stage": "done",
            "truth_label": "quality_failed",
            "pipeline_completed": True,
            "quality_gate_pass": False,
            "quality": {"gate_fail_reasons": ["ocr_score_below_quality_min:30<70"]},
            "ocr": {"failure_class": "no_valid_documents", "backend_ready": True, "evaluated_documents": 0},
            "stage_truth": {"ocr": {"ok": True, "completed": True, "result_path": "02_ocr_ingest.json"}},
        },
    )
    monkeypatch.setattr(
        daily_report,
        "_load_latest_swing",
        lambda: {
            "run_id": "swing_unit",
            "best_proposal": {"ticker": "NVDA", "score": 88},
            "proposals": [
                {
                    "ticker": "NVDA",
                    "setup_type": "pullback_to_trend",
                    "score": 88,
                    "confidence_label": "high",
                    "risk_reward_estimate": 2.1,
                    "entry_zone": "100.00-102.00",
                    "stop_zone": "96.00",
                    "target_zone": "110.00",
                    "time_horizon": "2_to_20_trading_days",
                    "relative_strength_rank": 82,
                    "atr_pct": 3.1,
                    "swing_snapshot": {"source": "yahoo_chart_daily", "as_of": "2026-05-14"},
                    "kg_grounding": {"triple_count": 4},
                    "earnings_or_event_risk": {"label": "normal"},
                }
            ],
            "market_regime": {"label": "risk_on"},
        },
    )
    monkeypatch.setattr(
        daily_report,
        "_load_sim_account",
        lambda: {
            "ok": True,
            "account": {
                "starting_balance": 100,
                "cash_balance": 80,
                "invested_amount": 20,
                "market_value": 101,
                "realized_return": 0,
                "realized_return_pct": 0,
                "unrealized_return": 1,
                "unrealized_return_pct": 1,
            },
            "next_cycle_plan": {
                "status": "queued",
                "planned_investment": 20,
                "planned_cash_reserve": 80,
                "note": "unit plan",
                "allocations": [{"ticker": "NVDA", "amount": 20, "entry_zone": "100.00-102.00"}],
            },
        },
    )


def _write_daily_report(tmp_path: Path, report_date: str, market_value: float) -> None:
    report_dir = tmp_path / "daily_reports" / report_date
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "apollo_daily_report.json").write_text(
        (
            "{"
            f'"date": "{report_date}", '
            f'"final_summary": {{"market_value": {market_value}}}, '
            f'"simulation_account": {{"market_value": {market_value}}}'
            "}"
        ),
        encoding="utf-8",
    )


def test_daily_report_created_and_records_gate_event(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)

    payload = daily_report.ensure_daily_report("2026-05-14")
    daily_report.record_gate_result(
        {
            "run_id": "gate_unit",
            "trigger": "manual",
            "candidate": {"ticker": "NVDA"},
            "decision": "clear_for_paper_sim",
            "blocker": "",
            "next_action": "Paper simulation may proceed after normal approval.",
            "score": 88,
            "headlines": [{"title": "upgrade"}],
            "quote": {"price": 101},
            "live_guard": {"ok": True},
            "json_path": "gate.json",
            "markdown_path": "gate.md",
        },
        report_date="2026-05-14",
    )
    result = daily_report.get_daily_report("2026-05-14")

    assert payload["date"] == "2026-05-14"
    assert result["report"]["latest_gate"]["decision"] == "clear_for_paper_sim"
    assert "NVDA" in result["markdown"]
    assert "Starting balance: $100.00" in result["markdown"]
    assert "## Nightly Truth" in result["markdown"]
    assert "quality gate failed because ocr_score_below_quality_min:30<70" in result["markdown"]
    assert "OCR failure class: `no_valid_documents`" in result["markdown"]
    assert "Homework trade proof: `missing_today`" in result["markdown"]
    assert "no graph images generated today" in result["markdown"]
    assert "## Simulation Truth" in result["markdown"]
    assert "## Paper Risk Ladder" in result["markdown"]
    assert "Current tier: `tier_0_hold`" in result["markdown"]
    assert "## Swing Trade Rationale" in result["markdown"]
    assert "not long-term/low-risk" in result["markdown"]
    assert "yahoo_chart_daily" in result["markdown"]
    assert Path(result["paths"]["json"]).exists()
    assert Path(result["paths"]["markdown"]).exists()


def test_daily_report_start_balance_matches_previous_final_close(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    _write_daily_report(tmp_path, "2026-05-13", 101.0)

    payload = daily_report.ensure_daily_report("2026-05-14")
    result = daily_report.get_daily_report("2026-05-14")

    assert payload["daily_balance_check"]["start_balance"] == 101.0
    assert payload["daily_balance_check"]["previous_end_balance"] == 101.0
    assert payload["daily_balance_check"]["reconciled"] is True
    assert payload["daily_balance_check"]["status"] == "matched_previous_close"
    assert "Daily start balance: $101.00" in result["markdown"]
    assert "Yesterday ending balance: $101.00 (`matched_previous_close`)" in result["markdown"]


def test_daily_report_sends_daily_balance_notification_once(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    _write_daily_report(tmp_path, "2026-05-13", 101.0)
    monkeypatch.setattr(daily_report, "_ct_date", lambda dt=None: "2026-05-14")
    monkeypatch.setenv("APOLLO_DAILY_BALANCE_NOTIFY", "1")
    captured = []

    class _Response:
        ok = True
        status_code = 200

    def _post(url, json, headers, timeout):
        captured.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(daily_report.requests, "post", _post)

    first = daily_report.ensure_daily_report("2026-05-14")
    second = daily_report.ensure_daily_report("2026-05-14")

    assert first["daily_balance_notification"]["sent"] is True
    assert second["daily_balance_notification"]["sent"] is True
    assert len(captured) == 1
    assert captured[0]["json"]["title"] == "Apollo daily paper balance"
    assert captured[0]["json"]["start_balance"] == 101.0
    assert captured[0]["json"]["previous_end_balance"] == 101.0
    assert captured[0]["json"]["reconciled"] is True


def test_daily_report_renders_learning_summary(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    learning_dir = tmp_path / "Apollo" / "logs" / "learning"
    learning_dir.mkdir(parents=True)
    (learning_dir / "latest_learning_state.json").write_text(
        (
            "{"
            '"run_id": "learning_unit", '
            '"created_at": "2026-05-14T21:28:00Z", '
            '"summary": {'
            '"record_count": 4, '
            '"best_setup_types": [{"value": "base_breakout", "expectancy_pct": 2.4, "sample_count": 3}], '
            '"worst_setup_types": [{"value": "event_dislocation", "expectancy_pct": -1.2, "sample_count": 2}], '
            '"best_tickers": [{"value": "NVDA", "expectancy_pct": 2.4, "sample_count": 3}], '
            '"worst_tickers": [{"value": "AMD", "expectancy_pct": -1.2, "sample_count": 2}], '
            '"score_adjustments_applied": [{"key": "setup_type:base_breakout", "recommended_score_adjustment": 1.5}]'
            "}"
            "}"
        ),
        encoding="utf-8",
    )

    result = daily_report.get_daily_report("2026-05-14")

    assert result["report"]["learning_summary"]["run_id"] == "learning_unit"
    assert "## What Apollo Learned Today" in result["markdown"]
    assert "base_breakout" in result["markdown"]
    assert "setup_type:base_breakout=1.5" in result["markdown"]


def test_daily_report_finalize_includes_account_and_plan(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)

    result = daily_report.finalize_daily_report("2026-05-14", note="unit final")

    assert result["final_summary"]["note"] == "unit final"
    assert result["final_summary"]["starting_balance"] == 100
    assert result["final_summary"]["planned_investment"] == 20
    assert "unit final" in daily_report.get_daily_report("2026-05-14")["markdown"]
    readme = daily_report.README_PATH.read_text(encoding="utf-8")
    assert daily_report.README_START in readme
    assert daily_report.README_END in readme
    assert "2026-05-14" in readme
    assert "Apollo Daily Report Completion Log" in readme


def test_daily_report_discovers_homework_and_graph_artifacts(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    apollo_root = tmp_path / "Apollo"
    run_dir = apollo_root / "logs" / "homework_trade" / "2026-05-14" / "trade_unit"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text('{"ok": true, "run_id": "trade_unit", "status": "complete"}', encoding="utf-8")
    (run_dir / "homework_trade_report.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "homework_trade_plan.json").write_text("{}", encoding="utf-8")
    graph_dir = apollo_root / "logs" / "plots" / "2026-05-14" / "graph_unit"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph_summary.md").write_text("# graph\n", encoding="utf-8")
    (graph_dir / "top_entities.png").write_bytes(b"png")

    result = daily_report.get_daily_report("2026-05-14")

    assert result["report"]["homework_trade"]["status"] == "complete"
    assert result["report"]["graph_artifacts"]["status"] == "available"
    assert "Homework trade proof: `complete`" in result["markdown"]
    assert "top_entities.png" in result["markdown"]


def test_daily_report_includes_high_risk_homework_confidence(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    apollo_root = tmp_path / "Apollo"
    latest = apollo_root / "reports" / "nightly_high_risk_homework" / "latest"
    latest.mkdir(parents=True)
    (latest / "status.json").write_text(
        '{"status": "passed", "run_id": "risk_homework_unit", "score": 86, '
        '"markdown_path": "Apollo/reports/nightly_high_risk_homework/latest/report.md", '
        '"next_day_projection": {"primary_action": "wait_for_entry_reset"}, '
        '"confidence_model": {"source_confidence": "medium", "event_confidence": "medium", '
        '"graph_usefulness": "supporting_context_only", "technical_confirmation": "medium", '
        '"top_blocker": "price_volume_confirmation", '
        '"blocker_hierarchy": ["price_volume_confirmation", "trade_risk_gate"]}, '
        '"ocr97_status": {"status": "passed", "fresh": true, "quality_confident": true, "score": 91}}',
        encoding="utf-8",
    )

    result = daily_report.get_daily_report("2026-05-14")

    assert result["report"]["high_risk_homework"]["projection_action"] == "wait_for_entry_reset"
    assert result["report"]["high_risk_homework"]["top_blocker"] == "price_volume_confirmation"
    assert "High-risk homework: `passed`; action `wait_for_entry_reset`; score `86`" in result["markdown"]
    assert "Top blocker hierarchy: `price_volume_confirmation, trade_risk_gate`" in result["markdown"]


def test_daily_report_renders_overnight_component_statuses(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    apollo_root = tmp_path / "Apollo"

    scheduler = apollo_root / "logs" / "nightly" / "scheduler_runs"
    scheduler.mkdir(parents=True)
    (scheduler / "nightly_20260514_010001.stdout.log").write_text(
        "[nightly_chain] Could not post curated enrichment event: Sky timeout\n",
        encoding="utf-8",
    )

    trade_dir = apollo_root / "logs" / "trade_cycle" / "trade_cycle_20260514_040001"
    trade_dir.mkdir(parents=True)
    (trade_dir / "status.json").write_text(
        '{"completed": true, "ok": true, "run_id": "trade_cycle_20260514_040001", "status_label": "trade_ready", '
        '"top_ticker": "NVDA", "trade_blockers": [], '
        '"deep_trade_adjudication": {"status": "unavailable", "decision": "adjudication_unavailable", "fallback_policy": "deterministic_simulation_only"}}',
        encoding="utf-8",
    )

    corpus = apollo_root / "logs" / "corpus_refresh"
    corpus.mkdir(parents=True)
    (corpus / "latest_corpus_refresh.json").write_text(
        '{"ok": true, "run_id": "corpus_unit", "ingest": {"exit_code": 0}, "graph": {"exit_code": 0}}',
        encoding="utf-8",
    )

    hippo = apollo_root / "logs" / "hipporag_improve" / "2026-05-14" / "hippo_unit"
    hippo.mkdir(parents=True)
    (hippo / "status.json").write_text('{"state": "complete", "run_id": "hippo_unit", "build": {"ok": true}, "summary": {"ok": true}}', encoding="utf-8")

    result = daily_report.get_daily_report("2026-05-14")

    assert "## Overnight Components" in result["markdown"]
    assert "Scheduled 1:00 AM nightly: `found`" in result["markdown"]
    assert "Embedded trade-cycle homework: `complete`" in result["markdown"]
    assert "fallback=`deterministic_simulation_only`" in result["markdown"]
    assert "Corpus refresh: `complete`" in result["markdown"]
    assert "Improved HippoRAG: `complete`" in result["markdown"]
    assert result["report"]["hipporag_discovery"]["actual_latest_status"] == "complete"
    assert "HippoRAG discovery: reported=`complete` actual=`complete` mismatch=`False`" in result["markdown"]
    assert "Notification issue: `Sky calendar post timed out`" in result["markdown"]


def test_nightly_scheduler_status_uses_full_stdout_not_tail_only(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    apollo_root = tmp_path / "Apollo"
    scheduler = apollo_root / "logs" / "nightly" / "scheduler_runs"
    scheduler.mkdir(parents=True)
    (scheduler / "nightly_20260514_010001.stdout.log").write_text(
        (
            '{"run_id":"daily_nightly_20260514_010001","quality_gate_pass": true,"truth_label": "passed"}\n'
            '{"ok":false,"error":"event_overlap"}\n'
        ),
        encoding="utf-8",
    )

    status = daily_report._nightly_scheduler_status("2026-05-14")

    assert status["status"] == "found"
    assert status["scheduler_failed"] is False


def test_daily_report_risk_ladder_blocks_plan_truth_mismatch(tmp_path, monkeypatch):
    _patch_report(monkeypatch, tmp_path)
    apollo_root = tmp_path / "Apollo"
    run_dir = apollo_root / "logs" / "homework_trade" / "2026-05-14" / "trade_unit"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text('{"ok": true, "run_id": "trade_unit", "status": "complete"}', encoding="utf-8")
    monkeypatch.setattr(
        daily_report,
        "_load_latest_nightly",
        lambda: {
            "run_id": "nightly_unit",
            "ok": True,
            "truth_label": "passed",
            "pipeline_completed": True,
            "quality_gate_pass": True,
            "quality": {"gate_pass": True},
            "stage_truth": {},
        },
    )
    monkeypatch.setattr(
        daily_report,
        "_load_sim_account",
        lambda: {
            "ok": True,
            "account": {
                "starting_balance": 100,
                "cash_balance": 100,
                "market_value": 100,
                "closed_positions": [],
                "open_positions": [],
                "simulation_warnings": [],
                "performance_stats": {"expectancy_pct": 2.0},
            },
            "next_cycle_plan": {
                "status": "queued_for_next_cycle",
                "planned_investment": 40,
                "planned_cash_reserve": 60,
                "allocations": [{"ticker": "MRVL", "amount": 40}],
            },
        },
    )

    daily_report.record_gate_result(
        {
            "run_id": "gate_unit",
            "trigger": "manual",
            "candidate": {"ticker": "TSM"},
            "decision": "clear_for_paper_sim",
            "blocker": "",
            "next_action": "Paper simulation may proceed after normal approval.",
            "score": 82,
            "headlines": [{"title": "positive"}],
            "live_guard": {"live_trade_execution": False},
        },
        report_date="2026-05-14",
    )
    result = daily_report.get_daily_report("2026-05-14")

    assert result["report"]["risk_scaling"]["risk_tier"] == "tier_0_hold"
    assert result["report"]["risk_scaling"]["active_paper_intent"]["mismatch"] is True
    assert "plan_truth_consistent" in result["markdown"]
