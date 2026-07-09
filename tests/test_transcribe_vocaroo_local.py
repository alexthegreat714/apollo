from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "transcribe_vocaroo_local.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_module(TOOL_PATH, "apollo_transcribe_vocaroo_local")


def test_extract_vocaroo_id_supports_short_and_full_urls():
    assert tool._extract_vocaroo_id("https://voca.ro/1kryzPgK0MyJ") == "1kryzPgK0MyJ"
    assert tool._extract_vocaroo_id("https://vocaroo.com/embed/1kryzPgK0MyJ") == "1kryzPgK0MyJ"


def test_vocaroo_media_url_uses_expected_mp3_host():
    assert tool._vocaroo_media_url("1kryzPgK0MyJ") == "https://media1.vocaroo.com/mp3/1kryzPgK0MyJ"


def test_resolve_source_audio_downloads_vocaroo(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_download(url, target, *, headers=None, timeout_sec=60):
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        target.write_bytes(b"mp3")
        return target

    monkeypatch.setattr(tool, "_download_file", fake_download)

    resolved = tool._resolve_source_audio("https://voca.ro/1kryzPgK0MyJ", work_dir=tmp_path)

    assert resolved.cleanup is True
    assert resolved.local_path.exists()
    assert captured["url"].endswith("/mp3/1kryzPgK0MyJ")
    assert captured["headers"]["Referer"] == "https://vocaroo.com/"


def test_run_cli_writes_text_and_json(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "clip.mp3"
    audio_path.write_bytes(b"mp3")
    monkeypatch.setattr(tool, "_resolve_source_audio", lambda source, work_dir: tool.SourceAudio(source=source, local_path=audio_path))
    monkeypatch.setattr(
        tool,
        "transcribe_audio",
        lambda *args, **kwargs: {
            "ok": True,
            "text": "hello world",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            "language": "en",
            "language_probability": 0.99,
            "duration_sec": 1.0,
            "model": "small.en",
            "device": "cpu",
            "compute_type": "int8",
            "source_path": str(audio_path),
        },
    )

    payload = tool.run_cli(str(audio_path), output_base=tmp_path / "out" / "transcript")

    assert payload["ok"] is True
    assert Path(payload["text_path"]).read_text(encoding="utf-8").strip() == "hello world"
    assert Path(payload["json_path"]).exists()
