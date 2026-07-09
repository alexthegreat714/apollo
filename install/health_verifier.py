"""Post-install health checks for each OCR component by tier."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_GRADE_ESTIMATES = {
    "tier1":      "97",
    "tier2":      "88–91",
    "tier2_lite": "80–84",
    "tier3":      "55–62",
}


@dataclass
class ComponentResult:
    name: str
    status: str        # ok / fail / skip
    reason: str = ""
    backend: str = ""
    note: str = ""


def _check_transformers_model(
    name: str,
    loader_fn_name: str,
    env_overrides: dict,
    engineering_root: Path,
) -> ComponentResult:
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.path.insert(0, str(engineering_root))
    try:
        from common.ocr_local_inference import (
            load_deplot_runtime,
            load_got_runtime,
            load_trocr_runtime,
        )
        fn_map = {
            "load_deplot_runtime": load_deplot_runtime,
            "load_got_runtime": load_got_runtime,
            "load_trocr_runtime": load_trocr_runtime,
        }
        fn = fn_map.get(loader_fn_name)
        if fn is None:
            return ComponentResult(name, "fail", reason=f"unknown loader: {loader_fn_name}")
        result = fn()
        if result.get("ok"):
            return ComponentResult(
                name, "ok",
                backend=result.get("backend", ""),
                reason=result.get("model_id", ""),
            )
        return ComponentResult(name, "fail", reason=result.get("error", "unknown"))
    except Exception as exc:
        return ComponentResult(name, "fail", reason=str(exc))


def _check_ollama(model: str, ollama_url: str) -> ComponentResult:
    try:
        import requests
        r = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if r.status_code != 200:
            return ComponentResult(model, "fail", reason=f"ollama tags HTTP {r.status_code}")
        names = [m.get("name", "") for m in r.json().get("models", [])]
        if any(model == n or model.split(":")[0] in n for n in names):
            return ComponentResult(model, "ok", backend="ollama", reason=f"listed at {ollama_url}")
        return ComponentResult(model, "fail", reason=f"model not found in ollama tags at {ollama_url}")
    except Exception as exc:
        return ComponentResult(model, "fail", reason=str(exc))


def _check_finbert(engineering_root: Path) -> ComponentResult:
    sys.path.insert(0, str(engineering_root))
    try:
        from common.ocr_local_inference import load_finbert_runtime  # type: ignore
        result = load_finbert_runtime()
        if result.get("ok"):
            return ComponentResult("finbert", "ok", backend=result.get("backend", ""))
        return ComponentResult("finbert", "fail", reason=result.get("error", "unknown"))
    except Exception as exc:
        return ComponentResult("finbert", "fail", reason=str(exc))


def run(
    tier: str,
    engineering_root: Path,
    env_path: Path,
    *,
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_ocr_url: str = "http://127.0.0.1:11435",
    trocr_local_path: Optional[str] = None,
) -> list[ComponentResult]:
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    # Load .env so loader functions pick up correct model paths
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

    results: list[ComponentResult] = []

    # --- DePlot ---
    env_ov = {"AEGIS_DEPLOT_ENABLE": "1"}
    results.append(_check_transformers_model("deplot", "load_deplot_runtime", env_ov, engineering_root))

    # --- TrOCR ---
    trocr_env = {"AEGIS_TROCR_ENABLE": "1"}
    if trocr_local_path:
        trocr_env["AEGIS_TROCR_MODEL_ID"] = trocr_local_path
    results.append(_check_transformers_model("trocr", "load_trocr_runtime", trocr_env, engineering_root))

    # --- GOT-OCR2.0 ---
    results.append(_check_transformers_model("got_ocr2", "load_got_runtime", {}, engineering_root))

    # --- FinBERT ---
    results.append(_check_finbert(engineering_root))

    # --- Ollama models (tier 1/2/2_lite) ---
    if tier == "tier1":
        results.append(_check_ollama("qwen3-vl:32b", ollama_ocr_url))
        results.append(_check_ollama("qwen2.5vl:7b", ollama_url))
    elif tier == "tier2":
        results.append(_check_ollama("qwen2.5vl:7b", ollama_url))
    elif tier == "tier2_lite":
        results.append(_check_ollama("qwen2.5vl:3b", ollama_url))

    return results


def print_report(results: list[ComponentResult], tier: str) -> bool:
    all_ok = True
    status_icons = {"ok": "[OK]  ", "fail": "[FAIL]", "skip": "[SKIP]"}
    print()
    print("  Component health")
    print("  ─────────────────────────────────────────────────────")
    for r in results:
        icon = status_icons.get(r.status, "[????]")
        parts = [f"  {icon}  {r.name:<28}"]
        if r.backend:
            parts.append(f"backend: {r.backend}")
        if r.reason:
            parts.append(r.reason)
        if r.note:
            parts.append(f"({r.note})")
        print("  ".join(parts))
        if r.status == "fail":
            all_ok = False
    print()
    grade = _GRADE_ESTIMATES.get(tier, "?")
    print(f"  Estimated OCR97 grade for this build:  ~{grade} / 97")
    print()
    return all_ok
