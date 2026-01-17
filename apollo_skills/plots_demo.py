"""
plots_demo.py - Demo skill for Plots & Artifacts panel

Provides a simple deterministic plot generator to test the UI plumbing.
"""

import os

from apollo.optional_deps import require_matplotlib_pyplot

PLOTS_DIR = os.path.join('logs', 'plots')


def ensure_plots_dir() -> str:
    """Ensure plots directory exists."""
    os.makedirs(PLOTS_DIR, exist_ok=True)
    return PLOTS_DIR


def demo_portfolio_return_plot() -> dict:
    """
    Generate a demo portfolio growth plot.
    
    Returns:
        dict with 'answer', 'plots', and 'artifacts' keys
    """
    plt = require_matplotlib_pyplot()
    ensure_plots_dir()
    
    # Simple fake data: 24 months of growth
    months = list(range(24))
    values = [100 + i * 2 + (i % 3) for i in months]  # Slight variation
    
    path = os.path.join(PLOTS_DIR, 'demo_portfolio_return.png')
    
    plt.figure(figsize=(8, 5))
    plt.plot(months, values, marker='o', linewidth=2, markersize=4)
    plt.xlabel('Month')
    plt.ylabel('Portfolio Value (indexed to 100)')
    plt.title('Demo Portfolio Growth (synthetic data)')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=100)
    plt.close()
    
    return {
        'answer': (
            'Here is a demo portfolio growth plot over 24 months. '
            'This is synthetic data to exercise the Plots & Artifacts panel. '
            f'Plot saved to: {path}'
        ),
        'plots': [path],
        'artifacts': []
    }


def is_demo_trigger(message: str) -> bool:
    """Check if message is a demo plot trigger."""
    msg = message.strip().lower()
    return msg.startswith('demo:') and 'portfolio' in msg and ('plot' in msg or 'return' in msg)
