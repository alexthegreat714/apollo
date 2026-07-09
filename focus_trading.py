from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from Apollo import nightly_pipeline


_APOLLO_ROOT = Path(__file__).resolve().parent
STATE_PATH = _APOLLO_ROOT / "logs" / "nightly" / "background" / "focus_trading_state.json"
FOCUS_DOC_ID = "apollo_focus_universe_current"
MAX_EVENTS = 80


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _default_state() -> Dict[str, Any]:
    return {
        "ok": True,
        "updated_at": "",
        "last_status_seq": 0,
        "last_run_id": "",
        "focus": {},
        "paper_positions": [],
        "events": [],
        "discussion_context": "",
    }


def load_state(path: Optional[str] = None) -> Dict[str, Any]:
    state_path = Path(path).resolve() if path else STATE_PATH
    state = _read_json(state_path, _default_state())
    for key, default in _default_state().items():
        state.setdefault(key, default)
    state["state_path"] = str(state_path)
    return state


def save_state(state: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    state_path = Path(path).resolve() if path else STATE_PATH
    payload = dict(_default_state())
    payload.update(dict(state or {}))
    payload["updated_at"] = _utc_iso()
    payload["events"] = list(payload.get("events") or [])[-MAX_EVENTS:]
    _write_json(state_path, payload)
    payload["state_path"] = str(state_path)
    return payload


def _append_event(state: Dict[str, Any], *, event_type: str, summary: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "id": hashlib.md5(f"{event_type}:{summary}:{_utc_iso()}".encode("utf-8")).hexdigest()[:12],
        "ts": _utc_iso(),
        "type": str(event_type or "event").strip() or "event",
        "summary": str(summary or "").strip(),
        "details": dict(details or {}),
    }
    events = list(state.get("events") or [])
    events.append(payload)
    state["events"] = events[-MAX_EVENTS:]
    return payload


def _focus_from_status(status: Dict[str, Any]) -> Dict[str, Any]:
    focus = dict(status.get("focus_universe") or {})
    if focus:
        return focus
    stage_details = dict(status.get("stage_details") or {})
    focus = dict(stage_details.get("focus_universe") or {})
    if focus:
        return focus
    gather_stage = dict((status.get("stages") or {}).get("gather") or {})
    return dict(gather_stage.get("focus_universe") or {})


def _decision_from_status(status: Dict[str, Any]) -> Dict[str, Any]:
    focus = dict(status.get("focus_universe") or {})
    decision = dict(focus.get("decision_gate") or {})
    if decision:
        return decision
    self_check = dict((status.get("stages") or {}).get("self_check") or {})
    return dict(self_check.get("decision_gate") or {})


def build_discussion_context(state: Dict[str, Any]) -> str:
    focus = dict(state.get("focus") or {})
    selected_theme = str(focus.get("selected_theme") or "").strip()
    tickers = [str(item).strip().upper() for item in list(focus.get("selected_tickers") or []) if str(item).strip()]
    decision = dict(focus.get("decision_gate") or {})
    lines = [
        f"Focus theme: {selected_theme or 'none'}",
        f"Universe tickers: {', '.join(tickers[:8]) or 'none'}",
        f"Theme score: {focus.get('theme_score', 0)}",
        f"Evidence volume: {focus.get('evidence_volume', 0)}",
        f"Decision ready: {decision.get('decision_ready')}",
        f"Decision mode: {decision.get('decision_mode') or 'n/a'}",
        f"Confidence: {decision.get('confidence_label') or 'n/a'}",
    ]
    positions = [row for row in list(state.get("paper_positions") or []) if isinstance(row, dict)]
    if positions:
        active = [f"{row.get('ticker')} {row.get('stance')}" for row in positions[:6]]
        lines.append(f"Paper positions: {', '.join(active)}")
    events = [row for row in list(state.get("events") or []) if isinstance(row, dict)]
    if events:
        lines.append(f"Recent event: {str(events[-1].get('summary') or '').strip()}")
    return "\n".join(lines).strip()


def _remember_focus_snapshot(state: Dict[str, Any], rag: Any = None) -> None:
    if rag is None:
        return
    try:
        rag.delete(ids=[FOCUS_DOC_ID])
    except Exception:
        pass
    focus = dict(state.get("focus") or {})
    text = "[Apollo Focus Universe]\n" + build_discussion_context(state)
    try:
        rag.remember(
            text=text,
            source="apollo_focus_universe",
            kind="financial_event",
            priority=0.92,
            tags=["apollo", "focus_universe", "paper_trading"],
            extra={
                "selected_theme": str(focus.get("selected_theme") or ""),
                "decision_ready": bool(((focus.get("decision_gate") or {}).get("decision_ready"))),
                "theme_score": float(focus.get("theme_score") or 0.0),
            },
            id_=FOCUS_DOC_ID,
        )
    except Exception:
        return


def sync_from_pipeline(
    *,
    latest_status: Optional[Dict[str, Any]] = None,
    rag: Any = None,
    memory_store: Any = None,
    note: str = "",
    path: Optional[str] = None,
) -> Dict[str, Any]:
    state = load_state(path)
    status = dict(latest_status or nightly_pipeline.latest_status() or {})
    focus = _focus_from_status(status)
    if not focus:
        state["discussion_context"] = build_discussion_context(state)
        return save_state(state, path)

    selected_theme = str(focus.get("selected_theme") or "").strip()
    decision = _decision_from_status(status)
    selected_tickers = [str(item).strip().upper() for item in list(focus.get("selected_tickers") or []) if str(item).strip()]
    if not selected_tickers:
        selected_tickers = [str(item.get("ticker") or "").strip().upper() for item in list(((status.get("stages") or {}).get("gather") or {}).get("focus_universe", {}).get("selected_tickers") or []) if str(item.get("ticker") or "").strip()]
    status_seq = int(status.get("status_seq") or 0)
    previous_focus = dict(state.get("focus") or {})
    changed = status_seq > int(state.get("last_status_seq") or 0) or selected_theme != str(previous_focus.get("selected_theme") or "")
    state["last_status_seq"] = status_seq
    state["last_run_id"] = str(status.get("run_id") or state.get("last_run_id") or "")
    state["focus"] = {
        "selected_theme": selected_theme,
        "selected_theme_id": str(focus.get("selected_theme_id") or focus.get("selected_theme_id") or ""),
        "selected_tickers": selected_tickers,
        "theme_score": float(focus.get("theme_score") or 0.0),
        "evidence_volume": float(focus.get("evidence_volume") or 0.0),
        "query_count": int(focus.get("query_count") or 0),
        "decision_gate": decision,
        "overall_quality_score": int(((status.get("quality") or {}).get("overall_score")) or 0),
        "confidence_band": str((((status.get("quality") or {}).get("confidence_band") or {}).get("label")) or ""),
        "source_run_id": str(status.get("run_id") or ""),
    }
    state["discussion_context"] = build_discussion_context(state)
    if changed or note:
        summary = f"Focus universe learned: {selected_theme or 'none'} ({', '.join(selected_tickers[:5]) or 'no tickers'})"
        if note:
            summary = f"{summary} | note={note}"
        event = _append_event(
            state,
            event_type="learning_sync",
            summary=summary,
            details={"run_id": state.get("last_run_id"), "status_seq": status_seq, "decision": decision},
        )
        if memory_store is not None:
            try:
                memory_store.log_event(summary, event_type="focus_universe_learning", details=event.get("details"))
            except Exception:
                pass
    _remember_focus_snapshot(state, rag=rag)
    return save_state(state, path)


def submit_paper_trade(
    *,
    action: str,
    ticker: str = "",
    thesis: str = "",
    confidence: float = 0.0,
    horizon: str = "",
    note: str = "",
    rag: Any = None,
    memory_store: Any = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    state = load_state(path)
    action_norm = str(action or "").strip().lower()
    if action_norm not in {"buy", "sell", "hold", "no_trade", "close"}:
        return {"ok": False, "error": "invalid_action", "allowed": ["buy", "sell", "hold", "no_trade", "close"]}
    ticker_norm = str(ticker or "").strip().upper()
    focus = dict(state.get("focus") or {})
    allowed_tickers = {str(item).strip().upper() for item in list(focus.get("selected_tickers") or []) if str(item).strip()}
    if action_norm in {"buy", "sell", "close"} and not ticker_norm:
        return {"ok": False, "error": "ticker_required"}
    if ticker_norm and allowed_tickers and ticker_norm not in allowed_tickers:
        return {"ok": False, "error": "ticker_not_in_focus_universe", "allowed_tickers": sorted(allowed_tickers)}
    positions = [dict(item) for item in list(state.get("paper_positions") or []) if isinstance(item, dict)]
    updated_positions: List[Dict[str, Any]] = []
    existing = next((row for row in positions if str(row.get("ticker") or "").upper() == ticker_norm), None)
    if action_norm == "close":
        for row in positions:
            if str(row.get("ticker") or "").upper() == ticker_norm:
                continue
            updated_positions.append(row)
    elif action_norm in {"buy", "sell"}:
        for row in positions:
            if str(row.get("ticker") or "").upper() == ticker_norm:
                continue
            updated_positions.append(row)
        updated_positions.append(
            {
                "ticker": ticker_norm,
                "stance": "long" if action_norm == "buy" else "short",
                "status": "open",
                "opened_at": _utc_iso(),
                "thesis": str(thesis or "").strip(),
                "confidence": round(max(0.0, min(float(confidence or 0.0), 1.0)), 3),
                "horizon": str(horizon or "").strip(),
                "note": str(note or "").strip(),
            }
        )
    else:
        updated_positions = positions
    state["paper_positions"] = updated_positions
    event_summary = f"Paper trade {action_norm}: {ticker_norm or 'none'}"
    if thesis:
        event_summary += f" | {str(thesis).strip()[:140]}"
    event = _append_event(
        state,
        event_type="paper_trade",
        summary=event_summary,
        details={
            "action": action_norm,
            "ticker": ticker_norm,
            "thesis": str(thesis or "").strip(),
            "confidence": round(max(0.0, min(float(confidence or 0.0), 1.0)), 3),
            "horizon": str(horizon or "").strip(),
            "had_existing_position": bool(existing),
        },
    )
    if memory_store is not None:
        try:
            memory_store.log_event(event_summary, event_type="paper_trade", details=event.get("details"))
        except Exception:
            pass
    state["discussion_context"] = build_discussion_context(state)
    _remember_focus_snapshot(state, rag=rag)
    saved = save_state(state, path)
    return {"ok": True, "event": event, "positions": saved.get("paper_positions") or [], "focus": saved.get("focus") or {}}


def format_focus_summary(state: Dict[str, Any]) -> str:
    focus = dict(state.get("focus") or {})
    selected_theme = str(focus.get("selected_theme") or "none").strip()
    tickers = [str(item).strip().upper() for item in list(focus.get("selected_tickers") or []) if str(item).strip()]
    decision = dict(focus.get("decision_gate") or {})
    lines = [
        f"Apollo Focus Universe: theme={selected_theme}",
        f"Tickers: {', '.join(tickers[:8]) or 'none'}",
        f"Theme score: {focus.get('theme_score', 0)} | Evidence volume: {focus.get('evidence_volume', 0)}",
        f"Decision gate: ready={decision.get('decision_ready')} mode={decision.get('decision_mode') or 'n/a'} confidence={decision.get('confidence_label') or 'n/a'}",
    ]
    positions = [row for row in list(state.get("paper_positions") or []) if isinstance(row, dict)]
    if positions:
        lines.append("Paper positions: " + ", ".join(f"{row.get('ticker')} {row.get('stance')}" for row in positions[:6]))
    return "\n".join(lines)


def format_recent_events(state: Dict[str, Any], *, limit: int = 5) -> str:
    events = [row for row in list(state.get("events") or []) if isinstance(row, dict)]
    if not events:
        return "No focus-universe events recorded yet."
    lines = []
    for row in events[-max(1, int(limit)):]:
        lines.append(f"- {row.get('ts')}: {row.get('summary')}")
    return "\n".join(lines)
