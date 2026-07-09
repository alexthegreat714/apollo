from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests


APOLLO_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = APOLLO_ROOT / "reports" / "audio_transcripts"
DEFAULT_MODEL = str(os.getenv("APOLLO_LOCAL_STT_MODEL", "tiny.en")).strip() or "tiny.en"
DEFAULT_LANG = str(os.getenv("APOLLO_LOCAL_STT_LANGUAGE", "en")).strip() or "en"
DEFAULT_BACKEND = str(os.getenv("APOLLO_LOCAL_STT_BACKEND", "transformers")).strip().lower() or "transformers"
VOCAROO_ID_RE = re.compile(r"(?i)(?:voca\.ro/|vocaroo\.com/(?:embed/)?)([A-Za-z0-9]+)")
TRANSFORMERS_MODEL_ALIASES = {
    "tiny": "openai/whisper-tiny",
    "tiny.en": "openai/whisper-tiny.en",
    "base": "openai/whisper-base",
    "base.en": "openai/whisper-base.en",
    "small": "openai/whisper-small",
    "small.en": "openai/whisper-small.en",
    "medium": "openai/whisper-medium",
    "medium.en": "openai/whisper-medium.en",
    "large-v3": "openai/whisper-large-v3",
}


@dataclass
class SourceAudio:
    source: str
    local_path: Path
    cleanup: bool = False
    media_url: str = ""


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _slug(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(raw or "").strip()).strip("_").lower()
    return text or "audio"


def _is_url(raw: str) -> bool:
    parsed = urlparse(str(raw or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_vocaroo_id(raw: str) -> str:
    text = str(raw or "").strip()
    match = VOCAROO_ID_RE.search(text)
    if not match:
        raise ValueError(f"unsupported_vocaroo_url:{text}")
    return match.group(1)


def _vocaroo_media_url(vocaroo_id: str, fmt: str = "mp3") -> str:
    clean_id = str(vocaroo_id or "").strip()
    if not clean_id:
        raise ValueError("vocaroo_id_required")
    return f"https://media1.vocaroo.com/{fmt}/{clean_id}"


def _download_file(url: str, target: Path, *, headers: Optional[Dict[str, str]] = None, timeout_sec: int = 60) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=headers or {}, timeout=timeout_sec, stream=True) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return target


def _resolve_source_audio(raw: str, *, work_dir: Path) -> SourceAudio:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("audio_source_required")
    if not _is_url(value):
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"audio_not_found:{path}")
        return SourceAudio(source=value, local_path=path, cleanup=False, media_url="")
    vocaroo_id = _extract_vocaroo_id(value)
    media_url = _vocaroo_media_url(vocaroo_id, "mp3")
    target = work_dir / f"{vocaroo_id}.mp3"
    headers = {
        "Referer": "https://vocaroo.com/",
        "User-Agent": "Mozilla/5.0",
        "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
    }
    _download_file(media_url, target, headers=headers)
    return SourceAudio(source=value, local_path=target, cleanup=True, media_url=media_url)


def _pick_device() -> tuple[str, str]:
    try:
        import torch  # type: ignore

        if bool(torch.cuda.is_available()):
            return "cuda", str(os.getenv("APOLLO_LOCAL_STT_COMPUTE_TYPE", "float16")).strip() or "float16"
    except Exception:
        pass
    return "cpu", str(os.getenv("APOLLO_LOCAL_STT_COMPUTE_TYPE_CPU", "int8")).strip() or "int8"


def _normalize_model_name(model_name: str, *, backend: str) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        return DEFAULT_MODEL
    if str(backend or "").strip().lower() == "transformers":
        return TRANSFORMERS_MODEL_ALIASES.get(raw, raw)
    return raw


def _load_whisper_model(model_name: str):
    from faster_whisper import WhisperModel  # type: ignore

    device, compute_type = _pick_device()
    model_dir = str(os.getenv("WHISPER_MODEL_DIR", "")).strip() or None
    model_name = _normalize_model_name(model_name, backend="faster-whisper")
    kwargs: Dict[str, Any] = {
        "model_size_or_path": model_name,
        "device": device,
        "compute_type": compute_type,
    }
    if model_dir:
        kwargs["download_root"] = model_dir
    return WhisperModel(**kwargs), device, compute_type


def _load_transformers_pipeline(model_name: str):
    import torch  # type: ignore
    from transformers import pipeline  # type: ignore

    use_cuda = bool(torch.cuda.is_available()) and str(os.getenv("APOLLO_LOCAL_STT_USE_CUDA", "0")).strip().lower() in {"1", "true", "yes"}
    device = 0 if use_cuda else -1
    dtype = torch.float16 if use_cuda else torch.float32
    model_dir = str(os.getenv("WHISPER_MODEL_DIR", "")).strip() or None
    model_name = _normalize_model_name(model_name, backend="transformers")
    kwargs: Dict[str, Any] = {
        "task": "automatic-speech-recognition",
        "model": model_name,
        "device": device,
        "torch_dtype": dtype,
    }
    if model_dir:
        kwargs["model_kwargs"] = {"cache_dir": model_dir}
    return pipeline(**kwargs), ("cuda" if use_cuda else "cpu"), ("float16" if use_cuda else "float32")


def _segments_to_rows(segments: Iterable[Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for segment in segments:
        rows.append(
            {
                "start": float(getattr(segment, "start", 0.0) or 0.0),
                "end": float(getattr(segment, "end", 0.0) or 0.0),
                "text": str(getattr(segment, "text", "") or "").strip(),
            }
        )
    return rows


def _chunks_to_rows(chunks: Iterable[Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        stamp = chunk.get("timestamp") or ()
        start = float(stamp[0] or 0.0) if len(stamp) >= 1 else 0.0
        end = float(stamp[1] or start) if len(stamp) >= 2 and stamp[1] is not None else start
        rows.append({"start": start, "end": end, "text": str(chunk.get("text") or "").strip()})
    return rows


def _transcribe_with_faster_whisper(
    source_path: Path,
    *,
    model_name: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
) -> Dict[str, Any]:
    model, device, compute_type = _load_whisper_model(model_name)
    segments, info = model.transcribe(
        str(source_path),
        language=language or None,
        beam_size=max(1, int(beam_size)),
        vad_filter=bool(vad_filter),
        word_timestamps=False,
    )
    rows = _segments_to_rows(segments)
    text = "\n".join(row["text"] for row in rows if row["text"]).strip()
    detected_language = str(getattr(info, "language", "") or "")
    language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    return {
        "ok": bool(text),
        "text": text,
        "segments": rows,
        "language": detected_language,
        "language_probability": language_probability,
        "duration_sec": duration,
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "backend": "faster-whisper",
        "source_path": str(source_path),
    }


def _transcribe_with_transformers(
    source_path: Path,
    *,
    model_name: str,
    language: str,
) -> Dict[str, Any]:
    pipe, device, compute_type = _load_transformers_pipeline(model_name)
    normalized_model = _normalize_model_name(model_name, backend="transformers").lower()
    kwargs: Dict[str, Any] = {"return_timestamps": True}
    if not normalized_model.endswith(".en"):
        kwargs["generate_kwargs"] = {"language": language or None, "task": "transcribe"}
    result = pipe(str(source_path), **kwargs)
    rows = _chunks_to_rows(result.get("chunks") or [])
    text = str(result.get("text") or "").strip()
    if not rows and text:
        rows = [{"start": 0.0, "end": 0.0, "text": text}]
    return {
        "ok": bool(text),
        "text": text,
        "segments": rows,
        "language": str(language or ""),
        "language_probability": 0.0,
        "duration_sec": max([float(row.get("end") or 0.0) for row in rows] + [0.0]),
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "backend": "transformers",
        "source_path": str(source_path),
    }


def transcribe_audio(
    source_path: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANG,
    beam_size: int = 5,
    vad_filter: bool = True,
    backend: str = DEFAULT_BACKEND,
) -> Dict[str, Any]:
    selected = str(backend or DEFAULT_BACKEND).strip().lower()
    if selected == "faster-whisper":
        return _transcribe_with_faster_whisper(
            source_path,
            model_name=model_name,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
    if selected == "transformers":
        return _transcribe_with_transformers(
            source_path,
            model_name=model_name,
            language=language,
        )
    raise ValueError(f"backend_invalid:{backend}")


def _write_outputs(payload: Dict[str, Any], *, output_base: Path) -> Dict[str, str]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    txt_path = output_base.with_suffix(".txt")
    json_path = output_base.with_suffix(".json")
    txt_path.write_text(str(payload.get("text") or "") + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"text_path": str(txt_path), "json_path": str(json_path)}


def _default_output_base(source_label: str) -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    name = _slug(Path(source_label).stem if not _is_url(source_label) else source_label)
    return REPORT_ROOT / f"{name}_{_utc_stamp()}"


def run_cli(
    source: str,
    *,
    output_base: Optional[Path] = None,
    model_name: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANG,
    beam_size: int = 5,
    vad_filter: bool = True,
    backend: str = DEFAULT_BACKEND,
) -> Dict[str, Any]:
    work_dir = Path(tempfile.mkdtemp(prefix="apollo_vocaroo_"))
    resolved: Optional[SourceAudio] = None
    try:
        resolved = _resolve_source_audio(source, work_dir=work_dir)
        payload = transcribe_audio(
            resolved.local_path,
            model_name=model_name,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            backend=backend,
        )
        payload["input_source"] = source
        payload["media_url"] = resolved.media_url
        outputs = _write_outputs(payload, output_base=output_base or _default_output_base(source))
        payload.update(outputs)
        return payload
    finally:
        if resolved is not None and resolved.cleanup:
            try:
                resolved.local_path.unlink()
            except Exception:
                pass
        shutil.rmtree(work_dir, ignore_errors=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and locally transcribe a Vocaroo link or local audio file.")
    parser.add_argument("source", help="Vocaroo URL or local audio path")
    parser.add_argument("--output-base", help="Output base path without extension. Writes both .txt and .json")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"faster-whisper model name or local path (default: {DEFAULT_MODEL})")
    parser.add_argument("--language", default=DEFAULT_LANG, help=f"Language hint for whisper (default: {DEFAULT_LANG})")
    parser.add_argument("--backend", default=DEFAULT_BACKEND, choices=["transformers", "faster-whisper"])
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--no-vad-filter", action="store_true", help="Disable VAD filtering")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    output_base = Path(args.output_base).expanduser().resolve() if args.output_base else None
    try:
        payload = run_cli(
            args.source,
            output_base=output_base,
            model_name=str(args.model or DEFAULT_MODEL),
            language=str(args.language or DEFAULT_LANG),
            beam_size=max(1, int(args.beam_size)),
            vad_filter=not bool(args.no_vad_filter),
            backend=str(args.backend or DEFAULT_BACKEND),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}"}))
        return 1
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
