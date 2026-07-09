"""Pull Ollama models for the detected tier and verify they loaded."""

from __future__ import annotations

import shutil
import subprocess
import time
import os
import sys
from pathlib import Path
from typing import Optional

_INSTALL_DIR = Path(__file__).resolve().parent
_APOLLO_ROOT = _INSTALL_DIR.parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent
if str(_ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINEERING_ROOT))


_TIER_MODELS: dict[str, list[str]] = {
    "tier1":      ["qwen3-vl:32b", "qwen2.5vl:7b"],
    "tier2":      ["qwen2.5vl:7b", "qwen2.5vl:3b"],
    "tier2_lite": ["qwen2.5vl:3b"],
    "tier3":      [],
}

# Models that need a dedicated second Ollama instance (port 11435 for OCR isolation)
_OCR_DEDICATED: set[str] = {"qwen3-vl:32b", "qwen2.5vl:7b", "qwen2.5vl:3b"}


def _ollama_available() -> bool:
    return bool(shutil.which("ollama"))


def _model_present(model: str, ollama_url: str = "http://127.0.0.1:11434") -> bool:
    try:
        import requests
        r = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return any(model == n or model.split(":")[0] == n.split(":")[0] for n in names)
    except Exception:
        return False


def _pull(model: str, *, verbose: bool = True) -> bool:
    from Apollo.model_policy import assert_model_storage_allowed, configure_model_storage_env
    configure_model_storage_env(os.environ)
    assert_model_storage_allowed()
    if verbose:
        print(f"  ollama pull {model} ...")
    try:
        subprocess.run(["ollama", "pull", model], check=True, timeout=3600)
        return True
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return False


def pull_tier(
    tier: str,
    *,
    verbose: bool = True,
    skip_if_present: bool = True,
) -> dict[str, bool]:
    """Pull all Ollama models for the tier. Returns {model: success}."""
    if not _ollama_available():
        if verbose and tier != "tier3":
            print("  ollama not found — skipping model pulls")
        return {}

    models = _TIER_MODELS.get(tier, [])
    if not models:
        if verbose:
            print("  tier3: no Ollama models required")
        return {}

    results: dict[str, bool] = {}
    for model in models:
        if skip_if_present and _model_present(model):
            if verbose:
                print(f"  {model} already present — skipping pull")
            results[model] = True
            continue
        results[model] = _pull(model, verbose=verbose)

    return results


def verify_running(
    model: str,
    ollama_url: str = "http://127.0.0.1:11434",
    *,
    timeout_sec: int = 30,
    verbose: bool = True,
) -> bool:
    """
    Send a minimal generate request to confirm the model actually runs
    (not just that it's listed). Times out after timeout_sec.
    """
    try:
        import requests
        payload = {
            "model": model,
            "prompt": "1+1=",
            "stream": False,
            "options": {"num_predict": 4},
        }
        r = requests.post(
            f"{ollama_url}/api/generate",
            json=payload,
            timeout=timeout_sec,
        )
        ok = r.status_code == 200 and bool(r.json().get("response"))
        if verbose:
            status = "OK" if ok else f"FAIL (status {r.status_code})"
            print(f"  {model} generate smoke-test: {status}")
        return ok
    except Exception as exc:
        if verbose:
            print(f"  {model} generate smoke-test: FAIL ({exc})")
        return False
