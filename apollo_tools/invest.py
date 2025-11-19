"""
invest.py - Investment Calculator for Apollo

Compound growth, future value, and present value calculations.
"""

from typing import Dict, Any, List
from .util import validate_positive, validate_non_negative, format_currency, format_percent


def compound_growth(
    principal: float,
    annual_rate: float,
    years: int,
    contributions: float = 0.0,
    contribution_frequency: str = "monthly"
) -> Dict[str, Any]:
    """
    Calculate compound growth with optional periodic contributions.

    Args:
        principal: Initial investment
        annual_rate: Annual return rate (decimal)
        years: Investment period in years
        contributions: Periodic contribution amount
        contribution_frequency: 'monthly', 'quarterly', 'annual'

    Returns:
        Growth summary dictionary
    """
    validate_non_negative(principal, "Principal")
    validate_positive(years, "Years")

    # Determine periods per year
    if contribution_frequency == "monthly":
        periods_per_year = 12
    elif contribution_frequency == "quarterly":
        periods_per_year = 4
    else:
        periods_per_year = 1

    periodic_rate = annual_rate / periods_per_year
    total_periods = years * periods_per_year

    # Calculate future value
    # FV = P(1+r)^n + C * [((1+r)^n - 1) / r]
    growth_factor = (1 + periodic_rate) ** total_periods

    if periodic_rate > 0:
        contribution_factor = ((growth_factor - 1) / periodic_rate)
    else:
        contribution_factor = total_periods

    future_value = principal * growth_factor + contributions * contribution_factor

    total_contributions = contributions * total_periods
    total_invested = principal + total_contributions
    total_growth = future_value - total_invested

    # Year by year breakdown
    yearly_breakdown = []
    balance = principal
    for year in range(1, years + 1):
        year_contributions = contributions * periods_per_year
        year_start = balance
        for _ in range(periods_per_year):
            balance = balance * (1 + periodic_rate) + contributions
        year_growth = balance - year_start - year_contributions
        yearly_breakdown.append({
            "year": year,
            "start_balance": round(year_start, 2),
            "contributions": round(year_contributions, 2),
            "growth": round(year_growth, 2),
            "end_balance": round(balance, 2),
        })

    return {
        "initial_investment": principal,
        "annual_rate": annual_rate,
        "annual_rate_formatted": format_percent(annual_rate),
        "years": years,
        "contribution_amount": contributions,
        "contribution_frequency": contribution_frequency,
        "total_contributions": round(total_contributions, 2),
        "total_invested": round(total_invested, 2),
        "future_value": round(future_value, 2),
        "total_growth": round(total_growth, 2),
        "growth_percent": round((total_growth / total_invested) * 100 if total_invested > 0 else 0, 2),
        "effective_annual_return": round(((future_value / total_invested) ** (1/years) - 1) * 100 if total_invested > 0 else 0, 2),
        "yearly_breakdown": yearly_breakdown,
    }


def real_growth(
    principal: float,
    nominal_rate: float,
    inflation_rate: float,
    years: int,
    contributions: float = 0.0
) -> Dict[str, Any]:
    """
    Calculate inflation-adjusted (real) investment growth.

    Args:
        principal: Initial investment
        nominal_rate: Nominal annual return rate
        inflation_rate: Annual inflation rate
        years: Investment period
        contributions: Monthly contributions

    Returns:
        Real growth summary
    """
    # Calculate real rate using Fisher equation
    real_rate = (1 + nominal_rate) / (1 + inflation_rate) - 1

    # Calculate nominal growth
    nominal = compound_growth(principal, nominal_rate, years, contributions)

    # Calculate real (inflation-adjusted) growth
    real = compound_growth(principal, real_rate, years, contributions)

    return {
        "nominal_rate": nominal_rate,
        "inflation_rate": inflation_rate,
        "real_rate": round(real_rate, 4),
        "years": years,
        "nominal_future_value": nominal["future_value"],
        "real_future_value": real["future_value"],
        "purchasing_power_today": round(nominal["future_value"] / ((1 + inflation_rate) ** years), 2),
        "inflation_erosion": round(nominal["future_value"] - real["future_value"], 2),
        "total_invested": nominal["total_invested"],
    }


def future_value(
    present_value: float,
    annual_rate: float,
    years: int,
    compounding: str = "annual"
) -> Dict[str, Any]:
    """
    Calculate future value with specified compounding.

    Args:
        present_value: Current value
        annual_rate: Annual rate
        years: Time period
        compounding: 'annual', 'semi-annual', 'quarterly', 'monthly', 'daily', 'continuous'

    Returns:
        Future value result
    """
    validate_non_negative(present_value, "Present value")
    validate_positive(years, "Years")

    if compounding == "continuous":
        import math
        fv = present_value * math.exp(annual_rate * years)
    else:
        periods_map = {
            "annual": 1,
            "semi-annual": 2,
            "quarterly": 4,
            "monthly": 12,
            "daily": 365,
        }
        n = periods_map.get(compounding, 1)
        fv = present_value * (1 + annual_rate / n) ** (n * years)

    return {
        "present_value": present_value,
        "annual_rate": annual_rate,
        "years": years,
        "compounding": compounding,
        "future_value": round(fv, 2),
        "total_growth": round(fv - present_value, 2),
        "growth_multiple": round(fv / present_value if present_value > 0 else 0, 3),
    }


def present_value(
    future_value: float,
    annual_rate: float,
    years: int,
    compounding: str = "annual"
) -> Dict[str, Any]:
    """
    Calculate present value (discount future cash flow).

    Args:
        future_value: Future amount
        annual_rate: Discount rate
        years: Time period
        compounding: Compounding frequency

    Returns:
        Present value result
    """
    validate_non_negative(future_value, "Future value")
    validate_positive(years, "Years")

    if compounding == "continuous":
        import math
        pv = future_value / math.exp(annual_rate * years)
    else:
        periods_map = {
            "annual": 1,
            "semi-annual": 2,
            "quarterly": 4,
            "monthly": 12,
            "daily": 365,
        }
        n = periods_map.get(compounding, 1)
        pv = future_value / (1 + annual_rate / n) ** (n * years)

    return {
        "future_value": future_value,
        "annual_rate": annual_rate,
        "years": years,
        "compounding": compounding,
        "present_value": round(pv, 2),
        "discount_amount": round(future_value - pv, 2),
        "discount_factor": round(pv / future_value if future_value > 0 else 0, 4),
    }


if __name__ == "__main__":
    print("Testing Investment Calculator")
    print("=" * 60)

    result = compound_growth(
        principal=10000,
        annual_rate=0.08,
        years=30,
        contributions=500,
        contribution_frequency="monthly"
    )

    print(f"Initial Investment: {format_currency(result['initial_investment'])}")
    print(f"Monthly Contributions: {format_currency(result['contribution_amount'])}")
    print(f"Annual Return: {result['annual_rate_formatted']}")
    print(f"Time Period: {result['years']} years")
    print(f"Total Invested: {format_currency(result['total_invested'])}")
    print(f"Future Value: {format_currency(result['future_value'])}")
    print(f"Total Growth: {format_currency(result['total_growth'])} ({result['growth_percent']}%)")

    print("\n" + "=" * 60)
    print("Real Growth (Inflation Adjusted)")

    real = real_growth(
        principal=10000,
        nominal_rate=0.08,
        inflation_rate=0.03,
        years=30,
        contributions=500
    )

    print(f"Real Rate: {format_percent(real['real_rate'])}")
    print(f"Nominal Future Value: {format_currency(real['nominal_future_value'])}")
    print(f"Purchasing Power (Today's $): {format_currency(real['purchasing_power_today'])}")
