from __future__ import annotations

from Apollo import app as app_mod


def test_apollo_pretrade_routes_expose_status_check_and_report(monkeypatch):
    monkeypatch.setattr(app_mod, "_require_local_token", lambda: None)
    monkeypatch.setattr(app_mod.apollo_pretrade_gate, "latest_status", lambda: {"run_id": "gate_latest", "decision": "clear_for_paper_sim"})
    monkeypatch.setattr(
        app_mod.apollo_pretrade_gate,
        "run_pretrade_gate",
        lambda payload: {"ok": True, "run_id": "gate_api", "decision": "clear_for_paper_sim", "trigger": payload.get("trigger")},
    )
    monkeypatch.setattr(
        app_mod.apollo_daily_report,
        "get_daily_report",
        lambda date=None: {"ok": True, "date": date or "2026-05-14", "markdown": "# Report", "paths": {"markdown": "report.md"}},
    )

    client = app_mod.app.test_client()

    status = client.get("/admin/apollo/pretrade/status")
    assert status.status_code == 200
    assert status.get_json()["status"]["decision"] == "clear_for_paper_sim"

    check = client.post("/admin/apollo/pretrade/check", json={})
    assert check.status_code == 200
    assert check.get_json()["trigger"] == "api"

    report = client.get("/admin/apollo/daily_report")
    assert report.status_code == 200
    assert report.get_json()["markdown"] == "# Report"


def test_apollo_schedule_includes_pretrade_and_daily_report_jobs(monkeypatch):
    monkeypatch.setattr(app_mod, "_require_local_token", lambda: None)
    monkeypatch.setattr(app_mod, "_schedule_collect_runs", lambda root, status_names, limit=20: [])

    client = app_mod.app.test_client()
    response = client.get("/admin/apollo/schedule_runs")
    payload = response.get_json()
    ids = {job["id"] for job in payload["jobs"]}

    assert response.status_code == 200
    assert {"daily_report_open", "pretrade_gate", "daily_report_final"}.issubset(ids)
