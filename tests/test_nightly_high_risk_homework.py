from __future__ import annotations

from pathlib import Path

from Apollo import nightly_high_risk_homework as homework


def test_projection_prefers_pressed_aggressive_candidate():
    context = {
        "ocr97_status": {"status": "passed", "fresh": True, "quality_confident": True, "score": 92},
        "phase_outputs": {
            "news_catalyst_gather": {
                "best_event_impact": {"top_event": {"source_quality": 86}},
                "high_confidence": True,
            },
            "high_risk_paper_screen": {
                "pressed_candidates": [{"ticker": "DDOG", "rr": 2.5, "next_action": "paper_pressed_aggressive_ready"}],
                "aggressive_candidates": [{"ticker": "UBER", "rr": 1.8}],
                "wait_for_entry_reset": [],
            },
            "technical_confirmation_trade_cycle": {
                "aggressive_probe_ready": True,
                "probe_ready": False,
                "trade_blockers": [],
                "evidence_quality": {"news_evidence": "good", "news_source_quality": "tier_a_mixed"},
            },
            "graph_rag_enrichment": {"hipporag_label": "graph_augmented", "fail_soft": False, "dense_only_avg": 80, "dense_graph_avg": 84},
        }
    }

    projection = homework._build_projection(context)

    assert projection["primary_action"] == "paper_pressed_aggressive_probe"
    assert projection["primary_candidate"]["ticker"] == "DDOG"
    assert projection["risk_posture"] == "pressed_simulation_only"
    assert projection["paper_only"] is True
    assert projection["live_trade_execution"] is False
    assert projection["human_approval_required"] is True


def test_high_risk_screen_classifies_graph_backed_pressed_candidate():
    context = {
        "phase_outputs": {
            "event_dislocation_study": {
                "top_proposals": [
                    {
                        "ticker": "DDOG",
                        "score": 84,
                        "risk_reward_estimate": 2.4,
                        "direction_bias": "long_bias",
                        "entry_zone": "100-101",
                        "stop_zone": "95",
                        "target_zone": "115",
                        "event_impact": {"impact_score": 76},
                        "volume_confirmation": {"volume_ratio": 1.4},
                        "graph_risk_appetite": {"score": 82},
                        "aggressive_reward_profile": {"pressed_candidate": True},
                    }
                ]
            }
        }
    }

    result = homework._phase_high_risk_screen(context)

    assert result["pressed_count"] == 1
    assert result["pressed_candidates"][0]["ticker"] == "DDOG"
    assert result["pressed_candidates"][0]["next_action"] == "paper_pressed_aggressive_ready"
    assert result["live_trade_execution"] is False


def test_run_homework_writes_phase_reports_with_monkeypatched_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(homework, "REPORT_ROOT", tmp_path)
    monkeypatch.setattr(homework, "_tickers", lambda raw="": ["DDOG", "UBER"])
    monkeypatch.setattr(homework, "_load_ocr97_status", lambda: {"status": "passed", "fresh": True, "quality_confident": True, "score": 92})

    def ok_phase(context):
        return {"ok": True, "phase": "ok"}

    monkeypatch.setitem(homework.PHASE_HANDLERS, "post_close_market_snapshot", lambda context: {"ok": True, "ticker_count": 2, "ok_count": 2})
    monkeypatch.setitem(homework.PHASE_HANDLERS, "news_catalyst_gather", lambda context: {"ok": True, "summary_count": 2, "high_confidence": True})
    monkeypatch.setitem(homework.PHASE_HANDLERS, "event_dislocation_study", lambda context: {"ok": True, "proposal_count": 1, "top_proposals": []})
    monkeypatch.setitem(homework.PHASE_HANDLERS, "high_risk_paper_screen", lambda context: {"ok": True, "pressed_candidates": [], "aggressive_candidates": [], "wait_for_entry_reset": []})
    monkeypatch.setitem(homework.PHASE_HANDLERS, "graph_rag_enrichment", lambda context: {"ok": True, "hipporag_label": "supporting_context_only"})
    monkeypatch.setitem(homework.PHASE_HANDLERS, "technical_confirmation_trade_cycle", lambda context: {"ok": True, "status_label": "blocked", "trade_blockers": ["news_evidence_missing"]})
    monkeypatch.setitem(homework.PHASE_HANDLERS, "pressed_aggressive_projection", ok_phase)
    monkeypatch.setitem(homework.PHASE_HANDLERS, "deep_daily_report", lambda context: {"ok": True, "markdown_path": "daily.md"})

    result = homework.run_homework(run_id="unit_high_risk_homework", write_reports=True)

    assert result["ok"] is True
    assert result["status"] == "passed"
    assert Path(result["json_path"]).exists()
    assert Path(result["markdown_path"]).exists()
    assert len(result["phases"]) == len(homework.PHASE_ORDER)
    assert all(Path(phase["json_path"]).exists() for phase in result["phases"])
    assert result["confidence_model"]["top_blocker"] == "source_quality"


def test_projection_downgrades_aggressive_action_on_weak_input_quality():
    context = {
        "ocr97_status": {"status": "passed", "fresh": False, "quality_confident": False, "score": 70},
        "phase_outputs": {
            "news_catalyst_gather": {
                "best_event_impact": {"top_event": {"source_quality": 40}},
                "high_confidence": False,
            },
            "high_risk_paper_screen": {
                "pressed_candidates": [{"ticker": "DDOG", "rr": 2.1, "next_action": "paper_pressed_aggressive_ready"}],
                "aggressive_candidates": [],
                "wait_for_entry_reset": [],
            },
            "technical_confirmation_trade_cycle": {
                "status_label": "aggressive_probe_ready",
                "aggressive_probe_ready": True,
                "trade_blockers": [],
                "evidence_quality": {"news_evidence": "weak", "news_source_quality": "c_tier_only_yahoo_rss"},
                "deep_trade_adjudication": {"status": "unavailable"},
            },
            "graph_rag_enrichment": {"hipporag_label": "supporting_context_only", "dense_only_avg": 80, "dense_graph_avg": 79},
        },
    }

    projection = homework._build_projection(context)

    assert projection["primary_action"] == "watch_for_volume_confirmation"
    assert projection["confidence_model"]["top_blocker"] == "source_quality"
    assert "ocr_document_quality" in projection["confidence_model"]["blocker_hierarchy"]
