from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from apollo.data.store import get_conn, init_db
from apollo.optional_deps import require_matplotlib_pyplot

PLOTS_DIR = Path(__file__).resolve().parents[1] / "plots"


def _parse_date(date_str: str) -> datetime.date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def run(
    category: str,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """
    Generate a time series plot (monthly) for a YNAB category between start_date and end_date.
    """

    init_db()
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plt = require_matplotlib_pyplot(force=True)

    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if end < start:
        return {
            "result": None,
            "meta": {
                "tool_name": "ynab_category_timeseries_plot",
                "error": "end_date must be >= start_date",
            },
        }

    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT date, amount
            FROM transactions
            WHERE category = ?
              AND date >= ?
              AND date <= ?
            ORDER BY date ASC
            """,
            (category, start_date, end_date),
        )
        rows = cur.fetchall()

    if not rows:
        return {
            "result": None,
            "meta": {
                "tool_name": "ynab_category_timeseries_plot",
                "warning": "No matching transactions for category/date range.",
            },
        }

    monthly: Dict[str, float] = defaultdict(float)
    for date_str, amount in rows:
        date_obj = _parse_date(date_str)
        if start <= date_obj <= end:
            key = f"{date_obj.year:04d}-{date_obj.month:02d}"
            monthly[key] += amount

    months: List[str] = sorted(monthly.keys())
    totals: List[float] = [-monthly[m] for m in months]

    fig, ax = plt.subplots()
    ax.plot(months, totals, marker="o")
    ax.set_title(f"Spending for '{category}' ({start_date} to {end_date})")
    ax.set_xlabel("Month")
    ax.set_ylabel("Total spent")
    fig.autofmt_xdate(rotation=45)

    plot_filename = f"ynab_{category.replace(' ', '_')}_{start_date}_{end_date}.png"
    plot_path = PLOTS_DIR / plot_filename
    fig.tight_layout()
    fig.savefig(plot_path)
    plt.close(fig)

    return {
        "result": {
            "category": category,
            "start_date": start_date,
            "end_date": end_date,
            "months": months,
            "totals": totals,
            "plot_path": str(plot_path),
        },
        "meta": {
            "tool_name": "ynab_category_timeseries_plot",
        },
    }
