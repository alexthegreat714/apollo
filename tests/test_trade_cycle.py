from __future__ import annotations

import sys
import types

import pytest

from Apollo import trade_cycle


pytestmark = pytest.mark.unit


def _proposal(ticker: str, *, score: float = 82, rr: float = 1.2, direction: str = "long_bias", **extra):
    row = {
        "ticker": ticker,
        "entry_zone": "100.00-101.00",
        "stop_zone": "95.00",
        "target_zone": "110.00",
        "setup_type": "pullback_to_trend",
        "score": score,
        "news_blended_score": score,
        "risk_reward_estimate": rr,
        "direction_bias": direction,
        "confidence_label": "high",
        "price_action_confirmation": {"ok": True, "reason": "confirmed_recovery"},
        "volume_confirmation": {"ok": True, "reason": "confirmed_breakout_volume", "volume_ratio": 1.8},
    }
    row.update(extra)
    return row


def _news_for(proposals):
    return {
        "news_evidence": "good",
        "news_by_ticker": {
            row["ticker"]: {
                "headline_count": 3,
                "weighted_sentiment": 0.2,
                "sentiment_backend": "local_model",
                "sentiment_label": "positive",
                "sentiment_confidence": 0.9,
                "standard_sentiment_eligible": True,
                "eligibility_path": "trusted_ab",
                "independent_domain_count": 2,
                "unique_story_count": 2,
                "items": [],
            }
            for row in proposals
        },
        "blended_proposals": proposals,
    }


def _market_for(proposals):
    return {"market_regime": {"label": "risk_on"}, "proposals": proposals}


def _patch_local_risk_io(monkeypatch):
    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    monkeypatch.setattr(
        trade_cycle,
        "_liquidity_ok",
        lambda ticker: {"ticker": ticker, "avg_volume_30d": 1_000_000, "min_required": 500_000, "ok": True},
    )
    monkeypatch.setattr(
        trade_cycle,
        "_entry_valid",
        lambda ticker, entry_low, entry_high: {"ok": True, "live": 100.5, "entry_low": entry_low, "entry_high": entry_high},
    )


def test_market_stage_enables_event_impact(monkeypatch, tmp_path):
    from Apollo import swing_study

    captured = {}

    def fake_build_swing_study(payload):
        captured.update(payload)
        return {
            "ok": True,
            "market_regime": {"label": "risk_on"},
            "proposals": [_proposal("BAC")],
            "event_impact": {"summaries": [{"ticker": "BAC"}], "best_event_impact": {"ticker": "BAC"}},
            "artifacts": {"event_path": "event.json", "analog_report_path": "analog.md"},
        }

    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    monkeypatch.setattr(swing_study, "build_swing_study", fake_build_swing_study)

    result = trade_cycle._stage_market_research(tmp_path)

    assert captured["include_event_impact"] is True
    assert captured["event_max_results"] == 10
    assert "BAC" in captured["tickers"]
    assert result["event_impact"]["event_impact_enabled"] is True
    assert result["event_impact"]["event_impact_path"] == "event.json"


def test_risk_checks_evaluates_beyond_first_five(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposals = [
        _proposal(f"LOW{i}", score=92 - i, rr=0.40)
        for i in range(5)
    ] + [_proposal("BAC", score=72, rr=2.2)]

    result = trade_cycle._stage_risk_checks(tmp_path, _news_for(proposals), _market_for(proposals))

    assert len(result["checks"]) == 6
    assert result["top_candidate"]["ticker"] == "BAC"
    assert result["ok"] is True


def test_probe_candidate_uses_reduced_sizing_and_is_not_trade_ready(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposals = [_proposal("TSM", score=88, rr=0.65)]

    result = trade_cycle._stage_risk_checks(tmp_path, _news_for(proposals), _market_for(proposals))

    assert result["ok"] is False
    assert result["probe_ok"] is True
    probe = result["top_probe_candidate"]
    assert probe["ticker"] == "TSM"
    assert probe["risk_tier"] == "probe"
    assert probe["probe_ready"] is True
    assert probe["all_checks_pass"] is False
    assert probe["position_sizing"]["risk_pct"] == trade_cycle.PROBE_RISK_PCT
    assert probe["position_sizing"]["max_notional_pct"] == trade_cycle.PROBE_MAX_NOTIONAL_PCT
    assert probe["live_trade_execution"] is False


def test_aggressive_probe_is_paper_only_and_not_trade_ready(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal(
        "DDOG",
        score=78,
        rr=1.8,
        event_impact={"impact_score": 70},
    )
    news = _news_for([proposal])
    news["news_by_ticker"]["DDOG"]["sentiment_backend"] = "keyword_fallback"
    news["news_by_ticker"]["DDOG"]["standard_sentiment_eligible"] = False

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    assert result["ok"] is False
    assert result["aggressive_probe_ok"] is True
    row = result["top_aggressive_probe_candidate"]
    assert row["risk_tier"] == "aggressive_probe"
    assert row["aggressive_probe_ready"] is True
    assert row["standard_ready"] is False
    assert row["all_checks_pass"] is False
    assert row["paper_only"] is True
    assert row["human_approval_required"] is True
    assert row["live_trade_execution"] is False
    assert row["position_sizing"]["risk_pct"] == trade_cycle.AGGRESSIVE_PROBE_RISK_PCT
    assert row["position_sizing"]["max_notional_pct"] == trade_cycle.AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT


def test_pressed_aggressive_uses_graph_backed_larger_paper_sizing(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal(
        "DDOG",
        score=84,
        rr=2.4,
        event_impact={"impact_score": 78},
        graph_risk_appetite={
            "score": 82,
            "conviction": "high",
            "use_for_aggressive_reward": True,
            "upside_terms": ["guidance", "growth", "demand"],
        },
        aggressive_reward_profile={
            "pressed_candidate": True,
            "extension": {"applied": True, "reason": "graph_backed_asymmetric_target_extension"},
            "paper_only": True,
        },
    )
    news = _news_for([proposal])
    news["news_by_ticker"]["DDOG"]["sentiment_backend"] = "keyword_fallback"
    news["news_by_ticker"]["DDOG"]["standard_sentiment_eligible"] = False

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    row = result["top_aggressive_probe_candidate"]
    assert row["aggressive_probe_ready"] is True
    assert row["pressed_aggressive"] is True
    assert row["position_sizing"]["risk_pct"] == trade_cycle.PRESSED_AGGRESSIVE_RISK_PCT
    assert row["position_sizing"]["max_notional_pct"] == trade_cycle.PRESSED_AGGRESSIVE_MAX_NOTIONAL_PCT
    assert row["graph_risk_appetite"]["score"] == 82
    assert "pressed_graph_backed_asymmetry" in row["aggressive_reason"]
    assert row["standard_ready"] is False
    assert row["live_trade_execution"] is False


def test_aggressive_probe_low_reward_waits_or_blocks(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal("NET", score=78, rr=0.9, event_impact={"impact_score": 40}, volume_confirmation={"ok": True, "volume_ratio": 1.0})
    news = _news_for([proposal])
    news["news_by_ticker"]["NET"]["sentiment_backend"] = "keyword_fallback"
    news["news_by_ticker"]["NET"]["standard_sentiment_eligible"] = False

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    row = result["checks"][0]
    assert row["aggressive_probe_ready"] is False
    assert row["aggressive_next_action"] in {"blocked_low_reward", "blocked_weak_evidence"}


def test_aggressive_probe_invalid_price_blocks(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    monkeypatch.setattr(trade_cycle, "_entry_valid", lambda ticker, low, high: {"ok": False, "reason": "invalid_live_price"})
    proposal = _proposal("COIN", score=80, rr=1.8, event_impact={"impact_score": 75})
    news = _news_for([proposal])
    news["news_by_ticker"]["COIN"]["sentiment_backend"] = "keyword_fallback"
    news["news_by_ticker"]["COIN"]["standard_sentiment_eligible"] = False

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    row = result["checks"][0]
    assert row["aggressive_probe_ready"] is False
    assert row["aggressive_next_action"] == "blocked_invalid_price"
    assert "invalid_live_price" in row["risk_notes"]


def test_aggressive_probe_invalid_geometry_blocks(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal("MDB", score=80, rr=1.8, stop_zone="105.00", event_impact={"impact_score": 75})
    news = _news_for([proposal])
    news["news_by_ticker"]["MDB"]["sentiment_backend"] = "keyword_fallback"
    news["news_by_ticker"]["MDB"]["standard_sentiment_eligible"] = False

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    row = result["checks"][0]
    assert row["aggressive_probe_ready"] is False
    assert row["aggressive_next_action"] == "blocked_invalid_price"
    assert "invalid_long_stop_above_entry" in row["risk_notes"]


def test_aggressive_universe_only_when_enabled(monkeypatch):
    monkeypatch.setattr(trade_cycle, "AGGRESSIVE_UNIVERSE_ENABLED", False)
    off = trade_cycle._event_research_tickers()
    discovery_off = trade_cycle._paper_discovery_tickers()
    monkeypatch.setattr(trade_cycle, "AGGRESSIVE_UNIVERSE_ENABLED", True)
    on = trade_cycle._event_research_tickers()
    discovery_on = trade_cycle._paper_discovery_tickers()

    assert len(on) >= len(off)
    assert bool(set(on) - set(off))
    assert len(discovery_on) >= len(discovery_off)
    assert bool(set(discovery_on) - set(discovery_off))


def test_short_event_dislocation_can_pass_standard_risk(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposals = [
        _proposal(
            "BAD",
            score=86,
            rr=1.4,
            direction="short_bias",
            setup_type="event_dislocation",
            stop_zone="105.00",
            target_zone="90.00",
            event_impact={"impact_score": 82},
        )
    ]

    result = trade_cycle._stage_risk_checks(tmp_path, _news_for(proposals), _market_for(proposals))

    assert result["ok"] is True
    assert result["top_candidate"]["ticker"] == "BAD"
    assert result["top_candidate"]["direction_bias"] == "short_bias"
    assert result["top_candidate"]["position_sizing"]["direction_bias"] == "short_bias"


def test_stale_no_trade_reason_blocks_probe(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposals = [_proposal("OLD", score=91, rr=0.75, no_trade_reason="expired_setup")]

    result = trade_cycle._stage_risk_checks(tmp_path, _news_for(proposals), _market_for(proposals))

    assert result["ok"] is False
    assert result["probe_ok"] is False
    assert result["checks"][0]["risk_tier"] in {"watch", "reject"}
    assert "expired_setup" in result["checks"][0]["risk_notes"]


def test_position_sizing_caps_oversized_notional():
    sizing = trade_cycle._position_sizing(
        1000.0,
        999.0,
        risk_pct=trade_cycle.PROBE_RISK_PCT,
        max_notional_pct=trade_cycle.PROBE_MAX_NOTIONAL_PCT,
    )

    assert sizing is not None
    assert sizing["capped_by_notional"] is True
    assert sizing["shares"] == 0.005
    assert sizing["pct_of_portfolio"] <= 5.0

    smaller = trade_cycle._position_sizing(
        3000.0,
        2990.0,
        risk_pct=trade_cycle.PROBE_RISK_PCT,
        max_notional_pct=trade_cycle.PROBE_MAX_NOTIONAL_PCT,
    )
    assert smaller is not None
    assert smaller["shares"] == 0.0017


def test_pullback_without_recovery_confirmation_cannot_be_standard(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal("NVDA", rr=1.5, price_action_confirmation={"ok": False, "reason": "blocked_price_action_confirmation"})

    result = trade_cycle._stage_risk_checks(tmp_path, _news_for([proposal]), _market_for([proposal]))

    assert result["ok"] is False
    assert result["checks"][0]["standard_ready"] is False
    assert "blocked_price_action_confirmation" in result["checks"][0]["risk_notes"]


def test_breakout_without_volume_confirmation_is_downgraded(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal("AVGO", rr=1.5, setup_type="base_breakout", volume_confirmation={"ok": False, "reason": "blocked_breakout_volume_confirmation"})

    result = trade_cycle._stage_risk_checks(tmp_path, _news_for([proposal]), _market_for([proposal]))

    assert result["ok"] is False
    assert result["checks"][0]["standard_ready"] is False
    assert "blocked_breakout_volume_confirmation" in result["checks"][0]["risk_notes"]


def test_keyword_sentiment_fallback_caps_standard(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal("MSFT", rr=1.5)
    news = _news_for([proposal])
    news["news_by_ticker"]["MSFT"]["sentiment_backend"] = "keyword_fallback"
    news["news_by_ticker"]["MSFT"]["standard_sentiment_eligible"] = False
    news["news_by_ticker"]["MSFT"]["standard_cap_reason"] = "news_provenance_caps_standard"

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    assert result["ok"] is False
    assert result["checks"][0]["sentiment_gate"]["ok"] is False
    assert "news_provenance_caps_standard" in result["checks"][0]["risk_notes"]


def test_keyword_sentiment_consensus_allows_standard(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    proposal = _proposal("MSFT", rr=1.5)
    news = _news_for([proposal])
    news["news_by_ticker"]["MSFT"].update(
        {
            "sentiment_backend": "keyword_fallback",
            "sentiment_label": "positive",
            "sentiment_confidence": 0.81,
            "headline_count": 6,
            "directional_headline_count": 4,
            "sentiment_consensus_ratio": 1.0,
            "weighted_sentiment": 0.52,
            "standard_sentiment_eligible": True,
        }
    )

    result = trade_cycle._stage_risk_checks(tmp_path, news, _market_for([proposal]))

    assert result["ok"] is True
    assert result["top_candidate"]["ticker"] == "MSFT"
    assert result["checks"][0]["sentiment_gate"]["ok"] is True
    assert "sentiment_keyword_fallback_caps_standard" not in result["checks"][0]["risk_notes"]


def test_news_stage_promotes_strong_keyword_consensus(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    monkeypatch.setattr(
        trade_cycle,
        "_fetch_ticker_news",
        lambda ticker, max_items=8: [
            {"title": "MSFT beats estimates", "url": "https://cnbc.com/msft-beats", "origin_domain": "cnbc.com", "source_tier": "C", "published": "2099-07-09T05:53:55Z", "sentiment": 1.0, "sentiment_label": "positive", "sentiment_confidence": 1.0, "sentiment_backend": "keyword_fallback"},
            {"title": "MSFT raises guidance", "url": "https://marketwatch.com/msft-guidance", "origin_domain": "marketwatch.com", "source_tier": "C", "published": "2099-07-09T04:53:55Z", "sentiment": 1.0, "sentiment_label": "positive", "sentiment_confidence": 1.0, "sentiment_backend": "keyword_fallback"},
            {"title": "MSFT wins cloud contract", "url": "https://cnn.com/msft-contract", "origin_domain": "cnn.com", "source_tier": "C", "published": "2099-07-09T03:53:55Z", "sentiment": 1.0, "sentiment_label": "positive", "sentiment_confidence": 1.0, "sentiment_backend": "keyword_fallback"},
            {"title": "MSFT stock breakout continues", "url": "https://investopedia.com/msft-breakout", "origin_domain": "investopedia.com", "source_tier": "C", "published": "2099-07-09T02:53:55Z", "sentiment": 1.0, "sentiment_label": "positive", "sentiment_confidence": 1.0, "sentiment_backend": "keyword_fallback"},
        ],
    )

    result = trade_cycle._stage_news_analysis(tmp_path, _market_for([_proposal("MSFT")]))
    msft = result["news_by_ticker"]["MSFT"]

    assert msft["sentiment_backend"] == "keyword_fallback"
    assert msft["sentiment_label"] == "positive"
    assert msft["sentiment_confidence"] >= trade_cycle.KEYWORD_STANDARD_MIN_CONFIDENCE
    assert msft["standard_sentiment_eligible"] is True
    assert msft["eligibility_path"] == "diverse_c"
    assert msft["independent_domain_count"] == 4
    assert msft["sentiment_consensus_ratio"] >= trade_cycle.KEYWORD_STANDARD_MIN_CONSENSUS_RATIO


def test_news_stage_caps_yahoo_only_consensus(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    rows = [
        {"title": f"MSFT positive catalyst {index}", "url": f"https://finance.yahoo.com/news/{index}", "origin_domain": "finance.yahoo.com", "source_tier": "C", "published": "2099-07-09T05:53:55Z", "sentiment": 1.0, "sentiment_backend": "keyword_fallback", "sentiment_confidence": 1.0}
        for index in range(6)
    ]

    result = trade_cycle._stage_news_analysis(
        tmp_path,
        _market_for([_proposal("MSFT")]),
        news_fetcher=lambda ticker, max_items=8: rows,
    )
    msft = result["news_by_ticker"]["MSFT"]

    assert msft["standard_sentiment_eligible"] is False
    assert msft["standard_cap_reason"] == "aggregator_only_news"
    assert msft["independent_domain_count"] == 0


def test_high_risk_default_holdout_allows_explicit_override(monkeypatch, tmp_path):
    _patch_local_risk_io(monkeypatch)
    monkeypatch.delenv("APOLLO_WATCHLIST", raising=False)
    proposal = _proposal("RTX", rr=1.5)

    blocked = trade_cycle._stage_risk_checks(tmp_path, _news_for([proposal]), _market_for([proposal]))
    assert blocked["ok"] is False
    assert "high_risk_review_default_holdout" in blocked["checks"][0]["risk_notes"]

    monkeypatch.setenv("APOLLO_WATCHLIST", "RTX,NVDA")
    allowed = trade_cycle._stage_risk_checks(tmp_path, _news_for([proposal]), _market_for([proposal]))
    assert allowed["ok"] is True
    assert allowed["top_candidate"]["ticker"] == "RTX"


def test_observation_report_includes_probe_and_event_metadata(tmp_path):
    proposals = [_proposal("TSM", score=88, rr=0.65)]
    risk = {
        "checks": [
            {
                "ticker": "TSM",
                "risk_tier": "probe",
                "probe_ready": True,
                "all_checks_pass": False,
                "risk_notes": ["risk_reward_below_standard:0.65"],
                "next_action": "simulation_probe_only_with_tight_invalidation",
                "position_sizing": {
                    "shares": 10,
                    "risk_tier": "probe",
                    "risk_pct": trade_cycle.PROBE_RISK_PCT,
                    "capped_by_notional": False,
                    "max_notional_dollars": 2500.0,
                    "total_cost": 1000.0,
                    "pct_of_portfolio": 2.0,
                    "max_loss_dollars": 250.0,
                    "risk_per_share": 25.0,
                },
                "blended_score": 88,
                "rr": 0.65,
                "direction_bias": "long_bias",
                "event_impact_score": 80,
            }
        ],
        "standard_candidates": [],
        "probe_candidates": [
            {"ticker": "TSM", "risk_tier": "probe", "blended_score": 88, "rr": 0.65, "direction_bias": "long_bias", "event_impact_score": 80, "risk_notes": []}
        ],
        "watch_candidates": [],
        "rejected_candidates": [],
    }

    report = trade_cycle._build_observation_report(
        tmp_path,
        {"confidence_band": "medium", "new_triples": 10, "gathered_sources": 3},
        {"market_regime": {"label": "risk_on"}, "proposals": proposals, "event_impact": {"event_impact_enabled": True, "event_impact_path": "event.json"}},
        {"news_evidence": "good", "source_quality": "c_tier_only_yahoo_rss", "evidence_confidence": "low", "news_by_ticker": {"TSM": {"weighted_sentiment": 0.2, "items": []}}, "blended_proposals": proposals},
        risk,
        {"status": "open"},
        proposals[0],
    )

    assert "status_label: `probe_ready`" in report
    assert "event_impact_path: `event.json`" in report
    assert "## Risk Tier Breakdown" in report
    assert "### Probe Candidates" in report


def test_observation_report_includes_aggressive_probe_section(tmp_path):
    proposal = _proposal("DDOG", score=78, rr=1.8)
    risk = {
        "checks": [
            {
                "ticker": "DDOG",
                "risk_tier": "aggressive_probe",
                "probe_ready": False,
                "aggressive_probe_ready": True,
                "all_checks_pass": False,
                "risk_notes": ["sentiment_keyword_fallback_caps_standard"],
                "next_action": "paper_aggressive_ready",
                "aggressive_next_action": "paper_aggressive_ready",
                "aggressive_reason": "rr_asymmetric; event_catalyst",
                "position_sizing": {
                    "shares": 10,
                    "risk_tier": "aggressive_probe",
                    "risk_pct": trade_cycle.AGGRESSIVE_PROBE_RISK_PCT,
                    "capped_by_notional": False,
                    "max_notional_dollars": 2500.0,
                    "total_cost": 1000.0,
                    "pct_of_portfolio": 2.0,
                    "max_loss_dollars": 250.0,
                    "risk_per_share": 25.0,
                },
                "blended_score": 78,
                "rr": 1.8,
                "direction_bias": "long_bias",
                "event_impact_score": 70,
                "volume_confirmation": {"volume_ratio": 1.5},
                "sentiment_backend": "keyword_fallback",
                "liquidity": {"ok": True},
                "price_valid": True,
                "paper_risk_pct": trade_cycle.AGGRESSIVE_PROBE_RISK_PCT,
                "max_notional_pct": trade_cycle.AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT,
            }
        ],
        "standard_candidates": [],
        "aggressive_probe_candidates": [
            {"ticker": "DDOG", "risk_tier": "aggressive_probe", "blended_score": 78, "rr": 1.8, "direction_bias": "long_bias", "event_impact_score": 70, "risk_notes": [], "aggressive_next_action": "paper_aggressive_ready"}
        ],
        "probe_candidates": [],
        "wait_for_entry_reset_candidates": [],
        "watch_candidates": [],
        "rejected_candidates": [],
    }

    report = trade_cycle._build_observation_report(
        tmp_path,
        {"confidence_band": "medium", "new_triples": 10, "gathered_sources": 3},
        {"market_regime": {"label": "risk_on"}, "proposals": [proposal]},
        {"news_evidence": "good", "news_by_ticker": {"DDOG": {"weighted_sentiment": 0.2, "items": []}}, "blended_proposals": [proposal]},
        risk,
        {"status": "open"},
        proposal,
    )

    assert "status_label: `aggressive_probe_ready`" in report
    assert "## Top Aggressive Paper Probe - SIMULATION ONLY" in report
    assert "### Aggressive Probe Candidates" in report
    assert "aggressive_reason: `rr_asymmetric; event_catalyst`" in report


def test_observation_report_includes_model_policy_and_storage(tmp_path):
    proposals = [_proposal("NVDA", score=88, rr=1.5)]
    risk = {
        "checks": [
            {
                "ticker": "NVDA",
                "risk_tier": "standard",
                "probe_ready": False,
                "all_checks_pass": True,
                "risk_notes": ["passes_all_standard_gates"],
                "next_action": "standard_simulation_candidate",
                "position_sizing": {
                    "shares": 10,
                    "risk_tier": "standard",
                    "risk_pct": trade_cycle.RISK_PCT,
                    "capped_by_notional": False,
                    "max_notional_dollars": 7500.0,
                    "total_cost": 1000.0,
                    "pct_of_portfolio": 2.0,
                    "max_loss_dollars": 100.0,
                    "risk_per_share": 10.0,
                },
            }
        ],
        "standard_candidates": [],
        "probe_candidates": [],
        "watch_candidates": [],
        "rejected_candidates": [],
    }
    adjudication = {
        "decision": "approved_standard",
        "rationale": "large model confirmed evidence quality",
        "model_policy": {
            "fast_model": {"base_url": "http://127.0.0.1:11434", "model": "gemma3:12b"},
            "deep_trade_model": {"base_url": "http://127.0.0.1:11435", "model": "qwen2.5:72b", "fallback_used": False},
            "vision_ocr_model": {"base_url": "http://127.0.0.1:11435", "model": "qwen3-vl:32b"},
        },
        "model_storage": {
            "c_drive_downloads_allowed": False,
            "required_storage": "D: drive or GB10-backed storage",
            "blocked_c_drive_paths": {},
            "resolved_paths": {
                "OLLAMA_MODELS": r"D:\ApolloModels\ollama",
                "HF_HOME": r"D:\ApolloModels\huggingface",
                "TRANSFORMERS_CACHE": r"D:\ApolloModels\huggingface\transformers",
                "TORCH_HOME": r"D:\ApolloModels\torch",
            },
        },
    }

    report = trade_cycle._build_observation_report(
        tmp_path,
        {"confidence_band": "medium", "new_triples": 10, "gathered_sources": 3},
        {"market_regime": {"label": "risk_on"}, "proposals": proposals},
        {"news_evidence": "good", "source_quality": "trusted", "news_by_ticker": {"NVDA": {"weighted_sentiment": 0.2, "items": []}}, "blended_proposals": proposals},
        risk,
        {"status": "open"},
        proposals[0],
        adjudication=adjudication,
    )

    assert "model_policy.deep_trade_model: `qwen2.5:72b`" in report
    assert "model_storage.c_drive_downloads_allowed: `False`" in report
    assert "deep_trade_adjudication.decision: `approved_standard`" in report
    assert r"D:\ApolloModels\ollama" in report


def test_deep_adjudication_cannot_upgrade_probe_to_standard():
    risk = {
        "ok": False,
        "probe_ok": True,
        "checks": [
            {"ticker": "AVGO", "risk_tier": "probe", "standard_ready": False, "probe_ready": True, "all_checks_pass": False}
        ],
        "standard_candidates": [],
        "probe_candidates": [{"ticker": "AVGO"}],
        "top_candidate": None,
        "top_probe_candidate": {"ticker": "AVGO"},
    }

    updated = trade_cycle._apply_adjudication_to_risk(risk, {"decision": "approved_standard"})

    assert updated["ok"] is False
    assert updated["top_candidate"] is None
    assert updated["top_probe_candidate"]["ticker"] == "AVGO"


def test_deep_adjudication_cannot_upgrade_aggressive_to_standard():
    risk = {
        "ok": False,
        "aggressive_probe_ok": True,
        "checks": [
            {"ticker": "DDOG", "risk_tier": "aggressive_probe", "standard_ready": False, "probe_ready": False, "aggressive_probe_ready": True, "all_checks_pass": False}
        ],
        "standard_candidates": [],
        "probe_candidates": [],
        "aggressive_probe_candidates": [{"ticker": "DDOG"}],
        "top_candidate": None,
        "top_probe_candidate": None,
        "top_aggressive_probe_candidate": {"ticker": "DDOG"},
    }

    updated = trade_cycle._apply_adjudication_to_risk(risk, {"decision": "approved_standard"})

    assert updated["ok"] is False
    assert updated["top_candidate"] is None
    assert updated["top_aggressive_probe_candidate"]["ticker"] == "DDOG"


def test_deep_adjudication_block_clears_ready_flags():
    risk = {
        "ok": True,
        "probe_ok": True,
        "checks": [
            {"ticker": "NVDA", "risk_tier": "standard", "standard_ready": True, "probe_ready": False, "all_checks_pass": True, "risk_notes": []},
            {"ticker": "AVGO", "risk_tier": "probe", "standard_ready": False, "probe_ready": True, "all_checks_pass": False, "risk_notes": []},
        ],
        "standard_candidates": [{"ticker": "NVDA"}],
        "probe_candidates": [{"ticker": "AVGO"}],
        "top_candidate": {"ticker": "NVDA"},
        "top_probe_candidate": {"ticker": "AVGO"},
    }

    updated = trade_cycle._apply_adjudication_to_risk(risk, {"decision": "blocked"})

    assert updated["ok"] is False
    assert updated["probe_ok"] is False
    assert updated["standard_candidates"] == []
    assert updated["probe_candidates"] == []
    assert all(not row["all_checks_pass"] and not row["probe_ready"] for row in updated["checks"])


def test_invalid_deep_adjudication_reply_preserves_deterministic_candidates():
    risk = {
        "ok": True,
        "probe_ok": True,
        "checks": [
            {"ticker": "NVDA", "risk_tier": "standard", "standard_ready": True, "probe_ready": False, "all_checks_pass": True, "risk_notes": []},
            {"ticker": "AVGO", "risk_tier": "probe", "standard_ready": False, "probe_ready": True, "all_checks_pass": False, "risk_notes": []},
        ],
        "standard_candidates": [{"ticker": "NVDA"}],
        "probe_candidates": [{"ticker": "AVGO"}],
        "top_candidate": {"ticker": "NVDA"},
        "top_probe_candidate": {"ticker": "AVGO"},
    }

    adjudication = trade_cycle._parse_adjudication_reply("not json")
    updated = trade_cycle._apply_adjudication_to_risk(risk, adjudication)

    assert adjudication["status"] == "unavailable"
    assert adjudication["decision"] == "adjudication_unavailable"
    assert adjudication["parse_ok"] is False
    assert updated["ok"] is True
    assert updated["standard_candidates"][0]["ticker"] == "NVDA"
    assert updated["probe_candidates"][0]["ticker"] == "AVGO"
    assert updated["deep_adjudication_unavailable"] is True


def test_deep_adjudication_model_failure_records_unavailable_fallback(monkeypatch, tmp_path):
    risk = {
        "ok": True,
        "probe_ok": False,
        "checks": [{"ticker": "NVDA", "risk_tier": "standard", "standard_ready": True, "all_checks_pass": True}],
        "standard_candidates": [{"ticker": "NVDA", "risk_tier": "standard", "standard_ready": True}],
        "probe_candidates": [],
        "watch_candidates": [],
        "top_candidate": {"ticker": "NVDA"},
    }

    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    monkeypatch.setattr(trade_cycle, "assert_model_storage_allowed", lambda: {"ok": True})
    monkeypatch.setattr(
        trade_cycle,
        "resolve_model_policy",
        lambda probe=True: {
            "deep_trade_model": {"available": True, "model": "qwen2.5:72b", "base_url": "http://127.0.0.1:11435"},
            "fast_model": {"model": "gemma3:12b"},
            "vision_ocr_model": {"model": "qwen3-vl:32b"},
        },
    )

    class FailingAdapter:
        def direct(self, **kwargs):
            raise TimeoutError("unit timeout")

        def sky_queue(self, **kwargs):
            return None

    result = trade_cycle._stage_deep_trade_adjudication(tmp_path, risk, {}, {}, {}, adapter=FailingAdapter())

    assert result["status"] == "unavailable"
    assert result["decision"] == "adjudication_unavailable"
    assert result["fallback_policy"] == "deterministic_simulation_only"
    assert result["deterministic_decision"] == "approved_standard"
    assert result["deterministic_decision_used"] is True


def test_entry_valid_rejects_invalid_live_price(monkeypatch):
    class FakeHist:
        empty = False

        def __getitem__(self, key):
            class FakeClose:
                iloc = [float("nan")]

            return FakeClose()

    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, period):
            return FakeHist()

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))

    result = trade_cycle._entry_valid("NVDA", 100.0, 101.0)

    assert result["ok"] is False
    assert result["reason"] == "invalid_live_price"


def test_trade_cycle_not_ready_without_hipporag_enrichment(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_cycle, "CYCLE_LOG_ROOT", tmp_path)
    monkeypatch.setattr(trade_cycle, "_log", lambda message: None)
    monkeypatch.setattr(
        trade_cycle,
        "_stage_hipporag_research",
        lambda run_dir: {
            "ok": False,
            "completed_at": "2026-05-19T09:02:45Z",
            "enrichment_skipped": True,
            "gathered_sources": 0,
            "new_triples": 0,
        },
    )
    monkeypatch.setattr(
        trade_cycle,
        "_stage_market_research",
        lambda run_dir: {"ok": True, "completed_at": "2026-05-19T09:03:00Z", "proposals": [{"ticker": "CAT"}]},
    )
    monkeypatch.setattr(
        trade_cycle,
        "_stage_news_analysis",
        lambda run_dir, market, hipporag=None: {
            "ok": True,
            "completed_at": "2026-05-19T09:03:30Z",
            "news_evidence": "good",
            "news_by_ticker": {"CAT": {"headline_count": 3}},
        },
    )
    monkeypatch.setattr(
        trade_cycle,
        "_stage_risk_checks",
        lambda run_dir, news, market: {"ok": True, "completed_at": "2026-05-19T09:04:00Z", "top_candidate": {"ticker": "CAT"}},
    )
    monkeypatch.setattr(
        trade_cycle,
        "_stage_deep_trade_adjudication",
        lambda run_dir, risk, news, market, hipporag: {"ok": True, "completed_at": "2026-05-19T09:04:15Z", "decision": "approved_standard", "rationale": "test"},
    )
    monkeypatch.setattr(
        trade_cycle,
        "_stage_simulate",
        lambda run_dir, risk, news, market, hipporag, adjudication=None: {"ok": True, "completed_at": "2026-05-19T09:04:30Z", "ticker": "CAT", "simulation": {"status": "open"}},
    )

    status = trade_cycle.run_cycle()

    assert status["ok"] is True
    assert status["trade_ready"] is False
    assert "hipporag_not_enriched" in status["trade_blockers"]
    assert status["evidence_quality"]["hipporag_enriched"] is False
