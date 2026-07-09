"""
apollo_memory.context — Build a structured [User Profile] prompt block.

Injected near the top of the Apollo prompt when profile data exists,
so the model has grounded user context for every response.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


_FIELD_LABELS: Dict[str, str] = {
    "income":                 "Income",
    "income_frequency":       "Income frequency",
    "tax_filing_status":      "Filing status",
    "state":                  "State",
    "age":                    "Age",
    "risk_tolerance":         "Risk tolerance",
    "investment_horizon_yrs": "Investment horizon",
    "retirement_age_target":  "Retirement target age",
    "monthly_expenses":       "Monthly expenses",
    "emergency_fund_months":  "Emergency fund",
    "tax_bracket":            "Est. marginal bracket",
    "goals":                  "Goals",
    "debts":                  "Debts",
    "assets":                 "Assets",
}


def _fmt_currency(val: float) -> str:
    return f"${val:,.0f}"


def _fmt_debt(debt: Dict) -> str:
    parts = [debt.get("type", "debt").title()]
    if "balance" in debt:
        parts.append(f"${debt['balance']:,.0f}")
    if "rate" in debt:
        parts.append(f"@ {debt['rate']*100:.1f}%")
    if "monthly_payment" in debt:
        parts.append(f"(${debt['monthly_payment']:,.0f}/mo)")
    return " ".join(parts)


def _fmt_asset(asset: Dict) -> str:
    parts = [asset.get("type", "asset").title()]
    if "value" in asset:
        parts.append(f"~${asset['value']:,.0f}")
    if "description" in asset:
        parts.append(f"({asset['description']})")
    return " ".join(parts)


def build_profile_block(profile: Dict[str, Any]) -> str:
    """
    Convert a profile dict into a compact text block for prompt injection.
    Returns empty string if profile is empty.

    Example output:
        [User Profile]
        Age: 35 | State: CA | Filing status: single
        Income: $85,000/year | Est. marginal bracket: 22%
        Risk tolerance: moderate | Retirement target age: 62
        Emergency fund: 4 months | Monthly expenses: $4,200
        Goals: retire early, buy a home
        Debts: Mortgage $320,000 @ 6.5%
    """
    if not profile:
        return ""

    lines = ["[User Profile]"]

    # --- Row 1: demographics
    row1 = []
    if "age" in profile:
        row1.append(f"Age: {profile['age']}")
    if "state" in profile:
        row1.append(f"State: {profile['state']}")
    if "tax_filing_status" in profile:
        row1.append(f"Filing: {profile['tax_filing_status']}")
    if row1:
        lines.append(" | ".join(row1))

    # --- Row 2: income / bracket
    row2 = []
    if "income" in profile:
        freq = profile.get("income_frequency", "annual")
        row2.append(f"Income: {_fmt_currency(profile['income'])}/{freq[:2]}")
    if "tax_bracket" in profile:
        row2.append(f"Bracket: {int(profile['tax_bracket']*100)}%")
    if row2:
        lines.append(" | ".join(row2))

    # --- Row 3: risk / horizon / retirement
    row3 = []
    if "risk_tolerance" in profile:
        row3.append(f"Risk: {profile['risk_tolerance']}")
    if "investment_horizon_yrs" in profile:
        row3.append(f"Horizon: {profile['investment_horizon_yrs']}yr")
    if "retirement_age_target" in profile:
        row3.append(f"Retire @: {profile['retirement_age_target']}")
    if row3:
        lines.append(" | ".join(row3))

    # --- Row 4: savings / expenses
    row4 = []
    if "emergency_fund_months" in profile:
        row4.append(f"Emergency fund: {profile['emergency_fund_months']} mo")
    if "monthly_expenses" in profile:
        row4.append(f"Monthly expenses: {_fmt_currency(profile['monthly_expenses'])}")
    if row4:
        lines.append(" | ".join(row4))

    # --- Goals
    goals = profile.get("goals")
    if goals:
        if isinstance(goals, list):
            lines.append(f"Goals: {', '.join(goals)}")
        else:
            lines.append(f"Goals: {goals}")

    # --- Debts
    debts = profile.get("debts")
    if debts:
        if isinstance(debts, list):
            debt_strs = [_fmt_debt(d) if isinstance(d, dict) else str(d) for d in debts[:4]]
            lines.append(f"Debts: {' | '.join(debt_strs)}")

    # --- Assets
    assets = profile.get("assets")
    if assets:
        if isinstance(assets, list):
            asset_strs = [_fmt_asset(a) if isinstance(a, dict) else str(a) for a in assets[:4]]
            lines.append(f"Assets: {' | '.join(asset_strs)}")

    return "\n".join(lines)


def build_profile_block_from_store(memory_store: Any) -> str:
    """Convenience wrapper — pulls profile from MemoryStore and formats it."""
    profile = memory_store.get_full_profile()
    return build_profile_block(profile)
