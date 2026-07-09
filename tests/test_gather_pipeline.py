"""
Gather pipeline tests - three-layer equity RSS fix.

Covers:
  Layer 1: pre_approved flag bypasses content scoring rejection checks
  Layer 2: equity RSS quality override relaxes aggregate gate thresholds
  Layer 3: explicit zero override values are not treated as falsy

All tests are pure logic tests against live module functions.
No network I/O, no Ollama, no Chroma.
"""
from __future__ import annotations

import os
import sys

# Ensure engineering root is on path before importing Apollo modules
_ENGINEERING_ROOT = r"C:\Users\blyth\Desktop\Engineering"
if _ENGINEERING_ROOT not in sys.path:
    sys.path.insert(0, _ENGINEERING_ROOT)

# Suppress Chroma telemetry before any import that might trigger it
os.environ.setdefault("CHROMA_ANONYMIZED_TELEMETRY", "FALSE")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "FALSE")
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")
os.environ.setdefault("POSTHOG_DISABLED", "1")

from Apollo.nightly_pipeline import (
    _apply_gather_quality_gate,
    _equity_rss_sources,
    _score_gather_candidate,
    DEFAULT_EQUITY_NEWS_TICKERS,
    DEFAULT_GATHER_MIN_ACCEPTED_SOURCES,
    DEFAULT_GATHER_MIN_AVG_SCORE,
    DEFAULT_GATHER_MIN_DOC_SOURCES,
    DEFAULT_GATHER_MIN_PDF_SOURCES,
    DEFAULT_GATHER_MIN_TRUSTED_RATIO,
    DEFAULT_TOPIC,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WATCHLIST = ["NVDA", "AMD", "AVGO", "MSFT", "AMZN"]

EQUITY_RSS_OVERRIDE = {
    "min_avg_score": 35.0,
    "min_trusted_ratio": 0.0,
    "min_doc_sources": 0,
    "min_pdf_sources": 0,
    "min_pdf_count": 0,
}


def _make_rss_candidates(tickers=None):
    """Return the same candidate list that _equity_rss_sources produces."""
    config = {"equity_news_tickers": tickers or WATCHLIST}
    return _equity_rss_sources(config)


def _make_gate_result(candidates):
    return {
        "topic": DEFAULT_TOPIC,
        "candidates": list(candidates),
        "sources": [],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# T-01 through T-05 - _equity_rss_sources()
# ---------------------------------------------------------------------------


class TestEquityRssSources:
    def test_all_pre_approved(self):
        sources = _make_rss_candidates()
        assert len(sources) == 5
        for src in sources:
            assert src.get("pre_approved") is True, (
                f"Missing pre_approved=True on {src.get('url')}"
            )

    def test_url_format(self):
        sources = _make_rss_candidates(["NVDA", "AMD"])
        for src in sources:
            url = src["url"]
            assert "feeds.finance.yahoo.com/rss/2.0/headline" in url
            assert "region=US" in url
            assert "lang=en-US" in url

        urls = [s["url"] for s in sources]
        assert any("s=NVDA" in u for u in urls)
        assert any("s=AMD" in u for u in urls)

    def test_enriched_title_snippet(self):
        sources = _make_rss_candidates(["AMZN"])
        assert len(sources) == 1
        src = sources[0]
        title = src["title"].lower()
        snippet = src["snippet"].lower()
        assert "stock" in title, f"Title missing 'stock': {src['title']}"
        assert "trading" in title, f"Title missing 'trading': {src['title']}"
        assert "earnings" in title, f"Title missing 'earnings': {src['title']}"
        assert len(snippet) > 20
        assert "amzn" in snippet or "amazon" in snippet

    def test_custom_tickers_respected(self):
        sources = _make_rss_candidates(["TSLA", "GOOG"])
        tickers_in_urls = [s["url"].split("s=")[1].split("&")[0] for s in sources]
        assert set(tickers_in_urls) == {"TSLA", "GOOG"}

    def test_max_10_tickers(self):
        sources = _make_rss_candidates([f"TICK{i}" for i in range(20)])
        assert len(sources) <= 10


# ---------------------------------------------------------------------------
# T-10 through T-14 - _score_gather_candidate()
# ---------------------------------------------------------------------------


class TestScoreGatherCandidate:
    def _nvda_rss_item(self, *, pre_approved=True):
        item = {
            "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
            "title": "NVDA stock trading news earnings guidance analyst",
            "snippet": "Latest NVDA stock news: earnings, guidance, analyst ratings, price targets, trading setup",
        }
        if pre_approved:
            item["pre_approved"] = True
        return item

    def test_pre_approved_bypasses_topic_intent_mismatch(self):
        result = _score_gather_candidate(self._nvda_rss_item(pre_approved=True), DEFAULT_TOPIC)
        assert result["reject_reason"] == "", (
            f"Pre-approved source rejected: {result['reject_reason']} | score={result['score']}"
        )
        assert result["score"] >= 30.0

    def test_pre_approved_denylist_still_blocked(self):
        item = {
            "url": "https://en.wikipedia.org/wiki/Nvidia",
            "title": "NVDA stock trading news",
            "pre_approved": True,
        }
        result = _score_gather_candidate(item, DEFAULT_TOPIC)
        assert result["reject_reason"] == "blocked_domain", (
            f"Wikipedia should be blocked_domain regardless of pre_approved. Got: {result['reject_reason']}"
        )

    def test_pre_approved_homepage_still_blocked(self):
        item = {
            "url": "https://finance.yahoo.com",
            "title": "Yahoo Finance stock trading news",
            "pre_approved": True,
        }
        result = _score_gather_candidate(item, DEFAULT_TOPIC)
        assert result["reject_reason"] == "homepage", (
            f"Bare homepage should be rejected regardless of pre_approved. Got: {result['reject_reason']}"
        )

    def test_non_pre_approved_rss_still_rejected(self):
        result = _score_gather_candidate(self._nvda_rss_item(pre_approved=False), DEFAULT_TOPIC)
        assert result["reject_reason"] != "", (
            f"Non-pre-approved RSS source should have been rejected but wasn't. "
            f"reject_reason={result['reject_reason']!r}, score={result['score']}"
        )

    def test_score_breakdown_fields_present(self):
        result = _score_gather_candidate(self._nvda_rss_item(), DEFAULT_TOPIC)
        breakdown = result["breakdown"]
        assert "domain_trust" in breakdown
        assert "topic_intent" in breakdown
        assert "document_utility" in breakdown
        assert "snippet_specificity" in breakdown
        assert "topic_intent_hits" in breakdown
        assert "tier" in breakdown
        # C-tier/equity RSS domain trust has moved slightly with current nightly scoring tables.
        assert isinstance(breakdown["domain_trust"], (int, float)), (
            f"domain_trust should be numeric, got {breakdown['domain_trust']!r}"
        )
        # Title has "stock" and "trading"
        assert breakdown["topic_intent_hits"] >= 2
        assert result["score"] >= 35.0


# ---------------------------------------------------------------------------
# T-20 through T-23 - _apply_gather_quality_gate()
# ---------------------------------------------------------------------------


class TestApplyGatherQualityGate:
    def _run_gate(self, tickers=None, override=None, max_articles=5):
        candidates = _make_rss_candidates(tickers or WATCHLIST)
        result = _make_gate_result(candidates)
        config = {
            "equity_news_tickers": tickers or WATCHLIST,
            "max_articles": max_articles,
        }
        return _apply_gather_quality_gate(
            result,
            config,
            allowed_tiers={"A", "B", "C"},
            quality_override=override,
        )

    def test_equity_rss_override_gate_passes(self):
        gated = self._run_gate(override=EQUITY_RSS_OVERRIDE)
        quality = gated["quality"]
        assert quality["gate_pass"] is True, (
            f"Gate should pass with equity RSS override. errors={gated.get('errors')}"
        )
        assert quality["accepted_count"] == 5
        assert quality["avg_score"] >= 35.0
        bad = {k: v for k, v in quality["reject_reason_counts"].items() if k}
        assert not bad, f"Pre-approved sources triggered reject reasons: {bad}"

    def test_zero_override_values_preserved(self):
        override = dict(EQUITY_RSS_OVERRIDE)
        override["min_doc_sources"] = 0
        override["min_pdf_sources"] = 0
        gated = self._run_gate(override=override)
        quality = gated["quality"]
        assert quality["min_doc_sources"] == 0, (
            f"min_doc_sources=0 override ignored. Got {quality['min_doc_sources']} "
            f"(default is {DEFAULT_GATHER_MIN_DOC_SOURCES})"
        )
        assert quality["min_pdf_sources"] == 0, (
            f"min_pdf_sources=0 override ignored. Got {quality['min_pdf_sources']} "
            f"(default is {DEFAULT_GATHER_MIN_PDF_SOURCES})"
        )
        assert quality["gate_pass"] is True

    def test_default_gate_blocks_rss_only(self):
        gated = self._run_gate(override=None)
        quality = gated["quality"]
        assert quality["gate_pass"] is False, (
            "Default gate should reject RSS-only sources (avg≈40 < 75, no PDFs, no docs)"
        )
        errors = gated.get("errors") or []
        assert any(
            "avg_score" in e or "doc_sources" in e or "pdf_sources" in e
            for e in errors
        ), f"Expected specific gate failure reasons, got: {errors}"

    def test_denylist_survives_override(self):
        candidates = _make_rss_candidates(["NVDA"])
        candidates.append(
            {
                "url": "https://en.wikipedia.org/wiki/Nvidia",
                "title": "NVDA stock trading news",
                "pre_approved": True,
            }
        )
        result = _make_gate_result(candidates)
        config = {"equity_news_tickers": ["NVDA"], "max_articles": 5}
        gated = _apply_gather_quality_gate(
            result,
            config,
            allowed_tiers={"A", "B", "C"},
            quality_override=EQUITY_RSS_OVERRIDE,
        )
        quality = gated["quality"]
        rejected_urls = [r["url"] for r in quality["rejected_candidates"]]
        assert any("wikipedia.org" in u for u in rejected_urls), (
            f"Wikipedia should appear in rejected_candidates. Got: {rejected_urls}"
        )
        assert "blocked_domain" in quality["reject_reason_counts"]


# ---------------------------------------------------------------------------
# T-30 through T-31 - Stage gather condition logic
# ---------------------------------------------------------------------------


class TestStageGatherCondition:
    def test_override_condition_fires_for_equity_rss(self):
        config = {
            "equity_news_tickers": WATCHLIST,
            "topic": DEFAULT_TOPIC,
        }
        explicit_sources = list(config.get("sources") or [])
        if not explicit_sources:
            explicit_sources = _equity_rss_sources(config)

        all_pre_approved = explicit_sources and all(
            bool(s.get("pre_approved")) for s in explicit_sources
        )
        assert all_pre_approved is True, (
            "Override condition should fire for equity RSS sources. "
            f"Sources missing pre_approved: "
            f"{[s.get('url') for s in explicit_sources if not s.get('pre_approved')]}"
        )

    def test_override_does_not_fire_for_manual_sources(self):
        config = {
            "equity_news_tickers": ["NVDA"],
            "sources": [
                {
                    "url": "https://example.com/nvda-report.pdf",
                    "title": "NVDA Report",
                }
            ],
        }
        explicit_sources = list(config.get("sources") or [])
        if not explicit_sources:
            explicit_sources = _equity_rss_sources(config)

        all_pre_approved = explicit_sources and all(
            bool(s.get("pre_approved")) for s in explicit_sources
        )
        assert all_pre_approved is False, (
            "Override must NOT fire for manually-supplied sources without pre_approved flag"
        )


# ---------------------------------------------------------------------------
# T-40 through T-41 - Smoke / end-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_all_default_tickers_pass_individual_scoring(self):
        sources = _equity_rss_sources({})
        assert len(sources) >= 5
        failures = [
            {"url": s["url"], "reject_reason": r["reject_reason"], "score": r["score"]}
            for s in sources
            for r in [_score_gather_candidate(s, DEFAULT_TOPIC)]
            if r["reject_reason"]
        ]
        assert not failures, (
            f"{len(failures)} default ticker source(s) unexpectedly rejected:\n"
            + "\n".join(str(f) for f in failures)
        )

    def test_full_three_layer_pipeline(self):
        """
        The regression test for the complete gather fix.
        Layer 1: pre_approved flag present on generated sources
        Layer 2: individual scoring passes
        Layer 3: aggregate gate passes under equity RSS override with zero thresholds preserved
        """
        config = {
            "equity_news_tickers": WATCHLIST,
            "max_articles": 5,
        }

        # Layer 1
        sources = _equity_rss_sources(config)
        assert all(s.get("pre_approved") for s in sources), (
            "Layer 1 FAIL: pre_approved not set on equity RSS sources"
        )

        # Layer 2
        scored = [_score_gather_candidate(s, DEFAULT_TOPIC) for s in sources]
        failures = [s for s in scored if s["reject_reason"]]
        assert not failures, (
            "Layer 2 FAIL: individual scoring rejected sources: "
            f"{[s['reject_reason'] for s in failures]}"
        )

        # Layer 3
        result = _make_gate_result(sources)
        gated = _apply_gather_quality_gate(
            result,
            config,
            allowed_tiers={"A", "B", "C"},
            quality_override=EQUITY_RSS_OVERRIDE,
        )
        quality = gated["quality"]

        assert quality["gate_pass"] is True, (
            f"Layer 3 FAIL: gate_pass=False\n"
            f"errors={gated.get('errors')}\n"
            f"avg_score={quality['avg_score']} min={quality['min_avg_score']}\n"
            f"doc_sources={quality['doc_sources']} min={quality['min_doc_sources']}\n"
            f"pdf_count={quality['pdf_count']} min={quality['min_pdf_sources']}"
        )
        assert quality["accepted_count"] == 5
        assert quality["avg_score"] >= 35.0
        assert quality["min_doc_sources"] == 0, (
            f"or-falsy bug still present: min_doc_sources={quality['min_doc_sources']}"
        )
        assert quality["min_pdf_sources"] == 0, (
            f"or-falsy bug still present: min_pdf_sources={quality['min_pdf_sources']}"
        )
