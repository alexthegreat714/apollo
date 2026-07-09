import os
import time

import pytest


@pytest.fixture(scope="session")
def apollo_app():
    # Importing Apollo.app builds the Flask app and wires routes.
    from Apollo.app import app as flask_app  # noqa: WPS433 (import inside fixture)

    return flask_app


def test_kb_personality_points_load():
    from Apollo import app as apollo_module  # noqa: WPS433

    points = apollo_module._load_kb_personality_points(max_items=6)
    assert isinstance(points, list)
    assert points, "Expected Apollo KB personality points to load (Apollo/memory/kb/apollo_personality.md)"
    assert all(isinstance(p, str) and p.strip() for p in points)


def test_apollo_chat_preprompt_is_apollo_specific():
    from Apollo import app as apollo_module  # noqa: WPS433

    prompt = apollo_module._apollo_chat_preprompt("personal_finance", "fast", budget_focus=True)
    assert "You are Apollo." in prompt
    assert "not Sky" in prompt
    assert "budget_focus=on" in prompt


def test_mcp_tools_registered():
    from common import mcp  # noqa: WPS433

    os.environ.setdefault("AGENT_NAME", "Apollo")
    os.environ.setdefault("SKY_AGENT_NAME", "Apollo")
    mcp.ensure_loaded()
    tools = set(mcp.list_tools())
    assert "ocr.dual" in tools
    assert "vision.process" in tools


def test_meta_endpoint(apollo_app):
    client = apollo_app.test_client()
    resp = client.get("/meta")
    assert resp.status_code == 200
    js = resp.get_json()
    assert js["agent"] == "Apollo"
    assert "ollama" in js and "url" in js["ollama"]
    assert js["ollama"]["url"].startswith("http://127.0.0.1:")
    assert js["chat_lane"]["model"] == js["ollama"]["model"]
    assert js["deep_lane"]["model"]
    assert "hipporag" in js
    assert js["hipporag"]["retrieval_path"] == "hippo_first_fallback_chroma"
    assert js["hipporag"]["graph_endpoint"] == "/admin/hipporag/graph"
    assert js["ocr_policy"]["primary_route"] == "gb10_auto"
    assert "nightly_pipeline" in js
    assert js["nightly_pipeline"]["run_endpoint"] == "/admin/nightly_pipeline/run"
    assert js["background_pipeline"]["run_endpoint"] == "/admin/background_pipeline/run"
    assert js["event_impact"]["run_endpoint"] == "/admin/event_impact/run"
    assert js["event_impact"]["live_trade_execution"] is False
    assert js["market_forecast_turn"]["run_endpoint"] == "/admin/market_forecast_turn/run"
    assert js["market_forecast_turn"]["live_trade_execution"] is False
    assert "background_runtime" in js


def test_meta_uses_chat_lane_env(apollo_app, monkeypatch):
    client = apollo_app.test_client()
    monkeypatch.setenv("OLLAMA_URL_CHAT", "http://127.0.0.1:19999")
    monkeypatch.setenv("OLLAMA_MODEL_CHAT", "unit-chat-model")
    monkeypatch.setenv("OLLAMA_MODEL_DEEP", "unit-deep-model")
    resp = client.get("/meta")
    assert resp.status_code == 200
    js = resp.get_json()
    assert js["ollama"]["url"] == "http://127.0.0.1:19999"
    assert js["ollama"]["model"] == "unit-chat-model"
    assert js["chat_lane"]["model"] == "unit-chat-model"
    assert js["deep_lane"]["model"] == "unit-deep-model"


def test_meta_exposes_focus_universe_contract(apollo_app):
    client = apollo_app.test_client()
    resp = client.get("/meta")
    assert resp.status_code == 200
    js = resp.get_json()
    focus = js["focus_universe"]
    assert focus["enabled"] is True
    assert focus["status_endpoint"] == "/admin/focus_universe/status"
    assert focus["learn_endpoint"] == "/admin/focus_universe/learn"
    assert focus["paper_trade_endpoint"] == "/admin/focus_universe/paper_trade"
    assert focus["events_endpoint"] == "/admin/focus_universe/events"


def test_home_includes_reverse_proxy_helpers(apollo_app):
    client = apollo_app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-app-base-path="' in html
    assert "__apolloApiUrl" in html


def test_chat_background_runtime_status_is_deterministic(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(
        apollo_module,
        "load_apollo_background_runtime",
        lambda: {
            "available": True,
            "current_stage": "hipporag",
            "overall_progress_pct": 50.5,
            "completed_stages": ["gather", "ocr"],
            "ok": False,
            "gb10_gate": {"idle": False, "active_models": 1, "max_active_models": 1, "idle_seconds": 221, "min_idle_sec": 300},
            "waiting": {"reason": "gb10_busy"},
            "gather": {"sources": [{"title": "Seed PDF"}]},
            "ocr": {
                "ingested": 1,
                "remaining_documents": 0,
                "documents": [{"title": "Seed PDF", "extract_engine": "gb10_auto", "text_chars": 16000}],
            },
            "hipporag": {"remaining_after": 607, "total_chunks": 620, "processed": 4, "stats": {"indexed_docs": 13, "nodes": 19, "edges": 30}},
            "last_step_at": "2026-04-14T21:08:07Z",
            "status_path": "C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\nightly\\latest_status.json",
        },
    )
    client = apollo_app.test_client()
    resp = client.post("/chat", json={"message": "what is the background pipeline doing right now?", "conversation_id": "bg-runtime"})
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert "stage=hipporag" in reply
    assert "remaining=607/620" in reply
    assert "Waiting: gb10_busy." in reply
    assert "Latest doc=Seed PDF" in reply


def test_chat_background_runtime_followup_keeps_context(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    runtime = {
        "available": True,
        "current_stage": "ocr",
        "overall_progress_pct": 26.0,
        "completed_stages": ["gather"],
        "ok": False,
        "gather": {"sources": [{"title": "FINRA day trading margin filing"}]},
        "ocr": {
            "ingested": 1,
            "remaining_documents": 0,
            "documents": [
                {
                    "title": "FINRA day trading margin filing",
                    "extract_engine": "gb10_auto",
                    "extract_pages": 4,
                    "text_chars": 16000,
                    "extract_confidence": 0.93,
                }
            ],
        },
        "status_path": "C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\nightly\\latest_status.json",
    }
    monkeypatch.setattr(apollo_module, "load_apollo_background_runtime", lambda: runtime)
    client = apollo_app.test_client()
    first = client.post("/chat", json={"message": "what is the background pipeline doing right now?", "conversation_id": "bg-followup"})
    assert first.status_code == 200
    second = client.post("/chat", json={"message": "what did OCR find from that pdf?", "conversation_id": "bg-followup"})
    assert second.status_code == 200
    reply = second.get_json()["reply"]
    assert "engine=gb10_auto" in reply
    assert "chars=16000" in reply


def test_chat_background_command_runs_step_and_returns_status(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "run_background_pipeline_step",
        lambda payload=None: {
            "ok": False,
            "run_id": "apollo_background_current",
            "run_mode": "background",
            "current_stage": "gather",
            "stages": {
                "gather": {
                    "completed": True,
                    "sources": [{"title": "Seed PDF"}],
                }
            },
        },
    )
    client = apollo_app.test_client()
    resp = client.post("/chat", json={"message": "run the Apollo background pipeline step now", "conversation_id": "bg-run"})
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert "step requested" in reply
    assert "sample=Seed PDF" in reply


def test_chat_focus_command_is_deterministic(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    state = {
        "focus": {
            "selected_theme": "Semiconductors",
            "selected_tickers": ["NVDA", "AMD", "AVGO"],
            "decision_gate": {"decision_ready": True, "decision_mode": "watch", "confidence_label": "high"},
            "theme_score": 91.0,
            "evidence_volume": 14,
        }
    }
    monkeypatch.setattr(apollo_module, "_sync_focus_state", lambda note="": state)
    monkeypatch.setattr(apollo_module.apollo_focus_trading, "format_focus_summary", lambda payload: "Apollo Focus Universe: theme=Semiconductors")
    client = apollo_app.test_client()
    resp = client.post("/chat", json={"message": "!focus", "conversation_id": "focus-command"})
    assert resp.status_code == 200
    js = resp.get_json()
    assert js["reasoning"] == "(apollo-focus-universe)"
    assert "theme=Semiconductors" in js["reply"]


def test_chat_learn_focus_refreshes_state(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    state = {
        "focus": {
            "selected_theme": "Cloud Software",
            "selected_tickers": ["MSFT", "CRM"],
            "decision_gate": {"decision_ready": False, "decision_mode": "research", "confidence_label": "medium"},
            "theme_score": 78.0,
            "evidence_volume": 9,
        }
    }
    calls = []

    def _fake_sync(note=""):
        calls.append(note)
        return state

    monkeypatch.setattr(apollo_module, "_sync_focus_state", _fake_sync)
    monkeypatch.setattr(apollo_module.apollo_focus_trading, "format_focus_summary", lambda payload: "Apollo Focus Universe: theme=Cloud Software")
    client = apollo_app.test_client()
    resp = client.post("/chat", json={"message": "!learn_focus", "conversation_id": "focus-learn"})
    assert resp.status_code == 200
    js = resp.get_json()
    assert calls == ["manual_chat_learn"]
    assert "refreshed its learned focus state" in js["reply"]
    assert "Cloud Software" in js["reply"]


def test_chat_paper_trade_command_records_theoretical_trade(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    result = {
        "ok": True,
        "positions": [{"ticker": "NVDA", "stance": "long", "status": "open"}],
        "focus": {"selected_theme": "Semiconductors", "selected_tickers": ["NVDA", "AMD"]},
    }
    state = {"focus": result["focus"], "paper_positions": result["positions"]}
    monkeypatch.setattr(apollo_module.apollo_focus_trading, "submit_paper_trade", lambda **kwargs: result)
    monkeypatch.setattr(apollo_module.apollo_focus_trading, "load_state", lambda: state)
    monkeypatch.setattr(apollo_module.apollo_focus_trading, "format_focus_summary", lambda payload: "Apollo Focus Universe: theme=Semiconductors\nPaper positions: NVDA long")
    client = apollo_app.test_client()
    resp = client.post(
        "/chat",
        json={"message": "!paper_trade buy NVDA thesis: earnings strength confidence 0.72 horizon swing", "conversation_id": "paper-trade"},
    )
    assert resp.status_code == 200
    js = resp.get_json()
    assert js["reasoning"] == "(apollo-paper-trade)"
    assert js["result"]["ok"] is True
    assert "Paper trade recorded." in js["reply"]
    assert "NVDA long" in js["reply"]


def test_chat_prompt_includes_focus_context_for_normal_discussion(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    captured = {}

    def _fake_query_model(prompt, **kwargs):
        captured["prompt"] = prompt
        return "Model reply"

    monkeypatch.setattr(apollo_module, "_focus_context_block", lambda: "[APOLLO_FOCUS_CONTEXT]\nFocus theme: Semiconductors\n[/APOLLO_FOCUS_CONTEXT]")
    monkeypatch.setattr(apollo_module, "gather_hits", lambda intent, message, depth: ([], 0))
    monkeypatch.setattr(apollo_module, "query_model", _fake_query_model)
    client = apollo_app.test_client()
    resp = client.post("/chat", json={"message": "Summarize the current investing process.", "conversation_id": "focus-context"})
    assert resp.status_code == 200
    assert resp.get_json()["reply"] == "Model reply"
    assert "[APOLLO_FOCUS_CONTEXT]" in captured["prompt"]
    assert "Focus theme: Semiconductors" in captured["prompt"]


def test_apollo_dashboard_context_block_summarizes_decision_state(tmp_path, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(apollo_module, "LOG_DIR", tmp_path)
    sim_dir = tmp_path / "swing_simulation"
    sim_dir.mkdir(parents=True)
    (sim_dir / "account_state.json").write_text(
        '{"starting_balance":100,"cash_balance":100,"invested_amount":0,"market_value":100,"status":"not_started","open_positions":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        apollo_module.apollo_swing_study,
        "latest_swing_study",
        lambda: {
            "run_id": "swing_unit",
            "created_at": "2026-05-14T02:14:42Z",
            "high_confidence": True,
            "blocked_session": False,
            "market_regime": {"label": "risk_on", "score": 100, "risk_notes": []},
            "best_proposal": {
                "ticker": "CAT",
                "setup_type": "trend_continuation",
                "score": 84.94,
                "status": "new",
                "confidence_label": "high",
                "direction_bias": "long_bias",
                "entry_zone": "888.71-909.85",
                "stop_zone": "754.45",
                "target_zone": "968.76",
                "risk_reward_estimate": 0.45,
                "review_by_date": "2026-05-19",
                "invalidation_trigger": "Close below 754.45",
                "score_components": {"trend": 80, "relative_strength": 87.34, "catalyst": 94.66, "market_regime": 100},
            },
            "proposals": [],
        },
    )
    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "latest_status",
        lambda: {
            "run_id": "daily_unit",
            "ok": True,
            "current_stage": "done",
            "quality": {"gate_pass": True, "stage_scores": {"gather": 90, "hipporag": 85}},
            "corpus": {"total_chunks": 900, "real_chunk_count": 250, "synthetic_chunk_count": 620},
        },
    )
    monkeypatch.setattr(apollo_module, "_latest_nightly_ocr_status", lambda: {"ingested": 4, "errors": []})

    block = apollo_module._apollo_dashboard_context_block("explain the Apollo dashboard")
    assert "[APOLLO_DASHBOARD_CONTEXT]" in block
    assert "ticker=CAT" in block
    assert "R/R 0.45 < 1.00" in block
    assert "starting_balance=$100.00" in block
    assert "01:00 CT" in block and "03:15 CT" in block


def test_chat_prompt_includes_dashboard_context_for_market_discussion(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    captured = {}

    def _fake_query_model(prompt, **kwargs):
        captured["prompt"] = prompt
        return "Model reply"

    monkeypatch.setattr(apollo_module, "_focus_context_block", lambda: "")
    monkeypatch.setattr(
        apollo_module,
        "_apollo_dashboard_context_block",
        lambda message: "[APOLLO_DASHBOARD_CONTEXT]\nBest setup: ticker=CAT; decision=blocked: reward does not justify risk.\n[/APOLLO_DASHBOARD_CONTEXT]",
    )
    monkeypatch.setattr(apollo_module, "gather_hits", lambda intent, message, depth: ([], 0))
    monkeypatch.setattr(apollo_module, "query_model", _fake_query_model)
    client = apollo_app.test_client()
    resp = client.post(
        "/chat",
        json={"message": "Explain the Apollo swing trade dashboard and its setup score details.", "conversation_id": "dashboard-context"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["reply"] == "Model reply"
    assert "[APOLLO_DASHBOARD_CONTEXT]" in captured["prompt"]
    assert "ticker=CAT" in captured["prompt"]


def test_chat_stream_emits_sse_for_runtime_status(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(
        apollo_module,
        "load_apollo_background_runtime",
        lambda: {
            "available": True,
            "current_stage": "ocr",
            "overall_progress_pct": 12.0,
            "completed_stages": [],
            "waiting": {"reason": "idle_window"},
            "ocr": {"ingested": 2, "remaining_documents": 4},
            "status_path": "C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\nightly\\latest_status.json",
        },
    )
    client = apollo_app.test_client()
    resp = client.post(
        "/chat/stream",
        json={"message": "what is the background pipeline doing right now?", "conversation_id": "bg-runtime-stream"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "event: delta" in body
    assert "event: final" in body
    assert "stage=ocr" in body


def test_background_pipeline_pause_endpoint_returns_status(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(apollo_module.apollo_nightly_pipeline, "set_pipeline_paused", lambda paused: None)
    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "mark_background_control_state",
        lambda reason: {"run_id": "apollo_background_current", "waiting": {"reason": reason}},
    )
    client = apollo_app.test_client()
    resp = client.post("/admin/background_pipeline/pause", json={"paused": True})
    assert resp.status_code == 200
    js = resp.get_json()
    assert js["ok"] is True
    assert js["paused"] is True
    assert js["status"]["waiting"]["reason"] == "paused"


def test_background_pipeline_run_endpoint_is_async(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "background_lock_state",
        lambda clear_stale=True: {"present": False, "stale": False, "cleared_stale": False, "payload": {}},
    )
    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "latest_status",
        lambda: {"run_id": "apollo_background_current", "current_stage": "gather"},
    )
    observed = {}

    def _fake_step(payload):
        observed["payload"] = dict(payload)
        return {"ok": True, "run_id": "apollo_background_current", "current_stage": "gather"}

    monkeypatch.setattr(apollo_module.apollo_nightly_pipeline, "run_background_pipeline_step", _fake_step)
    with apollo_module._BACKGROUND_PIPELINE_RUN_LOCK:
        apollo_module._BACKGROUND_PIPELINE_RUN_STATE.update(
            {
                "status": "idle",
                "started_at": "",
                "finished_at": "",
                "worker_name": "",
                "payload": {},
                "last_result": {},
                "last_error": "",
            }
        )

    client = apollo_app.test_client()
    resp = client.post("/admin/background_pipeline/run", json={"tick_reason": "unit_test"})
    assert resp.status_code == 202
    js = resp.get_json()
    assert js["ok"] is True
    assert js["accepted"] is True

    for _ in range(20):
        if observed:
            break
        time.sleep(0.01)
    assert observed["payload"]["tick_reason"] == "unit_test"


def test_background_pipeline_run_endpoint_returns_409_when_lock_present(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "background_lock_state",
        lambda clear_stale=True: {
            "present": True,
            "stale": False,
            "cleared_stale": False,
            "payload": {"owner_pid": 12345, "run_id": "apollo_background_current"},
        },
    )
    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "latest_status",
        lambda: {"run_id": "apollo_background_current", "current_stage": "gather"},
    )
    monkeypatch.setattr(
        apollo_module.apollo_nightly_pipeline,
        "run_background_pipeline_step",
        lambda payload: (_ for _ in ()).throw(AssertionError("should not run when lock is present")),
    )
    with apollo_module._BACKGROUND_PIPELINE_RUN_LOCK:
        apollo_module._BACKGROUND_PIPELINE_RUN_STATE.update(
            {
                "status": "idle",
                "started_at": "",
                "finished_at": "",
                "worker_name": "",
                "payload": {},
                "last_result": {},
                "last_error": "",
            }
        )

    client = apollo_app.test_client()
    resp = client.post("/admin/background_pipeline/run", json={"tick_reason": "unit_test"})
    assert resp.status_code == 409
    js = resp.get_json()
    assert js["ok"] is False
    assert js["accepted"] is False
    assert js["error"] == "nightly_lock_present"


def test_health_endpoint(apollo_app):
    client = apollo_app.test_client()
    start = time.perf_counter()
    resp = client.get("/health")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    js = resp.get_json()
    assert js.get("app") == "apollo"
    assert "ui_chat_gpu_backed" in js
    # Health should be fast enough for ops use (even if degraded).
    assert elapsed < 10.0


def test_rag_search_endpoint(apollo_app, monkeypatch):
    from Apollo import rag_routes  # noqa: WPS433

    monkeypatch.setattr(
        rag_routes.RAG,
        "search",
        lambda **kwargs: {"results": [{"text": "Inflation cooled", "score": 0.91}], "query": kwargs.get("query"), "top_k": kwargs.get("top_k", 2)},
    )
    client = apollo_app.test_client()
    resp = client.post("/rag/search", json={"query": "inflation", "top_k": 2})
    assert resp.status_code == 200
    js = resp.get_json()
    assert js.get("ok") is True
    assert "data" in js
    assert isinstance(js["data"], dict)


def test_rag_restore_is_disabled(apollo_app):
    client = apollo_app.test_client()
    resp = client.post("/rag/restore", json={"path": "C:/tmp/apollo_snapshot.zip"})
    assert resp.status_code == 403
    js = resp.get_json()
    assert js["ok"] is False
    assert js["error"] == "disabled"


def test_rag_store_is_apollo(apollo_app):
    from Apollo import rag_routes  # noqa: WPS433

    assert getattr(rag_routes, "RAG").agent.lower() == "apollo"


def test_hipporag_graph_endpoint(apollo_app):
    client = apollo_app.test_client()
    resp = client.get("/admin/hipporag/graph?limit_nodes=40&hops=2")
    assert resp.status_code == 200
    js = resp.get_json()
    assert js["ok"] is True
    assert "nodes" in js and isinstance(js["nodes"], list)
    assert "edges" in js and isinstance(js["edges"], list)
    assert "stats" in js and isinstance(js["stats"], dict)
    assert js["filters"]["limit_nodes"] == 40


def test_focus_universe_admin_endpoints(apollo_app, monkeypatch):
    from Apollo import app as apollo_module  # noqa: WPS433

    status_state = {
        "focus": {"selected_theme": "Semiconductors", "selected_tickers": ["NVDA", "AMD"]},
        "events": [{"ts": "2026-04-21T00:00:00Z", "summary": "Focus universe learned: Semiconductors"}],
        "discussion_context": "Focus theme: Semiconductors",
    }
    learned_state = {
        "focus": {"selected_theme": "Cloud Software", "selected_tickers": ["MSFT"]},
        "events": [{"ts": "2026-04-21T01:00:00Z", "summary": "Focus universe learned: Cloud Software"}],
        "discussion_context": "Focus theme: Cloud Software",
    }
    monkeypatch.setattr(apollo_module.apollo_focus_trading, "load_state", lambda: status_state)
    monkeypatch.setattr(apollo_module, "_sync_focus_state", lambda note="": learned_state)
    monkeypatch.setattr(
        apollo_module.apollo_focus_trading,
        "submit_paper_trade",
        lambda **kwargs: {"ok": True, "positions": [{"ticker": "MSFT", "stance": "long"}], "focus": learned_state["focus"]},
    )
    client = apollo_app.test_client()

    status_resp = client.get("/admin/focus_universe/status")
    assert status_resp.status_code == 200
    assert status_resp.get_json()["status"]["focus"]["selected_theme"] == "Semiconductors"

    learn_resp = client.post("/admin/focus_universe/learn", json={"note": "manual"})
    assert learn_resp.status_code == 200
    assert learn_resp.get_json()["status"]["focus"]["selected_theme"] == "Cloud Software"

    trade_resp = client.post("/admin/focus_universe/paper_trade", json={"action": "buy", "ticker": "MSFT", "thesis": "quality compounder"})
    assert trade_resp.status_code == 200
    assert trade_resp.get_json()["ok"] is True
    assert trade_resp.get_json()["positions"][0]["ticker"] == "MSFT"

    events_resp = client.get("/admin/focus_universe/events?limit=1")
    assert events_resp.status_code == 200
    events_js = events_resp.get_json()
    assert events_js["ok"] is True
    assert len(events_js["events"]) == 1
    assert "Focus theme" in events_js["discussion_context"]
