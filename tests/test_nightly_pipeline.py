from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import Apollo.nightly_pipeline as nightly_pipeline
import Apollo.trading_homework as trading_homework
from Apollo.nightly_pipeline import (
    _all_stages_complete,
    _apply_gather_quality_gate,
    _build_stage_details,
    _compute_quality,
    _enrich_status_payload,
    _evaluate_hipporag_quality,
    _evaluate_ocr_quality,
    _load_status,
    _lock_is_stale,
    _pipeline_config,
    _run_aegis_gateway_probe,
    _save_status,
    _update_stage_status,
    _write_json,
    _write_truthful_pipeline_report,
    estimate_runtime_breakdown,
    list_background_run_history,
    purge_background_source,
    scorecard,
)
from Apollo.trading_homework import (
    _deterministic_phase2_fallback,
    _pick_sources_with_llm,
    _study_default_queries,
    gather_trading_homework_sources,
)


def test_estimate_runtime_breakdown_uses_limit():
    config = _pipeline_config({"hipporag_limit": 10, "max_articles": 2, "hipporag_use_llm": True})
    estimate = estimate_runtime_breakdown(config, chunk_count=620)
    assert estimate["chunk_count"] == 10
    assert estimate["hipporag_mode"] == "llm"
    assert estimate["total_minutes"] >= estimate["hipporag_minutes"]


def test_schedule_background_run_enables_existing_task(monkeypatch, tmp_path):
    calls = []

    class Proc:
        def __init__(self, stdout="ok"):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return Proc(" ".join(str(part) for part in cmd))

    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "_ENGINEERING_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(nightly_pipeline.subprocess, "run", fake_run)

    result = nightly_pipeline.schedule_background_run({"background_interval_min": 17})

    assert result["ok"] is True
    assert result["enabled"] is True
    assert any(call[:4] == ["schtasks", "/Change", "/TN", nightly_pipeline.DEFAULT_BACKGROUND_TASK_NAME] and "/Enable" in call for call in calls)
    assert any(call[:4] == ["schtasks", "/Change", "/TN", nightly_pipeline.DEFAULT_TASK_NAME] and "/Disable" in call for call in calls)


def test_gather_sources_accepts_explicit_source_dicts():
    result = gather_trading_homework_sources(
        {
            "topic": "day trading",
            "max_articles": 2,
            "sources": [
                {"url": "https://example.com/one.pdf", "title": "One", "why": "seed"},
                {"url": "https://example.com/two.pdf", "title": "Two", "why": "backup"},
            ],
        }
    )
    assert result["ok"] is True
    assert [item["title"] for item in result["sources"]] == ["One", "Two"]
    assert [item["why"] for item in result["sources"]] == ["seed", "backup"]


def test_phase2_llm_selection_rejects_non_candidate_urls(monkeypatch):
    candidates = [
        {"title": "FINRA Rule 4210 Margin Requirements", "snippet": "pattern day trader", "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210"},
        {"title": "SEC Investor Bulletin Margin Accounts", "snippet": "margin and leverage risks", "url": "https://www.sec.gov/files/day-trading-bulletin.pdf"},
    ]

    monkeypatch.setattr(
        "Apollo.trading_homework.query_model_with_meta",
        lambda *args, **kwargs: {
            "text": json.dumps(
                {
                    "picks": [
                        {"candidate_id": 2, "why": "Regulator bulletin with direct margin guidance"},
                        {"url": "https://attacker.example/fake.pdf", "why": "hallucinated"},
                    ]
                }
            )
        },
    )

    selected = _pick_sources_with_llm(candidates, 2)
    assert [item.get("url") for item in selected] == ["https://www.sec.gov/files/day-trading-bulletin.pdf"]
    trace = dict(getattr(_pick_sources_with_llm, "last_trace", {}) or {})
    assert trace.get("ok") is True
    assert int(trace.get("accepted_count") or 0) == 1
    assert int(trace.get("rejected_count") or 0) >= 1
    assert "candidate_not_in_window" in list(trace.get("rejected_reasons") or [])


def test_phase2_deterministic_fallback_prefers_domain_diversity(monkeypatch):
    candidates = [
        {"title": "A1", "snippet": "stock trading", "url": "https://alpha.example/1.pdf", "mock_score": 100},
        {"title": "A2", "snippet": "stock trading", "url": "https://alpha.example/2.pdf", "mock_score": 99},
        {"title": "B1", "snippet": "stock trading", "url": "https://beta.example/1.pdf", "mock_score": 98},
    ]
    monkeypatch.setattr("Apollo.trading_homework._candidate_score", lambda item: float(item.get("mock_score") or 0))
    picks = _deterministic_phase2_fallback(candidates, 2)
    assert len(picks) == 2
    assert picks[0]["url"] == "https://alpha.example/1.pdf"
    assert picks[1]["url"] == "https://beta.example/1.pdf"
    assert all("deterministic_phase2_" in str(item.get("why") or "") for item in picks)


def test_gather_sources_filters_irrelevant_and_dictionary_results(monkeypatch):
    def fake_web_search(query, **_kwargs):
        return {
            "ok": True,
            "results": [
                {
                    "title": "CHEMICAL AIRWAY RESPIRATORY IRRITANTS BLS/ALS - Chicago EMS",
                    "snippet": "Inhalation of a variety of gases, mists, fumes, aerosols, or dusts may cause irritation or injury.",
                    "url": "https://chicagoems.org/wp-content/uploads/2024/08/ALS_BLS_Chemical-Airway-Respiratory-Irritants_8_15_24.pdf",
                },
                {
                    "title": "PATTERN Definition & Meaning - Merriam-Webster",
                    "snippet": "The meaning of pattern is a form or model proposed for imitation.",
                    "url": "https://www.merriam-webster.com/dictionary/pattern",
                },
                {
                    "title": "Pattern Day Trading Rules & Requirements Explained",
                    "snippet": "A pattern day trader is someone who executes four or more day trades in a margin account.",
                    "url": "https://www.daytrading.com/pattern-day-trading",
                },
                {
                    "title": "Mathematics of Money Management: Position Sizing and Risk of Ruin",
                    "snippet": "Learn position sizing models, risk of ruin calculations, and expectancy to protect capital.",
                    "url": "https://fnpulse.com/en/blog/mathematics-of-money-management-position-sizing-and-risk-of-ruin",
                },
            ],
        }

    monkeypatch.setattr("Apollo.trading_homework._web_search", fake_web_search)
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: [])

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 3,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )

    urls = [item["url"] for item in result["sources"]]
    assert "https://chicagoems.org/wp-content/uploads/2024/08/ALS_BLS_Chemical-Airway-Respiratory-Irritants_8_15_24.pdf" not in urls
    assert "https://www.merriam-webster.com/dictionary/pattern" not in urls
    assert any("federalreserve.gov" in str(url) or "finra.org" in str(url) for url in urls)
    assert any(str(url).endswith(".pdf") for url in urls)


def test_gather_sources_uses_local_seed_fallback_on_rate_limit(monkeypatch, tmp_path):
    seed_pdf = tmp_path / "seed_day_trading_margin.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed")

    monkeypatch.setattr(
        "Apollo.trading_homework._web_search",
        lambda query, **_kwargs: {"ok": False, "error": "rate_limited"},
    )
    monkeypatch.setattr(
        "Apollo.trading_homework._ensure_local_finance_seed_pdfs",
        lambda: [
            {
                "pack": "seed_fallback",
                "query": "seed_fallback_local_pdf",
                "title": "Seed day trading margin risk controls PDF",
                "snippet": "stock trading day trading margin risk management position sizing stop loss",
                "url": str(seed_pdf),
                "is_pdf": True,
                "provider": "seed_fallback",
            }
        ],
    )
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: [])

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 1,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )

    urls = [item["url"] for item in result["sources"]]
    assert urls
    assert any(str(url).endswith(".pdf") for url in urls)
    assert any("rate_limited" in str(err) for err in result.get("errors") or [])


def test_gather_sources_tries_provider_fallback_after_rate_limit(monkeypatch):
    calls = []

    def fake_web_search(query, **kwargs):
        calls.append(str(kwargs.get("provider") or ""))
        provider = str(kwargs.get("provider") or "")
        if provider == "auto":
            return {"ok": False, "error": "rate_limited"}
        return {
            "ok": True,
            "results": [
                {
                    "title": "SEC Market Structure Report PDF",
                    "snippet": "execution quality and overnight risk controls",
                    "url": "https://www.sec.gov/files/market-structure-report.pdf",
                }
            ],
            "meta": {"provider": provider},
        }

    monkeypatch.setattr("Apollo.trading_homework._web_search", fake_web_search)
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: [])
    monkeypatch.setattr("Apollo.trading_homework._resolve_document_source_url", lambda url, timeout=4: url)

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 1,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
            "web": {
                "provider": "auto",
                "provider_fallbacks": ["duckduckgo_html"],
                "rate_limit_retries": 0,
            },
        }
    )

    assert calls == ["auto", "duckduckgo_html"]
    assert len(result.get("sources") or []) == 1
    assert any("rate_limited" in str(err) for err in result.get("errors") or [])


def test_gather_sources_can_disable_seed_fallback_on_rate_limit(monkeypatch, tmp_path):
    seed_pdf = tmp_path / "seed_should_not_be_used.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed-no-use")
    monkeypatch.setattr("Apollo.trading_homework._web_search", lambda query, **_kwargs: {"ok": False, "error": "rate_limited"})
    monkeypatch.setattr("Apollo.trading_homework._manifest_candidates", lambda topic, max_items: [])
    monkeypatch.setattr(
        "Apollo.trading_homework._ensure_local_finance_seed_pdfs",
        lambda: [
            {
                "pack": "seed_fallback",
                "query": "seed_fallback_local_pdf",
                "title": "Seed candidate",
                "snippet": "seed fallback",
                "url": str(seed_pdf),
                "is_pdf": True,
                "provider": "seed_fallback",
            }
        ],
    )

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 1,
            "allow_web": True,
            "allow_inbox": False,
            "allow_seed_fallback": False,
            "queries": ["test query"],
        }
    )

    assert result["sources"] == []
    assert "web_search_rate_limited:no_seed_fallback" in list(result.get("errors") or [])


def test_gather_sources_backfills_when_llm_selects_fewer_than_max(monkeypatch):
    monkeypatch.setattr(
        "Apollo.trading_homework._web_search",
        lambda query, **_kwargs: {
            "ok": True,
            "results": [
                {"title": "FINRA day trading margin rule pdf", "snippet": "day trading margin risk management", "url": "https://example.com/a.pdf"},
                {"title": "SEC intraday margin bulletin pdf", "snippet": "stock trading day trading margin", "url": "https://example.com/b.pdf"},
                {"title": "Federal Reserve microstructure pdf", "snippet": "execution slippage transaction costs", "url": "https://example.com/c.pdf"},
                {"title": "NBER position sizing pdf", "snippet": "position sizing risk of ruin", "url": "https://example.com/d.pdf"},
            ],
            "meta": {"provider": "test"},
        },
    )
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: candidates[:2])

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 4,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )
    assert len(result["sources"]) == 4


def test_gather_sources_backfills_seed_pdf_when_pdf_min_not_met(monkeypatch, tmp_path):
    seed_pdf = tmp_path / "seed_secondary.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed2")

    def _score(item):
        return float(item.get("mock_score") or 0.0)

    monkeypatch.setattr("Apollo.trading_homework._candidate_score", _score)
    monkeypatch.setattr(
        "Apollo.trading_homework._manifest_candidates",
        lambda topic, max_items: [
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "Primary PDF",
                "snippet": "risk controls",
                "url": "https://example.com/primary.pdf",
                "is_pdf": True,
                "provider": "manifest_curated",
                "tier": "A",
                "mock_score": 100,
            },
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "Trusted HTML A",
                "snippet": "rules",
                "url": "https://example.com/a",
                "is_pdf": False,
                "provider": "manifest_curated",
                "tier": "A",
                "mock_score": 90,
            },
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "Trusted HTML B",
                "snippet": "execution",
                "url": "https://example.org/b",
                "is_pdf": False,
                "provider": "manifest_curated",
                "tier": "A",
                "mock_score": 80,
            },
        ],
    )
    monkeypatch.setattr("Apollo.trading_homework._web_search", lambda query, **_kwargs: {"ok": False, "error": "rate_limited"})
    monkeypatch.setattr(
        "Apollo.trading_homework._ensure_local_finance_seed_pdfs",
        lambda: [
            {
                "pack": "seed_fallback",
                "query": "seed_fallback_local_pdf",
                "title": "Seed PDF",
                "snippet": "fallback",
                "url": str(seed_pdf),
                "is_pdf": True,
                "provider": "seed_fallback",
                "mock_score": 10,
            }
        ],
    )
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: [])
    monkeypatch.setattr("Apollo.trading_homework._resolve_document_source_url", lambda url, timeout=4: url)

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading overnight hold strategy",
            "max_articles": 3,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )

    urls = [item["url"] for item in result["sources"]]
    pdf_count = sum(1 for url in urls if str(url).lower().endswith(".pdf"))
    assert pdf_count >= 2
    assert str(seed_pdf) in urls


def test_gather_sources_manifest_fallback_triggers_when_candidates_are_noise(monkeypatch):
    monkeypatch.setattr(
        "Apollo.trading_homework._manifest_candidates",
        lambda topic, max_items: [
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "FINRA Rule 4210 PDF",
                "snippet": "pattern day trader margin requirements",
                "url": "https://www.finra.org/sites/default/files/Rule4210.pdf",
                "is_pdf": True,
                "provider": "manifest_curated",
                "tier": "A",
            },
            {
                "pack": "risk_position_sizing",
                "query": "manifest_curated",
                "title": "Federal Reserve Stability Report",
                "snippet": "risk management and execution quality",
                "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-202404.pdf",
                "is_pdf": True,
                "provider": "manifest_curated",
                "tier": "A",
            },
        ],
    )
    monkeypatch.setattr(
        "Apollo.trading_homework._web_search",
        lambda query, **_kwargs: {
            "ok": True,
            "results": [
                {"title": "Google Finance", "snippet": "markets", "url": "https://www.google.com/finance/"},
                {"title": "Dictionary pattern", "snippet": "definition", "url": "https://www.merriam-webster.com/dictionary/pattern"},
            ],
        },
    )
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: [])
    monkeypatch.setattr("Apollo.trading_homework._resolve_document_source_url", lambda url, timeout=4: url)
    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 2,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )
    urls = [item["url"] for item in result["sources"]]
    assert "https://www.finra.org/sites/default/files/Rule4210.pdf" in urls
    assert any(str(item).endswith(".pdf") for item in urls)


def test_gather_sources_rejects_manifest_html_that_never_resolves_to_document(monkeypatch):
    monkeypatch.setattr(
        "Apollo.trading_homework._manifest_candidates",
        lambda topic, max_items: [
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "FINRA Rule 4210",
                "snippet": "pattern day trader margin requirements",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
                "is_pdf": False,
                "provider": "manifest_curated",
                "tier": "A",
            }
        ],
    )
    monkeypatch.setattr("Apollo.trading_homework._web_search", lambda query, **_kwargs: {"ok": False, "error": "rate_limited"})
    monkeypatch.setattr("Apollo.trading_homework._resolve_document_source_url", lambda url, timeout=4: "")
    monkeypatch.setattr("Apollo.trading_homework._ensure_local_finance_seed_pdfs", lambda: [])
    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 1,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )
    assert result["sources"] == []
    assert any("non_document_source_rejected:" in str(err) for err in result.get("errors") or [])


def test_gather_sources_accepts_trusted_report_like_html_sources(monkeypatch):
    monkeypatch.setattr(
        "Apollo.trading_homework._manifest_candidates",
        lambda topic, max_items: [
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "FINRA Rule 4210 Margin Requirements",
                "snippet": "pattern day trader margin requirements",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
                "is_pdf": False,
                "provider": "manifest_curated",
                "tier": "A",
            },
            {
                "pack": "regulatory_compliance",
                "query": "manifest_curated",
                "title": "SEC Investor Bulletin: Margin Accounts",
                "snippet": "day trading and leverage risks",
                "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
                "is_pdf": False,
                "provider": "manifest_curated",
                "tier": "A",
            },
        ],
    )
    monkeypatch.setattr("Apollo.trading_homework._web_search", lambda query, **_kwargs: {"ok": False, "error": "rate_limited"})
    monkeypatch.setattr("Apollo.trading_homework._ensure_local_finance_seed_pdfs", lambda: [])

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 2,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )

    urls = [item["url"] for item in result["sources"]]
    assert "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210" in urls
    assert "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts" in urls
    assert trading_homework._looks_like_report_like_url(urls[0]) is True


def test_resolve_document_source_url_prefers_discovered_pdf_for_report_like_pages(monkeypatch):
    report_page = "https://www.federalreserve.gov/publications/financial-stability-report.htm"
    resolved_pdf = "https://www.federalreserve.gov/publications/files/financial-stability-report-20250425.pdf"

    monkeypatch.setattr("Apollo.trading_homework._load_pdf_resolution_cache", lambda: {report_page: report_page})
    monkeypatch.setattr("Apollo.trading_homework._save_pdf_resolution_cache", lambda cache: None)
    monkeypatch.setattr("Apollo.trading_homework._discover_pdf_link", lambda url, timeout=4: resolved_pdf)

    resolved = trading_homework._resolve_document_source_url(report_page)

    assert resolved == resolved_pdf


def test_gather_sources_emits_phase2_trace(monkeypatch):
    monkeypatch.setattr(
        "Apollo.trading_homework._web_search",
        lambda query, **_kwargs: {
            "ok": True,
            "results": [
                {
                    "title": "SEC day trading bulletin",
                    "snippet": "margin leverage risk controls",
                    "url": "https://www.sec.gov/files/day-trading-bulletin.pdf",
                },
                {
                    "title": "FINRA pattern day trader rule",
                    "snippet": "pattern day trader margin requirements",
                    "url": "https://www.finra.org/sites/default/files/Rule4210.pdf",
                },
            ],
        },
    )
    monkeypatch.setattr("Apollo.trading_homework._pick_sources_with_llm", lambda candidates, max_pick: [])
    monkeypatch.setattr("Apollo.trading_homework._resolve_document_source_url", lambda url, timeout=4: url)
    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 2,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )
    phase2 = dict(result.get("phase2") or {})
    assert phase2.get("strategy") == "llm_then_deterministic_fallback"
    assert phase2.get("selection_mode") == "deterministic_fallback"
    assert int(phase2.get("candidate_count") or 0) >= 2
    assert int(phase2.get("selected_count") or 0) == 2


def test_gather_sources_falls_back_when_llm_picker_times_out(monkeypatch):
    monkeypatch.setattr(
        "Apollo.trading_homework._web_search",
        lambda query, **_kwargs: {
            "ok": True,
            "results": [
                {
                    "title": "Federal Reserve Financial Stability Report",
                    "snippet": "market microstructure and risk backdrop",
                    "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-202404.pdf",
                },
                {
                    "title": "FINRA Rule 4210 Margin Requirements",
                    "snippet": "pattern day trader margin requirements",
                    "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "Apollo.trading_homework.query_model_with_meta",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("picker timeout")),
    )

    result = gather_trading_homework_sources(
        {
            "topic": "stock trading and day trading strategies",
            "max_articles": 2,
            "allow_web": True,
            "allow_inbox": False,
            "queries": ["test query"],
        }
    )

    phase2 = dict(result.get("phase2") or {})
    assert phase2.get("selection_mode") == "deterministic_fallback"
    assert len(result.get("sources") or []) == 2
    assert any("llm_select_failed:TimeoutError" in str(err) for err in result.get("errors") or [])


def test_default_queries_bias_toward_trading_authority_and_exclusions():
    queries = _study_default_queries("stock trading and day trading strategies")
    assert any("site:finra.org" in item for item in queries)
    assert any("site:sec.gov" in item for item in queries)
    assert all("-medical" in item for item in queries)
    assert all("-dictionary" in item for item in queries)
    assert any("risk of ruin" in item for item in queries)
    assert any("market microstructure" in item for item in queries)


def test_stage_gather_autonomously_builds_authoritative_mix_after_thin_rate_limited_sources(monkeypatch, tmp_path):
    calls = []

    def fake_gather(payload):
        calls.append(payload)
        strategy = dict(payload.get("autonomous_quality_strategy") or {})
        if strategy.get("strategy") == "authoritative_evidence_mix":
            sources = [dict(item) for item in list(payload.get("sources") or [])]
            return {
                "ok": True,
                "topic": payload["topic"],
                "batch_id": payload["batch_id"],
                "sources": [{"title": item["title"], "url": item["url"], "why": item.get("why", "selected")} for item in sources],
                "candidates": sources,
                "errors": [],
                "phase2": {
                    "strategy": "autonomous_authoritative_mix",
                    "selection_mode": "provided_authoritative_sources",
                    "selected_count": len(sources),
                },
            }
        thin_candidates = [
            {
                "title": "FINRA Rule 4210 Margin Requirements for NVDA AMD risk controls",
                "snippet": "stock trading day trading margin risk position sizing rule",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
            },
            {
                "title": "SEC Investor Bulletin Margin Accounts for NVDA AMD trading risk",
                "snippet": "stock trading margin account risk leverage bulletin position sizing",
                "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
            },
        ]
        return {
            "ok": True,
            "topic": payload["topic"],
            "batch_id": payload["batch_id"],
            "sources": [{"title": item["title"], "url": item["url"], "why": "thin rate-limited selection"} for item in thin_candidates],
            "candidates": thin_candidates,
            "errors": ["web_search_error:rate_limited", "web_search_rate_limited:no_seed_fallback"],
            "phase2": {"strategy": "test", "selection_mode": "test_thin_selection", "selected_count": 2},
        }

    monkeypatch.setattr("Apollo.trading_homework.gather_trading_homework_sources", fake_gather)
    config = _pipeline_config(
        {
            "batch_id": "apollo_quality_pull_test",
            "topic": "one discretionary stock trade decision per day with overnight holding allowed",
            "max_articles": 4,
            "allow_web": True,
            "allow_inbox": False,
            "equity_news_tickers": ["NVDA", "AMD"],
            "gather_min_accepted_sources": 4,
            "gather_min_avg_score": 55,
            "gather_min_trusted_ratio": 0.75,
            "gather_min_doc_sources": 3,
            "gather_min_pdf_sources": 2,
            "gather_remediation_max_passes": 2,
        }
    )

    result = nightly_pipeline._stage_gather(tmp_path, config)

    assert result["ok"] is True
    assert result["attempt_name"] == "remediation_authoritative_mix"
    strategy = dict(result.get("autonomous_quality_strategy") or {})
    assert strategy.get("triggered") is True
    assert "web_rate_limited" in strategy.get("reasons", [])
    assert "document_source_count_below_gate" in strategy.get("reasons", [])
    assert "pdf_source_count_below_gate" in strategy.get("reasons", [])
    quality = dict(result.get("quality") or {})
    assert quality["gate_pass"] is True
    assert quality["accepted_count"] >= 4
    assert quality["doc_sources"] >= 3
    assert quality["pdf_count"] >= 2
    assert len(calls) >= 3
    authoritative_call = calls[-1]
    assert dict(authoritative_call.get("autonomous_quality_strategy") or {}).get("strategy") == "authoritative_evidence_mix"
    assert len(authoritative_call.get("sources") or []) >= 4
    assert not authoritative_call.get("queries")


def test_pipeline_config_background_defaults():
    config = _pipeline_config({"run_mode": "background"})
    assert config["run_mode"] == "background"
    assert config["batch_id"] == "apollo_background_current"
    assert config["background_interval_min"] >= 1
    assert config["background_hipporag_step_chunks"] >= 1
    assert config["background_min_idle_sec"] >= 0
    assert config["background_gather_timeout_sec"] >= 720
    assert config["background_ocr_step_timeout_sec"] >= 30
    assert config["gather_allow_seed_fallback"] is False
    assert config["gather_web_rate_limit_retries"] >= 0
    assert isinstance(config["gather_web_provider_fallbacks"], list)
    assert config["background_hipporag_step_timeout_sec"] >= 60
    assert config["background_self_check_timeout_sec"] >= 30
    assert config["ocr_qwen_primary"] is False
    assert str(config["ocr_primary_engine_pdf"]).strip().lower() != "gb10_qwen_ocr"


def test_nightly_ocr_rag_rotates_malformed_store_and_retries(monkeypatch, tmp_path):
    rag_dir = tmp_path / "nightly_ocr_rag"
    rag_dir.mkdir()
    (rag_dir / "chroma.sqlite3").write_text("broken", encoding="utf-8")
    calls = []

    class _FakeAgentRAG:
        def __init__(self, agent_name, store_path=None):
            calls.append((agent_name, store_path))
            if len(calls) == 1:
                raise KeyError("_type")
            self.agent_name = agent_name
            self.store_path = store_path

    monkeypatch.setattr("common.rag_store.AgentRAG", _FakeAgentRAG)
    monkeypatch.setenv("APOLLO_NIGHTLY_OCR_RAG_DIR", str(rag_dir))

    rag = nightly_pipeline._nightly_ocr_rag()
    rotated = list(tmp_path.glob("nightly_ocr_rag_corrupt_*"))

    assert isinstance(rag, _FakeAgentRAG)
    assert len(calls) == 2
    assert rotated
    assert rag_dir.exists()


def test_nightly_ocr_rag_dir_defaults_to_run_local_store(monkeypatch, tmp_path):
    monkeypatch.delenv("APOLLO_NIGHTLY_OCR_RAG_DIR", raising=False)
    resolved = nightly_pipeline._nightly_ocr_rag_dir(tmp_path / "apollo_background_current")
    assert resolved == (tmp_path / "apollo_background_current" / "nightly_ocr_rag").resolve()


def test_ocr_backend_readiness_includes_lane_scorecard(monkeypatch):
    monkeypatch.setenv("APOLLO_GB10_OCR_GATEWAY_URL", "http://127.0.0.1:5221")
    monkeypatch.setattr(
        "common.ocr_dual_tool.gb10_ocr_backend_readiness",
        lambda payload=None: {
            "ready": True,
            "mode": "gateway",
            "fail_reason": "",
            "checks": {"gateway_health": {"ok": True}, "gateway_extract": {"ok": True}},
        },
    )

    class _Resp:
        ok = True

        @staticmethod
        def json():
            return {
                "smoke": {"pass": True, "last_run": "2026-04-21T00:00:00Z"},
                "engine_details": {
                    "mineru2_5": {
                        "ready": True,
                        "health_ready": True,
                        "reason": "worker_ready",
                        "lane_signature_modes": ["mineru_native_api", "mineru_cli_fallback"],
                        "slo": {"timeout_rate": 0.0, "fallback_rate": 0.0, "p95_latency_ms": 1200, "last_24h_count": 4},
                        "ready_gate": {"pass": True},
                        "smoke": {"pass": True},
                    },
                    "olmocr2": {
                        "ready": True,
                        "health_ready": True,
                        "reason": "worker_ready",
                        "lane_signature_modes": ["olmocr_native_api", "olmocr_cli_fallback"],
                        "slo": {"timeout_rate": 0.0, "fallback_rate": 0.0, "p95_latency_ms": 1300, "last_24h_count": 4},
                        "ready_gate": {"pass": True},
                        "smoke": {"pass": True},
                    },
                },
            }

    monkeypatch.setattr(nightly_pipeline.requests, "get", lambda url, timeout=6: _Resp())
    config = _pipeline_config({})
    readiness = nightly_pipeline._ocr_backend_readiness({})
    assert readiness["ready"] is True
    assert "lane_scorecard" in readiness
    assert "mineru2_5" in readiness["lane_scorecard"]
    assert readiness["slo_summary"]["total"] >= 1
    assert config["gather_min_accepted_sources"] >= 1
    assert config["gather_min_avg_score"] >= 1
    assert 0.0 <= config["gather_min_trusted_ratio"] <= 1.0
    assert config["gather_min_doc_sources"] >= 0
    assert config["gather_min_pdf_sources"] >= 0
    assert config["ocr_doc_min_chars"] >= 100
    assert config["ocr_avg_min_chars"] >= 100
    assert 0.0 <= config["ocr_min_confidence"] <= 1.0
    assert 0.0 <= config["ocr_min_non_scrape_ratio"] <= 1.0
    assert 0.0 <= config["ocr_min_structure_score"] <= 1.0
    assert 0.0 <= config["ocr_min_numeric_fidelity"] <= 1.0
    assert config["ocr_route_mode"] in {"quality_first", "balanced"}
    assert isinstance(config["ocr_qwen_primary"], bool)
    assert config["ocr_stage_min_score"] >= 1
    assert config["strict_stage_min_score"] >= 1


def test_pipeline_config_preserves_zero_background_overrides():
    config = _pipeline_config(
        {
            "run_mode": "background",
            "background_min_idle_sec": 0,
            "background_max_active_models": 0,
            "background_gather_timeout_sec": 181,
            "background_ocr_step_timeout_sec": 301,
            "background_hipporag_step_timeout_sec": 901,
            "background_self_check_timeout_sec": 121,
        }
    )
    assert config["background_min_idle_sec"] == 0
    assert config["background_max_active_models"] == 0
    assert config["background_gather_timeout_sec"] == 181
    assert config["background_ocr_step_timeout_sec"] == 301
    assert config["background_hipporag_step_timeout_sec"] == 901
    assert config["background_self_check_timeout_sec"] == 121


def test_pipeline_config_clamps_gather_thresholds_to_max_articles():
    config = _pipeline_config(
        {
            "max_articles": 3,
            "gather_min_accepted_sources": 4,
            "gather_min_doc_sources": 3,
            "gather_min_pdf_sources": 2,
        }
    )
    assert config["max_articles"] == 3
    assert config["gather_min_accepted_sources"] == 3
    assert config["gather_min_doc_sources"] == 3
    assert config["gather_min_pdf_sources"] == 2


def test_apply_gather_quality_gate_rejects_noise_sources():
    config = _pipeline_config(
            {
                "max_articles": 3,
                "gather_min_accepted_sources": 2,
                "gather_min_avg_score": 50,
                "gather_min_doc_sources": 0,
                "gather_min_pdf_sources": 0,
                "gather_min_trusted_ratio": 0.5,
            }
        )
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "PATTERN Definition - Merriam-Webster",
                "snippet": "dictionary definition",
                "url": "https://www.merriam-webster.com/dictionary/pattern",
            },
            {
                "title": "FINRA Rule 4210 Margin Requirements",
                "snippet": "Pattern day trader and margin requirements summary",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
            },
            {
                "title": "SEC Investor Bulletin: Margin Accounts",
                "snippet": "Day trading and leverage risks",
                "url": "https://www.sec.gov/oiea/investor-alerts-and-bulletins/margin-accounts",
            },
        ],
        "sources": [
            {"title": "Pattern Definition", "url": "https://www.merriam-webster.com/dictionary/pattern", "why": "picked"},
            {"title": "Rule 4210", "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210", "why": "picked"},
            {"title": "Margin Bulletin", "url": "https://www.sec.gov/oiea/investor-alerts-and-bulletins/margin-accounts", "why": "picked"},
        ],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(result, config)
    urls = [item["url"] for item in gated.get("sources") or []]
    assert "https://www.merriam-webster.com/dictionary/pattern" not in urls
    assert len(urls) >= 2
    assert gated["quality"]["accepted_count"] >= 2


def test_apply_gather_quality_gate_fails_homepage_heavy_candidates():
    config = _pipeline_config(
        {
            "max_articles": 4,
            "gather_min_accepted_sources": 4,
            "gather_min_avg_score": 75,
            "gather_min_doc_sources": 3,
            "gather_min_pdf_sources": 2,
            "gather_min_trusted_ratio": 0.75,
        }
    )
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {"title": "Google Finance", "snippet": "market quotes and news", "url": "https://www.google.com/finance/"},
            {"title": "CNN Markets", "snippet": "us markets", "url": "https://www.cnn.com/markets/"},
            {"title": "SEC filing", "snippet": "margin requirements pdf", "url": "https://www.sec.gov/files/day-trading-bulletin.pdf"},
            {"title": "FINRA rule", "snippet": "pattern day trader", "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210"},
        ],
        "sources": [
            {"title": "Google Finance", "url": "https://www.google.com/finance/", "why": "picked"},
            {"title": "CNN Markets", "url": "https://www.cnn.com/markets/", "why": "picked"},
            {"title": "SEC filing", "url": "https://www.sec.gov/files/day-trading-bulletin.pdf", "why": "picked"},
            {"title": "FINRA rule", "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210", "why": "picked"},
        ],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(result, config)
    assert gated["ok"] is False
    assert any(str(err).startswith("gather_quality_failed:accepted_sources") for err in gated.get("errors") or [])
    assert int((gated.get("quality") or {}).get("accepted_count") or 0) < 4


def test_apply_gather_quality_gate_accepts_local_seed_pdf_sources(tmp_path):
    seed_pdf = tmp_path / "seed_stock_trading_margin.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed")
    config = _pipeline_config(
        {
            "max_articles": 1,
            "gather_min_accepted_sources": 1,
            "gather_min_avg_score": 55,
            "gather_min_doc_sources": 1,
            "gather_min_pdf_sources": 1,
            "gather_min_trusted_ratio": 0.5,
        }
    )
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "Seed day trading margin risk controls PDF",
                "snippet": "stock trading day trading margin risk management position sizing stop loss",
                "url": str(seed_pdf),
            }
        ],
        "sources": [{"title": "Seed day trading margin risk controls PDF", "url": str(seed_pdf), "why": "seed"}],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(result, config)
    assert gated["ok"] is True
    assert int((gated.get("quality") or {}).get("accepted_count") or 0) == 1
    accepted = list((gated.get("quality") or {}).get("accepted_sources") or [])
    assert accepted and accepted[0].get("tier") in {"A", "B"}


def test_evaluate_ocr_quality_gates_on_depth():
    config = _pipeline_config({"ocr_doc_min_chars": 600, "ocr_avg_min_chars": 1200})
    weak = _evaluate_ocr_quality(
        {
            "documents": [
                {"text_chars": 78, "extract_engine": "scrape", "semantic_section_hits": 0, "structure_score": 0.1, "numeric_fidelity_score": 0.1},
                {"text_chars": 640, "extract_engine": "scrape", "semantic_section_hits": 1, "structure_score": 0.2, "numeric_fidelity_score": 0.2},
            ]
        },
        config,
    )
    assert weak["gate_pass"] is False
    assert "ocr_unverified_scrape_only" in weak["gate_fail_reasons"]

    strong = _evaluate_ocr_quality(
        {
            "documents": [
                {
                    "text_chars": 1800,
                    "extract_engine": "gb10_paddleocr_vl",
                    "semantic_section_hits": 3,
                    "structure_score": 0.78,
                    "numeric_fidelity_score": 0.71,
                    "table_rows": 5,
                    "table_like_content": True,
                    "extract_attempts": [{"engine": "gb10_paddleocr_vl", "ok": True}],
                },
                {
                    "text_chars": 1400,
                    "extract_engine": "gb10_qwen_ocr",
                    "semantic_section_hits": 2,
                    "structure_score": 0.72,
                    "numeric_fidelity_score": 0.68,
                    "table_rows": 3,
                    "table_like_content": True,
                    "extract_attempts": [{"engine": "gb10_qwen_ocr", "ok": True}],
                },
            ]
        },
        config,
    )
    assert strong["gate_pass"] is True
    assert strong["benchmark_proxy_scores"]["structure_lane"] >= config["ocr_structure_bench_min"]
    assert strong["benchmark_proxy_scores"]["correctness_lane"] >= config["ocr_correctness_bench_min"]


def test_evaluate_ocr_quality_fails_when_backend_not_ready():
    config = _pipeline_config({})
    quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "documents": [],
            "readiness": {"ready": False, "mode": "unavailable", "fail_reason": "gateway_and_direct_unconfigured"},
        },
        config,
    )
    assert quality["gate_pass"] is False
    assert any(str(reason).startswith("ocr_backend_not_ready:") for reason in quality["gate_fail_reasons"])
    assert quality["backend_ready"] is False
    assert quality["backend_mode"] == "unavailable"


def test_evaluate_ocr_quality_accepts_native_pdf_text_when_visual_backend_unavailable():
    config = _pipeline_config({"ocr_doc_min_chars": 600, "ocr_avg_min_chars": 1200})
    quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "documents": [
                {
                    "text_chars": 2200,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "semantic_section_hits": 3,
                    "structure_score": 0.8,
                    "numeric_fidelity_score": 0.78,
                    "table_rows": 4,
                    "table_like_content": True,
                    "visual_ocr_required": False,
                    "visual_ocr_available": False,
                    "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                }
            ],
            "readiness": {
                "ready": False,
                "mode": "unavailable",
                "fail_reason": "qwen_primary_unready:non_vision_model_configured",
                "visual_ready": False,
                "visual_mode": "unavailable",
                "visual_fail_reason": "qwen_primary_unready:non_vision_model_configured",
                "native_pdf_supported": True,
            },
        },
        config,
    )
    assert quality["gate_pass"] is True
    assert quality["visual_ocr_ready"] is False
    assert quality["native_pdf_supported"] is True
    assert quality["extraction_mode_mix"] == ["native_pdf_text"]


def test_evaluate_ocr_quality_rejects_scrape_only_even_with_high_chars():
    config = _pipeline_config({"ocr_doc_min_chars": 600, "ocr_avg_min_chars": 1200})
    quality = _evaluate_ocr_quality(
        {
            "documents": [
                {
                    "text_chars": 6100,
                    "extract_engine": "html_scrape",
                    "extraction_mode": "html_scrape",
                    "source_doc_class": "web_page",
                    "semantic_section_hits": 3,
                    "structure_score": 0.84,
                    "numeric_fidelity_score": 0.8,
                    "table_rows": 4,
                    "table_like_content": True,
                    "extract_attempts": [{"engine": "html_scrape", "ok": True}],
                }
            ]
        },
        config,
    )
    assert quality["gate_pass"] is False
    assert "ocr_unverified_scrape_only" in quality["gate_fail_reasons"]
    assert quality["scrape_only_detected"] is True


def test_simulated_noisy_fixture_fails_gather_before_ocr():
    config = _pipeline_config(
        {
            "max_articles": 4,
            "gather_min_accepted_sources": 4,
            "gather_min_avg_score": 75,
            "gather_min_doc_sources": 3,
            "gather_min_pdf_sources": 2,
            "gather_min_trusted_ratio": 0.75,
        }
    )
    gather_payload = {
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {"title": "Pattern Definition", "snippet": "dictionary", "url": "https://www.merriam-webster.com/dictionary/pattern"},
            {"title": "Google Finance", "snippet": "markets", "url": "https://www.google.com/finance/"},
            {"title": "Forum Post", "snippet": "trade idea", "url": "https://forum.intraday.my/threads/x.1/"},
            {"title": "CNN Markets", "snippet": "headlines", "url": "https://www.cnn.com/markets/"},
        ],
        "sources": [
            {"title": "Pattern Definition", "url": "https://www.merriam-webster.com/dictionary/pattern", "why": "picked"},
            {"title": "Google Finance", "url": "https://www.google.com/finance/", "why": "picked"},
        ],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(gather_payload, config)
    assert gated["ok"] is False
    assert int((gated.get("quality") or {}).get("accepted_count") or 0) == 0
    assert any(str(err).startswith("gather_quality_failed") for err in gated.get("errors") or [])


def test_simulated_valid_fixture_passes_gather_and_ocr_quality():
    config = _pipeline_config(
        {
            "max_articles": 4,
            "gather_min_accepted_sources": 4,
            "gather_min_avg_score": 75,
            "gather_min_doc_sources": 3,
            "gather_min_pdf_sources": 2,
            "gather_min_trusted_ratio": 0.75,
            "ocr_doc_min_chars": 600,
            "ocr_avg_min_chars": 1500,
            "ocr_min_non_scrape_ratio": 0.85,
            "ocr_structure_bench_min": 80,
            "ocr_correctness_bench_min": 82,
        }
    )
    gather_payload = {
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {"title": "FINRA Rule 4210 day trading margin", "snippet": "pattern day trading margin requirements pdf", "url": "https://www.finra.org/sites/default/files/Rule4210.pdf"},
            {"title": "SEC Margin Bulletin", "snippet": "investor bulletin day trading margin", "url": "https://www.sec.gov/files/day-trading-margin.pdf"},
            {"title": "Federal Reserve paper on stock trading execution", "snippet": "day trading execution quality market microstructure and slippage", "url": "https://www.federalreserve.gov/pubs/microstructure.pdf"},
            {"title": "NBER intraday stock trading transaction costs", "snippet": "intraday day trading strategy transaction costs and risk management", "url": "https://www.nber.org/system/files/working_papers/w12345.pdf"},
        ],
        "sources": [
            {"title": "FINRA Rule 4210 day trading margin", "url": "https://www.finra.org/sites/default/files/Rule4210.pdf", "why": "seed"},
            {"title": "SEC Margin Bulletin", "url": "https://www.sec.gov/files/day-trading-margin.pdf", "why": "seed"},
            {"title": "Federal Reserve paper", "url": "https://www.federalreserve.gov/pubs/microstructure.pdf", "why": "seed"},
            {"title": "NBER execution costs", "url": "https://www.nber.org/system/files/working_papers/w12345.pdf", "why": "seed"},
        ],
        "errors": [],
    }
    gather_gated = _apply_gather_quality_gate(gather_payload, config)
    assert gather_gated["ok"] is True
    ocr_quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "readiness": {"ready": True, "mode": "gateway", "fail_reason": ""},
            "documents": [
                {
                    "text_chars": 2200,
                    "extract_engine": "gb10_paddleocr_vl",
                    "semantic_section_hits": 4,
                    "structure_score": 0.86,
                    "numeric_fidelity_score": 0.83,
                    "table_rows": 8,
                    "table_like_content": True,
                    "extract_attempts": [{"engine": "gb10_paddleocr_vl", "ok": True}],
                },
                {
                    "text_chars": 1700,
                    "extract_engine": "gb10_qwen_ocr",
                    "semantic_section_hits": 3,
                    "structure_score": 0.8,
                    "numeric_fidelity_score": 0.79,
                    "table_rows": 4,
                    "table_like_content": True,
                    "extract_attempts": [{"engine": "gb10_qwen_ocr", "ok": True}],
                },
            ],
        },
        config,
    )
    assert ocr_quality["gate_pass"] is True
    assert ocr_quality["non_scrape_ratio"] >= 0.85


def test_evaluate_hipporag_quality_requires_triples_when_processing():
    config = _pipeline_config({})
    weak = _evaluate_hipporag_quality(
        {"remaining_before": 100, "remaining_after": 92, "processed": 8, "triples": 0, "completed": False},
        config,
    )
    assert weak["gate_pass"] is False
    assert "hipporag_zero_triples" in weak["fail_reasons"]

    strong = _evaluate_hipporag_quality(
        {"remaining_before": 100, "remaining_after": 92, "processed": 8, "triples": 12, "completed": False},
        config,
    )
    assert strong["gate_pass"] is True


def test_evaluate_hipporag_quality_requires_finance_progress_when_chunks_remain():
    config = _pipeline_config({})
    quality = _evaluate_hipporag_quality(
        {
            "remaining_before": 8,
            "remaining_after": 8,
            "processed": 0,
            "processed_finance": 0,
            "skipped_non_finance": 8,
            "requeued_low_quality": 8,
            "triples": 0,
            "completed": False,
        },
        config,
    )
    assert quality["gate_pass"] is False
    assert "hipporag_no_progress" in quality["fail_reasons"]
    assert quality["explicit_no_work"] is False


def test_compute_quality_uses_strict_stage_gate():
    config = _pipeline_config({"strict_stage_min_score": 70})
    status = {
        "stages": {
            "gather": {"quality": {"accepted_count": 2, "avg_score": 85, "trusted_count": 2, "pdf_count": 1}},
            "ocr": {"documents": [{"text_chars": 1800, "extract_engine": "gb10_paddleocr_vl"}], "quality": {"gate_pass": True}},
            "hipporag": {"remaining_before": 10, "remaining_after": 8, "processed": 2, "triples": 0, "completed": False},
            "self_check": {"ok": True},
        }
    }
    quality = _compute_quality(status, config)
    assert quality["gate_pass"] is False
    assert any(str(reason).startswith("hipporag_score_below_min") for reason in quality["gate_fail_reasons"])


def test_compute_quality_respects_hipporag_quality_gate_floor():
    config = _pipeline_config({"strict_stage_min_score": 70})
    status = {
        "stages": {
            "gather": {"quality": {"accepted_count": 2, "avg_score": 84, "trusted_count": 2, "pdf_count": 1}},
            "ocr": {
                "documents": [{"text_chars": 1800, "extract_engine": "gb10_paddleocr_vl", "structure_score": 0.8, "numeric_fidelity_score": 0.8}],
                "quality": {
                    "gate_pass": True,
                    "non_scrape_ratio": 1.0,
                    "avg_structure_score": 0.8,
                    "avg_numeric_fidelity": 0.8,
                    "benchmark_proxy_scores": {"structure_lane": 88, "correctness_lane": 89},
                },
            },
            "hipporag": {
                "remaining_before": 3,
                "remaining_after": 0,
                "processed": 3,
                "triples": 2,
                "completed": True,
                "quality": {"gate_pass": True},
            },
            "self_check": {"ok": True},
        }
    }
    quality = _compute_quality(status, config)
    assert quality["stage_scores"]["hipporag"] >= 70
    assert quality["gate_pass"] is True


def test_compute_quality_fails_when_hipporag_only_requeues_non_finance():
    config = _pipeline_config({"strict_stage_min_score": 70})
    status = {
        "stages": {
            "gather": {"quality": {"accepted_count": 2, "avg_score": 80, "trusted_count": 2, "pdf_count": 1}},
            "ocr": {
                "documents": [{"text_chars": 2000, "extract_engine": "gb10_paddleocr_vl", "structure_score": 0.82, "numeric_fidelity_score": 0.84}],
                "quality": {
                    "gate_pass": True,
                    "non_scrape_ratio": 1.0,
                    "avg_structure_score": 0.82,
                    "avg_numeric_fidelity": 0.84,
                    "benchmark_proxy_scores": {"structure_lane": 90, "correctness_lane": 90},
                },
            },
            "hipporag": {
                "remaining_before": 8,
                "remaining_after": 8,
                "processed": 0,
                "processed_finance": 0,
                "skipped_non_finance": 8,
                "requeued_low_quality": 8,
                "triples": 0,
                "completed": False,
                "quality": {"gate_pass": False, "fail_reasons": ["hipporag_no_progress"]},
            },
            "self_check": {"ok": True},
        }
    }
    quality = _compute_quality(status, config)
    assert quality["stage_scores"]["hipporag"] < 70
    assert quality["gate_pass"] is False
    assert any(str(reason).startswith("hipporag_quality_gate:") for reason in quality["gate_fail_reasons"])


def test_enrich_status_applies_strict_gate_to_completed_run(tmp_path):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    config = _pipeline_config({"run_mode": "background", "strict_stage_min_score": 70})
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    status = {
        "run_id": "apollo_background_current",
        "run_dir": str(run_dir),
        "run_mode": "background",
        "started_at": "2026-04-17T00:00:00Z",
        "finished_at": "2026-04-17T01:00:00Z",
        "current_stage": "done",
        "stages": {
            "gather": {"completed": True, "quality": {"accepted_count": 2, "avg_score": 84, "trusted_count": 2, "pdf_count": 1}},
            "ocr": {"completed": True, "documents": [{"text_chars": 1700, "extract_engine": "gb10_paddleocr_vl"}]},
            "hipporag": {"completed": True, "remaining_before": 8, "remaining_after": 0, "processed": 8, "triples": 0},
            "self_check": {"completed": True, "ok": True},
        },
    }
    enriched = _enrich_status_payload(run_dir, status)
    assert enriched["quality"]["gate_pass"] is False
    assert enriched["ok"] is False


def test_stage_ocr_processes_native_pdf_when_visual_backend_not_ready(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True, exist_ok=True)
    gather_path = run_dir / "01_data_gather.json"
    seed_pdf = tmp_path / "seed_margin_controls.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed")
    gather_path.write_text(
        json.dumps(
            {
                "ok": True,
                "completed": True,
                "topic": "stock trading and day trading strategies",
                "sources": [{"url": str(seed_pdf), "title": "Seed bulletin", "why": "seed"}],
            }
        ),
        encoding="utf-8",
    )
    calls = {"count": 0}

    def fake_process(item, *, topic, config, rag_store_inst, readiness, seen_fingerprints=None):
        calls["count"] += 1
        return {
            "document": {
                "url": str(seed_pdf),
                "title": "Seed bulletin",
                "why": "seed",
                "extract_engine": "native_pdf_text",
                "extraction_mode": "native_pdf_text",
                "source_doc_class": "pdf",
                "text_chars": 2400,
                "semantic_section_hits": 3,
                "structure_score": 0.81,
                "numeric_fidelity_score": 0.79,
                "table_rows": 4,
                "table_like_content": True,
                "visual_ocr_required": False,
                "visual_ocr_available": False,
                "fallback_used": False,
                "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                "route_mode": "quality_first",
                "engine_chain": ["native_pdf_text"],
                "blocks_count": 0,
                "tables_count": 0,
                "reading_order_count": 0,
                "bbox_count": 0,
                "benchmark_proxy_scores": {"structure_lane": 88, "correctness_lane": 86},
                "ok": True,
                "ingested_id": "doc-1",
            },
            "errors": [],
        }

    monkeypatch.setattr(
        nightly_pipeline,
        "_ocr_backend_readiness",
        lambda config: {
            "ready": False,
            "mode": "unavailable",
            "fail_reason": "qwen_primary_unready:non_vision_model_configured",
            "visual_ready": False,
            "visual_mode": "unavailable",
            "visual_fail_reason": "qwen_primary_unready:non_vision_model_configured",
            "native_pdf_supported": True,
        },
    )
    monkeypatch.setattr(nightly_pipeline, "_process_ocr_source", fake_process)
    monkeypatch.setenv("APOLLO_OCR_MIN_EVALUATED_DOCS_FOR_CONFIDENCE", "1")
    config = _pipeline_config({"run_mode": "background", "batch_id": "apollo_background_current"})
    result = nightly_pipeline._stage_ocr(run_dir, config)
    assert calls["count"] == 1
    assert result["ok"] is True
    assert result["completed"] is True
    assert result["readiness"]["ready"] is False


def test_stage_ocr_uses_dedicated_nightly_rag_store(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True, exist_ok=True)
    seed_pdf = tmp_path / "seed_margin_controls.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed")
    (run_dir / "01_data_gather.json").write_text(
        json.dumps(
            {
                "ok": True,
                "completed": True,
                "topic": "stock trading and day trading strategies",
                "sources": [{"url": str(seed_pdf), "title": "Seed bulletin", "why": "seed"}],
            }
        ),
        encoding="utf-8",
    )
    sentinel_rag = object()
    seen = {"same": False}

    def fake_process(item, *, topic, config, rag_store_inst, readiness, seen_fingerprints=None):
        seen["same"] = rag_store_inst is sentinel_rag
        return {
            "document": {
                "url": str(seed_pdf),
                "title": "Seed bulletin",
                "why": "seed",
                "extract_engine": "native_pdf_text",
                "extraction_mode": "native_pdf_text",
                "source_doc_class": "pdf",
                "text_chars": 2400,
                "semantic_section_hits": 3,
                "structure_score": 0.81,
                "numeric_fidelity_score": 0.79,
                "table_rows": 4,
                "table_like_content": True,
                "visual_ocr_required": False,
                "visual_ocr_available": False,
                "fallback_used": False,
                "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                "route_mode": "quality_first",
                "engine_chain": ["native_pdf_text"],
                "blocks_count": 0,
                "tables_count": 0,
                "reading_order_count": 0,
                "bbox_count": 0,
                "benchmark_proxy_scores": {"structure_lane": 88, "correctness_lane": 86},
                "ok": True,
                "ingested_id": "doc-1",
            },
            "errors": [],
        }

    monkeypatch.setattr(nightly_pipeline, "_nightly_ocr_rag", lambda _run_dir: sentinel_rag)
    monkeypatch.setattr(
        nightly_pipeline,
        "_ocr_backend_readiness",
        lambda config: {
            "ready": False,
            "mode": "unavailable",
            "fail_reason": "qwen_primary_unready:non_vision_model_configured",
            "visual_ready": False,
            "visual_mode": "unavailable",
            "visual_fail_reason": "qwen_primary_unready:non_vision_model_configured",
            "native_pdf_supported": True,
        },
    )
    monkeypatch.setattr(nightly_pipeline, "_process_ocr_source", fake_process)
    config = _pipeline_config({"run_mode": "background", "batch_id": "apollo_background_current"})
    result = nightly_pipeline._stage_ocr(run_dir, config)
    assert result["ok"] is True
    assert seen["same"] is True


def test_stage_ocr_tops_up_curated_fallback_when_evidence_depth_is_low(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True, exist_ok=True)
    seed_pdf = tmp_path / "seed_margin_controls.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed")
    (run_dir / "01_data_gather.json").write_text(
        json.dumps(
            {
                "ok": True,
                "completed": True,
                "topic": "stock trading and day trading strategies",
                "sources": [{"url": str(seed_pdf), "title": "Seed bulletin", "why": "seed"}],
            }
        ),
        encoding="utf-8",
    )
    processed_urls: list[str] = []

    def fake_process(item, *, topic, config, rag_store_inst, readiness, seen_fingerprints=None):
        processed_urls.append(str(item.get("url") or ""))
        return {
            "document": {
                "url": str(item.get("url") or ""),
                "title": str(item.get("title") or "Document"),
                "why": str(item.get("why") or ""),
                "extract_engine": "native_pdf_text",
                "source_doc_class": "pdf",
                "text_chars": 2400,
                "semantic_section_hits": 3,
                "structure_score": 0.81,
                "numeric_fidelity_score": 0.79,
                "table_rows": 4,
                "table_like_content": True,
                "ok": True,
                "ingested_id": f"doc-{len(processed_urls)}",
            },
            "errors": [],
        }

    curated = [
        {"url": "https://example.com/report-a.pdf", "title": "Report A", "why": "curated"},
        {"url": "https://example.com/report-b.pdf", "title": "Report B", "why": "curated"},
    ]
    monkeypatch.setenv("APOLLO_OCR_MIN_EVALUATED_DOCS_FOR_CONFIDENCE", "2")
    monkeypatch.setattr(nightly_pipeline, "_nightly_ocr_rag", lambda _run_dir: object())
    monkeypatch.setattr(nightly_pipeline, "_ocr_backend_readiness", lambda config: {"ready": True, "native_pdf_supported": True})
    monkeypatch.setattr(nightly_pipeline, "_process_ocr_source", fake_process)
    monkeypatch.setattr("Apollo.trading_homework._manifest_candidates", lambda topic, max_items=12: curated)
    monkeypatch.setattr("Apollo.trading_homework._looks_like_report_like_url", lambda url: True)
    config = _pipeline_config({"run_mode": "background", "batch_id": "apollo_background_current"})

    result = nightly_pipeline._stage_ocr(run_dir, config)

    assert result["ok"] is True
    assert result["quality"]["evaluated_documents"] >= 2
    assert result["curated_document_fallback"]["attempted"] is True
    assert result["curated_document_fallback"]["reason"] == "ocr_low_evidence_depth"
    assert "https://example.com/report-a.pdf" in processed_urls


def test_stage_ocr_preserves_gather_accepted_macro_pdf_after_triage_rejects(tmp_path, monkeypatch):
    run_dir = tmp_path / "nightly" / "local_only"
    run_dir.mkdir(parents=True, exist_ok=True)
    fed_url = "https://www.federalreserve.gov/publications/files/financial-stability-report-20241122.pdf"
    seed_pdf = tmp_path / "homework_seed_docs" / "finra_margin_risk_playbook.pdf"
    seed_pdf.parent.mkdir(parents=True, exist_ok=True)
    seed_pdf.write_bytes(b"%PDF-1.4\n%seed")
    (run_dir / "01_data_gather.json").write_text(
        json.dumps(
            {
                "ok": True,
                "completed": True,
                "topic": "local-only overnight pipeline proof for FTP",
                "sources": [
                    {"url": fed_url, "title": "Federal Reserve Financial Stability Report November 2024", "why": "llm"},
                    {"url": str(seed_pdf), "title": "FINRA margin risk management and overnight-hold controls (seed PDF)", "why": ""},
                ],
                "quality": {
                    "accepted_sources": [
                        {
                            "url": fed_url,
                            "title": "Federal Reserve Financial Stability Report November 2024",
                            "why": "llm",
                            "is_pdf": True,
                            "is_doc_source": True,
                        },
                        {
                            "url": str(seed_pdf),
                            "title": "FINRA margin risk management and overnight-hold controls (seed PDF)",
                            "is_pdf": True,
                            "is_doc_source": True,
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    processed_urls: list[str] = []

    def fake_triage(sources, tickers, topic="", min_relevance=0.35):
        return {"accepted_direct": [], "accepted_ocr": [], "rejected": list(sources), "summary": {"rejected": len(sources)}}

    def fake_process(item, *, topic, config, rag_store_inst, readiness, seen_fingerprints=None):
        url = str(item.get("url") or "")
        processed_urls.append(url)
        if url == fed_url:
            return {
                "document": {
                    "url": url,
                    "title": str(item.get("title") or ""),
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "text_chars": 2600,
                    "semantic_section_hits": 4,
                    "structure_score": 0.62,
                    "numeric_fidelity_score": 0.7,
                    "table_rows": 2,
                    "table_like_content": True,
                    "quality_scan_keep": True,
                    "quality_scan_reason": "kept_macro_document_anchor",
                    "topic_anchor_profile": "macro_market_context",
                    "document_lane": "macro_context",
                    "visual_ocr_required": False,
                    "visual_ocr_available": True,
                    "fallback_used": False,
                    "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                    "route_mode": "quality_first",
                    "engine_chain": ["native_pdf_text"],
                    "blocks_count": 4,
                    "tables_count": 1,
                    "reading_order_count": 4,
                    "bbox_count": 0,
                    "ok": True,
                    "ingested_id": "fed-doc",
                },
                "errors": [],
            }
        return {
            "document": {
                "url": url,
                "title": str(item.get("title") or ""),
                "seed_fallback_source": True,
                "extract_engine": "gb10_qwen_ocr",
                "extraction_mode": "visual_ocr",
                "source_doc_class": "pdf",
                "text_chars": 15,
                "quality_scan_keep": False,
                "quality_scan_reason": "chars_below_scan_min:15<700",
                "ok": True,
            },
            "errors": ["ocr_quality_scan_rejected:chars_below_scan_min:15<700:" + url],
        }

    monkeypatch.setattr("Apollo.document_triage.triage_sources", fake_triage)
    monkeypatch.setattr(nightly_pipeline, "_nightly_ocr_rag", lambda _run_dir: object())
    monkeypatch.setattr(nightly_pipeline, "_ocr_backend_readiness", lambda config: {"ready": True, "visual_ready": True, "native_pdf_supported": True})
    monkeypatch.setattr(nightly_pipeline, "_process_ocr_source", fake_process)
    monkeypatch.setenv("APOLLO_OCR_MIN_EVALUATED_DOCS_FOR_CONFIDENCE", "1")
    config = _pipeline_config({"run_mode": "local_only", "batch_id": "local_only", "max_articles": 2})

    result = nightly_pipeline._stage_ocr(run_dir, config)

    assert fed_url in processed_urls
    assert result["ok"] is True
    assert result["quality"]["evaluated_documents"] == 1
    assert result["quality"]["failure_class"] == "passed"


def test_background_stage_gather_preserves_failed_completion_flag(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        nightly_pipeline,
        "_stage_gather",
        lambda run_dir_arg, config_arg: {"ok": False, "completed": False, "errors": ["gather_quality_failed:accepted_sources:0<4"]},
    )
    config = _pipeline_config({"run_mode": "background", "batch_id": "apollo_background_current"})
    stage_payload = nightly_pipeline.run_background_stage("gather", run_dir, config)
    assert stage_payload["ok"] is False
    assert stage_payload["completed"] is False


def test_background_stage_gather_caps_inner_llm_timeout(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}

    def fake_gather_sources(_payload):
        seen["timeout"] = str(os.getenv("APOLLO_HOMEWORK_LLM_TIMEOUT_SECS") or "")
        return {"ok": True, "completed": True, "sources": [], "candidates": [], "errors": []}

    monkeypatch.setattr("Apollo.trading_homework.gather_trading_homework_sources", fake_gather_sources)
    monkeypatch.setattr(nightly_pipeline, "_select_focus_universe", lambda config: {})
    monkeypatch.setattr(
        nightly_pipeline,
        "_apply_gather_quality_gate",
        lambda result, config, **kwargs: {**result, "ok": True, "completed": True, "quality": {"gate_pass": True}},
    )

    config = _pipeline_config(
        {
            "run_mode": "background",
            "batch_id": "apollo_background_current",
            "homework_llm_timeout_sec": 480,
            "background_gather_timeout_sec": 180,
        }
    )
    result = nightly_pipeline._stage_gather(run_dir, config)

    assert result["ok"] is True
    assert seen["timeout"] == "120"


def test_status_payload_includes_ocr_readiness_and_gather_pdf_fields(tmp_path):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True, exist_ok=True)
    config = _pipeline_config({"run_mode": "background", "batch_id": "apollo_background_current"})
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    status = {
        "ok": False,
        "run_id": "apollo_background_current",
        "run_dir": str(run_dir),
        "run_mode": "background",
        "started_at": "2026-04-19T00:00:00Z",
        "stages": {
            "gather": {
                "completed": True,
                "ok": True,
                "quality": {
                    "accepted_count": 4,
                    "rejected_count": 3,
                    "avg_score": 82,
                    "trusted_count": 3,
                    "trusted_ratio": 0.75,
                    "doc_sources": 3,
                    "pdf_count": 2,
                    "reject_reason_counts": {"homepage": 2},
                    "tier_breakdown": {"accepted": {"A": 2, "B": 1, "C": 1, "D": 0}, "rejected": {"A": 0, "B": 0, "C": 2, "D": 1}},
                },
            },
            "ocr": {
                "completed": False,
                "ok": False,
                "readiness": {"ready": False, "mode": "unavailable", "fail_reason": "gateway_and_direct_unconfigured"},
                "quality": {
                    "backend_ready": False,
                    "backend_mode": "unavailable",
                    "backend_fail_reason": "gateway_and_direct_unconfigured",
                    "visual_ocr_ready": False,
                    "visual_ocr_mode": "unavailable",
                    "native_pdf_supported": True,
                    "non_scrape_ratio": 0.0,
                    "engine_mix": [],
                    "extraction_mode_mix": [],
                    "document_class_mix": [],
                    "scrape_only_detected": False,
                    "benchmark_proxy_scores": {"structure_lane": 0, "correctness_lane": 0},
                    "gate_fail_reasons": ["ocr_backend_not_ready:gateway_and_direct_unconfigured"],
                    "quality_breakdown": {"backend_ready": False},
                },
                "documents": [],
            },
            "hipporag": {"completed": False, "ok": False},
            "self_check": {"completed": False, "ok": False},
        },
        "current_stage": "ocr",
    }
    enriched = _enrich_status_payload(run_dir, status)
    assert "readiness" in (enriched.get("stages") or {}).get("ocr", {})
    assert enriched["stage_details"]["gather"]["pdf_sources"] == 2
    assert enriched["stage_details"]["ocr"]["backend_ready"] is False
    assert enriched["ocr"]["backend_mode"] == "unavailable"
    assert "visual_ocr_ready" in enriched["ocr"]
    assert "extraction_mode_mix" in enriched["ocr"]
    assert "reject_reason_counts" in enriched["gather"]


def test_stage_details_include_corpus_real_ratio(monkeypatch):
    monkeypatch.setattr(
        "Apollo.corpus_bootstrap.audit_financial_corpus",
        lambda payload=None: {
            "ok": True,
            "total_chunks": 200,
            "real_chunk_count": 60,
            "synthetic_chunk_count": 130,
            "seed_local_chunk_count": 10,
            "real_ratio": 0.3,
            "top_real_sources": [{"source": "sec.gov", "chunks": 25}],
        },
    )
    details = _build_stage_details({"stages": {}})
    assert details["corpus"]["total_chunks"] == 200
    assert details["corpus"]["real_chunk_count"] == 60
    assert details["corpus"]["real_ratio"] == 0.3


def test_truthful_pipeline_report_uses_status_values(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    status = {
        "run_id": "apollo_background_current",
        "started_at": "2026-04-17T00:00:00Z",
        "finished_at": "2026-04-17T00:10:00Z",
        "run_mode": "background",
        "quality": {
            "gate_mode": "strict_each_stage",
            "score_basis": "current_run_only",
            "gate_pass": False,
            "overall_score": 48,
            "stage_scores": {"gather": 38, "ocr": 63, "hipporag": 7, "self_check": 85},
            "gate_fail_reasons": ["hipporag_score_below_min:7<70"],
        },
        "stage_details": {
            "gather": {"accepted_sources": 1, "rejected_candidates": 8, "avg_score": 42.0},
            "ocr": {"documents": 2, "ingested": 2, "avg_chars": 361, "min_chars": 78},
            "hipporag": {"processed": 8, "total_chunks": 620, "triples": 0, "remaining_after": 603, "completed": False},
            "self_check": {"ok": True, "check_count": 3},
        },
        "stages": {
            "gather": {"topic": "stock trading and day trading strategies", "sources": [{"title": "Sample", "url": "https://example.com/sample"}]},
            "ocr": {"documents": [{"title": "Sample", "text_chars": 78, "extract_engine": "scrape", "quality_flag": "low_chars"}]},
            "hipporag": {"stats": {"nodes": 19, "edges": 30, "indexed_docs": 17}},
        },
        "gateway_probe": {
            "ok": True,
            "score": 88,
            "docs_probed": 2,
            "success_count": 2,
            "non_generic_count": 2,
            "unique_fingerprint_count": 2,
            "artifact_path": "C:/tmp/05_aegis_gateway_probe.json",
        },
    }
    _write_truthful_pipeline_report(run_dir, status)
    report = (run_dir / "pipeline_report.md").read_text(encoding="utf-8")
    assert "overall_score: `48`" in report
    assert "nodes: `19`" in report
    assert "edges: `30`" in report
    assert "hipporag_score_below_min:7<70" in report
    assert "## Aegis Gateway Probe" in report
    assert "score: `88`" in report


def test_run_aegis_gateway_probe_writes_artifact_and_scores_distinct_results(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    doc_a = tmp_path / "doc_a.pdf"
    doc_b = tmp_path / "doc_b.pdf"
    doc_a.write_bytes(b"%PDF-1.4\n")
    doc_b.write_bytes(b"%PDF-1.4\n")
    status = {
        "stages": {
            "ocr": {
                "result": {
                    "documents": [
                        {"title": "Doc A", "local_path": str(doc_a)},
                        {"title": "Doc B", "local_path": str(doc_b)},
                    ]
                }
            }
        }
    }

    payloads = {
        str(doc_a.resolve()): {
            "ok": True,
            "engine": "native_pdf_text",
            "model": "",
            "markdown": "# Margin Rules\n| Rule | Value |\n| PDT | 25000 |",
            "quality": {"score": 0.92, "structure_score": 0.80, "numeric_fidelity_score": 0.88, "chars": 48},
        },
        str(doc_b.resolve()): {
            "ok": True,
            "engine": "gb10_qwen_ocr",
            "model": "qwen2.5vl:7b",
            "markdown": "# SEC Execution Study\n| Date | Volume |\n| 2023-01-01 | 1200000 |",
            "quality": {"score": 0.89, "structure_score": 0.76, "numeric_fidelity_score": 0.93, "chars": 67},
        },
    }

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, data=None, headers=None, timeout=None):
        return _Resp(payloads[str(data["path"])])

    monkeypatch.setattr(nightly_pipeline.requests, "post", fake_post)
    result = _run_aegis_gateway_probe(run_dir, status)
    assert result["ok"] is True
    assert result["docs_probed"] == 2
    assert result["success_count"] == 2
    assert result["unique_fingerprint_count"] == 2
    assert result["score"] >= 85
    assert (run_dir / "05_aegis_gateway_probe.json").exists()


def test_run_aegis_gateway_probe_retries_read_timeout_once(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    doc_a = tmp_path / "slow_doc.pdf"
    doc_a.write_bytes(b"%PDF-1.4\n")
    status = {
        "stages": {
            "ocr": {
                "result": {
                    "documents": [
                        {"title": "Slow Doc", "local_path": str(doc_a), "extract_pages": 3, "text_chars": 6207}
                    ]
                }
            }
        }
    }

    calls = {"count": 0, "payloads": []}

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["count"] += 1
        calls["payloads"].append({"timeout": timeout, "prewarm": data.get("prewarm"), "max_pages": data.get("max_pages")})
        if calls["count"] == 1:
            raise nightly_pipeline.requests.exceptions.ReadTimeout("slow")
        return _Resp(
            {
                "ok": True,
                "engine": "gb10_qwen_ocr",
                "model": "qwen2.5vl:7b",
                "markdown": "# SEC Execution Study\n| Date | Volume |\n| 2023-01-01 | 1200000 |",
                "quality": {"score": 0.91, "structure_score": 0.82, "numeric_fidelity_score": 0.94, "chars": 67},
            }
        )

    monkeypatch.setattr(nightly_pipeline.requests, "post", fake_post)
    result = _run_aegis_gateway_probe(run_dir, status)
    assert result["ok"] is True
    assert result["success_count"] == 1
    assert calls["count"] == 2
    assert calls["payloads"][0]["prewarm"] == "1"
    assert calls["payloads"][1]["prewarm"] == "0"
    assert int(calls["payloads"][0]["max_pages"]) == 3


def test_write_json_retries_transient_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    calls = {"count": 0}
    real_replace = nightly_pipeline.os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(nightly_pipeline.os, "replace", flaky_replace)
    _write_json(path, {"ok": True, "value": 7})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["value"] == 7
    assert calls["count"] >= 2


def test_extract_document_cascade_uses_direct_qwen_rescue_when_initial_pdf_attempts_are_too_weak(tmp_path, monkeypatch):
    pdf_path = tmp_path / "weak_scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    config = _pipeline_config({"ocr_doc_min_chars": 600, "ocr_max_pages": 4, "ocr_max_chars": 16000})

    monkeypatch.setattr(nightly_pipeline, "_extract_pdf_native_text", lambda path, max_pages, max_chars: {
        "ok": True,
        "engine": "native_pdf_text",
        "text": ", exit, and stop consistency.",
        "markdown": ", exit, and stop consistency.",
        "pages": 3,
        "confidence": None,
        "reason": "native_text:fitz",
        "quality_score": 18.0,
    })

    monkeypatch.setattr("common.mcp.ensure_loaded", lambda: None)

    def fake_mcp_run(name, payload):
        engine = str(payload.get("engine") or "")
        if engine in {"gb10_auto", "auto"}:
            return {
                "ok": True,
                "engine": "gb10_paddleocr_vl+gb10_qwen_cleanup",
                "text": ", exit, and stop consistency.",
                "markdown": ", exit, and stop consistency.",
                "pages": 3,
                "confidence": None,
                "reason": "document_layout_primary_qwen_cleanup",
                "quality": {"score": 0.09, "structure_score": 0.007, "numeric_fidelity_score": 0.2, "table_rows": 0},
                "attempts": [],
            }
        if engine == "gb10_qwen_ocr":
            return {
                "ok": True,
                "engine": "gb10_qwen_ocr",
                "text": "# 2017 Audited Financial Statements\n| Revenue | $525,674 |",
                "markdown": "# 2017 Audited Financial Statements\n| Revenue | $525,674 |",
                "pages": 3,
                "confidence": None,
                "reason": "gb10_qwen_vlm_ocr",
                "quality": {"score": 0.71, "structure_score": 0.24, "numeric_fidelity_score": 0.82, "table_rows": 1},
                "attempts": [],
            }
        raise AssertionError(f"unexpected engine {engine}")

    monkeypatch.setattr("common.mcp.run", fake_mcp_run)
    result = nightly_pipeline._extract_document_cascade(pdf_path, topic="stock trading and day trading strategies", config=config)
    assert result["meta"]["engine"] == "gb10_qwen_ocr"
    assert "2017 Audited Financial Statements" in result["markdown"]


def test_extract_document_cascade_skips_visual_ocr_when_native_pdf_text_is_strong(tmp_path, monkeypatch):
    pdf_path = tmp_path / "native_good.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    config = _pipeline_config({"ocr_doc_min_chars": 600, "ocr_max_pages": 4, "ocr_max_chars": 16000})

    monkeypatch.setattr(
        nightly_pipeline,
        "_extract_pdf_native_text",
        lambda path, max_pages, max_chars: {
            "ok": True,
            "engine": "native_pdf_text",
            "text": ("Board of Governors monetary policy report with margin, leverage, and trading risk controls.\n" * 20).strip(),
            "markdown": ("Board of Governors monetary policy report with margin, leverage, and trading risk controls.\n" * 20).strip(),
            "pages": 4,
            "confidence": None,
            "reason": "native_text:pypdf",
            "quality_score": 91.0,
        },
    )

    monkeypatch.setattr("common.mcp.ensure_loaded", lambda: (_ for _ in ()).throw(AssertionError("visual OCR should be skipped")))
    monkeypatch.setattr("common.mcp.run", lambda name, payload: (_ for _ in ()).throw(AssertionError("visual OCR should be skipped")))

    result = nightly_pipeline._extract_document_cascade(pdf_path, topic="stock trading and day trading strategies", config=config)

    assert result["meta"]["engine"] == "native_pdf_text"
    assert result["meta"]["extraction_mode"] == "native_pdf_text"
    assert "monetary policy report" in result["markdown"].lower()
    assert len(result["meta"]["attempts"]) == 1


def test_evaluate_ocr_quality_passes_reduced_local_hybrid_evidence_bundle():
    config = _pipeline_config(
        {
            "ocr_doc_min_chars": 600,
            "ocr_avg_min_chars": 1500,
            "ocr_min_numeric_fidelity": 0.5,
            "ocr_min_structure_score": 0.45,
            "ocr_structure_bench_min": 80,
            "ocr_correctness_bench_min": 82,
        }
    )
    quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "readiness": {"ready": True, "mode": "gateway", "visual_ready": True, "visual_mode": "gateway", "native_pdf_supported": True},
            "documents": [
                {
                    "text_chars": 926,
                    "semantic_section_hits": 12,
                    "structure_score": 0.798,
                    "numeric_fidelity_score": 0.526,
                    "table_like_content": True,
                    "table_rows": 8,
                    "tables_count": 0,
                    "blocks_count": 0,
                    "reading_order_count": 0,
                    "bbox_count": 0,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "fallback_used": True,
                    "extract_attempts": [{}, {}],
                },
                {
                    "text_chars": 1851,
                    "semantic_section_hits": 11,
                    "structure_score": 0.674,
                    "numeric_fidelity_score": 0.439,
                    "table_like_content": True,
                    "table_rows": 16,
                    "tables_count": 0,
                    "blocks_count": 0,
                    "reading_order_count": 0,
                    "bbox_count": 0,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "fallback_used": True,
                    "extract_attempts": [{}, {}],
                },
                {
                    "text_chars": 3778,
                    "semantic_section_hits": 4,
                    "structure_score": 0.042,
                    "numeric_fidelity_score": 1.0,
                    "table_like_content": True,
                    "table_rows": 1,
                    "tables_count": 1,
                    "blocks_count": 98,
                    "reading_order_count": 98,
                    "bbox_count": 0,
                    "extract_engine": "gb10_qwen_ocr",
                    "extraction_mode": "visual_ocr",
                    "source_doc_class": "pdf",
                    "fallback_used": True,
                    "extract_attempts": [{}, {}],
                },
            ],
        },
        config,
    )
    assert quality["benchmark_proxy_scores"]["structure_lane"] >= 80
    assert quality["benchmark_proxy_scores"]["correctness_lane"] >= 82
    assert quality["gate_pass"] is True
    assert quality["avg_chars_required_effective"] < quality["avg_chars_required"]


def test_evaluate_ocr_quality_ignores_failed_and_supplemental_html_when_native_pdf_bundle_is_strong():
    config = _pipeline_config(
        {
            "ocr_doc_min_chars": 600,
            "ocr_avg_min_chars": 1500,
            "ocr_min_confidence": 0.45,
            "ocr_min_semantic_hits": 2,
            "ocr_min_non_scrape_ratio": 0.85,
            "ocr_min_structure_score": 0.45,
            "ocr_min_numeric_fidelity": 0.5,
            "ocr_structure_bench_min": 80,
            "ocr_correctness_bench_min": 82,
            "ocr_require_finance_structure": True,
        }
    )
    quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "readiness": {"ready": True, "mode": "gateway", "visual_ready": True, "visual_mode": "gateway", "native_pdf_supported": True},
            "documents": [
                {
                    "url": "https://www.federalreserve.gov/publications/files/20240301_mprfullreport.pdf",
                    "ok": True,
                    "text_chars": 5110,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "semantic_section_hits": 1,
                    "structure_score": 0.235,
                    "numeric_fidelity_score": 0.124,
                    "table_like_content": True,
                    "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                    "engine_chain": ["native_pdf_text"],
                },
                {
                    "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-20250425.pdf",
                    "ok": True,
                    "text_chars": 2375,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "semantic_section_hits": 2,
                    "structure_score": 0.143,
                    "numeric_fidelity_score": 0.213,
                    "table_like_content": False,
                    "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                    "engine_chain": ["native_pdf_text"],
                },
                {
                    "url": "https://www.federalreserve.gov/publications/files/bad.pdf",
                    "ok": False,
                    "text_chars": 0,
                    "extract_engine": None,
                    "extraction_mode": "unknown",
                    "source_doc_class": "pdf",
                    "semantic_section_hits": 0,
                    "structure_score": 0.0,
                    "numeric_fidelity_score": 0.0,
                    "table_like_content": False,
                    "extract_attempts": [],
                },
                {
                    "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
                    "ok": True,
                    "text_chars": 16000,
                    "extract_engine": "html_scrape",
                    "extraction_mode": "html_scrape",
                    "source_doc_class": "web_page",
                    "semantic_section_hits": 6,
                    "structure_score": 0.253,
                    "numeric_fidelity_score": 0.464,
                    "table_like_content": False,
                    "extract_attempts": [{"engine": "html_scrape", "ok": True}],
                    "engine_chain": ["html_scrape"],
                },
            ],
        },
        config,
    )
    assert quality["gate_pass"] is True
    assert quality["native_pdf_bundle"] is True
    assert quality["excluded_failed_docs"] == 1
    assert quality["excluded_supplemental_html_docs"] == 1
    assert quality["benchmark_proxy_scores"]["structure_lane"] >= quality["structure_bench_min_effective"]
    assert quality["benchmark_proxy_scores"]["correctness_lane"] >= quality["correctness_bench_min_effective"]


def test_evaluate_ocr_quality_skips_strict_gate_for_seed_fallback_only_docs():
    config = _pipeline_config(
        {
            "ocr_doc_min_chars": 600,
            "ocr_avg_min_chars": 1500,
            "ocr_min_numeric_fidelity": 0.5,
            "ocr_min_structure_score": 0.45,
            "ocr_structure_bench_min": 80,
            "ocr_correctness_bench_min": 82,
        }
    )
    quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "readiness": {"ready": True, "mode": "gateway", "visual_ready": True, "visual_mode": "gateway", "native_pdf_supported": True},
            "documents": [
                {
                    "local_path": "C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\homework_seed_docs\\finra_margin_risk_playbook.pdf",
                    "seed_fallback_source": True,
                    "text_chars": 6117,
                    "semantic_section_hits": 6,
                    "structure_score": 0.034,
                    "numeric_fidelity_score": 0.425,
                    "table_like_content": False,
                    "table_rows": 0,
                    "tables_count": 0,
                    "blocks_count": 1,
                    "reading_order_count": 1,
                    "bbox_count": 0,
                    "extract_engine": "gb10_qwen_ocr",
                    "extraction_mode": "visual_ocr",
                    "source_doc_class": "pdf",
                    "fallback_used": True,
                    "extract_attempts": [{}, {}],
                }
            ],
        },
        config,
    )
    assert quality["documents"] == 1
    assert quality["evaluated_documents"] == 0
    assert quality["excluded_seed_fallback_docs"] == 1
    assert quality["gate_skipped"] is True
    assert quality["gate_skip_reason"] == "seed_fallback_only"
    assert quality["gate_fail_reasons"] == []
    assert quality["gate_pass"] is True


def test_ocr_quality_scan_rejects_duplicate_content_fingerprint():
    config = _pipeline_config(
        {
            "ocr_scan_enabled": True,
            "ocr_scan_min_chars": 200,
            "ocr_scan_min_quality_score": 20,
            "ocr_scan_min_semantic_hits": 1,
            "ocr_scan_min_structure_score": 0.0,
            "ocr_scan_min_numeric_fidelity": 0.0,
            "ocr_scan_min_anchor_hits": 0,
        }
    )
    doc = {
        "quality_flag": "pass",
        "text_chars": 1200,
        "quality_score": 88.0,
        "semantic_section_hits": 6,
        "structure_score": 0.4,
        "numeric_fidelity_score": 0.7,
    }
    seen: set[str] = set()
    first = nightly_pipeline._ocr_quality_scan_decision(doc, "Revenue grew 14% and margin expanded 120 bps.", config, seen_fingerprints=seen)
    second = nightly_pipeline._ocr_quality_scan_decision(doc, "Revenue grew 14% and margin expanded 120 bps.", config, seen_fingerprints=seen)
    assert first["keep"] is True
    assert second["keep"] is False
    assert second["reason"] == "duplicate_content_fingerprint"


def test_evaluate_ocr_quality_excludes_quality_scan_rejected_docs():
    config = _pipeline_config(
        {
            "ocr_doc_min_chars": 600,
            "ocr_avg_min_chars": 1000,
            "ocr_min_semantic_hits": 1,
            "ocr_min_non_scrape_ratio": 0.5,
            "ocr_min_structure_score": 0.1,
            "ocr_min_numeric_fidelity": 0.1,
            "ocr_structure_bench_min": 10,
            "ocr_correctness_bench_min": 10,
        }
    )
    quality = _evaluate_ocr_quality(
        {
            "topic": "stock trading and day trading strategies",
            "readiness": {"ready": True, "mode": "gateway", "visual_ready": True, "visual_mode": "gateway", "native_pdf_supported": True},
            "documents": [
                {
                    "url": "https://www.sec.gov/files/a.pdf",
                    "ok": True,
                    "quality_scan_keep": False,
                    "text_chars": 2500,
                    "semantic_section_hits": 5,
                    "structure_score": 0.5,
                    "numeric_fidelity_score": 0.8,
                    "table_like_content": True,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                },
                {
                    "url": "https://www.finra.org/files/b.pdf",
                    "ok": True,
                    "quality_scan_keep": True,
                    "text_chars": 2600,
                    "semantic_section_hits": 6,
                    "structure_score": 0.6,
                    "numeric_fidelity_score": 0.7,
                    "table_like_content": True,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "extract_attempts": [{"engine": "native_pdf_text", "ok": True}],
                },
            ],
        },
        config,
    )
    assert quality["excluded_quality_scan_docs"] == 1
    assert quality["documents"] == 1
    assert quality["gate_pass"] is True


def test_evaluate_ocr_quality_marks_rejected_docs_as_no_valid_documents():
    config = _pipeline_config({"topic": "swing trade risk management"})
    quality = _evaluate_ocr_quality(
        {
            "documents": [
                {
                    "ok": True,
                    "text_chars": 2500,
                    "extract_engine": "html_scrape",
                    "extraction_mode": "html_scrape",
                    "source_doc_class": "web_page",
                    "quality_scan_keep": False,
                    "quality_scan_reason": "ocr_unverified_scrape_only",
                }
            ],
            "readiness": {"ready": True, "visual_ready": True, "native_pdf_supported": True},
        },
        config,
    )

    assert quality["gate_pass"] is False
    assert quality["failure_class"] == "no_valid_documents"
    assert "ocr_no_documents" in quality["gate_fail_reasons"]


def test_ocr_quality_scan_rejects_on_topic_anchor_drift():
    """Generic OCR prose that shares no terms with the topic is rejected as extraction drift."""
    config = _pipeline_config(
        {
            "topic": "TSLA earnings options flow",
            "ocr_scan_enabled": True,
            "ocr_scan_min_chars": 200,
            "ocr_scan_min_quality_score": 20,
            "ocr_scan_min_semantic_hits": 1,
            "ocr_scan_min_structure_score": 0.0,
            "ocr_scan_min_numeric_fidelity": 0.0,
            "ocr_scan_min_anchor_hits": 1,
        }
    )
    doc = {
        "quality_flag": "pass",
        "text_chars": 1200,
        "quality_score": 88.0,
        "semantic_section_hits": 6,
        "structure_score": 0.4,
        "numeric_fidelity_score": 0.7,
    }
    # Generic overnight trading prose — passes structural checks but mentions none of the topic terms
    generic_prose = (
        "Overnight trading strategies involve managing risk and leverage. "
        "Entry and exit points must respect drawdown rules. "
        "Capital allocation follows margin requirements across all positions."
    )
    result = nightly_pipeline._ocr_quality_scan_decision(doc, generic_prose, config)
    assert result["keep"] is False
    assert "topic_anchor_retention_failed" in result["reason"]

    # Text that actually mentions the topic passes
    on_topic = (
        "TSLA reported strong earnings with call options flow surging ahead of the print. "
        "Entry at 245, risk defined to 230 with position sized at 2% capital."
    )
    result_on_topic = nightly_pipeline._ocr_quality_scan_decision(doc, on_topic, config)
    assert result_on_topic["keep"] is True


def test_ocr_quality_scan_keeps_macro_market_pdf_with_document_anchor():
    config = _pipeline_config(
        {
            "topic": "NVDA AMD TSM swing trade opportunities",
            "ocr_scan_enabled": True,
            "ocr_scan_min_chars": 200,
            "ocr_scan_min_quality_score": 20,
            "ocr_scan_min_semantic_hits": 1,
            "ocr_scan_min_structure_score": 0.0,
            "ocr_scan_min_numeric_fidelity": 0.0,
            "ocr_scan_min_anchor_hits": 2,
        }
    )
    doc = {
        "title": "BIS Quarterly Review December 2024",
        "url": "https://www.bis.org/publ/qtrpdf/r_qt2412.pdf",
        "source_doc_class": "pdf",
        "extraction_mode": "native_pdf_text",
        "text_chars": 2200,
        "quality_score": 90.0,
        "semantic_section_hits": 4,
        "structure_score": 0.4,
        "numeric_fidelity_score": 0.7,
    }
    text = (
        "The quarterly review covers market risk, liquidity, leverage, volatility, and equity market structure. "
        "It does not mention the active ticker symbols, but it is valid macro context for overnight position risk."
    )

    result = nightly_pipeline._ocr_quality_scan_decision(doc, text, config)

    assert result["keep"] is True
    assert result["topic_anchor_profile"] == "macro_market_context"


def test_evaluate_ocr_quality_allows_trusted_macro_context_native_pdf():
    config = _pipeline_config({"topic": "NVDA AMD TSM swing trade opportunities"})
    quality = _evaluate_ocr_quality(
        {
            "documents": [
                {
                    "ok": True,
                    "text_chars": 7837,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "semantic_section_hits": 4,
                    "structure_score": 0.438,
                    "numeric_fidelity_score": 0.316,
                    "table_rows": 0,
                    "table_like_content": True,
                    "quality_scan_keep": True,
                    "quality_scan_reason": "kept_macro_document_anchor",
                    "document_lane": "macro_context",
                    "topic_anchor_profile": "macro_market_context",
                    "engine_chain": ["native_pdf_text"],
                }
            ],
            "readiness": {"ready": True, "visual_ready": True, "native_pdf_supported": True},
        },
        config,
    )

    assert quality["gate_pass"] is True
    assert quality["failure_class"] == "passed"
    assert quality["macro_context_bundle"] is True
    assert quality["document_lane"] == "macro_context"
    assert quality["topic_anchor_profile"] == "macro_market_context"


def test_evaluate_ocr_quality_classifies_topic_anchor_mismatch():
    config = _pipeline_config({"topic": "TSLA earnings options flow"})
    quality = _evaluate_ocr_quality(
        {
            "documents": [
                {
                    "ok": True,
                    "text_chars": 2500,
                    "extract_engine": "native_pdf_text",
                    "extraction_mode": "native_pdf_text",
                    "source_doc_class": "pdf",
                    "quality_scan_keep": False,
                    "quality_scan_reason": "topic_anchor_retention_failed:0/1",
                }
            ],
            "readiness": {"ready": True, "visual_ready": True, "native_pdf_supported": True},
        },
        config,
    )

    assert quality["gate_pass"] is False
    assert quality["failure_class"] == "topic_anchor_mismatch"
    assert "topic_anchor_mismatch" in quality["gate_fail_reasons"]


def test_parse_equity_news_feed_articles_from_flat_yahoo_scrape():
    feed_text = (
        "Copyright Latest Financial News for AMD "
        "2867fa26-2407-35e2-877c-e27546136518 "
        "https://finance.yahoo.com/markets/stocks/articles/amd-mi450-ai-deals-meta-231108165.html?.tsrc=rss "
        "Mon, 11 May 2026 23:11:08 +0000 "
        "AMD MI450 AI Deals With Meta And OpenAI Test Rich Valuation "
        "Tesla shares stayed active as investors reacted. "
        "a4826b08-a694-3404-8bdc-918a51495f1a "
        "https://finance.yahoo.com/m/a4826b08-a694-3404-8bdc-918a51495f1a/qualcomm-stock.html?.tsrc=rss "
        "Mon, 11 May 2026 20:55:00 +0000 "
        "Qualcomm Stock Is Rising. Why the Chip Stock Hit a Record High."
    )
    rows = nightly_pipeline._parse_equity_news_feed_articles(
        feed_text,
        ticker="AMD",
        feed_url="https://feeds.finance.yahoo.com/rss/2.0/headline?s=AMD&region=US&lang=en-US",
    )
    assert len(rows) == 2
    assert rows[0]["ticker"] == "AMD"
    assert "MI450" in rows[0]["headline"]
    assert rows[0]["published_at"] == "Mon, 11 May 2026 23:11:08 +0000"


def test_news_feed_quality_gate_passes_without_ocr_scrape_failure():
    config = _pipeline_config(
        {
            "ocr_doc_min_chars": 200,
            "ocr_avg_min_chars": 300,
            "ocr_min_semantic_hits": 2,
            "ocr_min_non_scrape_ratio": 0.85,
            "ocr_min_structure_score": 0.45,
            "ocr_min_numeric_fidelity": 0.5,
            "ocr_structure_bench_min": 80,
            "ocr_correctness_bench_min": 82,
        }
    )
    quality = _evaluate_ocr_quality(
        {
            "topic": "one discretionary stock trade decision per day with overnight holding allowed",
            "readiness": {"ready": True, "mode": "gateway", "visual_ready": True, "visual_mode": "gateway", "native_pdf_supported": True},
            "documents": [
                {
                    "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AMD&region=US&lang=en-US",
                    "ok": True,
                    "quality_scan_keep": True,
                    "text_chars": 900,
                    "semantic_section_hits": 4,
                    "structure_score": 0.62,
                    "numeric_fidelity_score": 0.75,
                    "table_like_content": False,
                    "table_rows": 0,
                    "extract_engine": "news_feed_scrape",
                    "extraction_mode": "news_feed",
                    "source_doc_class": "news_feed",
                    "extract_attempts": [{"engine": "news_feed_scrape", "ok": True}],
                }
            ],
        },
        config,
    )
    assert quality["gate_pass"] is True
    assert quality["news_feed_bundle"] is True
    assert "ocr_unverified_scrape_only" not in quality["gate_fail_reasons"]


def test_gather_quality_marks_c_only_sources_probe_or_observation():
    config = _pipeline_config(
        {
            "max_articles": 2,
            "gather_min_accepted_sources": 1,
            "gather_min_avg_score": 35,
            "gather_min_trusted_ratio": 0.0,
            "equity_news_tickers": ["NVDA"],
        }
    )
    gated = _apply_gather_quality_gate(
        {
            "topic": "NVDA stock trading earnings guidance risk",
            "sources": [
                {
                    "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
                    "title": "NVDA stock trading news earnings guidance analyst",
                    "why": "equity news feed for watchlist ticker NVDA",
                    "pre_approved": True,
                }
            ],
            "candidates": [],
        },
        config,
        quality_override={"min_accepted_sources": 1, "min_trusted_ratio": 0.0, "min_doc_sources": 0, "min_pdf_sources": 0},
    )
    quality = gated["quality"]
    assert gated["ok"] is True
    assert quality["source_confidence"] == "low"
    assert quality["c_only_run"] is True
    assert quality["trade_confidence_cap"] == "probe_or_observation"
    assert quality["standard_trade_ready_allowed"] is False
    assert quality["evidence_lanes"]["market_news_evidence"] == 1
    assert quality["candidate_evidence_packs"][0]["ticker"] == "NVDA"


def test_stage_gather_rescues_c_only_rss_with_trusted_search(monkeypatch, tmp_path):
    calls = []

    def fake_gather(payload):
        calls.append({"sources": list(payload.get("sources") or []), "queries": list(payload.get("queries") or []), "web": dict(payload.get("web") or {})})
        if payload.get("sources"):
            return {
                "ok": True,
                "topic": payload["topic"],
                "batch_id": payload["batch_id"],
                "sources": list(payload.get("sources") or []),
                "candidates": [],
                "phase2": {"web": {"attempt_count": 0}},
                "errors": [],
            }
        return {
            "ok": True,
            "topic": payload["topic"],
            "batch_id": payload["batch_id"],
            "sources": [
                {
                    "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000010/form8k.htm",
                    "title": "NVDA SEC 8-K earnings guidance stock trading risk",
                    "why": "trusted SEC filing catalyst evidence for NVDA",
                }
            ],
            "candidates": [],
            "phase2": {"web": {"attempt_count": 1}},
            "errors": [],
        }

    monkeypatch.setattr(trading_homework, "gather_trading_homework_sources", fake_gather)
    monkeypatch.setattr(nightly_pipeline, "_select_focus_universe", lambda config: {})
    config = _pipeline_config(
        {
            "batch_id": "test_rescue",
            "topic": "NVDA stock trading earnings guidance risk",
            "allow_web": True,
            "allow_inbox": False,
            "max_articles": 1,
            "equity_news_tickers": ["NVDA"],
            "gather_remediation_max_passes": 2,
            "gather_min_accepted_sources": 1,
            "gather_min_avg_score": 35,
            "gather_min_trusted_ratio": 0.0,
        }
    )

    result = nightly_pipeline._stage_gather(tmp_path, config)

    assert len(calls) >= 2
    assert calls[0]["sources"]
    assert calls[1]["sources"] == []
    assert calls[1]["queries"]
    quality = result["quality"]
    assert result["attempt_name"] in {"remediation_strict_ab", "remediation_authoritative_mix"}
    assert result["attempt_name"] != "default"
    assert quality["source_confidence"] in {"medium", "high"}
    assert quality["trusted_count"] >= 1
    assert quality["standard_trade_ready_allowed"] is True


def test_process_ocr_source_ingests_news_feed_articles(monkeypatch):
    class FakeRag:
        def __init__(self):
            self.calls = []

        def remember(self, **kwargs):
            self.calls.append(kwargs)
            return f"doc-{len(self.calls)}"

    def fake_scrape(url, timeout=12, max_chars=16000):
        return (
            True,
            (
                "Latest Financial News for AVGO "
                "3f8dec21-89b0-346a-bbf9-7d09d258a83c "
                "https://finance.yahoo.com/markets/stocks/articles/celesticas-tsx-cls-1-6tbe-011709744.html?.tsrc=rss "
                "Tue, 12 May 2026 01:17:09 +0000 "
                "Celestica's 1.6TbE Switches Redefine AI Infrastructure Ambitions. "
                "Broadcom Tomahawk 6 silicon supports AI and machine learning data center networks."
            ),
            "",
        )

    import Apollo.trading_homework as trading_homework

    monkeypatch.setattr(trading_homework, "_scrape_url", fake_scrape)
    monkeypatch.setattr(
        nightly_pipeline,
        "_upsert_financial_news_articles",
        lambda articles, topic, batch_id: {"ok": True, "count": len(articles), "ids": ["main-doc-1"]},
    )
    fake_rag = FakeRag()
    config = _pipeline_config(
        {
            "batch_id": "test_news_feed",
            "ocr_scan_enabled": True,
            "ocr_scan_min_chars": 200,
            "ocr_scan_min_quality_score": 20,
            "ocr_scan_min_semantic_hits": 1,
            "ocr_scan_min_structure_score": 0.0,
            "ocr_scan_min_numeric_fidelity": 0.0,
            "ocr_scan_min_anchor_hits": 0,
        }
    )
    processed = nightly_pipeline._process_ocr_source(
        {
            "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AVGO&region=US&lang=en-US",
            "title": "AVGO stock trading news earnings guidance analyst",
            "why": "equity news feed for watchlist ticker AVGO",
        },
        topic="one discretionary stock trade decision per day with overnight holding allowed",
        config=config,
        rag_store_inst=fake_rag,
        readiness={"ready": True, "visual_ready": True},
        seen_fingerprints=set(),
    )
    doc = processed["document"]
    assert doc["quality_scan_keep"] is True
    assert doc["source_doc_class"] == "news_feed"
    assert doc["extraction_mode"] == "news_feed"
    assert doc["article_count"] == 1
    assert doc["ingested_ids"] == ["doc-1"]
    assert fake_rag.calls[0]["kind"] == "news_feed"
    assert fake_rag.calls[0]["extra"]["ticker"] == "AVGO"
    assert doc["financial_corpus_ids"] == ["main-doc-1"]


def test_all_stages_complete_requires_completed_flag():
    status = {
        "stages": {
            "gather": {"ok": True, "completed": True},
            "ocr": {"ok": True, "completed": False},
            "hipporag": {"ok": True, "completed": False},
            "self_check": {},
        }
    }
    assert _all_stages_complete(status) is False


def test_update_stage_status_clears_stale_waiting(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    status_path = run_dir / "status.json"
    status_path.write_text(
        """{
  "ok": false,
  "run_id": "apollo_background_current",
  "run_dir": "tmp",
  "stages": {},
  "current_stage": "hipporag",
  "waiting": {
    "reason": "RuntimeError:nightly_lock_present:{\\"pid\\": 123}",
    "ts": "2026-04-14T20:55:36Z"
  }
}""",
        encoding="utf-8",
    )

    _update_stage_status(run_dir, "hipporag", {"ok": True, "completed": False, "processed": 8})

    updated = _load_status(run_dir)
    assert "waiting" not in updated
    assert updated["stages"]["hipporag"]["processed"] == 8


def test_lock_is_stale_when_pid_is_dead(tmp_path, monkeypatch):
    lock_path = tmp_path / "nightly.lock"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    heartbeat = now - timedelta(seconds=30)
    lock_path.write_text(
        json.dumps(
            {
                "run_id": "apollo_background_current",
                "run_dir": "tmp",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "heartbeat_at": heartbeat.isoformat().replace("+00:00", "Z"),
                "owner_pid": 999999,
                "pid": 999999,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("Apollo.nightly_pipeline._pid_is_running", lambda pid: False)

    assert _lock_is_stale(lock_path, 43200) is True


def test_lock_is_stale_when_heartbeat_expired(tmp_path, monkeypatch):
    lock_path = tmp_path / "nightly.lock"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    heartbeat = now - timedelta(seconds=601)
    lock_path.write_text(
        json.dumps(
            {
                "run_id": "apollo_background_current",
                "run_dir": "tmp",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "heartbeat_at": heartbeat.isoformat().replace("+00:00", "Z"),
                "owner_pid": 12345,
                "pid": 12345,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("Apollo.nightly_pipeline._pid_is_running", lambda pid: True)
    assert _lock_is_stale(lock_path, 43200) is True


def test_lock_is_not_stale_with_fresh_heartbeat(tmp_path, monkeypatch):
    lock_path = tmp_path / "nightly.lock"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    heartbeat = now - timedelta(seconds=30)
    lock_path.write_text(
        json.dumps(
            {
                "run_id": "apollo_background_current",
                "run_dir": "tmp",
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "heartbeat_at": heartbeat.isoformat().replace("+00:00", "Z"),
                "owner_pid": 12345,
                "pid": 12345,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("Apollo.nightly_pipeline._pid_is_running", lambda pid: True)
    assert _lock_is_stale(lock_path, 43200) is False


def test_latest_status_clears_stale_lock_and_lock_waiting(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    run_dir = runs_root / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", runs_root / "nightly.lock")
    monkeypatch.setattr("Apollo.nightly_pipeline._pid_is_running", lambda pid: False)
    (run_dir / "config.json").write_text(json.dumps(_pipeline_config({"run_mode": "background"})), encoding="utf-8")

    status = {
        "ok": False,
        "run_id": "apollo_background_current",
        "run_mode": "background",
        "run_dir": str(run_dir),
        "started_at": "2026-04-23T18:00:00Z",
        "finished_at": "",
        "stages": {},
        "current_stage": "gather",
        "waiting": {"reason": "RuntimeError:nightly_lock_present:{\"owner_pid\":111,\"run_id\":\"apollo_background_current\"}", "ts": "2026-04-23T18:05:00Z"},
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (runs_root / "latest_status.json").write_text(json.dumps(status), encoding="utf-8")
    (runs_root / "background_current_pointer.json").write_text(
        json.dumps({"run_id": "apollo_background_current", "run_dir": str(run_dir.resolve()), "status_seq": 1}),
        encoding="utf-8",
    )
    (runs_root / "nightly.lock").write_text(
        json.dumps(
            {
                "run_id": "apollo_background_current",
                "run_dir": str(run_dir),
                "started_at": "2026-04-23T16:00:00Z",
                "heartbeat_at": "2026-04-23T16:00:00Z",
                "owner_pid": 111,
                "pid": 111,
                "stage": "gather",
            }
        ),
        encoding="utf-8",
    )

    latest = nightly_pipeline.latest_status()

    assert "waiting" not in latest
    assert not (runs_root / "nightly.lock").exists()


def test_stage_file_reconciliation_fills_truth_and_summary_rows(tmp_path, monkeypatch):
    run_dir = tmp_path / "nightly" / "2026-05-20" / "apollo_nightly_unit"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path / "nightly")
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "nightly" / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", tmp_path / "nightly" / "background_current_pointer.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly" / "nightly.lock")
    config = _pipeline_config({"run_mode": "nightly", "strict_stage_min_score": 80})
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    _write_json(run_dir / "01_data_gather.json", {"ok": True, "completed": True, "quality": {"accepted_count": 4, "avg_score": 90}, "sources": [{}, {}, {}, {}]})
    _write_json(run_dir / "02_ocr_ingest.json", {"ok": True, "completed": True, "ingested": 4, "documents": [{"text_chars": 1200, "semantic_section_hits": 6, "extract_engine": "fixture"}]})
    _write_json(run_dir / "03_hipporag.json", {"ok": True, "completed": True, "processed": 4, "processed_finance": 4, "triples": 20, "remaining_after": 0, "quality": {"gate_pass": True}})
    _write_json(run_dir / "04_self_check.json", {"ok": True, "completed": True, "checks": {"unit": {"ok": True}}})
    status = {
        "ok": False,
        "run_id": "apollo_nightly_unit",
        "run_dir": str(run_dir),
        "started_at": "2026-05-20T09:00:00Z",
        "finished_at": "2026-05-20T09:30:00Z",
        "current_stage": "done",
        "stages": {},
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")

    enriched = _enrich_status_payload(run_dir, status)
    summary = nightly_pipeline._render_summary(run_dir, config, {"total_minutes": 1})

    assert enriched["pipeline_completed"] is True
    assert enriched["stage_truth"]["gather"]["completed"] is True
    assert enriched["stage_truth"]["ocr"]["result_path"].endswith("02_ocr_ingest.json")
    assert "- gather: ok=True" in summary
    assert "- ocr: ok=True" in summary


def test_latest_status_clears_waiting_when_lock_owner_changes(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    run_dir = runs_root / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", runs_root / "nightly.lock")
    monkeypatch.setattr("Apollo.nightly_pipeline._pid_is_running", lambda pid: True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    heartbeat = now - timedelta(seconds=30)
    (run_dir / "config.json").write_text(json.dumps(_pipeline_config({"run_mode": "background"})), encoding="utf-8")

    status = {
        "ok": False,
        "run_id": "apollo_background_current",
        "run_mode": "background",
        "run_dir": str(run_dir),
        "started_at": "2026-04-23T18:00:00Z",
        "finished_at": "",
        "stages": {},
        "current_stage": "gather",
        "waiting": {"reason": "RuntimeError:nightly_lock_present:{\"owner_pid\":111,\"run_id\":\"apollo_background_current\"}", "ts": "2026-04-23T18:05:00Z"},
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (runs_root / "latest_status.json").write_text(json.dumps(status), encoding="utf-8")
    (runs_root / "background_current_pointer.json").write_text(
        json.dumps({"run_id": "apollo_background_current", "run_dir": str(run_dir.resolve()), "status_seq": 1}),
        encoding="utf-8",
    )
    (runs_root / "nightly.lock").write_text(
        json.dumps(
            {
                "run_id": "apollo_background_current",
                "run_dir": str(run_dir),
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "heartbeat_at": heartbeat.isoformat().replace("+00:00", "Z"),
                "owner_pid": 222,
                "pid": 222,
                "stage": "preflight",
            }
        ),
        encoding="utf-8",
    )

    latest = nightly_pipeline.latest_status()

    assert "waiting" not in latest
    assert (runs_root / "nightly.lock").exists()


def test_background_step_timeout_releases_lock_and_next_tick_reacquires(tmp_path, monkeypatch):
    run_id = "apollo_background_current"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")
    monkeypatch.setattr(
        nightly_pipeline,
        "_ensure_gx10_tunnel",
        lambda config, run_dir: {"ok": True, "log_path": str(run_dir / "00_preflight.log")},
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_background_gx10_idle",
        lambda config: {
            "idle": True,
            "active_models": 1,
            "active_models_primary": 1,
            "active_models_deep": 0,
            "max_active_models": 1,
            "idle_threshold_sec": 300,
            "base_url": "http://127.0.0.1:11434",
            "deep_url": "http://127.0.0.1:11435",
        },
    )
    calls = {"count": 0}

    def fake_runner(run_id_arg, run_dir_arg, stage_name, timeout_sec):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "stage": stage_name,
                "ok": False,
                "return_code": -9,
                "timed_out": True,
                "started_at": "2026-04-16T17:00:00Z",
                "finished_at": "2026-04-16T17:00:01Z",
                "timeout_sec": timeout_sec,
                "log_path": str(run_dir_arg / "01_data_gather.log"),
                "result_path": str(run_dir_arg / "01_data_gather.json"),
                "result": {"ok": False, "completed": False},
            }
        return {
            "stage": stage_name,
            "ok": True,
            "return_code": 0,
            "timed_out": False,
            "started_at": "2026-04-16T17:01:00Z",
            "finished_at": "2026-04-16T17:01:01Z",
            "timeout_sec": timeout_sec,
            "log_path": str(run_dir_arg / "01_data_gather.log"),
            "result_path": str(run_dir_arg / "01_data_gather.json"),
            "result": {"ok": True, "completed": True},
        }

    monkeypatch.setattr(nightly_pipeline, "_run_background_subprocess_stage", fake_runner)

    first = nightly_pipeline.run_background_pipeline_step({"run_mode": "background", "batch_id": run_id, "background_gather_timeout_sec": 1})
    assert first["waiting"]["reason"] == "stage_timeout:gather"
    assert first["stages"]["gather"]["timed_out"] is True
    assert nightly_pipeline.LOCK_PATH.exists() is False

    second = nightly_pipeline.run_background_pipeline_step({"run_mode": "background", "batch_id": run_id, "background_gather_timeout_sec": 1})
    assert calls["count"] == 2
    assert second["stages"]["gather"]["ok"] is True
    assert nightly_pipeline.LOCK_PATH.exists() is False


def test_background_hipporag_stall_triggers_auto_recovery(tmp_path, monkeypatch):
    run_id = "apollo_background_current"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")
    monkeypatch.setattr(
        nightly_pipeline,
        "_ensure_gx10_tunnel",
        lambda config, run_dir: {"ok": True, "log_path": str(run_dir / "00_preflight.log")},
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_background_gx10_idle",
        lambda config: {
            "idle": True,
            "active_models": 0,
            "active_models_primary": 0,
            "active_models_deep": 0,
            "max_active_models": 1,
            "idle_threshold_sec": 300,
            "base_url": "http://127.0.0.1:11434",
            "deep_url": "http://127.0.0.1:11435",
        },
    )

    run_dir = tmp_path / "background" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    base_status = {
        "ok": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "run_mode": "background",
        "started_at": "2026-04-18T00:00:00Z",
        "stages": {
            "gather": {"completed": True, "ok": True, "quality": {"accepted_count": 4, "gate_pass": True}},
            "ocr": {"completed": True, "ok": True, "documents": [{"url": "https://example.com/noise.pdf", "extract_engine": "scrape", "text_chars": 100}]},
            "hipporag": {"completed": False},
        },
        "current_stage": "hipporag",
    }
    (run_dir / "status.json").write_text(json.dumps(base_status), encoding="utf-8")
    (run_dir / "config.json").write_text(json.dumps(_pipeline_config({"run_mode": "background", "batch_id": run_id, "background_hipporag_step_chunks": 8})), encoding="utf-8")

    monkeypatch.setattr(
        nightly_pipeline,
        "_run_background_subprocess_stage",
        lambda _run_id, _run_dir, stage_name, timeout_sec: {
            "stage": stage_name,
            "ok": False,
            "return_code": 0,
            "timed_out": False,
            "timeout_sec": timeout_sec,
            "result": {
                "ok": False,
                "completed": False,
                "remaining_before": 8,
                "remaining_after": 8,
                "processed": 0,
                "processed_finance": 0,
                "skipped_non_finance": 8,
                "requeued_low_quality": 8,
                "triples": 0,
                "quality": {"gate_pass": False},
            },
        },
    )
    monkeypatch.setattr(nightly_pipeline, "_recovery_candidate_urls", lambda status, config: ["https://example.com/noise.pdf"])
    monkeypatch.setattr(nightly_pipeline, "_purge_operational_rag_by_url", lambda url: {"deleted": 1, "ids": ["id-1"]})
    monkeypatch.setattr(nightly_pipeline, "_purge_financial_corpus_by_url", lambda url: {"deleted": 0, "ids": [], "kg_updated": False})

    step_payload = {"run_mode": "background", "batch_id": run_id, "background_hipporag_step_chunks": 8}
    nightly_pipeline.run_background_pipeline_step(step_payload)
    nightly_pipeline.run_background_pipeline_step(step_payload)
    third = nightly_pipeline.run_background_pipeline_step(step_payload)
    assert third["waiting"]["reason"] == "auto_recovery_triggered"
    assert third["current_stage"] == "gather"
    assert (third.get("recovery") or {}).get("last_action") == "auto_recover_reset_to_gather"


def test_purge_background_source_clears_run_artifacts(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")

    status = {
        "ok": False,
        "run_id": "apollo_background_current",
        "run_dir": str(run_dir),
        "started_at": "2026-04-15T14:33:03Z",
        "finished_at": "",
        "stages": {
            "gather": {"ok": True, "completed": True},
            "ocr": {"ok": True, "completed": True},
            "hipporag": {"ok": True, "completed": False},
        },
        "current_stage": "hipporag",
        "waiting": {"reason": "gb10_busy"},
        "last_step_at": "2026-04-16T15:41:27Z",
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    for name in ("01_data_gather.json", "01_data_gather.log", "02_ocr_ingest.json", "02_ocr_extracts.md", "03_hipporag.json", "summary.md"):
        (run_dir / name).write_text("stale", encoding="utf-8")

    monkeypatch.setattr(nightly_pipeline, "_purge_operational_rag_by_url", lambda url: {"deleted": 1, "ids": ["rag-1"]})
    monkeypatch.setattr(
        nightly_pipeline,
        "_purge_financial_corpus_by_url",
        lambda url: {"deleted": 0, "ids": [], "kg_updated": False, "kg_stats": {"nodes": 19, "edges": 30, "indexed_docs": 17}},
    )

    result = purge_background_source("https://example.com/bad.pdf")

    assert result["operational_rag"]["deleted"] == 1
    assert result["financial_corpus"]["deleted"] == 0
    cleaned = _load_status(run_dir)
    assert cleaned["current_stage"] == "gather"
    assert cleaned["stages"] == {}
    assert "waiting" not in cleaned
    assert "last_step_at" not in cleaned
    assert cleaned["maintenance"]["purges"][-1]["url"] == "https://example.com/bad.pdf"
    assert not (run_dir / "01_data_gather.json").exists()
    assert not (run_dir / "02_ocr_ingest.json").exists()
    assert not (run_dir / "03_hipporag.json").exists()


def test_is_production_run_dir_rejects_fixture_like_paths(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    prod_dir = runs_root / "background" / "apollo_background_current"
    fixture_dir = runs_root / "tmp" / "apollo_background_current"
    assert nightly_pipeline._is_production_run_dir(prod_dir) is True
    assert nightly_pipeline._is_production_run_dir(fixture_dir) is False


def test_save_status_updates_pointer_and_monotonic_seq(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")
    run_dir = runs_root / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(_pipeline_config({"run_mode": "background"})), encoding="utf-8")

    payload = {
        "ok": False,
        "run_id": "apollo_background_current",
        "run_dir": str(run_dir),
        "run_mode": "background",
        "started_at": "2026-04-18T00:00:00Z",
        "finished_at": "",
        "stages": {},
        "current_stage": "gather",
    }
    _save_status(run_dir, payload)
    first = _load_status(run_dir)
    _save_status(run_dir, {**first, "current_stage": "ocr"})
    second = _load_status(run_dir)
    pointer = json.loads((runs_root / "background_current_pointer.json").read_text(encoding="utf-8"))

    assert int(second["status_seq"]) > int(first["status_seq"])
    assert pointer["run_id"] == "apollo_background_current"
    assert pointer["run_dir"] == str(run_dir.resolve())
    assert second["run_meta"]["current_pointer_valid"] is True


def test_save_status_replaces_latest_by_timestamp_across_runs(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")
    old_run_dir = runs_root / "background" / "apollo_background_current"
    new_run_dir = runs_root / "2026-05-14" / "apollo_nightly_20260514_090001"
    old_run_dir.mkdir(parents=True)
    new_run_dir.mkdir(parents=True)
    (runs_root / "latest_status.json").write_text(
        json.dumps(
            {
                "ok": False,
                "run_id": "apollo_background_current",
                "run_dir": str(old_run_dir),
                "run_mode": "background",
                "status_seq": 999,
                "updated_at": "2026-04-24T14:00:00Z",
                "current_stage": "hipporag",
                "stages": {},
            }
        ),
        encoding="utf-8",
    )

    _save_status(
        new_run_dir,
        {
            "ok": False,
            "run_id": "apollo_nightly_20260514_090001",
            "run_dir": str(new_run_dir),
            "run_mode": "nightly",
            "status_seq": 1,
            "started_at": "2026-05-14T09:00:02Z",
            "updated_at": "2026-05-14T09:01:32Z",
            "current_stage": "self_check",
            "stages": {},
        },
    )

    latest = json.loads((runs_root / "latest_status.json").read_text(encoding="utf-8"))
    assert latest["run_id"] == "apollo_nightly_20260514_090001"
    assert latest["run_dir"] == str(new_run_dir.resolve())


def test_stage_self_check_treats_disabled_focus_universe_as_skipped(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_json(
        run_dir / "status.json",
        {
            "ok": False,
            "run_id": "focus_disabled",
            "run_dir": str(run_dir),
            "run_mode": "nightly",
            "stages": {},
        },
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_http_self_check",
        lambda: {"status_code": 200, "payload": {"ok": True, "checks": {"server": {"ok": True}}}},
    )

    result = nightly_pipeline._stage_self_check(
        run_dir,
        _pipeline_config({"focus_universe_enabled": False, "focus_universe_gb10_only": False}),
    )

    decision_contract = result["checks"]["decision_gate_contract"]
    assert result["ok"] is True
    assert result["decision_gate"]["decision_mode"] == "disabled"
    assert result["decision_gate"]["skipped"] is True
    assert decision_contract["ok"] is True
    assert decision_contract["skipped"] is True


def test_history_ignores_fixture_status_files(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")

    prod_dir = runs_root / "background" / "apollo_background_current"
    prod_dir.mkdir(parents=True)
    prod_status = {
        "ok": True,
        "run_id": "apollo_background_current",
        "run_dir": str(prod_dir),
        "run_mode": "background",
        "started_at": "2026-04-18T01:00:00Z",
        "finished_at": "2026-04-18T01:10:00Z",
        "stages": {"gather": {"completed": True}, "ocr": {"completed": True}, "hipporag": {"completed": True}, "self_check": {"completed": True}},
        "current_stage": "done",
        "quality": {"overall_score": 80, "gate_pass": True, "stage_scores": {"gather": 76, "ocr": 84, "hipporag": 78, "self_check": 90}},
    }
    (prod_dir / "status.json").write_text(json.dumps(prod_status), encoding="utf-8")

    fixture_dir = runs_root / "tmp" / "apollo_fixture"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "status.json").write_text(json.dumps({"run_id": "fixture", "run_mode": "background", "ok": False}), encoding="utf-8")

    payload = list_background_run_history(limit=20)
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["run_id"] == "apollo_background_current"


def test_compute_quality_exposes_rolling_and_confidence(monkeypatch):
    sample_history = [
        {"run_id": "r1", "run_mode": "background", "quality": {"overall_score": 79, "stage_scores": {"gather": 75, "ocr": 80, "hipporag": 78, "self_check": 90}}},
        {"run_id": "r2", "run_mode": "background", "quality": {"overall_score": 82, "stage_scores": {"gather": 78, "ocr": 84, "hipporag": 81, "self_check": 90}}},
        {"run_id": "r3", "run_mode": "background", "quality": {"overall_score": 80, "stage_scores": {"gather": 77, "ocr": 83, "hipporag": 79, "self_check": 90}}},
    ]
    monkeypatch.setattr(nightly_pipeline, "_iter_background_run_statuses", lambda window=7: sample_history[:window])
    config = _pipeline_config({"strict_stage_min_score": 70})
    status = {
        "run_mode": "background",
        "stages": {
            "gather": {"quality": {"accepted_count": 2, "avg_score": 84, "trusted_count": 2, "pdf_count": 1}},
            "ocr": {"documents": [{"text_chars": 1900, "extract_engine": "gb10_paddleocr_vl", "semantic_section_hits": 3}]},
            "hipporag": {"remaining_before": 2, "remaining_after": 0, "processed": 2, "triples": 4, "completed": True, "quality": {"gate_pass": True}},
            "self_check": {"ok": True},
        },
    }
    quality = _compute_quality(status, config)
    assert quality["score_basis"] == "current_plus_rolling7_median"
    assert quality["rolling7_median_score"] == 80
    assert quality["rolling7_stage_medians"]["hipporag"] == 78
    assert quality["confidence_band"]["label"] in {"low", "medium", "high"}


def test_compute_quality_recent_recovery_streak_can_lift_confidence(monkeypatch):
    sample_history = [
        {
            "run_id": "current",
            "run_mode": "background",
            "pipeline_completed": True,
            "quality_gate_pass": True,
            "quality": {"overall_score": 90, "gate_pass": True, "stage_scores": {"gather": 88, "ocr": 90, "hipporag": 91, "self_check": 92}},
        },
        {
            "run_id": "previous",
            "run_mode": "background",
            "pipeline_completed": True,
            "quality_gate_pass": True,
            "quality": {"overall_score": 86, "gate_pass": True, "stage_scores": {"gather": 84, "ocr": 86, "hipporag": 88, "self_check": 90}},
        },
        {
            "run_id": "old-broken",
            "run_mode": "background",
            "pipeline_completed": False,
            "quality_gate_pass": False,
            "quality": {"overall_score": 20, "gate_pass": False, "stage_scores": {"gather": 20, "ocr": 20, "hipporag": 20, "self_check": 20}},
        },
    ]
    monkeypatch.setattr(nightly_pipeline, "_iter_background_run_statuses", lambda window=7: sample_history[:window])
    config = _pipeline_config({"strict_stage_min_score": 70})
    status = {
        "run_id": "current",
        "run_mode": "background",
        "stages": {
            "gather": {"quality": {"accepted_count": 5, "avg_score": 88, "trusted_count": 4, "pdf_count": 2}},
            "ocr": {
                "quality": {
                    "gate_pass": True,
                    "non_scrape_ratio": 1.0,
                    "avg_structure_score": 0.85,
                    "avg_numeric_fidelity": 0.835,
                    "benchmark_proxy_scores": {"structure_lane": 86, "correctness_lane": 84},
                },
                "documents": [
                    {
                        "text_chars": 2600,
                        "extract_engine": "native_pdf_text",
                        "semantic_section_hits": 4,
                        "structure_score": 0.86,
                        "numeric_fidelity_score": 0.84,
                        "table_rows": 5,
                        "table_like_content": True,
                    },
                    {
                        "text_chars": 2400,
                        "extract_engine": "gb10_paddleocr_vl",
                        "semantic_section_hits": 3,
                        "structure_score": 0.84,
                        "numeric_fidelity_score": 0.83,
                        "table_rows": 4,
                        "table_like_content": True,
                    },
                ]
            },
            "hipporag": {"remaining_before": 3, "remaining_after": 0, "processed": 3, "triples": 5, "completed": True, "quality": {"gate_pass": True}},
            "self_check": {"ok": True},
        },
    }

    quality = _compute_quality(status, config)

    assert quality["confidence_band"]["label"] == "medium"
    assert quality["confidence_band"]["basis"] == "recent_recovery_streak"
    assert quality["confidence_band"]["recent_high_quality_passes"] == 2


def test_evaluate_ocr_quality_requires_semantic_depth():
    config = _pipeline_config({"ocr_doc_min_chars": 600, "ocr_avg_min_chars": 1200, "ocr_min_semantic_hits": 2})
    weak = _evaluate_ocr_quality(
        {
            "documents": [
                {"text_chars": 1800, "extract_engine": "gb10_paddleocr_vl", "semantic_section_hits": 1, "structure_score": 0.62, "numeric_fidelity_score": 0.7},
                {"text_chars": 1500, "extract_engine": "gb10_qwen_ocr", "semantic_section_hits": 0, "structure_score": 0.59, "numeric_fidelity_score": 0.66},
            ]
        },
        config,
    )
    assert weak["gate_pass"] is False
    strong = _evaluate_ocr_quality(
        {
            "documents": [
                {"text_chars": 1800, "extract_engine": "gb10_paddleocr_vl", "semantic_section_hits": 3, "structure_score": 0.74, "numeric_fidelity_score": 0.71, "table_rows": 4, "table_like_content": True},
                {"text_chars": 1500, "extract_engine": "gb10_qwen_ocr", "semantic_section_hits": 2, "structure_score": 0.72, "numeric_fidelity_score": 0.69, "table_rows": 3, "table_like_content": True},
            ]
        },
        config,
    )
    assert strong["gate_pass"] is True


def test_enrich_status_payload_exposes_ocr_contract_fields(tmp_path):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    config = _pipeline_config({"run_mode": "background"})
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    status = {
        "run_id": "apollo_background_current",
        "run_dir": str(run_dir),
        "run_mode": "background",
        "started_at": "2026-04-18T01:00:00Z",
        "current_stage": "ocr",
        "stages": {
            "gather": {"quality": {"accepted_count": 2, "avg_score": 78, "trusted_count": 2, "pdf_count": 1}},
            "ocr": {
                "documents": [
                    {
                        "text_chars": 1900,
                        "extract_engine": "gb10_paddleocr_vl",
                        "route_mode": "quality_first",
                        "engine_chain": ["gb10_paddleocr_vl", "gb10_qwen_ocr"],
                        "semantic_section_hits": 3,
                        "structure_score": 0.76,
                        "numeric_fidelity_score": 0.74,
                        "table_rows": 4,
                        "table_like_content": True,
                        "extract_attempts": [{"engine": "gb10_paddleocr_vl", "ok": True}],
                    }
                ]
            },
            "hipporag": {"remaining_before": 2, "remaining_after": 2, "processed": 0, "triples": 0, "completed": False},
            "self_check": {"ok": True},
        },
    }
    enriched = _enrich_status_payload(run_dir, status)
    assert "ocr" in enriched
    assert "current_policy" in enriched["ocr"]
    assert "engine_chain_last_doc" in enriched["ocr"]
    assert "quality_breakdown" in enriched["ocr"]
    assert "gate_fail_reasons" in enriched["ocr"]


def test_apply_gather_quality_gate_rejects_topic_intent_mismatch():
    config = _pipeline_config({"max_articles": 2, "gather_min_accepted_sources": 1, "gather_min_avg_score": 68})
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "Federal Reserve policy bulletin for banks",
                "snippet": "Monetary policy and liquidity operations for depository institutions.",
                "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260101a.htm",
            }
        ],
        "sources": [
            {
                "title": "Federal Reserve policy bulletin for banks",
                "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260101a.htm",
                "why": "picked",
            }
        ],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(result, config)
    assert gated["ok"] is False
    reasons = [str(item.get("reject_reason") or "") for item in gated["quality"]["rejected_candidates"]]
    assert "topic_intent_mismatch" in reasons


def test_scorecard_returns_current_and_rolling(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")
    run_dir = runs_root / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(_pipeline_config({"run_mode": "background"})), encoding="utf-8")
    _save_status(
        run_dir,
        {
            "ok": True,
            "run_id": "apollo_background_current",
            "run_mode": "background",
            "run_dir": str(run_dir),
            "started_at": "2026-04-18T02:00:00Z",
            "finished_at": "2026-04-18T02:10:00Z",
            "stages": {
                "gather": {"completed": True, "quality": {"accepted_count": 2, "avg_score": 80, "trusted_count": 2, "pdf_count": 1}},
                "ocr": {"completed": True, "documents": [{"text_chars": 1700, "extract_engine": "gb10_paddleocr_vl", "semantic_section_hits": 3}]},
                "hipporag": {"completed": True, "remaining_before": 8, "remaining_after": 0, "processed": 8, "triples": 12, "quality": {"gate_pass": True}},
                "self_check": {"completed": True, "ok": True},
            },
            "current_stage": "done",
        },
    )
    payload = scorecard(window=7)
    assert payload["window"] == 7
    assert isinstance(payload["current"], dict)
    assert isinstance(payload["rolling"], dict)
    assert "label" in payload["confidence_band"]


def test_apply_gather_quality_gate_requires_trusted_ratio_and_doc_sources():
    config = _pipeline_config(
        {
            "max_articles": 4,
            "gather_min_accepted_sources": 2,
            "gather_min_avg_score": 70,
            "gather_min_trusted_ratio": 0.75,
            "gather_min_doc_sources": 2,
            "gather_min_pdf_sources": 1,
        }
    )
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "Market wrap and commentary",
                "snippet": "Stocks and market commentary with little research detail.",
                "url": "https://www.cnn.com/markets/",
            },
            {
                "title": "Google Finance market home",
                "snippet": "Live market page.",
                "url": "https://www.google.com/finance/",
            },
        ],
        "sources": [
            {"title": "CNN Markets", "url": "https://www.cnn.com/markets/", "why": "picked"},
            {"title": "Google Finance", "url": "https://www.google.com/finance/", "why": "picked"},
        ],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(result, config)
    assert gated["ok"] is False
    fail_errors = " ".join(gated.get("errors") or [])
    assert "trusted_ratio" in fail_errors
    assert "doc_sources" in fail_errors


def test_apply_gather_quality_gate_counts_trusted_report_like_html_as_doc_sources():
    config = _pipeline_config(
        {
            "max_articles": 4,
            "gather_min_accepted_sources": 4,
            "gather_min_avg_score": 55,
            "gather_min_trusted_ratio": 0.75,
            "gather_min_doc_sources": 4,
            "gather_min_pdf_sources": 2,
        }
    )
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "Federal Reserve Financial Stability Report",
                "snippet": "market microstructure and risk backdrop",
                "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-202404.pdf",
            },
            {
                "title": "Federal Reserve Monetary Policy Report",
                "snippet": "macro context for risk sizing",
                "url": "https://www.federalreserve.gov/publications/files/20240301_mprfullreport.pdf",
            },
            {
                "title": "FINRA Rule 4210 Margin Requirements",
                "snippet": "pattern day trader requirements and maintenance margin",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
            },
            {
                "title": "SEC Investor Bulletin: Margin Accounts",
                "snippet": "day trading and leverage risks",
                "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
            },
        ],
        "sources": [
            {"title": "Financial Stability Report", "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-202404.pdf", "why": "picked"},
            {"title": "Monetary Policy Report", "url": "https://www.federalreserve.gov/publications/files/20240301_mprfullreport.pdf", "why": "picked"},
            {"title": "Rule 4210", "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210", "why": "picked"},
            {"title": "Margin Bulletin", "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts", "why": "picked"},
        ],
        "errors": [],
    }

    gated = _apply_gather_quality_gate(result, config)
    assert gated["ok"] is True
    assert int((gated.get("quality") or {}).get("doc_sources") or 0) == 4
    assert int((gated.get("quality") or {}).get("pdf_count") or 0) == 2


def test_apply_gather_quality_gate_prefers_resolved_pdf_over_origin_report_page_candidate():
    config = _pipeline_config(
        {
            "max_articles": 4,
            "gather_min_accepted_sources": 4,
            "gather_min_avg_score": 55,
            "gather_min_trusted_ratio": 0.75,
            "gather_min_doc_sources": 4,
            "gather_min_pdf_sources": 2,
        }
    )
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "Federal Reserve Financial Stability Report",
                "snippet": "stock trading market microstructure day trading margin risk backdrop 2026 5%",
                "url": "https://www.federalreserve.gov/publications/financial-stability-report.htm",
            },
            {
                "title": "Federal Reserve Monetary Policy Report",
                "snippet": "macro context for risk sizing stock trading day trading margin 2026 3%",
                "url": "https://www.federalreserve.gov/publications/files/20240301_mprfullreport.pdf",
            },
            {
                "title": "FINRA Rule 4210 Margin Requirements",
                "snippet": "pattern day trader requirements and maintenance margin for stock trading",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
            },
            {
                "title": "SEC Investor Bulletin: Margin Accounts",
                "snippet": "day trading and leverage risks for stock trading accounts",
                "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
            },
        ],
        "sources": [
            {
                "title": "Federal Reserve Financial Stability Report",
                "url": "https://www.federalreserve.gov/publications/files/financial-stability-report-20251107.pdf",
                "why": "picked",
            },
            {
                "title": "Federal Reserve Monetary Policy Report",
                "url": "https://www.federalreserve.gov/publications/files/20240301_mprfullreport.pdf",
                "why": "picked",
            },
            {
                "title": "FINRA Rule 4210 Margin Requirements",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
                "why": "picked",
            },
            {
                "title": "SEC Investor Bulletin: Margin Accounts",
                "url": "https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/margin-accounts",
                "why": "picked",
            },
        ],
        "errors": [],
    }

    gated = _apply_gather_quality_gate(result, config)
    assert gated["ok"] is True
    accepted_urls = [str(item.get("url") or "") for item in (gated.get("quality") or {}).get("accepted_sources") or []]
    assert "https://www.federalreserve.gov/publications/files/financial-stability-report-20251107.pdf" in accepted_urls
    assert "https://www.federalreserve.gov/publications/financial-stability-report.htm" not in accepted_urls
    assert int((gated.get("quality") or {}).get("pdf_count") or 0) == 2


def test_apply_gather_quality_gate_tracks_tiers_and_reject_reasons():
    config = _pipeline_config({"max_articles": 4, "gather_min_accepted_sources": 1, "gather_min_avg_score": 60})
    result = {
        "ok": True,
        "topic": "stock trading and day trading strategies",
        "candidates": [
            {
                "title": "FINRA Rule 4210 Margin Requirements",
                "snippet": "Pattern day trader requirements and maintenance margin.",
                "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
            },
            {
                "title": "Pattern definition",
                "snippet": "dictionary definition entry",
                "url": "https://www.merriam-webster.com/dictionary/pattern",
            },
        ],
        "sources": [
            {"title": "Rule 4210", "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210", "why": "picked"},
            {"title": "Pattern definition", "url": "https://www.merriam-webster.com/dictionary/pattern", "why": "picked"},
        ],
        "errors": [],
    }
    gated = _apply_gather_quality_gate(result, config)
    tier_breakdown = gated["quality"]["tier_breakdown"]
    reject_counts = gated["quality"]["reject_reason_counts"]
    assert tier_breakdown["accepted"]["A"] >= 1
    assert reject_counts.get("blocked_domain", 0) >= 1


def test_history_payload_includes_current_run_health(tmp_path, monkeypatch):
    runs_root = tmp_path / "nightly"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", runs_root / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "CURRENT_RUN_POINTER_PATH", runs_root / "background_current_pointer.json")

    run_dir = runs_root / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    status = {
        "ok": False,
        "run_id": "apollo_background_current",
        "run_mode": "background",
        "run_dir": str(run_dir),
        "started_at": "2026-04-18T02:00:00Z",
        "current_stage": "hipporag",
        "waiting": {"reason": "gb10_busy"},
        "quality": {"overall_score": 52, "gate_pass": False},
        "stages": {},
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (runs_root / "latest_status.json").write_text(json.dumps(status), encoding="utf-8")
    (runs_root / "background_current_pointer.json").write_text(
        json.dumps({"run_id": "apollo_background_current", "run_dir": str(run_dir.resolve())}),
        encoding="utf-8",
    )

    payload = list_background_run_history(limit=20)
    assert payload["current_run_health"]["ok"] is False
    assert payload["current_run_health"]["stage"] == "hipporag"
    assert payload["current_run_health"]["recommended_next_action"] in {
        "wait_for_next_tick_or_click_run_step_now",
        "run_step_now",
    }


def test_pipeline_config_enforces_gb10_when_focus_universe_enabled():
    config = _pipeline_config(
        {
            "focus_universe_enabled": True,
            "focus_universe_gb10_only": True,
            "allow_cpu_fallback": True,
            "ocr_primary_engine_pdf": "tesseract",
            "ocr_primary_engine_image": "easyocr",
            "ocr_secondary_engine": "olmocr2",
        }
    )
    assert config["ocr_primary_engine_pdf"] == nightly_pipeline.DEFAULT_OCR_PRIMARY_ENGINE_PDF
    assert config["ocr_primary_engine_image"] == nightly_pipeline.DEFAULT_OCR_PRIMARY_ENGINE_IMAGE
    assert config["ocr_secondary_engine"] == nightly_pipeline.DEFAULT_OCR_SECONDARY_ENGINE
    assert config["allow_cpu_fallback"] is False


def test_select_focus_universe_prefers_theme_with_stronger_trusted_evidence(tmp_path, monkeypatch):
    manifest_path = tmp_path / "focus_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "test",
                "themes": [
                    {
                        "id": "ai_compute",
                        "label": "AI Compute",
                        "query_terms": "semiconductor datacenter",
                        "tickers": [
                            {"ticker": "NVDA", "name": "NVIDIA", "weight": 1.0},
                            {"ticker": "AMD", "name": "AMD", "weight": 0.95},
                            {"ticker": "AVGO", "name": "Broadcom", "weight": 0.94},
                            {"ticker": "TSM", "name": "TSMC", "weight": 0.93},
                            {"ticker": "ANET", "name": "Arista", "weight": 0.9},
                        ],
                    },
                    {
                        "id": "energy",
                        "label": "Energy Infrastructure",
                        "query_terms": "oil gas",
                        "tickers": [
                            {"ticker": "XOM", "name": "Exxon", "weight": 1.0},
                            {"ticker": "CVX", "name": "Chevron", "weight": 0.95},
                            {"ticker": "COP", "name": "ConocoPhillips", "weight": 0.93},
                            {"ticker": "SLB", "name": "Schlumberger", "weight": 0.9},
                            {"ticker": "HAL", "name": "Halliburton", "weight": 0.88},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_web_search(query, **_kwargs):
        if "AI Compute" in query or "semiconductor" in query:
            return {
                "ok": True,
                "results": [
                    {
                        "title": "NVIDIA 10-Q filing",
                        "snippet": "NVDA datacenter accelerator revenue and capex.",
                        "url": "https://www.sec.gov/Archives/edgar/data/1045810/nvda-10q.pdf",
                    },
                    {
                        "title": "Arista annual report",
                        "snippet": "ANET networking demand for AI clusters.",
                        "url": "https://investors.arista.com/static-files/anet-annual-report.pdf",
                    },
                    {
                        "title": "TSMC capital spending update",
                        "snippet": "TSM foundry capacity and semiconductor demand.",
                        "url": "https://www.nasdaq.com/articles/tsm-capacity-update.pdf",
                    },
                ],
            }
        return {
            "ok": True,
            "results": [
                {
                    "title": "Oil market blog",
                    "snippet": "general commentary",
                    "url": "https://example.com/oil-commentary",
                }
            ],
        }

    monkeypatch.setattr("Apollo.trading_homework._web_search", fake_web_search)
    config = _pipeline_config({"focus_universe_manifest_path": str(manifest_path), "focus_universe_enabled": True})
    selection = nightly_pipeline._select_focus_universe(config)
    assert selection["ok"] is True
    assert selection["selected_theme"]["id"] == "ai_compute"
    assert len(selection["selected_tickers"]) == 5
    assert selection["selected_tickers"][0]["ticker"] == "NVDA"


def test_compute_focus_decision_gate_returns_ready_for_high_confidence():
    status = {
        "quality": {
            "gate_pass": True,
            "overall_score": 91,
            "confidence_band": {"label": "high"},
        },
        "stage_details": {
            "gather": {"accepted_sources": 6},
            "ocr": {"documents": 6},
            "hipporag": {"processed_finance": 12, "triples": 12},
        },
    }
    config = _pipeline_config({"focus_universe_enabled": True})
    focus_universe = {
        "selected_theme": {"id": "ai_compute", "label": "AI Compute", "theme_score": 82.0},
        "selected_tickers": [
            {"ticker": "NVDA"},
            {"ticker": "AMD"},
            {"ticker": "AVGO"},
            {"ticker": "TSM"},
            {"ticker": "ANET"},
        ],
    }
    decision = nightly_pipeline._compute_focus_decision_gate(status, config, focus_universe)
    assert decision["decision_ready"] is True
    assert decision["decision_mode"] == "next_market_open_plus_15m"
    assert decision["recommended_local_time"]


def test_background_step_blocks_gather_when_focus_universe_needs_gb10(tmp_path, monkeypatch):
    run_id = "apollo_background_current"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")
    monkeypatch.setattr(nightly_pipeline, "pipeline_stopped", lambda: False)
    monkeypatch.setattr(nightly_pipeline, "pipeline_paused", lambda: False)
    monkeypatch.setattr(
        nightly_pipeline,
        "_ensure_gx10_tunnel",
        lambda config, run_dir: {"ok": True, "log_path": str(run_dir / "00_preflight.log")},
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_background_gx10_idle",
        lambda config: {
            "idle": False,
            "active_models": 2,
            "active_models_primary": 2,
            "active_models_deep": 0,
            "max_active_models": 1,
            "idle_threshold_sec": 300,
            "base_url": "http://127.0.0.1:11434",
            "deep_url": "http://127.0.0.1:11435",
        },
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_run_background_subprocess_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stage runner should not be called while gb10 is busy")),
    )

    result = nightly_pipeline.run_background_pipeline_step(
        {
            "run_mode": "background",
            "batch_id": run_id,
            "focus_universe_enabled": True,
            "focus_universe_gb10_only": True,
        }
    )
    assert result["waiting"]["reason"] == "gb10_busy"
    assert result["waiting"]["stage"] == "gather"


def test_background_step_blocks_seed_only_gather_sources(tmp_path, monkeypatch):
    run_id = "apollo_background_current"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")
    monkeypatch.setattr(nightly_pipeline, "pipeline_stopped", lambda: False)
    monkeypatch.setattr(nightly_pipeline, "pipeline_paused", lambda: False)
    monkeypatch.setattr(
        nightly_pipeline,
        "_memory_pressure_snapshot",
        lambda config: {"ok": True, "state": "ok", "reasons": [], "metrics": {}},
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_ensure_gx10_tunnel",
        lambda config, run_dir: {"ok": True, "log_path": str(run_dir / "00_preflight.log")},
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_background_gx10_idle",
        lambda config: {
            "idle": True,
            "active_models": 0,
            "active_models_primary": 0,
            "active_models_deep": 0,
            "max_active_models": 2,
            "idle_threshold_sec": 300,
            "base_url": "http://127.0.0.1:11434",
            "deep_url": "http://127.0.0.1:11435",
        },
    )

    def fake_runner(_run_id, run_dir_arg, stage_name, timeout_sec):
        assert stage_name == "gather"
        return {
            "stage": stage_name,
            "ok": True,
            "return_code": 0,
            "timed_out": False,
            "started_at": "2026-04-23T01:00:00Z",
            "finished_at": "2026-04-23T01:00:10Z",
            "timeout_sec": timeout_sec,
            "log_path": str(run_dir_arg / "01_data_gather.log"),
            "result_path": str(run_dir_arg / "01_data_gather.json"),
            "result": {
                "ok": True,
                "completed": True,
                "sources": [
                    {
                        "url": "C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\homework_seed_docs\\finra_margin_risk_playbook.pdf",
                        "title": "seed doc",
                        "why": "seed",
                    }
                ],
                "quality": {
                    "accepted_count": 1,
                    "accepted_sources": [
                        {
                            "url": "C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\homework_seed_docs\\finra_margin_risk_playbook.pdf",
                            "title": "seed doc",
                            "domain": "local_seed",
                        }
                    ],
                },
                "errors": ["web_search_rate_limited:seed_fallback_local_pdf"],
            },
        }

    monkeypatch.setattr(nightly_pipeline, "_run_background_subprocess_stage", fake_runner)

    result = nightly_pipeline.run_background_pipeline_step(
        {
            "run_mode": "background",
            "batch_id": run_id,
            "focus_universe_enabled": False,
            "focus_universe_gb10_only": False,
        }
    )
    assert result["waiting"]["reason"] == "insufficient_real_sources"
    assert result["current_stage"] == "gather"
    assert result["stages"]["gather"]["ok"] is False
    assert result["stages"]["gather"]["completed"] is False
    assert result["recovery"]["next_action"] == "retry_gather_for_real_sources"
    assert result["waiting"]["source_mix"]["seed_only"] is True


def test_background_step_waits_when_memory_pressure_active(tmp_path, monkeypatch):
    run_id = "apollo_background_current"
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")
    monkeypatch.setattr(nightly_pipeline, "pipeline_stopped", lambda: False)
    monkeypatch.setattr(nightly_pipeline, "pipeline_paused", lambda: False)
    monkeypatch.setattr(
        nightly_pipeline,
        "_memory_pressure_snapshot",
        lambda config: {
            "ok": False,
            "state": "pressure",
            "reasons": ["available_mb_below_min:1024<4096"],
            "metrics": {"available_mb": 1024, "used_percent": 95.0},
            "thresholds": {"min_available_mb": 4096},
        },
    )
    monkeypatch.setattr(
        nightly_pipeline,
        "_run_background_subprocess_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stage runner should not be called under memory pressure")),
    )

    result = nightly_pipeline.run_background_pipeline_step(
        {
            "run_mode": "background",
            "batch_id": run_id,
        }
    )
    assert result["waiting"]["reason"] == "memory_pressure"
    assert "available_mb_below_min:1024<4096" in result["waiting"]["reasons"]


def test_recommended_next_action_memory_pressure():
    status = {"waiting": {"reason": "memory_pressure"}}
    action = nightly_pipeline._recommended_next_action(status)
    assert action == "free_memory_then_retry"


def test_recommended_next_action_insufficient_real_sources():
    status = {"waiting": {"reason": "insufficient_real_sources"}}
    action = nightly_pipeline._recommended_next_action(status)
    assert action == "retry_gather_until_real_sources_available"


def test_run_pipeline_returns_structured_status_when_lock_is_present(tmp_path, monkeypatch):
    monkeypatch.setattr(nightly_pipeline, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(nightly_pipeline, "LATEST_STATUS_PATH", tmp_path / "latest_status.json")
    monkeypatch.setattr(nightly_pipeline, "LOCK_PATH", tmp_path / "nightly.lock")
    monkeypatch.setattr(
        nightly_pipeline,
        "_acquire_lock",
        lambda run_id, run_dir: (_ for _ in ()).throw(RuntimeError("nightly_lock_present:{\"pid\":123}")),
    )

    result = nightly_pipeline.run_pipeline({"batch_id": "apollo_nightly_test"})

    assert result["ok"] is False
    assert result["error"].startswith("lock_acquire_failed:RuntimeError:nightly_lock_present")
    assert result["waiting"]["reason"] == "nightly_lock_present:{\"pid\":123}"
    assert result["finished_at"]
    assert os.path.exists(os.path.join(result["run_dir"], "summary.md"))


def test_apollo_homework_target_recovers_missing_model(monkeypatch):
    monkeypatch.setenv("APOLLO_HOMEWORK_LLM_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("APOLLO_HOMEWORK_LLM_MODEL", "gemma4:31b")
    monkeypatch.delenv("OLLAMA_MODEL_CHAT", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    class _Resp:
        ok = True
        content = b"1"

        @staticmethod
        def json():
            return {
                "models": [
                    {"name": "gemma-4-26b-a4b-q4km-ctx8:latest"},
                    {"name": "gemma3:12b"},
                ]
            }

    monkeypatch.setattr(trading_homework.requests, "get", lambda url, timeout=None: _Resp())

    base_url, model = trading_homework._apollo_llm_target()

    assert base_url == "http://127.0.0.1:11434"
    assert model == "gemma-4-26b-a4b-q4km-ctx8:latest"


def test_background_hipporag_step_blocks_when_corpus_not_ready(tmp_path, monkeypatch):
    run_dir = tmp_path / "background" / "apollo_background_current"
    run_dir.mkdir(parents=True)
    config = _pipeline_config({"run_mode": "background"})
    monkeypatch.setattr(
        nightly_pipeline,
        "_nightly_hipporag_readiness",
        lambda _run_dir=None: {
            "ready": False,
            "rag_dir": str(tmp_path / "nightly_ocr_rag"),
            "collection": "ApolloNightlyOCR_rag",
            "doc_count": 0,
            "min_docs_required": 100,
            "reason": "doc_count_below_min:0<100",
        },
    )
    result = nightly_pipeline._background_stage_hipporag_step(run_dir, config)
    assert result["ok"] is False
    assert result["error"] == "financial_corpus_not_ready"
    assert result["readiness"]["ready"] is False


def test_stage_hipporag_uses_isolated_nightly_corpus(tmp_path, monkeypatch):
    run_dir = tmp_path / "2026-04-19" / "sample_run"
    run_dir.mkdir(parents=True)
    config = _pipeline_config({"run_mode": "quality_sample"})
    calls = {}

    monkeypatch.setattr(
        nightly_pipeline,
        "_nightly_hipporag_readiness",
        lambda _run_dir=None: {
            "ready": True,
            "rag_dir": str(tmp_path / "nightly_ocr_rag"),
            "collection": "ApolloNightlyOCR_rag",
            "doc_count": 128,
            "min_docs_required": 100,
            "reason": "",
        },
    )
    monkeypatch.setattr(nightly_pipeline, "_nightly_hipporag_rag_dir", lambda _run_dir=None: str(tmp_path / "nightly_ocr_rag"))
    monkeypatch.setattr(nightly_pipeline, "_nightly_hipporag_collection", lambda: "ApolloNightlyOCR_rag")

    def fake_build_graph(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "completed": True,
            "remaining_before": 8,
            "remaining_after": 0,
            "processed": 8,
            "processed_finance": 8,
            "triples": 12,
            "validated_non_triple_finance": 1,
            "pending_quarantine": 0,
        }

    monkeypatch.setitem(sys.modules, "Apollo.apollo_hipporag.build_graph", type("BG", (), {"build_graph": staticmethod(fake_build_graph)}))
    result = nightly_pipeline._stage_hipporag(run_dir, config)
    assert result["ok"] is True
    assert calls["rag_dir"] == str(tmp_path / "nightly_ocr_rag")
    assert calls["collection_name"] == "ApolloNightlyOCR_rag"
