from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


APOLLO_ROOT = Path(__file__).resolve().parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "improvement_suite"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    return f"apollo_improvement_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@contextmanager
def _temporary_env(values: Dict[str, str]) -> Iterator[None]:
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _phase(ok: bool, *, name: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def signal_confirmation_tests() -> Dict[str, Any]:
    from Apollo import trade_cycle

    pullback_missing = trade_cycle._proposal_price_action_confirmation({"setup_type": "pullback_to_trend"})
    pullback_confirmed = trade_cycle._proposal_price_action_confirmation(
        {"setup_type": "pullback_to_trend", "price_action_confirmation": {"ok": True, "reason": "confirmed_recovery"}}
    )
    breakout_missing = trade_cycle._proposal_breakout_volume_confirmation({"setup_type": "base_breakout"})
    breakout_confirmed = trade_cycle._proposal_breakout_volume_confirmation(
        {"setup_type": "base_breakout", "volume_confirmation": {"ok": True, "reason": "confirmed_breakout_volume", "volume_ratio": 1.8}}
    )
    ok = (
        not pullback_missing["ok"]
        and pullback_confirmed["ok"]
        and not breakout_missing["ok"]
        and breakout_confirmed["ok"]
    )
    return _phase(
        ok,
        name="signal_confirmation_tests",
        detail={
            "pullback_missing": pullback_missing,
            "pullback_confirmed": pullback_confirmed,
            "breakout_missing": breakout_missing,
            "breakout_confirmed": breakout_confirmed,
        },
    )


def sentiment_backend_tests() -> Dict[str, Any]:
    from Apollo import trade_cycle

    fallback_gate = trade_cycle._sentiment_standard_allowed({"sentiment_backend": "keyword_fallback", "sentiment_label": "positive", "sentiment_confidence": 0.9})
    model_gate = trade_cycle._sentiment_standard_allowed({"sentiment_backend": "local_model", "sentiment_label": "positive", "sentiment_confidence": 0.92})
    mixed_gate = trade_cycle._sentiment_standard_allowed({"sentiment_backend": "local_model", "sentiment_label": "mixed", "sentiment_confidence": 0.55})
    ok = not fallback_gate["ok"] and model_gate["ok"] and not mixed_gate["ok"]
    return _phase(ok, name="sentiment_backend_tests", detail={"fallback": fallback_gate, "model": model_gate, "mixed": mixed_gate})


def watchlist_hygiene_tests() -> Dict[str, Any]:
    from Apollo import config, trade_cycle

    default_watchlist = config.get_watchlist("")
    blocked = [ticker for ticker in config.get_high_risk_review_tickers() if ticker not in default_watchlist]
    rtx_default = trade_cycle._high_risk_standard_allowed("RTX")
    with _temporary_env({"APOLLO_WATCHLIST": "RTX,NVDA"}):
        rtx_override = trade_cycle._high_risk_standard_allowed("RTX")
    ok = "RTX" in blocked and "PLTR" in blocked and not rtx_default["standard_allowed"] and rtx_override["standard_allowed"]
    return _phase(ok, name="watchlist_hygiene_tests", detail={"blocked": blocked, "rtx_default": rtx_default, "rtx_override": rtx_override})


def hipporag_usefulness_benchmark() -> Dict[str, Any]:
    from Apollo import hipporag_benchmark

    result = hipporag_benchmark.run_live_benchmark()
    return _phase(bool(result.get("ok")), name="hipporag_usefulness_benchmark", detail=result)


def paper_sim_realism_tests() -> Dict[str, Any]:
    from Apollo import simulation_execution

    liquid = simulation_execution._execution_realism_profile(entry_price=100.0, qty=100, avg_volume=5_000_000)
    thin = simulation_execution._execution_realism_profile(entry_price=100.0, qty=2_000, avg_volume=500_000)
    ok = liquid["execution_realism_score"] > thin["execution_realism_score"] and thin["warnings"]
    return _phase(ok, name="paper_sim_realism_tests", detail={"liquid": liquid, "thin": thin})


def live_guard_block_tests() -> Dict[str, Any]:
    from Apollo import simulation_execution

    with _temporary_env({"APOLLO_SIM_LIVE_ENABLED": "0"}):
        try:
            simulation_execution._build_broker_client("alpaca", execution_mode="live")
            result = {"blocked": False, "error": ""}
        except Exception as exc:
            result = {"blocked": "live_execution_disabled" in str(exc), "error": str(exc)}
    return _phase(bool(result["blocked"]), name="live_guard_block_tests", detail=result)


def _build_markdown(status: Dict[str, Any]) -> str:
    lines = [
        f"# Apollo Improvement Suite - {status['run_id']}",
        "",
        f"- Status: `{status['status']}`",
        f"- Score: `{status['score']}`",
        f"- Completed at: `{status['completed_at']}`",
        f"- Live execution submitted: `{False}`",
        "",
        "## Phases",
    ]
    for phase in status["phases"]:
        lines.append(f"- `{phase['name']}`: `{'passed' if phase['ok'] else 'failed'}`")
    lines.extend(["", "## HippoRAG"])
    hippo = next((row for row in status["phases"] if row["name"] == "hipporag_usefulness_benchmark"), {})
    detail = hippo.get("detail") or {}
    lines.append(f"- Label: `{detail.get('hipporag_label') or 'unknown'}`")
    lines.append(f"- Dense avg: `{detail.get('dense_only_avg')}`")
    lines.append(f"- Dense+graph avg: `{detail.get('dense_graph_avg')}`")
    lines.append(f"- Fail-soft: `{detail.get('fail_soft')}`")
    return "\n".join(lines) + "\n"


def run_suite(*, run_id: Optional[str] = None, write_reports: bool = True) -> Dict[str, Any]:
    safe_run_id = run_id or _default_run_id()
    phases = [
        signal_confirmation_tests(),
        sentiment_backend_tests(),
        watchlist_hygiene_tests(),
        hipporag_usefulness_benchmark(),
        paper_sim_realism_tests(),
        live_guard_block_tests(),
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
        "broker_execution_enabled": False,
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
    parser = argparse.ArgumentParser(description="Run Apollo's bounded improvement readiness suite.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run_suite(run_id=args.run_id or None, write_reports=not args.no_write)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
