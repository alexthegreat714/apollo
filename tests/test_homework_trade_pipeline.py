from __future__ import annotations


def _proposal(
    ticker: str,
    *,
    status: str = "new",
    rr: float = 1.4,
    direction: str = "long_bias",
    no_trade_reason: str = "",
    score: float = 80.0,
    confidence_label: str = "medium",
):
    return {
        "ticker": ticker,
        "direction_bias": direction,
        "no_trade_reason": no_trade_reason,
        "status": status,
        "setup_type": "pullback_to_trend",
        "score": score,
        "confidence_label": confidence_label,
        "entry_zone": "100.00-102.00",
        "stop_zone": "96.00",
        "target_zone": "110.00",
        "risk_reward_estimate": rr,
        "provider_confidence": 100.0,
        "catalyst_quality_score": 80.0,
        "catalyst_summary": [f"{ticker} catalyst"],
        "source_links": ["https://example.test/news"],
        "market_regime": {"label": "risk_on"},
        "outcome_status": {"status": "still_valid"},
    }


def _strategy_context(*, hits: int = 3):
    return {
        "ok": True,
        "hits": [{"text": f"rule {idx}", "meta": {"title": f"Rule {idx}"}} for idx in range(hits)],
        "rubric": [f"Rule {idx}: risk-first swing rule" for idx in range(hits)],
    }


def test_homework_selects_actionable_candidate_over_expired_high_score(monkeypatch, tmp_path):
    from Apollo import homework_trade_pipeline as hw

    expired = _proposal("OLD", status="expired", rr=0.6, score=95.0)
    actionable = _proposal("NEW", status="new", rr=1.7, score=82.0)

    monkeypatch.setattr(hw, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(hw, "_corpus_equity_check", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(hw.swing_study, "build_swing_study", lambda payload: {"ok": True, "proposals": [expired, actionable]})
    monkeypatch.setattr(hw.swing_strategy_kb, "build_strategy_context", lambda candidate, top_k=5: _strategy_context())

    result = hw.build_homework_trade_plan({"write_artifacts": True, "tickers": "OLD,NEW"})
    plan = result["trade_plan"]

    assert plan["ticker"] == "NEW"
    assert plan["decision"] == "research_candidate"
    assert plan["quality_gates"]["passed"] is True
    assert result["learning_effect"]["actionable_candidate_count"] == 1

    report = (tmp_path / "latest_homework_trade.json").read_text(encoding="utf-8")
    assert "learning_effect" in report
    report_path = result["artifacts"]["report_path"]
    assert "## Homework Quality Gates" in open(report_path, encoding="utf-8").read()


def test_homework_blocks_candidate_with_low_rr_or_expired_status(monkeypatch, tmp_path):
    from Apollo import homework_trade_pipeline as hw

    weak = _proposal("WEAK", status="expired", rr=0.56, score=90.0)

    monkeypatch.setattr(hw, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(hw, "_corpus_equity_check", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(hw.swing_study, "build_swing_study", lambda payload: {"ok": True, "proposals": [weak]})
    monkeypatch.setattr(hw.swing_strategy_kb, "build_strategy_context", lambda candidate, top_k=5: _strategy_context())

    result = hw.build_homework_trade_plan({"write_artifacts": True, "tickers": "WEAK"})
    plan = result["trade_plan"]

    assert plan["decision"] == "no_trade"
    assert "rr_below_minimum" in plan["no_trade_reason"]
    assert "stale_or_expired:expired" in plan["no_trade_reason"]
    assert plan["quality"]["label"] == "blocked"
    assert result["learning_effect"]["verdict"] == "blocked_all_candidates"

    report_text = open(result["artifacts"]["report_path"], encoding="utf-8").read()
    assert "## Learning Effect" in report_text
    assert "blocked_all_candidates" in report_text
    assert "quality=blocked" in report_text


def test_homework_blocks_when_strategy_context_is_too_thin(monkeypatch, tmp_path):
    from Apollo import homework_trade_pipeline as hw

    candidate = _proposal("THIN", status="new", rr=1.8, score=86.0)

    monkeypatch.setattr(hw, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(hw, "_corpus_equity_check", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(hw.swing_study, "build_swing_study", lambda payload: {"ok": True, "proposals": [candidate]})
    monkeypatch.setattr(hw.swing_strategy_kb, "build_strategy_context", lambda candidate, top_k=5: _strategy_context(hits=1))

    result = hw.build_homework_trade_plan({"write_artifacts": False, "tickers": "THIN"})

    assert result["trade_plan"]["decision"] == "no_trade"
    assert "strategy_context_below_minimum" in result["trade_plan"]["no_trade_reason"]


def test_homework_blocks_low_confidence_label(monkeypatch, tmp_path):
    from Apollo import homework_trade_pipeline as hw

    candidate = _proposal("LOW", status="new", rr=1.8, score=72.0, confidence_label="low")

    monkeypatch.setattr(hw, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(hw, "_corpus_equity_check", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(hw.swing_study, "build_swing_study", lambda payload: {"ok": True, "proposals": [candidate]})
    monkeypatch.setattr(hw.swing_strategy_kb, "build_strategy_context", lambda candidate, top_k=5: _strategy_context())

    result = hw.build_homework_trade_plan({"write_artifacts": False, "tickers": "LOW"})

    assert result["trade_plan"]["decision"] == "no_trade"
    assert "confidence_label_low" in result["trade_plan"]["no_trade_reason"]
