from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

import requests


FAST_LLM_BASE_URL = "http://127.0.0.1:11434"
FAST_LLM_MODEL = "gemma3:12b"
DEEP_TRADE_BASE_URL = "http://127.0.0.1:11434"
DEEP_TRADE_MODEL = "qwen2.5:72b"
DEEP_TRADE_FALLBACKS = [
    "gemma-3-27b-it-Q4_K_M:latest",
    "gemma-4-26b-a4b-q4km-ctx8:latest",
    "gemma3:12b",
]
VISION_OCR_BASE_URL = "http://127.0.0.1:11434"
VISION_OCR_MODEL = "qwen3-vl:32b"
MODEL_STORAGE_ROOT = r"D:\ApolloModels"

_STORAGE_ENV_KEYS = [
    "OLLAMA_MODELS",
    "HF_HOME",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
    "APOLLO_OCR_MODEL_CACHE_DIR",
    "APOLLO_MODEL_STORAGE_ROOT",
]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _model_norm(name: str) -> str:
    value = str(name or "").strip().lower()
    if value.endswith(":latest"):
        return value[:-7]
    return value


def _split_models(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def ollama_installed_models(base_url: str, timeout_sec: int = 6) -> List[str]:
    target = str(base_url or "").strip().rstrip("/") + "/api/tags"
    try:
        resp = requests.get(target, timeout=(3, max(3, int(timeout_sec))))
        if not resp.ok:
            return []
        payload = resp.json() if resp.content else {}
        rows = payload.get("models") if isinstance(payload, dict) else []
        out: List[str] = []
        if isinstance(rows, list):
            for row in rows:
                name = str((row or {}).get("name") if isinstance(row, dict) else row or "").strip()
                if name:
                    out.append(name)
        return out
    except Exception:
        return []


def _choose_installed(requested: str, fallbacks: Iterable[str], installed: Iterable[str]) -> Dict[str, Any]:
    installed_list = [str(item).strip() for item in installed if str(item).strip()]
    norm_map = {_model_norm(name): name for name in installed_list}
    candidate_order = [str(requested or "").strip()] + [str(item or "").strip() for item in fallbacks]
    for candidate in candidate_order:
        norm = _model_norm(candidate)
        if norm and norm in norm_map:
            return {
                "model": norm_map[norm],
                "requested_model": requested,
                "fallback_used": norm != _model_norm(requested),
                "available": True,
                "installed_models": installed_list,
            }
    return {
        "model": str(requested or "").strip(),
        "requested_model": requested,
        "fallback_used": False,
        "available": False,
        "installed_models": installed_list,
    }


def resolve_model_policy(*, probe: bool = True, environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    env = environ or os.environ
    fast_base = str(env.get("APOLLO_FAST_LLM_BASE_URL") or FAST_LLM_BASE_URL).strip().rstrip("/")
    fast_model = str(env.get("APOLLO_FAST_LLM_MODEL") or FAST_LLM_MODEL).strip()
    deep_base = str(env.get("APOLLO_TRADE_REASON_LLM_BASE_URL") or DEEP_TRADE_BASE_URL).strip().rstrip("/")
    deep_requested = str(env.get("APOLLO_TRADE_REASON_LLM_MODEL") or DEEP_TRADE_MODEL).strip()
    deep_fallbacks = _split_models(
        str(env.get("APOLLO_TRADE_REASON_LLM_FALLBACKS") or ",".join(DEEP_TRADE_FALLBACKS))
    )
    vision_base = str(env.get("APOLLO_GB10_QWEN_OLLAMA_URL") or env.get("VISION_OLLAMA_URL") or VISION_OCR_BASE_URL).strip().rstrip("/")
    vision_model = str(env.get("APOLLO_GB10_QWEN_OCR_MODEL") or env.get("VISION_QWEN3VL_MODEL") or VISION_OCR_MODEL).strip()

    deep_installed = ollama_installed_models(deep_base) if probe else []
    deep = _choose_installed(deep_requested, deep_fallbacks, deep_installed) if probe else {
        "model": deep_requested,
        "requested_model": deep_requested,
        "fallback_used": False,
        "available": True,
        "installed_models": [],
    }

    return {
        "fast_model": {"base_url": fast_base, "model": fast_model},
        "deep_trade_model": {
            "base_url": deep_base,
            "model": deep["model"],
            "requested_model": deep["requested_model"],
            "fallbacks": deep_fallbacks,
            "fallback_used": bool(deep["fallback_used"]),
            "available": bool(deep["available"]),
            "installed_models": list(deep.get("installed_models") or []),
        },
        "vision_ocr_model": {"base_url": vision_base, "model": vision_model},
    }


def default_storage_paths(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = environ or os.environ
    root = str(env.get("APOLLO_MODEL_STORAGE_ROOT") or MODEL_STORAGE_ROOT).strip() or MODEL_STORAGE_ROOT
    root_path = Path(root)
    return {
        "APOLLO_MODEL_STORAGE_ROOT": str(root_path),
        "OLLAMA_MODELS": str(root_path / "ollama"),
        "HF_HOME": str(root_path / "huggingface"),
        "TRANSFORMERS_CACHE": str(root_path / "huggingface" / "transformers"),
        "TORCH_HOME": str(root_path / "torch"),
        "APOLLO_OCR_MODEL_CACHE_DIR": str(root_path / "ocr"),
    }


def configure_model_storage_env(environ: Optional[MutableMapping[str, str]] = None) -> Dict[str, str]:
    env = environ if environ is not None else os.environ
    defaults = default_storage_paths(env)
    for key, value in defaults.items():
        env.setdefault(key, value)
    return {key: str(env.get(key) or "") for key in defaults}


def _path_is_c_drive(path: Path) -> bool:
    return str(path.drive or "").upper() == "C:"


def _path_allowed(path: Path) -> bool:
    drive = str(path.drive or "").upper()
    if drive == "D:":
        return True
    marker = str(path).replace("\\", "/").lower()
    return "gb10" in marker or "/apollo_models" in marker or "/apollomodels" in marker


def validate_model_storage(
    *,
    environ: Optional[Mapping[str, str]] = None,
    extra_paths: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    env = environ or os.environ
    allow_c = _truthy(env.get("APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS", "0"))
    resolved = default_storage_paths(env)
    for key in _STORAGE_ENV_KEYS:
        raw = str(env.get(key) or "").strip()
        if raw:
            resolved[key] = raw
    for key, value in (extra_paths or {}).items():
        if str(value or "").strip():
            resolved[key] = str(value).strip()

    blocked: Dict[str, str] = {}
    warnings: Dict[str, str] = {}
    for key, raw in resolved.items():
        path = Path(str(raw)).expanduser()
        if _path_is_c_drive(path) and not allow_c:
            blocked[key] = str(path)
        elif not _path_allowed(path):
            warnings[key] = str(path)

    return {
        "ok": not blocked,
        "resolved_paths": {key: str(Path(value).expanduser()) for key, value in resolved.items()},
        "blocked_c_drive_paths": blocked,
        "non_preferred_paths": warnings,
        "c_drive_downloads_allowed": allow_c,
        "required_storage": "D: drive or GB10-backed storage",
    }


def assert_model_storage_allowed(
    *,
    environ: Optional[MutableMapping[str, str]] = None,
    extra_paths: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    env = environ if environ is not None else os.environ
    configure_model_storage_env(env)
    result = validate_model_storage(environ=env, extra_paths=extra_paths)
    if not result.get("ok"):
        blocked = result.get("blocked_c_drive_paths") or {}
        raise RuntimeError(f"model_storage_c_drive_blocked:{blocked}")
    return result
