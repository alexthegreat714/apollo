from __future__ import annotations

import json
import sys
from pathlib import Path

import Aegis.scripts.bootstrap_gb10_ocr_stack as bootstrap


def _patch_ready(monkeypatch):
    monkeypatch.setattr(
        bootstrap.gw,
        "_paddle_backend_status",
        lambda: {"ready": True, "reason": "worker_ready", "worker_callable": True, "model_assets_present": True},
    )
    monkeypatch.setattr(
        bootstrap.gw,
        "_mineru_backend_status",
        lambda: {"ready": True, "reason": "worker_ready", "worker_callable": True, "model_assets_present": True},
    )
    monkeypatch.setattr(
        bootstrap.gw,
        "_olmocr_backend_status",
        lambda: {"ready": True, "reason": "worker_ready", "worker_callable": True, "model_assets_present": True},
    )


def test_bootstrap_defaults_to_check_only(monkeypatch, tmp_path):
    _patch_ready(monkeypatch)
    output_path = tmp_path / "bootstrap.json"
    monkeypatch.setattr(sys, "argv", ["bootstrap_gb10_ocr_stack.py", "--output", str(output_path)])
    code = bootstrap.main()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["check_only"] is True
    assert payload["install_requested"] is False
    assert payload["install_action"]["mode"] == "check_only"


def test_bootstrap_ci_fails_on_dependency_conflict(monkeypatch, tmp_path):
    _patch_ready(monkeypatch)
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("rich==0.0.1\n", encoding="utf-8")
    output_path = tmp_path / "bootstrap_ci.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap_gb10_ocr_stack.py",
            "--ci",
            "--constraints-file",
            str(constraints),
            "--output",
            str(output_path),
        ],
    )
    code = bootstrap.main()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert code == 2
    assert payload["critical_version_conflicts"]


def test_bootstrap_does_not_install_without_install_flag(monkeypatch, tmp_path):
    _patch_ready(monkeypatch)
    called = {"install": 0}

    def fake_install(req_path: Path, cons_path: Path):
        called["install"] += 1
        return {"ok": True}

    monkeypatch.setattr(bootstrap, "_pip_install_requirements", fake_install)
    output_path = tmp_path / "bootstrap_check.json"
    monkeypatch.setattr(sys, "argv", ["bootstrap_gb10_ocr_stack.py", "--output", str(output_path)])
    code = bootstrap.main()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert code == 0
    assert called["install"] == 0
    assert payload["install_action"]["mode"] == "check_only"
