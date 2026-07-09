"""
Seed 45 nightly curated-enrichment calendar events into Sky.
Sky's calendar_run_watchdog will detect the PS1 path in `notes`,
attach a sky.local_command hook, and execute it at the scheduled time.

Run once: python -m Apollo.tools.seed_curated_enrichment_calendar
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import requests

SKY_URL = "http://127.0.0.1:5011"
LOCAL_TZ = ZoneInfo("America/Chicago")
PS1_PATH = r"C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_curated_enrichment.ps1"
DURATION_HOURS = 2  # budget: 120 chunks × ~40s + headroom
DAYS_TO_SEED = 45   # covers to ~mid-July; nightly chain self-perpetuates after that
START_HOUR = 23     # 11 PM CDT — finishes ~12:20 AM, before 12:30 AM KG LLM evicts qwen3-vl:32b


def _post_event(payload: dict) -> dict:
    resp = requests.post(
        f"{SKY_URL}/calendar/events",
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _event_exists_on_date(date_str: str) -> bool:
    try:
        resp = requests.get(f"{SKY_URL}/calendar/events", timeout=10)
        resp.raise_for_status()
        events = resp.json() if isinstance(resp.json(), list) else resp.json().get("events", [])
        for ev in events:
            title = str(ev.get("title") or "").lower()
            start = str(ev.get("start") or "")
            if "curated kg enrichment" in title and date_str in start:
                return True
    except Exception:
        pass
    return False


def main() -> None:
    now = datetime.now(LOCAL_TZ)
    # Start seeding from tomorrow morning
    seed_start = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0) + timedelta(days=1)

    print(f"Seeding {DAYS_TO_SEED} curated enrichment events starting {seed_start.date()} at {START_HOUR}:00 CDT")
    print(f"Sky: {SKY_URL}")

    created = 0
    skipped = 0

    for i in range(DAYS_TO_SEED):
        start_dt = seed_start + timedelta(days=i)
        end_dt = start_dt + timedelta(hours=DURATION_HOURS)
        date_str = start_dt.date().isoformat()

        if _event_exists_on_date(date_str):
            print(f"  [{date_str}] already exists — skip")
            skipped += 1
            continue

        notes = (
            f"Curated KG enrichment: priority-scored chunks, qwen3-vl:32b on GX10, "
            f"confidence>=7, dedup against existing edges.\n"
            f"Script: {PS1_PATH}"
        )

        payload = {
            "title": "Apollo Curated KG Enrichment (qwen3-vl:32b)",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "status": "pending",
            "source": "apollo",
            "category": "apollo",
            "trigger": (
                f"Scheduled curated HippoRAG enrichment for {date_str}. "
                f"Runs {PS1_PATH} — 120 high-priority chunks, qwen3-vl:32b."
            ),
            "notes": notes,
            "cpu_expected_percent": 10,
            "gpu_3090_expected_percent": 0,
            "gb10_expected_percent": 85,
        }

        try:
            result = _post_event(payload)
            ev_id = result.get("id") or result.get("event", {}).get("id") or "?"
            print(f"  [{date_str}] created id={ev_id}")
            created += 1
        except Exception as exc:
            print(f"  [{date_str}] ERROR: {exc}")

    print(f"\nDone. created={created}  skipped={skipped}")
    print(
        f"After {DAYS_TO_SEED} days, the nightly chain in run_apollo_nightly_daily.ps1 "
        f"will self-perpetuate by posting the next night's event."
    )


if __name__ == "__main__":
    main()
