"""Hardware detection and tier classification for the Apollo OCR installer."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HardwareProfile:
    vram_gb: int
    ram_gb: int
    unified_gb: int          # Apple Silicon: RAM that is also GPU-accessible
    cpu_cores: int
    gpu_name: str
    os: str
    ollama_available: bool
    cuda_version: Optional[str]
    tier: str                # tier1 / tier2 / tier2_lite / tier3
    primary_model: Optional[str]
    notes: list[str] = field(default_factory=list)


def _nvidia_vram_gb() -> tuple[int, str, Optional[str]]:
    """Returns (vram_gb, gpu_name, cuda_version). 0 / '' / None if no NVIDIA GPU."""
    if not shutil.which("nvidia-smi"):
        return 0, "", None
    try:
        mem = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=10
        ).strip().splitlines()
        name_out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True, timeout=10
        ).strip().splitlines()
        cuda_out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            text=True, timeout=10
        ).strip()
        vram_gb = max(int(v.strip()) for v in mem if v.strip()) // 1024
        gpu_name = name_out[0].strip() if name_out else "NVIDIA GPU"
        return vram_gb, gpu_name, cuda_out.strip() or None
    except Exception:
        return 0, "", None


def _apple_unified_memory_gb() -> int:
    """Returns unified memory GB for Apple Silicon; 0 on other platforms."""
    if platform.system() != "Darwin":
        return 0
    if platform.processor() != "arm":
        return 0
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], text=True, timeout=5
        )
        return int(out.strip()) // (1024 ** 3)
    except Exception:
        return 0


def _ram_gb() -> int:
    try:
        import psutil
        return psutil.virtual_memory().total // (1024 ** 3)
    except ImportError:
        pass
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                text=True, timeout=10
            )
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line) // (1024 ** 3)
        except Exception:
            pass
    try:
        out = subprocess.check_output(["free", "-g"], text=True, timeout=5)
        for line in out.splitlines():
            if line.startswith("Mem:"):
                return int(line.split()[1])
    except Exception:
        pass
    return 8  # safe fallback


def _cpu_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=False) or 1
    except ImportError:
        import os
        return os.cpu_count() or 1


def _is_ci() -> bool:
    import os
    return os.getenv("CI", "").lower() in ("true", "1", "yes")


def classify_tier(vram_gb: int, unified_gb: int, ram_gb: int) -> tuple[str, Optional[str]]:
    effective_vram = max(vram_gb, unified_gb)

    if effective_vram >= 20 or unified_gb >= 64:
        return "tier1", "qwen3-vl:32b"
    if vram_gb >= 8 and ram_gb >= 16:
        return "tier2", "qwen2.5vl:7b"
    if vram_gb >= 4 or (vram_gb == 0 and ram_gb >= 32):
        return "tier2_lite", "qwen2.5vl:3b"
    return "tier3", None


def detect() -> HardwareProfile:
    vram_gb, gpu_name, cuda_version = _nvidia_vram_gb()
    unified_gb = _apple_unified_memory_gb()
    ram_gb = _ram_gb()
    cpu_cores = _cpu_cores()
    os_name = platform.system()
    ollama_ok = bool(shutil.which("ollama"))

    notes = []
    if _is_ci():
        tier, primary_model = "tier3", None
        notes.append("CI environment detected — forcing tier3")
    else:
        tier, primary_model = classify_tier(vram_gb, unified_gb, ram_gb)

    if unified_gb > 0:
        notes.append(f"Apple Silicon: {unified_gb}GB unified memory counts as effective VRAM")
    if not ollama_ok:
        if tier in ("tier1", "tier2", "tier2_lite"):
            notes.append("ollama not found in PATH — Qwen models will not be pulled")
            if tier == "tier2_lite":
                tier = "tier3"
                primary_model = None
                notes.append("Downgraded to tier3: Qwen required for tier2_lite and ollama is missing")

    return HardwareProfile(
        vram_gb=vram_gb,
        ram_gb=ram_gb,
        unified_gb=unified_gb,
        cpu_cores=cpu_cores,
        gpu_name=gpu_name,
        os=os_name,
        ollama_available=ollama_ok,
        cuda_version=cuda_version,
        tier=tier,
        primary_model=primary_model,
        notes=notes,
    )


def print_report(profile: HardwareProfile) -> None:
    tier_labels = {
        "tier1": "Full stack  (qwen3-vl:32b, all components)",
        "tier2": "Mid-range   (qwen2.5vl:7b, most components)",
        "tier2_lite": "Lite        (qwen2.5vl:3b, reduced ensemble)",
        "tier3": "CPU / CI    (GOT + DePlot + TrOCR + fallbacks, no Qwen)",
    }
    print()
    print("  Detected hardware")
    print(f"  GPU VRAM:   {profile.vram_gb} GB" + (f"  ({profile.gpu_name})" if profile.gpu_name else "  (no GPU detected)"))
    if profile.unified_gb:
        print(f"  Unified:    {profile.unified_gb} GB  (Apple Silicon)")
    print(f"  System RAM: {profile.ram_gb} GB")
    print(f"  CPU cores:  {profile.cpu_cores}")
    print(f"  OS:         {profile.os}")
    print(f"  Ollama:     {'found' if profile.ollama_available else 'not found'}")
    if profile.cuda_version:
        print(f"  CUDA:       driver {profile.cuda_version}")
    print()
    print(f"  Assigned tier:  {profile.tier}")
    print(f"  Profile:        {tier_labels.get(profile.tier, profile.tier)}")
    if profile.primary_model:
        print(f"  Primary model:  {profile.primary_model}")
    for note in profile.notes:
        print(f"  NOTE: {note}")
    print()


if __name__ == "__main__":
    p = detect()
    print_report(p)
    print(f"tier={p.tier}")
