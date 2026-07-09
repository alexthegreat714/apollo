from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set


TIERS: Dict[str, Dict[str, Any]] = {
    "tier_0_hold": {
        "label": "hold",
        "deploy_cap_pct": 0.0,
        "max_position_pct": 0.0,
        "description": "No new paper deployment until truth gates are clean.",
    },
    "tier_1_baseline": {
        "label": "baseline",
        "deploy_cap_pct": 70.0,
        "max_position_pct": 40.0,
        "description": "Current conservative paper-sim allocation.",
    },
    "tier_2_measured": {
        "label": "measured",
        "deploy_cap_pct": 80.0,
        "max_position_pct": 45.0,
        "description": "Scaled paper allocation after a clean evidence window.",
    },
    "tier_3_confident": {
        "label": "confident",
        "deploy_cap_pct": 85.0,
        "max_position_pct": 50.0,
        "description": "Maximum first-version paper allocation after sustained clean performance.",
    },
}

TIER_ORDER = ["tier_0_hold", "tier_1_baseline", "tier_2_measured", "tier_3_confident"]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    except Exception:
        pass
    return default


def _ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _source_tickers(rows: Sequence[Dict[str, Any]]) -> Set[str]:
    return {_ticker(row.get("ticker")) for row in rows if isinstance(row, dict) and _ticker(row.get("ticker"))}


def _trade_ready(row: Dict[str, Any]) -> bool:
    setup = str(row.get("setup_type") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    if setup in {"no_trade", "extended_no_chase", "broken_trend"}:
        return False
    if status in {"rejected", "invalidated", "expired"}:
        return False
    if str(row.get("no_trade_reason") or "").strip():
        return False
    return _safe_float(row.get("risk_reward_estimate"), 0.0) >= 1.0


def _best_study_tickers(latest_study: Dict[str, Any]) -> Set[str]:
    proposals = [row for row in list(latest_study.get("proposals") or []) if isinstance(row, dict)]
    ready = [row for row in proposals if _trade_ready(row)]
    if not ready:
        best = latest_study.get("best_proposal")
        return {_ticker(best.get("ticker"))} if isinstance(best, dict) and _ticker(best.get("ticker")) else set()
    ready.sort(key=lambda row: (_safe_float(row.get("score") or row.get("confidence"), 0.0), _safe_float(row.get("risk_reward_estimate"), 0.0)), reverse=True)
    return {_ticker(ready[0].get("ticker"))}


def resolve_active_paper_intent(
    *,
    account: Optional[Dict[str, Any]] = None,
    latest_study: Optional[Dict[str, Any]] = None,
    next_cycle_plan: Optional[Dict[str, Any]] = None,
    latest_gate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    account = dict(account or {})
    latest_study = dict(latest_study or {})
    next_cycle_plan = dict(next_cycle_plan or {})
    latest_gate = dict(latest_gate or {})
    sources: Dict[str, Set[str]] = {}

    pending = dict(account.get("pending_plan") or {})
    pending_tickers = _source_tickers(list(pending.get("allocations") or []))
    if pending_tickers:
        sources["account_pending_plan"] = pending_tickers

    plan_tickers = _source_tickers(list(next_cycle_plan.get("allocations") or []))
    if plan_tickers:
        sources["next_cycle_plan"] = plan_tickers

    study_tickers = _best_study_tickers(latest_study)
    if study_tickers:
        sources["latest_swing_study"] = study_tickers

    gate_candidate = latest_gate.get("candidate")
    gate_ticker = _ticker(gate_candidate.get("ticker")) if isinstance(gate_candidate, dict) else ""
    gate_decision = str(latest_gate.get("decision") or "").strip()
    if gate_ticker and gate_decision in {"clear_for_paper_sim", "size_down", "blocked_by_news", "blocked_by_price", "needs_human_review"}:
        sources["latest_pretrade_gate"] = {gate_ticker}

    all_tickers = set().union(*sources.values()) if sources else set()
    consistent = True
    if len(all_tickers) > 1:
        consistent = False
    if plan_tickers and study_tickers and not plan_tickers.intersection(study_tickers):
        consistent = False
    if plan_tickers and gate_ticker and gate_ticker not in plan_tickers:
        consistent = False
    if pending_tickers and plan_tickers and pending_tickers != plan_tickers:
        consistent = False

    canonical = sorted(all_tickers)[0] if consistent and len(all_tickers) == 1 else ""
    return {
        "canonical_ticker": canonical,
        "consistent": consistent,
        "sources": {key: sorted(value) for key, value in sources.items()},
        "source_count": len(sources),
        "mismatch": not consistent,
        "mismatch_reason": "plan_truth_mismatch" if not consistent else "",
    }


def _clean_closed_trades(account: Dict[str, Any]) -> List[Dict[str, Any]]:
    warnings = [row for row in list(account.get("simulation_warnings") or []) if isinstance(row, dict)]
    warning_keys = {
        (_ticker(row.get("ticker")), str(row.get("opened_at") or ""), str(row.get("closed_at") or ""))
        for row in warnings
        if str(row.get("warning_class") or row.get("subcode") or "") != "valid_profit_stop"
    }
    out: List[Dict[str, Any]] = []
    for row in list(account.get("closed_positions") or []):
        if not isinstance(row, dict):
            continue
        key = (_ticker(row.get("ticker")), str(row.get("opened_at") or ""), str(row.get("closed_at") or ""))
        if key in warning_keys:
            continue
        out.append(dict(row))
    return out


def _good_decision_count(account: Dict[str, Any], latest_gate: Dict[str, Any]) -> int:
    closed = _clean_closed_trades(account)
    good = sum(1 for row in closed if _safe_float(row.get("realized_pnl_pct"), 0.0) > 0)
    for row in list(account.get("events") or []):
        if isinstance(row, dict) and row.get("type") == "validated_blocked_decision":
            good += 1
    if bool(latest_gate.get("validated_blocker")):
        good += 1
    return good


def _one_tier_down(tier: str) -> str:
    try:
        idx = TIER_ORDER.index(tier)
    except ValueError:
        return "tier_0_hold"
    return TIER_ORDER[max(0, idx - 1)]


def evaluate_risk_tier(
    *,
    account: Optional[Dict[str, Any]] = None,
    latest_study: Optional[Dict[str, Any]] = None,
    next_cycle_plan: Optional[Dict[str, Any]] = None,
    latest_gate: Optional[Dict[str, Any]] = None,
    nightly_truth: Optional[Dict[str, Any]] = None,
    homework_trade: Optional[Dict[str, Any]] = None,
    learning_state: Optional[Dict[str, Any]] = None,
    enforce_external_gates: bool = False,
) -> Dict[str, Any]:
    account = dict(account or {})
    latest_study = dict(latest_study or {})
    next_cycle_plan = dict(next_cycle_plan or {})
    latest_gate = dict(latest_gate or {})
    nightly_truth = dict(nightly_truth or {})
    homework_trade = dict(homework_trade or {})
    learning_state = dict(learning_state or {})
    intent = resolve_active_paper_intent(
        account=account,
        latest_study=latest_study,
        next_cycle_plan=next_cycle_plan,
        latest_gate=latest_gate,
    )
    warnings = [row for row in list(account.get("simulation_warnings") or []) if isinstance(row, dict)]
    non_valid_warnings = [
        row for row in warnings
        if str(row.get("warning_class") or row.get("subcode") or "") != "valid_profit_stop"
    ]
    max_drawdown = _safe_float(account.get("max_drawdown_pct"), 0.0)
    stats = dict(account.get("performance_stats") or {})
    expectancy = _safe_float(stats.get("expectancy_pct"), 0.0)
    good_count = _good_decision_count(account, latest_gate)
    latest_closed = _clean_closed_trades(account)[-1:] or []
    closed = _clean_closed_trades(account)

    truth_gates = {
        "simulation_math_clean": not bool(non_valid_warnings),
        "plan_truth_consistent": bool(intent.get("consistent")),
        "nightly_quality_pass": (not enforce_external_gates) or bool(nightly_truth.get("quality_gate_pass")),
        "homework_trade_present": (not enforce_external_gates) or not bool(homework_trade.get("missing_today")),
        "provider_fresh_for_candidate": _provider_fresh(latest_study, intent),
        "live_guard_disabled": not bool(latest_gate.get("live_guard", {}).get("live_trade_execution")),
        "max_drawdown_within_hard_limit": max_drawdown <= 5.0,
    }
    blockers = [key for key, value in truth_gates.items() if not value]
    rationale: List[str] = []
    if blockers:
        rationale.extend(blockers)
    if blockers:
        tier = "tier_0_hold"
    elif good_count >= 10 and expectancy > 0 and max_drawdown < 3.0:
        tier = "tier_3_confident"
        rationale.append("clean_10_decision_window")
    elif good_count >= 5 and expectancy > 0:
        tier = "tier_2_measured"
        rationale.append("clean_5_decision_window")
    else:
        tier = "tier_1_baseline"
        rationale.append("clean_current_state")

    soft_demotions: List[str] = []
    gate_decision = str(latest_gate.get("decision") or "").strip()
    if gate_decision in {"blocked_by_news", "blocked_by_price", "needs_human_review"}:
        soft_demotions.append(f"latest_gate_{gate_decision}")
    if len(closed) >= 2 and all(str(row.get("exit_reason") or "") == "stop_hit" for row in closed[-2:]):
        soft_demotions.append("two_consecutive_stop_hits")
    if latest_closed and _loss_exceeded_planned_risk(latest_closed[0]):
        soft_demotions.append("latest_loss_exceeded_planned_risk")
    learning_feedback = _learning_feedback(latest_study, intent, learning_state)
    if len(closed) >= 3 and learning_feedback.get("demote"):
        soft_demotions.append(str(learning_feedback.get("reason") or "learning_negative_expectancy"))
    if tier != "tier_0_hold" and soft_demotions:
        tier = _one_tier_down(tier)
        rationale.extend(soft_demotions)

    caps = dict(TIERS[tier])
    return {
        "risk_tier": tier,
        "risk_tier_label": caps["label"],
        "rationale": rationale,
        "truth_gates": truth_gates,
        "active_paper_intent": intent,
        "good_decision_count": good_count,
        "expectancy_pct": expectancy,
        "max_drawdown_pct": max_drawdown,
        "learning_feedback": learning_feedback,
        "allocation_caps": {
            "deploy_cap_pct": caps["deploy_cap_pct"],
            "max_position_pct": caps["max_position_pct"],
        },
        "paper_only": True,
        "live_trade_execution": False,
        "human_approval_required": True,
    }


def _loss_exceeded_planned_risk(row: Dict[str, Any]) -> bool:
    realized = _safe_float(row.get("realized_pnl_pct"), 0.0)
    if realized >= 0:
        return False
    fill = _safe_float(row.get("fill_price"), 0.0)
    stop = _safe_float(row.get("stop"), 0.0)
    if fill <= 0 or stop <= 0:
        return False
    planned = abs((fill - stop) / fill * 100.0)
    return planned > 0 and abs(realized) > planned + 0.25


def _provider_fresh(latest_study: Dict[str, Any], intent: Dict[str, Any]) -> bool:
    ticker = str(intent.get("canonical_ticker") or "").upper()
    if not ticker:
        return True
    proposals = [row for row in list(latest_study.get("proposals") or []) if isinstance(row, dict)]
    proposal = next((row for row in proposals if _ticker(row.get("ticker")) == ticker), {})
    provider = dict(proposal.get("provider_health") or {})
    snapshot = dict(proposal.get("swing_snapshot") or {})
    if provider and provider.get("ok") is False:
        return False
    if _safe_float(provider.get("stale_days"), 0.0) > 1:
        return False
    if str(snapshot.get("freshness") or "").lower() in {"stale", "delayed_stale"}:
        return False
    return True


def _learning_feedback(latest_study: Dict[str, Any], intent: Dict[str, Any], learning_state: Dict[str, Any]) -> Dict[str, Any]:
    groups = dict(learning_state.get("groups") or {})
    if not groups:
        return {"available": False, "demote": False, "reason": ""}
    ticker = str(intent.get("canonical_ticker") or "").upper()
    proposals = [row for row in list(latest_study.get("proposals") or []) if isinstance(row, dict)]
    proposal = next((row for row in proposals if _ticker(row.get("ticker")) == ticker), proposals[0] if proposals else {})
    setup = str(proposal.get("setup_type") or "")
    keys = [
        f"ticker:{ticker.lower()}",
        f"setup_type:{setup.lower()}",
    ]
    checked = []
    demote_reasons = []
    for key in keys:
        group = dict(groups.get(key) or {})
        if not group:
            continue
        sample_count = int(group.get("sample_count") or 0)
        expectancy = _safe_float(group.get("expectancy_pct"), 0.0)
        confidence = _safe_float(group.get("confidence_weight"), 0.0)
        checked.append({"key": key, "sample_count": sample_count, "expectancy_pct": expectancy, "confidence_weight": confidence})
        if sample_count >= 3 and confidence >= 0.5 and expectancy < 0:
            demote_reasons.append(f"{key}_negative_expectancy")
    return {
        "available": bool(checked),
        "demote": bool(demote_reasons),
        "reason": demote_reasons[0] if demote_reasons else "",
        "checked": checked,
        "learning_run_id": learning_state.get("run_id") or "",
    }
