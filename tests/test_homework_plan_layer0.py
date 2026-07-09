from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from Apollo import trade_cycle
from Apollo import nightly_high_risk_homework as homework
import common.gb10_gate as gb10_gate


def test_parse_adjudication_reply_handles_nested_think_and_code_fence():
    cases = [
        ('{"decision": "approved_standard", "rationale": "strong setup"}', True),
        ('<think>Let me reason...</think>\n{"decision": "approved_standard", "rationale": "ok"}', True),
        ('```json\n{"decision": "downgrade_to_watch", "rationale": "regime weak"}\n```', True),
        ('After careful analysis: {"decision": "blocked", "rationale": "no catalyst"} end.', True),
        ('<think>deep thought</think>\n```json\n{"decision": "approved_probe", "rationale": "x"}\n```', True),
    ]
    for text, expect_ok in cases:
        parsed = trade_cycle._parse_adjudication_reply(text)
        assert isinstance(parsed, dict)
        assert parsed["parse_ok"] is expect_ok
        if expect_ok:
            assert parsed["decision"] in {
                "approved_standard",
                "approved_probe",
                "approved_aggressive_probe",
                "downgrade_to_watch",
                "blocked",
            }


def test_parse_adjudication_reply_rejects_invalid_inputs():
    for text in ["", "not json at all", None]:
        parsed = trade_cycle._parse_adjudication_reply(text)
        assert isinstance(parsed, dict)
        assert parsed["parse_ok"] is False
        assert parsed["decision"] == "adjudication_unavailable"


def _fake_ocr_ledger(score: float = 100.0, completed_at: str | None = None, status: str = "complete") -> dict:
    return {
        "project": "ocr97",
        "project_label": "OCR97",
        "latest_score": score,
        "latest_status": status,
        "latest_completed_at": completed_at or "",
    }


def test_load_ocr97_status_current_is_fresh_and_confident(monkeypatch):
    from Sky.services import test_run_reports

    ledger = {
        "projects": {
            "ocr97": _fake_ocr_ledger(
                score=100.0,
                completed_at="2026-06-25T20:47:20Z",
                status="complete",
            )
        }
    }
    monkeypatch.setattr(test_run_reports, "load_project_capability_ledger", lambda: ledger)

    status = homework._load_ocr97_status()

    assert status["fresh"] is True
    assert status["quality_confident"] is True
    assert status["score"] == 100.0
    assert status["status"] == "complete"


def test_load_ocr97_status_stale_fails_fresh_gate(monkeypatch):
    from Sky.services import test_run_reports

    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    ledger = {"projects": {"ocr97": _fake_ocr_ledger(score=100.0, completed_at=stale)}}
    monkeypatch.setattr(test_run_reports, "load_project_capability_ledger", lambda: ledger)

    status = homework._load_ocr97_status()

    assert status["fresh"] is False
    assert status["quality_confident"] is False


def test_load_ocr97_status_score_below_threshold(monkeypatch):
    from Sky.services import test_run_reports

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ledger = {"projects": {"ocr97": _fake_ocr_ledger(score=80.0, completed_at=now)}}
    monkeypatch.setattr(test_run_reports, "load_project_capability_ledger", lambda: ledger)

    status = homework._load_ocr97_status()

    assert status["fresh"] is True
    assert status["quality_confident"] is False


def test_load_ocr97_status_unavailable_when_sky_import_fails(monkeypatch):
    fake = Path("C:/does-not-exist-for-apollo-tests")
    monkeypatch.setattr(homework, "ENGINEERING_ROOT", fake)
    for key in list(homework.sys.modules):
        if key.startswith("Sky"):
            monkeypatch.delitem(homework.sys.modules, key, raising=False)
    monkeypatch.setattr(homework.sys, "path", [str(fake)], raising=True)

    status = homework._load_ocr97_status()

    assert status["status"] == "unavailable"
    assert status["fresh"] is False
    assert status["quality_confident"] is False
    assert "error" in status


def test_blocker_hierarchy_order_all_blockers():
    context = {
        "phase_outputs": {
            "news_catalyst_gather": {"best_event_impact": {"top_event": {"source_quality": 90}}},
            "technical_confirmation_trade_cycle": {
                "deep_trade_adjudication": {"status": "ok"},
                "trade_blockers": ["manual_blocker"],
                "evidence_quality": {"news_evidence": "weak", "news_source_quality": "c_tier_only_yahoo_rss"},
            },
            "graph_rag_enrichment": {"hipporag_label": "supporting_context_only"},
        },
        "ocr97_status": {"fresh": False, "quality_confident": False},
    }

    blockers = homework._blocker_hierarchy(context)

    assert blockers == [
        "ocr_document_quality",
        "hipporag_usefulness",
        "price_volume_confirmation",
        "trade_risk_gate",
    ]


def test_blocker_hierarchy_prefers_ocr_pass_and_hipporag_weak():
    context = {
        "phase_outputs": {
            "news_catalyst_gather": {"best_event_impact": {"top_event": {"source_quality": 90}}},
            "technical_confirmation_trade_cycle": {
                "deep_trade_adjudication": {"status": "approved"},
                "trade_blockers": ["trade_risk_gate"],
                "evidence_quality": {"news_evidence": "good", "news_source_quality": "tier_a_mixed"},
            },
            "graph_rag_enrichment": {"hipporag_label": "supporting_context_only"},
        },
        "ocr97_status": {"fresh": True, "quality_confident": True},
    }

    blockers = homework._blocker_hierarchy(context)

    assert "ocr_document_quality" not in blockers
    assert blockers[0] == "hipporag_usefulness"


def _proposal(
    rr: float,
    graph: float,
    pressed: bool,
    score: float = 82,
    event_score: float = 0,
    volume_ratio: float = 0,
):
    return {
        "ticker": "TEST",
        "risk_reward_estimate": rr,
        "score": score,
        "aggressive_reward_profile": {"pressed_candidate": pressed},
        "graph_risk_appetite": {"score": graph},
        "event_impact": {"impact_score": event_score},
        "volume_confirmation": {"volume_ratio": volume_ratio},
    }


def test_phase_high_risk_screen_threshold_boundaries():
    study = {"top_proposals": []}
    cases = [
        (_proposal(2.0, 65, True), "pressed"),
        (_proposal(1.99, 65, True), "aggressive"),
        (_proposal(2.0, 64, True), "entry"),
        (_proposal(2.0, 65, False), "aggressive"),
        (_proposal(1.5, 20, False, event_score=65), "aggressive"),
        (_proposal(1.49, 64, False, score=71), "entry"),
        (_proposal(1.5, 20, False, score=69), "none"),
    ]

    for row, expected in cases:
        study["top_proposals"] = [row]
        result = homework._phase_high_risk_screen({"phase_outputs": {"event_dislocation_study": study}})
        has_pressed = bool(result["pressed_candidates"])
        has_aggressive = bool(result["aggressive_candidates"])
        has_entry = bool(result["wait_for_entry_reset"])

        if expected == "pressed":
            assert has_pressed
            assert not has_aggressive and not has_entry
        elif expected == "aggressive":
            assert has_aggressive
            assert not has_pressed and not has_entry
        elif expected == "entry":
            assert has_entry
            assert not has_pressed and not has_aggressive
        else:
            assert not has_pressed and not has_aggressive and not has_entry


def test_wait_for_gb10_ready_accepts_target_or_idle(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    monkeypatch.setattr(gb10_gate.requests, "get", lambda *_, **__: FakeResponse({"models": [{"name": "qwen2.5:72b", "size_vram": 1200}]}))
    result = gb10_gate.wait_for_gb10_ready(
        base_url="http://127.0.0.1:11435",
        target_model="qwen2.5:72b",
        max_wait_sec=30,
    )

    assert result["ready"] is True
    assert result["reason"] in {"target_model_loaded", "gb10_idle"}


def test_wait_for_gb10_ready_times_out_when_loading(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def always_loading(*_, **__):
        return FakeResponse({"models": [{"name": "qwen2.5:72b", "size_vram": 0}]})

    monkeypatch.setattr(gb10_gate.requests, "get", always_loading)
    result = gb10_gate.wait_for_gb10_ready(
        base_url="http://127.0.0.1:11435",
        target_model="qwen2.5:72b",
        max_wait_sec=5,
        poll_interval_sec=1,
    )

    assert result["ready"] is False
    assert result["reason"] == "max_wait_exceeded"


def test_wait_for_gb10_ready_marks_unreachable(monkeypatch):
    def fail(*_, **__):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(gb10_gate.requests, "get", fail)
    result = gb10_gate.wait_for_gb10_ready(
        base_url="http://127.0.0.1:9999",
        target_model="qwen2.5:72b",
        max_wait_sec=5,
        poll_interval_sec=1,
    )

    assert result["ready"] is False
    assert "unreachable" in result["reason"]
