from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


APOLLO_ROOT = Path(__file__).resolve().parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "premarket_sim_readiness"
DEFAULT_BASE_URL = "http://127.0.0.1:5218"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: Any, fallback: str = "premarket_sim_readiness") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._-")
    return text[:120] or fallback


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    except Exception:
        pass
    return default


def _http_json(url: str, *, timeout: float = 8.0) -> Dict[str, Any]:
    try:
        with urlrequest.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else {}
            return {"ok": True, "status_code": resp.getcode(), "body": payload}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"ok": False, "status_code": int(exc.code), "error": raw[-1200:]}
    except URLError as exc:
        return {"ok": False, "status_code": 0, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "error": str(exc)}


def _phase(name: str, ok: bool, detail: Dict[str, Any], *, required: bool = True) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": bool(required), "detail": dict(detail or {})}


def _env_value(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _provider_ticker_rows(provider_health: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    health = provider_health.get("provider_health") if isinstance(provider_health.get("provider_health"), dict) else {}
    tickers = health.get("tickers") if isinstance(health.get("tickers"), dict) else {}
    for ticker, row in tickers.items():
        if isinstance(row, dict):
            yield {"ticker": str(ticker), **row}


def check_apollo_health(base_url: str = DEFAULT_BASE_URL) -> Dict[str, Any]:
    result = _http_json(f"{base_url.rstrip('/')}/health", timeout=8)
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    ok = bool(result.get("ok")) and str(body.get("status") or "").lower() == "ok" and bool(body.get("financial_corpus_ready"))
    return _phase("apollo_health", ok, {"base_url": base_url, "http": result, "status": body.get("status"), "financial_corpus_ready": body.get("financial_corpus_ready")})


def check_provider_freshness(base_url: str = DEFAULT_BASE_URL, *, max_stale_days: int = 4) -> Dict[str, Any]:
    result = _http_json(f"{base_url.rstrip('/')}/admin/swing/provider_health", timeout=15)
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    rows = list(_provider_ticker_rows(body))
    stale_rows = []
    failed_rows = []
    for row in rows:
        if not bool(row.get("ok")):
            failed_rows.append({"ticker": row.get("ticker"), "reason": "provider_unhealthy", "row": row})
        stale_days = row.get("stale_days")
        if stale_days is not None and _safe_float(stale_days, 999.0) > max_stale_days:
            stale_rows.append({"ticker": row.get("ticker"), "stale_days": stale_days, "latest_date": row.get("latest_date")})
    ok = bool(result.get("ok")) and bool(body.get("ok")) and bool(rows) and not stale_rows and not failed_rows
    return _phase(
        "provider_freshness",
        ok,
        {
            "base_url": base_url,
            "max_stale_days": max_stale_days,
            "ticker_count": len(rows),
            "stale_rows": stale_rows,
            "failed_rows": failed_rows,
            "updated_at": (body.get("provider_health") or {}).get("updated_at") if isinstance(body.get("provider_health"), dict) else "",
            "http_status_code": result.get("status_code"),
            "error": result.get("error", ""),
        },
    )


def check_daily_report_open(report_date: Optional[str] = None) -> Dict[str, Any]:
    from Apollo import daily_report

    report = daily_report.get_daily_report(report_date)
    paths = report.get("paths") if isinstance(report.get("paths"), dict) else {}
    json_path = Path(str(paths.get("json") or ""))
    md_path = Path(str(paths.get("markdown") or ""))
    ok = bool(report.get("ok")) and bool(report.get("date")) and json_path.exists() and md_path.exists()
    return _phase("daily_report_open", ok, {"date": report.get("date"), "paths": paths, "markdown_chars": len(str(report.get("markdown") or ""))})


def check_stress_sandbox_config() -> Dict[str, Any]:
    from Apollo import stress_mode

    cfg = stress_mode.resolve_stress_mode()
    sim_broker = _env_value("APOLLO_SIM_BROKER_PROVIDER").lower() or "paper_sim"
    sim_mode = _env_value("APOLLO_SIM_EXECUTION_MODE").lower()
    blockers = []
    if not bool(cfg.get("enabled")):
        blockers.append("stress_mode_disabled")
    if str(cfg.get("provider") or "").lower() != "paper_sim":
        blockers.append("stress_provider_not_paper_sim")
    if bool(cfg.get("live_trade_execution")):
        blockers.append("stress_live_trade_execution_true")
    if not bool(cfg.get("paper_only")):
        blockers.append("stress_not_paper_only")
    if not bool(cfg.get("human_approval_required")):
        blockers.append("human_approval_not_required")
    if bool(cfg.get("production_readiness_credit")):
        blockers.append("production_readiness_credit_true")
    if sim_broker not in {"", "paper_sim"}:
        blockers.append(f"simulation_broker_env_not_paper_sim:{sim_broker}")
    if sim_mode and sim_mode not in {"paper", "paper_sim", "simulation", "sim"}:
        blockers.append(f"simulation_mode_env_unexpected:{sim_mode}")
    return _phase("stress_sandbox_config", not blockers, {"config": cfg, "env": {"APOLLO_SIM_BROKER_PROVIDER": sim_broker, "APOLLO_SIM_EXECUTION_MODE": sim_mode}, "blockers": blockers})


def check_aggressive_probe_suite(run_id: str) -> Dict[str, Any]:
    from Apollo import aggressive_probe_suite

    result = aggressive_probe_suite.run_suite(run_id=f"{run_id}_aggressive_probe", write_reports=True)
    ok = (
        bool(result.get("ok"))
        and _safe_float(result.get("score"), 0.0) >= 100.0
        and bool(result.get("human_approval_required"))
        and bool(result.get("live_trade_execution")) is False
    )
    return _phase("aggressive_probe_suite", ok, result)


def run_readiness_gate(
    *,
    run_id: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    report_date: Optional[str] = None,
    max_stale_days: int = 4,
    write_reports: bool = True,
) -> Dict[str, Any]:
    safe_run_id = _slug(run_id or f"premarket_sim_readiness_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    phases = [
        check_apollo_health(base_url),
        check_provider_freshness(base_url, max_stale_days=max_stale_days),
        check_daily_report_open(report_date),
        check_stress_sandbox_config(),
        check_aggressive_probe_suite(safe_run_id),
    ]
    failed_required = [phase for phase in phases if phase.get("required") and not phase.get("ok")]
    status = {
        "ok": not failed_required,
        "status": "passed" if not failed_required else "failed",
        "run_id": safe_run_id,
        "generated_at": _utc_iso(),
        "base_url": base_url,
        "report_date": report_date or "",
        "max_stale_days": max_stale_days,
        "phases": phases,
        "failed_required": [phase.get("name") for phase in failed_required],
        "decision": "ready_for_high_risk_simulation" if not failed_required else "blocked",
        "scope": "simulation_only",
        "live_trade_execution": False,
        "human_approval_required": True,
        "provider": "paper_sim",
        "production_readiness_credit": False,
    }
    if write_reports:
        run_dir = REPORT_ROOT / safe_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "status.json"
        md_path = run_dir / "report.md"
        status["json_path"] = str(json_path)
        status["markdown_path"] = str(md_path)
        json_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(_render_markdown(status), encoding="utf-8")
        latest_dir = REPORT_ROOT / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        (latest_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        (latest_dir / "report.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return status


def _render_markdown(status: Dict[str, Any]) -> str:
    lines = [
        "# Apollo Premarket Simulation Readiness",
        "",
        f"- Run id: `{status.get('run_id')}`",
        f"- Generated: `{status.get('generated_at')}`",
        f"- Status: `{status.get('status')}`",
        f"- Decision: `{status.get('decision')}`",
        f"- Scope: `{status.get('scope')}`",
        f"- Provider: `{status.get('provider')}`",
        f"- Live trade execution: `{status.get('live_trade_execution')}`",
        f"- Human approval required: `{status.get('human_approval_required')}`",
        f"- Production readiness credit: `{status.get('production_readiness_credit')}`",
        "",
        "## Gates",
        "",
    ]
    for phase in status.get("phases") or []:
        lines.append(f"- `{phase.get('name')}`: `{'passed' if phase.get('ok') else 'failed'}`")
    if status.get("failed_required"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- `{item}`" for item in status.get("failed_required") or [])
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Passing this gate means Apollo is ready to run high-variance simulation-only probes. It does not approve Alpaca, paper broker order submission, or live trading.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apollo premarket high-risk simulation readiness gate.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--date", default="")
    parser.add_argument("--max-stale-days", type=int, default=int(os.getenv("APOLLO_PROVIDER_MAX_STALE_DAYS", "4") or "4"))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run_readiness_gate(
        run_id=args.run_id or None,
        base_url=args.base_url,
        report_date=args.date or None,
        max_stale_days=args.max_stale_days,
        write_reports=not args.no_write,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
