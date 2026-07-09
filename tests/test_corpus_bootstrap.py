from __future__ import annotations

import sys
import types

from Apollo import corpus_bootstrap


class _FakeCollection:
    def __init__(self, metadatas=None):
        self.metadatas = list(metadatas or [])
        self.documents = []
        self.ids = []

    def count(self):
        return len(self.metadatas)

    def get(self, include=None, limit=500, offset=0):
        batch = self.metadatas[offset : offset + limit]
        return {"metadatas": list(batch)}

    def upsert(self, ids, documents, metadatas):
        self.ids.extend(list(ids or []))
        self.documents.extend(list(documents or []))
        self.metadatas.extend(list(metadatas or []))


class _FakeClient:
    def __init__(self, collection):
        self._collection = collection

    def get_or_create_collection(self, _name):
        return self._collection


def _install_fake_chroma(monkeypatch, collection):
    chroma_mod = types.SimpleNamespace(
        PersistentClient=lambda path, settings=None: _FakeClient(collection),
    )
    chroma_config = types.SimpleNamespace(Settings=lambda **kwargs: kwargs)
    monkeypatch.setitem(sys.modules, "chromadb", chroma_mod)
    monkeypatch.setitem(sys.modules, "chromadb.config", chroma_config)


def test_audit_financial_corpus_reports_real_ratio(monkeypatch):
    collection = _FakeCollection(
        metadatas=[
            {"provenance_class": "real_public", "source_url": "https://sec.gov/x"},
            {"provenance_class": "real_public", "source_url": "https://finra.org/y"},
            {"provenance_class": "synthetic"},
            {"provenance_class": "seed_local"},
        ]
    )
    _install_fake_chroma(monkeypatch, collection)
    payload = corpus_bootstrap.audit_financial_corpus({"collection": "apollo_financial"})
    assert payload["ok"] is True
    assert payload["real_chunk_count"] == 2
    assert payload["synthetic_chunk_count"] == 1
    assert payload["seed_local_chunk_count"] == 1
    assert payload["real_ratio"] == 0.5


def test_bootstrap_real_public_corpus_adds_provenance_chunks(monkeypatch):
    collection = _FakeCollection(metadatas=[])
    _install_fake_chroma(monkeypatch, collection)
    monkeypatch.setattr(
        corpus_bootstrap,
        "load_real_public_manifest",
        lambda path=None: [
            {
                "id": "sec-margin",
                "title": "SEC Margin Accounts",
                "url": "https://sec.gov/margin",
                "pack": "regulatory_compliance",
                "tier": "A",
                "format": "html",
                "license_hint": "public",
            }
        ],
    )
    monkeypatch.setattr(
        corpus_bootstrap,
        "_fetch_source_text",
        lambda spec, timeout_sec: (
            True,
            "stock trading day trading margin risk management " * 120,
            {"format": "html", "content_type": "text/html", "status_code": 200},
        ),
    )
    result = corpus_bootstrap.bootstrap_real_public_corpus({"max_sources": 1, "min_chars": 200})
    assert result["ok"] is True
    assert result["added_chunks"] > 0
    assert any(str(meta.get("provenance_class")) == "real_public" for meta in collection.metadatas)
    assert result["audit"]["real_chunk_count"] > 0
