from __future__ import annotations

from typing import Any, Dict

from common.mcp import register


def _event_impact(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.event_impact import build_event_impact

    payload = dict(payload or {})
    payload.setdefault("write_artifacts", False)
    return build_event_impact(payload)


def _event_swing_study(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.swing_study import build_swing_study

    payload = dict(payload or {})
    payload["include_event_impact"] = True
    payload.setdefault("write_artifacts", False)
    return build_swing_study(payload)


def _event_watchlist(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.event_impact import event_watchlist

    return event_watchlist(dict(payload or {}))


def _event_impact_health(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.event_impact import event_impact_health

    return event_impact_health()


def _market_forecast_turn(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.market_forecast_turn import build_market_forecast_turn

    payload = dict(payload or {})
    payload.setdefault("write_artifacts", False)
    payload.setdefault("write_child_artifacts", False)
    return build_market_forecast_turn(payload)


def _market_forecast_health(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.market_forecast_turn import market_forecast_health

    return market_forecast_health()


register(
    "apollo.event_impact",
    _event_impact,
    {
        "name": "apollo.event_impact",
        "title": "Apollo Event Impact",
        "summary": "Classify fresh financial events and estimate market-impact evidence. Does not execute trades.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.market_forecast_turn",
    _market_forecast_turn,
    {
        "name": "apollo.market_forecast_turn",
        "title": "Apollo Market Forecast Turn",
        "summary": "Combine news event impact and swing study into a next-session market estimate plus simulated future turn. Research-only.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.market_forecast_health",
    _market_forecast_health,
    {
        "name": "apollo.market_forecast_health",
        "title": "Apollo Market Forecast Health",
        "summary": "Report readiness for next-session forecast, event impact, OCR97 document hook, and broker safety gates.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.event_swing_study",
    _event_swing_study,
    {
        "name": "apollo.event_swing_study",
        "title": "Apollo Event Swing Study",
        "summary": "Run news event impact and Apollo swing study together. Research-only; no broker orders.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.event_watchlist",
    _event_watchlist,
    {
        "name": "apollo.event_watchlist",
        "title": "Apollo Event Watchlist",
        "summary": "Read latest event-impact watchlist and paper-prep candidates.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)

register(
    "apollo.event_impact_health",
    _event_impact_health,
    {
        "name": "apollo.event_impact_health",
        "title": "Apollo Event Impact Health",
        "summary": "Report event-impact model, cache, and broker-prep readiness.",
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)
