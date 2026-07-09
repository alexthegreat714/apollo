from __future__ import annotations

from typing import Any, Dict

from common.mcp import register


def _market_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.market_data import market_snapshot

    return market_snapshot(dict(payload or {}))


def _market_catalysts(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.market_data import market_catalysts

    return market_catalysts(dict(payload or {}))


def _market_study(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.market_study import build_market_study

    return build_market_study(dict(payload or {}))


def _trade_proposal(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.market_study import trade_proposal

    return trade_proposal(dict(payload or {}))


register(
    "apollo.market_snapshot",
    _market_snapshot,
    {
        "name": "apollo.market_snapshot",
        "title": "Apollo Market Snapshot",
        "summary": "Read-only normalized quote snapshot for one or more tickers.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.market_catalysts",
    _market_catalysts,
    {
        "name": "apollo.market_catalysts",
        "title": "Apollo Market Catalysts",
        "summary": "Read-only SEC/news catalyst lookup for one or more tickers.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.market_study",
    _market_study,
    {
        "name": "apollo.market_study",
        "title": "Apollo Market Study",
        "summary": "Build a market-study report and trade-consideration proposals. Does not execute trades.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.trade_proposal",
    _trade_proposal,
    {
        "name": "apollo.trade_proposal",
        "title": "Apollo Trade Proposal",
        "summary": "Return the best execution-ready research proposal with human approval required. Does not execute trades.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)
