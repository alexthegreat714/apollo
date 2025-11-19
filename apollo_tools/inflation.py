"""
inflation.py - Inflation Calculator for Apollo

Purchasing power, inflation adjustment, and real rate calculations.
"""

from typing import Dict, Any, List
from .util import validate_positive, format_currency, format_percent


def inflation_adjust(
    amount: float,
    inflation_rate: float,
    years: int,
    direction: str = "future"
) -> Dict[str, Any]:
    """
    Adjust an amount for inflation.

    Args:
        amount: Dollar amount
        inflation_rate: Annual inflation rate (decimal)
        years: Number of years
        direction: 'future' (what today's money will be worth) or
                   'past' (what past money is worth today)

    Returns:
        Inflation adjustment result
    """
    validate_positive(amount, "Amount")
    validate_positive(years, "Years")

    if direction == "future":
        # Today's dollars in future purchasing power
        adjusted = amount / ((1 + inflation_rate) ** years)
        purchasing_power_loss = amount - adjusted
    else:
        # Past dollars in today's purchasing power
        adjusted = amount * ((1 + inflation_rate) ** years)
        purchasing_power_loss = adjusted - amount

    return {
        "original_amount": amount,
        "inflation_rate": inflation_rate,
        "inflation_rate_formatted": format_percent(inflation_rate),
        "years": years,
        "direction": direction,
        "adjusted_amount": round(adjusted, 2),
        "purchasing_power_change": round(purchasing_power_loss, 2),
        "cumulative_inflation": round(((1 + inflation_rate) ** years - 1) * 100, 2),
    }


def purchasing_power(
    amount: float,
    start_year: int,
    end_year: int,
    annual_inflation: float = 0.03
) -> Dict[str, Any]:
    """
    Calculate purchasing power change over time.

    Args:
        amount: Dollar amount
        start_year: Starting year
        end_year: Ending year
        annual_inflation: Average annual inflation rate

    Returns:
        Purchasing power analysis
    """
    years = end_year - start_year
    if years < 0:
        years = abs(years)
        start_year, end_year = end_year, start_year

    # Value in end year dollars
    end_value = amount * ((1 + annual_inflation) ** years)

    # Purchasing power retained
    purchasing_power_retained = amount / ((1 + annual_inflation) ** years)
    power_lost_percent = ((amount - purchasing_power_retained) / amount) * 100

    return {
        "amount": amount,
        "start_year": start_year,
        "end_year": end_year,
        "years": years,
        "annual_inflation": annual_inflation,
        "value_in_end_year_dollars": round(end_value, 2),
        "purchasing_power_retained": round(purchasing_power_retained, 2),
        "purchasing_power_lost_percent": round(power_lost_percent, 2),
        "inflation_multiplier": round((1 + annual_inflation) ** years, 3),
    }


def real_rate(
    nominal_rate: float,
    inflation_rate: float
) -> Dict[str, Any]:
    """
    Calculate real rate using Fisher equation.

    Args:
        nominal_rate: Nominal interest/return rate
        inflation_rate: Inflation rate

    Returns:
        Real rate calculation
    """
    # Fisher equation: (1 + r_real) = (1 + r_nominal) / (1 + r_inflation)
    real = (1 + nominal_rate) / (1 + inflation_rate) - 1

    # Approximate method for comparison
    approximate = nominal_rate - inflation_rate

    return {
        "nominal_rate": nominal_rate,
        "nominal_rate_formatted": format_percent(nominal_rate),
        "inflation_rate": inflation_rate,
        "inflation_rate_formatted": format_percent(inflation_rate),
        "real_rate": round(real, 4),
        "real_rate_formatted": format_percent(real),
        "approximate_real_rate": round(approximate, 4),
        "approximation_error": round(abs(real - approximate), 6),
    }


def income_inflation_target(
    current_income: float,
    years: int,
    inflation_rate: float = 0.03
) -> Dict[str, Any]:
    """
    Calculate required future income to maintain purchasing power.

    Args:
        current_income: Current annual income
        years: Years into future
        inflation_rate: Expected inflation rate

    Returns:
        Income projection
    """
    validate_positive(current_income, "Current income")
    validate_positive(years, "Years")

    required_income = current_income * ((1 + inflation_rate) ** years)

    # Calculate annual raises needed
    yearly_projections = []
    income = current_income
    for year in range(1, years + 1):
        raise_amount = income * inflation_rate
        income = income * (1 + inflation_rate)
        yearly_projections.append({
            "year": year,
            "required_income": round(income, 2),
            "raise_amount": round(raise_amount, 2),
        })

    return {
        "current_income": current_income,
        "years": years,
        "inflation_rate": inflation_rate,
        "required_future_income": round(required_income, 2),
        "total_increase": round(required_income - current_income, 2),
        "increase_percent": round(((required_income / current_income) - 1) * 100, 2),
        "average_annual_raise_needed": round(inflation_rate * 100, 2),
        "projections": yearly_projections,
    }


if __name__ == "__main__":
    print("Testing Inflation Calculator")
    print("=" * 60)

    # Test purchasing power
    pp = purchasing_power(
        amount=100000,
        start_year=2024,
        end_year=2044,
        annual_inflation=0.03
    )

    print(f"${pp['amount']:,} in {pp['start_year']}")
    print(f"Will be worth ${pp['value_in_end_year_dollars']:,} in {pp['end_year']} dollars")
    print(f"But only have ${pp['purchasing_power_retained']:,.0f} purchasing power")
    print(f"Purchasing power lost: {pp['purchasing_power_lost_percent']:.1f}%")

    print("\n" + "=" * 60)
    print("Real Rate Calculation")

    rr = real_rate(nominal_rate=0.07, inflation_rate=0.03)
    print(f"Nominal Rate: {rr['nominal_rate_formatted']}")
    print(f"Inflation: {rr['inflation_rate_formatted']}")
    print(f"Real Rate: {rr['real_rate_formatted']}")
