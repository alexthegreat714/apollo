"""
Standalone Apollo Trade Simulation Dashboard — port 5222
Run: python simulation_server.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

_PORT = int(os.getenv("SIM_PORT", "5225"))


def _require_local_token() -> tuple | None:
    local_token = os.getenv("SKY_LOCAL_TOKEN", "") or os.getenv("APOLLO_LOCAL_TOKEN", "")
    allow_anon = os.getenv("SKY_ALLOW_ANON", "0") == "1"
    if not local_token or allow_anon:
        return None
    token = request.headers.get("X-Apollo-Token", "") or request.headers.get("X-Sky-Token", "")
    if token == local_token:
        return None
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower() == f"bearer {local_token}".lower():
        return None
    return {"ok": False, "error": "unauthorized"}, 401


def _simulation_request_context() -> dict:
    token = request.headers.get("X-Apollo-Token", "") or request.headers.get("X-Sky-Token", "")
    token_hint = token[:12] if token else ""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    remote_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else (request.remote_addr or "")
    return {
        "remote_ip": remote_ip.strip(),
        "user_agent": request.headers.get("User-Agent", "").strip(),
        "token_hint": token_hint,
    }
@app.route("/")
@app.route("/simulation")
def dashboard():
    return render_template("simulation_dashboard.html", agent="Apollo")


@app.route("/api/simulation/run", methods=["POST"])
def api_run():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        from Apollo.swing_simulation import run_simulation, run_simulation_from_latest_homework
        if payload.get("ticker") and payload.get("entry_zone"):
            result = run_simulation(payload, lookback_days=int(payload.get("lookback_days") or 90), save=True)
        else:
            result = run_simulation_from_latest_homework(save=True)
        if result is None:
            return jsonify({"ok": False, "error": "no_candidate"}), 404
        resp = {k: v for k, v in result.items() if k != "trace"}
        return jsonify({"ok": True, "simulation": resp})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/latest", methods=["GET"])
def api_latest():
    try:
        from Apollo.swing_simulation import get_latest_simulation
        sim = get_latest_simulation()
        if sim is None:
            return jsonify({"ok": False, "error": "no_simulation_yet"}), 404
        resp = {k: v for k, v in sim.items() if k != "trace"}
        return jsonify({"ok": True, "simulation": resp})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/list", methods=["GET"])
def api_list():
    try:
        from Apollo.swing_simulation import list_simulations
        sims = list_simulations(limit=50)
        return jsonify({"ok": True, "simulations": [
            {k: v for k, v in s.items() if k not in ("trace", "candles")}
            for s in sims
        ]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/live/<ticker>", methods=["GET"])
def api_live(ticker: str):
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
        hist = t.history(period="5d")
        candles = []
        for ts, row in hist.iterrows():
            candles.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        live_price = candles[-1]["close"] if candles else None
        return jsonify({"ok": True, "ticker": ticker.upper(), "live_price": live_price, "candles": candles})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/prepare", methods=["POST"])
def api_prepare():
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        payload = json.loads(request.get_data(as_text=True)) if request.data else request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        return jsonify(simulation_execution.create_simulation_execution_plan(payload)), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution", methods=["GET"])
def api_execution_list():
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        limit_raw = request.args.get("limit", "50")
        try:
            limit = int(limit_raw)
        except Exception:
            limit = 50
        return jsonify(simulation_execution.list_simulation_execution_plans(limit=limit)), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>", methods=["GET"])
def api_execution_get(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        plan = simulation_execution.get_simulation_execution_plan(execution_id)
        if not plan:
            return jsonify({"ok": False, "error": "plan_not_found"}), 404
        return jsonify({"ok": True, "execution_plan": plan}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>/approve", methods=["POST"])
def api_execution_approve(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        payload = request.get_json(force=True, silent=True) or {}
        context = _simulation_request_context()
        approved_by = payload.get("approved_by", "")
        if not approved_by and context.get("token_hint"):
            approved_by = f"token:{context['token_hint']}"
        result = simulation_execution.approve_simulation_execution_plan(
            execution_id=execution_id,
            approved_by=approved_by,
            approval_notes=payload.get("approval_notes", ""),
            approved_from=context.get("remote_ip", ""),
            approved_client=context.get("user_agent", ""),
            approval_source="api",
        )
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>/submit", methods=["POST"])
def api_execution_submit(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        payload = request.get_json(force=True, silent=True) or {}
        context = _simulation_request_context()
        submitted_by = payload.get("submitted_by", "")
        if not submitted_by and context.get("token_hint"):
            submitted_by = f"token:{context['token_hint']}"
        force_without_approval = str(payload.get("force_without_approval", "false")).strip().lower() in {
            "1", "true", "yes", "on", "y", "t"
        }
        dry_run = payload.get("dry_run")
        if isinstance(dry_run, str):
            dry_run = dry_run.strip().lower() in {"1", "true", "yes", "on", "y", "t"}
        result = simulation_execution.submit_simulation_execution_plan(
            execution_id=execution_id,
            force_without_approval=force_without_approval,
            dry_run=dry_run,
            executor=payload.get("executor"),
            submitted_by=submitted_by,
            submitted_from=context.get("remote_ip", ""),
            submitted_client=context.get("user_agent", ""),
            submission_source=payload.get("submission_source", "api"),
        )
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/<execution_id>/status", methods=["POST"])
def api_execution_status(execution_id: str):
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        result = simulation_execution.sync_simulation_execution_plan_with_broker(execution_id=execution_id)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/simulation/execution/reconcile", methods=["POST"])
def api_execution_reconcile():
    auth = _require_local_token()
    if auth is not None:
        return auth
    try:
        from Apollo import simulation_execution
        payload = request.get_json(force=True, silent=True) or {}
        context = _simulation_request_context()
        requested_by = str(payload.get("requested_by", "") or "").strip()
        if not requested_by and context.get("token_hint"):
            requested_by = f"token:{context['token_hint']}"

        max_plans = payload.get("max_plans")
        try:
            max_plans_int = int(max_plans) if max_plans is not None else None
        except Exception:
            max_plans_int = None

        result = simulation_execution.reconcile_simulation_execution_orders(
            max_plans=max_plans_int,
            requested_by=requested_by,
            requested_from=context.get("remote_ip", ""),
            requested_client=context.get("user_agent", ""),
            source=str(payload.get("source") or "api"),
        )
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/homework/latest", methods=["GET"])
def api_homework():
    try:
        from Apollo.homework_trade_pipeline import RUNS_ROOT
        latest_path = RUNS_ROOT / "latest_homework_trade.json"
        if not latest_path.exists():
            return jsonify({"ok": False, "error": "no_homework_run_yet"}), 404
        data = json.loads(latest_path.read_text(encoding="utf-8"))
        plan = data.get("trade_plan") or {}
        study = data.get("swing_study") or {}
        proposals = [
            {k: v for k, v in p.items() if k not in ("source_links",)}
            for p in list(study.get("proposals") or [])[:10]
        ]
        return jsonify({
            "ok": True,
            "run_id": data.get("run_id"),
            "created_at": data.get("created_at"),
            "trade_plan": plan,
            "proposals": proposals,
            "market_regime": study.get("market_regime"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "apollo-simulation", "port": _PORT})


if __name__ == "__main__":
    print(f"Apollo Simulation Dashboard — http://localhost:{_PORT}/simulation")
    app.run(host="0.0.0.0", port=_PORT, debug=False, threaded=True, use_reloader=False)
