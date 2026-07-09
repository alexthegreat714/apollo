"""Write a .env file from the tier template, merging with any existing values."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(tier: str) -> dict[str, str]:
    path = _TEMPLATES_DIR / f"env-{tier}.template"
    if not path.exists():
        raise FileNotFoundError(f"No env template for tier '{tier}': {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def _load_existing(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def write(
    tier: str,
    env_path: Path,
    overrides: Optional[dict[str, str]] = None,
    *,
    trocr_local_path: Optional[str] = None,
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_ocr_url: str = "http://127.0.0.1:11435",
    primary_model: Optional[str] = None,
) -> dict[str, str]:
    """
    Build the .env from template + caller overrides, write it, return the final dict.
    Existing values NOT in the template are preserved.
    Template values take precedence over existing — the installer is authoritative
    for OCR/model config; user-specific keys (API keys, ports, etc.) are kept.
    """
    template_vals = _load_template(tier)
    existing_vals = _load_existing(env_path)

    # Start from existing, apply template on top for OCR-owned keys
    merged = {**existing_vals, **template_vals}

    # Apply dynamic values
    if primary_model:
        merged["APOLLO_GB10_QWEN_OCR_MODEL"] = primary_model
    if ollama_url:
        merged["OLLAMA_URL"] = ollama_url
        merged["OLLAMA_URL_CHAT"] = ollama_url
    if ollama_ocr_url and tier != "tier3":
        merged["APOLLO_GB10_QWEN_OLLAMA_URL"] = ollama_ocr_url
    if trocr_local_path:
        merged["AEGIS_TROCR_MODEL_ID"] = trocr_local_path

    # Caller overrides (highest priority)
    if overrides:
        merged.update(overrides)

    # Write file with section headers preserved from template
    template_text = (_TEMPLATES_DIR / f"env-{tier}.template").read_text(encoding="utf-8")
    existing_keys_not_in_template = {
        k: v for k, v in existing_vals.items() if k not in template_vals
    }

    lines = []
    written_keys: set[str] = set()
    for line in template_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            lines.append(f"{key}={merged.get(key, template_vals.get(key, ''))}")
            written_keys.add(key)
        else:
            lines.append(line)

    if existing_keys_not_in_template:
        lines.append("")
        lines.append("# --- preserved from previous installation ---")
        for k, v in existing_keys_not_in_template.items():
            if k not in written_keys:
                lines.append(f"{k}={v}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Written: {env_path}")
    return merged
