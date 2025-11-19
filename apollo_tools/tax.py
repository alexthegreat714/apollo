"""
tax.py - Tax Estimation Tools for Apollo

US Federal tax, capital gains, and tax summary calculations.
"""

from typing import Dict, Any, List
from .util import format_currency, format_percent


# 2024 Federal Tax Brackets (Single)
FEDERAL_BRACKETS_SINGLE_2024 = [
    (11600, 0.10),
    (47150, 0.12),
    (100525, 0.22),
    (191950, 0.24),
    (243725, 0.32),
    (609350, 0.35),
    (float('inf'), 0.37),
]

# 2024 Federal Tax Brackets (Married Filing Jointly)
FEDERAL_BRACKETS_MFJ_2024 = [
    (23200, 0.10),
    (94300, 0.12),
    (201050, 0.22),
    (383900, 0.24),
    (487450, 0.32),
    (731200, 0.35),
    (float('inf'), 0.37),
]

# Standard Deductions 2024
STANDARD_DEDUCTIONS_2024 = {
    "single": 14600,
    "married_filing_jointly": 29200,
    "married_filing_separately": 14600,
    "head_of_household": 21900,
}

# Capital Gains Brackets 2024
LTCG_BRACKETS_SINGLE_2024 = [
    (47025, 0.00),
    (518900, 0.15),
    (float('inf'), 0.20),
]

LTCG_BRACKETS_MFJ_2024 = [
    (94050, 0.00),
    (583750, 0.15),
    (float('inf'), 0.20),
]


def federal_tax(
    gross_income: float,
    filing_status: str = "single",
    deductions: float = None,
    tax_year: int = 2024
) -> Dict[str, Any]:
    """
    Calculate federal income tax.

    Args:
        gross_income: Gross annual income
        filing_status: 'single', 'married_filing_jointly', 'married_filing_separately', 'head_of_household'
        deductions: Itemized deductions (uses standard if None)
        tax_year: Tax year

    Returns:
        Tax calculation breakdown
    """
    # Get standard deduction
    standard_deduction = STANDARD_DEDUCTIONS_2024.get(filing_status, 14600)

    # Use larger of standard or itemized
    if deductions is None or deductions < standard_deduction:
        deduction_amount = standard_deduction
        deduction_type = "standard"
    else:
        deduction_amount = deductions
        deduction_type = "itemized"

    # Calculate taxable income
    taxable_income = max(0, gross_income - deduction_amount)

    # Select brackets
    if filing_status == "married_filing_jointly":
        brackets = FEDERAL_BRACKETS_MFJ_2024
    else:
        brackets = FEDERAL_BRACKETS_SINGLE_2024

    # Calculate tax
    tax = 0
    prev_limit = 0
    bracket_breakdown = []

    for limit, rate in brackets:
        if taxable_income <= prev_limit:
            break

        taxable_in_bracket = min(taxable_income, limit) - prev_limit
        if taxable_in_bracket <= 0:
            break

        tax_in_bracket = taxable_in_bracket * rate
        tax += tax_in_bracket

        bracket_breakdown.append({
            "bracket_rate": rate,
            "income_in_bracket": round(taxable_in_bracket, 2),
            "tax_in_bracket": round(tax_in_bracket, 2),
        })

        prev_limit = limit

    # Effective and marginal rates
    effective_rate = tax / gross_income if gross_income > 0 else 0
    marginal_rate = bracket_breakdown[-1]["bracket_rate"] if bracket_breakdown else 0

    return {
        "gross_income": gross_income,
        "filing_status": filing_status,
        "tax_year": tax_year,
        "deduction_type": deduction_type,
        "deduction_amount": deduction_amount,
        "taxable_income": round(taxable_income, 2),
        "federal_tax": round(tax, 2),
        "effective_rate": round(effective_rate, 4),
        "effective_rate_formatted": format_percent(effective_rate),
        "marginal_rate": marginal_rate,
        "marginal_rate_formatted": format_percent(marginal_rate),
        "bracket_breakdown": bracket_breakdown,
    }


def capital_gains_tax(
    gain_amount: float,
    ordinary_income: float,
    holding_period: str = "long",
    filing_status: str = "single"
) -> Dict[str, Any]:
    """
    Calculate capital gains tax.

    Args:
        gain_amount: Capital gain amount
        ordinary_income: Taxable ordinary income
        holding_period: 'long' (>1 year) or 'short' (<1 year)
        filing_status: Filing status

    Returns:
        Capital gains tax calculation
    """
    if holding_period == "short":
        # Short-term gains taxed as ordinary income
        # Calculate marginal rate from ordinary income
        combined_income = ordinary_income + gain_amount
        tax_without = federal_tax(ordinary_income, filing_status)["federal_tax"]
        tax_with = federal_tax(combined_income, filing_status)["federal_tax"]
        cap_gains_tax = tax_with - tax_without
        rate = cap_gains_tax / gain_amount if gain_amount > 0 else 0

        return {
            "gain_amount": gain_amount,
            "holding_period": "short",
            "tax_treatment": "ordinary_income",
            "capital_gains_tax": round(cap_gains_tax, 2),
            "effective_rate": round(rate, 4),
            "effective_rate_formatted": format_percent(rate),
            "note": "Short-term gains taxed at ordinary income rates",
        }

    # Long-term capital gains
    if filing_status == "married_filing_jointly":
        brackets = LTCG_BRACKETS_MFJ_2024
    else:
        brackets = LTCG_BRACKETS_SINGLE_2024

    # The LTCG rate depends on total taxable income
    total_income = ordinary_income + gain_amount

    # Find the LTCG rate
    for limit, rate in brackets:
        if total_income <= limit:
            break

    cap_gains_tax = gain_amount * rate

    # NIIT (3.8% for high earners)
    niit = 0
    niit_threshold = 200000 if filing_status == "single" else 250000
    if total_income > niit_threshold:
        niit = min(gain_amount, total_income - niit_threshold) * 0.038

    total_tax = cap_gains_tax + niit

    return {
        "gain_amount": gain_amount,
        "holding_period": "long",
        "ordinary_income": ordinary_income,
        "total_income": total_income,
        "ltcg_rate": rate,
        "ltcg_rate_formatted": format_percent(rate),
        "capital_gains_tax": round(cap_gains_tax, 2),
        "niit": round(niit, 2),
        "total_tax": round(total_tax, 2),
        "effective_rate": round(total_tax / gain_amount if gain_amount > 0 else 0, 4),
    }


def tax_summary(
    gross_income: float,
    filing_status: str = "single",
    capital_gains: float = 0,
    holding_period: str = "long",
    deductions: float = None,
    state_rate: float = 0,
    local_rate: float = 0,
    fica_rate: float = 0.0765
) -> Dict[str, Any]:
    """
    Generate comprehensive tax summary.

    Args:
        gross_income: Gross W-2/ordinary income
        filing_status: Filing status
        capital_gains: Capital gains amount
        holding_period: Capital gains holding period
        deductions: Itemized deductions
        state_rate: State income tax rate
        local_rate: Local income tax rate
        fica_rate: FICA rate (default 7.65%)

    Returns:
        Complete tax summary
    """
    # Federal income tax
    fed = federal_tax(gross_income, filing_status, deductions)

    # Capital gains tax
    if capital_gains > 0:
        cap = capital_gains_tax(capital_gains, fed["taxable_income"], holding_period, filing_status)
        cap_gains_tax = cap["total_tax"]
    else:
        cap_gains_tax = 0

    # State and local
    state_tax = gross_income * state_rate
    local_tax = gross_income * local_rate

    # FICA (on earned income up to Social Security wage base)
    ss_wage_base = 168600  # 2024
    ss_tax = min(gross_income, ss_wage_base) * 0.062
    medicare_tax = gross_income * 0.0145

    # Additional Medicare tax
    additional_medicare = 0
    medicare_threshold = 200000 if filing_status == "single" else 250000
    if gross_income > medicare_threshold:
        additional_medicare = (gross_income - medicare_threshold) * 0.009

    fica_total = ss_tax + medicare_tax + additional_medicare

    # Totals
    total_income = gross_income + capital_gains
    total_tax = fed["federal_tax"] + cap_gains_tax + state_tax + local_tax + fica_total
    take_home = total_income - total_tax

    return {
        "gross_income": gross_income,
        "capital_gains": capital_gains,
        "total_income": total_income,
        "filing_status": filing_status,
        "federal_tax": fed["federal_tax"],
        "capital_gains_tax": round(cap_gains_tax, 2),
        "state_tax": round(state_tax, 2),
        "local_tax": round(local_tax, 2),
        "social_security_tax": round(ss_tax, 2),
        "medicare_tax": round(medicare_tax + additional_medicare, 2),
        "fica_total": round(fica_total, 2),
        "total_tax": round(total_tax, 2),
        "take_home": round(take_home, 2),
        "overall_effective_rate": round(total_tax / total_income if total_income > 0 else 0, 4),
        "overall_effective_formatted": format_percent(total_tax / total_income if total_income > 0 else 0),
        "marginal_rate": fed["marginal_rate"],
    }


if __name__ == "__main__":
    print("Testing Tax Estimation Tools")
    print("=" * 60)

    summary = tax_summary(
        gross_income=150000,
        filing_status="single",
        capital_gains=50000,
        holding_period="long",
        state_rate=0.05,
    )

    print(f"Gross Income: {format_currency(summary['gross_income'])}")
    print(f"Capital Gains: {format_currency(summary['capital_gains'])}")
    print(f"Total Income: {format_currency(summary['total_income'])}")
    print(f"\nTax Breakdown:")
    print(f"  Federal Tax: {format_currency(summary['federal_tax'])}")
    print(f"  Capital Gains Tax: {format_currency(summary['capital_gains_tax'])}")
    print(f"  State Tax: {format_currency(summary['state_tax'])}")
    print(f"  FICA: {format_currency(summary['fica_total'])}")
    print(f"  Total Tax: {format_currency(summary['total_tax'])}")
    print(f"\nTake Home: {format_currency(summary['take_home'])}")
    print(f"Effective Rate: {summary['overall_effective_formatted']}")
