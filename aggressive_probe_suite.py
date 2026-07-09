from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


APOLLO_ROOT = Path(__file__).resolve().parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "aggressive_probe_suite"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    return f"apollo_aggressive_probe_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _phase(ok: bool, *, name: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _proposal(ticker: str, *, score: float = 82, rr: float = 1.8, **extra: Any) -> Dict[str, Any]:
    row = {
        "ticker": ticker,
        "entry_zone": "100.00-101.00",
        "stop_zone": "95.00",
        "target_zone": "112.00",
        "setup_type": "trend_continuation",
        "score": score,
        "news_blended_score": score,
        "risk_reward_estimate": rr,
        "direction_bias": "long_bias",
        "confidence_label": "high",
        "price_action_confirmation": {"ok": True, "reason": "confirmed_recovery"},
        "volume_confirmation": {"ok": True, "reason": "confirmed_breakout_volume", "volume_ratio": 1.5},
        "event_impact": {"impact_score": 68},
    }
    row.update(extra)
    return row


def _news_for(proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "news_evidence": "good",
        "news_by_ticker": {
            row["ticker"]: {
                "headline_count": 3,
                "weighted_sentiment": 0.25,
                "sentiment_backend": "keyword_fallback",
                "sentiment_label": "positive",
                "sentiment_confidence": 0.8,
                "items": [],
            }
            for row in proposals
        },
        "blended_proposals": proposals,
    }


def _market_for(proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"market_regime": {"label": "risk_on"}, "proposals": proposals}


def _with_risk_io(trade_cycle: Any, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    old_log = trade_cycle._log
    old_liq = trade_cycle._liquidity_ok
    old_entry = trade_cycle._entry_valid
    try:
        trade_cycle._log = lambda message: None
        trade_cycle._liquidity_ok = lambda ticker: {"ticker": ticker, "avg_volume_30d": 2_000_000, "min_required": 500_000, "ok": True}
        trade_cycle._entry_valid = lambda ticker, low, high: {"ok": True, "live": 100.5, "entry_low": low, "entry_high": high}
        return fn()
    finally:
        trade_cycle._log = old_log
        trade_cycle._liquidity_ok = old_liq
        trade_cycle._entry_valid = old_entry


def aggressive_risk_lane_tests() -> Dict[str, Any]:
    from Apollo import trade_cycle

    def run() -> Dict[str, Any]:
        proposal = _proposal("DDOG")
        result = trade_cycle._stage_risk_checks(REPORT_ROOT / "_tmp", _news_for([proposal]), _market_for([proposal]))
        row = result["aggressive_probe_candidates"][0] if result.get("aggressive_probe_candidates") else {}
        ok = (
            bool(result.get("aggressive_probe_ok"))
            and row.get("risk_tier") == "aggressive_probe"
            and row.get("aggressive_probe_ready") is True
            and row.get("all_checks_pass") is False
            and row.get("live_trade_execution") is False
            and row.get("human_approval_required") is True
        )
        return _phase(ok, name="aggressive_risk_lane_tests", detail={"risk": result, "candidate": row})

    return _with_risk_io(trade_cycle, run)


def pressed_graph_risk_tests() -> Dict[str, Any]:
    from Apollo import trade_cycle

    def run() -> Dict[str, Any]:
        proposal = _proposal(
            "DDOG",
            score=84,
            rr=2.4,
            event_impact={"impact_score": 78},
            graph_risk_appetite={
                "score": 82,
                "conviction": "high",
                "use_for_aggressive_reward": True,
                "upside_terms": ["guidance", "growth", "demand"],
            },
            aggressive_reward_profile={
                "pressed_candidate": True,
                "extension": {"applied": True, "reason": "graph_backed_asymmetric_target_extension"},
                "paper_only": True,
            },
        )
        result = trade_cycle._stage_risk_checks(REPORT_ROOT / "_tmp", _news_for([proposal]), _market_for([proposal]))
        row = result["aggressive_probe_candidates"][0] if result.get("aggressive_probe_candidates") else {}
        ok = (
            row.get("pressed_aggressive") is True
            and row.get("risk_tier") == "aggressive_probe"
            and row.get("position_sizing", {}).get("risk_pct") == trade_cycle.PRESSED_AGGRESSIVE_RISK_PCT
            and row.get("position_sizing", {}).get("max_notional_pct") == trade_cycle.PRESSED_AGGRESSIVE_MAX_NOTIONAL_PCT
            and row.get("standard_ready") is False
            and row.get("live_trade_execution") is False
        )
        return _phase(ok, name="pressed_graph_risk_tests", detail={"risk": result, "candidate": row})

    return _with_risk_io(trade_cycle, run)


def aggressive_blocking_tests() -> Dict[str, Any]:
    from Apollo import trade_cycle

    def run() -> Dict[str, Any]:
        low_rr = _proposal("NET", rr=0.9, event_impact={"impact_score": 40}, volume_confirmation={"ok": True, "volume_ratio": 1.0})
        invalid_geo = _proposal("MDB", stop_zone="105.00")
        result = trade_cycle._stage_risk_checks(REPORT_ROOT / "_tmp", _news_for([low_rr, invalid_geo]), _market_for([low_rr, invalid_geo]))
        rows = {row.get("ticker"): row for row in result.get("checks", [])}
        ok = (
            rows["NET"].get("aggressive_probe_ready") is False
            and rows["NET"].get("aggressive_next_action") in {"blocked_low_reward", "blocked_weak_evidence"}
            and rows["MDB"].get("aggressive_probe_ready") is False
            and "invalid_long_stop_above_entry" in rows["MDB"].get("risk_notes", [])
        )
        return _phase(ok, name="aggressive_blocking_tests", detail={"checks": rows})

    return _with_risk_io(trade_cycle, run)


def aggressive_universe_tests() -> Dict[str, Any]:
    from Apollo import trade_cycle

    old_enabled = trade_cycle.AGGRESSIVE_UNIVERSE_ENABLED
    try:
        trade_cycle.AGGRESSIVE_UNIVERSE_ENABLED = False
        off = trade_cycle._event_research_tickers()
        trade_cycle.AGGRESSIVE_UNIVERSE_ENABLED = True
        on = trade_cycle._event_research_tickers()
    finally:
        trade_cycle.AGGRESSIVE_UNIVERSE_ENABLED = old_enabled
    added = sorted(set(on) - set(off))
    ok = "DDOG" in added or "UBER" in added or "COIN" in added
    return _phase(ok, name="aggressive_universe_tests", detail={"added_sample": added[:12], "off_count": len(off), "on_count": len(on)})


def simulation_label_tests() -> Dict[str, Any]:
    from Apollo import swing_simulation

    old_fetch = swing_simulation._fetch_history
    try:
        swing_simulation._fetch_history = lambda ticker, days=90: [
            {"date": "2026-05-20", "open": 100.0, "high": 112.5, "low": 99.0, "close": 110.0, "volume": 2_000_000}
        ]
        result = swing_simulation.run_simulation(
            {
                "ticker": "UNIT",
                "entry_zone": "100.00-101.00",
                "stop_zone": "95.00",
                "target_zone": "112.00",
                "risk_tier": "aggressive_probe",
                "paper_only": True,
                "paper_risk_pct": 0.005,
                "max_notional_pct": 0.05,
                "aggressive_probe_ready": True,
                "aggressive_reason": "rr_asymmetric; event_catalyst",
                "aggressive_next_action": "paper_aggressive_ready",
                "avg_volume_30d": 2_000_000,
                "created_at": "2026-05-20",
            },
            save=False,
        )
    finally:
        swing_simulation._fetch_history = old_fetch
    ok = (
        result.get("risk_tier") == "aggressive_probe"
        and result.get("aggressive_probe_ready") is True
        and result.get("live_trade_execution") is False
        and isinstance(result.get("execution_realism"), dict)
    )
    return _phase(ok, name="simulation_label_tests", detail=result)


def _build_markdown(status: Dict[str, Any]) -> str:
    lines = [
        f"# Apollo Aggressive Probe Suite - {status['run_id']}",
        "",
        f"- Status: `{status['status']}`",
        f"- Score: `{status['score']}`",
        f"- Completed at: `{status['completed_at']}`",
        "- Live execution submitted: `False`",
        "",
        "## Phases",
    ]
    for phase in status["phases"]:
        lines.append(f"- `{phase['name']}`: `{'passed' if phase['ok'] else 'failed'}`")
    return "\n".join(lines) + "\n"


def run_suite(*, run_id: Optional[str] = None, write_reports: bool = True) -> Dict[str, Any]:
    safe_run_id = run_id or _default_run_id()
    phases = [
        aggressive_risk_lane_tests(),
        pressed_graph_risk_tests(),
        aggressive_blocking_tests(),
        aggressive_universe_tests(),
        simulation_label_tests(),
    ]
    passed = sum(1 for phase in phases if phase.get("ok"))
    total = len(phases)
    status = {
        "ok": passed == total,
        "status": "passed" if passed == total else "failed",
        "run_id": safe_run_id,
        "completed_at": _utc_iso(),
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "score": round((passed / max(1, total)) * 100.0, 2),
        "phases": phases,
        "live_trade_execution": False,
        "human_approval_required": True,
    }
    if write_reports:
        run_dir = REPORT_ROOT / safe_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "status.json"
        md_path = run_dir / "report.md"
        json_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(_build_markdown(status), encoding="utf-8")
        status["json_path"] = str(json_path)
        status["markdown_path"] = str(md_path)
    return status


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Apollo aggressive paper probe suite.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run_suite(run_id=args.run_id or None, write_reports=not args.no_write)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
