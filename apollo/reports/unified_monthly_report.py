from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from apollo.config import settings
from apollo.llm import call_llm
from apollo.unified.router import load_unified
from apollo.unified.unified_schema import UnifiedTransaction
from apollo.reports.report_history import append_entry
from apollo.optional_deps import require_matplotlib_pyplot

PLOTS_DIR = Path(settings.data_dir) / "plots"
REPORT_DIR = Path(__file__).resolve().parent / "generated"


def _last_full_month() -> tuple[str, str]:
    today = date.today()
    first_this_month = today.replace(day=1)
    last_prev_month = first_this_month - timedelta(days=1)
    start = last_prev_month.replace(day=1)
    return (start.isoformat(), last_prev_month.isoformat())


def _period_from_inputs(month: Optional[str], start_date: Optional[str], end_date: Optional[str]) -> Dict[str, str]:
    if start_date and end_date:
        return {"start_date": start_date, "end_date": end_date}
    if month:
        dt = datetime.strptime(month, "%Y-%m")
        start = dt.replace(day=1).date()
        # compute last day of month
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = next_month - timedelta(days=1)
        return {"start_date": start.isoformat(), "end_date": last.isoformat()}
    start, end = _last_full_month()
    return {"start_date": start, "end_date": end}


def compute_basic_metrics(txns: List[UnifiedTransaction]) -> Dict[str, Any]:
    totals_income = 0.0
    totals_expense = 0.0
    by_category: Dict[str, float] = defaultdict(float)
    by_account: Dict[str, float] = defaultdict(float)
    by_source: Dict[str, float] = defaultdict(float)
    for t in txns:
        amt = float(t.amount or 0.0)
        if amt > 0:
            totals_income += amt
        else:
            totals_expense += abs(amt)
        by_category[t.category or "Uncategorized"] += amt
        by_account[t.account or "Unknown"] += amt
        by_source[t.source or "unknown"] += amt
    net = totals_income - totals_expense
    return {
        "total_income": totals_income,
        "total_expense": totals_expense,
        "net": net,
        "by_category": dict(by_category),
        "by_account": dict(by_account),
        "by_source": dict(by_source),
        "tx_count": len(txns),
    }


def compute_stability_index(metrics: Dict[str, Any]) -> Dict[str, Any]:
    income = metrics.get("total_income", 0.0)
    net = metrics.get("net", 0.0)
    tx_count = metrics.get("tx_count", 0)
    surplus_ratio = net / income if income else 0.0
    score = max(0.0, min(1.0, 0.5 + surplus_ratio)) * 100
    if tx_count < 5:
        score *= 0.7
    grade = "F"
    if score >= 85:
        grade = "A"
    elif score >= 70:
        grade = "B"
    elif score >= 55:
        grade = "C"
    elif score >= 40:
        grade = "D"
    notes = "Surplus" if net >= 0 else "Deficit"
    return {"stability_index": round(score, 2), "grade": grade, "notes": notes}


def generate_plots(
    txns: List[UnifiedTransaction],
    metrics: Dict[str, Any],
    period: Dict[str, str],
    test_mode: bool = False,
) -> List[Dict[str, str]]:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plots: List[Dict[str, str]] = []
    plt = require_matplotlib_pyplot()

    # Net timeseries (daily)
    daily: Dict[str, float] = defaultdict(float)
    for t in txns:
        daily[t.date.strftime("%Y-%m-%d")] += t.amount
    if daily:
        days = sorted(daily.keys())
        nets = [daily[d] for d in days]
        fig, ax = plt.subplots()
        ax.plot(days, nets, marker="o")
        ax.set_title(f"Net by day ({period['start_date']} to {period['end_date']})")
        ax.set_xlabel("Date")
        ax.set_ylabel("Net")
        plt.xticks(rotation=45)
        fig.tight_layout()
        path = PLOTS_DIR / f"unified_net_timeseries_{period['start_date']}_{period['end_date']}.png"
        if not test_mode:
            fig.savefig(path)
            plt.close(fig)
            plots.append({"name": "net_timeseries", "path": str(path)})
        else:
            plt.close(fig)
            plots.append({"name": "net_timeseries", "path": str(path)})

    # Top categories bar
    by_cat = metrics.get("by_category", {}) or {}
    if by_cat:
        top_items = sorted(by_cat.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
        labels = [k for k, _ in top_items]
        values = [v for _, v in top_items]
        fig, ax = plt.subplots()
        ax.bar(labels, values)
        ax.set_title("Top Categories (net)")
        ax.set_ylabel("Net")
        plt.xticks(rotation=45, ha="right")
        fig.tight_layout()
        path = PLOTS_DIR / f"unified_top_categories_{period['start_date']}_{period['end_date']}.png"
        if not test_mode:
            fig.savefig(path, bbox_inches="tight")
            plt.close(fig)
            plots.append({"name": "top_categories", "path": str(path)})
        else:
            plt.close(fig)
            plots.append({"name": "top_categories", "path": str(path)})

    return plots


def build_llm_summary(
    metrics: Dict[str, Any],
    stability: Dict[str, Any],
    period: Dict[str, str],
    plots: List[Dict[str, str]],
    sources: List[str],
    test_mode: bool = False,
) -> str:
    if test_mode:
        return "TEST_SUMMARY"
    prompt = f"""
You are Apollo, a local financial analysis assistant for Alex Blythe.
You are summarizing unified monthly data from sources: {', '.join(sources)}.
Do NOT give personalized investment advice. Summarize what happened and highlight obvious risks/optimizations.

Period: {period}
Metrics: {metrics}
Stability: {stability}
Plots: {[p.get('name') for p in plots]}
"""
    return call_llm(prompt=prompt, mode="conversation")


def write_markdown_report(
    metrics: Dict[str, Any],
    stability: Dict[str, Any],
    period: Dict[str, str],
    plots: List[Dict[str, str]],
    sources: List[str],
    llm_summary: str,
    title: Optional[str] = None,
) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"unified_monthly_report_{ts}.md"
    path = REPORT_DIR / fname
    title = title or "Unified Monthly Financial Report"

    lines = [
        f"# {title}",
        "",
        f"**Period:** {period['start_date']} → {period['end_date']}  ",
        f"**Sources:** {', '.join(sources) if sources else 'none'}  ",
        f"**Transactions:** {metrics.get('tx_count', 0)}",
        "",
        "## 1. High-level metrics",
        f"- Total income: {metrics.get('total_income', 0):.2f}",
        f"- Total expenses: {metrics.get('total_expense', 0):.2f}",
        f"- Net: {metrics.get('net', 0):.2f}",
        "",
        "## 2. Stability",
        f"- Stability index: {stability.get('stability_index', 0)}/100 (Grade: {stability.get('grade', 'N/A')})",
        f"- Notes: {stability.get('notes', '')}",
        "",
        "## 3. Top Categories",
    ]
    by_cat = metrics.get("by_category", {}) or {}
    if by_cat:
        top_items = sorted(by_cat.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
        lines.append("| Category | Net Amount |")
        lines.append("|----------|-----------:|")
        for cat, val in top_items:
            lines.append(f"| {cat} | {val:.2f} |")
    else:
        lines.append("_No category data_")

    lines.append("")
    lines.append("## 4. Accounts and sources")
    by_account = metrics.get("by_account", {}) or {}
    lines.append("### Accounts")
    if by_account:
        for acct, val in sorted(by_account.items(), key=lambda x: abs(x[1]), reverse=True):
            lines.append(f"- {acct}: {val:.2f}")
    else:
        lines.append("_No account data_")
    lines.append("")
    lines.append("### Sources")
    by_source = metrics.get("by_source", {}) or {}
    if by_source:
        for src, val in by_source.items():
            lines.append(f"- {src}: {val:.2f}")
    else:
        lines.append("_No source data_")

    lines.append("")
    lines.append("## 5. Plots")
    if plots:
        for p in plots:
            lines.append(f"- {p.get('name')}: `{p.get('path')}`")
    else:
        lines.append("_No plots generated_")

    lines.append("")
    lines.append("## 6. Narrative Summary")
    lines.append(llm_summary or "_No summary_")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def run(
    month: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    title: Optional[str] = None,
    test_mode: bool = False,
) -> Dict[str, Any]:
    period = _period_from_inputs(month, start_date, end_date)
    try:
        txns = load_unified(start_date=period["start_date"], end_date=period["end_date"])
    except Exception as exc:
        return {"result": None, "meta": {"error": str(exc), "tool_name": "unified_monthly_report"}}

    if not txns:
        return {
            "result": {
                "report_path": None,
                "metrics": {"empty": True},
                "plots": [],
                "sources": [],
                "period": period,
            },
            "meta": {"tool_name": "unified_monthly_report"},
        }

    metrics = compute_basic_metrics(txns)
    stability = compute_stability_index(metrics)
    plots = generate_plots(txns, metrics, period, test_mode=test_mode)
    sources = sorted({t.source for t in txns})
    llm_summary = build_llm_summary(metrics, stability, period, plots, sources, test_mode=test_mode)
    report_path = write_markdown_report(metrics, stability, period, plots, sources, llm_summary, title=title)

    try:
        top_cats = sorted((metrics.get("by_category") or {}).items(), key=lambda x: abs(x[1]), reverse=True)[:10]
        history_entry = {
            "report_path": report_path,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "period": period,
            "metrics": {
                "total_income": metrics.get("total_income", 0.0),
                "total_expense": metrics.get("total_expense", 0.0),
                "net": metrics.get("net", 0.0),
                "tx_count": metrics.get("tx_count", 0),
                "by_category": dict(top_cats),
            },
            "stability": stability,
            "sources": sources,
        }
        append_entry(history_entry)
    except Exception:
        pass

    return {
        "result": {
            "report_path": report_path,
            "metrics": metrics,
            "stability": stability,
            "plots": plots,
            "sources": sources,
            "period": period,
            "llm_summary": llm_summary,
        },
        "meta": {"tool_name": "unified_monthly_report"},
    }
