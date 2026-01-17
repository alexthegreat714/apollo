from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..optional_deps import require_matplotlib_pyplot
from ..config import settings
from ..models import Transaction
from ..analytics.summary import _filter_by_date, summarize_categories


def _plots_dir() -> Path:
    path = Path(settings.data_dir) / "plots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plot_category_breakdown(
    transactions: List[Transaction],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Path:
    plt = require_matplotlib_pyplot()
    plots_dir = _plots_dir()
    txs = _filter_by_date(transactions, start, end)
    cats = summarize_categories(txs, start, end)
    if not cats:
        out_path = plots_dir / "categories_empty.png"
        fig, ax = plt.subplots()
        ax.set_title("No data")
        fig.savefig(out_path)
        plt.close(fig)
        return out_path

    labels = [c.category for c in cats]
    values = [abs(c.total) for c in cats]

    fig, ax = plt.subplots()
    ax.bar(labels, values)
    ax.set_title("Spending by Category")
    ax.set_ylabel("Amount")
    ax.tick_params(axis="x", rotation=45)

    out_path = plots_dir / "categories.png"
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path
