from __future__ import annotations

from pathlib import Path

from Apollo import daily_report, pretrade_gate


def _ready_candidate(**overrides):
    row = {
        "ticker": "NVDA",
        "direction_bias": "long_bias",
        "setup_type": "base_breakout",
        "score": 88,
        "confidence": 88,
        "confidence_label": "high",
        "entry_zone": "100.00-102.00",
        "stop_zone": "95.00",
        "target_zone": "112.00",
        "risk_reward_estimate": 1.8,
        "status": "new",
        "no_trade_reason": "",
    }
    row.update(overrides)
    return row


def _patch_roots(monkeypatch, tmp_path: Path) -> None:
    gate_root = tmp_path / "pretrade_gate"
    report_root = tmp_path / "daily_reports"
    monkeypatch.setattr(pretrade_gate, "RUN_ROOT", gate_root)
    monkeypatch.setattr(pretrade_gate, "LATEST_STATUS_PATH", gate_root / "latest_status.json")
    monkeypatch.setattr(daily_report, "REPORT_ROOT", report_root)
    monkeypatch.setattr(daily_report, "record_gate_result", lambda result: {"ok": True, "stubbed": True, "result": result})
    monkeypatch.setattr(
        daily_report,
        "_load_sim_account",
        lambda: {
            "ok": True,
            "account": {
                "starting_balance": 100,
                "cash_balance": 100,
                "invested_amount": 0,
                "market_value": 100,
                "realized_return": 0,
                "realized_return_pct": 0,
                "unrealized_return": 0,
                "unrealized_return_pct": 0,
            },
            "next_cycle_plan": {"status": "waiting", "planned_investment": 0, "planned_cash_reserve": 100},
        },
    )


def _quote(price=101.0):
    return {
        "ok": True,
        "ticker": "NVDA",
        "price": price,
        "source": "fake_quote",
        "freshness": "fresh",
        "confidence": 100,
    }


def _news(title="Analyst upgrades NVDA on strong demand"):
    return [{"title": title, "link": "https://example.test/news", "published": "today", "sentiment": pretrade_gate.headline_sentiment(title)}]


def test_pretrade_gate_no_active_candidate_returns_no_action_due(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [], "best_proposal": {}})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "planner", "force": True},
        quote_provider=lambda ticker: {"ok": False, "error": "should_not_need_quote"},
        news_provider=lambda ticker, limit: [],
    )

    assert result["decision"] == "no_action_due"
    assert result["blocker"] == "no_active_candidate"
    assert Path(result["json_path"]).exists()


def test_pretrade_gate_blocks_negative_headline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [_ready_candidate()]})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "manual", "force": True},
        quote_provider=lambda ticker: _quote(),
        news_provider=lambda ticker, limit: _news("NVDA faces investigation after warning on demand"),
    )

    assert result["decision"] == "blocked_by_news"
    assert result["negative_headlines"]


def test_pretrade_gate_blocks_price_below_stop(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [_ready_candidate()]})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "manual", "force": True},
        quote_provider=lambda ticker: _quote(94.5),
        news_provider=lambda ticker, limit: _news(),
    )

    assert result["decision"] == "blocked_by_price"
    assert "price_at_or_below_stop" in result["blocker"]


def test_pretrade_gate_clear_for_paper_sim(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [_ready_candidate()]})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "manual", "force": True},
        quote_provider=lambda ticker: _quote(101.0),
        news_provider=lambda ticker, limit: _news(),
    )

    assert result["decision"] == "clear_for_paper_sim"
    assert result["heavy_daytime_hipporag_refresh"] is False


def test_pretrade_gate_short_dislocation_allows_negative_headline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    candidate = _ready_candidate(
        direction_bias="short_bias",
        setup_type="event_dislocation",
        entry_zone="99.00-101.00",
        stop_zone="106.00",
        target_zone="88.00",
    )
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [candidate]})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "manual", "force": True},
        quote_provider=lambda ticker: _quote(100.0),
        news_provider=lambda ticker, limit: _news("NVDA misses guidance and analyst downgrades shares"),
    )

    assert result["decision"] == "clear_for_paper_sim"
    assert result["candidate"]["direction_bias"] == "short_bias"
    assert result["paper_only"] is True


def test_pretrade_gate_short_dislocation_blocks_positive_headline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    candidate = _ready_candidate(
        direction_bias="short_bias",
        setup_type="event_dislocation",
        entry_zone="99.00-101.00",
        stop_zone="106.00",
        target_zone="88.00",
    )
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [candidate]})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "manual", "force": True},
        quote_provider=lambda ticker: _quote(100.0),
        news_provider=lambda ticker, limit: _news("Analyst upgrades NVDA after strong demand"),
    )

    assert result["decision"] == "blocked_by_news"
    assert "upgrades" in result["blocker"]


def test_pretrade_gate_missing_news_needs_human_review(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [_ready_candidate()]})

    result = pretrade_gate.run_pretrade_gate(
        {"trigger": "manual", "force": True},
        quote_provider=lambda ticker: _quote(),
        news_provider=lambda ticker, limit: [],
    )

    assert result["decision"] == "needs_human_review"
    assert result["blocker"] == "no_fresh_news_coverage"


def test_pretrade_gate_reuses_recent_gate_until_stale_then_refreshes(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(pretrade_gate.swing_study, "latest_swing_study", lambda: {"ok": True, "proposals": [_ready_candidate()]})
    calls = {"quote": 0}

    def quote_provider(ticker):
        calls["quote"] += 1
        return _quote()

    first = pretrade_gate.run_pretrade_gate(
        {"trigger": "planner", "force": True},
        quote_provider=quote_provider,
        news_provider=lambda ticker, limit: _news(),
    )
    second = pretrade_gate.run_pretrade_gate(
        {"trigger": "planner", "force": False, "stale_after_minutes": 20},
        quote_provider=quote_provider,
        news_provider=lambda ticker, limit: _news(),
    )

    assert first["decision"] == "clear_for_paper_sim"
    assert second["reused_recent_gate"] is True
    assert calls["quote"] == 1

    stale = dict(first)
    stale["checked_at"] = "2000-01-01T12:00:00Z"
    pretrade_gate._write_json(pretrade_gate.LATEST_STATUS_PATH, stale)
    third = pretrade_gate.run_pretrade_gate(
        {"trigger": "planner", "force": False, "stale_after_minutes": 1},
        quote_provider=quote_provider,
        news_provider=lambda ticker, limit: _news(),
    )
    assert third.get("reused_recent_gate") is not True
    assert calls["quote"] == 2
