from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings


_APOLLO_ROOT = Path(__file__).resolve().parent
KB_ROOT = _APOLLO_ROOT / "memory" / "kb" / "ingest" / "swing_strategy"
LOG_ROOT = _APOLLO_ROOT / "logs" / "homework_trade" / "strategy_kb"
DEFAULT_COLLECTION = "apollo_financial"


STRATEGY_DOCS: List[Dict[str, Any]] = [
    {
        "id": "swing_strategy_v1_risk_first",
        "title": "Swing Strategy Risk-First Rulebook",
        "category": "risk_model",
        "text": """Apollo swing strategy note: Risk comes before signal. A setup is not actionable unless entry, stop, invalidation, and review date are all explicit. Default risk per idea is paper-only until a statistically useful sample exists. Reject trades with unclear stop placement, major earnings/event risk inside the hold window, poor liquidity, stale data, or risk distance too wide for the account plan. Never convert a research candidate into execution without human approval.""",
    },
    {
        "id": "swing_strategy_v1_base_breakout",
        "title": "Base Breakout Checklist",
        "category": "base_breakout",
        "text": """Apollo swing strategy note: A base breakout is strongest when price is near or above a 20-day high, the 8 EMA is above the 21 EMA, price is above the 50 SMA, volume expands above the 20-day average, relative strength versus SPY and QQQ is positive, and market regime is risk-on or mixed. Invalidation is a close back inside the base or below the breakout pivot/ATR stop. Avoid breakouts that are already extended several ATRs from the 20 SMA.""",
    },
    {
        "id": "swing_strategy_v1_pullback_to_trend",
        "title": "Pullback To Trend Checklist",
        "category": "pullback_to_trend",
        "text": """Apollo swing strategy note: A pullback-to-trend setup is strongest when the longer trend is intact, price pulls back toward the 20 SMA or 50 SMA without closing decisively below trend support, RSI cools into a constructive zone rather than staying overheated, and volume dries up on the pullback then improves on the turn. Entry should be near support, not after a large bounce. Invalidation is a close below the selected support zone or a bearish 8/21 EMA reversal.""",
    },
    {
        "id": "swing_strategy_v1_trend_continuation",
        "title": "Trend Continuation Checklist",
        "category": "trend_continuation",
        "text": """Apollo swing strategy note: Trend continuation is a watchlist condition, not automatically a trade. Apollo should prefer continuation only when price is above the 20/50/200 moving averages, relative strength is improving, ATR risk is moderate, and the entry zone remains close enough to support to allow a defined stop. If price is too far above the 20 SMA, downgrade to extended_no_chase.""",
    },
    {
        "id": "swing_strategy_v1_no_chase",
        "title": "Extended No-Chase Rule",
        "category": "extended_no_chase",
        "text": """Apollo swing strategy note: Extended charts are usually observation candidates, not entries. If price is more than roughly 2.5 to 3 ATRs above the 20 SMA, RSI is overheated, or risk distance to a logical stop is too large, Apollo should mark no_trade and wait for a base, pullback, or lower-risk reset. Strong catalysts do not override excessive risk distance.""",
    },
    {
        "id": "swing_strategy_v1_broken_trend",
        "title": "Broken Trend Rejection Rule",
        "category": "broken_trend",
        "text": """Apollo swing strategy note: Broken trend overrides positive headlines. If price loses the 50 SMA, the 8 EMA falls below the 21 EMA, relative strength is deteriorating, or the market regime is risk-off, Apollo should reject long-biased proposals unless the setup is explicitly a recovery watchlist item. A broken trend with a catalyst can be logged for later, but it should not produce a long trade proposal.""",
    },
    {
        "id": "swing_strategy_v1_relative_strength",
        "title": "Relative Strength Filter",
        "category": "relative_strength",
        "text": """Apollo swing strategy note: Prefer stocks outperforming SPY and QQQ over 20 and 50 trading days. Relative strength improves confidence when it confirms the setup type. Weak relative strength lowers confidence even if the chart pattern appears clean, because capital should favor leaders during swing-trade windows.""",
    },
    {
        "id": "swing_strategy_v1_volume",
        "title": "Volume Confirmation",
        "category": "volume_confirmation",
        "text": """Apollo swing strategy note: Volume confirmation is context-sensitive. Breakouts benefit from volume expansion above the 20-day average. Pullbacks benefit from quieter volume during the pullback and renewed volume on the turn. Thin or missing volume should lower provider confidence and require manual verification.""",
    },
    {
        "id": "swing_strategy_v1_atr_stops",
        "title": "ATR Stop And Target Framework",
        "category": "atr_risk",
        "text": """Apollo swing strategy note: ATR should shape risk. Stops should sit beyond obvious support and account for normal volatility, usually around 1 to 1.5 ATR from entry unless structure requires otherwise. Targets should offer enough reward relative to risk. If ATR percentage is too high or the stop is too far away, Apollo should prefer no_trade.""",
    },
    {
        "id": "swing_strategy_v1_event_risk",
        "title": "Earnings And Event Risk",
        "category": "event_risk",
        "text": """Apollo swing strategy note: Earnings, guidance, lawsuits, SEC filings, downgrades, probes, and major macro events can change gap risk. Apollo should flag event risk separately from catalyst quality. Positive news can support a setup, but imminent earnings or binary events should either reduce confidence, shorten review window, or block a swing entry.""",
    },
    {
        "id": "swing_strategy_v1_market_regime",
        "title": "Market Regime Filter",
        "category": "market_regime",
        "text": """Apollo swing strategy note: Market regime controls aggressiveness. In risk-on markets, Apollo can rank clean breakouts and pullbacks normally. In mixed markets, require stronger relative strength and tighter risk. In risk-off markets, new long-biased swing proposals should be rare and must have exceptional relative strength, catalyst quality, and risk definition.""",
    },
    {
        "id": "swing_strategy_v1_postmortem",
        "title": "Paper Outcome Postmortem Rubric",
        "category": "paper_outcome",
        "text": """Apollo swing strategy note: Every candidate should be graded after 2, 5, 10, and 20 trading days. Track whether the target was hit, stop was hit, max favorable move, max adverse move, whether the setup stayed valid, and whether Apollo repeated a stale idea. Improve the strategy from outcome clusters, not single anecdotes.""",
    },
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rag_dir() -> Path:
    raw = os.getenv("RAG_DIR", "")
    if raw and not os.path.isabs(raw):
        return (_APOLLO_ROOT / raw).resolve()
    return Path(raw or (_APOLLO_ROOT / "chroma_db")).resolve()


def _collection_name() -> str:
    return str(os.getenv("RAG_COLLECTION") or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _embed_documents(texts: List[str]) -> Optional[List[List[float]]]:
    try:
        from common.rag_store import _ollama_embed

        vectors = _ollama_embed(texts)
        return vectors if vectors else None
    except Exception:
        return None


def materialize_strategy_docs() -> Dict[str, Any]:
    KB_ROOT.mkdir(parents=True, exist_ok=True)
    files: List[str] = []
    for doc in STRATEGY_DOCS:
        path = KB_ROOT / f"{doc['id']}.md"
        body = f"# {doc['title']}\n\nCategory: {doc['category']}\n\n{doc['text'].strip()}\n"
        path.write_text(body, encoding="utf-8")
        files.append(str(path))
    manifest = {
        "created_at": _utc_iso(),
        "doc_count": len(STRATEGY_DOCS),
        "files": files,
        "research_only": True,
    }
    _write_json(KB_ROOT / "manifest.json", manifest)
    return manifest


def ingest_strategy_kb() -> Dict[str, Any]:
    materialize_strategy_docs()
    rag_dir = _rag_dir()
    collection = _collection_name()
    client = chromadb.PersistentClient(
        path=str(rag_dir),
        settings=Settings(allow_reset=False, anonymized_telemetry=False),
    )
    col = client.get_or_create_collection(collection)
    ids: List[str] = []
    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []
    for doc in STRATEGY_DOCS:
        ids.append(str(doc["id"]))
        documents.append(str(doc["text"]).strip())
        metadatas.append(
            {
                "kind": "education",
                "source": "apollo_swing_strategy_kb",
                "strategy_category": str(doc["category"]),
                "title": str(doc["title"]),
                "tags": "swing_strategy,apollo_homework,trade_rubric,risk_first",
                "provenance_class": "seed_local",
                "research_only": True,
                "timestamp": _utc_iso(),
            }
        )
    try:
        col.upsert(ids=ids, documents=documents, metadatas=metadatas)
        embedding_mode = "collection_default"
    except Exception as exc:
        embeddings = _embed_documents(documents)
        if not embeddings:
            raise
        col.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
        embedding_mode = f"fallback_ollama_after_{type(exc).__name__}"
    result = {
        "ok": True,
        "created_at": _utc_iso(),
        "rag_dir": str(rag_dir),
        "collection": collection,
        "doc_count": len(ids),
        "ids": ids,
        "embedding_mode": embedding_mode,
        "collection_count": int(col.count()),
    }
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    _write_json(LOG_ROOT / "latest_strategy_kb_ingest.json", result)
    return result


def search_strategy_kb(query: str, top_k: int = 5) -> Dict[str, Any]:
    rag_dir = _rag_dir()
    collection = _collection_name()
    client = chromadb.PersistentClient(
        path=str(rag_dir),
        settings=Settings(allow_reset=False, anonymized_telemetry=False),
    )
    col = client.get_or_create_collection(collection)
    n_results = max(1, min(int(top_k), len(STRATEGY_DOCS)))
    try:
        res = col.query(
            query_texts=[query],
            n_results=n_results,
            where={"source": "apollo_swing_strategy_kb"},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        embeddings = _embed_documents([query])
        if not embeddings:
            return {"ok": False, "error": f"{type(exc).__name__}:{exc}", "results": []}
        try:
            res = col.query(
                query_embeddings=embeddings,
                n_results=n_results,
                where={"source": "apollo_swing_strategy_kb"},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as fallback_exc:
            return {"ok": False, "error": f"{type(fallback_exc).__name__}:{fallback_exc}", "results": []}
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    rows: List[Dict[str, Any]] = []
    for text, meta, dist in zip(docs, metas, dists):
        rows.append(
            {
                "text": text or "",
                "meta": meta or {},
                "score": round(1.0 - float(dist), 4),
            }
        )
    return {"ok": True, "query": query, "results": rows, "returned": len(rows)}


def build_strategy_context(proposal: Dict[str, Any], top_k: int = 5) -> Dict[str, Any]:
    setup = str(proposal.get("setup_type") or "")
    reason = str(proposal.get("no_trade_reason") or "")
    query = " ".join(
        part
        for part in [
            "swing trading strategy",
            setup,
            reason,
            "risk entry stop target market regime relative strength volume catalyst",
        ]
        if part
    )
    hits = search_strategy_kb(query, top_k=top_k)
    rubric: List[str] = []
    for hit in list(hits.get("results") or []):
        meta = dict(hit.get("meta") or {})
        title = str(meta.get("title") or meta.get("strategy_category") or "strategy")
        text = re.sub(r"\s+", " ", str(hit.get("text") or "")).strip()
        rubric.append(f"{title}: {text[:360]}")
    return {
        "ok": bool(hits.get("ok")),
        "query": query,
        "hits": list(hits.get("results") or []),
        "rubric": rubric,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apollo swing strategy KB")
    parser.add_argument("cmd", choices=["materialize", "ingest", "search"], nargs="?", default="ingest")
    parser.add_argument("--query", default="swing breakout pullback risk")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.cmd == "materialize":
        print(json.dumps(materialize_strategy_docs(), indent=2))
    elif args.cmd == "search":
        print(json.dumps(search_strategy_kb(args.query, args.top_k), indent=2))
    else:
        print(json.dumps(ingest_strategy_kb(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "STRATEGY_DOCS",
    "build_strategy_context",
    "ingest_strategy_kb",
    "materialize_strategy_docs",
    "search_strategy_kb",
]
