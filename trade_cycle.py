"""
Apollo Autonomous Trade Research Cycle
=======================================
Stages (run sequentially, all results logged):

  1. HippoRAG  — gather equity RSS, ingest into KG (pattern mode, ~30 min)
  2. Market    — swing_study with fresh KG, ranked proposals
  3. News      — sample Yahoo Finance headlines, weighted sentiment per ticker
  4. Risk      — position sizing, entry validity, liquidity gate
  5. Simulate  — run_simulation on top candidate, write observation report

Run:
  python -m Apollo.trade_cycle run
  python -m Apollo.trade_cycle status          # check last cycle log
  python -m Apollo.trade_cycle report          # print latest observation report
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
try:
    from .config import DEFAULT_WATCHLIST, get_high_risk_review_tickers, get_watchlist
    from .model_policy import assert_model_storage_allowed, resolve_model_policy
    from .news_evidence import aggregate_news_evidence, freshness_weight, normalize_domain
    from . import stress_mode
except ImportError:
    from config import DEFAULT_WATCHLIST, get_high_risk_review_tickers, get_watchlist
    from model_policy import assert_model_storage_allowed, resolve_model_policy
    from news_evidence import aggregate_news_evidence, freshness_weight, normalize_domain
    import stress_mode

_APOLLO_ROOT = Path(__file__).resolve().parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent
sys.path.insert(0, str(_ENGINEERING_ROOT))

CYCLE_LOG_ROOT = _APOLLO_ROOT / "logs" / "trade_cycle"
WATCHLIST: List[str] = get_watchlist(os.getenv("APOLLO_WATCHLIST"))
HIGH_RISK_REVIEW_TICKERS = set(get_high_risk_review_tickers())
PORTFOLIO_SIZE = float(
    os.getenv(
        "APOLLO_PAPER_PORTFOLIO_SIZE",
        os.getenv("APOLLO_SIM_SIMULATED_BUYING_POWER", os.getenv("APOLLO_SIM_BUYING_POWER", "100")),
    )
)  # paper portfolio dollars
RISK_PCT = 0.01             # 1% risk per trade
PROBE_RISK_PCT = 0.005      # 0.5% risk for simulation-only probes
STANDARD_MAX_NOTIONAL_PCT = 0.15
PROBE_MAX_NOTIONAL_PCT = 0.05
STANDARD_MIN_RR = 1.0
PROBE_MIN_RR = 0.45
PROBE_SCORE_MIN = 80.0
PROBE_EVENT_SCORE_MIN = 75.0
KEYWORD_STANDARD_MIN_HEADLINES = 4
KEYWORD_STANDARD_MIN_DIRECTIONAL_HEADLINES = 2
KEYWORD_STANDARD_MIN_ABS_WEIGHTED_SENTIMENT = 0.35
KEYWORD_STANDARD_MIN_CONSENSUS_RATIO = 0.75
KEYWORD_STANDARD_MIN_CONFIDENCE = 0.72
AGGRESSIVE_PAPER_ENABLED = os.getenv("APOLLO_AGGRESSIVE_PAPER_ENABLED", "1") == "1"
AGGRESSIVE_UNIVERSE_ENABLED = os.getenv("APOLLO_AGGRESSIVE_UNIVERSE_ENABLED", "1") == "1"
AGGRESSIVE_PROBE_RISK_PCT = float(os.getenv("APOLLO_AGGRESSIVE_PROBE_RISK_PCT", "0.005"))
AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT = float(os.getenv("APOLLO_AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT", "0.05"))
AGGRESSIVE_MIN_RR = float(os.getenv("APOLLO_AGGRESSIVE_MIN_RR", "1.5"))
AGGRESSIVE_MIN_SCORE = float(os.getenv("APOLLO_AGGRESSIVE_MIN_SCORE", "72"))
AGGRESSIVE_MIN_EVENT_SCORE = float(os.getenv("APOLLO_AGGRESSIVE_MIN_EVENT_SCORE", "65"))
AGGRESSIVE_MIN_VOLUME_RATIO = float(os.getenv("APOLLO_AGGRESSIVE_MIN_VOLUME_RATIO", "1.25"))
PRESSED_AGGRESSIVE_ENABLED = os.getenv("APOLLO_PRESSED_AGGRESSIVE_PAPER_ENABLED", "1") == "1"
PRESSED_AGGRESSIVE_RISK_PCT = float(os.getenv("APOLLO_PRESSED_AGGRESSIVE_RISK_PCT", "0.0075"))
PRESSED_AGGRESSIVE_MAX_NOTIONAL_PCT = float(os.getenv("APOLLO_PRESSED_AGGRESSIVE_MAX_NOTIONAL_PCT", "0.075"))
PRESSED_AGGRESSIVE_MIN_RR = float(os.getenv("APOLLO_PRESSED_AGGRESSIVE_MIN_RR", "2.0"))
PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE = float(os.getenv("APOLLO_PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE", "65"))
MIN_AVG_VOLUME = 500_000    # minimum 30-day avg daily volume
MAX_CATALYST_AGE_DAYS = 14  # news older than this is discounted
MIN_REGIME_SCORE_RISK_OFF = 88  # require higher bar in risk-off market
EVENT_EXPANSION_DEFAULT = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "BAC", "GS", "XOM", "CVX", "LLY", "UNH", "CAT", "DE",
]
AGGRESSIVE_UNIVERSE_DEFAULT = [
    "UBER", "NFLX", "DDOG", "NET", "CRWD", "MDB", "TEAM", "ZS", "CELH",
    "DKNG", "HOOD", "RBLX", "COIN", "SMCI", "ON", "MRVL", "DELL", "HIMS",
    "CAVA", "APP", "AFRM", "SOFI", "RIVN", "ENPH", "FSLR", "CCJ",
]

_POSITIVE_KW = {
    "beat", "beats", "record", "upgrade", "buy", "strong", "strength",
    "growth", "surge", "rally", "bullish", "accelerat", "win", "profit",
    "raise", "guidance", "outperform", "exceeded", "raised", "momentum",
    "breakout", "expansion", "demand", "contract", "partnership", "deal",
}
_NEGATIVE_KW = {
    "miss", "misses", "downgrade", "sell", "warn", "warning", "decline",
    "fall", "drop", "bearish", "disappoint", "layoff", "cut", "loss",
    "weak", "slowdown", "risk", "concern", "probe", "investigation",
    "lawsuit", "recall", "delay", "guidance cut", "below expectations",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _unique_tickers(*groups: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for group in groups:
        for raw in group:
            ticker = str(raw or "").strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            result.append(ticker)
    return result


def _aggressive_extra_tickers() -> List[str]:
    aggressive: List[str] = []
    if AGGRESSIVE_UNIVERSE_ENABLED:
        aggressive = get_watchlist(os.getenv("APOLLO_AGGRESSIVE_EXTRA_WATCHLIST", ",".join(AGGRESSIVE_UNIVERSE_DEFAULT)))
    return aggressive


def _paper_discovery_tickers() -> List[str]:
    return _unique_tickers(WATCHLIST, _aggressive_extra_tickers())


def _event_research_tickers() -> List[str]:
    expansion = get_watchlist(os.getenv("APOLLO_EVENT_EXPANSION_TICKERS", ",".join(EVENT_EXPANSION_DEFAULT)))
    aggressive = _aggressive_extra_tickers()
    return _unique_tickers(WATCHLIST, expansion, aggressive, list(DEFAULT_WATCHLIST[:20]))


def _event_artifacts(study: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = dict(study.get("artifacts") or {})
    event_payload = dict(study.get("event_impact") or {})
    return {
        "event_impact_path": artifacts.get("event_path") or "",
        "event_analog_report": artifacts.get("analog_report_path") or "",
        "event_impact_enabled": bool(event_payload),
        "event_summary_count": len(list(event_payload.get("summaries") or [])),
        "best_event_impact": event_payload.get("best_event_impact") or {},
    }


def _direction_supported(proposal: Dict[str, Any]) -> bool:
    direction = str(proposal.get("direction_bias") or "long_bias")
    if direction == "long_bias":
        return True
    if direction != "short_bias":
        return False
    event = dict(proposal.get("event_impact") or {})
    return (
        str(proposal.get("setup_type") or "") == "event_dislocation"
        or _safe_float(event.get("impact_score")) >= PROBE_EVENT_SCORE_MIN
        or bool(proposal.get("paper_order_candidate"))
    )


def _news_support_ok(news_evidence: str, ticker_news: Dict[str, Any], proposal: Dict[str, Any]) -> bool:
    if news_evidence in {"good", "partial"} and int(ticker_news.get("headline_count") or 0) > 0:
        return True
    event = dict(proposal.get("event_impact") or {})
    return _safe_float(event.get("impact_score")) >= PROBE_EVENT_SCORE_MIN


def _explicit_watchlist_contains(ticker: str) -> bool:
    raw = os.getenv("APOLLO_WATCHLIST", "")
    if not raw.strip():
        return False
    return ticker.upper() in {part.strip().upper() for part in raw.split(",") if part.strip()}


def _high_risk_standard_allowed(ticker: str) -> Dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    high_risk = symbol in HIGH_RISK_REVIEW_TICKERS
    explicit = _explicit_watchlist_contains(symbol)
    allowed = (not high_risk) or explicit
    return {
        "ticker": symbol,
        "high_risk_review": high_risk,
        "explicit_watchlist_override": explicit,
        "standard_allowed": allowed,
        "reason": "" if allowed else "high_risk_review_default_holdout",
    }


def _proposal_price_action_confirmation(proposal: Dict[str, Any]) -> Dict[str, Any]:
    setup = str(proposal.get("setup_type") or "").strip().lower()
    if setup != "pullback_to_trend":
        return {"ok": True, "required": False, "reason": "not_pullback_setup"}
    row = dict(proposal.get("price_action_confirmation") or {})
    if not row:
        return {"ok": False, "required": True, "reason": "missing_price_action_confirmation"}
    return {
        "ok": bool(row.get("ok")),
        "required": True,
        "reason": str(row.get("reason") or ("confirmed_recovery" if row.get("ok") else "blocked_price_action_confirmation")),
        **row,
    }


def _proposal_breakout_volume_confirmation(proposal: Dict[str, Any]) -> Dict[str, Any]:
    setup = str(proposal.get("setup_type") or "").strip().lower()
    if setup != "base_breakout":
        return {"ok": True, "required": False, "reason": "not_breakout_setup"}
    row = dict(proposal.get("volume_confirmation") or {})
    if not row:
        return {"ok": False, "required": True, "reason": "blocked_breakout_volume_confirmation"}
    return {
        "ok": bool(row.get("ok")),
        "required": True,
        "reason": str(row.get("reason") or ("confirmed_breakout_volume" if row.get("ok") else "blocked_breakout_volume_confirmation")),
        **row,
    }


def _sentiment_standard_allowed(ticker_news: Dict[str, Any]) -> Dict[str, Any]:
    backend = str(ticker_news.get("sentiment_backend") or "").strip()
    label = str(ticker_news.get("sentiment_label") or "").strip().lower()
    confidence = _safe_float(ticker_news.get("sentiment_confidence"), default=1.0 if not backend else 0.0)
    standard_eligible = bool(ticker_news.get("standard_sentiment_eligible"))
    cap = ""
    if not standard_eligible:
        cap = str(ticker_news.get("standard_cap_reason") or "news_provenance_caps_standard")
    elif label in {"mixed", "neutral"} and confidence < 0.65:
        cap = "sentiment_low_confidence_caps_standard"
    return {
        "ok": not bool(cap),
        "sentiment_backend": backend or "unknown",
        "sentiment_label": label or "unknown",
        "sentiment_confidence": confidence,
        "standard_sentiment_eligible": standard_eligible,
        "sentiment_consensus_ratio": _safe_float(ticker_news.get("sentiment_consensus_ratio")),
        "directional_headline_count": int(ticker_news.get("directional_headline_count") or 0),
        "independent_domain_count": int(ticker_news.get("independent_domain_count") or 0),
        "unique_story_count": int(ticker_news.get("unique_story_count") or 0),
        "eligibility_path": str(ticker_news.get("eligibility_path") or "none"),
        "cap_reason": cap,
    }


def _candidate_readiness_score(check: Dict[str, Any]) -> float:
    rr = _safe_float(check.get("rr"))
    score = _safe_float(check.get("blended_score"))
    event_score = _safe_float(check.get("event_impact_score"))
    liq = 20.0 if (check.get("liquidity") or {}).get("ok") else 0.0
    entry = 20.0 if (check.get("entry_valid") or {}).get("ok") else 0.0
    tier_bonus = {"standard": 100.0, "aggressive_probe": 65.0, "probe": 45.0, "watch": 0.0, "reject": -50.0}.get(str(check.get("risk_tier")), 0.0)
    return round(tier_bonus + score + min(rr, 3.0) * 12.0 + event_score * 0.2 + liq + entry, 3)


def _risk_notes(
    *,
    liquidity: Dict[str, Any],
    entry_val: Dict[str, Any],
    rr: float,
    regime_ok: bool,
    direction_ok: bool,
    news_ok: bool,
    no_trade_reason: str,
    sizing: Optional[Dict[str, Any]],
    price_action: Optional[Dict[str, Any]] = None,
    volume_confirmation: Optional[Dict[str, Any]] = None,
    sentiment_gate: Optional[Dict[str, Any]] = None,
    high_risk_gate: Optional[Dict[str, Any]] = None,
    geometry: Optional[Dict[str, Any]] = None,
) -> List[str]:
    notes: List[str] = []
    if no_trade_reason:
        notes.append(no_trade_reason)
    if not liquidity.get("ok", False):
        notes.append("liquidity_below_minimum")
    if not entry_val.get("ok", False):
        notes.append(str(entry_val.get("reason") or "entry_zone_not_valid"))
    if geometry and not geometry.get("ok", False):
        notes.append(str(geometry.get("reason") or "invalid_trade_geometry"))
    if rr < STANDARD_MIN_RR:
        notes.append(f"risk_reward_below_standard:{rr:.2f}")
    if not regime_ok:
        notes.append("regime_score_below_risk_off_minimum")
    if not direction_ok:
        notes.append("unsupported_direction")
    if not news_ok:
        notes.append("fresh_news_or_event_evidence_missing")
    if sizing is None:
        notes.append("position_sizing_unavailable_or_over_cap")
    if price_action and price_action.get("required") and not price_action.get("ok"):
        notes.append(str(price_action.get("reason") or "blocked_price_action_confirmation"))
    if volume_confirmation and volume_confirmation.get("required") and not volume_confirmation.get("ok"):
        notes.append(str(volume_confirmation.get("reason") or "blocked_breakout_volume_confirmation"))
    if sentiment_gate and not sentiment_gate.get("ok"):
        notes.append(str(sentiment_gate.get("cap_reason") or "sentiment_caps_standard"))
    if high_risk_gate and not high_risk_gate.get("standard_allowed"):
        notes.append(str(high_risk_gate.get("reason") or "high_risk_review_default_holdout"))
    return notes or ["passes_all_standard_gates"]


def _probe_next_action(check: Dict[str, Any]) -> str:
    rr = _safe_float(check.get("rr"))
    if rr < PROBE_MIN_RR:
        return "watch_only_until_entry_or_target_geometry_improves"
    if rr < 0.80:
        return "simulation_probe_only_with_tight_invalidation"
    return "simulation_probe_or_wait_for_entry_reset"


# ── Stage 1: HippoRAG Research ───────────────────────────────────────────────

def _stage_hipporag_research(run_dir: Path) -> Dict[str, Any]:
    _log("Stage 1: HippoRAG Research — gathering equity news and building KG")
    _log("  note: focus_universe_enabled=False is intentional — trade cycle manages its own"
         " ticker scope via equity_news_tickers; self_check decision_mode=disabled is expected")
    from Apollo.nightly_pipeline import run_pipeline

    model_policy = resolve_model_policy(probe=False)
    discovery_tickers = _paper_discovery_tickers()
    payload = {
        "topic": (
            "swing trade opportunities in US equities — identify high-probability setups "
            f"in {', '.join(discovery_tickers)} based on current macro conditions, "
            "sector momentum, earnings catalysts, and recent Fed policy signals"
        ),
        "max_articles": 8,
        "allow_web": True,
        "allow_inbox": False,
        "hipporag_use_llm": False,          # pattern mode — fast, no LLM needed
        "hipporag_batch_size": 25,
        "ocr_max_pages": 4,
        "ocr_max_chars": 12000,
        "ocr_route_mode": "quality_first",
        "focus_universe_enabled": False,    # intentional: trade cycle uses equity_news_tickers
        "equity_news_tickers": discovery_tickers,   # ticker-specific query injection + triage filter
        "hipporag_llm_model": os.getenv("APOLLO_HIPPORAG_LLM_MODEL", model_policy["fast_model"]["model"]),
        "run_id": f"trade_cycle_{datetime.now().strftime('%Y%m%d_%H%M')}",
    }

    _log("  Running nightly pipeline (gather + OCR + HippoRAG pattern mode)…")
    try:
        result = run_pipeline(payload)
    except Exception as exc:
        _log(f"  Pipeline error: {exc} — continuing with existing KG")
        result = {"ok": False, "error": str(exc)}

    new_triples = (result.get("stage_details") or {}).get("hipporag", {}).get("triples", 0)
    gather_details = (result.get("stage_details") or {}).get("gather", {}) or {}
    gathered = gather_details.get("accepted_sources", 0)
    enrichment_skipped = gathered == 0 and new_triples == 0
    if enrichment_skipped:
        _log("  WARNING: 0 sources gathered and 0 triples produced — KG was NOT enriched this cycle")
        _log("  Downstream stages will use existing KG state only")
    stage_result = {
        "stage": "hipporag_research",
        "ok": bool(result.get("ok")),
        "run_id": result.get("run_id"),
        "confidence_band": (result.get("quality") or {}).get("confidence_band", {}).get("label") or "unknown",
        "new_triples": new_triples,
        "gathered_sources": gathered,
        "gather_source_confidence": gather_details.get("source_confidence") or "unknown",
        "trade_confidence_cap": gather_details.get("trade_confidence_cap") or "unknown",
        "standard_trade_ready_allowed": bool(gather_details.get("standard_trade_ready_allowed", True)),
        "c_only_gather": bool(gather_details.get("c_only_run")),
        "evidence_lanes": dict(gather_details.get("evidence_lanes") or {}),
        "candidate_evidence_packs": list(gather_details.get("candidate_evidence_packs") or [])[:12],
        "enrichment_skipped": enrichment_skipped,
        "enrichment_reason": "gather_produced_0_sources" if enrichment_skipped else "",
        "completed_at": _utc_now(),
        "raw": {k: v for k, v in result.items() if k not in ("documents", "sources", "proposals")},
    }
    _write_json(run_dir / "01_hipporag.json", stage_result)
    _log(f"  HippoRAG done — ok={stage_result['ok']} band={stage_result['confidence_band']} "
         f"triples={stage_result['new_triples']} sources={stage_result['gathered_sources']}")
    return stage_result


# ── Stage 2: Market Research ─────────────────────────────────────────────────

def _stage_market_research(run_dir: Path) -> Dict[str, Any]:
    _log("Stage 2: Market Research — swing study with fresh KG")
    from Apollo.swing_study import build_swing_study

    research_tickers = _event_research_tickers()
    os.environ.setdefault("APOLLO_EVENT_IMPACT_ENABLED", "1")
    os.environ.setdefault("APOLLO_EVENT_ALLOW_PAPER_CANDIDATES", "1")
    study = build_swing_study({
        "tickers": ",".join(research_tickers),
        "notify": False,
        "write_artifacts": True,
        "refresh": True,
        "include_event_impact": True,
        "event_max_results": 10,
    })

    proposals = list(study.get("proposals") or [])
    regime = study.get("market_regime") or {}
    event_artifacts = _event_artifacts(study)

    stage_result = {
        "stage": "market_research",
        "ok": bool(study.get("ok")),
        "market_regime": regime,
        "research_tickers": research_tickers,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "top_ticker": proposals[0].get("ticker") if proposals else None,
        "event_impact": event_artifacts,
        "completed_at": _utc_now(),
    }
    _write_json(run_dir / "02_market.json", stage_result)
    _log(f"  Market done — regime={regime.get('label')} proposals={len(proposals)} "
         f"top={stage_result['top_ticker']}")
    return stage_result


# ── Stage 3: News Sampling + Weighted Sentiment ───────────────────────────────

def _headline_sentiment(text: str) -> float:
    lower = text.lower()
    pos = sum(1 for kw in _POSITIVE_KW if kw in lower)
    neg = sum(1 for kw in _NEGATIVE_KW if kw in lower)
    total = pos + neg
    if not total:
        return 0.0
    return round((pos - neg) / max(total, 1), 3)


def _model_headline_sentiment(text: str) -> Dict[str, Any]:
    """Return signed sentiment with local model preferred and keyword fallback capped."""
    try:
        from Apollo.event_impact import _model_sentiment

        row = dict(_model_sentiment(text))
    except Exception:
        row = {}
    mode = str(row.get("mode") or "").strip().lower()
    if not row or mode != "model":
        keyword_score = _headline_sentiment(text)
        return {
            "signed_score": keyword_score,
            "label": "positive" if keyword_score > 0 else ("negative" if keyword_score < 0 else "neutral"),
            "confidence": abs(keyword_score) if keyword_score else 0.5,
            "backend": "keyword_fallback",
            "fallback_reason": row.get("fallback_reason") if row else "model_unavailable",
        }
    label = str(row.get("label") or "neutral").strip().lower()
    confidence = _safe_float(row.get("score"), default=0.0)
    signed = confidence if label == "positive" else (-confidence if label == "negative" else 0.0)
    return {
        "signed_score": round(max(-1.0, min(1.0, signed)), 3),
        "label": label,
        "confidence": round(confidence, 3),
        "backend": "local_model",
        "model_id": row.get("model_id") or "",
    }


def _fetch_ticker_news(ticker: str, max_items: int = 10) -> List[Dict[str, Any]]:
    import feedparser
    import requests
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        feed = feedparser.parse(r.content)
        items = []
        for entry in feed.entries[:max_items]:
            pub = entry.get("published", "")
            text = entry.get("title", "") + " " + entry.get("summary", "")
            sentiment = _model_headline_sentiment(text)
            source = entry.get("source") or {}
            source_href = source.get("href", "") if isinstance(source, dict) else ""
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "link": entry.get("link", ""),
                "published": pub,
                "publisher": source.get("title", "") if isinstance(source, dict) else str(source or ""),
                "origin_domain": normalize_domain(source_href),
                "source_tier": "C",
                "evidence_lane": "market_news_evidence",
                "sentiment": sentiment["signed_score"],
                "sentiment_label": sentiment.get("label"),
                "sentiment_confidence": sentiment.get("confidence"),
                "sentiment_backend": sentiment.get("backend"),
                "sentiment_detail": sentiment,
            })
        return items
    except Exception:
        return []


def _news_freshness_weight(published_str: str) -> float:
    return freshness_weight(published_str)


def _aggregate_ticker_news_sentiment(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    ticker = str(items[0].get("ticker") or "") if items else ""
    return aggregate_news_evidence(items, ticker=ticker)


def _news_items_from_evidence_pack(ticker: str, pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for source in list(pack.get("sources") or []):
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or source.get("why") or "").strip()
        if not title:
            continue
        text = f"{title} {source.get('snippet') or ''}"
        sentiment = _model_headline_sentiment(text)
        items.append({
            "ticker": ticker,
            "title": title,
            "url": source.get("url") or "",
            "published": source.get("published") or source.get("published_at") or _utc_now(),
            "origin_domain": source.get("domain") or "",
            "source_tier": source.get("tier") or pack.get("best_source_tier") or "D",
            "evidence_lane": source.get("evidence_lane") or "",
            "sentiment": sentiment["signed_score"],
            "sentiment_label": sentiment.get("label"),
            "sentiment_confidence": sentiment.get("confidence"),
            "sentiment_backend": sentiment.get("backend"),
            "sentiment_detail": sentiment,
        })
    return items


def _stage_news_analysis(
    run_dir: Path,
    market_result: Dict,
    hipporag_result: Optional[Dict[str, Any]] = None,
    *,
    news_fetcher: Optional[Any] = None,
) -> Dict[str, Any]:
    _log("Stage 3: News Analysis — sampling headlines, weighted sentiment")
    proposals = list(market_result.get("proposals") or [])
    tickers = [p.get("ticker") for p in proposals if p.get("ticker")] or WATCHLIST

    packs = {
        str(pack.get("ticker") or "").upper(): pack
        for pack in list((hipporag_result or {}).get("candidate_evidence_packs") or [])
        if isinstance(pack, dict) and pack.get("ticker")
    }
    fetcher = news_fetcher or _fetch_ticker_news
    news_by_ticker: Dict[str, Any] = {}
    for ticker in tickers:
        items = _news_items_from_evidence_pack(ticker, packs.get(str(ticker).upper(), {}))
        items.extend(fetcher(ticker, max_items=8))
        if not items:
            news_by_ticker[ticker] = _aggregate_ticker_news_sentiment([])
            continue
        news_by_ticker[ticker] = _aggregate_ticker_news_sentiment(items)
        _log(f"  {ticker}: {len(items)} headlines, weighted_sentiment={news_by_ticker[ticker]['weighted_sentiment']:+.3f}")

    # Blend news sentiment into proposal scores (30% weight)
    blended_proposals = []
    stress_config = stress_mode.resolve_stress_mode(PORTFOLIO_SIZE)
    for p in proposals:
        ticker = p.get("ticker", "")
        tech_score = float(p.get("score") or 0)
        news = news_by_ticker.get(ticker, {})
        ws = float(news.get("weighted_sentiment", 0.0))
        # Normalize ws from [-1,+1] to [0,100] scale contribution
        news_score_contrib = 50.0 + ws * 50.0
        blended_score = round(0.70 * tech_score + 0.30 * news_score_contrib, 2)
        bp = dict(p)
        bp["news_weighted_sentiment"] = ws
        bp["news_blended_score"] = blended_score
        bp["score_delta_from_news"] = round(blended_score - tech_score, 2)
        blended_proposals.append(bp)

    blended_proposals.sort(
        key=lambda x: (
            1 if x.get("direction_bias") == "long_bias" and not x.get("no_trade_reason") else 0,
            x.get("news_blended_score", 0),
        ),
        reverse=True,
    )

    tickers_with_news = sum(1 for v in news_by_ticker.values() if int(v.get("headline_count") or 0) > 0)
    if tickers_with_news == 0:
        _log("  WARNING: 0 tickers returned headlines — sentiment blend has no evidence")
    news_evidence = "good" if tickers_with_news >= 3 else ("partial" if tickers_with_news > 0 else "none")
    eligibility_paths = [str(row.get("eligibility_path") or "none") for row in news_by_ticker.values()]
    independent_domains = max((int(row.get("independent_domain_count") or 0) for row in news_by_ticker.values()), default=0)
    unique_stories = max((int(row.get("unique_story_count") or 0) for row in news_by_ticker.values()), default=0)
    deduplicated_count = sum(int(row.get("deduplicated_count") or 0) for row in news_by_ticker.values())
    if "trusted_ab" in eligibility_paths:
        source_quality = "trusted_diverse"
        evidence_confidence = "high"
    elif "diverse_c" in eligibility_paths:
        source_quality = "diverse_c"
        evidence_confidence = "medium"
    elif tickers_with_news > 0:
        source_quality = "c_tier_only_yahoo_rss" if independent_domains == 0 else "insufficiently_diverse"
        evidence_confidence = "low"
    else:
        source_quality = "none"
        evidence_confidence = "none"

    stage_result = {
        "stage": "news_analysis",
        "ok": tickers_with_news > 0,
        "news_evidence": news_evidence,
        "source_quality": source_quality,
        "evidence_confidence": evidence_confidence,
        "independent_domain_count": independent_domains,
        "unique_story_count": unique_stories,
        "deduplicated_count": deduplicated_count,
        "eligibility_paths": eligibility_paths,
        "tickers_with_news": tickers_with_news,
        "tickers_empty": len(tickers) - tickers_with_news,
        "news_by_ticker": news_by_ticker,
        "blended_proposals": blended_proposals,
        "top_ticker": blended_proposals[0].get("ticker") if blended_proposals else None,
        "completed_at": _utc_now(),
    }
    _write_json(run_dir / "03_news.json", stage_result)
    _log(f"  News done — top after blend: {stage_result['top_ticker']} evidence={news_evidence}")
    return stage_result


# ── Stage 4: Risk Checks ──────────────────────────────────────────────────────

def _position_sizing(
    entry_price: float,
    stop: float,
    *,
    direction_bias: str = "long_bias",
    risk_pct: float = RISK_PCT,
    max_notional_pct: float = STANDARD_MAX_NOTIONAL_PCT,
    risk_tier: str = "",
) -> Optional[Dict[str, Any]]:
    if entry_price <= 0 or stop <= 0:
        return None
    if direction_bias == "short_bias":
        risk_per_share = stop - entry_price
    else:
        risk_per_share = entry_price - stop
    if risk_per_share <= 0:
        return None
    dollar_risk = PORTFOLIO_SIZE * risk_pct
    shares = dollar_risk / risk_per_share
    max_notional = PORTFOLIO_SIZE * max_notional_pct
    capped_shares = max_notional / entry_price
    if capped_shares <= 0:
        return None
    original_shares = shares
    shares = min(shares, capped_shares)
    if shares <= 0:
        return None
    total_cost = round(shares * entry_price, 2)
    return {
        "portfolio_size": PORTFOLIO_SIZE,
        "risk_pct": risk_pct,
        "risk_tier": risk_tier or ("probe" if risk_pct <= PROBE_RISK_PCT else "standard"),
        "direction_bias": direction_bias,
        "dollar_risk_target": round(dollar_risk, 2),
        "max_notional_pct": max_notional_pct,
        "max_notional_dollars": round(max_notional, 2),
        "entry_price": entry_price,
        "stop": stop,
        "risk_per_share": round(risk_per_share, 4),
        "shares": round(shares, 4),
        "uncapped_shares": round(original_shares, 4),
        "capped_by_notional": shares < original_shares,
        "total_cost": total_cost,
        "pct_of_portfolio": round(total_cost / PORTFOLIO_SIZE * 100, 2),
        "max_loss_dollars": round(shares * risk_per_share, 2),
    }


def _target_price_for_direction(target_zone: str, direction_bias: str) -> float:
    from Apollo.swing_simulation import _parse_zone, _parse_price

    low, high = _parse_zone(target_zone)
    if low and high:
        return low if direction_bias == "short_bias" else high
    return _parse_price(str(target_zone or ""))


def _setup_geometry_valid(entry_price: float, stop: float, target: float, direction_bias: str) -> Dict[str, Any]:
    if entry_price <= 0 or stop <= 0 or target <= 0:
        return {"ok": False, "reason": "missing_entry_stop_or_target"}
    if direction_bias == "short_bias":
        if stop <= entry_price:
            return {"ok": False, "reason": "invalid_short_stop_below_entry"}
        if target >= entry_price:
            return {"ok": False, "reason": "invalid_short_target_above_entry"}
    else:
        if stop >= entry_price:
            return {"ok": False, "reason": "invalid_long_stop_above_entry"}
        if target <= entry_price:
            return {"ok": False, "reason": "invalid_long_target_below_entry"}
    return {"ok": True, "reason": "valid_geometry"}


def _volume_ratio_from_confirmation(volume_confirmation: Dict[str, Any]) -> float:
    return _safe_float(volume_confirmation.get("volume_ratio"))


def _pressed_aggressive_profile(
    proposal: Dict[str, Any],
    *,
    rr: float,
    event_score: float,
    volume_ratio: float,
) -> Dict[str, Any]:
    graph = dict(proposal.get("graph_risk_appetite") or {})
    reward = dict(proposal.get("aggressive_reward_profile") or {})
    graph_score = _safe_float(graph.get("score"))
    enabled = bool(PRESSED_AGGRESSIVE_ENABLED)
    evidence_ok = (
        bool(reward.get("pressed_candidate"))
        or bool(graph.get("use_for_aggressive_reward"))
        or graph_score >= PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE
    )
    catalyst_ok = event_score >= AGGRESSIVE_MIN_EVENT_SCORE or volume_ratio >= AGGRESSIVE_MIN_VOLUME_RATIO
    ok = bool(enabled and rr >= PRESSED_AGGRESSIVE_MIN_RR and graph_score >= PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE and evidence_ok and catalyst_ok)
    reasons: List[str] = []
    if ok:
        reasons.append("pressed_graph_backed_asymmetry")
    if graph_score >= PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE:
        reasons.append("graph_rag_asymmetric_support")
    if rr >= PRESSED_AGGRESSIVE_MIN_RR:
        reasons.append("rr_above_pressed_threshold")
    if catalyst_ok:
        reasons.append("event_or_volume_confirmation")
    return {
        "ok": ok,
        "enabled": enabled,
        "graph_score": graph_score,
        "min_graph_score": PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE,
        "min_rr": PRESSED_AGGRESSIVE_MIN_RR,
        "risk_pct": PRESSED_AGGRESSIVE_RISK_PCT if ok else AGGRESSIVE_PROBE_RISK_PCT,
        "max_notional_pct": PRESSED_AGGRESSIVE_MAX_NOTIONAL_PCT if ok else AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT,
        "reasons": reasons,
        "graph_conviction": graph.get("conviction") or "",
        "extension": dict(reward.get("extension") or {}),
    }


def _aggressive_confirmation(
    proposal: Dict[str, Any],
    *,
    rr: float,
    score: float,
    event_score: float,
    price_action: Dict[str, Any],
    volume_confirmation: Dict[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []
    volume_ratio = _volume_ratio_from_confirmation(volume_confirmation)
    if rr >= AGGRESSIVE_MIN_RR:
        reasons.append("rr_asymmetric")
    if event_score >= AGGRESSIVE_MIN_EVENT_SCORE:
        reasons.append("event_catalyst")
    if volume_ratio >= AGGRESSIVE_MIN_VOLUME_RATIO:
        reasons.append("volume_expansion")
    graph_profile = dict(proposal.get("graph_risk_appetite") or {})
    if _safe_float(graph_profile.get("score")) >= PRESSED_AGGRESSIVE_MIN_GRAPH_SCORE:
        reasons.append("graph_rag_asymmetric_support")
    if price_action.get("required") and price_action.get("ok"):
        reasons.append("pullback_recovery")
    if bool(proposal.get("paper_order_candidate")):
        reasons.append("paper_event_candidate")
    score_ok = score >= AGGRESSIVE_MIN_SCORE
    return {
        "ok": bool(reasons) and score_ok,
        "reasons": reasons,
        "score_ok": score_ok,
        "min_score": AGGRESSIVE_MIN_SCORE,
        "min_rr": AGGRESSIVE_MIN_RR,
        "min_event_score": AGGRESSIVE_MIN_EVENT_SCORE,
        "min_volume_ratio": AGGRESSIVE_MIN_VOLUME_RATIO,
        "volume_ratio": volume_ratio,
    }


def _aggressive_next_action(
    *,
    aggressive_ok: bool,
    entry_val: Dict[str, Any],
    geometry: Dict[str, Any],
    rr: float,
    confirmation: Dict[str, Any],
    volume_confirmation: Dict[str, Any],
) -> str:
    if aggressive_ok:
        return "paper_aggressive_ready"
    if not entry_val.get("ok"):
        reason = str(entry_val.get("reason") or "")
        if reason.startswith("price_above_zone") or reason.startswith("price_below_zone"):
            return "wait_for_entry_reset"
        return "blocked_invalid_price"
    if not geometry.get("ok"):
        return "blocked_invalid_price"
    if rr < AGGRESSIVE_MIN_RR:
        return "blocked_low_reward"
    if volume_confirmation.get("required") and not volume_confirmation.get("ok"):
        return "watch_for_volume_confirmation"
    if not confirmation.get("ok"):
        return "blocked_weak_evidence"
    return "wait_for_entry_reset"


def _liquidity_ok(ticker: str) -> Dict[str, Any]:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="30d")
        avg_vol = float(hist["Volume"].mean()) if len(hist) > 0 else 0.0
        return {
            "ticker": ticker,
            "avg_volume_30d": int(avg_vol),
            "min_required": MIN_AVG_VOLUME,
            "ok": avg_vol >= MIN_AVG_VOLUME,
        }
    except Exception:
        return {"ticker": ticker, "avg_volume_30d": 0, "ok": False, "error": "fetch_failed"}


def _entry_valid(ticker: str, entry_low: float, entry_high: float) -> Dict[str, Any]:
    """Check if entry zone is still reachable — price hasn't blown far past it."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2d")
        if hist.empty:
            return {"ok": False, "reason": "no_price_data"}
        live = float(hist["Close"].iloc[-1])
        if not math.isfinite(live) or live <= 0:
            return {"ok": False, "reason": "invalid_live_price", "live": live}
        # Consider entry reachable if price is within 5% above the top of zone
        ceiling = entry_high * 1.05
        below_floor = live < entry_low * 0.95
        above_ceiling = live > ceiling
        if above_ceiling:
            return {"ok": False, "reason": f"price_above_zone: live={live:.2f} > ceiling={ceiling:.2f}", "live": live}
        if below_floor:
            return {"ok": False, "reason": f"price_below_zone: live={live:.2f} < floor={entry_low * 0.95:.2f}", "live": live}
        return {"ok": True, "live": live, "entry_low": entry_low, "entry_high": entry_high}
    except Exception as exc:
        return {"ok": False, "reason": "price_fetch_failed", "error": str(exc)}


def _stage_risk_checks_legacy(run_dir: Path, news_result: Dict, market_result: Dict) -> Dict[str, Any]:
    _log("Stage 4: Risk Checks — position sizing, entry validity, liquidity")
    proposals = list(news_result.get("blended_proposals") or market_result.get("proposals") or [])
    regime_label = (market_result.get("market_regime") or {}).get("label") or "unknown"
    risk_off = "risk_off" in regime_label.lower()

    checks: List[Dict[str, Any]] = []
    for p in proposals[:5]:
        ticker = p.get("ticker", "")
        entry_zone_str = str(p.get("entry_zone") or "")
        stop_str = str(p.get("stop_zone") or "")

        from Apollo.swing_simulation import _parse_zone, _parse_price
        entry_low, entry_high = _parse_zone(entry_zone_str)
        stop = _parse_price(stop_str)
        entry_price = (entry_low + entry_high) / 2 if entry_low and entry_high else 0.0

        sizing = _position_sizing(entry_price, stop) if entry_price and stop else None
        liquidity = _liquidity_ok(ticker)
        entry_val = _entry_valid(ticker, entry_low, entry_high) if entry_low and entry_high else {"ok": False, "reason": "no_zone"}
        rr = float(p.get("risk_reward_estimate") or 0)
        rr_ok = rr >= 1.0

        # Regime gate
        score = float(p.get("news_blended_score") or p.get("score") or 0)
        regime_ok = score >= MIN_REGIME_SCORE_RISK_OFF if risk_off else True

        all_ok = (
            liquidity.get("ok", False)
            and entry_val.get("ok", False)
            and rr_ok
            and regime_ok
            and bool(p.get("direction_bias") == "long_bias")
            and not bool(p.get("no_trade_reason"))
        )

        checks.append({
            "ticker": ticker,
            "all_checks_pass": all_ok,
            "direction_bias": p.get("direction_bias"),
            "no_trade_reason": p.get("no_trade_reason") or "",
            "rr": rr,
            "rr_ok": rr_ok,
            "liquidity": liquidity,
            "entry_valid": entry_val,
            "regime_ok": regime_ok,
            "position_sizing": sizing,
            "blended_score": p.get("news_blended_score") or p.get("score"),
        })
        status = "PASS" if all_ok else "FAIL"
        _log(f"  {ticker}: {status} | rr={rr} liq={liquidity.get('ok')} entry_valid={entry_val.get('ok')} regime_ok={regime_ok}")

    # Pick best candidate that passes all checks
    passing = [c for c in checks if c["all_checks_pass"]]
    top = passing[0] if passing else None

    stage_result = {
        "stage": "risk_checks",
        "ok": bool(top),
        "regime_label": regime_label,
        "risk_off": risk_off,
        "checks": checks,
        "top_candidate": top,
        "completed_at": _utc_now(),
    }
    _write_json(run_dir / "04_risk.json", stage_result)
    _log(f"  Risk done — passing candidates: {len(passing)} | top: {top['ticker'] if top else 'none'}")
    return stage_result


# ── Stage 5: Simulation + Observation Report ─────────────────────────────────

def _stage_risk_checks(run_dir: Path, news_result: Dict, market_result: Dict) -> Dict[str, Any]:
    _log("Stage 4: Risk Checks - full proposal scan with standard/probe tiers")
    proposals = list(news_result.get("blended_proposals") or market_result.get("proposals") or [])
    regime_label = (market_result.get("market_regime") or {}).get("label") or "unknown"
    risk_off = "risk_off" in regime_label.lower()
    news_evidence = str(news_result.get("news_evidence") or "unknown")
    news_by_ticker = dict(news_result.get("news_by_ticker") or {})
    stress_config = stress_mode.resolve_stress_mode(PORTFOLIO_SIZE)

    checks: List[Dict[str, Any]] = []
    for p in proposals:
        ticker = str(p.get("ticker") or "").upper()
        entry_zone_str = str(p.get("entry_zone") or "")
        stop_str = str(p.get("stop_zone") or "")
        direction_bias = str(p.get("direction_bias") or "long_bias")

        from Apollo.swing_simulation import _parse_zone, _parse_price
        entry_low, entry_high = _parse_zone(entry_zone_str)
        stop = _parse_price(stop_str)
        entry_price = (entry_low + entry_high) / 2 if entry_low and entry_high else 0.0

        standard_sizing = _position_sizing(
            entry_price,
            stop,
            direction_bias=direction_bias,
            risk_pct=RISK_PCT,
            max_notional_pct=STANDARD_MAX_NOTIONAL_PCT,
        ) if entry_price and stop else None
        probe_sizing = _position_sizing(
            entry_price,
            stop,
            direction_bias=direction_bias,
            risk_pct=PROBE_RISK_PCT,
            max_notional_pct=PROBE_MAX_NOTIONAL_PCT,
            risk_tier="probe",
        ) if entry_price and stop else None
        aggressive_sizing = _position_sizing(
            entry_price,
            stop,
            direction_bias=direction_bias,
            risk_pct=AGGRESSIVE_PROBE_RISK_PCT,
            max_notional_pct=AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT,
            risk_tier="aggressive_probe",
        ) if entry_price and stop else None
        liquidity = _liquidity_ok(ticker)
        entry_val = _entry_valid(ticker, entry_low, entry_high) if entry_low and entry_high else {"ok": False, "reason": "no_zone"}
        rr = _safe_float(p.get("risk_reward_estimate"))
        rr_ok = rr >= STANDARD_MIN_RR
        score = _safe_float(p.get("news_blended_score") or p.get("score"))
        regime_ok = score >= MIN_REGIME_SCORE_RISK_OFF if risk_off else True
        event_score = _safe_float((p.get("event_impact") or {}).get("impact_score"))
        direction_ok = _direction_supported(p)
        no_trade_reason = str(p.get("no_trade_reason") or "")
        ticker_news = dict(news_by_ticker.get(ticker) or {})
        news_ok = _news_support_ok(news_evidence, ticker_news, p)
        price_action = _proposal_price_action_confirmation(p)
        volume_confirmation = _proposal_breakout_volume_confirmation(p)
        sentiment_gate = _sentiment_standard_allowed(ticker_news)
        high_risk_gate = _high_risk_standard_allowed(ticker)
        target = _target_price_for_direction(str(p.get("target_zone") or ""), direction_bias)
        geometry = _setup_geometry_valid(entry_price, stop, target, direction_bias)
        stress_sizing = (
            stress_mode.stress_position_sizing(
                entry_price=entry_price,
                stop=stop,
                direction_bias=direction_bias,
                portfolio_size=PORTFOLIO_SIZE,
                config=stress_config,
            )
            if stress_config.get("enabled") and entry_price and stop
            else None
        )
        volume_ratio = _volume_ratio_from_confirmation(volume_confirmation)
        pressed_profile = _pressed_aggressive_profile(p, rr=rr, event_score=event_score, volume_ratio=volume_ratio)
        if pressed_profile.get("ok") and entry_price and stop:
            aggressive_sizing = _position_sizing(
                entry_price,
                stop,
                direction_bias=direction_bias,
                risk_pct=_safe_float(pressed_profile.get("risk_pct"), AGGRESSIVE_PROBE_RISK_PCT),
                max_notional_pct=_safe_float(pressed_profile.get("max_notional_pct"), AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT),
                risk_tier="aggressive_probe",
            )

        standard_ok = (
            liquidity.get("ok", False)
            and entry_val.get("ok", False)
            and geometry.get("ok", False)
            and rr_ok
            and regime_ok
            and direction_ok
            and news_ok
            and bool(price_action.get("ok"))
            and bool(volume_confirmation.get("ok"))
            and bool(sentiment_gate.get("ok"))
            and bool(high_risk_gate.get("standard_allowed"))
            and bool(standard_sizing)
            and not bool(no_trade_reason)
        )
        probe_score_ok = score >= PROBE_SCORE_MIN or event_score >= PROBE_EVENT_SCORE_MIN or bool(p.get("paper_order_candidate"))
        probe_ok = (
            not standard_ok
            and liquidity.get("ok", False)
            and entry_val.get("ok", False)
            and regime_ok
            and direction_ok
            and news_ok
            and probe_score_ok
            and rr >= PROBE_MIN_RR
            and geometry.get("ok", False)
            and bool(probe_sizing)
            and not bool(no_trade_reason)
        )
        aggressive_confirmation = _aggressive_confirmation(
            p,
            rr=rr,
            score=score,
            event_score=event_score,
            price_action=price_action,
            volume_confirmation=volume_confirmation,
        )
        aggressive_ok = (
            AGGRESSIVE_PAPER_ENABLED
            and not standard_ok
            and liquidity.get("ok", False)
            and entry_val.get("ok", False)
            and geometry.get("ok", False)
            and rr >= AGGRESSIVE_MIN_RR
            and regime_ok
            and direction_ok
            and news_ok
            and bool(aggressive_sizing)
            and bool(aggressive_confirmation.get("ok"))
            and not bool(no_trade_reason)
        )
        stress_ok = (
            bool(stress_config.get("enabled"))
            and liquidity.get("ok", False)
            and geometry.get("ok", False)
            and bool(stress_sizing)
            and not bool(no_trade_reason)
        )
        probe_ready = probe_ok and not aggressive_ok
        risk_tier = (
            "standard"
            if standard_ok
            else ("aggressive_probe" if aggressive_ok else ("probe" if probe_ok else ("watch" if direction_ok and news_ok else "reject")))
        )
        active_sizing = (
            standard_sizing
            if risk_tier == "standard"
            else (aggressive_sizing if risk_tier == "aggressive_probe" else (probe_sizing if risk_tier == "probe" else standard_sizing))
        )
        aggressive_reasons: List[str] = []
        for reason in list(aggressive_confirmation.get("reasons") or []) + (list(pressed_profile.get("reasons") or []) if pressed_profile.get("ok") else []):
            text = str(reason or "").strip()
            if text and text not in aggressive_reasons:
                aggressive_reasons.append(text)
        aggressive_next_action = _aggressive_next_action(
            aggressive_ok=aggressive_ok,
            entry_val=entry_val,
            geometry=geometry,
            rr=rr,
            confirmation=aggressive_confirmation,
            volume_confirmation=volume_confirmation,
        )
        check: Dict[str, Any] = {
                "ticker": ticker,
                "all_checks_pass": standard_ok,
                "standard_ready": standard_ok,
                "probe_ready": probe_ready,
                "aggressive_probe_ready": aggressive_ok,
                "stress_probe_ready": stress_ok,
            "risk_tier": risk_tier,
            "paper_only": risk_tier in {"probe", "aggressive_probe"},
            "human_approval_required": True,
            "live_trade_execution": False,
            "direction_bias": direction_bias,
            "no_trade_reason": no_trade_reason,
            "rr": rr,
            "rr_ok": rr_ok,
            "liquidity": liquidity,
            "entry_valid": entry_val,
            "price_valid": bool(entry_val.get("ok")),
            "setup_geometry": geometry,
            "regime_ok": regime_ok,
            "direction_ok": direction_ok,
            "news_ok": news_ok,
            "price_action_confirmation": price_action,
            "volume_confirmation": volume_confirmation,
            "sentiment_gate": sentiment_gate,
            "high_risk_review": high_risk_gate,
            "event_impact_score": event_score,
            "news_headline_count": int(ticker_news.get("headline_count") or 0),
            "sentiment_backend": ticker_news.get("sentiment_backend") or "unknown",
            "sentiment_label": ticker_news.get("sentiment_label") or "unknown",
            "sentiment_confidence": ticker_news.get("sentiment_confidence"),
            "position_sizing": active_sizing,
            "standard_position_sizing": standard_sizing,
            "probe_position_sizing": probe_sizing,
            "aggressive_position_sizing": aggressive_sizing,
            "stress_position_sizing": stress_sizing,
            "simulation_stress_mode": bool(stress_config.get("enabled")),
            "stress_mode": stress_config,
            "blended_score": score,
            "paper_risk_pct": (active_sizing or {}).get("risk_pct"),
            "max_notional_pct": (active_sizing or {}).get("max_notional_pct"),
            "pressed_aggressive": bool(pressed_profile.get("ok") and risk_tier == "aggressive_probe"),
            "pressed_aggressive_profile": pressed_profile,
            "graph_risk_appetite": dict(p.get("graph_risk_appetite") or {}),
            "aggressive_reward_profile": dict(p.get("aggressive_reward_profile") or {}),
            "aggressive_confirmation": aggressive_confirmation,
            "aggressive_reason": "; ".join(aggressive_reasons),
            "aggressive_next_action": aggressive_next_action,
            "next_action": (
                "standard_simulation_candidate"
                if standard_ok
                else (aggressive_next_action if risk_tier == "aggressive_probe" else (_probe_next_action({"rr": rr}) if risk_tier == "probe" else "watch_or_reject"))
            ),
        }
        check["risk_notes"] = _risk_notes(
            liquidity=liquidity,
            entry_val=entry_val,
            rr=rr,
            regime_ok=regime_ok,
            direction_ok=direction_ok,
            news_ok=news_ok,
            no_trade_reason=no_trade_reason,
            sizing=active_sizing,
            price_action=price_action,
            volume_confirmation=volume_confirmation,
            sentiment_gate=sentiment_gate,
            high_risk_gate=high_risk_gate,
            geometry=geometry,
        )
        if stress_ok:
            check["production_blockers_ignored_for_stress"] = [
                str(item)
                for item in list(check.get("risk_notes") or [])
                if str(item).strip()
            ] or ["production_trade_gate_not_required_for_stress_sandbox"]
        check["readiness_score"] = _candidate_readiness_score(check)
        checks.append(check)
        status = "STANDARD" if standard_ok else ("AGGRESSIVE" if aggressive_ok else ("PROBE" if probe_ok else "BLOCK"))
        _log(f"  {ticker}: {status} | rr={rr} liq={liquidity.get('ok')} entry_valid={entry_val.get('ok')} regime_ok={regime_ok}")

    checks.sort(key=lambda c: c.get("readiness_score", 0), reverse=True)
    standard_candidates = [c for c in checks if c.get("standard_ready")]
    probe_candidates = [c for c in checks if c.get("probe_ready")]
    aggressive_candidates = [c for c in checks if c.get("aggressive_probe_ready")]
    stress_candidates = [c for c in checks if c.get("stress_probe_ready")]
    entry_reset_candidates = [c for c in checks if c.get("aggressive_next_action") == "wait_for_entry_reset"]
    watch_candidates = [c for c in checks if c.get("risk_tier") == "watch"]
    rejected_candidates = [c for c in checks if c.get("risk_tier") == "reject"]
    top = standard_candidates[0] if standard_candidates else None
    top_probe = probe_candidates[0] if probe_candidates else None
    top_aggressive = aggressive_candidates[0] if aggressive_candidates else None
    top_stress = stress_candidates[0] if stress_candidates else None

    stage_result = {
        "stage": "risk_checks",
        "ok": bool(top),
        "probe_ok": bool(top_probe),
        "aggressive_probe_ok": bool(top_aggressive),
        "stress_probe_ok": bool(top_stress),
        "regime_label": regime_label,
        "risk_off": risk_off,
        "checks": checks,
        "standard_candidates": standard_candidates,
        "probe_candidates": probe_candidates,
        "aggressive_probe_candidates": aggressive_candidates,
        "stress_probe_candidates": stress_candidates,
        "wait_for_entry_reset_candidates": entry_reset_candidates,
        "watch_candidates": watch_candidates,
        "rejected_candidates": rejected_candidates,
        "top_candidate": top,
        "top_probe_candidate": top_probe,
        "top_aggressive_probe_candidate": top_aggressive,
        "top_stress_probe_candidate": top_stress,
        "risk_policy": {
            "standard_min_rr": STANDARD_MIN_RR,
            "probe_min_rr": PROBE_MIN_RR,
            "aggressive_min_rr": AGGRESSIVE_MIN_RR,
            "standard_risk_pct": RISK_PCT,
            "probe_risk_pct": PROBE_RISK_PCT,
            "aggressive_probe_risk_pct": AGGRESSIVE_PROBE_RISK_PCT,
            "standard_max_notional_pct": STANDARD_MAX_NOTIONAL_PCT,
            "probe_max_notional_pct": PROBE_MAX_NOTIONAL_PCT,
            "aggressive_probe_max_notional_pct": AGGRESSIVE_PROBE_MAX_NOTIONAL_PCT,
            "aggressive_paper_enabled": AGGRESSIVE_PAPER_ENABLED,
            "simulation_stress_mode": stress_config,
            "live_trade_execution": False,
        },
        "completed_at": _utc_now(),
    }
    _write_json(run_dir / "04_risk.json", stage_result)
    chosen = top or top_aggressive or top_probe or top_stress
    _log(
        "  Risk done - "
        f"standard={len(standard_candidates)} aggressive={len(aggressive_candidates)} probe={len(probe_candidates)} stress={len(stress_candidates)} "
        f"| top={chosen['ticker'] if chosen else 'none'}"
    )
    return stage_result


def _compact_candidate_for_adjudication(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ticker": row.get("ticker"),
        "risk_tier": row.get("risk_tier"),
        "standard_ready": bool(row.get("standard_ready")),
        "probe_ready": bool(row.get("probe_ready")),
        "aggressive_probe_ready": bool(row.get("aggressive_probe_ready")),
        "stress_probe_ready": bool(row.get("stress_probe_ready")),
        "rr": row.get("rr"),
        "blended_score": row.get("blended_score"),
        "direction_bias": row.get("direction_bias"),
        "event_impact_score": row.get("event_impact_score"),
        "news_headline_count": row.get("news_headline_count"),
        "risk_notes": list(row.get("risk_notes") or [])[:6],
        "next_action": row.get("next_action"),
        "aggressive_next_action": row.get("aggressive_next_action"),
        "aggressive_reason": row.get("aggressive_reason"),
        "position_sizing": row.get("position_sizing") or {},
        "stress_position_sizing": row.get("stress_position_sizing") or {},
    }


def _parse_adjudication_reply(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    # Strip thinking blocks (qwen3 / chain-of-thought models)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
    # Strip markdown code fences
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw).strip()
    # Try direct parse first, then extract first JSON object (handles leading prose)
    payload: Dict[str, Any] = {}
    try:
        payload = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*?\}", raw, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except Exception:
                pass
        if not payload:
            # Last resort: find the outermost { } span
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                try:
                    payload = json.loads(raw[start:end + 1])
                except Exception:
                    pass
    if not isinstance(payload, dict):
        payload = {}
    decision = str(payload.get("decision") or "").strip().lower()
    allowed = {"approved_standard", "approved_probe", "approved_aggressive_probe", "downgrade_to_watch", "blocked"}
    rationale = str(payload.get("rationale") or "").strip()
    if decision not in allowed:
        return {
            "status": "unavailable",
            "decision": "adjudication_unavailable",
            "rationale": "missing_or_invalid_adjudication_decision",
            "confidence": str(payload.get("confidence") or "").strip(),
            "parse_ok": False,
        }
    if not rationale:
        return {
            "status": "unavailable",
            "decision": "adjudication_unavailable",
            "rationale": "missing_or_invalid_adjudication_rationale",
            "confidence": str(payload.get("confidence") or "").strip(),
            "parse_ok": False,
        }
    status = "approved" if decision in {"approved_standard", "approved_probe", "approved_aggressive_probe"} else ("downgraded" if decision == "downgrade_to_watch" else "blocked")
    return {
        "status": status,
        "decision": decision,
        "rationale": rationale,
        "confidence": str(payload.get("confidence") or "").strip(),
        "parse_ok": True,
    }


def _apply_adjudication_to_risk(risk_result: Dict[str, Any], adjudication: Dict[str, Any]) -> Dict[str, Any]:
    decision = str(adjudication.get("decision") or "").strip()
    updated = dict(risk_result)
    if decision == "adjudication_unavailable" or str(adjudication.get("status") or "") == "unavailable":
        updated["deep_adjudication_unavailable"] = True
        return updated
    if decision == "approved_standard":
        if not updated.get("top_candidate"):
            updated["ok"] = False
        return updated
    if decision == "approved_probe":
        for row in list(updated.get("checks") or []):
            if row.get("standard_ready"):
                row["all_checks_pass"] = False
                row["standard_ready"] = False
                row["risk_tier"] = "watch"
                row.setdefault("risk_notes", []).append("deep_adjudication_not_standard")
        updated["standard_candidates"] = []
        updated["top_candidate"] = None
        updated["ok"] = False
        return updated
    if decision == "approved_aggressive_probe":
        for row in list(updated.get("checks") or []):
            if row.get("standard_ready") or row.get("probe_ready"):
                row["all_checks_pass"] = False
                row["standard_ready"] = False
                row["probe_ready"] = False
                row["risk_tier"] = "watch"
                row.setdefault("risk_notes", []).append("deep_adjudication_not_aggressive")
        updated["standard_candidates"] = []
        updated["probe_candidates"] = []
        updated["top_candidate"] = None
        updated["top_probe_candidate"] = None
        updated["ok"] = False
        updated["probe_ok"] = False
        updated["aggressive_probe_ok"] = bool(updated.get("top_aggressive_probe_candidate"))
        return updated
    if decision in {"downgrade_to_watch", "blocked"}:
        for row in list(updated.get("checks") or []):
            row["all_checks_pass"] = False
            row["standard_ready"] = False
            row["probe_ready"] = False
            row["aggressive_probe_ready"] = False
            if row.get("risk_tier") in {"standard", "probe", "aggressive_probe"}:
                row["risk_tier"] = "watch"
            row.setdefault("risk_notes", []).append(f"deep_adjudication_{decision}")
        updated["standard_candidates"] = []
        updated["probe_candidates"] = []
        updated["aggressive_probe_candidates"] = []
        updated["top_candidate"] = None
        updated["top_probe_candidate"] = None
        updated["top_aggressive_probe_candidate"] = None
        updated["ok"] = False
        updated["probe_ok"] = False
        updated["aggressive_probe_ok"] = False
    return updated


class DeepAdjudicationAdapter:
    """Production I/O boundary for model readiness, inference, and Sky queueing."""

    def direct(
        self,
        *,
        base_url: str,
        model: str,
        prompt: str,
        system_prompt: str,
        timeout_sec: int,
    ) -> Dict[str, Any]:
        from common.gb10_gate import wait_for_gb10_ready
        from common.query_client import query_model_with_meta

        gate = wait_for_gb10_ready(
            base_url=base_url,
            target_model=model,
            max_wait_sec=int(os.getenv("APOLLO_GB10_GATE_MAX_WAIT_SEC", "300")),
        )
        if not gate.get("ready"):
            return {"meta": None, "gate": gate}
        meta = query_model_with_meta(
            prompt,
            system_prompt=system_prompt,
            model=model,
            provider="ollama",
            task_type="finance_deep_trade_adjudication",
            timeout_sec=timeout_sec,
            retries=2,
            force_ui_chat=False,
            base_url=base_url,
        )
        return {"meta": meta, "gate": gate}

    def sky_queue(
        self,
        *,
        sky_url: str,
        base_url: str,
        model: str,
        prompt: str,
        system_prompt: str,
        timeout_sec: int,
    ) -> Optional[Dict[str, Any]]:
        import requests as request_client

        response = request_client.post(
            f"{sky_url}/gb10/enqueue",
            json={
                "prompt": prompt,
                "system_prompt": system_prompt,
                "model": model,
                "base_url": base_url,
                "task_type": "finance_deep_trade_adjudication",
                "timeout_sec": timeout_sec,
            },
            timeout=10,
        )
        response.raise_for_status()
        job_id = response.json().get("job_id")
        if not job_id:
            return None
        deadline = time.time() + timeout_sec + 360
        while time.time() < deadline:
            time.sleep(10)
            poll_data = request_client.get(f"{sky_url}/gb10/job/{job_id}", timeout=5).json()
            if poll_data.get("status") == "done":
                result = poll_data.get("result") or {}
                return {
                    "text": result.get("text", ""),
                    "response": result.get("text", ""),
                    "fallback_reason_code": result.get("fallback_reason_code", ""),
                    "_via_sky_queue": True,
                    "_job_id": job_id,
                }
            if poll_data.get("status") == "error":
                return None
        return None


def _stage_deep_trade_adjudication(
    run_dir: Path,
    risk_result: Dict[str, Any],
    news_result: Dict[str, Any],
    market_result: Dict[str, Any],
    hipporag_result: Dict[str, Any],
    *,
    adapter: Optional[DeepAdjudicationAdapter] = None,
) -> Dict[str, Any]:
    _log("Stage 4b: Deep Trade Adjudication - GB10 final reasoning gate")
    model_policy = resolve_model_policy(probe=True)
    storage = assert_model_storage_allowed()
    standard = list(risk_result.get("standard_candidates") or [])
    probes = list(risk_result.get("probe_candidates") or [])
    aggressive = list(risk_result.get("aggressive_probe_candidates") or [])
    watches = list(risk_result.get("watch_candidates") or [])

    if standard:
        deterministic_decision = "approved_standard"
    elif aggressive:
        deterministic_decision = "approved_aggressive_probe"
    elif probes:
        deterministic_decision = "approved_probe"
    else:
        deterministic_decision = "blocked"
    deep = model_policy.get("deep_trade_model") or {}

    prompt_payload = {
        "instruction": (
            "Return JSON only with keys decision, rationale, confidence. "
            "Allowed decisions: approved_standard, approved_aggressive_probe, approved_probe, downgrade_to_watch, blocked. "
            "You may confirm or downgrade candidates only. Never upgrade an aggressive/probe/watch/reject candidate to standard."
        ),
        "hard_rules": {
            "live_trade_execution": False,
            "human_approval_required": True,
            "aggressive_probe_candidates_never_live_or_standard_ready": True,
            "probe_candidates_never_live_or_standard_ready": True,
            "invalid_sizing_or_stale_evidence_must_not_be_upgraded": True,
        },
        "standard_candidates": [_compact_candidate_for_adjudication(row) for row in standard[:4]],
        "aggressive_probe_candidates": [_compact_candidate_for_adjudication(row) for row in aggressive[:4]],
        "probe_candidates": [_compact_candidate_for_adjudication(row) for row in probes[:4]],
        "watch_candidates": [_compact_candidate_for_adjudication(row) for row in watches[:4]],
        "evidence": {
            "news_evidence": news_result.get("news_evidence"),
            "news_source_quality": news_result.get("source_quality"),
            "event_impact_enabled": bool((market_result.get("event_impact") or {}).get("event_impact_enabled")),
            "gather_source_confidence": hipporag_result.get("gather_source_confidence"),
            "new_triples": int(hipporag_result.get("new_triples") or 0),
            "sources_gathered": int(hipporag_result.get("gathered_sources") or 0),
        },
    }
    adjudication: Dict[str, Any] = {
        "stage": "deep_trade_adjudication",
        "ok": True,
        "status": "approved" if deterministic_decision in {"approved_standard", "approved_probe", "approved_aggressive_probe"} else "blocked",
        "decision": deterministic_decision,
        "rationale": "deterministic_default_before_model_call",
        "model_policy": model_policy,
        "model_storage": storage,
        "model_available": bool(deep.get("available")),
        "model_called": False,
        "parse_ok": False,
        "fallback_policy": "",
        "deterministic_decision_used": False,
        "completed_at": _utc_now(),
    }

    if not bool(deep.get("available")):
        adjudication.update({
            "status": "unavailable",
            "decision": "adjudication_unavailable",
            "rationale": "deep_model_unavailable_used_deterministic_gate",
            "model_error": "deep_model_unavailable",
            "model_available": False,
            "fallback_policy": "deterministic_simulation_only",
            "deterministic_decision": deterministic_decision,
            "deterministic_decision_used": deterministic_decision in {"approved_standard", "approved_probe", "approved_aggressive_probe"},
        })
    else:
        gb10_url = str(deep.get("base_url") or "http://127.0.0.1:11435")
        gb10_model = str(deep.get("model") or "qwen2.5:72b")
        prompt_str = json.dumps(prompt_payload, indent=2)
        system_str = "You are Apollo's final paper-trading risk adjudicator. Return JSON only."
        timeout_sec = int(os.getenv("APOLLO_TRADE_REASON_LLM_TIMEOUT_SECS", "600"))
        sky_url = os.getenv("SKY_URL", "http://127.0.0.1:5011")

        def _direct_attempt() -> Optional[Dict[str, Any]]:
            """Layer 1+2: readiness gate then direct GB10 call."""
            if adapter is not None:
                result = adapter.direct(
                    base_url=gb10_url,
                    model=gb10_model,
                    prompt=prompt_str,
                    system_prompt=system_str,
                    timeout_sec=timeout_sec,
                )
                adjudication["gb10_gate"] = result.get("gate") or {}
                return result.get("meta")
            try:
                from common.gb10_gate import wait_for_gb10_ready
                gate = wait_for_gb10_ready(
                    base_url=gb10_url,
                    target_model=gb10_model,
                    max_wait_sec=int(os.getenv("APOLLO_GB10_GATE_MAX_WAIT_SEC", "300")),
                )
                adjudication["gb10_gate"] = gate
                if not gate["ready"]:
                    _log(f"  GB10 gate timed out after {gate['waited_sec']}s — escalating to Sky queue")
                    return None
            except Exception as gate_exc:
                _log(f"  GB10 gate error: {gate_exc} — proceeding anyway")

            from common.query_client import query_model_with_meta
            meta = query_model_with_meta(
                prompt_str,
                system_prompt=system_str,
                model=gb10_model,
                provider="ollama",
                task_type="finance_deep_trade_adjudication",
                timeout_sec=timeout_sec,
                retries=2,
                force_ui_chat=False,
                base_url=gb10_url,
            )
            adjudication["model_called"] = True
            adjudication["model_available"] = True
            adjudication["model_meta"] = {k: v for k, v in meta.items() if k not in {"text", "response", "reply"}}
            fallback_code = str(meta.get("fallback_reason_code") or "").strip()
            if bool(fallback_code) and any(x in fallback_code for x in ("unreachable", "timeout", "gpu_required", "all_providers")):
                _log(f"  Direct GB10 call returned fallback ({fallback_code}) — escalating to Sky queue")
                return None
            return meta

        def _sky_queue_attempt() -> Optional[Dict[str, Any]]:
            """Layer 3: enqueue through Sky and poll for result."""
            if adapter is not None:
                return adapter.sky_queue(
                    sky_url=sky_url,
                    base_url=gb10_url,
                    model=gb10_model,
                    prompt=prompt_str,
                    system_prompt=system_str,
                    timeout_sec=timeout_sec,
                )
            try:
                import requests as _req
                enqueue_resp = _req.post(
                    f"{sky_url}/gb10/enqueue",
                    json={
                        "prompt": prompt_str,
                        "system_prompt": system_str,
                        "model": gb10_model,
                        "base_url": gb10_url,
                        "task_type": "finance_deep_trade_adjudication",
                        "timeout_sec": timeout_sec,
                    },
                    timeout=10,
                )
                enqueue_resp.raise_for_status()
                job_id = enqueue_resp.json().get("job_id")
                if not job_id:
                    return None

                _log(f"  Sky queue job submitted: {job_id}")
                poll_deadline = time.time() + timeout_sec + 360  # gate wait + generation
                while time.time() < poll_deadline:
                    time.sleep(10)
                    poll_resp = _req.get(f"{sky_url}/gb10/job/{job_id}", timeout=5)
                    poll_data = poll_resp.json()
                    status = poll_data.get("status")
                    if status == "done":
                        result = poll_data.get("result") or {}
                        _log(f"  Sky queue job done (waited {poll_data.get('gate_waited_sec', 0)}s in gate)")
                        return {
                            "text": result.get("text", ""),
                            "response": result.get("text", ""),
                            "fallback_reason_code": result.get("fallback_reason_code", ""),
                            "_via_sky_queue": True,
                            "_job_id": job_id,
                        }
                    if status == "error":
                        _log(f"  Sky queue job error: {poll_data.get('error')}")
                        return None
                _log("  Sky queue poll timed out")
            except Exception as sky_exc:
                _log(f"  Sky queue fallback error: {sky_exc}")
            return None

        meta: Optional[Dict[str, Any]] = None
        try:
            meta = _direct_attempt()
        except Exception as exc:
            adjudication["model_meta"] = {"direct_attempt_error": str(exc)}
            _log(f"  Direct GB10 attempt raised: {exc} — escalating to Sky queue")

        if meta is None:
            try:
                meta = _sky_queue_attempt()
                if meta is not None:
                    adjudication["model_called"] = True
                    adjudication["model_available"] = True
                    adjudication.setdefault("model_meta", {})["via_sky_queue"] = True
            except Exception as sky_exc:
                _log(f"  Sky queue attempt raised: {sky_exc}")

        if meta is not None:
            reply = str(meta.get("text") or meta.get("response") or meta.get("reply") or "")
            fallback_code = str(meta.get("fallback_reason_code") or "").strip()
            if bool(fallback_code) and any(x in fallback_code for x in ("unreachable", "timeout", "gpu_required", "all_providers")):
                adjudication.update({
                    "status": "unavailable",
                    "decision": "adjudication_unavailable",
                    "rationale": f"primary_provider_unreachable:{fallback_code}",
                    "parse_ok": False,
                    "fallback_policy": "deterministic_simulation_only",
                    "deterministic_decision": deterministic_decision,
                    "deterministic_decision_used": deterministic_decision in {"approved_standard", "approved_probe", "approved_aggressive_probe"},
                })
            else:
                parsed = _parse_adjudication_reply(reply)
                adjudication.update(parsed)
                if adjudication["decision"] == "adjudication_unavailable":
                    adjudication["fallback_policy"] = "deterministic_simulation_only"
                    adjudication["deterministic_decision"] = deterministic_decision
                    adjudication["deterministic_decision_used"] = deterministic_decision in {"approved_standard", "approved_probe", "approved_aggressive_probe"}
        else:
            adjudication.update({
                "status": "unavailable",
                "decision": "adjudication_unavailable",
                "rationale": "all_layers_failed_used_deterministic_gate",
                "model_available": True,
                "fallback_policy": "deterministic_simulation_only",
                "deterministic_decision": deterministic_decision,
                "deterministic_decision_used": deterministic_decision in {"approved_standard", "approved_probe", "approved_aggressive_probe"},
            })

    # Enforce hard non-upgrade semantics after the model speaks.
    if adjudication["decision"] == "approved_standard" and not standard:
        adjudication["decision"] = "approved_aggressive_probe" if aggressive else ("approved_probe" if probes else "blocked")
        adjudication["rationale"] += "; hard_gate_prevented_standard_upgrade"
        adjudication["status"] = "approved" if (aggressive or probes) else "blocked"
    if adjudication["decision"] == "approved_aggressive_probe" and not aggressive:
        adjudication["decision"] = "approved_probe" if probes else "blocked"
        adjudication["rationale"] += "; hard_gate_prevented_aggressive_upgrade"
        adjudication["status"] = "approved" if probes else "blocked"
    if adjudication["decision"] == "approved_probe" and not probes:
        adjudication["decision"] = "approved_aggressive_probe" if aggressive else "blocked"
        adjudication["rationale"] += "; hard_gate_prevented_probe_upgrade"
        adjudication["status"] = "approved" if aggressive else "blocked"

    adjudication["completed_at"] = _utc_now()
    _write_json(run_dir / "04b_deep_trade_adjudication.json", adjudication)
    _log(f"  Deep adjudication done - decision={adjudication['decision']} model={deep.get('model')}")
    return adjudication


def _stage_simulate(
    run_dir: Path,
    risk_result: Dict,
    news_result: Dict,
    market_result: Dict,
    hipporag_result: Dict,
    adjudication_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _log("Stage 5: Simulation — running trade replay and writing observation report")
    from Apollo.swing_simulation import run_simulation
    from Apollo.swing_study import _CONFIDENCE_TIERS

    top = risk_result.get("top_candidate") or {}
    aggressive_top = risk_result.get("top_aggressive_probe_candidate") or {}
    probe_top = risk_result.get("top_probe_candidate") or {}
    stress_top = risk_result.get("top_stress_probe_candidate") or {}
    proposals = list(news_result.get("blended_proposals") or market_result.get("proposals") or [])

    # Use top risk-cleared candidate; fall back to highest blended score for observation
    candidate_ticker = top.get("ticker") if top else (aggressive_top.get("ticker") if aggressive_top else (probe_top.get("ticker") if probe_top else (stress_top.get("ticker") if stress_top else (proposals[0].get("ticker") if proposals else None))))
    candidate_proposal = next((p for p in proposals if p.get("ticker") == candidate_ticker), proposals[0] if proposals else {})
    selected_check = top or aggressive_top or probe_top or stress_top or {}

    sim_result = None
    if candidate_proposal:
        _log(f"  Simulating {candidate_proposal.get('ticker')} — replaying last 90 days vs entry/stop/target")
        sim_result = run_simulation({
            "ticker": candidate_proposal.get("ticker"),
            "entry_zone": candidate_proposal.get("entry_zone"),
            "stop_zone": candidate_proposal.get("stop_zone"),
            "target_zone": candidate_proposal.get("target_zone"),
            "setup_type": candidate_proposal.get("setup_type"),
            "score": candidate_proposal.get("news_blended_score") or candidate_proposal.get("score"),
            "confidence_label": candidate_proposal.get("confidence_label"),
            "risk_reward_estimate": candidate_proposal.get("risk_reward_estimate"),
            "direction_bias": candidate_proposal.get("direction_bias"),
            "risk_tier": selected_check.get("risk_tier") or "observation",
            "paper_only": bool(selected_check.get("paper_only")),
            "paper_risk_pct": selected_check.get("paper_risk_pct"),
            "max_notional_pct": selected_check.get("max_notional_pct"),
            "aggressive_probe_ready": bool(selected_check.get("aggressive_probe_ready")),
            "pressed_aggressive": bool(selected_check.get("pressed_aggressive")),
            "pressed_aggressive_profile": selected_check.get("pressed_aggressive_profile") or {},
            "graph_risk_appetite": selected_check.get("graph_risk_appetite") or {},
            "aggressive_reward_profile": selected_check.get("aggressive_reward_profile") or {},
            "aggressive_reason": selected_check.get("aggressive_reason") or "",
            "aggressive_next_action": selected_check.get("aggressive_next_action") or "",
            "simulation_stress_mode": bool(selected_check.get("simulation_stress_mode")),
            "stress_probe_ready": bool(selected_check.get("stress_probe_ready")),
            "stress_position_sizing": selected_check.get("stress_position_sizing") or {},
            "live_trade_execution": False,
            "created_at": _utc_now(),
            "run_id": run_dir.name,
        }, lookback_days=90, save=True)

    stage_result = {
        "stage": "simulate",
        "ok": sim_result is not None,
        "ticker": candidate_ticker,
        "risk_tier": selected_check.get("risk_tier") or "observation",
        "probe_ready": bool(selected_check.get("probe_ready")),
        "aggressive_probe_ready": bool(selected_check.get("aggressive_probe_ready")),
        "stress_probe_ready": bool(selected_check.get("stress_probe_ready")),
        "simulation_stress_mode": bool(selected_check.get("simulation_stress_mode")),
        "stress_position_sizing": selected_check.get("stress_position_sizing") or {},
        "pressed_aggressive": bool(selected_check.get("pressed_aggressive")),
        "graph_asymmetry_score": (selected_check.get("graph_risk_appetite") or {}).get("score"),
        "trade_ready": bool(selected_check.get("standard_ready")),
        "deep_trade_adjudication": {
            "decision": (adjudication_result or {}).get("decision") or "",
            "rationale": (adjudication_result or {}).get("rationale") or "",
        },
        "simulation": {k: v for k, v in (sim_result or {}).items() if k not in ("trace", "candles")},
        "completed_at": _utc_now(),
    }
    if selected_check.get("stress_probe_ready"):
        stress_report = stress_mode.write_stress_report(
            run_id=run_dir.name,
            candidate={**candidate_proposal, **selected_check},
            sizing=dict(selected_check.get("stress_position_sizing") or {}),
            production_blockers=list(selected_check.get("production_blockers_ignored_for_stress") or selected_check.get("risk_notes") or []),
            source="trade_cycle",
            extra={
                "observation_status": stage_result.get("risk_tier"),
                "simulation": stage_result.get("simulation") or {},
            },
        )
        stage_result["stress_report"] = {
            "ok": bool(stress_report.get("ok")),
            "markdown_path": stress_report.get("markdown_path") or "",
            "json_path": stress_report.get("json_path") or "",
        }
    _write_json(run_dir / "05_simulate.json", stage_result)

    # Write observation report
    report = _build_observation_report(
        run_dir=run_dir,
        hipporag=hipporag_result,
        market=market_result,
        news=news_result,
        risk=risk_result,
        sim=sim_result,
        candidate=candidate_proposal,
        adjudication=adjudication_result or {},
    )
    report_path = run_dir / "observation_report.md"
    report_path.write_text(report, encoding="utf-8")
    _log(f"  Observation report -> {report_path}")

    # Symlink latest
    latest_link = CYCLE_LOG_ROOT / "latest_observation_report.md"
    try:
        latest_link.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        latest_link.write_text(report, encoding="utf-8")
    except Exception:
        pass

    return stage_result


def _build_observation_report(
    run_dir: Path,
    hipporag: Dict, market: Dict, news: Dict, risk: Dict,
    sim: Optional[Dict], candidate: Dict, adjudication: Optional[Dict[str, Any]] = None,
) -> str:
    ticker = candidate.get("ticker") or "—"
    regime = (market.get("market_regime") or {}).get("label") or "—"
    band = hipporag.get("confidence_band") or "—"
    new_triples = hipporag.get("new_triples") or 0
    gathered = hipporag.get("gathered_sources") or 0

    entry_zone = candidate.get("entry_zone") or "—"
    stop_zone = candidate.get("stop_zone") or "—"
    target_zone = candidate.get("target_zone") or "—"
    rr = candidate.get("risk_reward_estimate")
    blended = candidate.get("news_blended_score") or candidate.get("score")
    label = candidate.get("confidence_label") or "—"
    setup = candidate.get("setup_type") or "—"
    kg = candidate.get("kg_grounding") or {}
    kg_triples = kg.get("triple_count") or 0

    news_ticker = (news.get("news_by_ticker") or {}).get(ticker) or {}
    ws = news_ticker.get("weighted_sentiment")
    headlines = list(news_ticker.get("items") or [])

    top_check = next((c for c in (risk.get("checks") or []) if c["ticker"] == ticker), {})
    sizing = top_check.get("position_sizing") or {}
    stress_sizing = top_check.get("stress_position_sizing") or {}
    passes = top_check.get("all_checks_pass", False)
    risk_tier = top_check.get("risk_tier") or "observation"
    probe_ready = bool(top_check.get("probe_ready"))
    aggressive_ready = bool(top_check.get("aggressive_probe_ready"))
    stress_ready = bool(top_check.get("stress_probe_ready"))
    risk_notes = list(top_check.get("risk_notes") or [])
    no_trade_reason = "; ".join(risk_notes) if risk_notes else (top_check.get("no_trade_reason") or ("passes all gates" if passes else "failed risk gate"))

    sim_status = (sim or {}).get("status") or "not_run"
    sim_entry_date = (sim or {}).get("entry_date") or "—"
    sim_exit_date = (sim or {}).get("exit_date") or "open"
    sim_pnl = (sim or {}).get("pnl_pct")
    sim_entry_price = (sim or {}).get("entry_price")
    sim_days = (sim or {}).get("candles_in_trade") or 0

    pnl_str = f"{sim_pnl:+.2f}%" if sim_pnl is not None else "not yet entered"

    proposals = list(news.get("blended_proposals") or market.get("proposals") or [])
    adjudication = adjudication or risk.get("deep_trade_adjudication") or {}
    model_policy = adjudication.get("model_policy") or risk.get("model_policy") or resolve_model_policy(probe=False)
    model_storage = adjudication.get("model_storage") or {}

    news_evidence = news.get("news_evidence") or ("partial" if any(
        int((news.get("news_by_ticker") or {}).get(t, {}).get("headline_count") or 0) > 0
        for t in [ticker]
    ) else "none")
    hipporag_enriched = not bool(hipporag.get("enrichment_skipped"))
    data_warnings: List[str] = []
    if gathered == 0:
        data_warnings.append("KG not enriched — 0 sources gathered this cycle (using prior KG state)")
    if new_triples == 0:
        data_warnings.append("0 new KG triples produced")
    if news_evidence == "none":
        data_warnings.append("No news headlines fetched — sentiment blend is 100% technical score")
    if str(news.get("source_quality") or "") == "c_tier_only_yahoo_rss":
        data_warnings.append("News source mix is C-tier-only Yahoo RSS; treat headlines as supporting evidence, not high-confidence proof")
    if not passes:
        data_warnings.append("No standard trade gate passed; this is observation/probe only, NOT a trade recommendation")
    if aggressive_ready:
        data_warnings.append("Aggressive probe is paper-only; live execution remains disabled and human approval remains required")
    if stress_ready:
        data_warnings.append("Stress sandbox is high-variance simulation evidence only; it does not count as production readiness")

    lines = [
        "# Apollo Trade Observation Report",
        "",
        f"- cycle_run_dir: `{run_dir.name}`",
        f"- generated_at: `{_utc_now()}`",
        f"- pipeline_confidence_band: `{band}`",
        f"- market_regime: `{regime}`",
        f"- new_kg_triples: `{new_triples}`",
        f"- sources_gathered: `{gathered}`",
        f"- kg_enriched: `{hipporag_enriched}`",
        f"- news_evidence: `{news_evidence}`",
        f"- news_source_quality: `{news.get('source_quality') or 'unknown'}`",
        f"- evidence_confidence: `{news.get('evidence_confidence') or 'unknown'}`",
        f"- event_impact_enabled: `{bool((market.get('event_impact') or {}).get('event_impact_enabled'))}`",
        f"- event_impact_path: `{(market.get('event_impact') or {}).get('event_impact_path') or ''}`",
        f"- model_policy.fast_model: `{(model_policy.get('fast_model') or {}).get('model')}`",
        f"- model_policy.deep_trade_model: `{(model_policy.get('deep_trade_model') or {}).get('model')}`",
        f"- model_policy.vision_ocr_model: `{(model_policy.get('vision_ocr_model') or {}).get('model')}`",
        f"- model_policy.deep_model_fallback_used: `{(model_policy.get('deep_trade_model') or {}).get('fallback_used')}`",
        f"- model_storage.c_drive_downloads_allowed: `{model_storage.get('c_drive_downloads_allowed')}`",
        f"- deep_trade_adjudication.status: `{adjudication.get('status') or ''}`",
        f"- deep_trade_adjudication.decision: `{adjudication.get('decision') or ''}`",
        f"- simulation_stress_mode: `{bool(top_check.get('simulation_stress_mode'))}`",
        f"- stress_probe_ready: `{stress_ready}`",
        f"- stress_risk_pct: `{stress_sizing.get('risk_pct') if stress_sizing else ''}`",
        f"- stress_planned_notional: `{stress_sizing.get('planned_notional') if stress_sizing else ''}`",
        f"- stress_fractional_shares: `{stress_sizing.get('fractional_shares') if stress_sizing else ''}`",
        f"- stress_max_loss_dollars: `{stress_sizing.get('max_loss_dollars') if stress_sizing else ''}`",
        f"- deep_trade_adjudication.rationale: `{adjudication.get('rationale') or ''}`",
        f"- deep_trade_adjudication.model_available: `{adjudication.get('model_available')}`",
        f"- deep_trade_adjudication.model_called: `{adjudication.get('model_called')}`",
        f"- deep_trade_adjudication.parse_ok: `{adjudication.get('parse_ok')}`",
        f"- deep_trade_adjudication.fallback_policy: `{adjudication.get('fallback_policy') or ''}`",
        f"- deep_trade_adjudication.deterministic_decision_used: `{adjudication.get('deterministic_decision_used')}`",
        f"- status_label: `{'trade_ready' if passes else ('aggressive_probe_ready' if aggressive_ready else ('probe_ready' if probe_ready else 'observation_only'))}`",
        f"- human_approval_required: `true`",
        f"- live_trade_execution: `false`",
        "",
    ]

    if data_warnings:
        lines += [
            "> [!WARNING]",
        ]
        for w in data_warnings:
            lines.append(f"> - {w}")
        lines.append("")

    resolved_paths = model_storage.get("resolved_paths") or {}
    if resolved_paths:
        lines += [
            "## Model Storage Guardrail",
            "",
            f"- required_storage: `{model_storage.get('required_storage')}`",
            f"- blocked_c_drive_paths: `{model_storage.get('blocked_c_drive_paths') or {}}`",
            f"- OLLAMA_MODELS: `{resolved_paths.get('OLLAMA_MODELS') or ''}`",
            f"- HF_HOME: `{resolved_paths.get('HF_HOME') or ''}`",
            f"- TRANSFORMERS_CACHE: `{resolved_paths.get('TRANSFORMERS_CACHE') or ''}`",
            f"- TORCH_HOME: `{resolved_paths.get('TORCH_HOME') or ''}`",
            "",
        ]

    candidate_header = "## Top Standard Candidate" if passes else (
        "## Top Aggressive Paper Probe - SIMULATION ONLY" if aggressive_ready else (
            "## Top Probe Candidate - SIMULATION ONLY" if probe_ready else "## OBSERVATION ONLY - NO TRADE"
        )
    )
    lines += [
        "---",
        "",
        candidate_header,
        "",
        f"- ticker: `{ticker}`",
        f"- setup_type: `{setup}`",
        f"- blended_score: `{blended}`  _(70% technical + 30% news sentiment)_",
        f"- confidence_label: `{label}`",
        f"- kg_triples_grounding: `{kg_triples}`",
        f"- news_weighted_sentiment: `{ws:+.3f}`" if ws is not None else "- news_weighted_sentiment: `n/a`",
        "",
        "### Trade Zones",
        "",
        f"| Zone | Value |",
        f"|------|-------|",
        f"| Entry | `{entry_zone}` |",
        f"| Stop | `{stop_zone}` |",
        f"| Target | `{target_zone}` |",
        f"| R/R | `{rr}:1` |",
        "",
        "### Risk Gate",
        "",
        f"- risk_tier: `{risk_tier}`",
        f"- all_checks_pass: `{passes}`",
        f"- probe_ready: `{probe_ready}`",
        f"- aggressive_probe_ready: `{aggressive_ready}`",
        f"- pressed_aggressive: `{top_check.get('pressed_aggressive')}`",
        f"- aggressive_next_action: `{top_check.get('aggressive_next_action') or 'n/a'}`",
        f"- aggressive_reason: `{top_check.get('aggressive_reason') or ''}`",
        f"- graph_asymmetry_score: `{(top_check.get('graph_risk_appetite') or {}).get('score')}`",
        f"- graph_conviction: `{(top_check.get('graph_risk_appetite') or {}).get('conviction')}`",
        f"- reward_extension: `{((top_check.get('aggressive_reward_profile') or {}).get('extension') or {}).get('reason') or ''}`",
        f"- event_score: `{top_check.get('event_impact_score')}`",
        f"- volume_ratio: `{((top_check.get('volume_confirmation') or {}).get('volume_ratio'))}`",
        f"- sentiment_backend: `{top_check.get('sentiment_backend')}`",
        f"- liquidity: `{(top_check.get('liquidity') or {}).get('ok')}`",
        f"- price_valid: `{top_check.get('price_valid')}`",
        f"- paper_risk_pct: `{top_check.get('paper_risk_pct')}`",
        f"- max_notional_pct: `{top_check.get('max_notional_pct')}`",
        f"- next_action: `{top_check.get('next_action') or 'n/a'}`",
        f"- reason: `{no_trade_reason}`",
    ]

    if sizing:
        lines += [
            "",
            f"### Position Sizing (paper - ${PORTFOLIO_SIZE:,.0f} portfolio)",
            "",
            f"| Item | Value |",
            f"|------|-------|",
            f"| Shares | `{sizing.get('shares')}` |",
            f"| Risk Tier | `{sizing.get('risk_tier')}` |",
            f"| Risk Percent | `{float(sizing.get('risk_pct') or 0) * 100:.2f}%` |",
            f"| Capped by Notional | `{sizing.get('capped_by_notional')}` |",
            f"| Max Notional | `${sizing.get('max_notional_dollars'):,.2f}` |",
            f"| Total Cost | `${sizing.get('total_cost'):,.2f}` |",
            f"| % of Portfolio | `{sizing.get('pct_of_portfolio')}%` |",
            f"| Max Loss | `${sizing.get('max_loss_dollars'):,.2f}` |",
            f"| Risk/Share | `${sizing.get('risk_per_share'):.2f}` |",
        ]

    lines += [
        "",
        "---",
        "",
        "## Historical Simulation (90-day replay)",
        "",
        f"- status: `{sim_status}`",
        f"- entry_date: `{sim_entry_date}`",
        f"- entry_price: `${sim_entry_price:.2f}`" if sim_entry_price else "- entry_price: `not entered`",
        f"- exit_date: `{sim_exit_date}`",
        f"- days_in_trade: `{sim_days}`",
        f"- pnl: `{pnl_str}`",
        "",
    ]

    if passes:
        lines += [
            f"> **What to watch tomorrow:** If price opens in the entry zone "
            f"(`{entry_zone}`), the setup is active. Stop is `{stop_zone}`. "
            f"Target is `{target_zone}`. Update this file with actual outcome.",
            "",
        ]
    else:
        lines += [
            f"> **OBSERVATION NOTE:** This simulation ran for learning purposes only. "
            f"`{ticker}` did NOT pass risk checks (reason: `{no_trade_reason}`). "
            f"Do not act on zones below until a future cycle produces a passing candidate.",
            "",
        ]

    lines += [
        "---",
        "",
        "## Recent Headlines",
        "",
    ]
    if headlines:
        for h in headlines[:5]:
            s = h.get("sentiment", 0)
            sign = "+" if s >= 0 else ""
            lines.append(f"- [{h['title']}]({h['link']}) `sentiment={sign}{s:.2f}`")
    else:
        lines.append("- No headlines fetched.")

    lines += [
        "",
        "---",
        "",
        "## Risk Tier Breakdown",
        "",
    ]
    for title, rows in [
        ("Standard Candidates", list(risk.get("standard_candidates") or [])),
        ("Aggressive Probe Candidates", list(risk.get("aggressive_probe_candidates") or [])),
        ("Probe Candidates", list(risk.get("probe_candidates") or [])),
        ("Wait For Entry Reset", list(risk.get("wait_for_entry_reset_candidates") or [])),
        ("Watch Candidates", list(risk.get("watch_candidates") or [])),
        ("Rejected Candidates", list(risk.get("rejected_candidates") or [])),
    ]:
        lines += [
            f"### {title}",
            "",
            "| Ticker | Tier | Score | R/R | Direction | Event | Graph | Pressed | Aggressive | Notes |",
            "|--------|------|-------|-----|-----------|-------|-------|---------|------------|-------|",
        ]
        if rows:
            for row in rows[:6]:
                notes = "; ".join(list(row.get("risk_notes") or [])[:2])
                lines.append(
                    f"| {row.get('ticker')} | {row.get('risk_tier')} | {row.get('blended_score')} "
                    f"| {row.get('rr')} | {row.get('direction_bias')} | {row.get('event_impact_score')} "
                    f"| {(row.get('graph_risk_appetite') or {}).get('score')} | {row.get('pressed_aggressive')} "
                    f"| {row.get('aggressive_next_action') or ''} | {notes} |"
                )
        else:
            lines.append("| - | - | - | - | - | - | - | - | - | - |")
        lines.append("")

    lines += [
        "",
        "---",
        "",
        "## Ranked Watchlist",
        "",
        "| Ticker | Blended Score | Label | Bias | R/R | KG Triples | News Sentiment |",
        "|--------|---------------|-------|------|-----|------------|----------------|",
    ]
    for p in proposals[:8]:
        t = p.get("ticker", "")
        news_t = (news.get("news_by_ticker") or {}).get(t) or {}
        ws_t = news_t.get("weighted_sentiment")
        ws_str = f"{ws_t:+.3f}" if ws_t is not None else "—"
        kg_t = (p.get("kg_grounding") or {}).get("triple_count") or 0
        rr_t = p.get("risk_reward_estimate") or "—"
        lines.append(
            f"| {t} | {p.get('news_blended_score') or p.get('score')} "
            f"| {p.get('confidence_label') or '—'} "
            f"| {p.get('direction_bias') or '—'} "
            f"| {rr_t} "
            f"| {kg_t} "
            f"| {ws_str} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Observation Log",
        "",
        "_Fill in tomorrow after market open:_",
        "",
        "| Date | Open | High | Low | Close | Status | Notes |",
        "|------|------|------|-----|-------|--------|-------|",
        f"| {datetime.now().strftime('%Y-%m-%d')} | — | — | — | — | watching | cycle generated |",
        "| | | | | | | |",
        "| | | | | | | |",
    ]

    return "\n".join(lines) + "\n"


# ── Full cycle runner ─────────────────────────────────────────────────────────

def run_cycle() -> Dict[str, Any]:
    run_id = f"trade_cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = CYCLE_LOG_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Apollo Trade Cycle starting — run_id={run_id}")
    started_at = _utc_now()

    status: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "stages": {},
        "ok": False,
    }
    _write_json(run_dir / "status.json", status)

    try:
        h = _stage_hipporag_research(run_dir)
        status["stages"]["hipporag"] = {"ok": h["ok"], "completed_at": h["completed_at"]}
        _write_json(run_dir / "status.json", status)

        m = _stage_market_research(run_dir)
        status["stages"]["market"] = {"ok": m["ok"], "completed_at": m["completed_at"]}
        _write_json(run_dir / "status.json", status)

        n = _stage_news_analysis(run_dir, m, h)
        status["stages"]["news"] = {"ok": n["ok"], "completed_at": n["completed_at"]}
        _write_json(run_dir / "status.json", status)

        r = _stage_risk_checks(run_dir, n, m)
        status["stages"]["risk"] = {"ok": r["ok"], "completed_at": r["completed_at"]}
        _write_json(run_dir / "status.json", status)

        a = _stage_deep_trade_adjudication(run_dir, r, n, m, h)
        status["stages"]["deep_trade_adjudication"] = {"ok": a["ok"], "completed_at": a["completed_at"]}
        adjudicated_r = _apply_adjudication_to_risk(r, a)
        adjudicated_r["deep_trade_adjudication"] = a
        adjudicated_r["model_policy"] = a.get("model_policy") or {}
        _write_json(run_dir / "status.json", status)

        s = _stage_simulate(run_dir, adjudicated_r, n, m, h, a)
        status["stages"]["simulate"] = {"ok": s["ok"], "completed_at": s["completed_at"]}

        hipporag_enriched = not bool(h.get("enrichment_skipped"))
        trade_blockers: List[str] = []
        if not bool(adjudicated_r.get("ok")):
            trade_blockers.append("standard_risk_gate_failed")
        if str(a.get("decision") or "") in {"downgrade_to_watch", "blocked"}:
            trade_blockers.append(f"deep_adjudication_{a.get('decision')}")
        if not hipporag_enriched:
            trade_blockers.append("hipporag_not_enriched")
        if h.get("standard_trade_ready_allowed") is False:
            trade_blockers.append("source_confidence_caps_standard_trade")
        news_cov = sum(
            1 for v in (n.get("news_by_ticker") or {}).values()
            if int(v.get("headline_count") or 0) > 0
        )
        news_evidence = str(n.get("news_evidence") or "unknown")
        if news_evidence not in {"good", "partial"}:
            trade_blockers.append("news_evidence_missing")
        trade_ready = not trade_blockers
        probe_ready = bool(adjudicated_r.get("probe_ok")) and news_evidence in {"good", "partial"}
        aggressive_probe_ready = bool(adjudicated_r.get("aggressive_probe_ok")) and news_evidence in {"good", "partial"}
        stress_probe_ready = bool(adjudicated_r.get("stress_probe_ok"))
        status["completed"] = True
        status["trade_ready"] = trade_ready
        status["probe_ready"] = probe_ready
        status["aggressive_probe_ready"] = aggressive_probe_ready
        status["stress_probe_ready"] = stress_probe_ready
        status["simulation_stress_mode"] = bool(((adjudicated_r.get("risk_policy") or {}).get("simulation_stress_mode") or {}).get("enabled"))
        status["status_label"] = (
            "trade_ready"
            if trade_ready
            else ("aggressive_probe_ready" if aggressive_probe_ready else ("probe_ready" if probe_ready else ("stress_sandbox_ready" if stress_probe_ready else "blocked")))
        )
        status["trade_blockers"] = trade_blockers
        status["live_trade_execution"] = False
        status["ok"] = bool(s.get("ok"))   # simulation ran = cycle produced useful output
        status["evidence_quality"] = {
            "hipporag_enriched": hipporag_enriched,
            "sources_gathered": int(h.get("gathered_sources") or 0),
            "new_triples": int(h.get("new_triples") or 0),
            "news_tickers_covered": news_cov,
            "news_evidence": news_evidence,
            "news_source_quality": n.get("source_quality") or "unknown",
            "news_independent_domains": int(n.get("independent_domain_count") or 0),
            "news_unique_stories": int(n.get("unique_story_count") or 0),
            "news_deduplicated_count": int(n.get("deduplicated_count") or 0),
            "news_eligibility_paths": list(n.get("eligibility_paths") or []),
            "gather_source_confidence": h.get("gather_source_confidence") or "unknown",
            "trade_confidence_cap": h.get("trade_confidence_cap") or "unknown",
            "standard_trade_ready_allowed": bool(h.get("standard_trade_ready_allowed", True)),
            "event_impact_enabled": bool((m.get("event_impact") or {}).get("event_impact_enabled")),
        }
        status["model_policy"] = a.get("model_policy") or {}
        status["model_storage"] = a.get("model_storage") or {}
        status["deep_trade_adjudication"] = {
            "status": a.get("status") or "",
            "decision": a.get("decision") or "",
            "rationale": a.get("rationale") or "",
            "model_available": bool(a.get("model_available")),
            "model_called": bool(a.get("model_called")),
            "parse_ok": bool(a.get("parse_ok")),
            "fallback_policy": a.get("fallback_policy") or "",
            "deterministic_decision": a.get("deterministic_decision") or "",
            "deterministic_decision_used": bool(a.get("deterministic_decision_used")),
            "ok": bool(a.get("ok")),
        }
        status["finished_at"] = _utc_now()
        status["top_ticker"] = s.get("ticker")
        status["risk_tier"] = s.get("risk_tier") or "observation"
        status["standard_candidate_count"] = len(list(adjudicated_r.get("standard_candidates") or []))
        status["aggressive_probe_candidate_count"] = len(list(adjudicated_r.get("aggressive_probe_candidates") or []))
        status["stress_probe_candidate_count"] = len(list(adjudicated_r.get("stress_probe_candidates") or []))
        status["top_stress_ticker"] = ((adjudicated_r.get("top_stress_probe_candidate") or {}).get("ticker") or "")
        status["stress_position_sizing"] = s.get("stress_position_sizing") or ((adjudicated_r.get("top_stress_probe_candidate") or {}).get("stress_position_sizing") or {})
        status["stress_report"] = s.get("stress_report") or {}
        status["pressed_aggressive_candidate_count"] = sum(
            1 for row in list(adjudicated_r.get("aggressive_probe_candidates") or [])
            if row.get("pressed_aggressive")
        )
        status["pressed_aggressive"] = bool(s.get("pressed_aggressive"))
        status["graph_asymmetry_score"] = s.get("graph_asymmetry_score")
        status["probe_candidate_count"] = len(list(adjudicated_r.get("probe_candidates") or []))
        status["wait_for_entry_reset_count"] = len(list(adjudicated_r.get("wait_for_entry_reset_candidates") or []))
        status["sim_status"] = (s.get("simulation") or {}).get("status")
        _write_json(run_dir / "status.json", status)
        _log(
            f"Cycle complete — trade_ready={trade_ready} top={status.get('top_ticker')} "
            f"sim={status.get('sim_status')} evidence={status['evidence_quality']}"
        )

    except Exception as exc:
        status["error"] = str(exc)
        status["finished_at"] = _utc_now()
        _write_json(run_dir / "status.json", status)
        _log(f"Cycle failed: {exc}")
        raise

    return status


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cmd_status() -> None:
    runs = sorted(CYCLE_LOG_ROOT.glob("trade_cycle_*/status.json"), key=lambda p: p.parent.name, reverse=True)
    if not runs:
        print("No trade cycle runs found.")
        return
    for p in runs[:3]:
        s = _read_json(p)
        print(f"\n{p.parent.name}")
        print(f"  ok={s.get('ok')} ticker={s.get('top_ticker')} sim={s.get('sim_status')}")
        print(f"  started={s.get('started_at')} finished={s.get('finished_at')}")


def _cmd_report() -> None:
    rpt = CYCLE_LOG_ROOT / "latest_observation_report.md"
    if rpt.exists():
        print(rpt.read_text(encoding="utf-8"))
    else:
        print("No observation report yet. Run trade cycle first.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apollo Trade Research Cycle")
    parser.add_argument("cmd", choices=["run", "status", "report"], nargs="?", default="run")
    args = parser.parse_args()
    if args.cmd == "status":
        _cmd_status()
    elif args.cmd == "report":
        _cmd_report()
    else:
        run_cycle()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
