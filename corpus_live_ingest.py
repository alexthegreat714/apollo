"""
Live ingestion for EDGAR filings (8-K, 10-Q, 10-K) and equity news RSS.

Pulls fresh per-ticker data for all tickers in the focus universe and
upserts into the ChromaDB financial corpus. Each embedding carries rich
metadata: ticker, theme, filing_type, filed_at, pack.

Entry point: run_live_ingest(cfg) or individual ingest_* functions.
Triggered by POST /corpus/live_ingest, the nightly pipeline, or
Windows Task Scheduler via watchdog/run_apollo_corpus_refresh.ps1.

  python -m Apollo.corpus_live_ingest          # run ingest now
  python -m Apollo.corpus_live_ingest schedule  # install schtask + Sky event
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, time as dt_time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

_APOLLO_ROOT = Path(__file__).resolve().parent
_DEFAULT_RAG_DIR = str((_APOLLO_ROOT / "chroma_db").resolve())
_DEFAULT_COLLECTION = os.getenv("RAG_COLLECTION", "apollo_financial")
_CHUNK_SIZE = 1400
_CHUNK_OVERLAP = 180
_MAX_CHARS = 28000
_EDGAR_DELAY_S = 0.25
_REQUEST_TIMEOUT = 20
_EDGAR_UA = "ApolloLiveIngest/1.0 alexthegreat123@gmail.com"
_MAX_FILING_BYTES = 15 * 1024 * 1024  # cap before string decode to avoid MemoryError
_YF_UA = "ApolloLiveIngest/1.0 alexthegreat123@gmail.com"

logger = logging.getLogger(__name__)

_EDGAR_ATOM_NS = "http://www.w3.org/2005/Atom"
_FILING_TYPES = ("8-K", "10-Q", "10-K")

# ── helpers ───────────────────────────────────────────────────────────────────

def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_filing_docs(index_html: str, index_href: str) -> List[Dict[str, str]]:
    """Parse EDGAR filing index HTML, return [{url, doc_type, description}]."""
    url_base = index_href.rsplit("/", 1)[0] + "/" if "/" in index_href else ""
    docs: List[Dict[str, str]] = []
    row_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
    href_re = re.compile(r'href="([^"?#]+\.[a-zA-Z]{2,4})"', re.IGNORECASE)
    cell_text = lambda s: re.sub(r"<[^>]+>", "", s or "").strip()
    for row_m in row_re.finditer(index_html):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_m.group(1), re.IGNORECASE | re.DOTALL)
        if len(cells) < 4:
            continue
        href_m = href_re.search(cells[2])
        if not href_m:
            continue
        href = href_m.group(1).strip()
        if href.startswith("/"):
            url = "https://www.sec.gov" + href
        elif href.startswith("http"):
            url = href
        else:
            url = url_base + href
        docs.append({
            "url": url,
            "doc_type": cell_text(cells[3]),
            "description": cell_text(cells[1]),
        })
    return docs


def _is_ixbrl(html: str) -> bool:
    return bool(re.search(r"xmlns:ix\s*=|<ix:", html[:8000], re.IGNORECASE))


def _extract_mda_section(text: str) -> str:
    """Find MD&A or Results of Operations narrative inside iXBRL-stripped text."""
    # 10-K Item 7 / 10-Q Item 2 — Management's Discussion
    mda_re = re.compile(
        r"item\s+(?:7|2)[\.\s]*(?:management.{0,40}?discussion|md&a|results\s+of\s+operations)",
        re.IGNORECASE,
    )
    m = mda_re.search(text)
    if m:
        start = max(0, m.start() - 30)
        return text[start: start + 22000]
    # No labelled MD&A — skip iXBRL preamble by finding first substantial paragraph
    # (typical iXBRL header is 6-12k chars of XBRL context tags before narrative)
    para_re = re.compile(r"[A-Z][A-Za-z ,;:()\-]{200,}")
    pm = para_re.search(text, 6000)
    if pm:
        return text[pm.start():]
    return text[10000:] if len(text) > 10000 else text


def _truncate(text: str, max_chars: int = _MAX_CHARS) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean if len(clean) <= max_chars else clean[: max_chars - 3] + "..."


def _chunks(text: str) -> List[str]:
    clean = _truncate(text)
    if not clean:
        return []
    if len(clean) <= _CHUNK_SIZE:
        return [clean]
    out: List[str] = []
    step = max(1, _CHUNK_SIZE - _CHUNK_OVERLAP)
    for idx in range(0, len(clean), step):
        chunk = clean[idx : idx + _CHUNK_SIZE].strip()
        if chunk:
            out.append(chunk)
        if idx + _CHUNK_SIZE >= len(clean):
            break
    return out


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()).strip("_") or "src"


def _get_client(rag_dir: str):
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(
        path=rag_dir,
        settings=Settings(allow_reset=False, anonymized_telemetry=False),
    )


def _existing_hashes(collection) -> set:
    hashes: set = set()
    offset = 0
    while True:
        payload = collection.get(include=["metadatas"], limit=500, offset=offset)
        metas = list(payload.get("metadatas") or [])
        if not metas:
            break
        for m in metas:
            dh = str((m or {}).get("doc_hash") or "").strip()
            if dh:
                hashes.add(dh)
        offset += len(metas)
        if len(metas) < 500:
            break
    return hashes


# ── focus universe tickers ────────────────────────────────────────────────────

def _load_focus_tickers() -> List[Dict[str, str]]:
    """Returns [{"ticker": "NVDA", "name": "NVIDIA", "theme": "ai_compute"}, ...]"""
    manifest_path = _APOLLO_ROOT / "evaluations" / "apollo_focus_universe_manifest.json"
    try:
        import json
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    seen: set = set()
    for theme in list(payload.get("themes") or []):
        theme_id = str(theme.get("id") or "")
        for row in list(theme.get("tickers") or []):
            ticker = str((row or {}).get("ticker") or "").strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            out.append({"ticker": ticker, "name": str(row.get("name") or ticker), "theme": theme_id})
    return out


# ── EDGAR ingestion ───────────────────────────────────────────────────────────

def _edgar_atom_entries(ticker: str, filing_type: str, count: int = 5) -> List[Dict[str, str]]:
    """Fetch EDGAR RSS atom feed, return list of filing entries."""
    url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={ticker}&type={filing_type}"
        f"&dateb=&owner=include&count={count}&search_text=&output=atom"
    )
    try:
        r = requests.get(url, timeout=_REQUEST_TIMEOUT, headers={"User-Agent": _EDGAR_UA})
        r.raise_for_status()
    except Exception as exc:
        logger.warning("[live_ingest] edgar atom fetch failed ticker=%s type=%s: %s", ticker, filing_type, exc)
        return []
    try:
        root = ET.fromstring(r.text)
    except Exception as exc:
        logger.warning("[live_ingest] edgar atom parse failed ticker=%s: %s", ticker, exc)
        return []
    ns = _EDGAR_ATOM_NS
    entries: List[Dict[str, str]] = []
    for entry in root.findall(f"{{{ns}}}entry"):
        content = entry.find(f"{{{ns}}}content")
        if content is None:
            continue
        href_el = entry.find(f"{{{ns}}}link")
        href = href_el.get("href", "") if href_el is not None else ""
        filing_date = (content.findtext(f"{{{ns}}}filing-date") or "").strip()
        filing_href = (content.findtext(f"{{{ns}}}filing-href") or href).strip()
        filing_type_found = (content.findtext(f"{{{ns}}}filing-type") or filing_type).strip()
        accession = (content.findtext(f"{{{ns}}}accession-number") or "").strip()
        items_desc = (content.findtext(f"{{{ns}}}items-desc") or "").strip()
        if filing_href:
            entries.append({
                "filing_date": filing_date,
                "filing_href": filing_href,
                "filing_type": filing_type_found,
                "accession": accession,
                "items_desc": items_desc,
            })
    return entries


def _resolve_primary_doc_url(index_href: str, filing_type: str = "") -> Optional[str]:
    """Fetch the filing index page and return the best document URL.

    For 8-K filings prefers EX-99.1 (earnings press release) over the primary
    iXBRL wrapper so we get clean narrative text instead of XBRL metadata.
    """
    try:
        r = requests.get(index_href, timeout=_REQUEST_TIMEOUT, headers={"User-Agent": _EDGAR_UA})
        r.raise_for_status()
    except Exception as exc:
        logger.debug("[live_ingest] edgar index fetch failed %s: %s", index_href, exc)
        return None
    time.sleep(_EDGAR_DELAY_S)

    docs = _parse_filing_docs(r.text, index_href)

    ft = filing_type.upper().strip()
    if ft == "8-K":
        # Prefer earnings press release exhibit — pure narrative, no iXBRL
        for doc in docs:
            if doc["doc_type"].upper() in ("EX-99.1", "EX-99"):
                return doc["url"]
    # For 10-K / 10-Q find the typed primary document (iXBRL, handled in _fetch_filing_text)
    if ft in ("10-K", "10-Q"):
        for doc in docs:
            if doc["doc_type"].upper() == ft:
                return doc["url"]

    # Fallback: original regex approach
    ix_matches = re.findall(r'href="(/ix\?doc=(/Archives/edgar/data/[^"]+\.htm))"', r.text)
    if ix_matches:
        return "https://www.sec.gov" + ix_matches[0][1]
    direct = re.findall(r'href="(/Archives/edgar/data/[^"]+\.htm)"', r.text)
    direct = [d for d in direct if not d.endswith("-index.htm")]
    if direct:
        return "https://www.sec.gov" + direct[0]
    return None


def _fetch_filing_text(doc_url: str) -> str:
    """Fetch a filing document and return plain text.

    Detects iXBRL documents and extracts the MD&A narrative section rather
    than returning the raw XBRL metadata preamble.
    """
    try:
        r = requests.get(doc_url, timeout=_REQUEST_TIMEOUT, headers={"User-Agent": _EDGAR_UA})
        r.raise_for_status()
    except Exception as exc:
        logger.debug("[live_ingest] filing doc fetch failed %s: %s", doc_url, exc)
        return ""
    time.sleep(_EDGAR_DELAY_S)
    raw = r.content
    if len(raw) > _MAX_FILING_BYTES:
        raw = raw[:_MAX_FILING_BYTES]
    html = raw.decode("utf-8", errors="replace")
    is_ix = _is_ixbrl(html)
    text = _strip_html(html)
    if is_ix:
        text = _extract_mda_section(text)
    return _truncate(text)


def ingest_edgar_filings(
    tickers: List[Dict[str, str]],
    *,
    filing_types: Tuple[str, ...] = ("8-K", "10-Q"),
    max_per_ticker_per_type: int = 5,
    collection: Any,
    existing_hashes: set,
) -> Dict[str, Any]:
    """Ingest recent EDGAR filings for each ticker into the ChromaDB collection."""
    added_chunks = 0
    added_filings = 0
    skipped = 0
    errors: List[str] = []
    now_iso = _utc_iso()

    for ticker_info in tickers:
        ticker = ticker_info["ticker"]
        theme = ticker_info.get("theme", "")
        name = ticker_info.get("name", ticker)

        for filing_type in filing_types:
            entries = _edgar_atom_entries(ticker, filing_type, count=max_per_ticker_per_type)
            time.sleep(_EDGAR_DELAY_S)

            for entry in entries:
                index_href = entry["filing_href"]
                accession = entry["accession"]
                filing_date = entry["filing_date"]
                items_desc = entry["items_desc"]

                doc_url = _resolve_primary_doc_url(index_href, filing_type=filing_type)
                if not doc_url:
                    errors.append(f"no_primary_doc:{ticker}:{accession}")
                    continue

                text = _fetch_filing_text(doc_url)
                if len(text) < 200:
                    skipped += 1
                    continue

                doc_hash = _sha256(f"edgar:{ticker}:{accession}:{text[:4000]}")
                if doc_hash in existing_hashes:
                    skipped += 1
                    continue

                chunks_list = _chunks(text)
                if not chunks_list:
                    skipped += 1
                    continue

                slug = _safe_slug(f"{ticker}_{accession}")
                ids = [f"edgar_{slug}_{doc_hash[:10]}_{i:03d}" for i in range(1, len(chunks_list) + 1)]
                meta_base = {
                    "ticker": ticker,
                    "theme": theme,
                    "name": name,
                    "filing_type": filing_type,
                    "filed_at": filing_date,
                    "items_desc": items_desc,
                    "accession": accession,
                    "source_url": doc_url,
                    "source": f"{ticker} {filing_type} {filing_date}",
                    "title": f"{ticker} {filing_type} {filing_date}",
                    "pack": "sec_filings",
                    "kind": "sec_filings",
                    "source_tier": "A",
                    "provenance_class": "real_public",
                    "license_hint": "Public SEC EDGAR filing",
                    "retrieved_at": now_iso,
                    "doc_hash": doc_hash,
                }
                metadatas = [{**meta_base, "chunk_index": i} for i in range(len(chunks_list))]
                try:
                    collection.upsert(ids=ids, documents=chunks_list, metadatas=metadatas)
                    existing_hashes.add(doc_hash)
                    added_chunks += len(chunks_list)
                    added_filings += 1
                    logger.info("[live_ingest] edgar added ticker=%s type=%s date=%s chunks=%d",
                                ticker, filing_type, filing_date, len(chunks_list))
                except Exception as exc:
                    errors.append(f"upsert_failed:{ticker}:{accession}:{type(exc).__name__}")

    return {"added_filings": added_filings, "added_chunks": added_chunks, "skipped": skipped, "errors": errors}


# ── Yahoo Finance RSS ingestion ───────────────────────────────────────────────

def _fetch_yf_rss_entries(ticker: str, max_items: int = 20) -> List[Dict[str, str]]:
    """Fetch Yahoo Finance RSS and return cleaned items."""
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        import feedparser
        feed = feedparser.parse(url)
        entries = list(feed.entries or [])
    except Exception as exc:
        logger.warning("[live_ingest] yf rss fetch failed ticker=%s: %s", ticker, exc)
        return []
    out: List[Dict[str, str]] = []
    for entry in entries[:max_items]:
        title = str(entry.get("title") or "").strip()
        summary = str(entry.get("summary") or entry.get("description") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        link = str(entry.get("link") or "").strip()
        published = str(entry.get("published") or "").strip()
        if not title:
            continue
        text = f"{title}. {summary}".strip(". ")
        if text:
            out.append({"title": title, "text": text, "link": link, "published": published})
    return out


def ingest_equity_news_rss(
    tickers: List[Dict[str, str]],
    *,
    max_items_per_ticker: int = 20,
    collection: Any,
    existing_hashes: set,
) -> Dict[str, Any]:
    """Ingest Yahoo Finance RSS headlines for each ticker."""
    added_chunks = 0
    added_items = 0
    skipped = 0
    errors: List[str] = []
    now_iso = _utc_iso()

    for ticker_info in tickers:
        ticker = ticker_info["ticker"]
        theme = ticker_info.get("theme", "")
        name = ticker_info.get("name", ticker)

        items = _fetch_yf_rss_entries(ticker, max_items=max_items_per_ticker)
        time.sleep(0.1)

        for item in items:
            text = item["text"]
            if len(text) < 60:
                skipped += 1
                continue
            doc_hash = _sha256(f"yf_news:{ticker}:{text[:500]}")
            if doc_hash in existing_hashes:
                skipped += 1
                continue

            chunks_list = _chunks(text)
            if not chunks_list:
                skipped += 1
                continue

            slug = _safe_slug(f"yf_{ticker}_{doc_hash[:8]}")
            ids = [f"news_{slug}_{i:03d}" for i in range(1, len(chunks_list) + 1)]
            meta_base = {
                "ticker": ticker,
                "theme": theme,
                "name": name,
                "published_at": item["published"],
                "source_url": item["link"],
                "source": f"Yahoo Finance: {ticker}",
                "title": item["title"][:200],
                "pack": "equity_news",
                "kind": "equity_news",
                "source_tier": "C",
                "provenance_class": "real_public",
                "license_hint": "Yahoo Finance RSS public feed",
                "retrieved_at": now_iso,
                "doc_hash": doc_hash,
            }
            metadatas = [{**meta_base, "chunk_index": i} for i in range(len(chunks_list))]
            try:
                collection.upsert(ids=ids, documents=chunks_list, metadatas=metadatas)
                existing_hashes.add(doc_hash)
                added_chunks += len(chunks_list)
                added_items += 1
            except Exception as exc:
                errors.append(f"upsert_failed:{ticker}:yf_news:{type(exc).__name__}")

    return {"added_items": added_items, "added_chunks": added_chunks, "skipped": skipped, "errors": errors}


# ── earnings calendar ingestion ───────────────────────────────────────────────

def ingest_earnings_calendar(
    tickers: List[Dict[str, str]],
    *,
    collection,
    existing_hashes: set,
) -> Dict[str, Any]:
    """
    Fetch next earnings date + EPS/revenue estimates via yfinance for each ticker.
    Stores one document per ticker with pack='earnings_calendar'.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"added_items": 0, "added_chunks": 0, "skipped": 0, "errors": ["yfinance_not_installed"]}

    added_items = 0
    added_chunks = 0
    skipped = 0
    errors: List[str] = []
    now_iso = _utc_iso()
    today_str = datetime.now(timezone.utc).date().isoformat()

    for ticker_info in tickers:
        ticker = ticker_info["ticker"]
        theme = ticker_info.get("theme", "")
        name = ticker_info.get("name", ticker)
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            # calendar is a dict with keys like 'Earnings Date', 'EPS Estimate', etc.
            if cal is None or not isinstance(cal, dict):
                skipped += 1
                continue
            earnings_dates = cal.get("Earnings Date") or []
            if not earnings_dates:
                skipped += 1
                continue
            # take first upcoming date
            if hasattr(earnings_dates, "tolist"):
                earnings_dates = earnings_dates.tolist()
            next_date = None
            for ed in earnings_dates:
                try:
                    if hasattr(ed, "date"):
                        ed_str = ed.date().isoformat()
                    else:
                        ed_str = str(ed)[:10]
                    if ed_str >= today_str:
                        next_date = ed_str
                        break
                except Exception:
                    continue
            if not next_date:
                skipped += 1
                continue

            eps_est = cal.get("EPS Estimate")
            rev_est = cal.get("Revenue Estimate")
            eps_text = f" EPS estimate: {eps_est}." if eps_est is not None else ""
            rev_text = f" Revenue estimate: {rev_est}." if rev_est is not None else ""

            text = (
                f"{ticker} ({name}) next earnings date: {next_date}.{eps_text}{rev_text} "
                f"Theme: {theme}. Retrieved: {today_str}."
            )
            doc_hash = _sha256(f"earnings_calendar:{ticker}:{next_date}")
            if doc_hash in existing_hashes:
                skipped += 1
                continue

            slug = _safe_slug(f"earn_{ticker}_{next_date}")
            doc_id = f"earnings_{slug}_001"
            meta = {
                "ticker": ticker,
                "theme": theme,
                "name": name,
                "pack": "earnings_calendar",
                "kind": "earnings_calendar",
                "event_date": next_date,
                "source": "yfinance_calendar",
                "source_tier": "B",
                "provenance_class": "real_public",
                "license_hint": "Yahoo Finance public data",
                "retrieved_at": now_iso,
                "doc_hash": doc_hash,
                "chunk_index": 0,
            }
            try:
                collection.upsert(ids=[doc_id], documents=[text], metadatas=[meta])
                existing_hashes.add(doc_hash)
                added_chunks += 1
                added_items += 1
            except Exception as exc:
                errors.append(f"upsert_failed:{ticker}:earnings:{type(exc).__name__}")
        except Exception as exc:
            errors.append(f"fetch_failed:{ticker}:{type(exc).__name__}:{str(exc)[:80]}")

    return {"added_items": added_items, "added_chunks": added_chunks, "skipped": skipped, "errors": errors}


# ── fundamentals + macro ingest ───────────────────────────────────────────────

def ingest_fundamentals_snapshot(
    tickers: List[Dict[str, str]],
    *,
    collection: Any,
    existing_hashes: set,
) -> Dict[str, Any]:
    """
    Ingest per-ticker fundamental metrics via yfinance.
    Creates text like "Revenue grew 20.5% year-over-year. Forward P/E: 28.4."
    which the financial fact regex patterns can match directly.
    Refreshed daily; pack='fundamentals'.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"added_items": 0, "added_chunks": 0, "skipped": 0, "errors": ["yfinance_not_installed"]}

    added_items = 0
    added_chunks = 0
    skipped = 0
    errors: List[str] = []
    now_iso = _utc_iso()
    today_str = datetime.now(timezone.utc).date().isoformat()

    for ticker_info in tickers:
        ticker = ticker_info["ticker"]
        theme = ticker_info.get("theme", "")
        name = ticker_info.get("name", ticker)
        try:
            info = (yf.Ticker(ticker).info or {})
            rev_growth = info.get("revenueGrowth")
            earn_growth = info.get("earningsGrowth")
            fwd_pe = info.get("forwardPE")
            fwd_eps = info.get("forwardEps")
            profit_margin = info.get("profitMargins")
            op_margin = info.get("operatingMargins")
            roe = info.get("returnOnEquity")
            debt_eq = info.get("debtToEquity")
            short_ratio = info.get("shortRatio")
            short_pct = info.get("shortPercentOfFloat")

            if not any(v is not None for v in (rev_growth, earn_growth, fwd_pe, profit_margin)):
                skipped += 1
                continue

            parts = [f"{ticker} ({name}) fundamental metrics as of {today_str}."]
            if rev_growth is not None:
                label = "grew" if rev_growth > 0 else "declined"
                parts.append(f"Revenue {label} {abs(float(rev_growth)) * 100:.1f}% year-over-year.")
            if earn_growth is not None:
                label = "grew" if earn_growth > 0 else "declined"
                parts.append(f"Net earnings {label} {abs(float(earn_growth)) * 100:.1f}% year-over-year.")
            if fwd_pe is not None:
                parts.append(f"Forward P/E ratio: {float(fwd_pe):.1f}.")
            if fwd_eps is not None:
                parts.append(f"Forward EPS estimate: ${float(fwd_eps):.2f}.")
            if profit_margin is not None:
                parts.append(f"Net profit margin: {float(profit_margin) * 100:.1f}%.")
            if op_margin is not None:
                parts.append(f"Operating margin: {float(op_margin) * 100:.1f}%.")
            if roe is not None:
                parts.append(f"Return on equity: {float(roe) * 100:.1f}%.")
            if debt_eq is not None:
                parts.append(f"Debt-to-equity ratio: {float(debt_eq):.2f}.")
            if short_ratio is not None:
                label = "high" if float(short_ratio) > 5 else ("moderate" if float(short_ratio) > 2 else "low")
                parts.append(f"Short ratio: {float(short_ratio):.1f} days to cover ({label} short interest).")
            if short_pct is not None:
                pct_val = float(short_pct) * 100 if float(short_pct) < 1 else float(short_pct)
                parts.append(f"Short interest: {pct_val:.1f}% of float.")

            text = " ".join(parts)
            doc_hash = _sha256(f"fundamentals:{ticker}:{today_str}")
            if doc_hash in existing_hashes:
                skipped += 1
                continue

            today_compact = today_str.replace("-", "")
            doc_id = f"fund_{ticker}_{today_compact}_001"
            meta = {
                "ticker": ticker,
                "theme": theme,
                "name": name,
                "pack": "fundamentals",
                "kind": "fundamentals",
                "source": "yfinance_fundamentals",
                "source_tier": "B",
                "provenance_class": "real_public",
                "license_hint": "Yahoo Finance public data",
                "retrieved_at": now_iso,
                "doc_hash": doc_hash,
                "chunk_index": 0,
                "short_ratio": str(round(float(short_ratio), 2)) if short_ratio is not None else "",
                "short_pct_float": str(round(float(short_pct) * 100 if float(short_pct) < 1 else float(short_pct), 2)) if short_pct is not None else "",
            }
            try:
                collection.upsert(ids=[doc_id], documents=[text], metadatas=[meta])
                existing_hashes.add(doc_hash)
                added_chunks += 1
                added_items += 1
            except Exception as exc:
                errors.append(f"upsert_failed:{ticker}:fundamentals:{type(exc).__name__}")
        except Exception as exc:
            errors.append(f"fetch_failed:{ticker}:{type(exc).__name__}:{str(exc)[:80]}")

    return {"added_items": added_items, "added_chunks": added_chunks, "skipped": skipped, "errors": errors}


def ingest_macro_snapshot(
    *,
    collection: Any,
    existing_hashes: set,
) -> Dict[str, Any]:
    """
    Ingest a daily macro context snapshot: VIX, 10Y yield, SPY/QQQ performance.
    Provides macro regime context for RAG retrieval. pack='macro_snapshot'.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"added_items": 0, "added_chunks": 0, "skipped": 0, "errors": ["yfinance_not_installed"]}

    now_iso = _utc_iso()
    today_str = datetime.now(timezone.utc).date().isoformat()

    try:
        symbols = {"VIX": "^VIX", "TNX": "^TNX", "SPY": "SPY", "QQQ": "QQQ"}
        data: Dict[str, Any] = {}
        for sym_name, sym in symbols.items():
            try:
                hist = yf.Ticker(sym).history(period="5d", interval="1d")
                if not hist.empty:
                    data[sym_name] = {
                        "close": round(float(hist["Close"].iloc[-1]), 4),
                        "prev": round(float(hist["Close"].iloc[-2]), 4) if len(hist) >= 2 else None,
                    }
            except Exception:
                pass

        if not data:
            return {"added_items": 0, "added_chunks": 0, "skipped": 1, "errors": []}

        parts: List[str] = [f"Macro market snapshot as of {today_str}."]
        if "VIX" in data:
            vix = data["VIX"]["close"]
            vix_label = "elevated" if vix > 25 else ("normal" if vix > 15 else "low")
            parts.append(f"VIX volatility index: {vix:.2f} ({vix_label} market volatility).")
        if "TNX" in data:
            parts.append(f"10-year Treasury yield: {data['TNX']['close']:.3f}%.")
        if "SPY" in data:
            spy = data["SPY"]
            chg = round((spy["close"] / spy["prev"] - 1) * 100, 2) if spy.get("prev") else None
            parts.append(f"SPY close: {spy['close']}" + (f", 1-day change: {chg:+.2f}%." if chg is not None else "."))
        if "QQQ" in data:
            qqq = data["QQQ"]
            chg = round((qqq["close"] / qqq["prev"] - 1) * 100, 2) if qqq.get("prev") else None
            parts.append(f"QQQ close: {qqq['close']}" + (f", 1-day change: {chg:+.2f}%." if chg is not None else "."))

        text = " ".join(parts)
        doc_hash = _sha256(f"macro_snapshot:{today_str}")
        if doc_hash in existing_hashes:
            return {"added_items": 0, "added_chunks": 0, "skipped": 1, "errors": []}

        today_compact = today_str.replace("-", "")
        doc_id = f"macro_snapshot_{today_compact}_001"
        meta = {
            "pack": "macro_snapshot",
            "kind": "macro_snapshot",
            "source": "yfinance_macro",
            "source_tier": "B",
            "provenance_class": "real_public",
            "license_hint": "Yahoo Finance public data",
            "retrieved_at": now_iso,
            "doc_hash": doc_hash,
            "chunk_index": 0,
        }
        collection.upsert(ids=[doc_id], documents=[text], metadatas=[meta])
        existing_hashes.add(doc_hash)
        return {"added_items": 1, "added_chunks": 1, "skipped": 0, "errors": []}
    except Exception as exc:
        return {"added_items": 0, "added_chunks": 0, "skipped": 0, "errors": [str(exc)[:100]]}


# ── orchestrator ──────────────────────────────────────────────────────────────

def run_live_ingest(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Pull fresh EDGAR filings and Yahoo Finance news for all focus universe tickers.
    Upserts into the ChromaDB financial corpus.

    cfg keys (all optional):
      rag_dir, collection, tickers (list of ticker dicts),
      filing_types, max_per_ticker_per_type, max_news_per_ticker,
      skip_edgar, skip_news
    """
    cfg = dict(cfg or {})
    rag_dir = str(cfg.get("rag_dir") or _DEFAULT_RAG_DIR).strip()
    collection_name = str(cfg.get("collection") or _DEFAULT_COLLECTION).strip() or _DEFAULT_COLLECTION
    tickers: List[Dict[str, str]] = list(cfg.get("tickers") or []) or _load_focus_tickers()
    filing_types_raw = cfg.get("filing_types") or ["8-K", "10-Q"]
    filing_types = tuple(str(t).strip().upper() for t in filing_types_raw if str(t).strip())
    max_per_ticker_per_type = max(1, min(int(cfg.get("max_per_ticker_per_type") or 5), 20))
    max_news_per_ticker = max(1, min(int(cfg.get("max_news_per_ticker") or 20), 50))
    skip_edgar = bool(cfg.get("skip_edgar"))
    skip_news = bool(cfg.get("skip_news"))
    skip_earnings = bool(cfg.get("skip_earnings"))
    skip_fundamentals = bool(cfg.get("skip_fundamentals"))
    skip_macro = bool(cfg.get("skip_macro"))

    if not tickers:
        return {"ok": False, "error": "no_tickers", "ticker_count": 0}

    try:
        client = _get_client(rag_dir)
        collection = client.get_or_create_collection(collection_name)
    except Exception as exc:
        return {"ok": False, "error": f"collection_unavailable:{exc}", "collection": collection_name}

    hashes = _existing_hashes(collection)
    started_at = _utc_iso()
    edgar_result: Dict[str, Any] = {}
    news_result: Dict[str, Any] = {}

    if not skip_edgar:
        logger.info("[live_ingest] edgar start tickers=%d types=%s", len(tickers), filing_types)
        edgar_result = ingest_edgar_filings(
            tickers,
            filing_types=filing_types,
            max_per_ticker_per_type=max_per_ticker_per_type,
            collection=collection,
            existing_hashes=hashes,
        )

    if not skip_news:
        logger.info("[live_ingest] news rss start tickers=%d", len(tickers))
        news_result = ingest_equity_news_rss(
            tickers,
            max_items_per_ticker=max_news_per_ticker,
            collection=collection,
            existing_hashes=hashes,
        )

    earnings_result: Dict[str, Any] = {}
    if not skip_earnings:
        logger.info("[live_ingest] earnings calendar start tickers=%d", len(tickers))
        earnings_result = ingest_earnings_calendar(
            tickers,
            collection=collection,
            existing_hashes=hashes,
        )

    fundamentals_result: Dict[str, Any] = {}
    if not skip_fundamentals:
        logger.info("[live_ingest] fundamentals snapshot start tickers=%d", len(tickers))
        fundamentals_result = ingest_fundamentals_snapshot(
            tickers,
            collection=collection,
            existing_hashes=hashes,
        )

    macro_result: Dict[str, Any] = {}
    if not skip_macro:
        logger.info("[live_ingest] macro snapshot start")
        macro_result = ingest_macro_snapshot(
            collection=collection,
            existing_hashes=hashes,
        )

    total_added = (
        int(edgar_result.get("added_chunks") or 0)
        + int(news_result.get("added_chunks") or 0)
        + int(earnings_result.get("added_chunks") or 0)
        + int(fundamentals_result.get("added_chunks") or 0)
        + int(macro_result.get("added_chunks") or 0)
    )
    result = {
        "ok": True,
        "started_at": started_at,
        "finished_at": _utc_iso(),
        "collection": collection_name,
        "rag_dir": rag_dir,
        "ticker_count": len(tickers),
        "total_chunks_added": total_added,
        "edgar": edgar_result,
        "news": news_result,
        "earnings": earnings_result,
        "fundamentals": fundamentals_result,
        "macro": macro_result,
    }
    try:
        _write_ingest_stage_report(result)
    except Exception:
        pass
    return result


# ── stage reports ─────────────────────────────────────────────────────────────

_TASK_NAME = os.getenv("APOLLO_CORPUS_REFRESH_TASK_NAME", "ApolloCorpusRefresh0315")
_DEFAULT_START = os.getenv("APOLLO_CORPUS_REFRESH_START", "03:15")
_LOG_DIR = _APOLLO_ROOT / "logs" / "corpus_refresh"
_SCHEDULE_JSON = _LOG_DIR / "schedule.json"
_LATEST_INGEST_JSON = _LOG_DIR / "latest_corpus_refresh_ingest.json"
_LATEST_CORPUS_REFRESH_JSON = _LOG_DIR / "latest_corpus_refresh.json"
_KG_PATH = _APOLLO_ROOT / "chroma_db" / "financial_kg.json"
_WRAPPER = _APOLLO_ROOT / "watchdog" / "run_apollo_corpus_refresh.ps1"
_ENGINEERING_ROOT = _APOLLO_ROOT.parent


def _write_ingest_stage_report(result: Dict[str, Any]) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "stage": "live_ingest",
        "ok": result.get("ok", False),
        "date": (result.get("started_at") or _utc_iso())[:10],
        "ticker_count": result.get("ticker_count", 0),
        "total_chunks_added": result.get("total_chunks_added", 0),
        "edgar_filings": (result.get("edgar") or {}).get("added_filings"),
        "edgar_chunks": (result.get("edgar") or {}).get("added_chunks"),
        "edgar_skipped": (result.get("edgar") or {}).get("skipped"),
        "edgar_errors": len((result.get("edgar") or {}).get("errors") or []),
        "news_items": (result.get("news") or {}).get("added_items"),
        "news_chunks": (result.get("news") or {}).get("added_chunks"),
        "earnings_items": (result.get("earnings") or {}).get("added_items"),
        "earnings_chunks": (result.get("earnings") or {}).get("added_chunks"),
        "fundamentals_items": (result.get("fundamentals") or {}).get("added_items"),
        "fundamentals_chunks": (result.get("fundamentals") or {}).get("added_chunks"),
        "macro_items": (result.get("macro") or {}).get("added_items"),
        "macro_chunks": (result.get("macro") or {}).get("added_chunks"),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
    }
    _LATEST_INGEST_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")


def finalize_corpus_report(ingest_exit: int, graph_exit: int) -> Dict[str, Any]:
    """Combine ingest + KG stats into latest_corpus_refresh.json for Sky morning report."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ingest_data: Dict[str, Any] = {}
    if _LATEST_INGEST_JSON.exists():
        try:
            ingest_data = json.loads(_LATEST_INGEST_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass

    graph_stats: Dict[str, Any] = {}
    if _KG_PATH.exists():
        try:
            kg = json.loads(_KG_PATH.read_text(encoding="utf-8"))
            edges = list(kg.get("edges") or [])
            fact_edges = sum(1 for e in edges if e.get("relation") == "has_fact")
            graph_stats = {
                "nodes": len(kg.get("nodes") or {}),
                "edges": len(edges),
                "fact_edges": fact_edges,
                "kg_path": str(_KG_PATH),
                "finished_at": _utc_iso(),
            }
        except Exception as exc:
            graph_stats = {"error": str(exc), "kg_path": str(_KG_PATH)}

    overall_ok = ingest_exit == 0 and graph_exit == 0
    report = {
        "ok": overall_ok,
        "status": "complete" if overall_ok else "failed",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "finished_at": _utc_iso(),
        "ingest_exit": ingest_exit,
        "graph_exit": graph_exit,
        "ingest": ingest_data,
        "graph": graph_stats,
    }
    _LATEST_CORPUS_REFRESH_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _append_readme_run_section(report, ingest_data, graph_stats)
    return report


def _append_readme_run_section(report: Dict[str, Any], ingest_data: Dict[str, Any], graph_stats: Dict[str, Any]) -> None:
    """Append a dated run-report section to Apollo's README.md."""
    readme = _APOLLO_ROOT / "README.md"
    try:
        from datetime import datetime, timezone
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/Chicago")
        now_ct = datetime.now(tz)
        date_label = now_ct.strftime("%Y-%m-%d")
        time_label = now_ct.strftime("%H:%M CT")
        status = "OK" if report.get("ok") else "FAILED"

        edgar_filings = ingest_data.get("edgar_filings") or 0
        edgar_chunks = ingest_data.get("edgar_chunks") or 0
        fund_items = ingest_data.get("fundamentals_items") or 0
        fund_chunks = ingest_data.get("fundamentals_chunks") or 0
        macro_chunks = ingest_data.get("macro_chunks") or 0
        news_chunks = ingest_data.get("news_chunks") or 0
        total_chunks = ingest_data.get("total_chunks_added") or 0

        kg_nodes = graph_stats.get("nodes") or 0
        kg_edges = graph_stats.get("edges") or 0
        kg_facts = graph_stats.get("fact_edges") or 0

        section = (
            f"\n## {date_label} — Nightly Corpus Refresh\n\n"
            f"**Completed:** {time_label}  **Status:** {status}\n\n"
            f"| Stage | Result |\n"
            f"|-------|--------|\n"
            f"| EDGAR filings | {edgar_filings} new / {edgar_chunks} chunks |\n"
            f"| Fundamentals snapshots | {fund_items} tickers / {fund_chunks} chunks |\n"
            f"| Macro snapshot | {macro_chunks} chunk(s) |\n"
            f"| News | {news_chunks} chunks |\n"
            f"| Total new chunks | {total_chunks} |\n"
            f"| build_graph nodes | {kg_nodes:,} |\n"
            f"| build_graph edges | {kg_edges:,} |\n"
            f"| has_fact triples | {kg_facts:,} |\n\n"
            f"**Agents:** Apollo (ingest + KG), Aegis (monitor), Sky (calendar + morning report)\n"
        )

        existing = readme.read_text(encoding="utf-8") if readme.exists() else ""
        readme.write_text(existing + section, encoding="utf-8")
    except Exception:
        pass


def _powershell_exe() -> str:
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "powershell.exe"


def _next_window(start_text: str, minutes: int = 90) -> tuple:
    """Return (start_dt, end_dt) for tomorrow at start_text local time."""
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    hour, minute = [int(p) for p in start_text.split(":", 1)]
    import zoneinfo
    try:
        tz = zoneinfo.ZoneInfo("America/Chicago")
    except Exception:
        tz = timezone.utc
    start_dt = datetime.combine(tomorrow, dt_time(hour=hour, minute=minute), tzinfo=tz)
    end_dt = start_dt + timedelta(minutes=minutes)
    return start_dt, end_dt


def schedule_corpus_refresh() -> Dict[str, Any]:
    """
    Install a daily Windows Task Scheduler entry for the corpus refresh
    (live ingest + build_graph) and post a recurring calendar event to Sky.
    Runs every day at 03:15 CT, after the nightly pipeline finishes.
    """
    from Sky.core.calendar_store import create_event, list_events, update_event

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    ps_exe = _powershell_exe()
    task_command = subprocess.list2cmdline([
        ps_exe, "-WindowStyle", "Hidden", "-NoProfile",
        "-ExecutionPolicy", "Bypass", "-File", str(_WRAPPER),
    ])
    cmd = [
        "schtasks", "/Create",
        "/SC", "DAILY",
        "/ST", _DEFAULT_START,
        "/TN", _TASK_NAME,
        "/TR", task_command,
        "/F",
    ]
    proc = subprocess.run(cmd, cwd=str(_ENGINEERING_ROOT), capture_output=True, text=True, timeout=60, check=False)

    start_dt, end_dt = _next_window(_DEFAULT_START, minutes=90)
    title = "Apollo Corpus Refresh (EDGAR + KG rebuild)"
    notes = "\n".join([
        "Daily corpus refresh: live ingest of EDGAR 8-K/10-Q/10-K + Yahoo Finance RSS for all 36 focus universe tickers, followed by HippoRAG build_graph to update financial_kg.json.",
        f"Scheduled task: {_TASK_NAME}",
        f"Start: {_DEFAULT_START} CT daily",
        f"Expected runtime: ~90 minutes",
        f"Wrapper: {_WRAPPER}",
        f"Log dir: {_LOG_DIR}",
        "Covers: ai_compute, enterprise_platforms, money_center_financials, energy_infrastructure, healthcare_quality, industrial_automation",
    ])
    event_payload = {
        "title": title,
        "start": start_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "end": end_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "scheduled",
        "source": "apollo",
        "trigger": f"schtasks:{_TASK_NAME}",
        "notes": notes,
        "all_day": False,
        "recurrence": "daily",
    }

    existing = None
    for ev in list_events(start=event_payload["start"], end=event_payload["end"]):
        if str((ev or {}).get("title") or "").strip() == title:
            existing = ev
            break
    if existing and existing.get("id"):
        calendar_event = update_event(str(existing["id"]), event_payload)
        calendar_mode = "updated"
    else:
        calendar_event = create_event(event_payload)
        calendar_mode = "created"

    out = {
        "ok": proc.returncode == 0,
        "task_name": _TASK_NAME,
        "task_command": task_command,
        "task_stdout": proc.stdout or "",
        "task_stderr": proc.stderr or "",
        "start_local": _DEFAULT_START,
        "calendar_mode": calendar_mode,
        "calendar_event": calendar_event,
    }
    _SCHEDULE_JSON.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


# ── homework schedule ─────────────────────────────────────────────────────────

_HOMEWORK_RUNS = [
    {
        "task_name": "ApolloHomework10K2230",
        "start_time": "22:30",
        "duration_min": 90,
        "wrapper": "run_apollo_10k_ingest.ps1",
        "title": "Apollo Homework: 10-K Annual Report Deep Ingest",
        "description": (
            "Deep ingest of 10-K annual reports for all 36 focus universe tickers. "
            "Annual filings contain the richest MD&A narrative text and are the best "
            "source for entity extractor financial facts. Runs before LLM KG enrichment."
        ),
    },
    {
        "task_name": "ApolloHomeworkKGLLM0030",
        "start_time": "00:30",
        "duration_min": 150,
        "wrapper": "run_apollo_kg_llm.ps1",
        "title": "Apollo Homework: LLM KG Enrichment (gx10)",
        "description": (
            "Process up to 600 unindexed corpus chunks through gx10 (Ollama) for richer "
            "knowledge graph triple extraction. Regex extraction yields ~1-2 triples/chunk; "
            "LLM extraction yields 3-7 triples/chunk with company-specific facts. "
            "Runs after 10-K ingest, before daily corpus refresh."
        ),
    },
    {
        "task_name": "ApolloHomeworkSwing0530",
        "start_time": "05:30",
        "duration_min": 40,
        "wrapper": "run_apollo_swing_overnight.ps1",
        "title": "Apollo Homework: Overnight Swing Study",
        "description": (
            "Run full swing study after all overnight data refreshes complete. "
            "Generates fresh proposals with updated KG context, sector ETF RS, and "
            "earnings dates. Output cached for morning report and 07:30 market study."
        ),
    },
]


def schedule_homework_runs() -> Dict[str, Any]:
    """
    Install Windows Task Scheduler entries for all Apollo homework runs and
    post recurring calendar events to Sky for each.
    """
    from Sky.core.calendar_store import create_event, list_events, update_event

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ps_exe = _powershell_exe()
    results = []

    for run in _HOMEWORK_RUNS:
        wrapper_path = _APOLLO_ROOT / "watchdog" / run["wrapper"]
        task_command = subprocess.list2cmdline([
            ps_exe, "-WindowStyle", "Hidden", "-NoProfile",
            "-ExecutionPolicy", "Bypass", "-File", str(wrapper_path),
        ])
        cmd = [
            "schtasks", "/Create",
            "/SC", "DAILY",
            "/ST", run["start_time"],
            "/TN", run["task_name"],
            "/TR", task_command,
            "/F",
        ]
        proc = subprocess.run(
            cmd, cwd=str(_ENGINEERING_ROOT),
            capture_output=True, text=True, timeout=60, check=False,
        )

        start_dt, end_dt = _next_window(run["start_time"], minutes=run["duration_min"])
        notes = "\n".join([
            run["description"],
            f"Scheduled task: {run['task_name']}",
            f"Start: {run['start_time']} CT daily",
            f"Expected runtime: ~{run['duration_min']} minutes",
            f"Wrapper: {wrapper_path}",
            f"Log dir: {_LOG_DIR}",
        ])
        # Use "understood" status to bypass overlap detection on create/update.
        # Sky calendar treats "understood" as non-blocking (parallel background tasks).
        event_payload = {
            "title": run["title"],
            "start": start_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "end": end_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "status": "understood",
            "source": "apollo",
            "trigger": f"schtasks:{run['task_name']}",
            "notes": notes,
            "all_day": False,
            "recurrence": "daily",
        }
        existing = None
        for ev in list_events(start=event_payload["start"], end=event_payload["end"]):
            if str((ev or {}).get("title") or "").strip() == run["title"]:
                existing = ev
                break
        if existing and existing.get("id"):
            calendar_event = update_event(str(existing["id"]), event_payload)
            calendar_mode = "updated"
        else:
            calendar_event = create_event(event_payload)
            calendar_mode = "created"

        results.append({
            "ok": proc.returncode == 0,
            "task_name": run["task_name"],
            "start_time": run["start_time"],
            "duration_min": run["duration_min"],
            "schtask_stdout": proc.stdout.strip() or "",
            "schtask_stderr": proc.stderr.strip() or "",
            "calendar_mode": calendar_mode,
            "calendar_event_id": str((calendar_event or {}).get("id") or ""),
        })

    return {"ok": all(r["ok"] for r in results), "runs": results}


# ── __main__ ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "schedule":
        result = schedule_corpus_refresh()
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("ok") else 1)
    elif cmd == "report":
        ingest_exit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        graph_exit = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        result = finalize_corpus_report(ingest_exit, graph_exit)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok") else 1)
    elif cmd == "deep":
        # 10-K annual report deep ingest — skip news/earnings/fundamentals/macro
        # (those run at 03:15 daily; deep focuses only on annual report text)
        result = run_live_ingest({
            "filing_types": ["10-K"],
            "max_per_ticker_per_type": 3,
            "skip_news": True,
            "skip_earnings": True,
            "skip_fundamentals": True,
            "skip_macro": True,
        })
        print(json.dumps({
            "ok": result["ok"],
            "ticker_count": result["ticker_count"],
            "total_chunks_added": result["total_chunks_added"],
            "edgar_filings": (result.get("edgar") or {}).get("added_filings"),
            "edgar_chunks": (result.get("edgar") or {}).get("added_chunks"),
            "edgar_skipped": (result.get("edgar") or {}).get("skipped"),
            "edgar_errors": len((result.get("edgar") or {}).get("errors") or []),
            "started_at": result["started_at"],
            "finished_at": result["finished_at"],
        }, indent=2))
        sys.exit(0 if result["ok"] else 1)
    elif cmd == "schedule_homework":
        result = schedule_homework_runs()
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if all(r.get("ok") for r in result.get("runs") or [{}]) else 1)
    else:
        result = run_live_ingest()
        print(json.dumps({
            "ok": result["ok"],
            "ticker_count": result["ticker_count"],
            "total_chunks_added": result["total_chunks_added"],
            "edgar_filings": (result.get("edgar") or {}).get("added_filings"),
            "edgar_chunks": (result.get("edgar") or {}).get("added_chunks"),
            "edgar_skipped": (result.get("edgar") or {}).get("skipped"),
            "edgar_errors": len((result.get("edgar") or {}).get("errors") or []),
            "news_items": (result.get("news") or {}).get("added_items"),
            "news_chunks": (result.get("news") or {}).get("added_chunks"),
            "fundamentals_items": (result.get("fundamentals") or {}).get("added_items"),
            "fundamentals_chunks": (result.get("fundamentals") or {}).get("added_chunks"),
            "macro_items": (result.get("macro") or {}).get("added_items"),
            "started_at": result["started_at"],
            "finished_at": result["finished_at"],
        }, indent=2))
        sys.exit(0 if result["ok"] else 1)
