from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests


_APOLLO_ROOT = Path(__file__).resolve().parent
_DEFAULT_MANIFEST_PATH = _APOLLO_ROOT / "config" / "real_public_corpus_manifest.json"
_DEFAULT_COLLECTION = os.getenv("RAG_COLLECTION", "apollo_financial")
_DEFAULT_RAG_DIR = str((_APOLLO_ROOT / "chroma_db").resolve())
_MAX_SOURCE_TEXT_CHARS = int(os.getenv("APOLLO_CORPUS_BOOTSTRAP_MAX_SOURCE_CHARS", "28000"))
_CHUNK_SIZE = int(os.getenv("APOLLO_CORPUS_BOOTSTRAP_CHUNK_SIZE", "1400"))
_CHUNK_OVERLAP = int(os.getenv("APOLLO_CORPUS_BOOTSTRAP_CHUNK_OVERLAP", "180"))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized_rag_dir() -> str:
    raw = str(os.getenv("RAG_DIR") or "").strip()
    if not raw:
        return _DEFAULT_RAG_DIR
    if os.path.isabs(raw):
        return raw
    return str((_APOLLO_ROOT / raw).resolve())


def _safe_slug(value: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return raw or "source"


def _domain(url: str) -> str:
    match = re.match(r"^[a-z]+://([^/]+)", str(url or "").strip().lower())
    return match.group(1) if match else ""


def _truncate(text: str, max_chars: int = _MAX_SOURCE_TEXT_CHARS) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 3)] + "..."


def _chunks(text: str, *, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    clean = _truncate(text, max_chars=max(size, _MAX_SOURCE_TEXT_CHARS))
    if not clean:
        return []
    overlap = max(0, min(overlap, size // 2))
    if len(clean) <= size:
        return [clean]
    out: List[str] = []
    step = max(1, size - overlap)
    for idx in range(0, len(clean), step):
        chunk = clean[idx : idx + size].strip()
        if chunk:
            out.append(chunk)
        if idx + size >= len(clean):
            break
    return out


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def load_real_public_manifest(path: str | Path | None = None) -> List[Dict[str, Any]]:
    manifest_path = Path(path or _DEFAULT_MANIFEST_PATH)
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "id": str(row.get("id") or _safe_slug(url)).strip(),
                "title": str(row.get("title") or "").strip() or url,
                "url": url,
                "pack": str(row.get("pack") or "market_data_series").strip() or "market_data_series",
                "tier": str(row.get("tier") or "B").strip().upper() or "B",
                "format": str(row.get("format") or "").strip().lower(),
                "license_hint": str(row.get("license_hint") or "").strip(),
                "source_type": str(row.get("source_type") or "public").strip(),
            }
        )
    return out


def _extract_html_text(html: str) -> str:
    body = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html or "")
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return _truncate(body)


def _extract_csv_text(raw: str, max_rows: int = 160) -> str:
    lines = [line for line in str(raw or "").splitlines() if line.strip()]
    if not lines:
        return ""
    reader = csv.reader(lines)
    rows = []
    for idx, row in enumerate(reader):
        if idx >= max_rows:
            break
        rows.append([str(cell).strip() for cell in row])
    if not rows:
        return ""
    headers = rows[0]
    data_rows = rows[1:]
    parts = ["CSV TABLE"]
    parts.append(" | ".join(headers[:10]))
    for row in data_rows[: max_rows - 1]:
        parts.append(" | ".join(row[:10]))
    return _truncate("\n".join(parts))


def _extract_json_text(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except Exception:
        return _truncate(raw)
    pretty = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return _truncate(pretty)


def _extract_pdf_text(binary: bytes) -> str:
    try:
        import fitz  # type: ignore
    except Exception:
        return ""
    try:
        with fitz.open(stream=binary, filetype="pdf") as doc:
            pages: List[str] = []
            for page in doc[: min(20, len(doc))]:
                pages.append(str(page.get_text("text") or ""))
    except Exception:
        return ""
    return _truncate("\n".join(pages))


def _guess_format(spec: Dict[str, Any], content_type: str) -> str:
    fmt = str(spec.get("format") or "").strip().lower()
    if fmt:
        return fmt
    url = str(spec.get("url") or "").lower()
    if url.endswith(".pdf"):
        return "pdf"
    if url.endswith(".csv"):
        return "csv"
    if "json" in content_type:
        return "json"
    if "pdf" in content_type:
        return "pdf"
    if "csv" in content_type:
        return "csv"
    return "html"


def _fetch_source_text(spec: Dict[str, Any], timeout_sec: int) -> Tuple[bool, str, Dict[str, Any]]:
    url = str(spec.get("url") or "").strip()
    if not url:
        return False, "", {"error": "missing_url"}
    meta: Dict[str, Any] = {"url": url, "status_code": 0, "content_type": "", "format": ""}
    try:
        response = requests.get(url, timeout=timeout_sec, headers={"User-Agent": "ApolloRealCorpusBootstrap/1.0"})
    except Exception as exc:
        meta["error"] = f"request_failed:{type(exc).__name__}"
        return False, "", meta
    meta["status_code"] = int(getattr(response, "status_code", 0) or 0)
    if not response.ok:
        meta["error"] = f"http_{meta['status_code']}"
        return False, "", meta
    content_type = str(response.headers.get("content-type") or "").strip().lower()
    meta["content_type"] = content_type
    fmt = _guess_format(spec, content_type)
    meta["format"] = fmt
    text = ""
    if fmt == "pdf":
        text = _extract_pdf_text(response.content or b"")
    else:
        raw = response.text or ""
        if fmt == "csv":
            text = _extract_csv_text(raw)
        elif fmt == "json":
            text = _extract_json_text(raw)
        else:
            text = _extract_html_text(raw)
    if not text:
        meta["error"] = "extract_empty"
        return False, "", meta
    return True, text, meta


def _iter_collection_metadatas(collection: Any, *, batch_size: int = 500) -> Iterable[Dict[str, Any]]:
    offset = 0
    while True:
        payload = collection.get(include=["metadatas"], limit=batch_size, offset=offset)
        metadatas = list(payload.get("metadatas") or [])
        if not metadatas:
            break
        for meta in metadatas:
            if isinstance(meta, dict):
                yield meta
        offset += len(metadatas)
        if len(metadatas) < batch_size:
            break


def audit_financial_corpus(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = dict(payload or {})
    rag_dir = str(cfg.get("rag_dir") or _normalized_rag_dir()).strip()
    collection_name = str(cfg.get("collection") or _DEFAULT_COLLECTION).strip() or _DEFAULT_COLLECTION
    top_k = max(1, min(int(cfg.get("top_k_sources") or 8), 20))
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:
        return {"ok": False, "error": f"chromadb_import_failed:{type(exc).__name__}", "collection": collection_name}
    try:
        client = chromadb.PersistentClient(path=rag_dir, settings=Settings(allow_reset=False, anonymized_telemetry=False))
        collection = client.get_or_create_collection(collection_name)
    except Exception as exc:
        return {"ok": False, "error": f"collection_unavailable:{type(exc).__name__}:{exc}", "collection": collection_name}
    total = int(collection.count())
    provenance_counts: Counter[str] = Counter()
    real_sources: Counter[str] = Counter()
    for meta in _iter_collection_metadatas(collection):
        provenance = str(meta.get("provenance_class") or "synthetic").strip().lower() or "synthetic"
        provenance_counts[provenance] += 1
        if provenance == "real_public":
            source_url = str(meta.get("source_url") or meta.get("url") or "").strip()
            source_key = _domain(source_url) or source_url or str(meta.get("source") or "unknown")
            real_sources[source_key] += 1
    real_count = int(provenance_counts.get("real_public", 0))
    synthetic_count = int(provenance_counts.get("synthetic", 0))
    seed_count = int(provenance_counts.get("seed_local", 0))
    unknown_count = max(0, total - real_count - synthetic_count - seed_count)
    ratio = float(real_count / max(1, total))
    top_real = [{"source": source, "chunks": int(count)} for source, count in real_sources.most_common(top_k)]
    return {
        "ok": True,
        "collection": collection_name,
        "rag_dir": rag_dir,
        "total_chunks": total,
        "real_chunk_count": real_count,
        "synthetic_chunk_count": synthetic_count,
        "seed_local_chunk_count": seed_count,
        "unknown_chunk_count": unknown_count,
        "real_ratio": round(ratio, 4),
        "provenance_counts": dict(provenance_counts),
        "top_real_sources": top_real,
        "audited_at": _utc_iso(),
    }


def bootstrap_real_public_corpus(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = dict(payload or {})
    rag_dir = str(cfg.get("rag_dir") or _normalized_rag_dir()).strip()
    collection_name = str(cfg.get("collection") or _DEFAULT_COLLECTION).strip() or _DEFAULT_COLLECTION
    timeout_sec = max(3, min(int(cfg.get("timeout_sec") or 20), 120))
    min_chars = max(120, min(int(cfg.get("min_chars") or 400), 2000))
    max_sources = max(1, min(int(cfg.get("max_sources") or 18), 80))
    preferred_packs = {str(item).strip() for item in list(cfg.get("packs") or []) if str(item).strip()}
    manifest_rows = load_real_public_manifest(cfg.get("manifest_path"))
    if preferred_packs:
        manifest_rows = [row for row in manifest_rows if str(row.get("pack") or "") in preferred_packs]
    manifest_rows = manifest_rows[:max_sources]
    if not manifest_rows:
        return {"ok": False, "error": "manifest_empty", "collection": collection_name, "rag_dir": rag_dir}

    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:
        return {"ok": False, "error": f"chromadb_import_failed:{type(exc).__name__}", "collection": collection_name}
    try:
        client = chromadb.PersistentClient(path=rag_dir, settings=Settings(allow_reset=False, anonymized_telemetry=False))
        collection = client.get_or_create_collection(collection_name)
    except Exception as exc:
        return {"ok": False, "error": f"collection_unavailable:{type(exc).__name__}:{exc}", "collection": collection_name}

    existing_hashes: set[str] = set()
    for meta in _iter_collection_metadatas(collection):
        doc_hash = str(meta.get("doc_hash") or "").strip()
        if doc_hash:
            existing_hashes.add(doc_hash)

    now_iso = _utc_iso()
    added_chunks = 0
    added_sources = 0
    skipped_existing = 0
    skipped_low_quality = 0
    errors: List[str] = []
    added_details: List[Dict[str, Any]] = []

    for spec in manifest_rows:
        ok, text, fetch_meta = _fetch_source_text(spec, timeout_sec=timeout_sec)
        if not ok:
            errors.append(f"fetch_failed:{spec.get('id')}:{fetch_meta.get('error')}")
            continue
        if len(text) < min_chars:
            skipped_low_quality += 1
            errors.append(f"source_too_short:{spec.get('id')}:{len(text)}<{min_chars}")
            continue
        source_url = str(spec.get("url") or "")
        doc_hash = _sha256(f"{source_url}\n{text[:12000]}")
        if doc_hash in existing_hashes:
            skipped_existing += 1
            continue
        chunks = _chunks(text)
        if not chunks:
            skipped_low_quality += 1
            errors.append(f"chunking_empty:{spec.get('id')}")
            continue
        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        source_slug = _safe_slug(str(spec.get("id") or spec.get("title") or source_url))
        for idx, chunk in enumerate(chunks, start=1):
            ids.append(f"real_{source_slug}_{doc_hash[:12]}_{idx:03d}")
            documents.append(chunk)
            metadatas.append(
                {
                    "source": str(spec.get("title") or spec.get("id") or source_url),
                    "title": str(spec.get("title") or ""),
                    "kind": str(spec.get("pack") or "market_data"),
                    "pack": str(spec.get("pack") or "market_data"),
                    "source_tier": str(spec.get("tier") or "B"),
                    "source_url": source_url,
                    "url": source_url,
                    "retrieved_at": now_iso,
                    "license_hint": str(spec.get("license_hint") or ""),
                    "provenance_class": "real_public",
                    "doc_hash": doc_hash,
                    "source_format": str(fetch_meta.get("format") or ""),
                    "content_type": str(fetch_meta.get("content_type") or ""),
                }
            )
        try:
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        except Exception as exc:
            errors.append(f"collection_upsert_failed:{spec.get('id')}:{type(exc).__name__}")
            continue
        existing_hashes.add(doc_hash)
        added_sources += 1
        added_chunks += len(chunks)
        added_details.append(
            {
                "id": str(spec.get("id") or ""),
                "title": str(spec.get("title") or ""),
                "url": source_url,
                "pack": str(spec.get("pack") or ""),
                "tier": str(spec.get("tier") or ""),
                "chunks_added": len(chunks),
                "chars": len(text),
            }
        )

    audit = audit_financial_corpus({"rag_dir": rag_dir, "collection": collection_name})
    return {
        "ok": added_chunks > 0,
        "collection": collection_name,
        "rag_dir": rag_dir,
        "added_sources": added_sources,
        "added_chunks": added_chunks,
        "skipped_existing": skipped_existing,
        "skipped_low_quality": skipped_low_quality,
        "errors": errors,
        "sources": added_details,
        "audited_at": now_iso,
        "audit": audit,
    }

