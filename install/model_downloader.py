"""Download HuggingFace model weights for the detected tier."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_INSTALL_DIR = Path(__file__).resolve().parent
_APOLLO_ROOT = _INSTALL_DIR.parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent
if str(_ENGINEERING_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINEERING_ROOT))


# Per-tier model download list.
# Format: (hf_model_id, local_dir_name, needs_trocr_convert)
_TIER_MODELS: dict[str, list[tuple[str, str, bool]]] = {
    "tier1": [
        ("google/deplot",                        "google--deplot",                   False),
        ("microsoft/trocr-large-handwritten",    "trocr-large-handwritten",          True),
        ("microsoft/table-transformer-detection","table-transformer-detection",       False),
        ("ProsusAI/finbert",                     "finbert",                          False),
        ("ucaslcl/GOT-OCR2_0",                   "GOT-OCR2_0",                       False),
    ],
    "tier2": [
        ("google/deplot",                        "google--deplot",                   False),
        ("microsoft/trocr-large-handwritten",    "trocr-large-handwritten",          True),
        ("microsoft/table-transformer-detection","table-transformer-detection",       False),
        ("ProsusAI/finbert",                     "finbert",                          False),
        ("ucaslcl/GOT-OCR2_0",                   "GOT-OCR2_0",                       False),
    ],
    "tier2_lite": [
        ("google/deplot",                        "google--deplot",                   False),
        ("microsoft/trocr-large-handwritten",    "trocr-large-handwritten",          True),
        ("microsoft/table-transformer-detection","table-transformer-detection",       False),
        ("ProsusAI/finbert",                     "finbert",                          False),
        ("ucaslcl/GOT-OCR2_0",                   "GOT-OCR2_0",                       False),
    ],
    "tier3": [
        ("google/deplot",                        "google--deplot",                   False),
        ("microsoft/trocr-large-handwritten",    "trocr-large-handwritten",          True),
        ("microsoft/table-transformer-detection","table-transformer-detection",       False),
        ("ProsusAI/finbert",                     "finbert",                          False),
        ("ucaslcl/GOT-OCR2_0",                   "GOT-OCR2_0",                       False),
    ],
}


def _hf_home(models_root: Path) -> Path:
    env = os.getenv("HF_HOME")
    if env:
        return Path(env)
    return models_root / "huggingface"


def download_tier(
    tier: str,
    models_root: Path,
    *,
    verbose: bool = True,
) -> dict[str, Path]:
    """Download all models for the given tier. Returns {model_id: local_path}."""
    from Apollo.model_policy import assert_model_storage_allowed, configure_model_storage_env
    from huggingface_hub import snapshot_download

    configure_model_storage_env(os.environ)
    assert_model_storage_allowed(extra_paths={"models_root": str(models_root)})
    hf_home = _hf_home(models_root)
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    models = _TIER_MODELS.get(tier, _TIER_MODELS["tier3"])
    results: dict[str, Path] = {}

    for model_id, dir_name, needs_convert in models:
        local_dir = models_root / dir_name
        if verbose:
            print(f"  [{model_id}] downloading → {local_dir} ...")
        try:
            path = snapshot_download(
                model_id,
                local_dir=str(local_dir),
                ignore_patterns=["*.msgpack", "flax_model*", "tf_model*", "rust_model*"],
            )
            results[model_id] = Path(path)
            if verbose:
                print(f"  [{model_id}] done")
        except Exception as exc:
            print(f"  [{model_id}] FAILED: {exc}")
            continue

        if needs_convert:
            try:
                from trocr_convert import convert
                convert(Path(path), verbose=verbose)
            except Exception as exc:
                print(f"  [{model_id}] safetensors conversion failed: {exc}")

    return results
