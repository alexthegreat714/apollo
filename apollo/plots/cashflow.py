from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..optional_deps import require_matplotlib_pyplot
from ..config import settings
from ..models import Transaction
from ..analytics.summary import _filter_by_date, cashflow_series


def _plots_dir() -> Path:
    path = Path(settings.data_dir) / "plots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plot_cumulative_cashflow(
    transactions: List[Transaction],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Path:
    plt = require_matplotlib_pyplot()
    plots_dir = _plots_dir()
    txs = _filter_by_date(transactions, start, end)
    cf = cashflow_series(txs)
    if not cf:
        out_path = plots_dir / "cashflow_empty.png"
        fig, ax = plt.subplots()
        ax.set_title("No data")
        fig.savefig(out_path)
        plt.close(fig)
        return out_path

    dates = [p.date for p in cf]
    nets = [p.net for p in cf]
    cumulative = []
    total = 0.0
    for n in nets:
        total += n
        cumulative.append(total)

    fig, ax = plt.subplots()
    ax.plot(dates, cumulative)
    ax.set_title("Cumulative Net Cashflow")
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative Net")
    fig.autofmt_xdate()

    out_path = plots_dir / "cashflow.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
