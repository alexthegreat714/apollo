from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests

from Apollo import daily_report, market_data, swing_study


_APOLLO_ROOT = Path(__file__).resolve().parent
RUN_ROOT = _APOLLO_ROOT / "logs" / "pretrade_gate"
LATEST_STATUS_PATH = RUN_ROOT / "latest_status.json"
CT = ZoneInfo("America/Chicago")

DECISIONS = {
    "clear_for_paper_sim",
    "delay_entry",
    "size_down",
    "blocked_by_news",
    "blocked_by_price",
    "needs_human_review",
    "no_action_due",
}

NEGATIVE_KEYWORDS = {
    "downgrade",
    "sell",
    "miss",
    "misses",
    "warn",
    "warning",
    "probe",
    "investigation",
    "lawsuit",
    "recall",
    "guidance cut",
    "below expectations",
    "fraud",
    "sec",
    "delay",
    "cuts outlook",
}

POSITIVE_KEYWORDS = {
    "upgrade",
    "buy",
    "beat",
    "beats",
    "raise",
    "raises",
    "record",
    "outperform",
    "partnership",
    "deal",
    "contract",
    "guidance raised",
}


QuoteProvider = Callable[[str], Dict[str, Any]]
NewsProvider = Callable[[str, int], List[Dict[str, Any]]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now_utc()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ct_stamp(dt: Optional[datetime] = None) -> str:
    return (dt or _now_utc()).astimezone(CT).strftime("%H:%M CST")


def _ct_date(dt: Optional[datetime] = None) -> str:
    return (dt or _now_utc()).astimezone(CT).date().isoformat()


def _slug(value: Any, fallback: str = "pretrade_gate") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")
    return text[:96] or fallback


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
        if math.isfinite(num):
            return num
    except Exception:
        pass
    return default


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _minutes_since(value: Any) -> Optional[float]:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (_now_utc() - parsed).total_seconds() / 60.0)


def _parse_zone(value: Any) -> tuple[float, float]:
    text = str(value or "").replace("$", "").replace("\u2013", "-").replace("\u2014", "-").strip()
    parts = re.split(r"\s*-\s*", text)
    try:
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
        val = float(parts[0])
        return val * 0.995, val * 1.005
    except Exception:
        return 0.0, 0.0


def _parse_price(value: Any) -> float:
    text = str(value or "").replace("$", "").replace("\u2013", "-").replace("\u2014", "-").strip()
    parts = re.split(r"\s*-\s*", text)
    try:
        return float(parts[0])
    except Exception:
        return 0.0


def headline_sentiment(text: str) -> float:
    lower = str(text or "").lower()
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in lower)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in lower)
    total = pos + neg
    if not total:
        return 0.0
    return round((pos - neg) / float(total), 3)


def fetch_ticker_news(ticker: str, max_items: int = 8) -> List[Dict[str, Any]]:
    ticker_norm = market_data.normalize_ticker(ticker)
    if not ticker_norm:
        return []
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(ticker_norm)}&region=US&lang=en-US"
    try:
        response = requests.get(url, timeout=8, headers={"User-Agent": market_data.USER_AGENT})
        if not response.ok:
            return []
        import feedparser

        feed = feedparser.parse(response.content)
        out: List[Dict[str, Any]] = []
        for entry in list(feed.entries or [])[:max_items]:
            text = f"{entry.get('title', '')} {entry.get('summary', '')}"
            out.append(
                {
                    "title": str(entry.get("title") or ""),
                    "link": str(entry.get("link") or ""),
                    "published": str(entry.get("published") or ""),
                    "sentiment": headline_sentiment(text),
                }
            )
        return out
    except Exception:
        return []


def latest_status() -> Dict[str, Any]:
    return _read_json(LATEST_STATUS_PATH, {})


def _candidate_is_actionable(row: Dict[str, Any]) -> bool:
    setup = str(row.get("setup_type") or "").lower()
    status = str(row.get("status") or "").lower()
    if setup in {"no_trade", "broken_trend", "extended_no_chase"}:
        return False
    if status in {"rejected", "invalidated", "expired"}:
        return False
    direction = str(row.get("direction_bias") or "long_bias")
    if direction not in {"", "long_bias", "short_bias"}:
        return False
    if direction == "short_bias" and setup != "event_dislocation":
        return False
    score = _safe_float(row.get("score") or row.get("confidence"), 0.0)
    rr = _safe_float(row.get("risk_reward_estimate"), 0.0)
    return bool(score >= 75.0 or row.get("paper_order_candidate") or status in {"watching", "confirmed", "paper_open"}) and rr >= 1.0


def choose_candidate(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
    explicit = payload.get("candidate")
    if isinstance(explicit, dict) and explicit.get("ticker"):
        return dict(explicit)
    latest = payload.get("latest_study") if isinstance(payload.get("latest_study"), dict) else swing_study.latest_swing_study()
    proposals = [dict(row) for row in list((latest or {}).get("proposals") or []) if isinstance(row, dict)]
    if ticker:
        proposals = [row for row in proposals if market_data.normalize_ticker(str(row.get("ticker") or "")) == ticker]
    ready = [row for row in proposals if _candidate_is_actionable(row)]
    ready.sort(key=lambda row: (_safe_float(row.get("score") or row.get("confidence"), 0.0), _safe_float(row.get("risk_reward_estimate"), 0.0)), reverse=True)
    if ready:
        return ready[0]
    best = dict((latest or {}).get("best_proposal") or (proposals[0] if proposals else {}))
    return best if ticker and best.get("ticker") else {}


def _negative_headlines(headlines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in headlines:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        sentiment = item.get("sentiment")
        if sentiment is None:
            sentiment = headline_sentiment(text)
            item["sentiment"] = sentiment
        if _safe_float(sentiment, 0.0) < 0:
            out.append(dict(item))
    return out


def _positive_headlines(headlines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in headlines:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        sentiment = item.get("sentiment")
        if sentiment is None:
            sentiment = headline_sentiment(text)
            item["sentiment"] = sentiment
        if _safe_float(sentiment, 0.0) > 0:
            out.append(dict(item))
    return out


def _live_guard() -> Dict[str, Any]:
    enabled = str(os.getenv("APOLLO_SIM_LIVE_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on", "y"}
    return {
        "ok": not enabled,
        "live_trade_execution": bool(enabled),
        "detail": "APOLLO_SIM_LIVE_ENABLED is disabled" if not enabled else "APOLLO_SIM_LIVE_ENABLED is enabled",
    }


def evaluate_gate(
    candidate: Dict[str, Any],
    *,
    quote: Dict[str, Any],
    headlines: List[Dict[str, Any]],
    trigger: str,
) -> Dict[str, Any]:
    ticker = market_data.normalize_ticker(str(candidate.get("ticker") or ""))
    if not ticker:
        return {"decision": "no_action_due", "blocker": "no_active_candidate", "next_action": "Keep the paper account in cash until a candidate clears setup rules.", "score": 0}

    no_trade = str(candidate.get("no_trade_reason") or "").strip()
    direction_bias = str(candidate.get("direction_bias") or "long_bias")
    rr = _safe_float(candidate.get("risk_reward_estimate"), 0.0)
    score = _safe_float(candidate.get("score") or candidate.get("confidence"), 0.0)
    live_guard = _live_guard()
    if not live_guard.get("ok"):
        return {"decision": "needs_human_review", "blocker": "live_execution_guard_enabled", "next_action": "Disable live execution before any paper-sim submit.", "score": min(score, 20)}
    if no_trade:
        return {"decision": "delay_entry", "blocker": no_trade, "next_action": "Wait until the setup clears its no-trade reason.", "score": min(score, 45)}
    if rr and rr < 1.0:
        return {"decision": "delay_entry", "blocker": f"risk_reward_below_1:{rr:.2f}", "next_action": "Wait for a better entry or target/stop structure.", "score": min(score, 50)}
    if not quote.get("ok"):
        return {"decision": "needs_human_review", "blocker": quote.get("error") or "quote_unavailable", "next_action": "Do not submit paper orders until a fresh quote is available.", "score": min(score, 55)}
    if not headlines:
        return {"decision": "needs_human_review", "blocker": "no_fresh_news_coverage", "next_action": "Review current coverage manually before paper execution.", "score": min(score, 60)}

    negative = _negative_headlines(headlines)
    positive = _positive_headlines(headlines)
    if direction_bias == "short_bias" and positive:
        return {"decision": "blocked_by_news", "blocker": str(positive[0].get("title") or "positive_headline_against_short"), "next_action": "Pause short paper simulation and reassess the thesis.", "score": min(score, 40)}
    if direction_bias != "short_bias" and negative:
        return {"decision": "blocked_by_news", "blocker": str(negative[0].get("title") or "negative_headline"), "next_action": "Pause paper execution and reassess the thesis.", "score": min(score, 40)}

    price = _safe_float(quote.get("price"), 0.0)
    entry_low, entry_high = _parse_zone(candidate.get("entry_zone"))
    stop = _parse_price(candidate.get("stop_zone") or candidate.get("stop"))
    if direction_bias == "short_bias" and stop > 0 and price > 0 and price >= stop:
        return {"decision": "blocked_by_price", "blocker": f"price_at_or_above_short_stop:{price:.2f}>={stop:.2f}", "next_action": "Invalidate or rebuild the short setup before paper execution.", "score": min(score, 35)}
    if direction_bias != "short_bias" and stop > 0 and price > 0 and price <= stop:
        return {"decision": "blocked_by_price", "blocker": f"price_at_or_below_stop:{price:.2f}<={stop:.2f}", "next_action": "Invalidate or rebuild the setup before paper execution.", "score": min(score, 35)}
    if direction_bias == "short_bias" and entry_low > 0 and price > 0 and price < entry_low * 0.97:
        return {"decision": "delay_entry", "blocker": "price_extended_below_short_entry_zone", "next_action": "Do not chase downside; wait for a reset or new levels.", "score": min(score, 68)}
    if direction_bias == "short_bias" and entry_high > 0 and price > entry_high * 1.02:
        return {"decision": "delay_entry", "blocker": "price_above_short_entry_zone", "next_action": "Wait for price to reject back into the short entry zone.", "score": min(score, 68)}
    if direction_bias != "short_bias" and entry_low > 0 and price > 0 and price < entry_low * 0.98:
        return {"decision": "delay_entry", "blocker": "price_below_entry_zone", "next_action": "Wait for price to reclaim the entry zone.", "score": min(score, 68)}
    if direction_bias != "short_bias" and entry_high > 0 and price > entry_high * 1.03:
        return {"decision": "delay_entry", "blocker": "price_extended_above_entry_zone", "next_action": "Do not chase; wait for a reset or new levels.", "score": min(score, 68)}
    if rr and rr < 1.25:
        return {"decision": "size_down", "blocker": "thin_risk_reward", "next_action": "Paper-sim only with smaller simulated size if approved.", "score": min(score, 75)}
    return {"decision": "clear_for_paper_sim", "blocker": "", "next_action": "Paper simulation may proceed after normal approval.", "score": score or 80}


def _markdown_report(result: Dict[str, Any]) -> str:
    candidate = dict(result.get("candidate") or {})
    quote = dict(result.get("quote") or {})
    lines = [
        f"# Apollo Pre-Trade Gate - {result.get('run_id')}",
        "",
        f"- Checked: {result.get('checked_at_ct')}",
        f"- Trigger: `{result.get('trigger')}`",
        f"- Ticker: `{candidate.get('ticker') or 'none'}`",
        f"- Direction: `{candidate.get('direction_bias') or 'long_bias'}`",
        f"- Decision: `{result.get('decision')}`",
        f"- Blocker: `{result.get('blocker') or 'none'}`",
        f"- Next action: {result.get('next_action')}",
        f"- Live guard: `{(result.get('live_guard') or {}).get('detail')}`",
        "",
        "## Candidate",
        "",
        f"- Setup: `{candidate.get('setup_type') or ''}`",
        f"- Score: `{candidate.get('score') or candidate.get('confidence')}`",
        f"- Entry: `{candidate.get('entry_zone') or ''}`",
        f"- Stop: `{candidate.get('stop_zone') or ''}`",
        f"- Target: `{candidate.get('target_zone') or ''}`",
        f"- R/R: `{candidate.get('risk_reward_estimate')}`",
        "",
        "## Quote",
        "",
        f"- Source: `{quote.get('source') or ''}`",
        f"- Price: `{quote.get('price')}`",
        f"- Freshness: `{quote.get('freshness') or ''}`",
        f"- Confidence: `{quote.get('confidence')}`",
        "",
        "## Headlines",
        "",
    ]
    headlines = list(result.get("headlines") or [])
    if headlines:
        for item in headlines[:8]:
            lines.append(f"- `{item.get('sentiment', 0)}` {item.get('title') or ''}")
    else:
        lines.append("- No current headlines were returned by the news provider.")
    lines.extend(["", "This gate is paper-simulation only and never places live orders."])
    return "\n".join(lines).strip() + "\n"


def _save_result(result: Dict[str, Any], run_dir: Path) -> Dict[str, Any]:
    result["json_path"] = str(run_dir / "status.json")
    result["markdown_path"] = str(run_dir / "report.md")
    _write_json(run_dir / "status.json", result)
    (run_dir / "report.md").write_text(_markdown_report(result), encoding="utf-8")
    _write_json(LATEST_STATUS_PATH, result)
    return result


def run_pretrade_gate(
    payload: Optional[Dict[str, Any]] = None,
    *,
    quote_provider: Optional[QuoteProvider] = None,
    news_provider: Optional[NewsProvider] = None,
) -> Dict[str, Any]:
    payload = dict(payload or {})
    trigger = str(payload.get("trigger") or "planner").strip() or "planner"
    force = bool(payload.get("force")) or trigger in {"pre_submit", "manual", "api"}
    stale_after = max(1, int(_safe_float(payload.get("stale_after_minutes") or os.getenv("APOLLO_PRETRADE_STALE_MINUTES", "20"), 20)))
    latest = latest_status()
    if not force and latest:
        age = _minutes_since(latest.get("checked_at"))
        requested_ticker = market_data.normalize_ticker(str(payload.get("ticker") or ""))
        latest_ticker = market_data.normalize_ticker(str((latest.get("candidate") or {}).get("ticker") or ""))
        if age is not None and age < stale_after and (not requested_ticker or requested_ticker == latest_ticker):
            reused = dict(latest)
            reused["ok"] = True
            reused["reused_recent_gate"] = True
            reused["reuse_age_minutes"] = round(age, 2)
            return reused

    candidate = choose_candidate(payload)
    run_id = _slug(payload.get("run_id") or f"pretrade_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    run_dir = RUN_ROOT / _ct_date() / run_id
    ticker = market_data.normalize_ticker(str(candidate.get("ticker") or ""))
    quote_fetch = quote_provider or market_data.fetch_market_snapshot
    news_fetch = news_provider or fetch_ticker_news
    quote = quote_fetch(ticker) if ticker else {"ok": False, "ticker": "", "error": "no_active_candidate"}
    headlines = news_fetch(ticker, 8) if ticker else []
    evaluation = evaluate_gate(candidate, quote=quote, headlines=headlines, trigger=trigger)
    result = {
        "ok": True,
        "run_id": run_id,
        "checked_at": _utc_iso(),
        "checked_at_ct": _ct_stamp(),
        "trigger": trigger,
        "decision": evaluation["decision"],
        "blocker": evaluation.get("blocker", ""),
        "next_action": evaluation.get("next_action", ""),
        "score": evaluation.get("score", 0),
        "candidate": candidate,
        "quote": quote,
        "headlines": headlines,
        "negative_headlines": _negative_headlines(headlines),
        "live_guard": _live_guard(),
        "paper_only": True,
        "heavy_daytime_hipporag_refresh": False,
    }
    _save_result(result, run_dir)
    try:
        daily_report.record_gate_result(result)
    except Exception as exc:
        result["daily_report_error"] = str(exc)
        _save_result(result, run_dir)
    return result


def run_pre_submit_gate(
    simulation_or_plan: Dict[str, Any],
    *,
    force: bool = True,
    quote_provider: Optional[QuoteProvider] = None,
    news_provider: Optional[NewsProvider] = None,
) -> Dict[str, Any]:
    simulation = dict(simulation_or_plan.get("simulation") or simulation_or_plan or {})
    candidate = {
        "ticker": simulation.get("ticker"),
        "setup_type": simulation.get("setup_type") or "simulation_execution",
        "score": simulation.get("score") or simulation.get("confidence") or 80,
        "entry_zone": simulation.get("entry_zone"),
        "stop_zone": simulation.get("stop_zone") or simulation.get("stop"),
        "target_zone": simulation.get("target_zone") or simulation.get("target"),
        "risk_reward_estimate": simulation.get("rr_proposed") or simulation.get("risk_reward_estimate") or 1.25,
        "direction_bias": simulation.get("direction_bias") or "long_bias",
    }
    return run_pretrade_gate(
        {
            "trigger": "pre_submit",
            "force": force,
            "candidate": candidate,
            "run_id": f"pre_submit_{simulation.get('ticker') or simulation_or_plan.get('execution_id') or 'paper'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        },
        quote_provider=quote_provider,
        news_provider=news_provider,
    )


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Apollo lightweight daytime pre-trade gate.")
    parser.add_argument("action", nargs="?", default="check", choices=["check", "status"])
    parser.add_argument("--ticker", default="")
    parser.add_argument("--trigger", default="planner")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--date", default="")
    args = parser.parse_args(argv)
    if args.action == "status":
        result = latest_status()
    else:
        if args.date:
            daily_report.ensure_daily_report(args.date)
        result = run_pretrade_gate({"ticker": args.ticker, "trigger": args.trigger, "force": args.force})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
