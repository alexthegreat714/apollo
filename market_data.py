from __future__ import annotations

import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus

import requests


_APOLLO_ROOT = Path(__file__).resolve().parent
STATE_PATH = _APOLLO_ROOT / "logs" / "market_study" / "state.json"
BROWSER_STATUS_PATH = _APOLLO_ROOT / "logs" / "market_study" / "browser_status.json"
USER_AGENT = os.getenv(
    "APOLLO_MARKET_USER_AGENT",
    "ApolloMarketStudy/1.0 contact=local",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_ticker(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9.\-]", "", str(value or "").strip()).upper()
    return text[:12]


def normalize_tickers(payload: Dict[str, Any]) -> List[str]:
    raw = payload.get("tickers")
    if raw is None:
        raw = payload.get("ticker")
    if isinstance(raw, str):
        parts = re.split(r"[\s,;|]+", raw)
    elif isinstance(raw, list):
        parts = [str(item) for item in raw]
    else:
        parts = []
    out: List[str] = []
    for item in parts:
        ticker = normalize_ticker(item)
        if ticker and ticker not in out:
            out.append(ticker)
    return out[:24]


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return None
        return int(float(value))
    except Exception:
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc).replace(microsecond=0)
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0)
    except Exception:
        return None


def normalize_market_snapshot(raw: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    ticker = normalize_ticker(str(raw.get("ticker") or ""))
    now_dt = now or _utc_now()
    price = _safe_float(raw.get("price"))
    prior_close = _safe_float(raw.get("prior_close"))
    volume = _safe_int(raw.get("volume"))
    avg_volume = _safe_int(raw.get("avg_volume"))
    ts = _parse_ts(raw.get("timestamp")) or now_dt
    age_sec = max(0.0, (now_dt - ts).total_seconds())
    gap_pct: Optional[float] = None
    if price is not None and prior_close and prior_close > 0:
        gap_pct = round(((price - prior_close) / prior_close) * 100.0, 3)
    rel_volume: Optional[float] = None
    if volume is not None and avg_volume and avg_volume > 0:
        rel_volume = round(float(volume) / float(avg_volume), 3)

    confidence = 0.0
    if ticker:
        confidence += 15.0
    if price is not None:
        confidence += 30.0
    if prior_close is not None:
        confidence += 15.0
    if volume is not None:
        confidence += 10.0
    if avg_volume is not None:
        confidence += 5.0
    if age_sec <= 20 * 60:
        confidence += 20.0
    elif age_sec <= 6 * 3600:
        confidence += 12.0
    elif age_sec <= 36 * 3600:
        confidence += 5.0
    confidence = round(max(0.0, min(100.0, confidence)), 2)

    freshness = "fresh"
    if age_sec > 36 * 3600:
        freshness = "stale"
    elif age_sec > 6 * 3600:
        freshness = "delayed"

    return {
        "ok": bool(ticker and price is not None),
        "ticker": ticker,
        "price": price,
        "prior_close": prior_close,
        "gap_pct": gap_pct,
        "volume": volume,
        "avg_volume": avg_volume,
        "relative_volume": rel_volume,
        "timestamp": _utc_iso(ts),
        "age_seconds": int(age_sec),
        "source": str(raw.get("source") or "unknown"),
        "freshness": freshness,
        "confidence": confidence,
        "error": str(raw.get("error") or ""),
    }


def _http_get(url: str, *, timeout: int = 8, headers: Optional[Dict[str, str]] = None) -> requests.Response:
    merged_headers = {"User-Agent": USER_AGENT}
    merged_headers.update(headers or {})
    return requests.get(url, timeout=timeout, headers=merged_headers)


def _snapshot_yahoo_chart(ticker: str) -> Dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(ticker)}?range=10d&interval=1d"
    resp = _http_get(url, timeout=8)
    if not resp.ok:
        return {"ticker": ticker, "source": "yahoo_chart", "error": f"http_{resp.status_code}"}
    payload = resp.json() if resp.content else {}
    result = (((payload.get("chart") or {}).get("result") or []) or [{}])[0]
    meta = result.get("meta") or {}
    quote = (((result.get("indicators") or {}).get("quote") or []) or [{}])[0]
    closes = [value for value in list((quote or {}).get("close") or []) if value is not None]
    volumes = [value for value in list((quote or {}).get("volume") or []) if value is not None]
    timestamps = list(result.get("timestamp") or [])
    price = _safe_float(meta.get("regularMarketPrice"))
    if price is None and closes:
        price = _safe_float(closes[-1])
    prior_close = _safe_float(meta.get("chartPreviousClose"))
    if prior_close is None and len(closes) >= 2:
        prior_close = _safe_float(closes[-2])
    avg_volume = None
    if volumes:
        avg_volume = int(sum(int(v or 0) for v in volumes) / max(1, len(volumes)))
    ts = _safe_int(meta.get("regularMarketTime"))
    if ts is None and timestamps:
        ts = _safe_int(timestamps[-1])
    return normalize_market_snapshot(
        {
            "ticker": ticker,
            "price": price,
            "prior_close": prior_close,
            "volume": volumes[-1] if volumes else None,
            "avg_volume": avg_volume,
            "timestamp": ts,
            "source": "yahoo_chart",
        }
    )


def _snapshot_stooq(ticker: str) -> Dict[str, Any]:
    symbol = ticker.lower()
    if "." not in symbol:
        symbol = f"{symbol}.us"
    url = f"https://stooq.com/q/l/?s={quote_plus(symbol)}&f=sd2t2ohlcv&h&e=csv"
    resp = _http_get(url, timeout=8)
    if not resp.ok:
        return {"ticker": ticker, "source": "stooq", "error": f"http_{resp.status_code}"}
    rows = list(csv.DictReader(resp.text.splitlines()))
    row = rows[0] if rows else {}
    date_text = str(row.get("Date") or "").strip()
    time_text = str(row.get("Time") or "").strip()
    ts = _utc_now()
    if date_text:
        try:
            parsed = datetime.fromisoformat(f"{date_text}T{time_text or '20:00:00'}+00:00")
            ts = parsed.astimezone(timezone.utc)
        except Exception:
            pass
    return normalize_market_snapshot(
        {
            "ticker": ticker,
            "price": row.get("Close"),
            "prior_close": row.get("Open"),
            "volume": row.get("Volume"),
            "timestamp": ts,
            "source": "stooq",
        }
    )


def fetch_market_snapshot(ticker: str) -> Dict[str, Any]:
    ticker_norm = normalize_ticker(ticker)
    if not ticker_norm:
        return {"ok": False, "ticker": "", "error": "ticker_required"}
    errors: List[str] = []
    for fetcher in (_snapshot_yahoo_chart, _snapshot_stooq):
        try:
            row = fetcher(ticker_norm)
        except Exception as exc:
            errors.append(f"{fetcher.__name__}:{type(exc).__name__}")
            continue
        if row.get("ok"):
            if errors:
                row["fallback_errors"] = errors
            return row
        if row.get("error"):
            errors.append(f"{row.get('source')}:{row.get('error')}")
    return normalize_market_snapshot({"ticker": ticker_norm, "source": "unavailable", "error": ";".join(errors) or "no_source"})


def _browser_status() -> Dict[str, Any]:
    path = Path(os.getenv("APOLLO_MARKET_BROWSER_STATUS_FILE", str(BROWSER_STATUS_PATH)))
    data = _read_json(path, {})
    status = str(data.get("status") or "").strip().lower()
    blocked = status in {"blocked", "auth_required", "captcha", "login_required"} or bool(data.get("blocked"))
    return {
        "ok": not blocked,
        "status": status or ("ok" if data else "not_configured"),
        "blocked": blocked,
        "reason": str(data.get("reason") or data.get("error") or ""),
        "recommended_action": (
            "Refresh the saved market browser session manually; Apollo will continue with API sources."
            if blocked
            else ""
        ),
        "path": str(path),
        "updated_at": str(data.get("updated_at") or ""),
    }


def market_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    tickers = normalize_tickers(payload)
    if not tickers:
        return {"ok": False, "error": "ticker_required", "snapshots": []}
    state = load_market_state()
    cache = dict(state.get("snapshots") or {})
    refresh = bool(payload.get("refresh", True))
    snapshots: List[Dict[str, Any]] = []
    for ticker in tickers:
        row = dict(cache.get(ticker) or {})
        if refresh or not row:
            row = fetch_market_snapshot(ticker)
        snapshots.append(row)
        if row.get("ok"):
            cache[ticker] = row
    state["snapshots"] = cache
    state["updated_at"] = _utc_iso()
    browser = _browser_status()
    if browser.get("blocked"):
        state["last_blocked_browser_session"] = browser
        state["blocked_sessions"] = [browser, *list(state.get("blocked_sessions") or [])][:10]
    state["source_health"] = {
        "browser": browser,
        "api": {"ok": any(row.get("ok") for row in snapshots), "source": "yahoo_chart_then_stooq"},
    }
    save_market_state(state)
    return {"ok": any(row.get("ok") for row in snapshots), "snapshots": snapshots, "source_health": state["source_health"]}


def _google_news(ticker: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    query = quote_plus(f"{ticker} stock earnings guidance market news")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    resp = _http_get(url, timeout=8)
    if not resp.ok:
        return []
    root = ET.fromstring(resp.text)
    rows: List[Dict[str, Any]] = []
    for item in root.findall(".//item")[:max_results]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if title and link:
            rows.append(
                {
                    "kind": "news",
                    "ticker": ticker,
                    "title": title,
                    "url": link,
                    "published_at": pub_date,
                    "source": "google_news_rss",
                    "confidence": 0.62,
                }
            )
    return rows


@lru_cache(maxsize=1)
def _sec_ticker_map() -> Dict[str, str]:
    resp = _http_get("https://www.sec.gov/files/company_tickers.json", timeout=10, headers={"Accept": "application/json"})
    if not resp.ok:
        return {}
    payload = resp.json() if resp.content else {}
    out: Dict[str, str] = {}
    if isinstance(payload, dict):
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker = normalize_ticker(str(row.get("ticker") or ""))
            cik = _safe_int(row.get("cik_str"))
            if ticker and cik:
                out[ticker] = f"{cik:010d}"
    return out


def _sec_recent_submissions(ticker: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    cik = _sec_ticker_map().get(normalize_ticker(ticker))
    if not cik:
        return []
    resp = _http_get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=10, headers={"Accept": "application/json"})
    if not resp.ok:
        return []
    payload = resp.json() if resp.content else {}
    recent = ((payload.get("filings") or {}).get("recent") or {}) if isinstance(payload, dict) else {}
    forms = list(recent.get("form") or [])
    accession_numbers = list(recent.get("accessionNumber") or [])
    primary_documents = list(recent.get("primaryDocument") or [])
    filing_dates = list(recent.get("filingDate") or [])
    accepted_dates = list(recent.get("acceptanceDateTime") or [])
    rows: List[Dict[str, Any]] = []
    target_forms = {"8-K", "10-Q", "10-K"}
    for idx, form in enumerate(forms):
        form_text = str(form or "").strip().upper()
        if form_text not in target_forms:
            continue
        accession = str(accession_numbers[idx] if idx < len(accession_numbers) else "").strip()
        doc = str(primary_documents[idx] if idx < len(primary_documents) else "").strip()
        if not accession or not doc:
            continue
        accession_path = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{doc}"
        filed = str(filing_dates[idx] if idx < len(filing_dates) else "").strip()
        accepted = str(accepted_dates[idx] if idx < len(accepted_dates) else "").strip()
        rows.append(
            {
                "kind": "sec_filing",
                "ticker": normalize_ticker(ticker),
                "title": f"{normalize_ticker(ticker)} {form_text} filed {filed}".strip(),
                "url": url,
                "published_at": accepted or filed,
                "source": "sec_submissions_api",
                "confidence": 0.88,
            }
        )
        if len(rows) >= max_results:
            break
    return rows


def _sec_recent_atom_filings(ticker: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={quote_plus(ticker)}&type=8-K%2C10-Q%2C10-K&owner=exclude&count={max_results}&output=atom"
    )
    try:
        resp = _http_get(url, timeout=8, headers={"Accept": "application/atom+xml"})
    except Exception:
        return []
    if not resp.ok:
        return []
    try:
        root = ET.fromstring(resp.text)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns) or root.findall("entry")
    for entry in entries[:max_results]:
        title = (entry.findtext("a:title", default="", namespaces=ns) or entry.findtext("title") or "").strip()
        updated = (entry.findtext("a:updated", default="", namespaces=ns) or entry.findtext("updated") or "").strip()
        link_el = entry.find("a:link", ns)
        if link_el is None:
            link_el = entry.find("link")
        href = str(link_el.attrib.get("href") if link_el is not None else "").strip()
        if title or href:
            rows.append(
                {
                    "kind": "sec_filing",
                    "ticker": ticker,
                    "title": title or f"{ticker} SEC filing",
                    "url": href,
                    "published_at": updated,
                    "source": "sec_edgar_atom",
                    "confidence": 0.8,
                }
            )
    return rows


def _sec_recent_filings(ticker: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    try:
        rows = _sec_recent_submissions(ticker, max_results=max_results)
        if rows:
            return rows
    except Exception:
        pass
    return _sec_recent_atom_filings(ticker, max_results=max_results)


def _dedupe_catalysts(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("url") or row.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _load_sec_cache(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(state or load_market_state())
    cache = dict(payload.get("sec_cache") or {})
    cache.setdefault("filings_by_ticker", {})
    cache.setdefault("rate_limited_until", "")
    return cache


def _cache_sec_filings(state: Dict[str, Any], ticker: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    cache = _load_sec_cache(state)
    filings = dict(cache.get("filings_by_ticker") or {})
    filings[normalize_ticker(ticker)] = {"updated_at": _utc_iso(), "rows": rows[:10]}
    cache["filings_by_ticker"] = filings
    cache["updated_at"] = _utc_iso()
    state["sec_cache"] = cache


def _cached_sec_filings(state: Dict[str, Any], ticker: str) -> List[Dict[str, Any]]:
    cache = _load_sec_cache(state)
    row = dict((cache.get("filings_by_ticker") or {}).get(normalize_ticker(ticker)) or {})
    rows = [dict(item) for item in list(row.get("rows") or []) if isinstance(item, dict)]
    for item in rows:
        item["source"] = f"cache:{item.get('source') or 'sec'}"
        item["cached_at"] = str(row.get("updated_at") or "")
    return rows


def market_catalysts(payload: Dict[str, Any]) -> Dict[str, Any]:
    tickers = normalize_tickers(payload)
    if not tickers:
        return {"ok": False, "error": "ticker_required", "catalysts": []}
    max_results = max(1, min(int(payload.get("max_results") or 5), 10))
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    browser = _browser_status()
    source_health = {"news": {"ok": False}, "sec": {"ok": False}, "browser": browser}
    state = load_market_state()
    for ticker in tickers:
        rows: List[Dict[str, Any]] = []
        try:
            news = _google_news(ticker, max_results=max_results)
            rows.extend(news)
            source_health["news"]["ok"] = bool(source_health["news"].get("ok") or news)
        except Exception as exc:
            source_health["news"]["error"] = f"{type(exc).__name__}:{exc}"
        try:
            filings = _sec_recent_filings(ticker, max_results=max_results)
            rows.extend(filings)
            source_health["sec"]["ok"] = bool(source_health["sec"].get("ok") or filings)
            _cache_sec_filings(state, ticker, filings)
        except Exception as exc:
            source_health["sec"]["error"] = f"{type(exc).__name__}:{exc}"
            filings = []
        if not filings:
            cached = _cached_sec_filings(state, ticker)
            if cached:
                rows.extend(cached[:max_results])
                source_health["sec"]["fallback"] = "last_good_cache"
                source_health["sec"]["ok"] = True
        by_ticker[ticker] = _dedupe_catalysts(rows)[: max_results * 2]
    state["catalysts"] = by_ticker
    if browser.get("blocked"):
        state["last_blocked_browser_session"] = browser
        state["blocked_sessions"] = [browser, *list(state.get("blocked_sessions") or [])][:10]
    state["source_health"] = {**dict(state.get("source_health") or {}), **source_health}
    state["updated_at"] = _utc_iso()
    save_market_state(state)
    return {
        "ok": any(by_ticker.values()),
        "catalysts": by_ticker,
        "source_health": source_health,
    }


def load_market_state(path: Optional[str] = None) -> Dict[str, Any]:
    state_path = Path(path or os.getenv("APOLLO_MARKET_STATE_PATH", str(STATE_PATH)))
    state = _read_json(state_path, {})
    state.setdefault("snapshots", {})
    state.setdefault("catalysts", {})
    state.setdefault("source_health", {})
    state.setdefault("blocked_sessions", [])
    state["state_path"] = str(state_path)
    return state


def save_market_state(state: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    state_path = Path(path or os.getenv("APOLLO_MARKET_STATE_PATH", str(STATE_PATH)))
    payload = dict(state or {})
    payload["updated_at"] = _utc_iso()
    payload["state_path"] = str(state_path)
    _write_json(state_path, payload)
    return payload


__all__ = [
    "fetch_market_snapshot",
    "load_market_state",
    "market_catalysts",
    "market_snapshot",
    "normalize_market_snapshot",
    "normalize_ticker",
    "normalize_tickers",
    "save_market_state",
]
