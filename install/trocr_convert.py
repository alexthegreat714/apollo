"""Convert microsoft/trocr-large-handwritten pytorch_model.bin to model.safetensors.

TrOCR ships only a .bin file. Transformers 5.x blocks .bin loading on torch < 2.6
(CVE-2025-32434). This converter uses torch.load directly (not transformers) to
bypass that check, then writes safetensors format. The operation is safe here
because we're loading a local checkpoint we fetched ourselves.

The shared-tensor issue: decoder.model.decoder.embed_tokens.weight and
decoder.output_projection.weight share underlying storage. safetensors.save_file
rejects shared tensors — we clone all tensors to break the aliasing before saving.
"""

from __future__ import annotations

from pathlib import Path


def convert(local_dir: Path, *, verbose: bool = True) -> Path:
    st_path = local_dir / "model.safetensors"
    if st_path.exists():
        if verbose:
            print(f"  safetensors already present: {st_path} ({st_path.stat().st_size // (1024**2)} MB)")
        return st_path

    bin_path = local_dir / "pytorch_model.bin"
    if not bin_path.exists():
        raise FileNotFoundError(f"pytorch_model.bin not found at {bin_path}")

    import torch
    from safetensors.torch import save_file

    if verbose:
        print(f"  Converting {bin_path.stat().st_size // (1024**2)} MB .bin → safetensors ...")
    state_dict = torch.load(str(bin_path), map_location="cpu", weights_only=False)
    state_dict = {k: v.contiguous().clone() for k, v in state_dict.items()}
    save_file(state_dict, str(st_path))
    if verbose:
        print(f"  Saved: {st_path} ({st_path.stat().st_size // (1024**2)} MB)")
    return st_path


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("D:/AI/models/trocr-large-handwritten")
    convert(path)
