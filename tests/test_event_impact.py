from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from Apollo import event_impact, market_data, market_study, swing_data, swing_study


@pytest.fixture(autouse=True)
def _disable_optional_event_model(monkeypatch):
    monkeypatch.setenv("APOLLO_EVENT_MODEL_ENABLED", "0")
    monkeypatch.setattr(swing_study, "_rag_context_for_ticker", lambda ticker, setup_type="": [])
    monkeypatch.setattr(swing_study, "_load_earnings_dates_from_corpus", lambda tickers: {})
    monkeypatch.setattr(swing_study, "_load_fundamentals_from_corpus", lambda tickers: {})


def _candles(count=260, start=100.0, step=0.25, volume=1_000_000):
    rows = []
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = start
    for idx in range(count):
        price += step
        rows.append(
            {
                "date": (base + timedelta(days=idx)).date().isoformat(),
                "open": price - 0.4,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": volume + (idx * 2000),
            }
        )
    return rows


def _patch_event_inputs(monkeypatch, title: str, *, published_at: str = "", snapshot_gap=0.5):
    monkeypatch.setattr(
        market_data,
        "market_snapshot",
        lambda payload: {
            "ok": True,
            "snapshots": [
                {
                    "ok": True,
                    "ticker": "NVDA",
                    "price": 110,
                    "prior_close": 100,
                    "gap_pct": snapshot_gap,
                    "relative_volume": 1.2,
                    "freshness": "fresh",
                    "confidence": 90,
                }
            ],
            "source_health": {"browser": {"blocked": False}},
        },
    )
    monkeypatch.setattr(
        market_data,
        "market_catalysts",
        lambda payload: {
            "ok": True,
            "catalysts": {
                "NVDA": [
                    {
                        "kind": "news",
                        "ticker": "NVDA",
                        "title": title,
                        "url": "https://news/nvda",
                        "published_at": published_at,
                        "source": "google_news_rss",
                    }
                ]
            },
            "source_health": {"news": {"ok": True}, "sec": {"ok": False}, "browser": {"blocked": False}},
        },
    )
    monkeypatch.setattr(
        swing_data,
        "fetch_provider_candles",
        lambda ticker: {"ok": True, "ticker": ticker, "candles": _candles(), "provider_health": {"confidence": 100}},
    )


def test_fresh_positive_event_creates_impact_record(monkeypatch, tmp_path):
    monkeypatch.setattr(event_impact, "RUNS_ROOT", tmp_path)
    monkeypatch.setenv("APOLLO_EVENT_MODEL_ENABLED", "0")
    monkeypatch.setenv("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES", "1")
    _patch_event_inputs(monkeypatch, "NVDA beats earnings and raises guidance", published_at=event_impact._utc_iso())

    result = event_impact.build_event_impact({"ticker": "NVDA", "write_artifacts": True})

    assert result["ok"] is True
    row = result["summaries"][0]
    assert row["ticker"] == "NVDA"
    assert row["directional_bias"] == "positive"
    assert row["event_score_component"] >= 0
    assert result["broker_readiness"]["live_trade_execution"] is False
    assert result["broker_readiness"]["broker_order_created"] is False
    assert "event_path" in result["artifacts"]


def test_stale_duplicate_event_does_not_boost(monkeypatch, tmp_path):
    monkeypatch.setattr(event_impact, "RUNS_ROOT", tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    _patch_event_inputs(monkeypatch, "NVDA recap says prior guidance was strong", published_at=old)

    result = event_impact.build_event_impact({"ticker": "NVDA", "write_artifacts": False})

    row = result["summaries"][0]
    assert row["top_event"]["novelty"]["score"] < 55
    assert row["event_score_component"] == 0.0


def test_negative_event_downgrades_swing_study(monkeypatch, tmp_path):
    monkeypatch.setattr(swing_study, "RUNS_ROOT", tmp_path / "swing")
    monkeypatch.setattr(swing_study, "STATE_PATH", tmp_path / "state.json")
    snapshot = swing_data.build_swing_snapshot_from_candles("NVDA", _candles(step=0.55), spy_candles=_candles(step=0.1), qqq_candles=_candles(step=0.1))
    snapshot["close"] = snapshot["high20"]
    snapshot["rsi14"] = 63
    snapshot["atr14"] = 3.0
    snapshot["volume_trend"]["ratio"] = 1.8
    snapshot["market_regime"] = {"label": "risk_on", "score": 90, "risk_notes": []}
    monkeypatch.setattr(
        swing_data,
        "swing_snapshot",
        lambda payload: {"ok": True, "snapshots": [snapshot], "market_regime": snapshot["market_regime"], "source_health": {}},
    )
    monkeypatch.setattr(
        market_study,
        "build_market_study",
        lambda payload: {"ok": True, "blocked_session": False, "source_health": {}, "proposals": [{"ticker": "NVDA", "catalyst_summary": ["NVDA demand strong"], "source_links": ["x"]}]},
    )
    monkeypatch.setattr(
        event_impact,
        "build_event_impact",
        lambda payload: {
            "ok": True,
            "summaries": [
                {
                    "ticker": "NVDA",
                    "ok": True,
                    "impact_score": 75,
                    "directional_bias": "negative",
                    "event_score_component": -12,
                    "top_event": {"title": "NVDA faces regulatory probe"},
                    "paper_order_candidate": None,
                }
            ],
        },
    )

    with_event = swing_study.build_swing_study({"ticker": "NVDA", "write_artifacts": False, "include_event_impact": True, "state_path": str(tmp_path / "state.json")})
    without_event = swing_study.build_swing_study({"ticker": "NVDA", "write_artifacts": False, "include_event_impact": False, "state_path": str(tmp_path / "state2.json")})

    assert without_event["best_proposal"]["direction_bias"] == "long_bias"
    assert with_event["best_proposal"]["setup_type"] == "event_dislocation"
    assert with_event["best_proposal"]["direction_bias"] == "short_bias"
    assert with_event["best_proposal"]["event_score_component"] == -12
    assert with_event["best_proposal"]["live_trade_execution"] is False


def test_negative_event_can_create_short_paper_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(event_impact, "RUNS_ROOT", tmp_path)
    monkeypatch.setenv("APOLLO_EVENT_MODEL_ENABLED", "0")
    monkeypatch.setenv("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES", "1")
    monkeypatch.setenv("APOLLO_EVENT_DISLOCATION_MIN_IMPACT", "55")
    _patch_event_inputs(monkeypatch, "NVDA misses guidance and faces regulatory probe", published_at=event_impact._utc_iso(), snapshot_gap=-4.0)

    result = event_impact.build_event_impact({"ticker": "NVDA", "write_artifacts": False})

    row = result["summaries"][0]
    candidate = row["paper_order_candidate"]
    assert row["directional_bias"] == "negative"
    assert candidate["setup_type"] == "event_dislocation"
    assert candidate["direction_bias"] == "short_bias"
    assert candidate["live_trade_execution"] is False


def test_event_cache_defaults_are_d_storage(monkeypatch):
    for key in ("APOLLO_EVENT_CACHE_ROOT", "HF_HOME", "TRANSFORMERS_CACHE", "TORCH_HOME"):
        monkeypatch.delenv(key, raising=False)

    health = event_impact.event_impact_health()

    assert health["cache"]["d_storage_only"] is True
    assert health["cache"]["cache_root"].replace("\\", "/").lower().startswith("d:/")


def test_event_impact_admin_routes(monkeypatch):
    from Apollo import app as app_mod

    monkeypatch.setattr(app_mod, "_require_local_token", lambda: None)
    monkeypatch.setattr(app_mod.apollo_event_impact, "build_event_impact", lambda payload: {"ok": True, "payload": payload})
    monkeypatch.setattr(app_mod.apollo_event_impact, "latest_event_impact", lambda: {"ok": True, "summaries": []})
    monkeypatch.setattr(app_mod.apollo_event_impact, "event_impact_history", lambda payload: {"ok": True, "runs": [], "payload": payload})
    monkeypatch.setattr(app_mod.apollo_event_impact, "event_impact_health", lambda: {"ok": True, "broker_readiness": {"live_trade_execution": False}})
    client = app_mod.app.test_client()

    assert client.post("/admin/event_impact/run", json={"ticker": "NVDA"}).get_json()["ok"] is True
    assert client.get("/admin/event_impact/latest").get_json()["ok"] is True
    assert client.get("/admin/event_impact/history").get_json()["ok"] is True
    health = client.get("/admin/event_impact/health").get_json()
    assert health["broker_readiness"]["live_trade_execution"] is False
