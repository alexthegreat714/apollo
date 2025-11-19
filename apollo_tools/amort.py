"""
amort.py - Amortization Calculator for Apollo

Calculates loan amortization schedules and summaries.
"""

from typing import Dict, Any, List
from .util import validate_positive, format_currency, format_percent


def amortization_schedule(
    principal: float,
    annual_rate: float,
    term_months: int,
    extra_payment: float = 0.0
) -> Dict[str, Any]:
    """
    Generate a complete loan amortization schedule.

    Args:
        principal: Loan principal amount
        annual_rate: Annual interest rate (decimal, e.g., 0.065 for 6.5%)
        term_months: Loan term in months
        extra_payment: Extra monthly payment toward principal

    Returns:
        Dictionary with schedule and summary
    """
    validate_positive(principal, "Principal")
    validate_positive(term_months, "Term months")

    if annual_rate < 0:
        raise ValueError("Annual rate cannot be negative")

    monthly_rate = annual_rate / 12

    # Calculate standard monthly payment
    if monthly_rate > 0:
        payment = principal * (monthly_rate * (1 + monthly_rate) ** term_months) / \
                  ((1 + monthly_rate) ** term_months - 1)
    else:
        payment = principal / term_months

    schedule = []
    balance = principal
    total_interest = 0.0
    total_principal = 0.0
    actual_months = 0

    for month in range(1, term_months + 1):
        if balance <= 0:
            break

        interest_payment = balance * monthly_rate
        principal_payment = payment - interest_payment + extra_payment

        # Don't overpay
        if principal_payment > balance:
            principal_payment = balance
            payment = principal_payment + interest_payment

        balance -= principal_payment
        if balance < 0.01:
            balance = 0

        total_interest += interest_payment
        total_principal += principal_payment
        actual_months = month

        schedule.append({
            "month": month,
            "payment": round(payment + extra_payment, 2),
            "principal": round(principal_payment, 2),
            "interest": round(interest_payment, 2),
            "balance": round(balance, 2),
        })

        if balance == 0:
            break

    return {
        "loan_amount": principal,
        "annual_rate": annual_rate,
        "term_months": term_months,
        "monthly_payment": round(payment, 2),
        "extra_payment": extra_payment,
        "total_payments": round(total_principal + total_interest, 2),
        "total_interest": round(total_interest, 2),
        "total_principal": round(total_principal, 2),
        "actual_months": actual_months,
        "months_saved": term_months - actual_months if extra_payment > 0 else 0,
        "interest_saved": 0.0,  # Calculated below if extra payment
        "schedule": schedule,
    }


def loan_summary(
    principal: float,
    annual_rate: float,
    term_months: int,
    down_payment: float = 0.0,
    extra_payment: float = 0.0
) -> Dict[str, Any]:
    """
    Generate a loan summary with key metrics.

    Args:
        principal: Total loan amount (before down payment)
        annual_rate: Annual interest rate
        term_months: Loan term in months
        down_payment: Down payment amount
        extra_payment: Extra monthly payment

    Returns:
        Loan summary dictionary
    """
    loan_amount = principal - down_payment
    validate_positive(loan_amount, "Loan amount after down payment")

    # Calculate with and without extra payment
    base_schedule = amortization_schedule(loan_amount, annual_rate, term_months)

    result = {
        "purchase_price": principal,
        "down_payment": down_payment,
        "down_payment_percent": round(down_payment / principal * 100, 2) if principal > 0 else 0,
        "loan_amount": loan_amount,
        "annual_rate": annual_rate,
        "annual_rate_formatted": format_percent(annual_rate),
        "term_months": term_months,
        "term_years": term_months / 12,
        "monthly_payment": base_schedule["monthly_payment"],
        "monthly_payment_formatted": format_currency(base_schedule["monthly_payment"]),
        "total_payments": base_schedule["total_payments"],
        "total_interest": base_schedule["total_interest"],
        "interest_to_principal_ratio": round(base_schedule["total_interest"] / loan_amount, 3),
    }

    if extra_payment > 0:
        extra_schedule = amortization_schedule(loan_amount, annual_rate, term_months, extra_payment)
        result["with_extra_payment"] = {
            "extra_payment": extra_payment,
            "new_monthly_total": base_schedule["monthly_payment"] + extra_payment,
            "months_saved": term_months - extra_schedule["actual_months"],
            "years_saved": round((term_months - extra_schedule["actual_months"]) / 12, 1),
            "interest_saved": round(base_schedule["total_interest"] - extra_schedule["total_interest"], 2),
            "new_total_interest": extra_schedule["total_interest"],
            "payoff_months": extra_schedule["actual_months"],
        }

    return result


if __name__ == "__main__":
    # Test amortization
    print("Testing Amortization Calculator")
    print("=" * 60)

    summary = loan_summary(
        principal=300000,
        annual_rate=0.065,
        term_months=360,
        down_payment=60000,
        extra_payment=200
    )

    print(f"Purchase Price: {format_currency(summary['purchase_price'])}")
    print(f"Down Payment: {format_currency(summary['down_payment'])} ({summary['down_payment_percent']}%)")
    print(f"Loan Amount: {format_currency(summary['loan_amount'])}")
    print(f"Interest Rate: {summary['annual_rate_formatted']}")
    print(f"Term: {summary['term_years']} years")
    print(f"Monthly Payment: {summary['monthly_payment_formatted']}")
    print(f"Total Interest: {format_currency(summary['total_interest'])}")

    if "with_extra_payment" in summary:
        extra = summary["with_extra_payment"]
        print(f"\nWith ${extra['extra_payment']} extra/month:")
        print(f"  Months Saved: {extra['months_saved']} ({extra['years_saved']} years)")
        print(f"  Interest Saved: {format_currency(extra['interest_saved'])}")
