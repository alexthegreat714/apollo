from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


APOLLO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_ROOT = APOLLO_ROOT.parent
REPORT_ROOT = APOLLO_ROOT / "reports" / "weekly_rag_graph_review"
REVIEW_LOG_ROOT = APOLLO_ROOT / "logs" / "weekly_rag_graph_review"
DOMAIN_STATE_PATH = REVIEW_LOG_ROOT / "domain_decision_state.json"
DOMAIN_POLICY_PATH = REVIEW_LOG_ROOT / "domain_policy.json"
LOCAL_TZ = ZoneInfo("America/Chicago")

PROJECT = "apollo"
PROJECT_LABEL = "Apollo"
TEST_TYPE = "apollo_weekly_rag_graph_review"
CAPABILITY = "weekly_rag_graph_evolution_and_purge_recommendations"
PURGE_STREAK_REQUIRED = 2
HARD_DELETE_APPROVAL_ID_PREFIX = "apollo-rag-hard-delete-approval"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_utc(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _clip(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    except Exception:
        pass
    return default


def _slug(value: Any, fallback: str = "run") -> str:
    text = re.sub(r"[^a-z0-9._-]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("._-")
    return text[:96] or fallback


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_dt(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _default_week_end_local(now: Optional[datetime] = None) -> date:
    local = (now or _utc_now()).astimezone(LOCAL_TZ).date()
    return local


def _window_dates(week_end_local: date, days: int = 7) -> List[date]:
    return [week_end_local - timedelta(days=offset) for offset in range(max(1, int(days)) - 1, -1, -1)]


def _run_id(week_end_local: date) -> str:
    return f"{TEST_TYPE}_{week_end_local.isoformat().replace('-', '')}"


def verification_command(run_id: str) -> str:
    return f'{sys.executable or "python"} "{Path(__file__).resolve()}" --run-id {run_id}'


def _domain_from_url(raw: Any) -> str:
    host = str(urlparse(str(raw or "")).netloc or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _collect_nightly_statuses(window_days: List[date]) -> List[Dict[str, Any]]:
    statuses: List[Dict[str, Any]] = []
    nightly_root = APOLLO_ROOT / "logs" / "nightly"
    if not nightly_root.exists():
        return statuses
    for day in window_days:
        day_dir = nightly_root / day.isoformat()
        if not day_dir.exists():
            continue
        for run_dir in sorted(day_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            status_path = run_dir / "status.json"
            if not status_path.exists():
                continue
            status = _read_json(status_path)
            if status:
                status["_status_path"] = str(status_path)
                status["_run_dir"] = str(run_dir)
                statuses.append(status)
    statuses.sort(
        key=lambda row: (
            _parse_dt(row.get("finished_at")) or _parse_dt(row.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("run_id") or ""),
        )
    )
    return statuses


def _collect_daily_reports(window_days: List[date]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    root = APOLLO_ROOT / "logs" / "daily_reports"
    for day in window_days:
        path = root / day.isoformat() / "apollo_daily_report.json"
        if path.exists():
            payload = _read_json(path)
            if payload:
                payload["_report_path"] = str(path)
                rows.append(payload)
    rows.sort(key=lambda row: str(row.get("date") or ""))
    return rows


def _domain_breakdown(nightly_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    domain_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "domain": "",
            "accepted_count": 0,
            "total_score": 0.0,
            "avg_score": 0.0,
            "tiers": {"A": 0, "B": 0, "C": 0, "D": 0},
            "runs_seen": 0,
            "quality_gate_fail_runs": 0,
            "urls": [],
        }
    )
    for row in nightly_rows:
        gather = (((row.get("stages") or {}).get("gather") or {}).get("quality") or {})
        accepted = [item for item in list(gather.get("accepted_sources") or []) if isinstance(item, dict)]
        run_gate_failed = not bool((row.get("quality") or {}).get("gate_pass"))
        seen_domains = set()
        for source in accepted:
            domain = _domain_from_url(source.get("url"))
            if not domain:
                continue
            bucket = domain_stats[domain]
            bucket["domain"] = domain
            bucket["accepted_count"] += 1
            bucket["total_score"] += _safe_float(source.get("score"), 0.0)
            tier = str(source.get("tier") or "").upper()
            if tier in bucket["tiers"]:
                bucket["tiers"][tier] += 1
            if str(source.get("url") or "") and len(bucket["urls"]) < 8:
                bucket["urls"].append(str(source.get("url")))
            if domain not in seen_domains:
                bucket["runs_seen"] += 1
                seen_domains.add(domain)
        if run_gate_failed:
            for domain in seen_domains:
                domain_stats[domain]["quality_gate_fail_runs"] += 1
    for domain, bucket in domain_stats.items():
        count = int(bucket.get("accepted_count") or 0)
        bucket["avg_score"] = round((_safe_float(bucket.get("total_score"), 0.0) / count), 2) if count > 0 else 0.0
        fail_runs = int(bucket.get("quality_gate_fail_runs") or 0)
        runs_seen = max(1, int(bucket.get("runs_seen") or 0))
        bucket["quality_fail_ratio"] = round(fail_runs / runs_seen, 3)
    return dict(sorted(domain_stats.items(), key=lambda item: (-_safe_float(item[1].get("avg_score")), item[0])))


def _graph_summary(nightly_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graph_rows: List[Dict[str, Any]] = []
    for row in nightly_rows:
        hipporag = ((row.get("stages") or {}).get("hipporag") or {})
        stats = hipporag.get("stats") if isinstance(hipporag.get("stats"), dict) else {}
        quality = hipporag.get("quality") if isinstance(hipporag.get("quality"), dict) else {}
        graph_rows.append(
            {
                "run_id": row.get("run_id"),
                "nodes": _safe_float(stats.get("nodes"), 0.0),
                "edges": _safe_float(stats.get("edges"), 0.0),
                "indexed_docs": _safe_float(stats.get("indexed_docs"), 0.0),
                "requeued_low_quality": _safe_float(quality.get("requeued_low_quality"), _safe_float(hipporag.get("requeued_low_quality"), 0.0)),
                "pending_quarantine": _safe_float(quality.get("pending_quarantine"), _safe_float(hipporag.get("pending_quarantine"), 0.0)),
                "pruned_low_quality_nodes": _safe_float(hipporag.get("pruned_low_quality_nodes"), 0.0),
                "triples": _safe_float(hipporag.get("triples"), 0.0),
                "gate_pass": bool((row.get("quality") or {}).get("gate_pass")),
            }
        )
    if not graph_rows:
        return {"runs": [], "averages": {}, "trend": "unknown"}
    node_avg = sum(item["nodes"] for item in graph_rows) / len(graph_rows)
    edge_avg = sum(item["edges"] for item in graph_rows) / len(graph_rows)
    quarantine_avg = sum(item["pending_quarantine"] for item in graph_rows) / len(graph_rows)
    requeue_avg = sum(item["requeued_low_quality"] for item in graph_rows) / len(graph_rows)
    return {
        "runs": graph_rows[-14:],
        "averages": {
            "nodes": round(node_avg, 2),
            "edges": round(edge_avg, 2),
            "pending_quarantine": round(quarantine_avg, 3),
            "requeued_low_quality": round(requeue_avg, 3),
        },
        "trend": "stable_or_better" if quarantine_avg <= 0.25 and requeue_avg <= 0.25 else "needs_cleanup",
    }


def _simulation_summary(daily_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not daily_reports:
        return {"runs": 0, "avg_trade_score": 0.0, "avg_math_warnings": 0.0, "avg_balance": 0.0, "latest": {}}
    trade_scores: List[float] = []
    warning_counts: List[float] = []
    balances: List[float] = []
    for row in daily_reports:
        trade = row.get("apollo_trade_score") if isinstance(row.get("apollo_trade_score"), dict) else {}
        sim = row.get("simulation_truth") if isinstance(row.get("simulation_truth"), dict) else {}
        if trade.get("score") is not None:
            trade_scores.append(_safe_float(trade.get("score"), 0.0))
        warning_counts.append(_safe_float(sim.get("math_warning_count"), 0.0))
        balances.append(_safe_float(trade.get("simulated_balance"), _safe_float(sim.get("account_value"), 0.0)))
    latest = daily_reports[-1]
    return {
        "runs": len(daily_reports),
        "avg_trade_score": round(sum(trade_scores) / len(trade_scores), 2) if trade_scores else 0.0,
        "avg_math_warnings": round(sum(warning_counts) / len(warning_counts), 2) if warning_counts else 0.0,
        "avg_balance": round(sum(balances) / len(balances), 2) if balances else 0.0,
        "latest": {
            "date": latest.get("date"),
            "trade_score": _safe_float(((latest.get("apollo_trade_score") or {}).get("score")), 0.0),
            "math_warnings": _safe_float(((latest.get("simulation_truth") or {}).get("math_warning_count")), 0.0),
            "balance": _safe_float(((latest.get("apollo_trade_score") or {}).get("simulated_balance")), 0.0),
            "report_path": latest.get("_report_path"),
        },
    }


def _decision_sets(domain_stats: Dict[str, Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    keep: List[Dict[str, Any]] = []
    purge: List[Dict[str, Any]] = []
    quarantine: List[Dict[str, Any]] = []
    for domain, row in domain_stats.items():
        runs_seen = int(row.get("runs_seen") or 0)
        avg_score = _safe_float(row.get("avg_score"), 0.0)
        fail_ratio = _safe_float(row.get("quality_fail_ratio"), 0.0)
        tier_b = int((row.get("tiers") or {}).get("B") or 0)
        tier_a = int((row.get("tiers") or {}).get("A") or 0)
        item = {
            "domain": domain,
            "avg_score": avg_score,
            "runs_seen": runs_seen,
            "quality_fail_ratio": fail_ratio,
            "tiers": row.get("tiers"),
            "sample_urls": list(row.get("urls") or [])[:3],
        }
        if runs_seen >= 3 and avg_score >= 45 and (tier_a > 0 or tier_b > 0) and fail_ratio <= 0.4:
            keep.append({**item, "reason": "Consistent score and tier quality across the week."})
        elif runs_seen >= 3 and (avg_score < 35 or fail_ratio >= 0.8):
            purge.append({**item, "reason": "Repeated low utility or strongly correlated with gate failures."})
        else:
            quarantine.append({**item, "reason": "Mixed evidence; keep for one more week before purge/keep decision."})
    return {"keep": keep, "purge": purge, "quarantine": quarantine}


def _decision_index(decisions: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for bucket_name in ("keep", "purge", "quarantine"):
        for row in list((decisions.get(bucket_name) or [])):
            if not isinstance(row, dict):
                continue
            domain = str(row.get("domain") or "").strip().lower()
            if not domain:
                continue
            indexed[domain] = {"bucket": bucket_name, "row": row}
    return indexed


def _approval_card_id(domain: str) -> str:
    return f"{HARD_DELETE_APPROVAL_ID_PREFIX}-{_slug(domain, 'domain')}"


def _calendar_get_event(event_id: str) -> Dict[str, Any]:
    if not event_id:
        return {}
    try:
        if str(ENGINEERING_ROOT) not in sys.path:
            sys.path.insert(0, str(ENGINEERING_ROOT))
        from Sky.core.calendar_store import get_event

        event = get_event(event_id)
        return event if isinstance(event, dict) else {}
    except Exception:
        return {}


def _ensure_hard_delete_approval_card(domain: str, sample_urls: List[str], reason: str) -> Dict[str, Any]:
    event_id = _approval_card_id(domain)
    now = _utc_now()
    start = _iso_utc(now + timedelta(minutes=5))
    end = _iso_utc(now + timedelta(minutes=35))
    sample_text = "\n".join([f"- {str(url)}" for url in list(sample_urls or [])[:3]]) or "- (no sample URLs captured)"
    notes = "\n".join(
        [
            "Manual approval required before Apollo executes hard-delete purge.",
            f"Domain: {domain}",
            f"Reason: {reason or 'repeated low utility'}",
            f"Purge streak policy: {PURGE_STREAK_REQUIRED}+ consecutive weekly purge flags.",
            "To approve hard delete, set this card status to `understood`.",
            "",
            "Sample URLs:",
            sample_text,
        ]
    )
    payload = {
        "id": event_id,
        "title": f"Apollo hard-delete approval: {domain}",
        "start": start,
        "end": end,
        "status": "partial",
        "source": "apollo",
        "trigger": "manual_approval_required_before_hard_delete",
        "notes": notes,
        "all_day": False,
    }
    try:
        if str(ENGINEERING_ROOT) not in sys.path:
            sys.path.insert(0, str(ENGINEERING_ROOT))
        from Sky.core.calendar_store import create_event, update_event

        existing = _calendar_get_event(event_id)
        if existing:
            merged = dict(payload)
            merged["status"] = existing.get("status") or payload["status"]
            return update_event(event_id, merged)
        return create_event(payload)
    except Exception:
        return _calendar_get_event(event_id)


def _approval_granted(event: Dict[str, Any]) -> bool:
    return str((event or {}).get("status") or "").strip().lower() == "understood"


def _run_hard_delete(domain: str, sample_urls: List[str]) -> Dict[str, Any]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Apollo.nightly_pipeline import _purge_financial_corpus_by_url, _purge_operational_rag_by_url

    cleaned_urls = [str(url).strip() for url in list(sample_urls or []) if str(url).strip()][:3]
    if not cleaned_urls:
        return {"ok": False, "domain": domain, "reason": "no_sample_urls"}
    operations: List[Dict[str, Any]] = []
    success = True
    for url in cleaned_urls:
        try:
            operational = _purge_operational_rag_by_url(url)
        except Exception as exc:
            operational = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
            success = False
        try:
            corpus = _purge_financial_corpus_by_url(url)
        except Exception as exc:
            corpus = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
            success = False
        operations.append({"url": url, "operational_rag": operational, "financial_corpus": corpus})
        if not bool((operational or {}).get("ok", True)) or not bool((corpus or {}).get("ok", True)):
            success = False
    return {"ok": success, "domain": domain, "operations": operations}


def _apply_auto_policy(decisions: Dict[str, List[Dict[str, Any]]], *, window_end: str) -> Dict[str, Any]:
    state_raw = _read_json(DOMAIN_STATE_PATH)
    policy_raw = _read_json(DOMAIN_POLICY_PATH)
    state_domains = dict(state_raw.get("domains") or {}) if isinstance(state_raw.get("domains"), dict) else {}
    policy_domains_prev = dict(policy_raw.get("domains") or {}) if isinstance(policy_raw.get("domains"), dict) else {}
    decision_map = _decision_index(decisions)
    all_domains = sorted(set(state_domains.keys()) | set(policy_domains_prev.keys()) | set(decision_map.keys()))
    now_iso = _iso_utc()

    next_state_domains: Dict[str, Dict[str, Any]] = {}
    next_policy_domains: Dict[str, Dict[str, Any]] = {}

    approval_cards_created: List[str] = []
    approval_pending_domains: List[str] = []
    soft_blocked_domains: List[str] = []
    quarantine_domains: List[str] = []
    hard_deleted_domains: List[str] = []
    hard_delete_executions: List[Dict[str, Any]] = []

    for domain in all_domains:
        if not domain:
            continue
        prev_state = dict(state_domains.get(domain) or {})
        prev_policy = dict(policy_domains_prev.get(domain) or {})
        decision = decision_map.get(domain) or {}
        bucket = str(decision.get("bucket") or "").strip().lower()
        row = decision.get("row") if isinstance(decision.get("row"), dict) else {}
        sample_urls = [str(url).strip() for url in list(row.get("sample_urls") or []) if str(url).strip()][:3]
        reason = str(row.get("reason") or prev_policy.get("reason") or "").strip()

        purge_streak = int(prev_state.get("purge_streak") or 0)
        mode = str(prev_policy.get("mode") or "quarantine").strip().lower()
        hard_deleted_at = str(prev_state.get("hard_deleted_at") or prev_policy.get("hard_deleted_at") or "").strip()
        approval_event_id = str(prev_policy.get("approval_event_id") or "").strip()
        last_counted_window_end = str(prev_state.get("last_counted_window_end") or "").strip()
        should_count_window = bool(window_end) and window_end != last_counted_window_end

        # Back-compat: older state files didn't record the counted window.
        # If we have a streak already, assume the previous run counted the current window once and
        # just stamp the window_end without modifying streak again.
        backfill_only = bool(not last_counted_window_end and should_count_window and int(prev_state.get("purge_streak") or 0) > 0)
        if backfill_only:
            should_count_window = False

        if backfill_only:
            last_counted_window_end = window_end
        elif mode == "hard_deleted" and hard_deleted_at:
            pass
        elif bucket == "keep":
            if should_count_window:
                purge_streak = 0
                last_counted_window_end = window_end
            mode = "keep"
        elif bucket == "quarantine":
            if should_count_window:
                purge_streak = 0
                last_counted_window_end = window_end
            mode = "quarantine"
        elif bucket == "purge":
            if should_count_window:
                purge_streak = max(0, purge_streak) + 1
                last_counted_window_end = window_end
            if purge_streak >= PURGE_STREAK_REQUIRED:
                mode = "purge_soft"
            else:
                mode = "quarantine"
                reason = f"{reason} (purge streak {purge_streak}/{PURGE_STREAK_REQUIRED})".strip()

        approval_event = {}
        approval_status = ""
        if mode == "purge_soft":
            if not approval_event_id:
                approval_event_id = _approval_card_id(domain)
            approval_event = _ensure_hard_delete_approval_card(domain, sample_urls, reason)
            if approval_event:
                approval_status = str(approval_event.get("status") or "").strip().lower()
                if str(approval_event.get("created_at") or "").strip():
                    approval_cards_created.append(domain)
            if _approval_granted(approval_event):
                hard_delete = _run_hard_delete(domain, sample_urls)
                hard_delete_executions.append(hard_delete)
                if bool(hard_delete.get("ok")):
                    mode = "hard_deleted"
                    hard_deleted_at = now_iso
                else:
                    approval_pending_domains.append(domain)
            else:
                approval_pending_domains.append(domain)

        if mode == "purge_soft":
            soft_blocked_domains.append(domain)
        if mode == "quarantine":
            quarantine_domains.append(domain)
        if mode == "hard_deleted":
            hard_deleted_domains.append(domain)
            soft_blocked_domains.append(domain)

        next_state_domains[domain] = {
            "domain": domain,
            "last_seen_at": now_iso,
            "last_decision_bucket": bucket or str(prev_state.get("last_decision_bucket") or ""),
            "purge_streak": purge_streak,
            "hard_deleted_at": hard_deleted_at,
            "last_counted_window_end": last_counted_window_end,
            "updated_at": now_iso,
        }
        next_policy_domains[domain] = {
            "domain": domain,
            "mode": mode,
            "reason": reason,
            "purge_streak": purge_streak,
            "updated_at": now_iso,
            "sample_urls": sample_urls,
            "approval_event_id": approval_event_id,
            "approval_status": approval_status,
            "hard_deleted_at": hard_deleted_at,
        }

    state_payload = {"generated_at": now_iso, "domains": next_state_domains}
    policy_payload = {
        "generated_at": now_iso,
        "domains": next_policy_domains,
        "deny_domains": sorted(set(soft_blocked_domains)),
        "quarantine_domains": sorted(set(quarantine_domains)),
        "hard_deleted_domains": sorted(set(hard_deleted_domains)),
    }
    _write_json(DOMAIN_STATE_PATH, state_payload)
    _write_json(DOMAIN_POLICY_PATH, policy_payload)

    return {
        "state_path": str(DOMAIN_STATE_PATH),
        "policy_path": str(DOMAIN_POLICY_PATH),
        "domains_evaluated": len(all_domains),
        "soft_blocked_domains": sorted(set(soft_blocked_domains)),
        "quarantine_domains": sorted(set(quarantine_domains)),
        "hard_deleted_domains": sorted(set(hard_deleted_domains)),
        "approval_pending_domains": sorted(set(approval_pending_domains)),
        "approval_cards_created_for": sorted(set(approval_cards_created)),
        "hard_delete_executions": hard_delete_executions,
    }


def _score_review(
    nightly_rows: List[Dict[str, Any]],
    sim_summary: Dict[str, Any],
    graph_summary: Dict[str, Any],
    decisions: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not nightly_rows:
        return {"score": 20, "status": "failed", "components": {"coverage": 0, "quality_gate": 0, "graph_hygiene": 0, "simulation_health": 0, "decision_quality": 20}}
    gate_pass_count = sum(1 for row in nightly_rows if bool((row.get("quality") or {}).get("gate_pass")))
    gate_ratio = gate_pass_count / max(1, len(nightly_rows))
    avg_warn = _safe_float(sim_summary.get("avg_math_warnings"), 0.0)
    graph_avg_quarantine = _safe_float(((graph_summary.get("averages") or {}).get("pending_quarantine")), 0.0)
    graph_avg_requeue = _safe_float(((graph_summary.get("averages") or {}).get("requeued_low_quality")), 0.0)

    coverage = 20 if len(nightly_rows) >= 5 else 12 if len(nightly_rows) >= 3 else 6
    quality_gate = 25 if gate_ratio >= 0.7 else 18 if gate_ratio >= 0.5 else 10 if gate_ratio >= 0.3 else 4
    graph_hygiene = 20 if graph_avg_quarantine <= 0.25 and graph_avg_requeue <= 0.25 else 12 if graph_avg_quarantine <= 1.0 else 6
    simulation_health = 20 if avg_warn <= 0.5 else 12 if avg_warn <= 1.5 else 5
    has_actions = any(len(decisions.get(key) or []) > 0 for key in ("keep", "purge", "quarantine"))
    decision_quality = 15 if has_actions else 5

    score = int(max(0, min(100, coverage + quality_gate + graph_hygiene + simulation_health + decision_quality)))
    status = "passed" if score >= 75 else "partial" if score >= 55 else "failed"
    return {
        "score": score,
        "status": status,
        "components": {
            "coverage": coverage,
            "quality_gate": quality_gate,
            "graph_hygiene": graph_hygiene,
            "simulation_health": simulation_health,
            "decision_quality": decision_quality,
        },
    }


def _render_markdown(result: Dict[str, Any]) -> str:
    decisions = result.get("decisions") if isinstance(result.get("decisions"), dict) else {}
    keep = list(decisions.get("keep") or [])
    purge = list(decisions.get("purge") or [])
    quarantine = list(decisions.get("quarantine") or [])
    sim = result.get("simulation_summary") if isinstance(result.get("simulation_summary"), dict) else {}
    graph = result.get("graph_summary") if isinstance(result.get("graph_summary"), dict) else {}
    auto_apply = result.get("auto_apply") if isinstance(result.get("auto_apply"), dict) else {}
    lines = [
        f"# Apollo Weekly RAG + Graph Evolution Review - {result.get('run_id')}",
        "",
        f"- Status: `{result.get('status')}`",
        f"- Score: `{result.get('score')}/100`",
        f"- Week window: `{result.get('window_start')}` to `{result.get('window_end')}`",
        f"- Completed: `{result.get('completed_at')}`",
        "",
        "## Evolution Readout",
        "",
        f"- Nightly runs reviewed: `{result.get('nightly_run_count', 0)}`",
        f"- Nightly gate pass ratio: `{result.get('nightly_gate_pass_ratio', 0)}`",
        f"- Simulation average trade score: `{sim.get('avg_trade_score', 0)}`",
        f"- Simulation average warnings/day: `{sim.get('avg_math_warnings', 0)}`",
        f"- Graph trend: `{graph.get('trend') or 'unknown'}`",
        "",
        "## Weekly Decisions",
        "",
        "### Keep",
    ]
    if keep:
        for item in keep[:20]:
            lines.append(f"- `{item.get('domain')}` avg_score `{item.get('avg_score')}` runs `{item.get('runs_seen')}` reason: {item.get('reason')}")
    else:
        lines.append("- No clear keep promotions this week.")
    lines.extend(["", "### Purge", ""])
    if purge:
        for item in purge[:20]:
            lines.append(f"- `{item.get('domain')}` avg_score `{item.get('avg_score')}` fail_ratio `{item.get('quality_fail_ratio')}` reason: {item.get('reason')}")
    else:
        lines.append("- No immediate purge candidates this week.")
    lines.extend(["", "### Quarantine", ""])
    if quarantine:
        for item in quarantine[:20]:
            lines.append(f"- `{item.get('domain')}` avg_score `{item.get('avg_score')}` reason: {item.get('reason')}")
    else:
        lines.append("- No quarantine candidates this week.")
    lines.extend(
        [
            "",
            "## Proposed Actions For Next Week",
            "",
            "- Purge list should be removed from gather allow-lists or deprioritized in source seeding.",
            "- Quarantine list should remain enabled but tagged for one-week probation.",
            "- Keep list should receive positive weighting in source selection.",
            "- Review simulation math warnings before promoting any trade score changes.",
            "",
            "## Auto-Apply Policy",
            "",
            f"- Soft-blocked domains active: `{len(list(auto_apply.get('soft_blocked_domains') or []))}`",
            f"- Hard-deleted domains: `{len(list(auto_apply.get('hard_deleted_domains') or []))}`",
            f"- Approval cards pending: `{len(list(auto_apply.get('approval_pending_domains') or []))}`",
            f"- Domain policy path: `{auto_apply.get('policy_path') or ''}`",
            "",
            "## Notes",
            "",
            "Quarantine is auto-applied immediately. Purge is auto-block only after two consecutive weekly purge flags.",
            "Hard-delete only executes after manual approval card status is changed to `understood`.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _report_payload(result: Dict[str, Any], report_path: Path, summary_path: Path) -> Dict[str, Any]:
    status = str(result.get("status") or "failed")
    score = int(result.get("score") or 0)
    tests_improved = [
        "weekly_rag_source_keep_purge_quarantine_decisions",
        "weekly_graph_hygiene_readout",
        "weekly_simulation_warning_pressure_tracking",
    ]
    capabilities_confirmed = [CAPABILITY] if status in {"passed", "partial"} else []
    capabilities_not_confirmed = [] if status == "passed" else [CAPABILITY]
    summary = (
        f"Apollo weekly RAG/graph review {status} with score {score}/100; "
        f"keep={len((result.get('decisions') or {}).get('keep') or [])}, "
        f"purge={len((result.get('decisions') or {}).get('purge') or [])}, "
        f"quarantine={len((result.get('decisions') or {}).get('quarantine') or [])}."
    )
    return {
        "project": PROJECT,
        "project_label": PROJECT_LABEL,
        "test_type": TEST_TYPE,
        "run_id": result.get("run_id"),
        "name": "Apollo weekly RAG and graph evolution review",
        "status": status,
        "summary": summary,
        "score": score,
        "computed_score": score,
        "score_components": result.get("score_components") or {},
        "passed": 1 if status == "passed" else 0,
        "failed": 1 if status == "failed" else 0,
        "total": 1,
        "capability": CAPABILITY,
        "verification_command": verification_command(str(result.get("run_id") or "")),
        "tests_improved": tests_improved,
        "tests_confirmed": [
            f"nightly_runs_reviewed={result.get('nightly_run_count', 0)}",
            f"daily_reports_reviewed={result.get('daily_report_count', 0)}",
            f"window={result.get('window_start')}..{result.get('window_end')}",
        ],
        "capabilities_confirmed": capabilities_confirmed,
        "capabilities_not_confirmed": capabilities_not_confirmed,
        "diagnostic": {
            "cause": "weekly_rag_graph_review_ready" if status != "failed" else "weekly_rag_graph_review_low_confidence",
            "category": "none" if status == "passed" else "quality",
            "recoverable": True,
            "next_action": "apply_keep_purge_quarantine_to_source_and_graph_policy",
            "evidence": [str(report_path), str(summary_path)],
            "confidence": 0.88 if status == "passed" else 0.8 if status == "partial" else 0.72,
        },
        "artifact_paths": [str(report_path), str(summary_path)],
        "details": _clip(json.dumps({"decisions": result.get("decisions"), "simulation": result.get("simulation_summary")}, ensure_ascii=False), 4000),
        "completed_at": result.get("completed_at"),
        "report_path": str(report_path),
        "source": PROJECT,
    }


def _record_sky_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    if str(ENGINEERING_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINEERING_ROOT))
    from Sky.services.test_run_reports import record_test_run_report

    return record_test_run_report(payload)


def run_review(*, run_id: Optional[str] = None, week_end_local: Optional[date] = None, no_sky_report: bool = False) -> Dict[str, Any]:
    end_date = week_end_local or _default_week_end_local()
    run_id_value = run_id or _run_id(end_date)
    days = _window_dates(end_date, days=7)
    nightly_rows = _collect_nightly_statuses(days)
    daily_reports = _collect_daily_reports(days)
    domain_stats = _domain_breakdown(nightly_rows)
    decisions = _decision_sets(domain_stats)
    auto_apply = _apply_auto_policy(decisions, window_end=days[-1].isoformat())
    graph = _graph_summary(nightly_rows)
    sim = _simulation_summary(daily_reports)
    scored = _score_review(nightly_rows, sim, graph, decisions)
    gate_pass_count = sum(1 for row in nightly_rows if bool((row.get("quality") or {}).get("gate_pass")))
    result = {
        "ok": scored.get("status") in {"passed", "partial"},
        "run_id": run_id_value,
        "window_start": days[0].isoformat(),
        "window_end": days[-1].isoformat(),
        "completed_at": _iso_utc(),
        "nightly_run_count": len(nightly_rows),
        "nightly_gate_pass_ratio": round((gate_pass_count / max(1, len(nightly_rows))), 3) if nightly_rows else 0.0,
        "daily_report_count": len(daily_reports),
        "domain_stats": domain_stats,
        "decisions": decisions,
        "auto_apply": auto_apply,
        "graph_summary": graph,
        "simulation_summary": sim,
        "score": int(scored.get("score") or 0),
        "score_components": scored.get("components") or {},
        "status": scored.get("status") or "failed",
    }

    run_dir = REPORT_ROOT / _slug(run_id_value, "run")
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"
    report_path = run_dir / "report.md"
    summary_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_markdown(result), encoding="utf-8")

    capability_payload = _report_payload(result, report_path=report_path, summary_path=summary_path)
    sky_report = {"ok": False, "skipped": True}
    if not no_sky_report:
        sky_report = _record_sky_report(capability_payload)

    return {
        **result,
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "capability_report": capability_payload,
        "sky_report": sky_report,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apollo weekly RAG/graph evolution review.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--week-end-date", default="")
    parser.add_argument("--no-sky-report", action="store_true")
    args = parser.parse_args(argv)

    week_end = None
    if args.week_end_date:
        week_end = date.fromisoformat(args.week_end_date[:10])
    result = run_review(run_id=args.run_id or None, week_end_local=week_end, no_sky_report=args.no_sky_report)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
