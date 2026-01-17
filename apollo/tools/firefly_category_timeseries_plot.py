from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from apollo.config import settings
from apollo.integrations.firefly_client import FireflyClient, FireflyClientError
from apollo.optional_deps import require_matplotlib_pyplot

PLOTS_DIR = Path(settings.data_dir) / "plots"


def _month_key(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str.split(" ")[0])
    except Exception:
        dt = datetime.strptime(date_str.split(" ")[0], "%Y-%m-%d")
    return dt.strftime("%Y-%m")


def run(start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Build a monthly spending time series plot from Firefly III transactions.
    """
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plt = require_matplotlib_pyplot()
    client = FireflyClient()
    try:
        transactions = client.get_transactions(start_date=start_date, end_date=end_date)
    except FireflyClientError as exc:
        return {"result": None, "meta": {"tool_name": "firefly_category_timeseries_plot", "error": str(exc)}}

    series: Dict[str, float] = defaultdict(float)
    for tx in transactions:
        attrs = tx.get("attributes") or {}
        date_str = attrs.get("date") or attrs.get("created_at") or start_date
        key = _month_key(str(date_str))
        try:
            amt = float(attrs.get("amount", 0.0))
        except Exception:
            amt = 0.0
        series[key] += amt

    months = sorted(series.keys())
    totals = [series[m] for m in months]

    fig, ax = plt.subplots()
    ax.plot(months, totals, marker="o")
    ax.set_title(f"Firefly Monthly Totals ({start_date} to {end_date})")
    ax.set_xlabel("Month")
    ax.set_ylabel("Net Amount")
    plt.xticks(rotation=45)
    fig.tight_layout()

    out_path = PLOTS_DIR / f"firefly_timeseries_{start_date}_{end_date}.png"
    fig.savefig(out_path)
    plt.close(fig)

    return {
        "result": {
            "start_date": start_date,
            "end_date": end_date,
            "months": months,
            "totals": totals,
            "plot_path": str(out_path),
            "summary": f"{len(transactions)} transactions across {len(months)} months.",
        },
        "meta": {"tool_name": "firefly_category_timeseries_plot"},
    }
