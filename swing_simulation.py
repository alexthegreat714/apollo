from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Apollo import risk_scaling, stress_mode

_APOLLO_ROOT = Path(__file__).resolve().parent
OUTCOMES_PATH = _APOLLO_ROOT / "logs" / "swing_study" / "swing_outcomes.json"
SIMULATIONS_PATH = _APOLLO_ROOT / "logs" / "swing_simulation"
SIMULATION_ACCOUNT_PATH = SIMULATIONS_PATH / "account_state.json"
SIMULATION_REPORTS_PATH = SIMULATIONS_PATH / "reports"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    except Exception:
        pass
    return default


def _money(value: Any) -> float:
    return round(_safe_float(value), 2)


def _paper_portfolio_size(default: float = 100.0) -> float:
    return _safe_float(
        os.getenv("APOLLO_PAPER_PORTFOLIO_SIZE")
        or os.getenv("APOLLO_SIM_SIMULATED_BUYING_POWER")
        or os.getenv("APOLLO_SIM_BUYING_POWER"),
        default,
    )


def _execution_realism_profile(*, entry_price: float, shares: float, avg_volume: float = 0.0) -> Dict[str, Any]:
    notional = max(0.0, entry_price * shares)
    adv_notional = max(0.0, avg_volume * entry_price)
    adv_pct = (notional / adv_notional) if adv_notional > 0 else 0.0
    warnings: List[str] = []
    if adv_notional <= 0:
        warnings.append("adv_unknown")
    if adv_pct > 0.01:
        warnings.append("notional_vs_adv_elevated")
    if adv_pct > 0.03:
        warnings.append("partial_fill_possible")
    slippage_bps = 5.0 + min(75.0, adv_pct * 2500.0)
    score = max(0, int(round(100 - slippage_bps - (15 if "partial_fill_possible" in warnings else 0))))
    return {
        "notional": round(notional, 2),
        "avg_volume": int(avg_volume) if avg_volume else 0,
        "adv_notional": round(adv_notional, 2),
        "notional_adv_pct": round(adv_pct * 100, 4),
        "estimated_slippage_bps": round(slippage_bps, 2),
        "partial_fill_warning": "partial_fill_possible" in warnings,
        "warnings": warnings,
        "execution_realism_score": score,
    }


def _refresh_position_buckets(account: Dict[str, Any]) -> None:
    open_positions = [row for row in list(account.get("open_positions") or []) if isinstance(row, dict)]
    closed_positions = [row for row in list(account.get("closed_positions") or []) if isinstance(row, dict)]
    all_positions = open_positions + closed_positions
    account["standard_positions"] = [row for row in all_positions if str(row.get("risk_tier") or "standard") == "standard"]
    account["probe_positions"] = [row for row in all_positions if str(row.get("risk_tier") or "") == "probe"]
    account["aggressive_probe_positions"] = [row for row in all_positions if str(row.get("risk_tier") or "") == "aggressive_probe"]


def _warning_key(warning: Dict[str, Any]) -> str:
    return "|".join(str(warning.get(key) or "") for key in ("code", "ticker", "sim_id", "opened_at", "closed_at"))


def _dedupe_warnings(warnings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    rows: List[Dict[str, Any]] = []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        key = _warning_key(warning)
        if key in seen:
            continue
        seen.add(key)
        rows.append(warning)
    return rows


def _validate_closed_position(position: Dict[str, Any]) -> List[Dict[str, Any]]:
    ticker = str(position.get("ticker") or "").upper()
    fill = _safe_float(position.get("fill_price"), 0.0)
    exit_price = _safe_float(position.get("exit_price"), 0.0)
    realized = _safe_float(position.get("realized_pnl"), 0.0)
    stop = _safe_float(position.get("stop"), 0.0)
    reason = str(position.get("exit_reason") or "")
    profit_stop_validated = bool(position.get("profit_stop_validated") or position.get("trailing_stop_validated"))
    warnings: List[Dict[str, Any]] = []
    if reason == "stop_hit" and realized > 0 and exit_price >= fill and not profit_stop_validated:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "mislabeled_exit_reason",
                "warning_class": "mislabeled_exit_reason",
                "ticker": ticker,
                "reason": "Stopped-out long paper trade showed positive P/L; review whether stop is above entry or exit reason is mislabeled.",
                "fill_price": fill,
                "exit_price": exit_price,
                "stop": stop,
                "realized_pnl": realized,
                "profit_stop_validated": False,
                "opened_at": position.get("opened_at"),
                "closed_at": position.get("closed_at"),
            }
        )
    if reason == "stop_hit" and realized > 0 and exit_price >= fill and profit_stop_validated:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "valid_profit_stop",
                "warning_class": "valid_profit_stop",
                "ticker": ticker,
                "reason": "Long paper trade used an explicit trailing/profit stop, so positive P/L on stop exit is valid.",
                "fill_price": fill,
                "exit_price": exit_price,
                "stop": stop,
                "realized_pnl": realized,
                "profit_stop_validated": True,
                "opened_at": position.get("opened_at"),
                "closed_at": position.get("closed_at"),
            }
        )
    if reason == "stop_hit" and stop > 0 and fill > 0 and stop >= fill:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "invalid_long_stop_above_entry" if not profit_stop_validated else "valid_profit_stop",
                "warning_class": "invalid_long_stop_above_entry" if not profit_stop_validated else "valid_profit_stop",
                "ticker": ticker,
                "reason": "Long setup stop is not below fill price, so it must be labeled as a validated profit/trailing stop before it can close as stop_hit.",
                "fill_price": fill,
                "stop": stop,
                "profit_stop_validated": profit_stop_validated,
                "opened_at": position.get("opened_at"),
                "closed_at": position.get("closed_at"),
            }
        )
    return warnings


def _validate_simulation_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = str(result.get("status") or "")
    entry = _safe_float(result.get("entry_price"), 0.0)
    exit_price = _safe_float(result.get("exit_price"), 0.0)
    stop = _safe_float(result.get("stop"), 0.0)
    target = _safe_float(result.get("target"), 0.0)
    pnl_pct = _safe_float(result.get("pnl_pct"), 0.0)
    profit_stop_validated = bool(result.get("profit_stop_validated") or result.get("trailing_stop_validated"))
    warnings: List[Dict[str, Any]] = []
    if status == "invalid_setup" and str(result.get("invalid_reason") or "") == "invalid_long_stop_above_entry":
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "invalid_long_stop_above_entry",
                "warning_class": "invalid_long_stop_above_entry",
                "sim_id": result.get("sim_id"),
                "ticker": result.get("ticker"),
                "reason": "Long replay was blocked because the stop is not below the entry zone and no profit/trailing stop was validated.",
                "entry_price": entry,
                "entry_low": result.get("entry_low"),
                "stop": stop,
                "profit_stop_validated": False,
            }
        )
    if status == "stopped_out" and pnl_pct > 0 and exit_price >= entry and not profit_stop_validated:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "mislabeled_exit_reason",
                "warning_class": "mislabeled_exit_reason",
                "sim_id": result.get("sim_id"),
                "ticker": result.get("ticker"),
                "reason": "Replay marked the setup stopped_out but exit was above entry, producing positive P/L.",
                "entry_price": entry,
                "exit_price": exit_price,
                "stop": stop,
                "pnl_pct": pnl_pct,
                "profit_stop_validated": False,
            }
        )
    if status == "stopped_out" and pnl_pct > 0 and exit_price >= entry and profit_stop_validated:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "valid_profit_stop",
                "warning_class": "valid_profit_stop",
                "sim_id": result.get("sim_id"),
                "ticker": result.get("ticker"),
                "reason": "Replay used an explicit trailing/profit stop, so positive P/L on stopped_out is valid.",
                "entry_price": entry,
                "exit_price": exit_price,
                "stop": stop,
                "pnl_pct": pnl_pct,
                "profit_stop_validated": True,
            }
        )
    if entry > 0 and stop > 0 and stop >= entry:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "invalid_long_stop_above_entry" if not profit_stop_validated else "valid_profit_stop",
                "warning_class": "invalid_long_stop_above_entry" if not profit_stop_validated else "valid_profit_stop",
                "sim_id": result.get("sim_id"),
                "ticker": result.get("ticker"),
                "reason": "Long setup stop is not below entry.",
                "entry_price": entry,
                "stop": stop,
                "profit_stop_validated": profit_stop_validated,
            }
        )
    if entry > 0 and target > 0 and target <= entry:
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "long_target_not_above_entry",
                "sim_id": result.get("sim_id"),
                "ticker": result.get("ticker"),
                "reason": "Long setup target is not above entry.",
                "entry_price": entry,
                "target": target,
            }
        )
    if status in {"stopped_out", "hit_target"} and (not result.get("exit_date") or exit_price <= 0):
        warnings.append(
            {
                "code": "simulation_math_warning",
                "subcode": "terminal_trade_missing_exit",
                "sim_id": result.get("sim_id"),
                "ticker": result.get("ticker"),
                "reason": "Terminal replay status is missing exit date or exit price.",
            }
        )
    return _dedupe_warnings(warnings)


def _validate_account_state(account: Dict[str, Any]) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    for position in list(account.get("closed_positions") or []):
        if isinstance(position, dict):
            warnings.extend(_validate_closed_position(position))
    warnings = _dedupe_warnings(warnings)
    account["simulation_warnings"] = warnings
    account["simulation_math_warning"] = bool(warnings)
    return warnings


def _tomorrow_date() -> str:
    return (datetime.now(timezone.utc).astimezone() + timedelta(days=1)).date().isoformat()


def _today_date() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def _load_account_state(path: Optional[Path] = None) -> Dict[str, Any]:
    state_path = path or SIMULATION_ACCOUNT_PATH
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    starting = _money(os.getenv("APOLLO_SIM_STARTING_BALANCE", str(_paper_portfolio_size())))
    return {
        "account_id": "apollo_paper_swing_100",
        "mode": "paper_sim",
        "starting_balance": starting,
        "cash_balance": starting,
        "invested_amount": 0.0,
        "market_value": starting,
        "realized_return": 0.0,
        "realized_return_pct": 0.0,
        "unrealized_return": 0.0,
        "unrealized_return_pct": 0.0,
        "status": "not_started",
        "open_positions": [],
        "closed_positions": [],
        "events": [],
        "last_updated": _utc_now(),
    }


def _save_account_state(payload: Dict[str, Any], path: Optional[Path] = None) -> None:
    state_path = path or SIMULATION_ACCOUNT_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _entry_midpoint(entry_zone: Any) -> float:
    low, high = _parse_zone(str(entry_zone or ""))
    if low > 0 and high > 0:
        return round((low + high) / 2.0, 4)
    return 0.0


def _proposal_current_price(row: Dict[str, Any]) -> float:
    snapshot = dict(row.get("swing_snapshot") or {})
    levels = dict(row.get("chart_levels") or {})
    return _money(
        snapshot.get("close")
        or levels.get("current")
        or row.get("current_price")
        or _entry_midpoint(row.get("entry_zone"))
    )


def _latest_daily_candle(row: Dict[str, Any]) -> Dict[str, Any]:
    chart = dict((row.get("swing_snapshot") or {}).get("chart") or {})
    candles = [item for item in list(chart.get("candles") or []) if isinstance(item, dict)]
    if candles:
        return dict(candles[-1])
    snapshot = dict(row.get("swing_snapshot") or {})
    if snapshot.get("as_of") and snapshot.get("close"):
        return {
            "date": str(snapshot.get("as_of") or ""),
            "open": snapshot.get("close"),
            "high": snapshot.get("close"),
            "low": snapshot.get("close"),
            "close": snapshot.get("close"),
            "source": snapshot.get("source") or "swing_snapshot",
        }
    return {}


def _daily_fill_from_proposal(item: Dict[str, Any], proposal: Dict[str, Any], cycle_date: str) -> Dict[str, Any]:
    candle = _latest_daily_candle(proposal)
    candle_date = str(candle.get("date") or "")[:10]
    if not candle_date or candle_date < cycle_date:
        return {"ok": False, "reason": "waiting_for_cycle_daily_candle", "latest_candle_date": candle_date, "cycle_date": cycle_date}
    entry_low, entry_high = _parse_zone(str(item.get("entry_zone") or ""))
    high = _safe_float(candle.get("high"), 0.0)
    low = _safe_float(candle.get("low"), 0.0)
    if entry_low <= 0 or entry_high <= 0 or high <= 0 or low <= 0:
        return {"ok": False, "reason": "missing_daily_entry_data", "latest_candle_date": candle_date}
    if not (low <= entry_high and high >= entry_low):
        return {
            "ok": False,
            "reason": "entry_zone_not_touched",
            "latest_candle_date": candle_date,
            "daily_low": low,
            "daily_high": high,
            "entry_low": entry_low,
            "entry_high": entry_high,
        }
    fill = min(max(_entry_midpoint(item.get("entry_zone")), low), high)
    return {
        "ok": True,
        "fill_price": _money(fill),
        "fill_source": "daily_candle_entry_touch",
        "latest_candle_date": candle_date,
        "daily_open": _safe_float(candle.get("open"), 0.0),
        "daily_high": high,
        "daily_low": low,
        "daily_close": _safe_float(candle.get("close"), 0.0),
    }


def _append_account_event(account: Dict[str, Any], event_type: str, details: Dict[str, Any]) -> None:
    events = [row for row in list(account.get("events") or []) if isinstance(row, dict)]
    events.append({"ts": _utc_now(), "type": event_type, **dict(details or {})})
    account["events"] = events[-500:]


def _update_equity_curve(account: Dict[str, Any]) -> None:
    today = _today_date()
    value = _money(account.get("market_value") or 0.0)
    curve: List[Dict[str, Any]] = [
        row for row in list(account.get("equity_curve") or []) if isinstance(row, dict)
    ]
    if not curve or curve[-1].get("date") != today:
        curve.append({"date": today, "value": value})
    else:
        curve[-1]["value"] = value
    account["equity_curve"] = curve[-500:]
    peak = max((row["value"] for row in curve), default=value)
    account["peak_value"] = _money(peak)
    drawdown = round(((peak - value) / peak) * 100.0, 3) if peak > 0 else 0.0
    account["current_drawdown_pct"] = drawdown
    all_drawdowns = []
    running_peak = curve[0]["value"] if curve else value
    for row in curve:
        if row["value"] > running_peak:
            running_peak = row["value"]
        dd = ((running_peak - row["value"]) / running_peak * 100.0) if running_peak > 0 else 0.0
        all_drawdowns.append(dd)
    account["max_drawdown_pct"] = round(max(all_drawdowns, default=0.0), 3)


def _compute_performance_stats(account: Dict[str, Any]) -> None:
    closed = [row for row in list(account.get("closed_positions") or []) if isinstance(row, dict)]
    if not closed:
        account["performance_stats"] = {
            "total_trades": 0, "win_rate_pct": None, "avg_win_pct": None,
            "avg_loss_pct": None, "expectancy_pct": None, "profit_factor": None,
        }
        return
    wins = [_safe_float(row.get("realized_pnl_pct"), 0.0) for row in closed if _safe_float(row.get("realized_pnl_pct"), 0.0) > 0]
    losses = [_safe_float(row.get("realized_pnl_pct"), 0.0) for row in closed if _safe_float(row.get("realized_pnl_pct"), 0.0) <= 0]
    n = len(closed)
    win_rate = round(len(wins) / n * 100.0, 1) if n else None
    avg_win = round(sum(wins) / len(wins), 3) if wins else None
    avg_loss = round(sum(losses) / len(losses), 3) if losses else None
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
    wr = len(wins) / n if n else 0.0
    lr = len(losses) / n if n else 0.0
    expectancy = round(wr * (avg_win or 0.0) + lr * (avg_loss or 0.0), 3)
    account["performance_stats"] = {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy,
        "profit_factor": profit_factor,
    }


def _previous_equity_close(account: Dict[str, Any]) -> Tuple[float, str]:
    today = _today_date()
    previous_rows: List[Dict[str, Any]] = []
    for row in list(account.get("equity_curve") or []):
        if not isinstance(row, dict):
            continue
        row_date = str(row.get("date") or "").strip()
        if row_date and row_date < today:
            previous_rows.append(row)
    previous_rows.sort(key=lambda row: str(row.get("date") or ""))
    if not previous_rows:
        return 0.0, ""
    last = previous_rows[-1]
    return _money(last.get("value")), str(last.get("date") or "")


def _annotate_day_start(account: Dict[str, Any]) -> None:
    previous_close, previous_date = _previous_equity_close(account)
    if previous_close > 0:
        day_start = previous_close
        source = "previous_equity_close"
    else:
        day_start = _safe_float(account.get("market_value"), _safe_float(account.get("starting_balance"), _paper_portfolio_size()))
        source = "current_market_value"
    account["day_starting_balance"] = _money(day_start)
    account["previous_close_balance"] = _money(previous_close) if previous_close > 0 else None
    account["previous_close_date"] = previous_date
    account["day_starting_balance_source"] = source


def _sync_account_totals(account: Dict[str, Any]) -> None:
    open_positions = [row for row in list(account.get("open_positions") or []) if isinstance(row, dict)]
    closed_positions = [row for row in list(account.get("closed_positions") or []) if isinstance(row, dict)]
    invested = 0.0
    open_value = 0.0
    unrealized = 0.0
    for row in open_positions:
        cost_basis = _safe_float(row.get("cost_basis"), 0.0)
        market_value = _safe_float(row.get("market_value"), cost_basis)
        invested += cost_basis
        open_value += market_value
        unrealized += _safe_float(row.get("unrealized_pnl"), 0.0)
    realized = sum(_safe_float(row.get("realized_pnl"), 0.0) for row in closed_positions)
    starting = max(0.01, _safe_float(account.get("starting_balance"), 100.0))
    account["invested_amount"] = _money(invested)
    account["open_position_value"] = _money(open_value)
    account["market_value"] = _money(_safe_float(account.get("cash_balance"), 0.0) + open_value)
    account["realized_return"] = _money(realized)
    account["realized_return_pct"] = round((realized / starting) * 100.0, 3)
    account["unrealized_return"] = _money(unrealized)
    account["unrealized_return_pct"] = round((unrealized / starting) * 100.0, 3)
    account["status"] = "paper_open" if open_positions else ("paper_closed" if closed_positions else "not_started")
    account["last_updated"] = _utc_now()
    _refresh_position_buckets(account)
    _update_equity_curve(account)
    _annotate_day_start(account)
    _compute_performance_stats(account)


def _mark_open_positions(account: Dict[str, Any], latest_study: Dict[str, Any]) -> None:
    proposals = {
        str(row.get("ticker") or "").upper(): row
        for row in list(latest_study.get("proposals") or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    }
    open_positions = [row for row in list(account.get("open_positions") or []) if isinstance(row, dict)]
    still_open: List[Dict[str, Any]] = []
    closed = [row for row in list(account.get("closed_positions") or []) if isinstance(row, dict)]
    cash = _safe_float(account.get("cash_balance"), _safe_float(account.get("starting_balance"), 100.0))
    for position in open_positions:
        ticker = str(position.get("ticker") or "").upper()
        proposal = dict(proposals.get(ticker) or {})
        current = _proposal_current_price(proposal) if proposal else _safe_float(position.get("last_price"), _safe_float(position.get("fill_price"), 0.0))
        shares = _safe_float(position.get("shares"), 0.0)
        fill = _safe_float(position.get("fill_price"), 0.0)
        stop = _safe_float(position.get("stop"), 0.0)
        target = _safe_float(position.get("target"), 0.0)
        profit_stop_validated = bool(position.get("profit_stop_validated") or position.get("trailing_stop_validated"))
        market_value = _money(shares * current)
        unrealized = _money((current - fill) * shares)
        position.update(
            {
                "last_price": current,
                "last_marked_at": _utc_now(),
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": round(((current - fill) / fill) * 100.0, 3) if fill > 0 else 0.0,
            }
        )
        exit_reason = ""
        exit_price = 0.0
        if stop > 0 and current <= stop and (stop < fill or profit_stop_validated):
            exit_reason = "stop_hit"
            exit_price = stop
        elif target > 0 and current >= target:
            exit_reason = "target_hit"
            exit_price = target
        if exit_reason:
            realized = _money((exit_price - fill) * shares)
            proceeds = _money(exit_price * shares)
            cash = _money(cash + proceeds)
            position.update(
                {
                    "status": "closed",
                    "closed_at": _utc_now(),
                    "exit_reason": exit_reason,
                    "exit_price": exit_price,
                    "realized_pnl": realized,
                    "realized_pnl_pct": round(((exit_price - fill) / fill) * 100.0, 3) if fill > 0 else 0.0,
                    "market_value": 0.0,
                    "unrealized_pnl": 0.0,
                }
            )
            closed.append(position)
            _append_account_event(account, "paper_position_closed", {"ticker": ticker, "exit_reason": exit_reason, "realized_pnl": realized})
        else:
            still_open.append(position)
    account["cash_balance"] = _money(cash)
    account["open_positions"] = still_open
    account["closed_positions"] = closed[-500:]
    _sync_account_totals(account)


def _execute_due_pending_plan(account: Dict[str, Any], latest_study: Dict[str, Any]) -> None:
    pending = dict(account.get("pending_plan") or {})
    if not pending or str(pending.get("status") or "") != "queued_for_next_cycle":
        return
    if str(pending.get("cycle_date") or "") > _today_date():
        return
    plan_id = str(pending.get("plan_id") or f"{pending.get('cycle_date')}:{pending.get('source_run_id')}")
    executed = {str(item) for item in list(account.get("executed_plan_ids") or [])}
    if plan_id in executed:
        return
    cash = _safe_float(account.get("cash_balance"), _safe_float(account.get("starting_balance"), 100.0))
    open_positions = [row for row in list(account.get("open_positions") or []) if isinstance(row, dict)]
    allocations = [row for row in list(pending.get("allocations") or []) if isinstance(row, dict)]
    proposals = {
        str(row.get("ticker") or "").upper(): row
        for row in list(latest_study.get("proposals") or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    }
    deferred: List[Dict[str, Any]] = []
    for item in allocations:
        ticker = str(item.get("ticker") or "").upper()
        if not ticker or any(str(row.get("ticker") or "").upper() == ticker for row in open_positions):
            continue
        fill_check = _daily_fill_from_proposal(item, dict(proposals.get(ticker) or {}), str(pending.get("cycle_date") or ""))
        if not bool(fill_check.get("ok")):
            held = dict(item)
            held["defer_reason"] = fill_check.get("reason")
            held["daily_data_check"] = fill_check
            deferred.append(held)
            continue
        amount = min(cash, _money(item.get("amount")))
        fill = _safe_float(fill_check.get("fill_price"), 0.0)
        if amount <= 0 or fill <= 0:
            continue
        shares = round(amount / fill, 6)
        cost = _money(shares * fill)
        cash = _money(cash - cost)
        position = {
            "ticker": ticker,
            "side": "long",
            "status": "open",
            "risk_tier": item.get("risk_tier") or "standard",
            "paper_only": True,
            "paper_risk_pct": item.get("paper_risk_pct"),
            "max_notional_pct": item.get("max_notional_pct"),
            "aggressive_probe_ready": bool(item.get("aggressive_probe_ready")),
            "pressed_aggressive": bool(item.get("pressed_aggressive")),
            "pressed_aggressive_profile": item.get("pressed_aggressive_profile") or {},
            "graph_risk_appetite": item.get("graph_risk_appetite") or {},
            "aggressive_reward_profile": item.get("aggressive_reward_profile") or {},
            "aggressive_reason": item.get("aggressive_reason") or "",
            "aggressive_next_action": item.get("aggressive_next_action") or "",
            "opened_at": _utc_now(),
            "source_plan_id": plan_id,
            "source_run_id": str(pending.get("source_run_id") or ""),
            "fill_source": fill_check.get("fill_source"),
            "daily_fill_evidence": fill_check,
            "fill_price": fill,
            "shares": shares,
            "cost_basis": cost,
            "entry_zone": item.get("entry_zone"),
            "stop": _parse_price(str(item.get("stop_zone") or "")),
            "target": _parse_price(str(item.get("target_zone") or "")),
            "setup_type": item.get("setup_type"),
            "score": item.get("score"),
            "risk_reward": item.get("risk_reward"),
            "last_price": fill,
            "market_value": cost,
            "unrealized_pnl": 0.0,
            "execution_realism": _execution_realism_profile(
                entry_price=fill,
                shares=shares,
                avg_volume=_safe_float(item.get("avg_volume_30d"), 0.0),
            ),
        }
        open_positions.append(position)
        _append_account_event(account, "paper_position_opened", {"ticker": ticker, "amount": cost, "shares": shares, "fill_price": fill})
    account["cash_balance"] = _money(cash)
    account["open_positions"] = open_positions
    if deferred:
        pending["allocations"] = deferred
        pending["allocation_count"] = len(deferred)
        pending["deferred_at"] = _utc_now()
        pending["defer_reason"] = "waiting_for_real_daily_entry_touch"
        account["pending_plan"] = pending
    else:
        account["pending_plan"] = {}
        account["executed_plan_ids"] = sorted(executed | {plan_id})
        account["last_executed_plan_id"] = plan_id
    _mark_open_positions(account, latest_study)


def _proposal_trade_ready(row: Dict[str, Any]) -> bool:
    setup = str(row.get("setup_type") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    rr = _safe_float(row.get("risk_reward_estimate"), default=0.0)
    if setup in {"no_trade", "extended_no_chase", "broken_trend"}:
        return False
    if status in {"rejected", "invalidated", "expired", "stale"}:
        return False
    return rr >= 1.0


def _rank_candidate(row: Dict[str, Any]) -> float:
    score = _safe_float(row.get("score"), default=0.0)
    rr = max(1.0, _safe_float(row.get("risk_reward_estimate"), default=1.0))
    relative_strength = max(0.0, _safe_float(row.get("relative_strength_rank"), default=50.0)) / 100.0
    return round(score * rr * (0.75 + relative_strength * 0.5), 4)


def _main_simulation_lane() -> str:
    return str(os.getenv("APOLLO_SIM_MAIN_LANE") or "aggressive_news_cycle").strip().lower()


def _catalyst_text(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("ticker", "setup_type", "aggressive_reason", "aggressive_next_action", "no_trade_reason"):
        value = row.get(key)
        if value:
            parts.append(str(value))
    for key in ("catalyst_summary", "source_links", "risk_notes"):
        value = row.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _catalyst_tags(row: Dict[str, Any]) -> List[str]:
    text = _catalyst_text(row)
    tags: List[str] = []
    checks = (
        ("earnings", ("earnings", "q1", "q2", "quarter", "revenue", "eps")),
        ("guidance", ("guidance", "outlook", "forecast", "raises", "raised", "upbeat")),
        ("analyst", ("upgrade", "downgrade", "rating", "price target", "analyst")),
        ("ai_demand", ("ai", "data center", "datacenter", "accelerator", "chip")),
        ("momentum", ("rally", "surge", "soars", "breakout", "volume")),
        ("event_dislocation", ("event_dislocation", "dislocation", "shock")),
    )
    for tag, needles in checks:
        if any(needle in text for needle in needles):
            tags.append(tag)
    return tags


def _catalyst_strength(row: Dict[str, Any]) -> float:
    explicit = _safe_float(row.get("catalyst_quality_score"), 0.0)
    if explicit > 0:
        return min(100.0, explicit)
    blended = _safe_float(row.get("news_blended_score"), 0.0)
    if blended > 0:
        return min(100.0, blended)
    score = 0.0
    if row.get("source_links"):
        score += 35.0
    if row.get("catalyst_summary"):
        score += 35.0
    score += min(30.0, len(_catalyst_tags(row)) * 7.5)
    return min(100.0, score)


def _aggressive_lane_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    score = _safe_float(row.get("score"), 0.0)
    rr = _safe_float(row.get("risk_reward_estimate"), 0.0)
    catalyst = _catalyst_strength(row)
    pressed_flag = bool(row.get("pressed_aggressive")) or bool((row.get("aggressive_reward_profile") or {}).get("pressed_candidate"))
    if pressed_flag or (score >= 70.0 and rr >= 2.0 and catalyst >= 80.0):
        return {
            "profile": "pressed",
            "deploy_cap_pct": 100.0,
            "max_position_pct": 100.0,
            "risk_tier": "pressed_aggressive",
            "reason": "graph/catalyst-backed asymmetric setup qualifies for concentrated simulation sizing",
        }
    if score >= 65.0 and rr >= 1.5 and catalyst >= 65.0:
        return {
            "profile": "aggressive",
            "deploy_cap_pct": 70.0,
            "max_position_pct": 35.0,
            "risk_tier": "aggressive_probe",
            "reason": "fresh catalyst and asymmetric reward/risk qualify for aggressive simulation sizing",
        }
    if rr >= 1.0 and catalyst > 0:
        return {
            "profile": "probe",
            "deploy_cap_pct": 25.0,
            "max_position_pct": 10.0,
            "risk_tier": "probe",
            "reason": "catalyst exists but confidence is limited, so this remains a smaller probe",
        }
    return {}


def _build_aggressive_news_cycle_plan(
    account: Dict[str, Any],
    latest_study: Dict[str, Any],
    risk_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    proposals = [row for row in list(latest_study.get("proposals") or []) if isinstance(row, dict)]
    ready: List[Dict[str, Any]] = []
    for row in proposals:
        if not _proposal_trade_ready(row):
            continue
        profile = _aggressive_lane_profile(row)
        if not profile:
            continue
        enriched = dict(row)
        enriched["_aggressive_lane_profile"] = profile
        ready.append(enriched)
    ready.sort(
        key=lambda row: (
            {"pressed": 3, "aggressive": 2, "probe": 1}.get(str((row.get("_aggressive_lane_profile") or {}).get("profile")), 0),
            _catalyst_strength(row),
            _rank_candidate(row),
        ),
        reverse=True,
    )
    balance = _money(account.get("cash_balance", account.get("starting_balance", 100.0)))
    allocations: List[Dict[str, Any]] = []
    spent = 0.0
    selected_profile: Dict[str, Any] = {}
    for row in ready[:3]:
        if balance <= 0:
            break
        profile = dict(row.get("_aggressive_lane_profile") or {})
        if not selected_profile:
            selected_profile = profile
        deploy_budget = _money(balance * (_safe_float(profile.get("deploy_cap_pct"), 0.0) / 100.0))
        max_position = _money(balance * (_safe_float(profile.get("max_position_pct"), 0.0) / 100.0))
        remaining_budget = _money(max(0.0, deploy_budget - spent))
        amount = _money(min(max_position, remaining_budget))
        if amount <= 0:
            continue
        entry_mid = _entry_midpoint(row.get("entry_zone"))
        shares = round(amount / entry_mid, 6) if entry_mid > 0 else 0.0
        spent = _money(spent + amount)
        profile_name = str(profile.get("profile") or "probe")
        allocations.append(
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "amount": amount,
                "weight_pct": round((amount / balance) * 100, 1) if balance > 0 else 0.0,
                "estimated_shares": shares,
                "entry_zone": row.get("entry_zone"),
                "entry_mid": entry_mid,
                "stop_zone": row.get("stop_zone"),
                "target_zone": row.get("target_zone"),
                "risk_reward": row.get("risk_reward_estimate"),
                "score": row.get("score"),
                "setup_type": row.get("setup_type"),
                "risk_tier": profile.get("risk_tier") or "aggressive_probe",
                "paper_risk_pct": row.get("paper_risk_pct"),
                "max_notional_pct": round(_safe_float(profile.get("max_position_pct"), 0.0) / 100.0, 4),
                "aggressive_probe_ready": profile_name in {"aggressive", "pressed"},
                "pressed_aggressive": profile_name == "pressed",
                "pressed_aggressive_profile": row.get("pressed_aggressive_profile") or {},
                "graph_risk_appetite": row.get("graph_risk_appetite") or {},
                "aggressive_reward_profile": row.get("aggressive_reward_profile") or {},
                "aggressive_reason": row.get("aggressive_reason") or profile.get("reason") or "",
                "aggressive_next_action": "paper_pressed_aggressive_ready" if profile_name == "pressed" else "paper_aggressive_ready",
                "avg_volume_30d": row.get("avg_volume_30d"),
                "catalyst_strength": round(_catalyst_strength(row), 2),
                "catalyst_tags": _catalyst_tags(row),
                "catalyst_summary": list(row.get("catalyst_summary") or [])[:5],
                "source_links": list(row.get("source_links") or [])[:5],
                "lane_profile": profile_name,
                "reason": profile.get("reason") or "aggressive news-cycle simulation candidate",
                "paper_only": True,
                "live_trade_execution": False,
                "human_approval_required": True,
                "production_readiness_credit": False,
            }
        )
    deploy_cap_pct = _safe_float(selected_profile.get("deploy_cap_pct"), 0.0) if selected_profile else 0.0
    max_position_pct = _safe_float(selected_profile.get("max_position_pct"), 0.0) if selected_profile else 0.0
    reserve = _money(max(0.0, balance - spent))
    if allocations:
        status = "queued_for_next_cycle"
        note = "Main lane is aggressive news-cycle simulation: concentrated, catalyst-backed, paper-only, and not production readiness credit."
    else:
        status = "waiting_for_catalyst_setup"
        note = "No fresh catalyst-backed setup cleared aggressive simulation rules; account stays in cash."
    return {
        "status": status,
        "cycle_date": _tomorrow_date(),
        "plan_id": f"{latest_study.get('run_id') or 'swing'}:{_tomorrow_date()}:aggressive_news",
        "source_run_id": latest_study.get("run_id") or "",
        "strategy": "aggressive news-cycle paper simulation",
        "main_lane": "aggressive_news_cycle",
        "available_balance": balance,
        "planned_investment": spent,
        "planned_cash_reserve": reserve,
        "deploy_cap_pct": round(deploy_cap_pct, 1),
        "max_position_pct": round(max_position_pct, 1),
        "allocation_count": len(allocations),
        "allocations": allocations,
        "note": note,
        "risk_tier": f"aggressive_news_{str(selected_profile.get('profile') or 'waiting')}",
        "risk_tier_label": str(selected_profile.get("profile") or "waiting"),
        "risk_ladder": risk_policy or {},
        "paper_only": True,
        "live_trade_execution": False,
        "human_approval_required": True,
        "production_readiness_credit": False,
    }


def _build_next_cycle_plan(account: Dict[str, Any], latest_study: Dict[str, Any], risk_policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        from Apollo.config import get_sector
    except ImportError:
        from config import get_sector
    risk_policy = dict(risk_policy or {})
    if _main_simulation_lane() in {"aggressive_news", "aggressive_news_cycle", "main_aggressive"}:
        return _build_aggressive_news_cycle_plan(account, latest_study, risk_policy=risk_policy)
    caps = dict(risk_policy.get("allocation_caps") or {})
    risk_tier = str(risk_policy.get("risk_tier") or "tier_1_baseline")
    proposals = [row for row in list(latest_study.get("proposals") or []) if isinstance(row, dict)]
    ready = sorted([row for row in proposals if _proposal_trade_ready(row)], key=_rank_candidate, reverse=True)
    if risk_tier == "tier_0_hold":
        ready = []
    balance = _money(account.get("cash_balance", account.get("starting_balance", 100.0)))
    deploy_default = _safe_float(caps.get("deploy_cap_pct"), 70.0) / 100.0
    position_default = _safe_float(caps.get("max_position_pct"), 40.0) / 100.0
    deploy_cap_pct = min(1.0, max(0.0, deploy_default))
    max_position_pct = min(1.0, max(0.0, position_default))
    deploy_budget = _money(balance * deploy_cap_pct)
    max_position = _money(balance * max_position_pct)
    # Collect sectors already occupied by open positions
    open_sectors = {
        get_sector(str(row.get("ticker") or ""))
        for row in list(account.get("open_positions") or [])
        if isinstance(row, dict)
    }
    # Pick up to 3, one per sector
    picked: List[Dict[str, Any]] = []
    used_sectors: set = set(open_sectors)
    for candidate in ready:
        if len(picked) >= 3:
            break
        sector = get_sector(str(candidate.get("ticker") or ""))
        if sector != "other" and sector in used_sectors:
            continue
        picked.append(candidate)
        used_sectors.add(sector)
    weights = [_rank_candidate(row) for row in picked]
    weight_total = sum(weights) or float(len(picked) or 1)
    allocations: List[Dict[str, Any]] = []
    spent = 0.0
    for row, weight in zip(picked, weights):
        raw_amount = deploy_budget * (weight / weight_total)
        amount = _money(min(max_position, raw_amount))
        if amount <= 0:
            continue
        entry_mid = _entry_midpoint(row.get("entry_zone"))
        shares = round(amount / entry_mid, 4) if entry_mid > 0 else 0.0
        spent = _money(spent + amount)
        allocations.append(
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "amount": amount,
                "weight_pct": round((amount / balance) * 100, 1) if balance > 0 else 0.0,
                "estimated_shares": shares,
                "entry_zone": row.get("entry_zone"),
                "entry_mid": entry_mid,
                "stop_zone": row.get("stop_zone"),
                "target_zone": row.get("target_zone"),
                "risk_reward": row.get("risk_reward_estimate"),
                "score": row.get("score"),
                "setup_type": row.get("setup_type"),
                "risk_tier": row.get("risk_tier") or ("aggressive_probe" if row.get("aggressive_probe_ready") else "standard"),
                "paper_risk_pct": row.get("paper_risk_pct"),
                "max_notional_pct": row.get("max_notional_pct"),
                "aggressive_probe_ready": bool(row.get("aggressive_probe_ready")),
                "pressed_aggressive": bool(row.get("pressed_aggressive")),
                "pressed_aggressive_profile": row.get("pressed_aggressive_profile") or {},
                "graph_risk_appetite": row.get("graph_risk_appetite") or {},
                "aggressive_reward_profile": row.get("aggressive_reward_profile") or {},
                "aggressive_reason": row.get("aggressive_reason") or "",
                "aggressive_next_action": row.get("aggressive_next_action") or "",
                "avg_volume_30d": row.get("avg_volume_30d"),
                "reason": f"{str(row.get('setup_type') or 'setup').replace('_', ' ')} with score {row.get('score')} and R/R {row.get('risk_reward_estimate')}",
            }
        )
    reserve = _money(max(0.0, balance - spent))
    if risk_tier == "tier_0_hold":
        plan_status = "risk_hold"
        plan_note = "Risk ladder is holding new paper deployment until truth gates are clean."
    elif not allocations:
        plan_status = "waiting_for_trade_ready_setup"
        plan_note = "No latest setup clears paper deployment rules yet; account stays in cash."
    else:
        plan_status = "queued_for_next_cycle"
        plan_note = "Tomorrow's paper cycle deploys only simulated capital and keeps live broker execution disabled."
    return {
        "status": plan_status,
        "cycle_date": _tomorrow_date(),
        "plan_id": f"{latest_study.get('run_id') or 'swing'}:{_tomorrow_date()}",
        "source_run_id": latest_study.get("run_id") or "",
        "strategy": "risk-capped swing paper simulation",
        "available_balance": balance,
        "planned_investment": spent,
        "planned_cash_reserve": reserve,
        "deploy_cap_pct": round(deploy_cap_pct * 100, 1),
        "max_position_pct": round(max_position_pct * 100, 1),
        "allocation_count": len(allocations),
        "allocations": allocations,
        "note": plan_note,
        "risk_tier": risk_tier,
        "risk_tier_label": risk_policy.get("risk_tier_label") or "",
        "risk_ladder": risk_policy,
    }


def _build_stress_sandbox_plan(account: Dict[str, Any], latest_study: Dict[str, Any]) -> Dict[str, Any]:
    cfg = stress_mode.resolve_stress_mode(_paper_portfolio_size())
    balance = _money(account.get("cash_balance", account.get("starting_balance", cfg.get("paper_portfolio_size", 100.0))))
    proposals = [row for row in list(latest_study.get("proposals") or []) if isinstance(row, dict)]
    ready = sorted([row for row in proposals if _proposal_trade_ready(row)], key=_rank_candidate, reverse=True)
    if not ready:
        ready = sorted(proposals, key=_rank_candidate, reverse=True)
    allocations: List[Dict[str, Any]] = []
    if cfg.get("enabled") and ready and balance > 0:
        candidate = ready[0]
        entry_mid = _entry_midpoint(candidate.get("entry_zone"))
        stop = _parse_price(candidate.get("stop_zone"))
        sizing = stress_mode.stress_position_sizing(
            entry_price=entry_mid,
            stop=stop,
            direction_bias=str(candidate.get("direction_bias") or "long_bias"),
            portfolio_size=balance,
            config={**cfg, "paper_portfolio_size": balance},
        )
        if sizing:
            allocations.append(
                {
                    "ticker": str(candidate.get("ticker") or "").upper(),
                    "amount": sizing.get("planned_notional"),
                    "estimated_shares": sizing.get("fractional_shares"),
                    "entry_zone": candidate.get("entry_zone"),
                    "entry_mid": entry_mid,
                    "stop_zone": candidate.get("stop_zone"),
                    "target_zone": candidate.get("target_zone"),
                    "risk_reward": candidate.get("risk_reward_estimate"),
                    "score": candidate.get("score"),
                    "setup_type": candidate.get("setup_type"),
                    "risk_tier": "stress_sandbox",
                    "stress_position_sizing": sizing,
                    "paper_only": True,
                    "live_trade_execution": False,
                    "human_approval_required": True,
                    "production_readiness_credit": False,
                }
            )
    spent = _money(sum(_safe_float(row.get("amount"), 0.0) for row in allocations))
    return {
        "status": "stress_sandbox_ready" if allocations else ("stress_mode_disabled" if not cfg.get("enabled") else "stress_waiting_for_candidate"),
        "cycle_date": _tomorrow_date(),
        "plan_id": f"{latest_study.get('run_id') or 'swing'}:{_tomorrow_date()}:stress",
        "source_run_id": latest_study.get("run_id") or "",
        "strategy": "high-variance paper-only stress simulation",
        "available_balance": balance,
        "planned_investment": spent,
        "planned_cash_reserve": _money(max(0.0, balance - spent)),
        "deploy_cap_pct": round(_safe_float(cfg.get("max_notional_pct"), 1.0) * 100.0, 1),
        "risk_pct": round(_safe_float(cfg.get("risk_pct"), 0.25) * 100.0, 2),
        "allocation_count": len(allocations),
        "allocations": allocations,
        "simulation_stress_mode": cfg,
        "production_readiness_credit": False,
        "paper_only": True,
        "live_trade_execution": False,
        "human_approval_required": True,
        "note": "Stress sandbox bypasses production truth gates for dynamics visibility only; it does not count as production paper-trading readiness.",
    }


def _account_report_markdown(account_payload: Dict[str, Any]) -> str:
    account = dict(account_payload.get("account") or {})
    plan = dict(account_payload.get("next_cycle_plan") or {})
    latest = dict(account_payload.get("latest_study") or {})
    stress_plan = dict(account_payload.get("stress_sandbox_plan") or {})
    warnings = [row for row in list(account.get("simulation_warnings") or []) if isinstance(row, dict)]
    day_start = account.get("day_starting_balance")
    if day_start is None:
        day_start = account.get("starting_balance")
    previous_close_date = str(account.get("previous_close_date") or "").strip()
    source_suffix = f" (previous close {previous_close_date})" if previous_close_date else ""
    lines = [
        "# Apollo Paper Swing Simulation Report",
        "",
        f"- Generated: {account_payload.get('generated_at')}",
        f"- Mode: {account.get('mode', 'paper_sim')} (live broker execution disabled)",
        f"- Account status: {account.get('status', 'not_started')}",
        f"- Starting balance: ${_money(day_start):.2f}{source_suffix}",
        f"- Original paper seed: ${_money(account.get('starting_balance')):.2f}",
        f"- Current cash balance: ${_money(account.get('cash_balance')):.2f}",
        f"- Currently invested: ${_money(account.get('invested_amount')):.2f}",
        f"- Market value: ${_money(account.get('market_value')):.2f}",
        f"- Realized return: ${_money(account.get('realized_return')):.2f} ({_safe_float(account.get('realized_return_pct')):.2f}%)",
        f"- Unrealized return: ${_money(account.get('unrealized_return')):.2f} ({_safe_float(account.get('unrealized_return_pct')):.2f}%)",
        f"- Peak value: ${_money(account.get('peak_value')):.2f}",
        f"- Max drawdown: {_safe_float(account.get('max_drawdown_pct')):.2f}%",
        f"- Current drawdown: {_safe_float(account.get('current_drawdown_pct')):.2f}%",
        f"- Simulation math warnings: {len(warnings)}",
        f"- Main simulation lane: `{plan.get('main_lane') or _main_simulation_lane()}`",
        f"- Risk tier: {plan.get('risk_tier') or 'tier_1_baseline'}",
        f"- Standard positions tracked: {len(list(account.get('standard_positions') or []))}",
        f"- Probe positions tracked: {len(list(account.get('probe_positions') or []))}",
        f"- Aggressive probe positions tracked: {len(list(account.get('aggressive_probe_positions') or []))}",
        "",
        "## Risk Ladder",
        "",
        f"- Active tier: `{plan.get('risk_tier') or 'tier_1_baseline'}`",
        f"- Good-decision count: `{(plan.get('risk_ladder') or {}).get('good_decision_count', 0)}`",
        f"- Expectancy: `{(plan.get('risk_ladder') or {}).get('expectancy_pct', 0.0)}%`",
        f"- Max drawdown: `{(plan.get('risk_ladder') or {}).get('max_drawdown_pct', 0.0)}%`",
        f"- Truth gates: `{', '.join(k for k, v in dict((plan.get('risk_ladder') or {}).get('truth_gates') or {}).items() if not v) or 'clean'}`",
        f"- Allocation caps: deploy `{plan.get('deploy_cap_pct')}%`, max position `{plan.get('max_position_pct')}%`",
        "- Live execution remains disabled; human approval remains required.",
        "",
        "## Tomorrow's Cycle",
        "",
        f"- Cycle date: {plan.get('cycle_date', '')}",
        f"- Strategy: {plan.get('strategy', '')}",
        f"- Planned investment: ${_money(plan.get('planned_investment')):.2f}",
        f"- Planned cash reserve: ${_money(plan.get('planned_cash_reserve')):.2f}",
        f"- Rule: deploy up to {plan.get('deploy_cap_pct')}% of cash, max {plan.get('max_position_pct')}% per setup.",
        f"- Note: {plan.get('note', '')}",
    ]
    if plan.get("main_lane") == "aggressive_news_cycle":
        allocations_for_lane = list(plan.get("allocations") or [])
        first = dict(allocations_for_lane[0]) if allocations_for_lane else {}
        lines.extend(
            [
                "",
                "## Aggressive News-Cycle Lane",
                "",
                f"- Lane status: `{plan.get('status') or 'unknown'}`",
                f"- Primary candidate: `{first.get('ticker') or 'none'}`",
                f"- Profile: `{first.get('lane_profile') or plan.get('risk_tier_label') or 'waiting'}`",
                f"- Catalyst strength: `{first.get('catalyst_strength') or 0}`",
                f"- Catalyst tags: `{', '.join(list(first.get('catalyst_tags') or [])) or 'none'}`",
                f"- Allocation: ${_money(first.get('amount')):.2f} ({first.get('weight_pct') or 0}%)",
                f"- Guardrail: paper-only, live execution disabled, human approval required, no production-readiness credit.",
            ]
        )
    open_positions = list(account.get("open_positions") or [])
    closed_positions = list(account.get("closed_positions") or [])
    if open_positions:
        lines.extend(["", "## Open Paper Positions"])
        for item in open_positions:
            realism = dict(item.get("execution_realism") or {})
            lines.append(
                f"- {item.get('ticker')}: {item.get('shares')} shares @ ${_money(item.get('fill_price')):.2f}; "
                f"last ${_money(item.get('last_price')):.2f}; unrealized ${_money(item.get('unrealized_pnl')):.2f}; "
                f"tier `{item.get('risk_tier') or 'standard'}`; realism `{realism.get('execution_realism_score', 'n/a')}`"
            )
    if closed_positions:
        lines.extend(["", "## Recent Closed Paper Positions"])
        for item in closed_positions[-5:]:
            lines.append(
                f"- {item.get('ticker')}: {item.get('exit_reason')} @ ${_money(item.get('exit_price')):.2f}; "
                f"realized ${_money(item.get('realized_pnl')):.2f}"
            )
    if warnings:
        lines.extend(["", "## Simulation Math Warnings"])
        for warning in warnings[:10]:
            lines.append(
                f"- {warning.get('ticker') or warning.get('sim_id') or 'unknown'}: "
                f"{warning.get('subcode') or warning.get('code')} - {warning.get('reason') or ''}"
            )
    stats = dict(account.get("performance_stats") or {})
    if stats.get("total_trades"):
        lines.extend(["", "## Performance Stats"])
        lines.extend([
            f"- Total closed trades: {stats.get('total_trades')}",
            f"- Win rate: {stats.get('win_rate_pct')}%  ({stats.get('wins')}W / {stats.get('losses')}L)",
            f"- Avg win: {stats.get('avg_win_pct')}%  |  Avg loss: {stats.get('avg_loss_pct')}%",
            f"- Expectancy: {stats.get('expectancy_pct')}%  |  Profit factor: {stats.get('profit_factor')}",
        ])
    lines.extend(["", "## Planned Spread"])
    allocations = list(plan.get("allocations") or [])
    if allocations:
        for item in allocations:
            lines.append(
                f"- {item.get('ticker')}: ${_money(item.get('amount')):.2f} ({item.get('weight_pct')}%) "
                f"near {item.get('entry_zone')} | stop {item.get('stop_zone')} | target {item.get('target_zone')} | {item.get('reason')}"
            )
    else:
        lines.append("- No deployment yet. Apollo keeps the full paper balance in cash until a setup clears the rules.")
    stress_allocations = list(stress_plan.get("allocations") or [])
    stress_first = dict(stress_allocations[0]) if stress_allocations else {}
    stress_sizing = dict(stress_first.get("stress_position_sizing") or {})
    lines.extend(
        [
            "",
            "## Apollo Stress Simulation",
            "",
            f"- Stress mode: `{bool((stress_plan.get('simulation_stress_mode') or {}).get('enabled'))}`",
            f"- Stress status: `{stress_plan.get('status') or 'unknown'}`",
            f"- Candidate: `{stress_first.get('ticker') or 'none'}`",
            f"- Stress risk percent: `{stress_plan.get('risk_pct')}`",
            f"- Planned stress notional: ${_money(stress_plan.get('planned_investment')):.2f}",
            f"- Estimated fractional shares: `{stress_first.get('estimated_shares') or ''}`",
            f"- Max loss at stop: ${_money(stress_sizing.get('max_loss_dollars')):.2f}",
            f"- Production readiness credit: `{bool(stress_plan.get('production_readiness_credit'))}`",
            "- Warning: stress results are high-variance simulation-only evidence and cannot enable live trading.",
        ]
    )
    lines.extend(
        [
            "",
            "## Source",
            "",
            f"- Latest swing study: {latest.get('run_id', '') or 'none'}",
            f"- Best setup: {latest.get('best_ticker', '') or 'none'}",
            "",
            "This is a paper simulation report only. It does not place live orders.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def get_simulation_account(latest_study: Optional[Dict[str, Any]] = None, *, save_report: bool = True) -> Dict[str, Any]:
    if latest_study is None:
        try:
            from Apollo import swing_study

            latest_study = swing_study.latest_swing_study()
        except Exception:
            latest_study = {}
    latest_study = latest_study if isinstance(latest_study, dict) else {}
    account = _load_account_state()
    default_starting = _paper_portfolio_size()
    account.setdefault("starting_balance", default_starting)
    account.setdefault("cash_balance", account.get("starting_balance", default_starting))
    account.setdefault("invested_amount", 0.0)
    account.setdefault("market_value", _money(_safe_float(account.get("cash_balance")) + _safe_float(account.get("invested_amount"))))
    account.setdefault("status", "not_started")
    account.setdefault("open_positions", [])
    account.setdefault("closed_positions", [])
    account.setdefault("events", [])
    _mark_open_positions(account, latest_study)
    _execute_due_pending_plan(account, latest_study)
    warnings = _validate_account_state(account)
    base_plan = _build_next_cycle_plan(account, latest_study)
    try:
        from Apollo import learning_loop

        learning_state = learning_loop.latest_learning_state()
    except Exception:
        learning_state = {}
    risk_policy = risk_scaling.evaluate_risk_tier(
        account=account,
        latest_study=latest_study,
        next_cycle_plan=base_plan,
        learning_state=learning_state,
        enforce_external_gates=False,
    )
    plan = _build_next_cycle_plan(account, latest_study, risk_policy=risk_policy)
    stress_plan = _build_stress_sandbox_plan(account, latest_study)
    risk_policy = risk_scaling.evaluate_risk_tier(
        account=account,
        latest_study=latest_study,
        next_cycle_plan=plan if plan.get("allocation_count") else base_plan,
        learning_state=learning_state,
        enforce_external_gates=False,
    )
    plan["risk_ladder"] = risk_policy
    plan["risk_tier"] = risk_policy.get("risk_tier")
    plan["risk_tier_label"] = risk_policy.get("risk_tier_label")
    caps = dict(risk_policy.get("allocation_caps") or {})
    plan["deploy_cap_pct"] = caps.get("deploy_cap_pct", plan.get("deploy_cap_pct"))
    plan["max_position_pct"] = caps.get("max_position_pct", plan.get("max_position_pct"))
    if plan.get("allocation_count") and not dict(account.get("pending_plan") or {}):
        account["pending_plan"] = plan
    account["risk_ladder"] = risk_policy
    account["risk_tier"] = risk_policy.get("risk_tier")
    latest_summary = {
        "run_id": latest_study.get("run_id") or "",
        "created_at": latest_study.get("created_at") or "",
        "best_ticker": str((latest_study.get("best_proposal") or {}).get("ticker") or ""),
        "proposal_count": len(list(latest_study.get("proposals") or [])),
    }
    payload = {
        "ok": True,
        "generated_at": _utc_now(),
        "account": account,
        "next_cycle_plan": plan,
        "stress_sandbox_plan": stress_plan,
        "latest_study": latest_summary,
        "simulation_warnings": warnings,
    }
    report_text = _account_report_markdown(payload)
    report_path = ""
    if save_report:
        SIMULATION_REPORTS_PATH.mkdir(parents=True, exist_ok=True)
        report_path_obj = SIMULATION_REPORTS_PATH / "latest_account_report.md"
        report_path_obj.write_text(report_text, encoding="utf-8")
        report_path = str(report_path_obj)
    payload["report"] = {"markdown": report_text, "path": report_path}
    _save_account_state(account)
    return payload


def _parse_zone(zone_str: str) -> Tuple[float, float]:
    """Parse 'low-high' or single price string into (low, high)."""
    s = str(zone_str or "").strip().replace("$", "")
    parts = re.split(r"\s*[-–]\s*", s)
    try:
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
        v = float(parts[0])
        return v * 0.995, v * 1.005
    except (ValueError, IndexError):
        return 0.0, 0.0


def _parse_price(price_str: str) -> float:
    s = str(price_str or "").strip().replace("$", "")
    parts = re.split(r"\s*[-–]\s*", s)
    try:
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _fetch_history(ticker: str, days: int = 90) -> List[Dict[str, Any]]:
    try:
        import yfinance as yf
        from datetime import date, timedelta
        start = (date.today() - timedelta(days=days + 10)).isoformat()
        end = (date.today() + timedelta(days=1)).isoformat()
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        rows = []
        for ts, row in hist.iterrows():
            rows.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return rows[-days:] if len(rows) > days else rows
    except Exception:
        return []


def _simulate_outcome(
    candles: List[Dict[str, Any]],
    entry_low: float,
    entry_high: float,
    stop: float,
    target: float,
    start_date: str = "",
    allow_profit_stop: bool = False,
) -> Dict[str, Any]:
    """
    Replay candle data against entry/stop/target.
    Returns outcome dict with status, entry/exit dates, P&L, and per-candle trace.
    """
    if not candles or entry_low <= 0 or entry_high <= 0 or stop <= 0 or target <= 0:
        return {"status": "insufficient_data", "pnl_pct": None, "pnl_dollars": None}
    if stop >= entry_low and not allow_profit_stop:
        return {
            "status": "invalid_setup",
            "pnl_pct": None,
            "pnl_dollars": None,
            "invalid_reason": "invalid_long_stop_above_entry",
            "profit_stop_validated": False,
        }

    # filter to candles on or after start_date
    start = start_date[:10] if start_date else ""
    relevant = [c for c in candles if not start or c["date"] >= start] or candles

    in_trade = False
    entry_price: Optional[float] = None
    entry_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    status = "open"
    trace = []

    for candle in relevant:
        day_state = {"date": candle["date"], "close": candle["close"], "phase": "watching"}

        if not in_trade:
            # Gap risk: if candle opens above entry zone, can't fill cleanly — skip
            touched_entry_zone = candle["low"] <= entry_high and candle["high"] >= entry_low
            if not touched_entry_zone:
                candle_open = candle.get("open") or candle["high"]
                if float(candle_open) > entry_high * 1.002 and candle["low"] > entry_high:
                    day_state["phase"] = "gap_above_zone"
                trace.append(day_state)
                continue
            if touched_entry_zone:
                entry_price = round((entry_low + entry_high) / 2.0, 4)
                entry_date = candle["date"]
                in_trade = True
                day_state["phase"] = "entered"
                day_state["entry_price"] = entry_price

        if in_trade:
            day_state["phase"] = "in_trade"
            # Check stop first (worse case intraday)
            if candle["low"] <= stop:
                exit_price = stop
                exit_date = candle["date"]
                status = "stopped_out"
                day_state["phase"] = "stopped_out"
                day_state["exit_price"] = exit_price
                trace.append(day_state)
                break
            # Check target
            if candle["high"] >= target:
                exit_price = target
                exit_date = candle["date"]
                status = "hit_target"
                day_state["phase"] = "hit_target"
                day_state["exit_price"] = exit_price
                trace.append(day_state)
                break

        trace.append(day_state)

    if not in_trade:
        status = "never_entered"

    pnl_pct = None
    pnl_dollars = None
    if entry_price and exit_price:
        pnl_dollars = round(exit_price - entry_price, 4)
        pnl_pct = round((pnl_dollars / entry_price) * 100, 3)
    elif entry_price and status == "open":
        last_close = relevant[-1]["close"] if relevant else None
        if last_close:
            pnl_dollars = round(last_close - entry_price, 4)
            pnl_pct = round((pnl_dollars / entry_price) * 100, 3)

    return {
        "status": status,
        "entry_price": entry_price,
        "entry_date": entry_date,
        "exit_price": exit_price,
        "exit_date": exit_date,
        "pnl_pct": pnl_pct,
        "pnl_dollars": pnl_dollars,
        "candles_in_trade": sum(1 for c in trace if c.get("phase") in ("in_trade", "hit_target", "stopped_out")),
        "trace": trace,
    }


def run_simulation(
    research_candidate: Dict[str, Any],
    lookback_days: int = 90,
    save: bool = True,
) -> Dict[str, Any]:
    ticker = str(research_candidate.get("ticker") or "").strip().upper()
    entry_zone_str = str(research_candidate.get("entry_zone") or "")
    stop_str = str(research_candidate.get("stop_zone") or "")
    target_str = str(research_candidate.get("target_zone") or "")
    created_at = str(research_candidate.get("created_at") or research_candidate.get("review_by_date") or "")
    run_id = str(research_candidate.get("run_id") or "")

    entry_low, entry_high = _parse_zone(entry_zone_str)
    stop = _parse_price(stop_str)
    target = _parse_price(target_str)

    candles = _fetch_history(ticker, days=lookback_days)
    profit_stop_validated = bool(research_candidate.get("profit_stop_validated") or research_candidate.get("trailing_stop_validated"))
    outcome = _simulate_outcome(candles, entry_low, entry_high, stop, target, start_date=created_at, allow_profit_stop=profit_stop_validated)

    result = {
        "sim_id": f"sim_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "simulated_at": _utc_now(),
        "ticker": ticker,
        "run_id": run_id,
        "risk_tier": str(research_candidate.get("risk_tier") or "observation"),
        "paper_only": bool(research_candidate.get("paper_only", True)),
        "paper_risk_pct": research_candidate.get("paper_risk_pct"),
        "max_notional_pct": research_candidate.get("max_notional_pct"),
        "aggressive_probe_ready": bool(research_candidate.get("aggressive_probe_ready")),
        "pressed_aggressive": bool(research_candidate.get("pressed_aggressive")),
        "pressed_aggressive_profile": research_candidate.get("pressed_aggressive_profile") or {},
        "graph_risk_appetite": research_candidate.get("graph_risk_appetite") or {},
        "aggressive_reward_profile": research_candidate.get("aggressive_reward_profile") or {},
        "aggressive_reason": str(research_candidate.get("aggressive_reason") or ""),
        "aggressive_next_action": str(research_candidate.get("aggressive_next_action") or ""),
        "live_trade_execution": False,
        "setup_type": str(research_candidate.get("setup_type") or ""),
        "score": research_candidate.get("score"),
        "confidence_label": str(research_candidate.get("confidence_label") or ""),
        "entry_zone": entry_zone_str,
        "stop_zone": stop_str,
        "target_zone": target_str,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop": stop,
        "target": target,
        "profit_stop_validated": profit_stop_validated,
        "rr_proposed": research_candidate.get("risk_reward_estimate"),
        "created_at": created_at,
        "lookback_days": lookback_days,
        "candles": candles,
        **outcome,
    }
    result["execution_realism"] = _execution_realism_profile(
        entry_price=_safe_float(result.get("entry_price"), entry_high or entry_low),
        shares=1.0,
        avg_volume=_safe_float(research_candidate.get("avg_volume_30d"), 0.0),
    )
    warnings = _validate_simulation_result(result)
    result["simulation_warnings"] = warnings
    result["simulation_math_warning"] = bool(warnings)
    result["confidence_summary_eligible"] = not bool(warnings)

    if save:
        _save_outcome(result)

    return result


def _save_outcome(result: Dict[str, Any]) -> None:
    OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if OUTCOMES_PATH.exists():
        try:
            existing = json.loads(OUTCOMES_PATH.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    # Keep last 200 outcomes; deduplicate by sim_id
    existing = [o for o in existing if o.get("sim_id") != result.get("sim_id")]
    existing.append(result)
    existing = existing[-200:]
    OUTCOMES_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also write to per-sim file for dashboard reads
    SIMULATIONS_PATH.mkdir(parents=True, exist_ok=True)
    sim_path = SIMULATIONS_PATH / f"{result['sim_id']}.json"
    sim_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    # Update latest pointer
    (SIMULATIONS_PATH / "latest.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_latest_simulation() -> Optional[Dict[str, Any]]:
    latest = SIMULATIONS_PATH / "latest.json"
    if latest.exists():
        try:
            return json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            pass
    if OUTCOMES_PATH.exists():
        try:
            outcomes = json.loads(OUTCOMES_PATH.read_text(encoding="utf-8"))
            if outcomes:
                return outcomes[-1]
        except Exception:
            pass
    return None


def load_simulation(sim_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not sim_id:
        return get_latest_simulation()
    sim_path = SIMULATIONS_PATH / f"{str(sim_id)}.json"
    if sim_path.exists():
        try:
            return json.loads(sim_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    # Legacy fallback: search latest files for matching id fields.
    sims = list_simulations(limit=500)
    for row in sims:
        if str(row.get("sim_id") or "") == str(sim_id):
            return row
        if str(row.get("simulation_id") or "") == str(sim_id):
            return row
    return None


def list_simulations(limit: int = 20) -> List[Dict[str, Any]]:
    if not OUTCOMES_PATH.exists():
        return []
    try:
        outcomes = json.loads(OUTCOMES_PATH.read_text(encoding="utf-8"))
        if isinstance(outcomes, list):
            return outcomes[-limit:][::-1]
    except Exception:
        pass
    return []


def run_simulation_from_latest_homework(save: bool = True) -> Optional[Dict[str, Any]]:
    from Apollo.homework_trade_pipeline import RUNS_ROOT
    latest_path = RUNS_ROOT / "latest_homework_trade.json"
    if not latest_path.exists():
        return None
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    plan = data.get("trade_plan") or {}
    if plan.get("decision") != "research_candidate":
        # Simulate the top-scored candidate regardless of decision, marked as research-only
        proposals = list((data.get("swing_study") or {}).get("proposals") or [])
        if not proposals:
            return None
        plan = proposals[0]

    candidate = {
        "ticker": plan.get("ticker"),
        "entry_zone": plan.get("entry_zone"),
        "stop_zone": plan.get("stop_zone"),
        "target_zone": plan.get("target_zone"),
        "setup_type": plan.get("setup_type"),
        "score": plan.get("score"),
        "confidence_label": plan.get("confidence_label"),
        "risk_reward_estimate": plan.get("risk_reward_estimate"),
        "created_at": data.get("created_at") or "",
        "run_id": data.get("run_id") or "",
    }
    return run_simulation(candidate, lookback_days=90, save=save)


__all__ = [
    "run_simulation",
    "run_simulation_from_latest_homework",
    "load_simulation",
    "get_latest_simulation",
    "list_simulations",
    "get_simulation_account",
]
