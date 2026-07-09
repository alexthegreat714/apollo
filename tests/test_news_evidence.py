from __future__ import annotations

import json
from pathlib import Path

import pytest

from Apollo.news_evidence import aggregate_news_evidence, canonicalize_url


pytestmark = pytest.mark.unit


FRESH = "2099-07-09T05:53:55Z"


def _item(title: str, domain: str, tier: str = "C", score: float = 1.0):
    return {
        "ticker": "MSFT",
        "title": title,
        "url": f"https://{domain}/{title.lower().replace(' ', '-')}",
        "origin_domain": domain,
        "source_tier": tier,
        "published": FRESH,
        "sentiment": score,
        "sentiment_confidence": 0.9,
        "sentiment_backend": "keyword_fallback",
    }


def test_canonical_url_removes_tracking_and_fragment():
    result = canonicalize_url("http://www.reuters.com/story?id=7&utm_source=rss#top")

    assert result == "https://reuters.com/story?id=7"


def test_syndicated_duplicates_count_once():
    result = aggregate_news_evidence(
        [
            _item("Microsoft raises cloud guidance after strong demand", "cnbc.com"),
            _item("Microsoft raises cloud guidance after strong demand today", "marketwatch.com"),
            _item("Microsoft raises cloud guidance after strong demand", "cnn.com"),
        ],
        ticker="MSFT",
    )

    assert result["unique_story_count"] == 1
    assert result["deduplicated_count"] == 2
    assert result["standard_sentiment_eligible"] is False
    assert result["standard_cap_reason"] == "duplicate_heavy_news"


def test_trusted_source_with_independent_corroboration_is_standard_eligible():
    result = aggregate_news_evidence(
        [
            _item("Microsoft files earnings update", "sec.gov", "A"),
            _item("Microsoft lifts cloud revenue outlook", "reuters.com", "B"),
        ],
        ticker="MSFT",
    )

    assert result["eligibility_path"] == "trusted_ab"
    assert result["independent_domain_count"] == 2
    assert result["standard_sentiment_eligible"] is True


def test_three_independent_c_tier_domains_can_qualify():
    result = aggregate_news_evidence(
        [
            _item("Microsoft beats quarterly estimates", "cnbc.com"),
            _item("Microsoft raises annual guidance", "marketwatch.com"),
            _item("Microsoft wins major cloud contract", "cnn.com"),
        ],
        ticker="MSFT",
    )

    assert result["eligibility_path"] == "diverse_c"
    assert result["independent_domain_count"] == 3
    assert result["standard_sentiment_eligible"] is True


def test_yahoo_only_and_mixed_evidence_are_capped():
    yahoo = aggregate_news_evidence(
        [_item(f"Microsoft catalyst {index}", "finance.yahoo.com") for index in range(5)],
        ticker="MSFT",
    )
    mixed = aggregate_news_evidence(
        [
            _item("Microsoft demand improves", "cnbc.com", score=1.0),
            _item("Microsoft demand weakens", "marketwatch.com", score=-1.0),
            _item("Microsoft outlook unchanged", "cnn.com", score=0.0),
        ],
        ticker="MSFT",
    )

    assert yahoo["standard_cap_reason"] == "aggregator_only_news"
    assert yahoo["standard_sentiment_eligible"] is False
    assert mixed["standard_cap_reason"] == "mixed_or_neutral_news"
    assert mixed["standard_sentiment_eligible"] is False


def test_operational_baseline_fixtures_are_sanitized():
    fixture_root = Path(__file__).parent / "fixtures" / "trade_cycles"
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(fixture_root.glob("*.json"))]

    assert [row["date"] for row in rows] == ["2026-07-07", "2026-07-09"]
    assert rows[1]["trade_ready"] is True
    assert all("secret" not in json.dumps(row).lower() and "token" not in json.dumps(row).lower() for row in rows)
