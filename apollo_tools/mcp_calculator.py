"""
apollo.calculator — MCP tool wrapping all Apollo financial calculators.

Registers as: apollo.calculator

Input:
    {"tool": "<name>", "params": {<tool-specific kwargs>}}

Supported tools (param names must match exactly):
    invest:
        compound_growth(principal, annual_rate, years, contributions=0, contribution_frequency="annual")
        real_growth(principal, nominal_rate, inflation_rate, years, contributions=0)
        future_value(present_value, annual_rate, years, compounding="annual")
        present_value(future_value, annual_rate, years, compounding="annual")
    amort:
        amortization_schedule(principal, annual_rate, term_months, extra_payment=0)
        loan_summary(principal, annual_rate, term_months, down_payment=0, extra_payment=0)
    risk:
        sharpe_ratio(returns: list[float], risk_free_rate=0.04, annualize=True)
        capm_return(risk_free_rate, beta, market_return)
        beta_estimate(asset_returns: list[float], market_returns: list[float])
        risk_score(volatility, max_drawdown, sharpe, beta)
        dti_ratio(monthly_debt, monthly_income)
    tax:
        federal_tax(gross_income, filing_status="single", deductions=None, tax_year=2024)
        capital_gains_tax(gain_amount, ordinary_income, holding_period="long", filing_status="single")
        tax_summary(gross_income, filing_status="single", capital_gains=0, holding_period="long", deductions=None)
    retirement:
        four_percent_rule(portfolio_value, annual_expenses=None, inflation_rate=0.03)
        retirement_horizon(current_age, retirement_age, current_savings, monthly_contribution, expected_return=0.07, target_income=None)
        withdrawal_schedule(portfolio_value, annual_withdrawal, expected_return=0.05, inflation_rate=0.03, years=30)
    rebalance:
        portfolio_rebalance(current_holdings: dict, target_allocation: dict)
        drift_analysis(current_holdings: dict, target_allocation: dict, threshold=0.05)
        optimal_rebalance_frequency(portfolio_value, annual_drift_cost, transaction_cost_per_rebalance)
    inflation:
        inflation_adjust(amount, inflation_rate, years, direction="future")
        purchasing_power(amount, start_year, end_year, annual_inflation=0.03)
        real_rate(nominal_rate, inflation_rate)
        income_inflation_target(current_income, years, inflation_rate=0.03)
"""
from __future__ import annotations

from typing import Any, Dict

from common.mcp import register

# Built lazily on first call to avoid circular import issues at module load time.
_TOOL_MAP: Dict[str, Any] = {}


def _load() -> None:
    if _TOOL_MAP:
        return
    from apollo_tools import amort, invest, inflation
    from apollo_tools import risk as risk_mod
    from apollo_tools import tax, retirement, rebalance

    _TOOL_MAP.update({
        # invest
        "compound_growth": invest.compound_growth,
        "real_growth": invest.real_growth,
        "future_value": invest.future_value,
        "present_value": invest.present_value,
        # amort
        "amortization_schedule": amort.amortization_schedule,
        "loan_summary": amort.loan_summary,
        # risk
        "sharpe_ratio": risk_mod.sharpe_ratio,
        "capm_return": risk_mod.capm_return,
        "beta_estimate": risk_mod.beta_estimate,
        "risk_score": risk_mod.risk_score,
        "dti_ratio": risk_mod.dti_ratio,
        # tax
        "federal_tax": tax.federal_tax,
        "capital_gains_tax": tax.capital_gains_tax,
        "tax_summary": tax.tax_summary,
        # retirement
        "four_percent_rule": retirement.four_percent_rule,
        "retirement_horizon": retirement.retirement_horizon,
        "withdrawal_schedule": retirement.withdrawal_schedule,
        # rebalance
        "portfolio_rebalance": rebalance.portfolio_rebalance,
        "drift_analysis": rebalance.drift_analysis,
        "optimal_rebalance_frequency": rebalance.optimal_rebalance_frequency,
        # inflation
        "inflation_adjust": inflation.inflation_adjust,
        "purchasing_power": inflation.purchasing_power,
        "real_rate": inflation.real_rate,
        "income_inflation_target": inflation.income_inflation_target,
    })


def calculator(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatch to an Apollo financial calculator function.

    Required fields:
        tool   (str)  — calculator name (see module docstring for full list)
        params (dict) — keyword arguments forwarded to the function

    Returns {"ok": true, "tool": "<name>", "result": {...}} on success.
    Returns {"ok": false, "error": "...", "available": [...]} on failure.
    """
    _load()
    tool_name = str(payload.get("tool") or "").strip()
    params = payload.get("params") or {}

    if not tool_name:
        return {
            "ok": False,
            "error": "tool name required",
            "available": sorted(_TOOL_MAP.keys()),
        }

    fn = _TOOL_MAP.get(tool_name)
    if fn is None:
        return {
            "ok": False,
            "error": f"unknown tool: {tool_name!r}",
            "available": sorted(_TOOL_MAP.keys()),
        }

    try:
        result = fn(**params)
        return {"ok": True, "tool": tool_name, "result": result}
    except TypeError as exc:
        return {"ok": False, "error": f"bad params for {tool_name}: {exc}", "tool": tool_name}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tool": tool_name}


register(
    "apollo.calculator",
    calculator,
    {
        "name": "apollo.calculator",
        "title": "Apollo Financial Calculator",
        "summary": (
            "Run any Apollo financial calculation: compound growth, amortization, "
            "Sharpe ratio, federal tax, retirement drawdown, portfolio rebalancing, "
            "inflation adjustment. Pass tool name + params dict."
        ),
        "category": "finance",
        "readonly": True,
        "target_scope": "self",
        "recommended_for_primary": True,
    },
)
