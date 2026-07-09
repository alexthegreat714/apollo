from flask import Blueprint, request, jsonify
import os, sys, json

APP_DIR = os.path.dirname(__file__)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

try:
    from auto_run.runner import plan, run  # type: ignore
except Exception:  # pragma: no cover
    def plan():  # type: ignore
        return {"ok": False, "error": "auto_run_unavailable"}

    def run(dry_run: bool = False, force: bool = False):  # type: ignore
        return {"ok": False, "error": "auto_run_unavailable", "dry_run": bool(dry_run), "force": bool(force)}

bp = Blueprint("autorun", __name__, url_prefix="/autorun")
LOGS = os.getenv(
    "APOLLO_AUTORUN_LOG_DIR",
    r"C:\\Users\\blyth\\Desktop\\Engineering\\Apollo\\logs\\auto_run",
)

@bp.route("/plan", methods=["POST"])
def autorun_plan():
    return jsonify(plan())

@bp.route("/execute", methods=["POST"])
def autorun_exec():
    data = request.get_json(silent=True) or {}
    dry = bool(data.get("dry_run", False))
    force = bool(data.get("force", False))
    return jsonify(run(dry_run=dry, force=force))

@bp.route("/streak", methods=["GET"])
def autorun_streak():
    try:
        with open(os.path.join(LOGS, "streak.json"), "r", encoding="utf-8") as f:
            streak = json.load(f)
    except Exception:
        streak = {}
    return jsonify({"streak": streak})
