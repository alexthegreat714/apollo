"""Simulation execution planning state for broker integration.

This module keeps simulation and execution concerns separated.
Phase 1 created durable execution intents without direct broker calls.
Phase 3 now adds broker-ready order translation (entry/stop/target packets)
for paper execution plans so submission paths can be wired to real adapters later.
Phase 4 adds broker adapter submission and broker-side status sync hooks for
paper simulation flow.
Phase 6 adds post-fill sync back into focus_trading_state.json so filled
orders are reflected in paper position state.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import math
import os
import uuid
import requests

from Apollo import swing_simulation, stress_mode


_APOLLO_ROOT = Path(__file__).resolve().parent
SIMULATION_EXECUTION_ROOT = _APOLLO_ROOT / "logs" / "swing_simulation" / "execution"
SIMULATION_EXECUTION_PLANS = SIMULATION_EXECUTION_ROOT / "plans"
SIMULATION_EXECUTION_QUEUE = SIMULATION_EXECUTION_ROOT / "plan_queue.json"
SIMULATION_EXECUTION_SUBMIT_WINDOW = SIMULATION_EXECUTION_ROOT / "submission_rate_windows.json"
SIMULATION_EXECUTION_RECONCILE_LOCK = SIMULATION_EXECUTION_ROOT / "reconcile.lock"


def _normalize_broker_provider(raw: Any, default: str = "paper_sim") -> str:
    provider = _safe_order_name(raw, default=default)
    if provider in {"paper", "paper_trading", "paper_only", "paper_simulation"}:
        return "paper_sim"
    if provider in {"alpaca", "alpaca_paper", "alpaca-paper", "paper_alpaca", "paperalpaca", "alpaca_paper_trading"}:
        return "alpaca"
    if provider in {"none", "off", "disabled", "no_broker"}:
        return "none"
    return provider


def _normalize_execution_mode(raw: Any, default: str = "paper") -> str:
    candidate = str(raw or default).strip().lower().replace("-", "_")
    if candidate in {"live", "live_only", "real", "real_money", "live_money", "on"}:
        return "live"
    if candidate in {"paper", "paper_only", "paper_sim", "paper_simulation", "sim", "simulation", "dry_run", "off", "disabled", "false", "0"}:
        return "paper"
    return "paper"


def _resolve_sim_execution_mode() -> str:
    return _normalize_execution_mode(
        os.getenv("APOLLO_SIM_EXECUTION_MODE", "paper"),
        default="paper",
    )


def _is_sim_live_enabled() -> bool:
    return _to_bool(os.getenv("APOLLO_SIM_LIVE_ENABLED"), default=False)


def _resolve_broker_retry_settings() -> Dict[str, int]:
    return {
        "max_retries": max(0, _safe_int(os.getenv("APOLLO_SIM_BROKER_MAX_RETRIES", "3"), default=3)),
        "retry_window_sec": max(0, _safe_int(os.getenv("APOLLO_SIM_BROKER_RETRY_WINDOW_SEC", "600"), default=600)),
        "stale_order_sec": max(0, _safe_int(os.getenv("APOLLO_SIM_STALE_ORDER_SECONDS", "1200"), default=1200)),
    }


def _resolve_reconcile_lock_seconds() -> int:
    return max(0, _safe_int(os.getenv("APOLLO_SIM_RECONCILE_LOCK_STALE_SECONDS", "300"), default=300))


def _resolve_reconcile_max_plans() -> int:
    return max(0, _safe_int(os.getenv("APOLLO_SIM_RECONCILE_MAX_PLANS", "0"), default=0))


def _resolve_force_without_approval_enabled() -> bool:
    return _to_bool(os.getenv("APOLLO_SIM_ALLOW_FORCE_WITHOUT_APPROVAL"), default=False)


def _resolve_submit_rate_limit_seconds() -> int:
    return max(0, _safe_int(os.getenv("APOLLO_SIM_MIN_SECONDS_BETWEEN_SUBMISSIONS", "0"), default=0))


def _safe_url(raw: Any, default: str) -> str:
    text = str(raw or "").strip()
    return text if text else default


def _normalize_alpaca_order_type(order_type: str) -> str:
    match str(order_type or "").strip().lower():
        case "stop":
            return "stop"
        case "stop_market":
            return "stop"
        case "stop_limit":
            return "stop_limit"
        case "market":
            return "market"
        case _:
            return "limit"


def _safe_client_order_id(raw: str) -> str:
    text = str(raw or "").strip().replace(" ", "")
    if not text:
        return "apollo_paper_order"
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "._-")
    if not cleaned:
        cleaned = text.replace(" ", "-")
    return cleaned[:48]


def _resolve_alpaca_credentials(mode: str = "paper") -> Dict[str, Any]:
    prefix = "APOLLO_ALPACA_PAPER" if mode == "paper" else "APOLLO_ALPACA_LIVE"
    key = os.getenv(f"{prefix}_API_KEY", "").strip()
    if not key:
        key = os.getenv(f"{prefix}_KEY", "").strip()
    secret = os.getenv(f"{prefix}_API_SECRET", "").strip()
    if not secret:
        secret = os.getenv(f"{prefix}_SECRET", "").strip()
    base_url = _safe_url(
        os.getenv(f"{prefix}_BASE_URL", ""),
        "https://paper-api.alpaca.markets" if mode == "paper" else "https://api.alpaca.markets",
    )
    if not key or not secret:
        raise ValueError(f"alpaca_credentials_missing:{mode}")
    return {
        "api_key": key,
        "secret_key": secret,
        "base_url": base_url.rstrip("/"),
    }


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
        if math.isfinite(num):
            return num
    except Exception:
        pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        num = int(value)
        return num
    except Exception:
        return default


def _iso_to_epoch(value: Any) -> Optional[float]:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return None
    return parsed.timestamp()


def _normalize_actor_key(user: str = "", ip: str = "", client: str = "") -> str:
    user = str(user or "").strip()
    if user:
        return f"user:{user.lower()}"
    client = str(client or "").strip()
    if client:
        return f"client:{client}"
    ip = str(ip or "").strip()
    return f"ip:{ip}" if ip else "unknown"


def _read_submit_rate_state() -> Dict[str, Any]:
    payload = _read_json(SIMULATION_EXECUTION_SUBMIT_WINDOW, default={})
    if isinstance(payload, dict):
        return payload
    return {}


def _write_submit_rate_state(payload: Dict[str, Any]) -> None:
    _write_json(SIMULATION_EXECUTION_SUBMIT_WINDOW, payload)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _seconds_since(timestamp: Any, reference: Optional[datetime] = None) -> Optional[int]:
    if reference is None:
        reference = datetime.now(timezone.utc)
    parsed = _parse_iso_datetime(timestamp)
    if parsed is None:
        return None
    return max(0, int((reference - parsed).total_seconds()))


def _is_reconciler_lock_stale(payload: Dict[str, Any], stale_after_sec: int) -> bool:
    if stale_after_sec <= 0:
        return False
    started_at_epoch = _safe_float(payload.get("started_at_epoch"), default=None)
    if started_at_epoch is None:
        started_at_epoch = _safe_float(payload.get("created_at_epoch"), default=None)
    if started_at_epoch is None:
        started_at_epoch = _seconds_since(payload.get("created_at"), datetime.now(timezone.utc))
    if not started_at_epoch:
        return True
    return (datetime.now(timezone.utc) - datetime.fromtimestamp(started_at_epoch, tz=timezone.utc)).total_seconds() > stale_after_sec


def _acquire_reconcile_lock(*, stale_after_sec: Optional[int] = None) -> Dict[str, Any]:
    stale_after_sec = _resolve_reconcile_lock_seconds() if stale_after_sec is None else max(0, int(stale_after_sec))
    lock_path = SIMULATION_EXECUTION_RECONCILE_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _read_json(lock_path, default=None)
    if isinstance(existing, dict) and existing:
        if not _is_reconciler_lock_stale(existing, stale_after_sec):
            owner = str(existing.get("owner", "unknown"))
            return {
                "ok": False,
                "error": "reconcile_lock_in_use",
                "lock": existing,
            }

    cleared_stale = isinstance(existing, dict) and bool(existing)
    lock_payload: Dict[str, Any] = {
        "owner": os.getpid(),
        "created_at": _utc_iso(),
        "created_at_epoch": _epoch_now(),
        "source": "simulation_execution_reconcile",
        "stale_after_sec": stale_after_sec,
    }
    _write_json(lock_path, lock_payload)
    return {"ok": True, "lock": lock_payload, "stale_lock_cleared": bool(cleared_stale)}


def _release_reconcile_lock() -> None:
    try:
        if SIMULATION_EXECUTION_RECONCILE_LOCK.exists():
            SIMULATION_EXECUTION_RECONCILE_LOCK.unlink()
    except Exception:
        pass


def _epoch_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _within_retry_window(last_attempt_at: Any, window_sec: int, now: Optional[float] = None) -> bool:
    if window_sec <= 0:
        return False
    parsed = _parse_iso_datetime(last_attempt_at)
    if parsed is None:
        return False
    current = datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else datetime.now(timezone.utc)
    return int((current - parsed).total_seconds()) < window_sec


def _is_terminal_order_status(status: str) -> bool:
    return status in {
        "filled",
        "partial_filled",
        "partially_filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "replaced",
        "suspended",
        "error",
        "failed",
    }


def _is_active_order_status(status: str) -> bool:
    return status in {"new", "accepted", "pending_new", "pending_replace", "partially_filled", "activated", "submitted"}


def _to_status_is_filled(raw_status: Any) -> bool:
    return str(raw_status or "").strip().lower() in {"filled", "partial_filled", "partially_filled"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if payload is not None else default
    except Exception:
        return default


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _focus_trading_state_path() -> Optional[Path]:
    raw_path = os.getenv("APOLLO_FOCUS_TRADING_STATE_PATH", "").strip()
    if not raw_path:
        return None
    return Path(raw_path)


def _load_focus_state() -> Optional[Dict[str, Any]]:
    try:
        from Apollo import focus_trading
    except Exception:
        return None

    state_path = _focus_trading_state_path()
    try:
        if state_path is None:
            return focus_trading.load_state()
        return focus_trading.load_state(str(state_path))
    except Exception:
        return None


def _save_focus_state(state: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from Apollo import focus_trading
    except Exception:
        return dict(state or {})

    state_path = _focus_trading_state_path()
    if state_path is None:
        return focus_trading.save_state(state)
    return focus_trading.save_state(state, str(state_path))


def _status_is_filled(raw_status: Any) -> bool:
    status = str(raw_status or "").strip().lower()
    return status in {"filled", "complete", "completed"}


def _safe_fill_price(raw: Any) -> float:
    return _safe_float(raw, default=0.0)


def _upsert_focus_position(state: Dict[str, Any], position: Dict[str, Any]) -> Dict[str, Any]:
    positions = [dict(item) for item in list(state.get("paper_positions") or []) if isinstance(item, dict)]
    execution_plan_id = str(position.get("execution_plan_id") or "").strip()
    ticker = str(position.get("ticker") or "").strip().upper()
    side = str(position.get("stance") or "").strip().lower()

    updated: List[Dict[str, Any]] = []
    matched = False
    for row in positions:
        row_ticker = str(row.get("ticker") or "").strip().upper()
        row_plan = str(row.get("execution_plan_id") or "").strip()
        row_side = str(row.get("stance") or "").strip().lower()
        if not matched and row_ticker == ticker and row_plan == execution_plan_id and row_side == side:
            merged = dict(row)
            merged.update({k: v for k, v in position.items() if v is not None})
            updated.append(merged)
            matched = True
        else:
            updated.append(row)

    if not matched:
        updated.append(position)
    state["paper_positions"] = updated
    return state


def _sync_plan_fill_to_focus_trading(plan: Dict[str, Any], statuses: List[Dict[str, Any]]) -> Dict[str, Any]:
    plan = _ensure_plan_dict(plan)
    if plan is None:
        return {"ok": False, "error": "invalid_plan"}

    broker_state = _ensure_plan_dict(plan.get("broker")) or {}
    order_plan = _ensure_plan_dict(broker_state.get("order_plan")) or {}
    orders = list(order_plan.get("orders") or [])
    if not orders:
        return {"ok": False, "reason": "no_order_plan"}

    order_statuses = {
        str(status.get("order_id") or status.get("broker_order_id") or "").strip(): dict(status)
        for status in statuses
        if isinstance(status, dict)
    }
    entry_orders = [
        dict(item)
        for item in orders
        if str(item.get("label") or "").strip() == "entry_limit" or str(item.get("side") or "").strip().lower() in {"buy", "sell"}
    ]
    if not entry_orders:
        return {"ok": False, "reason": "no_entry_order"}
    entry_order = entry_orders[0]
    entry_order_id = str(entry_order.get("order_id") or "").strip()
    if not entry_order_id:
        return {"ok": False, "reason": "entry_order_id_missing"}
    entry_status = order_statuses.get(entry_order_id, {})
    if not _status_is_filled(entry_status.get("status")):
        return {"ok": False, "reason": "entry_not_filled"}

    focus_state = _load_focus_state()
    if not isinstance(focus_state, dict):
        return {"ok": False, "reason": "focus_state_unavailable"}

    execution_id = str(plan.get("execution_id") or "").strip()
    ticker = str(plan.get("ticker") or "").strip().upper()
    simulation = _ensure_plan_dict(plan.get("simulation")) or {}
    execution = _ensure_plan_dict(plan.get("execution")) or {}
    risk = _ensure_plan_dict(plan.get("risk")) or {}

    exit_orders = [
        dict(item)
        for item in orders
        if str(item.get("label") or "").strip() in {"protective_stop", "profit_taker"}
    ]
    exit_filled = False
    for order in exit_orders:
        status = order_statuses.get(str(order.get("order_id") or ""), {})
        if _status_is_filled(status.get("status")):
            exit_filled = True
            break

    position = {
        "execution_plan_id": execution_id,
        "ticker": ticker,
        "stance": _normalize_order_side(entry_order.get("side") or "buy"),
        "status": "paper_closed" if exit_filled else "paper_open",
        "opened_at": _utc_iso(),
        "entry_order_id": entry_order_id,
        "entry_order_label": str(entry_order.get("label") or "").strip(),
        "broker_provider": str((broker_state.get("provider") or "paper_sim")).strip().lower(),
        "entry_order_provider_status": str(entry_status.get("status") or "").strip(),
        "fill_price": _safe_fill_price(
            entry_status.get("filled_avg_price")
            if entry_status.get("filled_avg_price") is not None
            else entry_status.get("fill_price")
            if entry_status.get("fill_price") is not None
            else entry_status.get("avg_fill_price")
            if entry_status.get("avg_fill_price") is not None
            else entry_status.get("filled")
        ),
        "filled_avg_price": _safe_fill_price(
            entry_status.get("filled_avg_price")
            if entry_status.get("filled_avg_price") is not None
            else entry_status.get("fill_price")
            if entry_status.get("fill_price") is not None
            else entry_status.get("avg_fill_price")
            if entry_status.get("avg_fill_price") is not None
            else entry_status.get("filled")
        ),
        "entry_qty": int(max(0, _safe_int(entry_order.get("qty"), default=0))),
        "paper_position_id": str(entry_status.get("broker_position_id") or entry_status.get("position_id") or entry_order_id),
        "entry_zone": str(simulation.get("entry_zone") or ""),
        "stop_zone": str(simulation.get("stop_zone") or ""),
        "target_zone": str(simulation.get("target_zone") or ""),
        "confidence": _safe_float(simulation.get("score"), default=_safe_float(simulation.get("confidence"), default=0.0)),
        "risk_per_share": _safe_float(risk.get("risk_per_share"), default=0.0),
        "risk_budget_dollars": _safe_float(risk.get("risk_budget_dollars"), default=0.0),
        "notes": str(plan.get("notes") or ""),
    }
    if exit_filled:
        position["closed_at"] = _utc_iso()
        position["exit_order_filled"] = True
        for order in exit_orders:
            status = order_statuses.get(str(order.get("order_id") or ""), {})
            if _status_is_filled(status.get("status")):
                position["exit_order_id"] = str(status.get("order_id") or "")
                position["exit_order_label"] = str(order.get("label") or "")
                position["exit_order_status"] = str(status.get("status") or "")
                position["exit_fill_price"] = _safe_fill_price(
                    status.get("filled_avg_price")
                    if status.get("filled_avg_price") is not None
                    else status.get("fill_price")
                )
                position["exit_order_timestamp"] = _utc_iso()
                break

    updated_state = _upsert_focus_position(focus_state, position)
    _save_focus_state(updated_state)
    return {"ok": True, "updated": True, "ticker": ticker, "execution_id": execution_id, "paper_position_id": position["paper_position_id"]}

def _to_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _set_status(plan: Dict[str, Any], status: str) -> None:
    if status:
        plan["status"] = str(status)


def _save_plan(plan: Dict[str, Any]) -> None:
    execution_id = str(plan.get("execution_id") or "")
    if not execution_id:
        return
    _write_json(_plan_path(execution_id), plan)


def _ensure_plan_dict(plan: Any) -> Optional[Dict[str, Any]]:
    if isinstance(plan, dict):
        return plan
    return None


def _parse_zone(zone_str: str) -> Tuple[float, float]:
    text = str(zone_str or "").strip().replace("$", "")
    if "-" in text:
        left, right = [item.strip() for item in text.split("-", 1)]
    elif "–" in text:
        left, right = [item.strip() for item in text.split("–", 1)]
    elif "—" in text:
        left, right = [item.strip() for item in text.split("—", 1)]
    else:
        try:
            val = float(text)
            return val * 0.995, val * 1.005
        except Exception:
            return 0.0, 0.0
    try:
        return float(left), float(right)
    except Exception:
        return 0.0, 0.0


def _parse_price(price_str: str) -> float:
    text = str(price_str or "").strip().replace("$", "")
    try:
        return float(text)
    except Exception:
        return 0.0


def _normalize_simulation(sim: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(sim, dict):
        return None
    ticker = str(sim.get("ticker") or "").strip().upper()
    if not ticker:
        return None
    direction_bias = _normalize_direction_bias(
        sim.get("direction_bias")
        or sim.get("direction")
        or sim.get("side")
        or sim.get("stance")
    )
    return {
        "ticker": ticker,
        "sim_id": str(sim.get("sim_id") or ""),
        "simulation_id": str(sim.get("simulation_id") or ""),
        "run_id": str(sim.get("run_id") or ""),
        "created_at": str(sim.get("created_at") or ""),
        "entry_zone": str(sim.get("entry_zone") or ""),
        "stop_zone": str(sim.get("stop_zone") or ""),
        "target_zone": str(sim.get("target_zone") or ""),
        "direction_bias": direction_bias,
        "status": str(sim.get("status") or ""),
        "setup_type": str(sim.get("setup_type") or ""),
        "entry_low": _safe_float(sim.get("entry_low")),
        "entry_high": _safe_float(sim.get("entry_high")),
        "stop": _parse_price(str(sim.get("stop") or sim.get("stop_zone") or "")),
        "target": _parse_price(str(sim.get("target") or sim.get("target_zone") or "")),
        "rr_proposed": sim.get("rr_proposed"),
        "score": sim.get("score"),
        "confidence_label": str(sim.get("confidence_label") or ""),
        "avg_volume_30d": _safe_float(sim.get("avg_volume_30d") or sim.get("avg_volume") or sim.get("average_volume")),
        "liquidity": dict(sim.get("liquidity") or {}) if isinstance(sim.get("liquidity"), dict) else {},
        "candles": list(sim.get("candles") or []),
        "simulation_stress_mode": _to_bool(sim.get("simulation_stress_mode"), default=False),
        "stress_probe_ready": _to_bool(sim.get("stress_probe_ready"), default=False),
        "stress_sandbox": dict(sim.get("stress_sandbox") or {}) if isinstance(sim.get("stress_sandbox"), dict) else {},
        "candidate_type": str(sim.get("candidate_type") or ""),
    }


def _normalize_direction_bias(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if text in {"short", "short_bias", "sell", "bear", "bearish"}:
        return "short_bias"
    if text in {"long", "long_bias", "buy", "bull", "bullish", ""}:
        return "long_bias"
    return "long_bias"


def _paper_portfolio_size(default: float = 100.0) -> float:
    return _safe_float(
        os.getenv("APOLLO_PAPER_PORTFOLIO_SIZE")
        or os.getenv("APOLLO_SIM_SIMULATED_BUYING_POWER")
        or os.getenv("APOLLO_SIM_BUYING_POWER"),
        default=default,
    )


def _resolve_risk_settings() -> Dict[str, Any]:
    paper_portfolio_size = _paper_portfolio_size()
    return {
        "paper_portfolio_size": paper_portfolio_size,
        "risk_per_trade_dollars": _safe_float(
            os.getenv("APOLLO_SIM_RISK_PER_TRADE_DOLLARS", str(max(0.01, paper_portfolio_size * 0.01))),
            default=max(0.01, paper_portfolio_size * 0.01),
        ),
        "max_position_qty": _safe_int(os.getenv("APOLLO_SIM_MAX_POSITION_QTY", "0"), default=0),
        "min_position_qty": _safe_int(os.getenv("APOLLO_SIM_MIN_POSITION_QTY", "0"), default=1),
        "max_open_positions": max(
            0,
            _safe_int(os.getenv("APOLLO_SIM_MAX_OPEN_POSITIONS", "5"), default=5),
        ),
        "simulated_buying_power": _safe_float(
            os.getenv("APOLLO_SIM_SIMULATED_BUYING_POWER", os.getenv("APOLLO_SIM_BUYING_POWER", str(paper_portfolio_size))),
            default=paper_portfolio_size,
        ),
    }


def _safe_order_name(raw: Any, default: str = "paper") -> str:
    name = str(raw or "").strip().lower().replace(" ", "_")
    return name if name else default


def _resolve_broker_provider() -> str:
    return _normalize_broker_provider(os.getenv("APOLLO_SIM_BROKER_PROVIDER"), default="paper_sim")


def _normalize_order_side(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"buy", "long"}:
        return "buy"
    if text in {"sell", "short"}:
        return "sell"
    return text


class _PaperSimulationBroker:
    """Minimal paper-trade adapter used for local/simulated execution flow."""

    def __init__(self, state_root: Path | None = None):
        self._state_root = state_root or SIMULATION_EXECUTION_ROOT
        self._state_path = self._state_root / "paper_broker_state.json"
        self._state = self._read_state()
        risk_settings = _resolve_risk_settings()
        self._state.setdefault("buying_power", risk_settings["simulated_buying_power"])

    def _read_state(self) -> Dict[str, Any]:
        payload = _read_json(self._state_path, {"orders": {}, "positions": []})
        if not isinstance(payload, dict):
            payload = {"orders": {}, "positions": []}
        payload.setdefault("orders", {})
        payload.setdefault("positions", [])
        return payload

    def _write_state(self) -> None:
        _write_json(self._state_path, self._state)

    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        order_id = str(order.get("order_id") or "").strip()
        if not order_id:
            return {"ok": False, "error": "missing_order_id"}
        qty = _safe_float(order.get("qty"), default=0.0)
        if qty <= 0:
            return {"ok": False, "error": "invalid_order_qty", "order_id": order_id}
        packet = {
            "execution_id": str(order.get("execution_id") or ""),
            "provider": "paper_sim",
            "order_id": order_id,
            "status": "submitted",
            "submitted_at": _utc_iso(),
            "qty": round(qty, 6),
            "ticker": str(order.get("ticker") or order.get("symbol") or "").strip().upper(),
            "side": str(order.get("side") or "").strip().lower(),
            "order_type": str(order.get("order_type") or "").strip().lower(),
            "label": str(order.get("label") or "").strip(),
            "price": order.get("price"),
            "stop_price": order.get("stop_price"),
        }
        if packet["price"] is None and packet["order_type"] in {"limit", "limit_limit", "limit_stop"}:
            packet["price"] = _safe_float(order.get("stop_price"))
        self._state.setdefault("orders", {})
        self._state["orders"][order_id] = packet
        self._write_state()
        return {
            "ok": True,
            "provider_order_id": order_id,
            "status": "submitted",
            "filled": 0,
            "filled_avg_price": None,
            "order_id": order_id,
        }

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        order = self._state.get("orders", {}).get(str(order_id or ""))
        if not isinstance(order, dict):
            return {"ok": False, "error": "order_not_found", "order_id": str(order_id or "")}
        return {
            "ok": True,
            "order_id": str(order_id),
            "status": str(order.get("status") or "submitted"),
            "broker_order_id": str(order_id),
            "filled": _safe_float(order.get("filled"), default=0.0),
            "filled_avg_price": order.get("filled_avg_price"),
            "submitted_at": str(order.get("submitted_at") or ""),
        }

    def get_positions(self) -> Dict[str, Any]:
        return {"ok": True, "positions": list(self._state.get("positions") or [])}

    def count_open_positions(self) -> int:
        return len(_normalize_open_positions(self.get_positions().get("positions") or []))

    def has_position(self, ticker: str, side: str) -> bool:
        target_ticker = str(ticker or "").strip().upper()
        target_side = _normalize_order_side(side)
        if not target_ticker or target_side not in {"buy", "sell"}:
            return False
        positions = _normalize_open_positions(self.get_positions().get("positions") or [])
        return any(item.get("ticker") == target_ticker and item.get("side") == target_side for item in positions)

    def available_buying_power(self) -> float:
        buying_power = _safe_float(self._state.get("buying_power"), default=0.0)
        if buying_power < 0:
            buying_power = 0.0
        open_positions = _normalize_open_positions(self.get_positions().get("positions") or [])
        for item in open_positions:
            if item.get("side") != "buy":
                continue
            used = _safe_float(item.get("entry_price"), default=0.0) * _safe_float(item.get("qty"), default=0.0)
            buying_power = max(0.0, buying_power - used)
        return buying_power


class _AlpacaPaperBroker:
    """Thin adapter for Alpaca paper trading REST API."""

    def __init__(self, *, state_root: Path | None = None, mode: str = "paper"):
        self._mode = "paper" if str(mode or "paper").lower() not in {"live", "real"} else "live"
        credentials = _resolve_alpaca_credentials(mode=self._mode)
        self._api_key = credentials["api_key"]
        self._api_secret = credentials["secret_key"]
        self._api_base = credentials["base_url"].rstrip("/")
        self._state_root = state_root or SIMULATION_EXECUTION_ROOT
        self._state_path = self._state_root / f"{self._mode}_alpaca_broker_state.json"
        self._state = self._read_state()
        self._state.setdefault("orders", {})
        self._state.setdefault("order_links", {})
        self._state.setdefault("positions", [])

    def _read_state(self) -> Dict[str, Any]:
        payload = _read_json(self._state_path, {"orders": {}, "positions": [], "order_links": {}})
        if not isinstance(payload, dict):
            payload = {"orders": {}, "positions": [], "order_links": {}}
        payload.setdefault("orders", {})
        payload.setdefault("positions", [])
        payload.setdefault("order_links", {})
        return payload

    def _write_state(self) -> None:
        _write_json(self._state_path, self._state)

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, json_body: Any = None, params: Any = None) -> Dict[str, Any]:
        response = requests.request(
            method=method,
            url=f"{self._api_base}{path}",
            headers=self._headers,
            json=json_body,
            params=params,
            timeout=20,
        )
        status = int(getattr(response, "status_code", 0))
        payload = {}
        try:
            payload = response.json() if response is not None else {}
        except Exception:
            payload = {}
        if status >= 400:
            return {"ok": False, "status": status, "error": payload.get("message") or payload.get("code") or str(payload)}
        return {"ok": True, "status": status, "payload": payload}

    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        order_id = str(order.get("order_id") or "").strip()
        if not order_id:
            return {"ok": False, "error": "missing_order_id"}
        qty = _safe_int(order.get("qty"), default=0)
        if qty <= 0:
            return {"ok": False, "error": "invalid_order_qty", "order_id": order_id}

        order_type = _normalize_alpaca_order_type(str(order.get("order_type") or ""))
        order_side = _normalize_order_side(order.get("side") or "buy")
        ticker = str(order.get("ticker") or order.get("symbol") or "").strip().upper()
        if not ticker:
            return {"ok": False, "error": "missing_ticker", "order_id": order_id}
        price = order.get("price")
        stop_price = order.get("stop_price")
        if order_type in {"limit", "limit_limit"}:
            limit_price = _safe_float(price, default=0.0)
        else:
            limit_price = None
        if order_type in {"stop", "stop_limit"}:
            stop_price = _safe_float(stop_price, default=0.0)
        else:
            stop_price = None

        payload = {
            "symbol": ticker,
            "qty": str(qty),
            "side": order_side,
            "type": order_type,
            "time_in_force": "day",
            "client_order_id": _safe_client_order_id(order_id),
        }
        if limit_price is not None:
            payload["limit_price"] = str(round(limit_price, 4))
        if stop_price is not None:
            payload["stop_price"] = str(round(stop_price, 4))

        result = self._request("POST", "/v2/orders", json_body=payload)
        if not result.get("ok"):
            return {"ok": False, "order_id": order_id, "error": "alpaca_submit_failed", "details": result}
        data = result.get("payload") or {}
        broker_order_id = str(data.get("id") or "").strip()
        if not broker_order_id:
            return {"ok": False, "order_id": order_id, "error": "alpaca_submit_failed", "details": "missing_order_id"}
        status = str(data.get("status") or "submitted").strip().lower()
        packet = {
            "provider": "alpaca",
            "order_id": order_id,
            "broker_order_id": broker_order_id,
            "status": status,
            "submitted_at": _utc_iso(),
            "qty": qty,
            "ticker": ticker,
            "side": order_side,
            "order_type": order_type,
            "label": str(order.get("label") or "").strip(),
            "price": price,
            "stop_price": order.get("stop_price"),
        }
        self._state.setdefault("orders", {})
        self._state["orders"][order_id] = packet
        self._state.setdefault("order_links", {})
        self._state["order_links"][order_id] = broker_order_id
        self._write_state()
        return {
            "ok": True,
            "provider_order_id": broker_order_id,
            "order_id": order_id,
            "broker_order_id": broker_order_id,
            "status": status,
            "filled": _safe_int(data.get("filled_qty"), default=0),
            "filled_avg_price": _safe_float(data.get("filled_avg_price"), default=0.0),
        }

    def _find_order_payload(self, order_id: str) -> Optional[Dict[str, Any]]:
        normalized = _safe_client_order_id(order_id)
        broker_id = self._state.get("order_links", {}).get(order_id)
        if broker_id:
            response = self._request("GET", f"/v2/orders/{broker_id}")
            if response.get("ok"):
                return response.get("payload")
        response = self._request("GET", "/v2/orders", params={"client_order_id": normalized, "status": "all"})
        if not response.get("ok"):
            return None
        orders = response.get("payload") if isinstance(response.get("payload"), list) else []
        if not isinstance(orders, list):
            return None
        for item in orders:
            if not isinstance(item, dict):
                continue
            if str(item.get("client_order_id") or "") == normalized:
                return item
        return None

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        payload = self._find_order_payload(str(order_id or ""))
        if not payload:
            return {"ok": False, "error": "order_not_found", "order_id": str(order_id or "")}
        status_raw = str(payload.get("status") or "").strip().lower()
        return {
            "ok": True,
            "order_id": str(order_id),
            "broker_order_id": str(payload.get("id") or ""),
            "status": status_raw,
            "filled": int(float(payload.get("filled_qty") or 0)),
            "filled_avg_price": _safe_float(payload.get("filled_avg_price"), default=0.0),
            "submitted_at": str(payload.get("created_at") or ""),
        }

    def get_positions(self) -> Dict[str, Any]:
        response = self._request("GET", "/v2/positions")
        if not response.get("ok"):
            return {"ok": False, "error": "positions_lookup_failed", "details": response.get("payload")}
        positions: List[Dict[str, Any]] = []
        for item in response.get("payload") or []:
            if not isinstance(item, dict):
                continue
            positions.append({
                "ticker": str(item.get("symbol") or "").strip().upper(),
                "side": _normalize_order_side(item.get("side")),
                "status": "open",
                "qty": _safe_int(item.get("qty"), default=0),
                "entry_price": _safe_float(item.get("avg_entry_price"), default=0.0),
            })
        return {"ok": True, "positions": positions}

    def available_buying_power(self) -> float:
        response = self._request("GET", "/v2/account")
        if not response.get("ok"):
            return 0.0
        payload = response.get("payload") or {}
        return _safe_float(payload.get("buying_power"), default=0.0)


def _normalize_open_positions(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        status = str(row.get("status") or "open").strip().lower()
        if status == "closed":
            continue
        side = _normalize_order_side(row.get("side") or row.get("stance"))
        if side not in {"buy", "sell"}:
            side = _normalize_order_side("buy" if str(row.get("qty", "")).strip().startswith("-") else "buy")
        normalized.append({
            "ticker": ticker,
            "side": side,
            "status": status,
            "qty": _safe_int(row.get("qty"), default=0),
            "entry_price": _safe_float(row.get("entry_price"), default=0.0),
        })
    return normalized


def _first_entry_order(order_plan: Dict[str, Any]) -> Dict[str, Any]:
    orders = list(order_plan.get("orders") or [])
    if orders and isinstance(orders, list):
        for order in orders:
            if isinstance(order, dict) and str(order.get("label") or "").strip().lower() == "entry_limit":
                return order
    return orders[0] if orders and isinstance(orders[0], dict) else {}


def _check_risk_controls_for_plan(
    *,
    broker_client: Any,
    execution_id: str,
    order_plan: Dict[str, Any],
    risk_settings: Dict[str, Any],
) -> Dict[str, Any]:
    positions_result = getattr(broker_client, "get_positions", None)
    if not callable(positions_result):
        return {
            "ok": False,
            "error": "risk_controls_failed",
            "reason": "broker_get_positions_not_implemented",
            "execution_id": execution_id,
        }
    positions_payload = positions_result()
    if not isinstance(positions_payload, dict) or not positions_payload.get("ok", True):
        return {
            "ok": False,
            "error": "risk_controls_failed",
            "reason": "broker_positions_lookup_failed",
            "execution_id": execution_id,
            "provider_positions": positions_payload,
        }

    positions = _normalize_open_positions(positions_payload.get("positions") or [])
    max_open_positions = _safe_int(risk_settings.get("max_open_positions"), default=0)
    if max_open_positions > 0 and len(positions) >= max_open_positions:
        return {
            "ok": False,
            "error": "risk_control_open_position_limit",
            "execution_id": execution_id,
            "max_open_positions": max_open_positions,
            "open_positions": len(positions),
        }

    entry_order = _first_entry_order(order_plan)
    side = _normalize_order_side(entry_order.get("side") or "")
    ticker = str(entry_order.get("ticker") or order_plan.get("ticker") or "").strip().upper()
    if side in {"buy", "sell"} and any(item["ticker"] == ticker and item["side"] == side for item in positions):
        return {
            "ok": False,
            "error": "risk_control_duplicate_side",
            "execution_id": execution_id,
            "ticker": ticker,
            "side": side,
            "open_positions": positions,
        }

    if side == "buy":
        order_qty = _safe_float(entry_order.get("qty"), default=0.0)
        order_price = _safe_float(entry_order.get("price"), default=0.0)
        notional_required = max(0.0, order_qty * order_price)
        available_power = None
        get_power = getattr(broker_client, "available_buying_power", None)
        if callable(get_power):
            available_power = _safe_float(get_power(), default=0.0)
        else:
            available_power = _safe_float(risk_settings.get("simulated_buying_power"), default=0.0)
            used_power = 0.0
            for item in positions:
                if item.get("side") == "buy":
                    used_power += _safe_float(item.get("entry_price"), default=0.0) * _safe_float(item.get("qty"), default=0.0)
            available_power = max(0.0, available_power - used_power)
        if notional_required > available_power:
            return {
                "ok": False,
                "error": "risk_control_insufficient_buying_power",
                "execution_id": execution_id,
                "ticker": ticker,
                "requested_notional": round(notional_required, 4),
                "available_buying_power": round(available_power, 4),
            }

    return {"ok": True, "positions": positions}


def _normalize_order_tracking_entry(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    return {
        "attempts": 0,
        "status": "not_submitted",
        "broker_order_id": "",
        "last_attempt_at": "",
        "last_status_at": "",
        "last_error": "",
        "retries": 0,
    }


def _ensure_order_tracking(broker_state: Dict[str, Any]) -> Dict[str, Any]:
    raw_tracking = broker_state.get("order_tracking")
    if not isinstance(raw_tracking, dict):
        raw_tracking = {}
        broker_state["order_tracking"] = raw_tracking
    return raw_tracking


def _record_order_attempt(
    tracking: Dict[str, Dict[str, Any]],
    *,
    order_id: str,
    broker_order_id: str = "",
    status: str = "",
    error: str = "",
    submission_source: str = "",
    submission_actor: str = "",
) -> None:
    entry = _normalize_order_tracking_entry(tracking.get(order_id))
    entry["attempts"] = _safe_int(entry.get("attempts"), default=0) + 1
    entry["status"] = status or entry.get("status") or "submitted"
    now = _utc_iso()
    entry["last_attempt_at"] = now
    entry["last_status_at"] = now
    if status:
        entry["status"] = status
    if broker_order_id:
        entry["broker_order_id"] = str(broker_order_id)
    if error:
        entry["last_error"] = str(error)
    if submission_source:
        entry["submission_source"] = _normalize_text(submission_source)
    if submission_actor:
        entry["submission_actor"] = _normalize_text(submission_actor)
    tracking[order_id] = entry


def _mark_order_status(
    tracking: Dict[str, Dict[str, Any]],
    *,
    order_id: str,
    status: str,
    broker_order_id: str = "",
    error: str = "",
) -> None:
    entry = _normalize_order_tracking_entry(tracking.get(order_id))
    entry["status"] = status
    entry["last_status_at"] = _utc_iso()
    if broker_order_id:
        entry["broker_order_id"] = str(broker_order_id)
    if error:
        entry["last_error"] = str(error)
    tracking[order_id] = entry


def _enforce_submission_rate_window(
    actor_key: str,
    *,
    execution_id: str,
    now_iso: str,
    interval_sec: Optional[int] = None,
) -> Dict[str, Any]:
    interval = _resolve_submit_rate_limit_seconds() if interval_sec is None else max(0, int(interval_sec))
    if interval <= 0 or not actor_key:
        return {"ok": True}

    state = _read_submit_rate_state()
    record = state.get(actor_key)
    now_ts = _iso_to_epoch(now_iso)
    if isinstance(record, dict):
        last_ts = _safe_float(record.get("last_submission_ts"), default=0.0)
        if now_ts is not None and last_ts > 0 and (now_ts - last_ts) < interval:
            return {
                "ok": False,
                "error": "submission_rate_limited",
                "actor": actor_key,
                "retry_after_seconds": max(0, int(interval - (now_ts - last_ts))),
                "window_seconds": interval,
            }
    state[actor_key] = {
        "last_submission_at": now_iso,
        "last_submission_ts": now_ts if now_ts is not None else 0.0,
        "execution_id": execution_id,
    }
    _write_submit_rate_state(state)
    return {"ok": True}


def _stale_order_detected(record: Dict[str, Any], stale_order_sec: int) -> bool:
    if stale_order_sec <= 0:
        return False
    return _seconds_since(record.get("last_attempt_at"), datetime.now(timezone.utc)) is not None and _seconds_since(
        record.get("last_attempt_at"),
        datetime.now(timezone.utc),
    ) > stale_order_sec


def _build_broker_client(
    provider: str,
    *,
    execution_root: Path | None = None,
    execution_mode: str = "paper",
) -> Any:
    provider = _normalize_broker_provider(provider, default="paper_sim")
    execution_mode = _normalize_execution_mode(execution_mode, default="paper")
    if provider == "paper_sim":
        if execution_mode == "live":
            raise ValueError("provider_not_live_ready:paper_sim_cannot_use_live")
        return _PaperSimulationBroker(state_root=execution_root)
    if provider == "alpaca":
        if execution_mode == "live" and not _is_sim_live_enabled():
            raise RuntimeError("live_execution_disabled")
        return _AlpacaPaperBroker(
            state_root=execution_root,
            mode=("live" if execution_mode == "live" else "paper"),
        )
    raise ValueError(f"unsupported_broker_provider:{provider}")


def _broker_submit_order_packet(client: Any, order: Dict[str, Any]) -> Dict[str, Any]:
    submit_fn = getattr(client, "submit_order", None)
    if callable(submit_fn):
        return submit_fn(order)
    return {"ok": False, "error": "broker_client_submit_order_not_implemented"}


def _broker_order_status(client: Any, order_id: str) -> Dict[str, Any]:
    status_fn = getattr(client, "get_order_status", None)
    if callable(status_fn):
        return status_fn(order_id)
    return {"ok": False, "error": "broker_client_status_not_implemented", "order_id": str(order_id or "")}


def _build_order_packet(
    *,
    execution_id: str,
    index: int,
    ticker: str = "",
    side: str,
    order_type: str,
    qty: Any,
    price: float | None = None,
    stop_price: float | None = None,
    label: str = "",
    provider: str = "paper_sim",
) -> Dict[str, Any]:
    numeric_qty = _safe_float(qty, 0.0) if provider == "paper_sim" else float(_safe_int(qty, default=0))
    packet = {
        "execution_id": execution_id,
        "provider": provider,
        "order_id": f"{provider}_{execution_id}_{index}",
        "order_type": str(order_type or "").strip().lower(),
        "side": str(side or "").strip().lower(),
        "qty": round(max(0.0, numeric_qty), 6) if provider == "paper_sim" else int(max(0.0, numeric_qty)),
        "label": str(label or "").strip(),
    }
    ticker_value = str(ticker or "").strip().upper()
    if ticker_value:
        packet["ticker"] = ticker_value
        packet["symbol"] = ticker_value
    if price is not None:
        packet["price"] = _safe_float(price, default=0.0)
    if stop_price is not None:
        packet["stop_price"] = _safe_float(stop_price, default=0.0)
    return packet


def _build_broker_order_plan(
    *,
    execution_id: str,
    ticker: str,
    execution: Dict[str, Any],
    risk: Dict[str, Any],
    provider: str,
    simulation: Dict[str, Any],
) -> Dict[str, Any]:
    safe_provider = _safe_order_name(provider, default="paper_sim")
    if risk.get("simulation_stress_mode"):
        safe_provider = "paper_sim"
    entry_low = _safe_float(execution.get("entry"))
    entry_high = _safe_float(simulation.get("entry_high"))
    stop = _safe_float(execution.get("stop"))
    target = _safe_float(execution.get("target"))
    qty = _safe_float(risk.get("proposed_qty"), default=0.0) if risk.get("simulation_stress_mode") else int(_safe_int(risk.get("proposed_qty"), default=0))
    direction_bias = _normalize_direction_bias(
        simulation.get("direction_bias")
        or risk.get("direction_bias")
        or execution.get("direction_bias")
    )
    entry_side = "sell" if direction_bias == "short_bias" else "buy"
    exit_side = "buy" if direction_bias == "short_bias" else "sell"
    if entry_low <= 0 and entry_high > 0:
        entry_low = _safe_float(simulation.get("entry"), default=0.0)
        entry_high = entry_low
    entry_mid = ((entry_low + entry_high) / 2.0) if entry_low > 0 and entry_high > 0 else _safe_float(execution.get("entry"))

    return {
        "provider": safe_provider,
        "ticker": str(ticker or "").strip().upper(),
        "symbol": str(ticker or "").strip().upper(),
        "side": entry_side,
        "direction_bias": direction_bias,
        "currency": "USD",
        "orders": [
            _build_order_packet(
                execution_id=execution_id,
                ticker=ticker,
                index=1,
                side=entry_side,
                order_type="limit",
                qty=qty,
                price=entry_mid,
                label="entry_limit",
                provider=safe_provider,
            ),
            _build_order_packet(
                execution_id=execution_id,
                ticker=ticker,
                index=2,
                side=exit_side,
                order_type="stop_market",
                qty=qty,
                stop_price=stop,
                label="protective_stop",
                provider=safe_provider,
            ),
            _build_order_packet(
                execution_id=execution_id,
                ticker=ticker,
                index=3,
                side=exit_side,
                order_type="limit",
                qty=qty,
                price=target,
                label="profit_taker",
                provider=safe_provider,
            ),
        ],
    }


def _apply_order_plan(broker_state: Dict[str, Any], order_plan: Dict[str, Any]) -> list[str]:
    order_ids = [str(item.get("order_id") or "") for item in list((order_plan or {}).get("orders") or [])]
    order_ids = [item for item in order_ids if item]
    broker_state["order_plan"] = order_plan
    broker_state["order_ids"] = order_ids
    broker_state["order_tracking"] = broker_state.get("order_tracking", {})
    tracking = _ensure_order_tracking(broker_state)
    for order_id in order_ids:
        if order_id not in tracking:
            tracking[order_id] = _normalize_order_tracking_entry(None)
    return order_ids


def _append_submission_history(plan: Dict[str, Any], entry: Dict[str, Any]) -> None:
    history = plan.get("submission_history")
    if not isinstance(history, list):
        history = []
    elif len(history) > 100:
        history = history[-100:]
    history.append(dict(entry))
    plan["submission_history"] = history


def _decide_order_submit_action(
    order: Dict[str, Any],
    *,
    client: Any,
    tracking: Dict[str, Dict[str, Any]],
    retry_settings: Dict[str, int],
) -> str:
    order_id = str(order.get("order_id") or "").strip()
    if not order_id:
        return "skip"
    record = _normalize_order_tracking_entry(tracking.get(order_id))
    attempts = _safe_int(record.get("attempts"), default=0)
    max_retries = retry_settings.get("max_retries", 0)
    retry_window_sec = retry_settings.get("retry_window_sec", 0)
    stale_order_sec = retry_settings.get("stale_order_sec", 0)

    if attempts >= max_retries:
        return "max_retries_exceeded"

    broker_order_id = str(record.get("broker_order_id") or "").strip()
    if not broker_order_id:
        if attempts >= max_retries:
            return "max_retries_exceeded"
        return "submit"

    # Reconcile an existing order id with the broker before deciding to resubmit.
    status_payload = _broker_order_status(client, order_id)
    if status_payload.get("ok"):
        status = str(status_payload.get("status") or "").strip().lower()
        payload_order_id = str(status_payload.get("broker_order_id") or status_payload.get("order_id") or "")
        if payload_order_id:
            broker_order_id = payload_order_id
        _mark_order_status(
            tracking,
            order_id=order_id,
            status=status,
            broker_order_id=broker_order_id,
            error="" if str(status_payload.get("error", "")) == "" else str(status_payload.get("error", "")),
        )
        if _to_status_is_filled(status):
            return "skip"
        if _is_active_order_status(status):
            if _within_retry_window(record.get("last_attempt_at"), retry_window_sec):
                return "skip"
            if _stale_order_detected(record, stale_order_sec) and attempts < max_retries:
                return "submit"
            return "skip"
        if _is_terminal_order_status(status):
            if attempts >= max_retries:
                return "terminal_no_retry"
            if attempts < max_retries and not _within_retry_window(record.get("last_attempt_at"), retry_window_sec):
                return "submit"
            return "skip"
        return "skip"

    # Broker status check failed; if stale and still retry budget remains, attempt to resubmit.
    if _stale_order_detected(record, stale_order_sec) and attempts < max_retries:
        record["broker_order_id"] = ""
        _mark_order_status(
            tracking,
            order_id=order_id,
            status="stale",
            broker_order_id="",
            error=str(status_payload.get("error") or "broker_order_status_unavailable"),
        )
        return "submit"
    return "skip_status_unknown"


def _derive_fill_status_from_order_statuses(statuses: Sequence[Dict[str, Any]]) -> str:
    fills = [str(item.get("status") or "").strip().lower() for item in list(statuses) if isinstance(item, dict)]
    if not fills:
        return "not_submitted"
    if any(value in {"rejected", "cancelled", "canceled", "expired", "error", "failed"} for value in fills):
        return "rejected"
    if all(value in {"filled", "partial_filled", "partially_filled"} for value in fills):
        return "filled"
    if any(value in {"filled", "partial_filled", "partially_filled"} for value in fills):
        return "partially_filled"
    return "submitted"


def _execution_realism_profile(
    *,
    entry_price: float,
    qty: int,
    avg_volume: float = 0.0,
    notional: float = 0.0,
) -> Dict[str, Any]:
    entry = _safe_float(entry_price, default=0.0)
    shares = max(0, int(qty or 0))
    adv = _safe_float(avg_volume, default=0.0)
    gross_notional = _safe_float(notional, default=0.0) or (entry * shares)
    if adv >= 5_000_000:
        slippage_bps = 5
    elif adv >= 1_000_000:
        slippage_bps = 12
    elif adv >= 500_000:
        slippage_bps = 25
    elif adv > 0:
        slippage_bps = 50
    else:
        slippage_bps = 35
    participation_pct = round((shares / adv) * 100.0, 4) if adv > 0 else None
    warnings: list[str] = []
    partial_fill_risk = "unknown"
    if adv <= 0:
        warnings.append("avg_volume_unavailable")
    elif shares > adv * 0.002:
        warnings.append("partial_fill_risk_high")
        partial_fill_risk = "high"
    elif shares > adv * 0.0005:
        warnings.append("partial_fill_risk_medium")
        partial_fill_risk = "medium"
    else:
        partial_fill_risk = "low"
    if gross_notional >= 25_000:
        warnings.append("notional_exposure_large_for_paper_rehearsal")
    if participation_pct is not None and participation_pct > 0.2:
        warnings.append("notional_vs_adv_warning")
    score = 100
    if partial_fill_risk == "medium":
        score -= 12
    elif partial_fill_risk == "high":
        score -= 28
    if "avg_volume_unavailable" in warnings:
        score -= 15
    if "notional_exposure_large_for_paper_rehearsal" in warnings:
        score -= 8
    if "notional_vs_adv_warning" in warnings:
        score -= 10
    return {
        "execution_realism_score": max(0, min(100, score)),
        "estimated_slippage_bps": slippage_bps,
        "estimated_slippage_dollars": round(gross_notional * (slippage_bps / 10000.0), 2),
        "avg_volume": int(adv) if adv else 0,
        "qty": shares,
        "notional": round(gross_notional, 2),
        "adv_participation_pct": participation_pct,
        "partial_fill_risk": partial_fill_risk,
        "warnings": warnings,
    }


def _has_active_tracking(tracking: Dict[str, Any]) -> bool:
    for _, record in tracking.items():
        if not isinstance(record, dict):
            continue
        if not _is_terminal_order_status(str(record.get("status") or "")):
            return True
    return False


def _reconcile_single_plan(
    *,
    plan: Dict[str, Any],
    execution_root: Optional[Path] = None,
    max_retries_override: Optional[int] = None,
    submission_source: str = "api",
) -> Dict[str, Any]:
    execution_id = str(plan.get("execution_id") or "").strip()
    if not execution_id:
        return {"ok": False, "error": "missing_execution_id"}
    broker_state = _ensure_plan_dict(plan.get("broker")) or {}
    if not broker_state.get("order_submitted"):
        return {"ok": False, "error": "not_submitted", "execution_id": execution_id}

    provider = _normalize_broker_provider(broker_state.get("provider"), default="paper_sim")
    mode_hint = str(broker_state.get("mode") or "").lower()
    execution_mode = "live" if "live" in mode_hint else "paper"
    order_ids = [str(item) for item in list(broker_state.get("order_ids") or []) if str(item)]
    if not order_ids:
        return {"ok": False, "error": "no_orders_to_reconcile", "execution_id": execution_id}

    order_plan = _ensure_plan_dict(broker_state.get("order_plan")) or {}
    orders = list(order_plan.get("orders") or [])
    if not orders:
        return {"ok": False, "error": "no_order_plan", "execution_id": execution_id}

    tracking = _ensure_order_tracking(broker_state)
    if not tracking:
        for item in orders:
            order_id = str(item.get("order_id") or "").strip()
            if order_id:
                tracking[order_id] = _normalize_order_tracking_entry(None)

    retry_settings = _resolve_broker_retry_settings()
    if isinstance(max_retries_override, int) and max_retries_override >= 0:
        retry_settings["max_retries"] = max_retries_override
    reconcile_submission_source = _normalize_text(submission_source) or "api"

    try:
        client = _build_broker_client(
            provider,
            execution_root=execution_root or SIMULATION_EXECUTION_ROOT,
            execution_mode=execution_mode,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": "broker_client_init_failed",
            "execution_id": execution_id,
            "provider": provider,
            "reason": str(exc),
        }

    checked = 0
    reconciled = 0
    skipped = 0
    errors = 0
    decisions: Dict[str, str] = {}
    statuses: List[Dict[str, Any]] = []
    submit_results: List[Dict[str, Any]] = []

    if not _has_active_tracking(tracking):
        for order_id in order_ids:
            payload = _broker_order_status(client, order_id)
            statuses.append(payload)
            status = str(payload.get("status") or "unknown").strip().lower()
            if payload.get("ok"):
                _mark_order_status(
                    tracking,
                    order_id=order_id,
                    status=status,
                    broker_order_id=str(payload.get("broker_order_id") or payload.get("order_id") or ""),
                    error=str(payload.get("error") or ""),
                )
            else:
                _mark_order_status(
                    tracking,
                    order_id=order_id,
                    status="status_unknown",
                    broker_order_id="",
                    error=str(payload.get("error") or payload.get("details") or "status_lookup_failed"),
                )
            _ensure_order_tracking(broker_state)[order_id] = tracking.get(order_id, _normalize_order_tracking_entry(None))
        broker_state["order_tracking"] = tracking
        broker_state["order_statuses"] = statuses
        broker_state["order_decisions"] = {}
        if statuses:
            broker_state["last_reconciled_at"] = _utc_iso()
        broker_state["fill_status"] = _derive_fill_status_from_order_statuses(statuses)
        plan["broker"] = broker_state
        _save_plan(plan)
        return {
            "ok": True,
            "execution_id": execution_id,
            "checked": 0,
            "reconciled": 0,
            "skipped": len(order_ids),
            "errors": 0,
            "decisions": {},
            "submit_results": [],
            "broker_status": statuses,
        }

    for order in orders:
        if not isinstance(order, dict):
            continue
        order_id = str(order.get("order_id") or "").strip()
        if not order_id:
            continue
        checked += 1

        decision = _decide_order_submit_action(
            order,
            client=client,
            tracking=tracking,
            retry_settings=retry_settings,
        )
        decisions[order_id] = decision
        if decision == "submit":
            submission = _broker_submit_order_packet(client, order)
            submission = dict(submission) if isinstance(submission, dict) else {
                "ok": False,
                "error": str(submission),
                "order_id": order_id,
            }
            submission["submission_source"] = reconcile_submission_source
            submission["submission_actor"] = "reconcile"
            submit_results.append(submission)
            if not submission.get("ok"):
                errors += 1
                _mark_order_status(
                    tracking,
                    order_id=order_id,
                    status="failed",
                    error=str(submission.get("error") or submission.get("message") or "submit_failed"),
                )
            else:
                reconciled += 1
                _record_order_attempt(
                    tracking,
                    order_id=order_id,
                    broker_order_id=str(submission.get("broker_order_id") or submission.get("order_id") or ""),
                    status=str(submission.get("status") or "submitted"),
                    error=str(submission.get("error") or ""),
                    submission_source=reconcile_submission_source,
                    submission_actor="reconcile",
                )
        else:
            skipped += 1

    for order_id in order_ids:
        payload = _broker_order_status(client, order_id)
        statuses.append(payload)
        status = str(payload.get("status") or "unknown").strip().lower()
        if payload.get("ok"):
            _mark_order_status(
                tracking,
                order_id=order_id,
                status=status,
                broker_order_id=str(payload.get("broker_order_id") or payload.get("order_id") or ""),
                error=str(payload.get("error") or ""),
            )
        else:
            _mark_order_status(
                tracking,
                order_id=order_id,
                status="status_unknown",
                error=str(payload.get("error") or payload.get("details") or "status_lookup_failed"),
            )
        _ensure_order_tracking(broker_state)[order_id] = tracking.get(order_id, _normalize_order_tracking_entry(None))

    broker_state["order_tracking"] = tracking
    broker_state["order_statuses"] = statuses
    broker_state["order_decisions"] = decisions
    if reconciled or statuses:
        broker_state["last_reconciled_at"] = _utc_iso()
    broker_state["fill_status"] = _derive_fill_status_from_order_statuses(statuses)
    if submit_results:
        broker_state["order_reconciliations"] = submit_results

    plan["broker"] = broker_state
    _save_plan(plan)

    return {
        "ok": True,
        "execution_id": execution_id,
        "checked": checked,
        "reconciled": reconciled,
        "skipped": skipped,
        "errors": errors,
        "decisions": decisions,
        "submit_results": submit_results,
        "broker_status": statuses,
    }


def _simulation_to_plan(sim: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_simulation(sim)
    if not normalized:
        raise ValueError("invalid_simulation")

    risk_settings = _resolve_risk_settings()
    parsed_entry = _parse_zone(normalized["entry_zone"])
    entry_low = _safe_float(normalized["entry_low"]) or parsed_entry[0]
    entry_high = _safe_float(normalized["entry_high"]) or parsed_entry[1]
    stop = _safe_float(normalized["stop"])
    target = _safe_float(normalized["target"])
    entry = (entry_low + entry_high) / 2.0 if entry_low > 0 and entry_high > 0 else 0.0
    if entry <= 0:
        entry_low, entry_high = _parse_zone(normalized["entry_zone"])
        entry = (entry_low + entry_high) / 2.0 if entry_low > 0 and entry_high > 0 else 0.0
    if stop <= 0:
        parsed_stop = _parse_zone(normalized["stop_zone"])
        stop = parsed_stop[0] if parsed_stop else _parse_price(normalized["stop_zone"])
    if target <= 0:
        parsed_target = _parse_zone(normalized["target_zone"])
        if parsed_target:
            target = parsed_target[0] if normalized["direction_bias"] == "short_bias" else parsed_target[1]
        else:
            target = _parse_price(normalized["target_zone"])

    if normalized["direction_bias"] == "short_bias":
        risk_per_share = stop - entry
        geometry_ok = bool(entry > 0 and stop > entry and 0 < target < entry)
    else:
        risk_per_share = entry - stop
        geometry_ok = bool(entry > 0 and 0 < stop < entry and target > entry)
    stress_requested = bool(
        normalized.get("simulation_stress_mode")
        or normalized.get("stress_probe_ready")
        or normalized.get("stress_sandbox")
        or normalized.get("candidate_type") == "stress_sandbox"
    )
    stress_cfg = stress_mode.resolve_stress_mode(risk_settings.get("paper_portfolio_size"))
    stress_enabled = bool(stress_requested and stress_cfg.get("enabled"))
    stress_sizing = (
        stress_mode.stress_position_sizing(
            entry_price=entry,
            stop=stop,
            direction_bias=normalized["direction_bias"],
            portfolio_size=risk_settings.get("paper_portfolio_size"),
            config=stress_cfg,
        )
        if stress_enabled
        else None
    )
    risk_budget = max(0.0, _safe_float((stress_sizing or {}).get("dollar_risk_target"), risk_settings["risk_per_trade_dollars"]))
    raw_qty = (
        _safe_float((stress_sizing or {}).get("fractional_shares"), 0.0)
        if stress_sizing and stress_cfg.get("allow_fractional")
        else (int(risk_budget / risk_per_share) if risk_per_share > 0 else 0)
    )
    max_qty = int(risk_settings["max_position_qty"])
    min_qty = max(1, int(risk_settings["min_position_qty"]))
    proposed_qty = raw_qty
    if max_qty > 0 and not stress_sizing:
        proposed_qty = min(proposed_qty, max_qty)
    if proposed_qty < min_qty and not stress_sizing:
        proposed_qty = 0
    liquidity_payload = normalized.get("liquidity") if isinstance(normalized.get("liquidity"), dict) else {}
    avg_volume = _safe_float(
        normalized.get("avg_volume_30d")
        or normalized.get("avg_volume")
        or normalized.get("average_volume")
        or liquidity_payload.get("avg_volume_30d")
        or 0.0,
        default=0.0,
    )
    realism = _execution_realism_profile(
        entry_price=entry,
        qty=proposed_qty,
        avg_volume=avg_volume,
        notional=entry * proposed_qty,
    )

    return {
        **normalized,
        "entry": round(entry, 4),
        "entry_low": round(entry_low, 4),
        "entry_high": round(entry_high, 4),
        "stop": round(stop, 4) if stop > 0 else 0.0,
        "target": round(target, 4) if target > 0 else 0.0,
        "risk": {
            "risk_budget_dollars": risk_budget,
            "risk_per_share": round(risk_per_share, 4),
            "proposed_qty": proposed_qty,
            "max_qty": max_qty,
            "min_qty": min_qty,
            "direction_bias": normalized["direction_bias"],
            "paper_portfolio_size": risk_settings.get("paper_portfolio_size"),
            "simulation_stress_mode": stress_enabled,
            "stress_position_sizing": stress_sizing or {},
            "paper_only": True,
            "live_trade_execution": False,
            "production_readiness_credit": False,
        },
        "execution_realism": realism,
        "ready_for_execution": bool(geometry_ok and risk_per_share > 0 and proposed_qty > 0),
    }


def _queue() -> List[str]:
    queue = _read_json(SIMULATION_EXECUTION_QUEUE, default=[]) or []
    return [str(item) for item in list(queue) if str(item).strip()]


def _write_queue(items: Sequence[str]) -> None:
    _write_json(SIMULATION_EXECUTION_QUEUE, list(items))


def _plan_path(plan_id: str) -> Path:
    return SIMULATION_EXECUTION_PLANS / f"{plan_id}.json"


def create_simulation_execution_plan(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    simulation = payload.get("simulation")
    sim_id = str(payload.get("sim_id") or "").strip()
    notes = str(payload.get("notes") or "").strip()

    if not isinstance(simulation, dict):
        simulation = None
    if simulation is None and sim_id:
        simulation = swing_simulation.load_simulation(sim_id)
    if simulation is None:
        simulation = swing_simulation.get_latest_simulation()
    if not isinstance(simulation, dict):
        return {"ok": False, "error": "no_simulation_found"}
    try:
        plan_simulation = _simulation_to_plan(simulation)
    except Exception as exc:
        return {"ok": False, "error": f"invalid_simulation:{type(exc).__name__}:{exc}"}

    execution_id = f"exec_{uuid.uuid4().hex}"
    now = _utc_iso()
    plan: Dict[str, Any] = {
        "execution_id": execution_id,
        "created_at": now,
        "updated_at": now,
        "status": "draft",
        "execution_mode": "simulation",
        "execution_type": "paper_only",
        "simulation_id": plan_simulation.get("sim_id") or plan_simulation.get("simulation_id") or "",
        "run_id": plan_simulation.get("run_id") or "",
        "notes": notes,
        "ticker": plan_simulation["ticker"],
        "simulation": {
            "sim_id": plan_simulation.get("sim_id") or "",
            "simulation_id": plan_simulation.get("simulation_id") or "",
            "run_id": plan_simulation.get("run_id") or "",
            "ticker": plan_simulation["ticker"],
            "entry_zone": plan_simulation["entry_zone"],
            "stop_zone": plan_simulation["stop_zone"],
            "target_zone": plan_simulation["target_zone"],
            "direction_bias": plan_simulation["direction_bias"],
            "status": plan_simulation["status"],
            "setup_type": plan_simulation["setup_type"],
            "entry_low": plan_simulation["entry_low"],
            "entry_high": plan_simulation["entry_high"],
            "score": plan_simulation.get("score"),
            "confidence_label": plan_simulation.get("confidence_label"),
            "created_at": plan_simulation.get("created_at"),
        },
        "risk": plan_simulation["risk"],
        "execution_realism": plan_simulation.get("execution_realism") or {},
        "execution": {
            "entry": plan_simulation["entry"],
            "stop": plan_simulation["stop"],
            "target": plan_simulation["target"],
            "direction_bias": plan_simulation["direction_bias"],
        },
        "approval": {
            "required": True,
            "approved": False,
            "approved_at": "",
            "approved_by": "",
            "approval_notes": "",
            "approved_from": "",
            "approved_client": "",
            "approval_source": "",
        },
        "broker": {
            "provider": "none",
            "mode": "simulation_only",
            "order_submitted": False,
            "order_ids": [],
            "fill_status": "not_submitted",
        },
        "readiness": {
            "source": "simulation_latest" if not sim_id and "simulation" not in payload else ("simulation_inline" if "simulation" in payload else "simulation_by_id"),
            "ready_for_execution": plan_simulation["ready_for_execution"],
        },
    }

    _write_json(_plan_path(execution_id), plan)
    queue = _queue()
    if execution_id not in queue:
        queue.insert(0, execution_id)
    _write_queue(queue[:200])
    return {"ok": True, "execution_plan": plan}


def get_simulation_execution_plan(execution_id: str) -> Optional[Dict[str, Any]]:
    if not execution_id:
        return None
    row = _read_json(_plan_path(execution_id), default=None)
    if isinstance(row, dict):
        return row
    return None


def list_simulation_execution_plans(limit: int = 50) -> Dict[str, Any]:
    limit = max(1, min(_safe_int(limit, default=50), 500))
    queue = _queue()
    plans = []
    for execution_id in queue[:limit]:
        row = get_simulation_execution_plan(execution_id)
        if isinstance(row, dict):
            plans.append(row)
    if not plans:
        fallback = []
        for path in sorted(SIMULATION_EXECUTION_PLANS.glob("*.json"), reverse=True):
            row = _read_json(path, default=None)
            if isinstance(row, dict):
                fallback.append(row)
            if len(fallback) >= limit:
                break
        plans = fallback
    return {
        "ok": True,
        "count": len(plans),
        "limit": limit,
        "plans": plans,
    }


def reconcile_simulation_execution_orders(
    *,
    max_plans: Optional[int] = None,
    requested_by: str = "",
    requested_from: str = "",
    requested_client: str = "",
    source: str = "api",
) -> Dict[str, Any]:
    lock = _acquire_reconcile_lock()
    if not lock.get("ok"):
        return {"ok": False, "error": lock.get("error", "reconcile_lock_in_use"), "lock": lock.get("lock", {})}

    resolved_max = _resolve_reconcile_max_plans() if max_plans is None else max(0, max_plans)
    resolved_max = resolved_max or 0

    try:
        queue = _queue()
        if not queue:
            _release_reconcile_lock()
            return {
                "ok": True,
                "summary": {
                    "plans_considered": 0,
                    "checked": 0,
                    "reconciled": 0,
                    "skipped": 0,
                    "errors": 0,
                },
                "results": [],
                "lock": {
                    "ok": True,
                    "holder": lock.get("lock", {}),
                    "stale_lock_cleared": lock.get("stale_lock_cleared", False),
                },
                "requested_by": requested_by,
                "requested_from": requested_from,
                "requested_client": requested_client,
                "source": source,
            }

        plans_to_check = queue if resolved_max <= 0 else queue[:resolved_max]
        summary = {
            "plans_considered": 0,
            "checked": 0,
            "reconciled": 0,
            "skipped": 0,
            "errors": 0,
        }
        per_plan: List[Dict[str, Any]] = []

        for execution_id in plans_to_check:
            execution_plan = _ensure_plan_dict(get_simulation_execution_plan(execution_id))
            if execution_plan is None:
                summary["errors"] += 1
                per_plan.append({
                    "ok": False,
                    "execution_id": execution_id,
                    "error": "plan_not_found",
                })
                continue

            summary["plans_considered"] += 1
            result = _reconcile_single_plan(
                plan=execution_plan,
                execution_root=SIMULATION_EXECUTION_ROOT,
                submission_source=source,
            )
            if result.get("error") == "not_submitted":
                summary["skipped"] += 1
            elif result.get("error") == "no_order_plan":
                summary["skipped"] += 1
            elif result.get("error") == "no_orders_to_reconcile":
                summary["skipped"] += 1
            elif not result.get("ok"):
                summary["errors"] += 1
            else:
                summary["checked"] += int(result.get("checked", 0))
                summary["reconciled"] += int(result.get("reconciled", 0))
                summary["skipped"] += int(result.get("skipped", 0))
                summary["errors"] += int(result.get("errors", 0))
                if bool(result.get("reconciled")):
                    result["plan_reconciled"] = True
            per_plan.append(result)

        return {
            "ok": True,
            "summary": summary,
            "results": per_plan,
            "requested_by": requested_by,
            "requested_from": requested_from,
            "requested_client": requested_client,
            "source": source,
            "lock": {
                "ok": True,
                "holder": lock.get("lock", {}),
                "stale_lock_cleared": lock.get("stale_lock_cleared", False),
            },
        }
    finally:
        _release_reconcile_lock()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def approve_simulation_execution_plan(
    execution_id: str,
    approved_by: str = "",
    approval_notes: str = "",
    approved_from: str = "",
    approved_client: str = "",
    approval_source: str = "",
) -> Dict[str, Any]:
    if not execution_id:
        return {"ok": False, "error": "missing_execution_id"}

    plan = _ensure_plan_dict(get_simulation_execution_plan(execution_id))
    if plan is None:
        return {"ok": False, "error": "plan_not_found"}

    approval = _ensure_plan_dict(plan.get("approval")) or {}
    if not approval.get("required", True):
        return {
            "ok": True,
            "execution_plan": plan,
            "message": "approval_not_required",
            "already_approved": approval.get("approved", False),
        }

    now = _utc_iso()
    approval["approved"] = True
    approval["approved_at"] = now
    approval["approved_by"] = _normalize_text(approved_by)
    approval["approval_notes"] = _normalize_text(approval_notes)
    approval["approved_from"] = _normalize_text(approved_from)
    approval["approved_client"] = _normalize_text(approved_client)
    approval["approval_source"] = _normalize_text(approval_source) or "api"
    plan["approval"] = approval

    if str(plan.get("status") or "").lower() in {"draft", "not_approved"}:
        _set_status(plan, "approved")
    plan["updated_at"] = now
    _save_plan(plan)
    return {"ok": True, "execution_plan": plan}


def submit_simulation_execution_plan(
    execution_id: str,
    force_without_approval: bool = False,
    dry_run: bool | None = None,
    executor: str | None = None,
    submitted_by: str = "",
    submitted_from: str = "",
    submitted_client: str = "",
    submission_source: str | None = None,
) -> Dict[str, Any]:
    if not execution_id:
        return {"ok": False, "error": "missing_execution_id"}
    if dry_run is None:
        dry_run = _to_bool(os.getenv("APOLLO_SIM_EXECUTION_DRY_RUN"), default=False)

    plan = _ensure_plan_dict(get_simulation_execution_plan(execution_id))
    if plan is None:
        return {"ok": False, "error": "plan_not_found"}

    if force_without_approval and not _resolve_force_without_approval_enabled():
        return {"ok": False, "error": "force_without_approval_disabled"}

    broker_state = _ensure_plan_dict(plan.get("broker")) or {}
    approval = _ensure_plan_dict(plan.get("approval")) or {}
    risk = _ensure_plan_dict(plan.get("risk")) or {}
    stress_plan = bool(risk.get("simulation_stress_mode"))

    if not force_without_approval and approval.get("required", True) and not approval.get("approved"):
        return {"ok": False, "error": "approval_required"}

    actor_key = _normalize_actor_key(user=submitted_by, ip=submitted_from, client=submitted_client)
    now_iso = _utc_iso()
    resolved_submission_source = _normalize_text(submission_source) or "api"
    rate_check = _enforce_submission_rate_window(actor_key, execution_id=execution_id, now_iso=now_iso)
    if not rate_check.get("ok"):
        return {"ok": False, "error": "submission_rate_limited", "details": rate_check}

    readiness = _ensure_plan_dict(plan.get("readiness")) or {}
    if not readiness.get("ready_for_execution", False):
        return {"ok": False, "error": "execution_not_ready", "reason": "invalid_or_incomplete_plan"}

    provider = _normalize_broker_provider(
        _normalize_text(executor) if executor is not None else None,
        default=_resolve_broker_provider(),
    )
    if stress_plan:
        provider = "paper_sim"
    execution_mode = _resolve_sim_execution_mode()
    if stress_plan and execution_mode == "live":
        execution_mode = "paper"
    retry_settings = _resolve_broker_retry_settings()

    if dry_run:
        if execution_mode == "live":
            mode = "alpaca_live_dry_run" if provider == "alpaca" else "paper_simulation_dry_run"
        else:
            mode = "paper_simulation_dry_run"
    else:
        mode = "paper_simulation" if execution_mode == "paper" else f"{provider}_live_simulation"

    if not broker_state.get("order_ids"):
        order_plan = _build_broker_order_plan(
            execution_id=execution_id,
            ticker=plan["ticker"],
            execution=_ensure_plan_dict(plan.get("execution")) or {},
            risk=_ensure_plan_dict(plan.get("risk")) or {},
            provider=provider,
            simulation=plan.get("simulation", {}),
        )
        order_ids = _apply_order_plan(broker_state, order_plan)
    else:
        order_ids = [str(item) for item in list(broker_state.get("order_ids") or []) if str(item)]
        if not order_ids:
            order_plan = _build_broker_order_plan(
                execution_id=execution_id,
                ticker=plan["ticker"],
                execution=_ensure_plan_dict(plan.get("execution")) or {},
                risk=_ensure_plan_dict(plan.get("risk")) or {},
                provider=provider,
                simulation=plan.get("simulation", {}),
            )
            order_ids = _apply_order_plan(broker_state, order_plan)

    status_message: str | None = None
    decisions: Dict[str, str] = {}
    submission_results: List[Dict[str, Any]] = []

    if not dry_run:
        if provider == "paper_sim" and _to_bool(os.getenv("APOLLO_PRETRADE_GATE_ON_SUBMIT"), default=True):
            try:
                from Apollo import pretrade_gate

                gate_result = pretrade_gate.run_pre_submit_gate(plan, force=True)
            except Exception as exc:
                gate_result = {
                    "ok": False,
                    "decision": "needs_human_review",
                    "blocker": f"pretrade_gate_failed:{type(exc).__name__}:{exc}",
                    "next_action": "Do not rely on the paper submit until the pre-trade gate can run.",
                }
            broker_state["pretrade_gate"] = {
                "run_id": gate_result.get("run_id"),
                "decision": gate_result.get("decision"),
                "blocker": gate_result.get("blocker"),
                "next_action": gate_result.get("next_action"),
                "json_path": gate_result.get("json_path"),
                "checked_at": gate_result.get("checked_at"),
                "checked_at_ct": gate_result.get("checked_at_ct"),
            }
            if _to_bool(os.getenv("APOLLO_PRETRADE_GATE_ENFORCE"), default=True):
                decision = str(gate_result.get("decision") or "")
                if decision not in {"clear_for_paper_sim", "size_down"}:
                    plan["broker"] = broker_state
                    plan["updated_at"] = _utc_iso()
                    _save_plan(plan)
                    return {
                        "ok": False,
                        "error": "pretrade_gate_blocked",
                        "execution_id": execution_id,
                        "decision": decision,
                        "reason": gate_result.get("blocker") or "pretrade gate did not clear paper simulation",
                        "pretrade_gate": broker_state["pretrade_gate"],
                    }

        risk_settings = _resolve_risk_settings()
        try:
            client = _build_broker_client(
                provider,
                execution_root=SIMULATION_EXECUTION_ROOT,
                execution_mode=execution_mode,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": "broker_client_init_failed",
                "provider": provider,
                "reason": str(exc),
            }
        risk_check = _check_risk_controls_for_plan(
            broker_client=client,
            execution_id=execution_id,
            order_plan=broker_state.get("order_plan") or {},
            risk_settings=risk_settings,
        )
        if not risk_check.get("ok"):
            return {
                "ok": False,
                "error": risk_check.get("error"),
                "execution_id": execution_id,
                "reason": risk_check.get("reason") or risk_check.get("error"),
                "details": {k: v for k, v in risk_check.items() if k not in {"ok"}},
            }

        tracking = _ensure_order_tracking(broker_state)
        submission_started = False

        for order in list((broker_state.get("order_plan") or {}).get("orders") or []):
            if not isinstance(order, dict):
                continue
            order_id = str(order.get("order_id") or "").strip()
            if not order_id:
                continue
            decision = _decide_order_submit_action(
                order,
                client=client,
                tracking=tracking,
                retry_settings=retry_settings,
            )
            decisions[order_id] = decision

            if decision != "submit":
                if decision == "max_retries_exceeded":
                    return {
                        "ok": False,
                        "error": "max_retries_exceeded",
                        "provider": provider,
                        "order_id": order_id,
                        "execution_mode": execution_mode,
                        "details": {
                            "decision": decision,
                            "retry_settings": retry_settings,
                        },
                    }
                if decision == "terminal_no_retry":
                    # Terminal state reached without retry policy authorization.
                    # Treat as a non-fatal completion state for that order.
                    _mark_order_status(
                        tracking,
                        order_id=order_id,
                        status="terminal_no_retry",
                        error="terminal_status_reached_no_retry",
                    )
                    continue

                if decision == "skip_status_unknown":
                    # If broker status polling was unavailable, continue with safe skip.
                    _mark_order_status(
                        tracking,
                        order_id=order_id,
                        status="skip_status_unknown",
                        error="broker_status_unavailable",
                    )
                    continue
                if decision == "skip":
                    continue

                _mark_order_status(
                    tracking,
                    order_id=order_id,
                    status=decision if decision else "skip",
                    error="retry_window_active_or_already_submitted",
                )
                continue

            result = _broker_submit_order_packet(client, order)
            submission_result = dict(result) if isinstance(result, dict) else {"ok": False, "error": "submission_response_not_dict"}
            submission_result["submission_source"] = resolved_submission_source
            submission_result["submission_actor"] = actor_key
            submission_results.append(submission_result)
            submission_started = True
            if not submission_result.get("ok"):
                _mark_order_status(
                    tracking,
                    order_id=order_id,
                    status="failed",
                    error=str(submission_result.get("error") or submission_result.get("message") or "submit_failed"),
                )
                return {
                    "ok": False,
                    "error": "broker_order_submit_failed",
                    "provider": provider,
                    "order_id": order_id,
                    "reason": str(submission_result.get("error") or submission_result.get("message") or "unknown"),
                }
            _record_order_attempt(
                tracking,
                order_id=order_id,
                broker_order_id=str(submission_result.get("broker_order_id") or submission_result.get("order_id") or ""),
                status=str(submission_result.get("status") or "submitted"),
                error=str(submission_result.get("error") or ""),
                submission_source=resolved_submission_source,
                submission_actor=actor_key,
            )

        broker_state["order_submissions"] = submission_results
        broker_state["order_decisions"] = decisions

        if not submission_started:
            if broker_state.get("order_submitted"):
                if decisions:
                    status_message = "already_submitted"
                broker_state["fill_status"] = "submitted"
            elif any(item in {"skip_status_unknown", "skip"} for item in decisions.values()):
                broker_state["fill_status"] = "submission_deferred"
            else:
                broker_state["fill_status"] = "not_submitted"
        else:
            broker_state["fill_status"] = "submitted"

        broker_state["order_submitted"] = bool(
            not dry_run and (
                broker_state.get("order_submitted") or bool(submission_results and isinstance(submission_results, list))
            )
        )
    else:
        broker_state["order_submitted"] = False
        broker_state["fill_status"] = "dry_run_not_submitted"
    broker_state["mode"] = mode
    broker_state["provider"] = provider
    broker_state["executor"] = _normalize_text(executor) or _normalize_broker_provider(broker_state.get("provider"), default="paper_sim")
    if broker_state.get("order_plan") and not dry_run:
        broker_state["provider"] = _safe_order_name(provider, default="paper_sim")

    plan["broker"] = broker_state
    submission_entry = {
        "requested_at": now_iso,
        "dry_run": bool(dry_run),
        "executor": broker_state["executor"],
        "requested_by": _normalize_text(submitted_by),
        "requested_from": _normalize_text(submitted_from),
        "requested_client": _normalize_text(submitted_client),
        "force_without_approval": bool(force_without_approval),
        "force_without_approval_allowed": _resolve_force_without_approval_enabled(),
        "source": resolved_submission_source,
        "submission_actor": actor_key,
        "order_count": len(order_ids),
        "decisions": decisions,
        "status": (
            "already_submitted"
            if status_message == "already_submitted"
            else ("deferred" if not dry_run and not submission_started else "dry_run" if dry_run else "submitted")
        ),
    }
    _append_submission_history(plan, submission_entry)

    _set_status(plan, "submitted")
    plan["updated_at"] = _utc_iso()
    plan["submission"] = submission_entry
    plan["submission"]["mode"] = mode
    _save_plan(plan)
    response: Dict[str, Any] = {"ok": True, "execution_plan": plan}
    if status_message:
        response["message"] = status_message
    if decisions:
        response["decisions"] = decisions
    if status_message == "already_submitted":
        response["message"] = status_message
    return response


def sync_simulation_execution_plan_with_broker(execution_id: str) -> Dict[str, Any]:
    if not execution_id:
        return {"ok": False, "error": "missing_execution_id"}

    plan = _ensure_plan_dict(get_simulation_execution_plan(execution_id))
    if plan is None:
        return {"ok": False, "error": "plan_not_found"}

    broker_state = _ensure_plan_dict(plan.get("broker")) or {}
    if not broker_state.get("order_submitted"):
        return {"ok": False, "error": "plan_not_submitted", "execution_plan": plan}

    provider = _normalize_broker_provider(broker_state.get("provider"), default="paper_sim")
    mode_hint = str(broker_state.get("mode") or "").lower()
    execution_mode = "live" if "live" in mode_hint else "paper"
    order_ids = [str(item) for item in list(broker_state.get("order_ids") or []) if str(item)]
    if not order_ids:
        return {"ok": False, "error": "no_orders_to_poll", "execution_plan": plan}

    try:
        client = _build_broker_client(provider, execution_root=SIMULATION_EXECUTION_ROOT, execution_mode=execution_mode)
    except Exception as exc:
        return {
            "ok": False,
            "error": "broker_client_init_failed",
            "provider": provider,
            "execution_plan": plan,
            "reason": str(exc),
        }

    statuses: List[Dict[str, Any]] = []
    for order_id in order_ids:
        status = _broker_order_status(client, order_id)
        statuses.append(status)

    broker_state["order_statuses"] = statuses
    broker_state["last_status_poll"] = _utc_iso()
    fills = [str(item.get("status") or "").lower() for item in statuses if isinstance(item, dict)]
    if fills:
        if all(value == "filled" for value in fills):
            broker_state["fill_status"] = "filled"
        elif any(value == "rejected" for value in fills):
            broker_state["fill_status"] = "rejected"
        else:
            broker_state["fill_status"] = "submitted"

    focus_sync = _sync_plan_fill_to_focus_trading(plan, statuses)
    broker_state["focus_sync"] = focus_sync
    plan["broker"] = broker_state
    _save_plan(plan)
    return {"ok": True, "execution_plan": plan}


__all__ = [
    "create_simulation_execution_plan",
    "get_simulation_execution_plan",
    "list_simulation_execution_plans",
    "approve_simulation_execution_plan",
    "submit_simulation_execution_plan",
    "sync_simulation_execution_plan_with_broker",
    "reconcile_simulation_execution_orders",
]
