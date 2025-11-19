"""
retirement.py - Retirement Planning Tools for Apollo

4% rule, retirement horizon modeling, and withdrawal schedules.
"""

from typing import Dict, Any, List
from .util import format_currency, format_percent


def four_percent_rule(
    portfolio_value: float,
    annual_expenses: float = None,
    inflation_rate: float = 0.03
) -> Dict[str, Any]:
    """
    Calculate 4% rule safe withdrawal rate.

    Args:
        portfolio_value: Current portfolio value
        annual_expenses: Optional annual expenses (calculates required if not given)
        inflation_rate: Expected inflation rate

    Returns:
        4% rule analysis
    """
    # Standard 4% withdrawal
    safe_withdrawal = portfolio_value * 0.04
    monthly_withdrawal = safe_withdrawal / 12

    # Calculate how many years it could last (simplified)
    years_at_4pct = 25  # Historical safe period

    # More conservative 3.5%
    conservative_withdrawal = portfolio_value * 0.035

    # More aggressive 4.5%
    aggressive_withdrawal = portfolio_value * 0.045

    result = {
        "portfolio_value": portfolio_value,
        "safe_withdrawal_annual": round(safe_withdrawal, 2),
        "safe_withdrawal_monthly": round(monthly_withdrawal, 2),
        "withdrawal_rate": 0.04,
        "conservative_annual": round(conservative_withdrawal, 2),
        "aggressive_annual": round(aggressive_withdrawal, 2),
        "expected_duration_years": years_at_4pct,
    }

    if annual_expenses:
        # Calculate if current portfolio supports expenses
        required_portfolio = annual_expenses / 0.04
        surplus_deficit = portfolio_value - required_portfolio
        coverage_years = portfolio_value / annual_expenses if annual_expenses > 0 else 0

        result["annual_expenses"] = annual_expenses
        result["required_portfolio"] = round(required_portfolio, 2)
        result["surplus_deficit"] = round(surplus_deficit, 2)
        result["expenses_covered"] = portfolio_value >= required_portfolio
        result["simple_coverage_years"] = round(coverage_years, 1)

        if surplus_deficit >= 0:
            result["status"] = "Portfolio supports expenses at 4% rule"
        else:
            result["status"] = f"Need additional {format_currency(abs(surplus_deficit))} for 4% rule"

    return result


def retirement_horizon(
    current_age: int,
    retirement_age: int,
    current_savings: float,
    monthly_contribution: float,
    expected_return: float = 0.07,
    target_income: float = None,
    social_security: float = 0,
    pension: float = 0
) -> Dict[str, Any]:
    """
    Model retirement savings trajectory.

    Args:
        current_age: Current age
        retirement_age: Target retirement age
        current_savings: Current retirement savings
        monthly_contribution: Monthly contribution
        expected_return: Expected annual return
        target_income: Target annual retirement income
        social_security: Expected annual Social Security benefit
        pension: Expected annual pension benefit

    Returns:
        Retirement projection
    """
    years_to_retirement = retirement_age - current_age

    if years_to_retirement <= 0:
        return {"error": "Retirement age must be greater than current age"}

    # Project savings at retirement
    monthly_rate = expected_return / 12
    months = years_to_retirement * 12

    # Future value with contributions
    growth_factor = (1 + monthly_rate) ** months
    if monthly_rate > 0:
        contribution_factor = ((growth_factor - 1) / monthly_rate)
    else:
        contribution_factor = months

    projected_savings = current_savings * growth_factor + monthly_contribution * contribution_factor

    # Calculate sustainable income
    annual_withdrawal = projected_savings * 0.04
    total_income = annual_withdrawal + social_security + pension
    monthly_income = total_income / 12

    # Year by year projection
    yearly_projection = []
    balance = current_savings
    for year in range(1, years_to_retirement + 1):
        age = current_age + year
        annual_contrib = monthly_contribution * 12
        growth = balance * expected_return
        balance = balance + growth + annual_contrib

        yearly_projection.append({
            "year": year,
            "age": age,
            "balance": round(balance, 2),
            "growth": round(growth, 2),
            "contributions": round(annual_contrib, 2),
        })

    result = {
        "current_age": current_age,
        "retirement_age": retirement_age,
        "years_to_retirement": years_to_retirement,
        "current_savings": current_savings,
        "monthly_contribution": monthly_contribution,
        "annual_contribution": monthly_contribution * 12,
        "expected_return": expected_return,
        "projected_savings_at_retirement": round(projected_savings, 2),
        "sustainable_withdrawal": round(annual_withdrawal, 2),
        "social_security": social_security,
        "pension": pension,
        "total_annual_income": round(total_income, 2),
        "total_monthly_income": round(monthly_income, 2),
        "yearly_projection": yearly_projection,
    }

    if target_income:
        income_gap = target_income - total_income
        result["target_income"] = target_income
        result["income_gap"] = round(income_gap, 2)

        if income_gap <= 0:
            result["meets_target"] = True
            result["status"] = "On track to meet retirement income goal"
        else:
            # Calculate additional savings needed
            additional_needed = income_gap / 0.04
            result["meets_target"] = False
            result["additional_savings_needed"] = round(additional_needed, 2)
            result["status"] = f"Need additional {format_currency(additional_needed)} to meet income goal"

            # Calculate increased contribution needed
            # Solve for C where: FV = P*g^n + C*((g^n-1)/r) = required
            required_total = (target_income - social_security - pension) / 0.04
            needed_from_contributions = required_total - current_savings * growth_factor

            if contribution_factor > 0:
                required_monthly = needed_from_contributions / contribution_factor
                result["required_monthly_contribution"] = round(max(0, required_monthly), 2)
                result["contribution_increase_needed"] = round(max(0, required_monthly - monthly_contribution), 2)

    return result


def withdrawal_schedule(
    portfolio_value: float,
    annual_withdrawal: float,
    expected_return: float = 0.05,
    inflation_rate: float = 0.03,
    years: int = 30
) -> Dict[str, Any]:
    """
    Generate retirement withdrawal schedule.

    Args:
        portfolio_value: Starting portfolio value
        annual_withdrawal: Initial annual withdrawal
        expected_return: Expected portfolio return
        inflation_rate: Inflation rate (withdrawals increase)
        years: Years to project

    Returns:
        Withdrawal schedule
    """
    schedule = []
    balance = portfolio_value
    withdrawal = annual_withdrawal
    total_withdrawn = 0
    depleted_year = None

    for year in range(1, years + 1):
        if balance <= 0:
            if depleted_year is None:
                depleted_year = year - 1
            schedule.append({
                "year": year,
                "withdrawal": 0,
                "growth": 0,
                "end_balance": 0,
                "depleted": True,
            })
            continue

        # Withdraw at start of year
        actual_withdrawal = min(withdrawal, balance)
        balance -= actual_withdrawal
        total_withdrawn += actual_withdrawal

        # Growth on remaining balance
        growth = balance * expected_return
        balance += growth

        # Inflation adjust withdrawal for next year
        next_withdrawal = withdrawal * (1 + inflation_rate)

        schedule.append({
            "year": year,
            "withdrawal": round(actual_withdrawal, 2),
            "withdrawal_rate": round(actual_withdrawal / portfolio_value * 100, 2),
            "growth": round(growth, 2),
            "end_balance": round(balance, 2),
            "depleted": False,
        })

        withdrawal = next_withdrawal

        if balance <= 0 and depleted_year is None:
            depleted_year = year

    # Sustainability analysis
    final_balance = balance if balance > 0 else 0
    sustainable = final_balance > 0

    return {
        "initial_portfolio": portfolio_value,
        "initial_withdrawal": annual_withdrawal,
        "initial_withdrawal_rate": round(annual_withdrawal / portfolio_value * 100, 2),
        "expected_return": expected_return,
        "inflation_rate": inflation_rate,
        "years_projected": years,
        "total_withdrawn": round(total_withdrawn, 2),
        "final_balance": round(final_balance, 2),
        "sustainable": sustainable,
        "depleted_year": depleted_year,
        "schedule": schedule,
        "summary": f"Portfolio {'sustained' if sustainable else f'depleted in year {depleted_year}'} over {years} years",
    }


if __name__ == "__main__":
    print("Testing Retirement Planning Tools")
    print("=" * 60)

    # 4% Rule
    rule = four_percent_rule(
        portfolio_value=1500000,
        annual_expenses=55000
    )

    print(f"Portfolio: {format_currency(rule['portfolio_value'])}")
    print(f"Safe Annual Withdrawal: {format_currency(rule['safe_withdrawal_annual'])}")
    print(f"Safe Monthly Withdrawal: {format_currency(rule['safe_withdrawal_monthly'])}")
    print(f"Status: {rule['status']}")

    print("\n" + "=" * 60)
    print("Retirement Horizon")

    horizon = retirement_horizon(
        current_age=35,
        retirement_age=65,
        current_savings=150000,
        monthly_contribution=1500,
        expected_return=0.07,
        target_income=80000,
        social_security=24000,
        pension=0
    )

    print(f"Years to Retirement: {horizon['years_to_retirement']}")
    print(f"Projected Savings: {format_currency(horizon['projected_savings_at_retirement'])}")
    print(f"Total Annual Income: {format_currency(horizon['total_annual_income'])}")
    print(f"Status: {horizon['status']}")

    print("\n" + "=" * 60)
    print("Withdrawal Schedule")

    sched = withdrawal_schedule(
        portfolio_value=1000000,
        annual_withdrawal=40000,
        years=30
    )

    print(f"Initial Portfolio: {format_currency(sched['initial_portfolio'])}")
    print(f"Initial Withdrawal: {format_currency(sched['initial_withdrawal'])}")
    print(f"After {sched['years_projected']} years: {format_currency(sched['final_balance'])}")
    print(f"Summary: {sched['summary']}")
