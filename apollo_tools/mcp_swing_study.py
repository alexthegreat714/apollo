from __future__ import annotations

from typing import Any, Dict

from common.mcp import register


def _swing_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.swing_data import swing_snapshot

    return swing_snapshot(dict(payload or {}))


def _swing_study(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.swing_study import build_swing_study

    payload = dict(payload or {})
    payload.setdefault("write_artifacts", False)
    return build_swing_study(payload)


def _swing_watchlist(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.swing_study import swing_watchlist

    return swing_watchlist(dict(payload or {}))


def _swing_outcomes(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.swing_study import swing_outcomes

    return swing_outcomes(dict(payload or {}))


def _swing_provider_health(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.swing_study import swing_provider_health

    return swing_provider_health(dict(payload or {}))


register(
    "apollo.swing_snapshot",
    _swing_snapshot,
    {
        "name": "apollo.swing_snapshot",
        "title": "Apollo Swing Snapshot",
        "summary": "Read-only daily technical snapshot for swing-trade research.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.swing_study",
    _swing_study,
    {
        "name": "apollo.swing_study",
        "title": "Apollo Swing Study",
        "summary": "Build long-biased swing setup research. Does not execute trades.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.swing_watchlist",
    _swing_watchlist,
    {
        "name": "apollo.swing_watchlist",
        "title": "Apollo Swing Watchlist",
        "summary": "Read-only latest swing watchlist and lifecycle state.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.swing_outcomes",
    _swing_outcomes,
    {
        "name": "apollo.swing_outcomes",
        "title": "Apollo Swing Outcomes",
        "summary": "Read-only swing proposal outcome ledger.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.swing_provider_health",
    _swing_provider_health,
    {
        "name": "apollo.swing_provider_health",
        "title": "Apollo Swing Provider Health",
        "summary": "Read-only provider freshness and disagreement status for swing data.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)
