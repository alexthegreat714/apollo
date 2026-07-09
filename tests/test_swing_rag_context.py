from __future__ import annotations

from Apollo import corpus_live_ingest, swing_study


def test_rag_context_falls_back_to_metadata_get_when_vector_query_fails(monkeypatch):
    class FakeCollection:
        def query(self, **_kwargs):
            raise RuntimeError("Error finding id")

        def get(self, **kwargs):
            assert kwargs["where"] == {"ticker": "AMD"}
            return {
                "documents": [
                    "AMD revenue growth and earnings guidance improved after a data center demand surge.",
                    "AMD analyst note discusses valuation and margin risk.",
                ],
                "metadatas": [
                    {
                        "ticker": "AMD",
                        "pack": "equity_news",
                        "source": "apollo_nightly_news_feed",
                        "title": "AMD raises guidance after data center demand",
                        "timestamp": "2026-06-29T12:00:00Z",
                    },
                    {
                        "ticker": "AMD",
                        "pack": "analyst_note",
                        "source": "broker_research",
                        "title": "AMD analyst valuation note",
                        "timestamp": "2026-06-29T13:00:00Z",
                    },
                ],
            }

    class FakeClient:
        def get_or_create_collection(self, _name):
            return FakeCollection()

    monkeypatch.setattr(corpus_live_ingest, "_get_client", lambda _rag_dir: FakeClient())

    rows = swing_study._rag_context_for_ticker("AMD", "event_dislocation")

    assert len(rows) == 2
    assert rows[0]["retrieval_mode"] == "metadata_fallback"
    assert "RuntimeError:Error finding id" in rows[0]["retrieval_error"]
    assert rows[0]["pack"] == "equity_news"
