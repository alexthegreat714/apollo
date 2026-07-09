from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import yaml

from common.query_client import query_model


def _load_sources(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    src_path = path or Path(__file__).resolve().parents[1] / "common" / "budget_sources.yml"
    if not src_path.exists():
        return []
    raw = yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}
    return raw.get("sources", []) or []


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _extract_text(html: str) -> str:
    try:
        from readability import Document
        doc = Document(html)
        html = doc.summary()
    except Exception:
        pass
    html = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\\s+", " ", text)
    return text.strip()


def _quality_score(text: str) -> float:
    if not text:
        return 0.0
    length = len(text)
    if length < 400:
        return 0.2
    unique_ratio = len(set(text.split())) / max(len(text.split()), 1)
    score = min(1.0, (length / 2000.0)) * 0.6 + min(1.0, unique_ratio) * 0.4
    return round(score, 3)


def fetch_budget_sources(timeout: int = 8) -> List[Dict[str, Any]]:
    sources = _load_sources()
    results: List[Dict[str, Any]] = []
    for src in sources:
        url = src.get("url")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "ApolloBudget/1.0"})
        except Exception as exc:
            results.append({"url": url, "ok": False, "error": str(exc)})
            continue
        if not resp.ok:
            results.append({"url": url, "ok": False, "error": f"status:{resp.status_code}"})
            continue
        text = _extract_text(resp.text or "")
        score = _quality_score(text)
        results.append(
            {
                "url": url,
                "domain": _domain(url),
                "title": src.get("name") or url,
                "trust": float(src.get("trust", 0.8)),
                "text": text,
                "quality": score,
                "ok": True,
            }
        )
    return results


def summarize_guidance(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No guidance sources were available."

    def fallback_summary() -> str:
        source_titles = [str(item.get("title") or item.get("domain") or item.get("url") or "source") for item in items[:4]]
        source_text = ", ".join(source_titles)
        return (
            "- Review fixed bills first and schedule due-date reminders.\n"
            "- Prioritize overspent categories and reallocate from lower-priority categories.\n"
            "- Track weekly category activity to prevent end-of-month surprises.\n"
            "- Plan paycheck assignments in advance and keep a buffer for irregular expenses.\n"
            f"- Sources reviewed: {source_text}."
        )

    combined = "\n\n".join(item.get("text", "")[:1200] for item in items if item.get("text"))
    if not combined:
        return "No guidance text extracted."
    prompt = (
        "You are a budgeting advisor. Summarize the guidance below into 4-6 actionable bullet points. "
        "Focus on practical steps, not theory.\n\n"
        f"{combined}\n"
    )
    try:
        response = query_model(prompt)
        if isinstance(response, str) and len(response.strip()) >= 120:
            return response
        return fallback_summary()
    except Exception:
        return fallback_summary()


def refresh_insights(rag, month: str) -> Dict[str, Any]:
    fetched = fetch_budget_sources()
    ok_items = [item for item in fetched if item.get("ok") and item.get("quality", 0) >= 0.35]
    summary = summarize_guidance(ok_items)
    ids = []
    for item in ok_items:
        text = item.get("text", "")
        if not text:
            continue
        ids.append(
            rag.remember(
                text=text[:4000],
                source="budget_guidance",
                kind="budget_guidance",
                tags=["budget", "guidance"],
                extra={
                    "url": item.get("url"),
                    "domain": item.get("domain"),
                    "trust": item.get("trust"),
                    "quality": item.get("quality"),
                },
            )
        )
    sources = [
        {
            "url": item.get("url"),
            "title": item.get("title"),
            "trust": item.get("trust"),
            "quality": item.get("quality"),
        }
        for item in ok_items
    ]
    return {"ok": True, "month": month, "summary": summary, "sources": sources, "ids": ids}
