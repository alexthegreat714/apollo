"""
util.py - Utility Functions for Apollo Financial Tools
"""

from typing import List, Union
import math


def validate_positive(value: float, name: str) -> None:
    """Validate that a value is positive."""
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def validate_non_negative(value: float, name: str) -> None:
    """Validate that a value is non-negative."""
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


def validate_rate(rate: float, name: str) -> None:
    """Validate that a rate is reasonable (0-100%)."""
    if rate < 0 or rate > 1:
        raise ValueError(f"{name} must be between 0 and 1, got {rate}")


def format_currency(amount: float, decimals: int = 2) -> str:
    """Format amount as currency string."""
    return f"${amount:,.{decimals}f}"


def format_percent(rate: float, decimals: int = 2) -> str:
    """Format rate as percentage string."""
    return f"{rate * 100:.{decimals}f}%"


def annualize_monthly_rate(monthly_rate: float) -> float:
    """Convert monthly rate to annual rate."""
    return (1 + monthly_rate) ** 12 - 1


def monthly_from_annual_rate(annual_rate: float) -> float:
    """Convert annual rate to monthly rate."""
    return annual_rate / 12


def periods_per_year(frequency: str) -> int:
    """Get number of periods per year for a frequency."""
    frequencies = {
        "annual": 1,
        "semi-annual": 2,
        "quarterly": 4,
        "monthly": 12,
        "weekly": 52,
        "daily": 365,
    }
    return frequencies.get(frequency.lower(), 12)


def calculate_mean(values: List[float]) -> float:
    """Calculate arithmetic mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def calculate_std(values: List[float]) -> float:
    """Calculate standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = calculate_mean(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def calculate_covariance(x: List[float], y: List[float]) -> float:
    """Calculate covariance between two series."""
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mean_x = calculate_mean(x)
    mean_y = calculate_mean(y)
    return sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / (len(x) - 1)


def npv(rate: float, cash_flows: List[float]) -> float:
    """Calculate Net Present Value."""
    return sum(cf / (1 + rate) ** i for i, cf in enumerate(cash_flows))


def irr(cash_flows: List[float], guess: float = 0.1, tolerance: float = 1e-6, max_iter: int = 100) -> float:
    """
    Calculate Internal Rate of Return using Newton's method.

    Args:
        cash_flows: List of cash flows (initial investment should be negative)
        guess: Initial guess for IRR
        tolerance: Convergence tolerance
        max_iter: Maximum iterations

    Returns:
        IRR as decimal (e.g., 0.10 for 10%)
    """
    rate = guess

    for _ in range(max_iter):
        # NPV at current rate
        npv_val = sum(cf / (1 + rate) ** i for i, cf in enumerate(cash_flows))

        # Derivative of NPV
        dnpv = sum(-i * cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cash_flows))

        if abs(dnpv) < 1e-10:
            break

        # Newton's method update
        new_rate = rate - npv_val / dnpv

        if abs(new_rate - rate) < tolerance:
            return new_rate

        rate = new_rate

    return rate
