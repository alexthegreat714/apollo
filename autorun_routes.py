from flask import Blueprint, request, jsonify
import os, sys, json

APP_DIR = os.path.dirname(__file__)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from auto_run.runner import plan, run

bp = Blueprint("autorun", __name__, url_prefix="/autorun")
LOGS = r"C:\\Users\\blyth\\Desktop\\Engineering\\Aegis\\logs\\auto_run"

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
