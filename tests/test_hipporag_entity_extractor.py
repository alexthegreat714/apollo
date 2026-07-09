from __future__ import annotations

from Apollo.apollo_hipporag.entity_extractor import extract_entities_regex, extract_triples, is_valid_entity


def test_extract_entities_regex_captures_trading_terms():
    text = "Pattern day trader margin requirements and buying power limits for day trading accounts."
    entities = extract_entities_regex(text)
    assert "pattern day trader" in entities
    assert "margin requirement" in entities or "margin" in entities
    assert "buying power" in entities


def test_extract_entities_regex_captures_ticker_like_tokens():
    text = "AAPL and NVDA are often discussed with intraday volatility."
    entities = extract_entities_regex(text)
    assert "aapl" in entities
    assert "nvda" in entities
    assert "and" not in entities


def test_entity_validation_rejects_stopwords_and_short_fragments():
    assert not is_valid_entity("and")
    assert not is_valid_entity("to")
    assert not is_valid_entity("xy")
    assert is_valid_entity("sec")
    assert is_valid_entity("gm")
    assert is_valid_entity("pattern day trader")


def test_extract_triples_uses_keyword_fallback_when_sparse():
    text = "This guidance focuses on day trading margin requirement and risk management."
    triples = extract_triples(text, "doc-1", use_llm=False)
    assert triples
    assert any("day trading" in t[0] or "day trading" in t[2] for t in triples)
