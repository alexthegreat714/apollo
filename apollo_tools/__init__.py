"""
Apollo Financial Tools Library

Comprehensive financial calculation tools for deep-mode reasoning.
"""

from .amort import amortization_schedule, loan_summary
from .invest import compound_growth, real_growth, future_value, present_value
from .inflation import inflation_adjust, purchasing_power, real_rate
from .risk import sharpe_ratio, capm_return, beta_estimate, risk_score, dti_ratio
from .tax import federal_tax, capital_gains_tax, tax_summary
from .rebalance import portfolio_rebalance, drift_analysis
from .retirement import four_percent_rule, retirement_horizon, withdrawal_schedule

__all__ = [
    # Amortization
    "amortization_schedule",
    "loan_summary",
    # Investing
    "compound_growth",
    "real_growth",
    "future_value",
    "present_value",
    # Inflation
    "inflation_adjust",
    "purchasing_power",
    "real_rate",
    # Risk
    "sharpe_ratio",
    "capm_return",
    "beta_estimate",
    "risk_score",
    "dti_ratio",
    # Tax
    "federal_tax",
    "capital_gains_tax",
    "tax_summary",
    # Rebalance
    "portfolio_rebalance",
    "drift_analysis",
    # Retirement
    "four_percent_rule",
    "retirement_horizon",
    "withdrawal_schedule",
]
