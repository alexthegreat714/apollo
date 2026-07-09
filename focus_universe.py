from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "evaluations" / "apollo_focus_universe_manifest.json"


def _norm_text(*parts: Any) -> str:
    return " ".join(" ".join(str(part or "").lower().split()) for part in parts if str(part or "").strip())


def load_focus_universe_manifest(path: Optional[str] = None) -> Dict[str, Any]:
    manifest_path = Path(path).resolve() if path else DEFAULT_MANIFEST_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("themes"), list):
            payload.setdefault("manifest_path", str(manifest_path))
            return payload
    except Exception:
        pass
    return {"name": "Apollo Focus Universe", "themes": [], "manifest_path": str(manifest_path)}


def _theme_queries(topic: str, theme: Dict[str, Any]) -> List[str]:
    label = str(theme.get("label") or theme.get("id") or "focus universe").strip()
    terms = str(theme.get("query_terms") or label).strip()
    tickers = [str((row or {}).get("ticker") or "").strip().upper() for row in list(theme.get("tickers") or [])]
    lead = " ".join([ticker for ticker in tickers[:3] if ticker])
    negatives = "-medical -respiratory -chemical -dictionary -definition -wikipedia -forum -reddit -quora"
    queries = [
        f"\"{topic}\" {label} {terms} earnings filing risk pdf {negatives}",
        f"\"{label}\" equity outlook sector risk research pdf {negatives}",
    ]
    if lead:
        queries.append(f"site:sec.gov {lead} 10-Q 8-K earnings pdf {negatives}")
    return [item.strip() for item in queries if item.strip()]


def _dedupe_evidence(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        url = str((row or {}).get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(dict(row))
    return out


def _theme_summary(score_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    accepted = [row for row in score_rows if not str(row.get("reject_reason") or "").strip()]
    accepted_count = len(accepted)
    trusted_count = sum(1 for row in accepted if str(row.get("tier") or "").upper() in {"A", "B"})
    doc_count = sum(1 for row in accepted if bool(row.get("is_doc_source")))
    pdf_count = sum(1 for row in accepted if bool(row.get("is_pdf")))
    avg_score = round(sum(float(row.get("score") or 0.0) for row in accepted) / max(1, accepted_count), 2) if accepted else 0.0
    trusted_ratio = round(float(trusted_count / max(1, accepted_count)), 3) if accepted else 0.0
    evidence_volume = min(100.0, (accepted_count * 10.0) + (doc_count * 4.0) + (pdf_count * 3.0))
    composite = round(
        min(
            100.0,
            (avg_score * 0.55)
            + (trusted_ratio * 100.0 * 0.20)
            + (min(accepted_count, 8) / 8.0 * 15.0)
            + (min(doc_count, 6) / 6.0 * 10.0),
        ),
        2,
    )
    return {
        "accepted_count": int(accepted_count),
        "trusted_count": int(trusted_count),
        "trusted_ratio": trusted_ratio,
        "doc_count": int(doc_count),
        "pdf_count": int(pdf_count),
        "avg_score": avg_score,
        "evidence_volume": round(evidence_volume, 2),
        "theme_score": composite,
        "top_evidence": accepted[:5],
    }


def _score_theme_tickers(theme: Dict[str, Any], evidence_rows: List[Dict[str, Any]], max_names: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for order_idx, row in enumerate(list(theme.get("tickers") or []), start=1):
        ticker = str((row or {}).get("ticker") or "").strip().upper()
        name = str((row or {}).get("name") or "").strip()
        if not ticker:
            continue
        mentions = 0
        doc_mentions = 0
        for evidence in evidence_rows:
            text = _norm_text(evidence.get("title"), evidence.get("snippet"), evidence.get("url"))
            if ticker.lower() in text or (name and name.lower() in text):
                mentions += 1
                if bool(evidence.get("is_doc_source")):
                    doc_mentions += 1
        base_weight = float((row or {}).get("weight") or 1.0)
        score = round((base_weight * 20.0) + (mentions * 7.0) + (doc_mentions * 4.0) - (order_idx * 0.05), 2)
        out.append(
            {
                "ticker": ticker,
                "name": name or ticker,
                "score": score,
                "mentions": int(mentions),
                "doc_mentions": int(doc_mentions),
                "weight": base_weight,
                "priority_rank": order_idx,
            }
        )
    out.sort(key=lambda item: (float(item.get("score") or 0.0), -int(item.get("priority_rank") or 999)), reverse=True)
    return out[: max(1, int(max_names))]


def select_focus_universe(
    topic: str,
    *,
    max_names: int,
    min_names: int,
    manifest_path: Optional[str],
    web_search_fn: Callable[..., Dict[str, Any]],
    score_candidate_fn: Callable[[Dict[str, Any], str], Dict[str, Any]],
) -> Dict[str, Any]:
    manifest = load_focus_universe_manifest(manifest_path)
    themes = [row for row in list(manifest.get("themes") or []) if isinstance(row, dict)]
    if not themes:
        return {
            "ok": False,
            "error": "focus_universe_manifest_empty",
            "manifest_path": str(manifest.get("manifest_path") or manifest_path or DEFAULT_MANIFEST_PATH),
            "themes": [],
        }

    scored_themes: List[Dict[str, Any]] = []
    for theme in themes:
        theme_id = str(theme.get("id") or "").strip() or "theme"
        label = str(theme.get("label") or theme_id).strip()
        queries = _theme_queries(topic, theme)
        evidence_rows: List[Dict[str, Any]] = []
        for query in queries:
            payload = web_search_fn(
                query,
                provider="auto",
                max_results=8,
                timeout=14,
                freshness_days=45,
                mode="finance_research",
            )
            rows = list((payload or {}).get("results") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                merged = dict(row)
                merged["query"] = query
                evidence_rows.append(merged)
        deduped_evidence = _dedupe_evidence(evidence_rows)
        theme_topic = f"{topic} {label} {str(theme.get('query_terms') or '').strip()}".strip()
        score_rows = [score_candidate_fn(row, theme_topic) for row in deduped_evidence]
        summary = _theme_summary(score_rows)
        top_tickers = _score_theme_tickers(theme, deduped_evidence, max_names=max_names)
        selected = top_tickers[: max(min_names, min(max_names, len(top_tickers)))]
        scored_themes.append(
            {
                "id": theme_id,
                "label": label,
                "why": str(theme.get("why") or "").strip(),
                "query_terms": str(theme.get("query_terms") or "").strip(),
                "queries": queries,
                "summary": summary,
                "selected_tickers": selected,
                "ticker_pool_size": len(list(theme.get("tickers") or [])),
            }
        )

    scored_themes.sort(
        key=lambda item: (
            float(((item.get("summary") or {}).get("theme_score")) or 0.0),
            float(((item.get("summary") or {}).get("avg_score")) or 0.0),
            int(((item.get("summary") or {}).get("accepted_count")) or 0),
        ),
        reverse=True,
    )
    best = dict(scored_themes[0])
    selected_tickers = list(best.get("selected_tickers") or [])
    selected_labels = [f"{row.get('ticker')} {row.get('name')}".strip() for row in selected_tickers[:3]]
    selection_queries = list(best.get("queries") or [])
    if selected_labels:
        selection_queries.append(
            f"\"{best.get('label') or best.get('id') or 'focus universe'}\" {' '.join(selected_labels)} earnings guidance risk pdf"
        )
    return {
        "ok": True,
        "manifest_path": str(manifest.get("manifest_path") or manifest_path or DEFAULT_MANIFEST_PATH),
        "topic": str(topic or "").strip(),
        "selected_theme": {
            "id": str(best.get("id") or "").strip(),
            "label": str(best.get("label") or "").strip(),
            "why": str(best.get("why") or "").strip(),
            "query_terms": str(best.get("query_terms") or "").strip(),
            "theme_score": float(((best.get("summary") or {}).get("theme_score")) or 0.0),
            "avg_source_score": float(((best.get("summary") or {}).get("avg_score")) or 0.0),
            "accepted_source_count": int(((best.get("summary") or {}).get("accepted_count")) or 0),
            "trusted_ratio": float(((best.get("summary") or {}).get("trusted_ratio")) or 0.0),
            "doc_count": int(((best.get("summary") or {}).get("doc_count")) or 0),
            "pdf_count": int(((best.get("summary") or {}).get("pdf_count")) or 0),
            "evidence_volume": float(((best.get("summary") or {}).get("evidence_volume")) or 0.0),
        },
        "selected_tickers": selected_tickers,
        "research_queries": selection_queries,
        "themes": scored_themes,
    }


def evaluate_decision_gate(
    *,
    quality: Dict[str, Any],
    stage_details: Dict[str, Any],
    focus_universe: Dict[str, Any],
) -> Dict[str, Any]:
    selected_theme = dict(focus_universe.get("selected_theme") or {})
    selected_tickers = [row for row in list(focus_universe.get("selected_tickers") or []) if isinstance(row, dict)]
    gather = dict(stage_details.get("gather") or {})
    ocr = dict(stage_details.get("ocr") or {})
    hippo = dict(stage_details.get("hipporag") or {})
    confidence = dict(quality.get("confidence_band") or {})
    confidence_label = str(confidence.get("label") or "low").strip().lower() or "low"
    confidence_score = {"low": 25, "medium": 65, "high": 90}.get(confidence_label, 25)
    theme_score = float(selected_theme.get("theme_score") or 0.0)
    gather_sources = int(gather.get("accepted_sources") or 0)
    ocr_docs = int(ocr.get("documents") or 0)
    hippo_processed = int(hippo.get("processed_finance") or hippo.get("processed") or 0)
    hippo_triples = int(hippo.get("triples") or 0)
    market = dict(stage_details.get("market_study") or {})
    market_snapshot_confidence = float(market.get("avg_snapshot_confidence") or 0.0)
    market_catalyst_count = int(market.get("catalyst_count") or 0)
    market_blocked = bool(market.get("blocked_session"))
    market_score = min(
        100.0,
        (market_snapshot_confidence * 0.65)
        + (min(market_catalyst_count, 8) / 8.0 * 25.0)
        + (10.0 if market.get("has_trade_proposal") else 0.0),
    )
    volume_score = min(
        100.0,
        (min(gather_sources, 6) / 6.0 * 25.0)
        + (min(ocr_docs, 6) / 6.0 * 14.0)
        + (min(hippo_processed, 12) / 12.0 * 25.0)
        + (min(hippo_triples, 12) / 12.0 * 16.0)
        + (min(market_score, 100.0) * 0.10)
        + (min(theme_score, 100.0) * 0.10),
    )
    overall = int(quality.get("overall_score") or 0)
    gate_pass = bool(quality.get("gate_pass"))
    ready = bool(
        gate_pass
        and theme_score >= 65.0
        and volume_score >= 60.0
        and confidence_label in {"medium", "high"}
        and len(selected_tickers) >= 5
        and not market_blocked
    )
    if ready and confidence_label == "high" and volume_score >= 80.0:
        timing_mode = "next_market_open_plus_15m"
        offset_minutes = 15
    elif ready:
        timing_mode = "next_market_open_plus_60m"
        offset_minutes = 60
    else:
        timing_mode = "defer_and_continue_research"
        offset_minutes = -1
    reasons: List[str] = []
    if not gate_pass:
        reasons.append("pipeline_quality_gate_failed")
    if theme_score < 65.0:
        reasons.append(f"theme_score_below_target:{round(theme_score,2)}<65")
    if volume_score < 60.0:
        reasons.append(f"volume_score_below_target:{round(volume_score,2)}<60")
    if confidence_label not in {"medium", "high"}:
        reasons.append(f"confidence_too_low:{confidence_label}")
    if len(selected_tickers) < 5:
        reasons.append(f"selected_tickers_below_min:{len(selected_tickers)}<5")
    if market_blocked:
        reasons.append("market_browser_session_blocked")
    return {
        "computed": True,
        "decision_ready": ready,
        "decision_mode": timing_mode,
        "decision_offset_minutes": offset_minutes,
        "confidence_label": confidence_label,
        "confidence_score": confidence_score,
        "volume_score": round(volume_score, 2),
        "theme_score": round(theme_score, 2),
        "market_score": round(market_score, 2),
        "market_snapshot_confidence": round(market_snapshot_confidence, 2),
        "market_catalyst_count": market_catalyst_count,
        "market_blocked_session": market_blocked,
        "overall_quality_score": overall,
        "selected_theme_label": str(selected_theme.get("label") or "").strip(),
        "selected_ticker_count": len(selected_tickers),
        "recommended_use": "Apollo Daily Decision" if ready else "continue_research_only",
        "reasons": reasons,
    }
