from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from Apollo import market_data, market_study
from Apollo.focus_universe import evaluate_decision_gate


pytestmark = pytest.mark.unit


def test_normalize_market_snapshot_good_data():
    now = datetime(2026, 4, 27, 14, 0, tzinfo=timezone.utc)
    row = market_data.normalize_market_snapshot(
        {
            "ticker": "nvda",
            "price": "110",
            "prior_close": "100",
            "volume": "2000000",
            "avg_volume": "1000000",
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "source": "unit",
        },
        now=now,
    )
    assert row["ok"] is True
    assert row["ticker"] == "NVDA"
    assert row["gap_pct"] == 10.0
    assert row["relative_volume"] == 2.0
    assert row["freshness"] == "fresh"
    assert row["confidence"] >= 90


def test_normalize_market_snapshot_stale_missing_values():
    now = datetime(2026, 4, 27, 14, 0, tzinfo=timezone.utc)
    row = market_data.normalize_market_snapshot(
        {
            "ticker": "AMD",
            "timestamp": (now - timedelta(days=3)).isoformat(),
            "source": "unit",
        },
        now=now,
    )
    assert row["ok"] is False
    assert row["freshness"] == "stale"
    assert row["gap_pct"] is None
    assert row["confidence"] < 40


def test_market_catalysts_dedup_and_source_health(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_MARKET_BROWSER_STATUS_FILE", str(tmp_path / "browser.json"))
    monkeypatch.setattr(
        market_data,
        "_google_news",
        lambda ticker, max_results=5: [
            {"kind": "news", "ticker": ticker, "title": "NVDA beats", "url": "https://news/a"},
            {"kind": "news", "ticker": ticker, "title": "NVDA beats duplicate", "url": "https://news/a"},
        ],
    )
    monkeypatch.setattr(
        market_data,
        "_sec_recent_filings",
        lambda ticker, max_results=5: [{"kind": "sec_filing", "ticker": ticker, "title": "8-K", "url": "https://sec/8k"}],
    )
    result = market_data.market_catalysts({"ticker": "NVDA"})
    assert result["ok"] is True
    assert len(result["catalysts"]["NVDA"]) == 2
    assert result["source_health"]["news"]["ok"] is True
    assert result["source_health"]["sec"]["ok"] is True


def test_trade_proposal_strong_data_yields_direction(monkeypatch, tmp_path):
    monkeypatch.setattr(market_study, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(
        market_data,
        "market_snapshot",
        lambda payload: {
            "ok": True,
            "snapshots": [
                market_data.normalize_market_snapshot(
                    {
                        "ticker": "NVDA",
                        "price": 110,
                        "prior_close": 100,
                        "volume": 2_000_000,
                        "avg_volume": 1_000_000,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "source": "unit",
                    }
                )
            ],
            "source_health": {"browser": {"blocked": False}},
        },
    )
    monkeypatch.setattr(
        market_data,
        "market_catalysts",
        lambda payload: {
            "ok": True,
            "catalysts": {"NVDA": [{"title": "NVDA beats and raises guidance", "url": "https://news/a"} for _ in range(5)]},
            "source_health": {"browser": {"blocked": False}},
        },
    )
    study = market_study.build_market_study({"ticker": "NVDA", "write_artifacts": True})
    proposal = study["best_proposal"]
    assert study["ok"] is True
    assert proposal["direction_bias"] == "long_bias"
    assert proposal["human_approval_required"] is True
    assert proposal["live_trade_execution"] is False
    assert "report_path" in study["artifacts"]


def test_trade_proposal_negative_dislocation_yields_short_bias(monkeypatch, tmp_path):
    monkeypatch.setattr(market_study, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(
        market_data,
        "market_snapshot",
        lambda payload: {
            "ok": True,
            "snapshots": [
                market_data.normalize_market_snapshot(
                    {
                        "ticker": "NVDA",
                        "price": 92,
                        "prior_close": 100,
                        "volume": 3_000_000,
                        "avg_volume": 1_000_000,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "source": "unit",
                    }
                )
            ],
            "source_health": {"browser": {"blocked": False}},
        },
    )
    monkeypatch.setattr(
        market_data,
        "market_catalysts",
        lambda payload: {
            "ok": True,
            "catalysts": {"NVDA": [{"title": "NVDA misses guidance and analyst downgrades shares", "url": "https://news/a"} for _ in range(4)]},
            "source_health": {"browser": {"blocked": False}},
        },
    )
    study = market_study.build_market_study({"ticker": "NVDA", "write_artifacts": False})
    proposal = study["best_proposal"]
    assert proposal["direction_bias"] == "short_bias"
    assert proposal["setup_type"] == "event_dislocation"
    assert proposal["event_dislocation"]["is_event_dislocation"] is True
    assert proposal["live_trade_execution"] is False


def test_trade_proposal_weak_data_yields_no_trade(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "market_snapshot",
        lambda payload: {"ok": False, "snapshots": [{"ticker": "AMD", "ok": False, "confidence": 10}], "source_health": {"browser": {"blocked": False}}},
    )
    monkeypatch.setattr(
        market_data,
        "market_catalysts",
        lambda payload: {"ok": False, "catalysts": {"AMD": []}, "source_health": {"browser": {"blocked": False}}},
    )
    proposal = market_study.trade_proposal({"ticker": "AMD", "write_artifacts": False})["proposal"]
    assert proposal["direction_bias"] == "no_trade"
    assert proposal["no_trade_reason"]


def test_browser_blocked_does_not_fail_study(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "market_snapshot",
        lambda payload: {"ok": True, "snapshots": [{"ticker": "AVGO", "ok": True, "confidence": 80}], "source_health": {"browser": {"blocked": True}}},
    )
    monkeypatch.setattr(
        market_data,
        "market_catalysts",
        lambda payload: {"ok": True, "catalysts": {"AVGO": [{"title": "AVGO news", "url": "x"}]}, "source_health": {"browser": {"blocked": True}}},
    )
    study = market_study.build_market_study({"ticker": "AVGO", "write_artifacts": False})
    assert study["ok"] is True
    assert study["blocked_session"] is True
    assert study["notify_reason"] == "browser_session_blocked"


def test_decision_gate_uses_market_context_and_ocr_can_fail():
    result = evaluate_decision_gate(
        quality={"overall_score": 82, "gate_pass": True, "confidence_band": {"label": "medium"}},
        stage_details={
            "gather": {"accepted_sources": 4},
            "ocr": {"documents": 0},
            "hipporag": {"processed": 10, "triples": 6},
            "market_study": {
                "avg_snapshot_confidence": 90,
                "catalyst_count": 6,
                "has_trade_proposal": True,
                "blocked_session": False,
            },
        },
        focus_universe={
            "selected_theme": {"theme_score": 80, "label": "AI Compute"},
            "selected_tickers": [{"ticker": ticker} for ticker in ["NVDA", "AMD", "AVGO", "TSM", "ANET"]],
        },
    )
    assert result["computed"] is True
    assert result["market_score"] > 80
    assert result["decision_mode"] in {"next_market_open_plus_60m", "next_market_open_plus_15m", "defer_and_continue_research"}
    assert "market_browser_session_blocked" not in result["reasons"]


def test_market_scheduler_wrapper_exists_and_targets_0730():
    wrapper = market_study._APOLLO_ROOT / "watchdog" / "run_apollo_market_study_0730.ps1"
    text = wrapper.read_text(encoding="utf-8")
    assert "Apollo.swing_study" in text
    assert "--notify" in text
    assert "alerts/external" in text
    assert "apollo_market_study.lock" in text
