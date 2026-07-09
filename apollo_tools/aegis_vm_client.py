from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin

import requests

try:
    from common.agent_auth import sign_interagent_headers
except Exception:  # pragma: no cover - optional shared auth helper
    sign_interagent_headers = None  # type: ignore[assignment]


DEFAULT_AEGIS_BASE_URL = "http://127.0.0.1:5221"
_TOKEN_KEYS = ("APOLLO_AEGIS_TOKEN", "AEGIS_LOCAL_TOKEN", "SKY_LOCAL_TOKEN", "APOLLO_LOCAL_TOKEN")


def aegis_base_url() -> str:
    return str(
        os.getenv("APOLLO_AEGIS_BASE_URL")
        or os.getenv("AEGIS_BASE_URL")
        or os.getenv("SKY_AEGIS_LOCAL_URL")
        or DEFAULT_AEGIS_BASE_URL
    ).strip().rstrip("/")


def _local_token() -> str:
    for key in _TOKEN_KEYS:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    for value in _dotenv_token_candidates():
        if value:
            return value
    return ""


def _dotenv_token_candidates() -> list[str]:
    engineering_root = Path(__file__).resolve().parents[2]
    paths = (
        engineering_root / "Apollo" / ".env",
        engineering_root / "Aegis" / ".env",
        engineering_root / "Sky" / ".env",
        engineering_root / ".env",
    )
    candidates: list[str] = []
    for path in paths:
        try:
            if not path.exists():
                continue
            rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        values: dict[str, str] = {}
        for row in rows:
            text = row.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, raw_value = text.split("=", 1)
            key = key.strip()
            if key not in _TOKEN_KEYS:
                continue
            values[key] = raw_value.strip().strip('"').strip("'")
        for key in _TOKEN_KEYS:
            value = values.get(key, "").strip()
            if value:
                candidates.append(value)
    return candidates


def _headers(method: str, path: str, body: bytes = b"") -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json", "X-Agent-Name": "Apollo"}
    token = _local_token()
    if token:
        headers["X-Aegis-Token"] = token
        headers["X-Apollo-Token"] = token
        headers["Authorization"] = f"Bearer {token}"
    if sign_interagent_headers is not None:
        try:
            headers.update(sign_interagent_headers("Apollo", method, path, body))
        except Exception:
            pass
    return headers


def _request(method: str, path: str, payload: Dict[str, Any] | None = None, timeout_s: float = 10.0) -> Dict[str, Any]:
    normalized_path = "/" + str(path or "").lstrip("/")
    signature_path = normalized_path.split("?", 1)[0]
    body = json.dumps(payload or {}, ensure_ascii=True).encode("utf-8") if method.upper() != "GET" else b""
    started = time.perf_counter()
    url = urljoin(aegis_base_url() + "/", normalized_path.lstrip("/"))
    try:
        response = requests.request(
            method.upper(),
            url,
            data=body if body else None,
            headers=_headers(method.upper(), signature_path, body),
            timeout=max(1.0, min(float(timeout_s or 10.0), 60.0)),
        )
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:1000]}
        if not isinstance(data, dict):
            data = {"raw": data}
        data.setdefault("ok", bool(response.ok and data.get("ok", True)))
        data["http_status"] = int(response.status_code)
        data["aegis_url"] = url
        data["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return data
    except Exception as exc:
        return {
            "ok": False,
            "error": "aegis_vm_request_failed",
            "detail": str(exc)[:400],
            "aegis_url": url,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }


def vm_status(timeout_s: float = 8.0) -> Dict[str, Any]:
    return _request("GET", "/forgeclaw/vm/status?cache=1", timeout_s=timeout_s)


def vm_exec(command: str, *, title: str = "Apollo Aegis VM exec", timeout_s: float = 15.0) -> Dict[str, Any]:
    return _request(
        "POST",
        "/forgeclaw/vm/exec",
        {
            "command": str(command or ""),
            "title": title,
            "timeout_s": max(1.0, min(float(timeout_s or 15.0), 60.0)),
            "cleanup_policy": "auto_close",
            "owned_window_patterns": ["Apollo Aegis VM"],
        },
        timeout_s=max(2.0, min(float(timeout_s or 15.0) + 5.0, 70.0)),
    )


def vm_probe(timeout_s: float = 15.0) -> Dict[str, Any]:
    command = (
        "printf 'apollo-aegis-vm-ok\\n'; "
        "uname -a 2>/dev/null || true; "
        "python3 --version 2>/dev/null || python --version 2>/dev/null || true; "
        "curl -Is --max-time 5 https://api.ebay.com/ 2>/dev/null | head -n 1 || true"
    )
    return vm_exec(command, title="Apollo Aegis VM probe", timeout_s=timeout_s)


def vm_screenshot(
    *,
    target: str = "aegis_tab",
    timeout_s: float = 15.0,
    overlay_cursor: bool = True,
) -> Dict[str, Any]:
    payload = {
        "target": str(target or "aegis_tab").strip() or "aegis_tab",
        "timeout_s": max(1.0, min(float(timeout_s or 15.0), 60.0)),
    }
    if overlay_cursor:
        payload["overlay_cursor"] = True
    return _request("POST", "/forgeclaw/vm/screenshot", payload, timeout_s=max(2.0, min(float(timeout_s or 15.0) + 5.0, 70.0)))
