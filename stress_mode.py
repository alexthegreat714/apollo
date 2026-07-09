from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


APOLLO_ROOT = Path(__file__).resolve().parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "stress_simulation"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    except Exception:
        pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def resolve_stress_mode(portfolio_size: Optional[float] = None) -> Dict[str, Any]:
    paper_size = _safe_float(
        portfolio_size
        if portfolio_size is not None
        else (
            os.getenv("APOLLO_PAPER_PORTFOLIO_SIZE")
            or os.getenv("APOLLO_SIM_SIMULATED_BUYING_POWER")
            or os.getenv("APOLLO_SIM_BUYING_POWER")
            or "100"
        ),
        100.0,
    )
    return {
        "enabled": _to_bool(os.getenv("APOLLO_SIM_STRESS_MODE"), default=True),
        "mode": "stress_sandbox",
        "risk_pct": max(0.0, _safe_float(os.getenv("APOLLO_SIM_STRESS_RISK_PCT", "0.25"), 0.25)),
        "max_notional_pct": max(0.0, _safe_float(os.getenv("APOLLO_SIM_STRESS_MAX_NOTIONAL_PCT", "1.0"), 1.0)),
        "max_open_positions": max(0, _safe_int(os.getenv("APOLLO_SIM_STRESS_MAX_OPEN_POSITIONS", "3"), 3)),
        "allow_fractional": _to_bool(os.getenv("APOLLO_SIM_STRESS_ALLOW_FRACTIONAL", "1"), default=True),
        "paper_portfolio_size": paper_size,
        "paper_only": True,
        "live_trade_execution": False,
        "human_approval_required": True,
        "provider": "paper_sim",
        "evidence_class": "simulation_sandbox_evidence",
        "production_readiness_credit": False,
    }


def stress_position_sizing(
    *,
    entry_price: float,
    stop: float,
    direction_bias: str = "long_bias",
    portfolio_size: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    cfg = dict(config or resolve_stress_mode(portfolio_size))
    entry = _safe_float(entry_price, 0.0)
    stop_price = _safe_float(stop, 0.0)
    if entry <= 0 or stop_price <= 0:
        return None
    risk_per_share = stop_price - entry if direction_bias == "short_bias" else entry - stop_price
    if risk_per_share <= 0:
        return None
    paper_size = _safe_float(cfg.get("paper_portfolio_size"), 100.0)
    risk_pct = _safe_float(cfg.get("risk_pct"), 0.25)
    max_notional_pct = _safe_float(cfg.get("max_notional_pct"), 1.0)
    dollar_risk = paper_size * risk_pct
    uncapped_shares = dollar_risk / risk_per_share
    max_notional = paper_size * max_notional_pct
    max_shares = max_notional / entry if entry > 0 else 0.0
    shares = min(uncapped_shares, max_shares)
    if shares <= 0:
        return None
    notional = shares * entry
    return {
        "mode": "stress_sandbox",
        "risk_tier": "stress_sandbox",
        "portfolio_size": round(paper_size, 2),
        "risk_pct": risk_pct,
        "dollar_risk_target": round(dollar_risk, 2),
        "max_notional_pct": max_notional_pct,
        "max_notional_dollars": round(max_notional, 2),
        "entry_price": round(entry, 4),
        "stop": round(stop_price, 4),
        "risk_per_share": round(risk_per_share, 4),
        "shares": round(shares, 6),
        "fractional_shares": round(shares, 6),
        "uncapped_shares": round(uncapped_shares, 6),
        "capped_by_notional": shares < uncapped_shares,
        "total_cost": round(notional, 2),
        "planned_notional": round(notional, 2),
        "pct_of_portfolio": round((notional / paper_size) * 100.0, 2) if paper_size > 0 else 0.0,
        "max_loss_dollars": round(shares * risk_per_share, 2),
        "allow_fractional": bool(cfg.get("allow_fractional", True)),
        "paper_only": True,
        "live_trade_execution": False,
        "human_approval_required": True,
        "production_readiness_credit": False,
    }


def _candidate_label(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("ticker") or candidate.get("symbol") or "unknown").upper()


def write_stress_report(
    *,
    run_id: str,
    candidate: Dict[str, Any],
    sizing: Dict[str, Any],
    production_blockers: Optional[List[str]] = None,
    source: str = "apollo",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    safe_run_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(run_id or "stress_run")).strip("._-") or "stress_run"
    run_dir = REPORT_ROOT / safe_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    blockers = [str(item) for item in list(production_blockers or []) if str(item).strip()]
    cfg = resolve_stress_mode(sizing.get("portfolio_size"))
    payload = {
        "ok": True,
        "project": "apollo",
        "capability": "simulation_sandbox_evidence",
        "run_id": safe_run_id,
        "generated_at": _utc_iso(),
        "source": source,
        "test_type": "apollo_stress_simulation",
        "evidence_class": "simulation_sandbox_evidence",
        "simulation_stress_mode": bool(cfg.get("enabled")),
        "production_readiness_credit": False,
        "paper_only": True,
        "live_trade_execution": False,
        "human_approval_required": True,
        "candidate": dict(candidate or {}),
        "sizing": dict(sizing or {}),
        "production_blockers_ignored_for_sandbox": blockers,
        "extra": dict(extra or {}),
    }
    json_path = run_dir / "status.json"
    md_path = run_dir / "report.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Apollo Stress Simulation",
        "",
        f"- Run: `{safe_run_id}`",
        f"- Generated: `{payload['generated_at']}`",
        f"- Stress mode: `{payload['simulation_stress_mode']}`",
        f"- Evidence class: `{payload['evidence_class']}`",
        f"- Candidate: `{_candidate_label(candidate)}`",
        f"- Stress risk percent: `{round(_safe_float(sizing.get('risk_pct'), 0.0) * 100, 2)}%`",
        f"- Planned notional: `${_safe_float(sizing.get('planned_notional') or sizing.get('total_cost'), 0.0):.2f}`",
        f"- Estimated fractional shares: `{sizing.get('fractional_shares') or sizing.get('shares')}`",
        f"- Max loss at stop: `${_safe_float(sizing.get('max_loss_dollars'), 0.0):.2f}`",
        f"- Live broker execution: `{payload['live_trade_execution']}`",
        f"- Human approval required: `{payload['human_approval_required']}`",
        "",
        "## Production Blockers Ignored For Sandbox",
        "",
    ]
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- none recorded")
    lines.extend(
        [
            "",
            "## Warning",
            "",
            "This is high-variance simulation-only evidence. It does not count as production paper-trading readiness and cannot enable live execution.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    latest = REPORT_ROOT / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "status.json").write_text(json.dumps({**payload, "json_path": str(json_path), "markdown_path": str(md_path)}, indent=2, ensure_ascii=False), encoding="utf-8")
    (latest / "report.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    ftp_result: Dict[str, Any] = {"ok": False, "skipped": True, "reason": "sky_report_service_unavailable"}
    try:
        from Sky.services import test_run_reports

        ftp_result = test_run_reports.record_test_run_report(
            {
                "project": "apollo",
                "project_label": "Apollo",
                "test_type": "apollo_stress_simulation",
                "run_id": safe_run_id,
                "name": "Apollo simulation stress mode",
                "status": "passed",
                "score": 100,
                "summary": "Stress sandbox report captured as simulation-only evidence; no production readiness credit.",
                "capability": "simulation_sandbox_evidence",
                "verification_command": "Apollo stress simulation report writer",
                "tests_improved": ["apollo_stress_simulation"],
                "tests_confirmed": ["stress_sandbox_report_written"],
                "capabilities_confirmed": ["simulation_sandbox_evidence"],
                "capabilities_not_confirmed": ["production_trading_readiness"],
                "report_path": str(md_path),
                "artifact_paths": [str(md_path), str(json_path)],
                "diagnostic": {
                    "cause": "stress_sandbox_capture",
                    "category": "simulation",
                    "recoverable": False,
                    "next_action": "review stress dynamics separately from production paper readiness",
                    "confidence": 0.9,
                },
                "ftp_bridge": {
                    "pipeline_id": safe_run_id,
                    "campaign_id": "apollo_stress_simulation",
                    "campaign_stage": 1,
                    "phase": "complete",
                    "progress_percent": 100,
                    "summary_md_path": str(md_path),
                },
                "details": json.dumps(payload, indent=2),
            }
        )
    except Exception as exc:
        ftp_result = {"ok": False, "skipped": True, "reason": type(exc).__name__, "error": str(exc)}
    latest_payload = {**payload, "json_path": str(json_path), "markdown_path": str(md_path), "ftp_result": ftp_result}
    (latest / "status.json").write_text(json.dumps(latest_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "json_path": str(json_path), "markdown_path": str(md_path), "payload": payload, "ftp_result": ftp_result}
