#!/usr/bin/env python3
"""
Apollo OCR Installer
--------------------
Detects hardware, classifies a build tier, installs Python dependencies,
downloads model weights, pulls Ollama models, writes .env, and runs health checks.

Usage:
    python install/install.py                     # auto-detect + confirm
    python install/install.py --tier tier3        # force a specific tier
    python install/install.py --tier tier3 --no-confirm   # CI mode, no prompts
    python install/install.py --models-root D:/AI/models  # custom model directory
    python install/install.py --help
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_INSTALL_DIR = Path(__file__).resolve().parent
_APOLLO_ROOT = _INSTALL_DIR.parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent

# Default model storage — matches the existing GX10 convention
_DEFAULT_MODELS_ROOT = Path(os.getenv("APOLLO_MODELS_ROOT", "D:/AI/models"))

sys.path.insert(0, str(_INSTALL_DIR))
sys.path.insert(0, str(_ENGINEERING_ROOT))


def _pip_install(requirements_file: Path, *, verbose: bool = True) -> bool:
    if not requirements_file.exists():
        print(f"  WARNING: requirements file not found: {requirements_file}")
        return False
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements_file), "-q"]
    if verbose:
        print(f"  pip install -r {requirements_file.name} ...")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  FAILED: {exc}")
        return False


def _confirm(tier: str, profile) -> bool:
    from detect_hardware import print_report
    print_report(profile)
    answer = input(f"  Install as {tier}? [Y/n/override tier]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return True
    if answer in ("n", "no"):
        print("  Aborted.")
        sys.exit(0)
    # User typed a tier name
    valid = ("tier1", "tier2", "tier2_lite", "tier3")
    if answer in valid:
        return answer  # type: ignore[return-value]
    print(f"  Unknown input '{answer}'. Valid tiers: {valid}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apollo OCR hardware-adaptive installer")
    parser.add_argument("--tier", choices=["tier1", "tier2", "tier2_lite", "tier3"],
                        help="Force a specific tier instead of auto-detecting")
    parser.add_argument("--no-confirm", action="store_true",
                        help="Skip confirmation prompt (CI mode)")
    parser.add_argument("--models-root", type=Path, default=_DEFAULT_MODELS_ROOT,
                        help=f"Root directory for model storage (default: {_DEFAULT_MODELS_ROOT})")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434",
                        help="Ollama base URL")
    parser.add_argument("--ollama-ocr-url", default="http://127.0.0.1:11435",
                        help="Ollama OCR-dedicated instance URL (tier1 only)")
    parser.add_argument("--skip-models", action="store_true",
                        help="Skip HuggingFace model downloads (useful if already present)")
    parser.add_argument("--skip-ollama", action="store_true",
                        help="Skip Ollama model pulls")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip post-install health checks")
    args = parser.parse_args()

    # ── Step 1: detect hardware ──────────────────────────────────────────────
    print("\n[1/6] Detecting hardware ...")
    from detect_hardware import detect, print_report
    profile = detect()

    tier = args.tier or profile.tier
    primary_model = profile.primary_model

    if args.no_confirm or os.getenv("CI"):
        if not args.tier:
            print_report(profile)
        print(f"  Using tier: {tier}  (--no-confirm / CI mode)")
    else:
        result = _confirm(tier, profile)
        if isinstance(result, str) and result != tier:
            tier = result
            print(f"  Override accepted — using tier: {tier}")
            # Reclassify primary model for the overridden tier
            from detect_hardware import classify_tier
            _, primary_model = classify_tier(profile.vram_gb, profile.unified_gb, profile.ram_gb)
            if tier == "tier1":
                primary_model = "qwen3-vl:32b"
            elif tier == "tier2":
                primary_model = "qwen2.5vl:7b"
            elif tier == "tier2_lite":
                primary_model = "qwen2.5vl:3b"
            else:
                primary_model = None

    from Apollo.model_policy import assert_model_storage_allowed, configure_model_storage_env

    configure_model_storage_env(os.environ)
    storage_policy = assert_model_storage_allowed(extra_paths={"models_root": str(args.models_root)})
    print(f"  Model storage policy: {storage_policy['required_storage']}")
    for key, value in (storage_policy.get("resolved_paths") or {}).items():
        print(f"    {key}={value}")

    models_root = args.models_root
    models_root.mkdir(parents=True, exist_ok=True)

    # ── Step 2: install Python dependencies ──────────────────────────────────
    print(f"\n[2/6] Installing Python dependencies (tier: {tier}) ...")
    req_dir = _INSTALL_DIR / "requirements"
    _pip_install(req_dir / "requirements-base.txt")
    _pip_install(req_dir / f"requirements-{tier}.txt")

    # ── Step 3: download HuggingFace models ───────────────────────────────────
    if args.skip_models:
        print("\n[3/6] Skipping HuggingFace model downloads (--skip-models)")
    else:
        print(f"\n[3/6] Downloading HuggingFace models → {models_root} ...")
        from model_downloader import download_tier
        download_tier(tier, models_root)

    # Determine TrOCR local path (converter writes model.safetensors into this dir)
    trocr_local = str(models_root / "trocr-large-handwritten")

    # ── Step 4: pull Ollama models ─────────────────────────────────────────────
    if args.skip_ollama or tier == "tier3":
        if tier == "tier3":
            print("\n[4/6] tier3: no Ollama models needed — skipping")
        else:
            print("\n[4/6] Skipping Ollama pulls (--skip-ollama)")
    else:
        print(f"\n[4/6] Pulling Ollama models for {tier} ...")
        from ollama_puller import pull_tier
        pull_tier(tier)

    # ── Step 5: write .env ─────────────────────────────────────────────────────
    print(f"\n[5/6] Writing .env → {_APOLLO_ROOT / '.env'} ...")
    from env_writer import write as write_env
    write_env(
        tier,
        _APOLLO_ROOT / ".env",
        trocr_local_path=trocr_local,
        ollama_url=args.ollama_url,
        ollama_ocr_url=args.ollama_ocr_url,
        primary_model=primary_model,
    )

    # ── Step 6: health checks ──────────────────────────────────────────────────
    if args.skip_verify:
        print("\n[6/6] Skipping health checks (--skip-verify)")
        print("\n  Installation complete.")
        return

    print(f"\n[6/6] Running health checks ...")
    from health_verifier import run as run_health, print_report as print_health
    results = run_health(
        tier,
        _ENGINEERING_ROOT,
        _APOLLO_ROOT / ".env",
        ollama_url=args.ollama_url,
        ollama_ocr_url=args.ollama_ocr_url,
        trocr_local_path=trocr_local,
    )
    all_ok = print_health(results, tier)

    if all_ok:
        print("  Installation complete — all checks passed.")
    else:
        print("  Installation complete with warnings — review FAIL entries above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
