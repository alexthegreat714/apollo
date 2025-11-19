"""
rebalance.py - Portfolio Rebalancing Tools for Apollo

Portfolio rebalancing and drift analysis calculations.
"""

from typing import Dict, Any, List
from .util import format_currency, format_percent


def portfolio_rebalance(
    current_holdings: Dict[str, float],
    target_allocation: Dict[str, float],
    contribution: float = 0
) -> Dict[str, Any]:
    """
    Calculate trades needed to rebalance portfolio.

    Args:
        current_holdings: Dict of asset -> current value
        target_allocation: Dict of asset -> target percentage (decimal)
        contribution: New cash to add to portfolio

    Returns:
        Rebalancing recommendation
    """
    # Validate target allocation sums to ~100%
    total_target = sum(target_allocation.values())
    if abs(total_target - 1.0) > 0.01:
        return {"error": f"Target allocation must sum to 100%, got {total_target * 100}%"}

    # Current total and new total
    current_total = sum(current_holdings.values())
    new_total = current_total + contribution

    if new_total <= 0:
        return {"error": "Total portfolio value must be positive"}

    # Calculate current allocation
    current_allocation = {}
    for asset, value in current_holdings.items():
        current_allocation[asset] = value / current_total if current_total > 0 else 0

    # Calculate target values and trades
    trades = []
    total_buys = 0
    total_sells = 0

    for asset in set(current_holdings.keys()) | set(target_allocation.keys()):
        current_value = current_holdings.get(asset, 0)
        target_pct = target_allocation.get(asset, 0)
        target_value = new_total * target_pct

        trade_amount = target_value - current_value
        current_pct = current_allocation.get(asset, 0)

        if abs(trade_amount) > 0.01:
            action = "buy" if trade_amount > 0 else "sell"
            if trade_amount > 0:
                total_buys += trade_amount
            else:
                total_sells += abs(trade_amount)

            trades.append({
                "asset": asset,
                "action": action,
                "amount": round(abs(trade_amount), 2),
                "current_value": round(current_value, 2),
                "current_allocation": round(current_pct * 100, 2),
                "target_value": round(target_value, 2),
                "target_allocation": round(target_pct * 100, 2),
            })

    # Sort trades: sells first, then buys
    trades.sort(key=lambda x: (0 if x["action"] == "sell" else 1, -x["amount"]))

    return {
        "current_total": round(current_total, 2),
        "contribution": contribution,
        "new_total": round(new_total, 2),
        "total_buys": round(total_buys, 2),
        "total_sells": round(total_sells, 2),
        "net_trades": round(total_buys - total_sells, 2),
        "trades": trades,
        "trade_count": len([t for t in trades if t["amount"] > 1]),
    }


def drift_analysis(
    current_holdings: Dict[str, float],
    target_allocation: Dict[str, float],
    rebalance_threshold: float = 0.05
) -> Dict[str, Any]:
    """
    Analyze portfolio drift from target allocation.

    Args:
        current_holdings: Dict of asset -> current value
        target_allocation: Dict of asset -> target percentage
        rebalance_threshold: Drift threshold to trigger rebalance (decimal)

    Returns:
        Drift analysis
    """
    current_total = sum(current_holdings.values())

    if current_total <= 0:
        return {"error": "Total portfolio value must be positive"}

    # Calculate drift for each asset
    drift_details = []
    max_drift = 0
    total_drift = 0

    for asset in set(current_holdings.keys()) | set(target_allocation.keys()):
        current_value = current_holdings.get(asset, 0)
        current_pct = current_value / current_total
        target_pct = target_allocation.get(asset, 0)

        drift = current_pct - target_pct
        abs_drift = abs(drift)

        max_drift = max(max_drift, abs_drift)
        total_drift += abs_drift

        drift_details.append({
            "asset": asset,
            "current_value": round(current_value, 2),
            "current_allocation": round(current_pct * 100, 2),
            "target_allocation": round(target_pct * 100, 2),
            "drift": round(drift * 100, 2),
            "drift_absolute": round(abs_drift * 100, 2),
            "over_threshold": abs_drift > rebalance_threshold,
        })

    # Sort by absolute drift
    drift_details.sort(key=lambda x: -x["drift_absolute"])

    # Rebalance recommendation
    needs_rebalance = max_drift > rebalance_threshold
    assets_over_threshold = sum(1 for d in drift_details if d["over_threshold"])

    return {
        "current_total": round(current_total, 2),
        "max_drift": round(max_drift * 100, 2),
        "total_drift": round(total_drift * 100, 2),
        "rebalance_threshold": round(rebalance_threshold * 100, 2),
        "needs_rebalance": needs_rebalance,
        "assets_over_threshold": assets_over_threshold,
        "drift_details": drift_details,
        "recommendation": "Rebalance recommended" if needs_rebalance else "Portfolio within tolerance",
    }


def optimal_rebalance_frequency(
    annual_return: float = 0.08,
    annual_volatility: float = 0.15,
    transaction_cost: float = 0.001,
    tax_rate: float = 0.15
) -> Dict[str, Any]:
    """
    Estimate optimal rebalancing frequency.

    Args:
        annual_return: Expected annual return
        annual_volatility: Annual portfolio volatility
        transaction_cost: Cost per trade (decimal)
        tax_rate: Tax rate on gains

    Returns:
        Rebalancing frequency recommendation
    """
    # Simplified model based on volatility and costs
    # Higher volatility = more frequent rebalancing
    # Higher costs = less frequent rebalancing

    drift_rate = annual_volatility * 0.5  # Expected drift per year
    cost_impact = transaction_cost + (tax_rate * annual_return * 0.25)

    # Optimal frequency: balance drift reduction vs costs
    if cost_impact > 0.02:
        frequency = "annually"
        interval_months = 12
    elif cost_impact > 0.01:
        frequency = "semi-annually"
        interval_months = 6
    elif drift_rate > 0.10:
        frequency = "quarterly"
        interval_months = 3
    else:
        frequency = "semi-annually"
        interval_months = 6

    return {
        "recommended_frequency": frequency,
        "interval_months": interval_months,
        "annual_volatility": annual_volatility,
        "estimated_drift_per_interval": round(drift_rate * (interval_months / 12), 3),
        "cost_per_rebalance": round(cost_impact, 4),
        "annual_rebalances": round(12 / interval_months, 1),
        "notes": [
            "Consider tax-loss harvesting opportunities",
            "Use new contributions to rebalance when possible",
            "Threshold-based rebalancing may outperform calendar-based",
        ],
    }


if __name__ == "__main__":
    print("Testing Portfolio Rebalancing Tools")
    print("=" * 60)

    current = {
        "US Stocks": 65000,
        "International Stocks": 20000,
        "Bonds": 12000,
        "Cash": 3000,
    }

    target = {
        "US Stocks": 0.60,
        "International Stocks": 0.25,
        "Bonds": 0.10,
        "Cash": 0.05,
    }

    result = portfolio_rebalance(current, target, contribution=5000)

    print(f"Current Total: {format_currency(result['current_total'])}")
    print(f"Contribution: {format_currency(result['contribution'])}")
    print(f"New Total: {format_currency(result['new_total'])}")
    print(f"\nTrades Needed:")
    for trade in result['trades']:
        print(f"  {trade['action'].upper()} {trade['asset']}: {format_currency(trade['amount'])}")
        print(f"    Current: {trade['current_allocation']}% -> Target: {trade['target_allocation']}%")

    print("\n" + "=" * 60)
    print("Drift Analysis")

    drift = drift_analysis(current, target)
    print(f"Max Drift: {drift['max_drift']}%")
    print(f"Needs Rebalance: {drift['needs_rebalance']}")
    print(f"Recommendation: {drift['recommendation']}")
