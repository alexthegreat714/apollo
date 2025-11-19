"""
risk.py - Risk Analysis Tools for Apollo

Sharpe ratio, CAPM, beta, and risk scoring calculations.
"""

from typing import Dict, Any, List
import math
from .util import (
    validate_positive,
    format_percent,
    calculate_mean,
    calculate_std,
    calculate_covariance,
)


def sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.04,
    annualize: bool = True
) -> Dict[str, Any]:
    """
    Calculate Sharpe Ratio for risk-adjusted returns.

    Args:
        returns: List of periodic returns (decimal)
        risk_free_rate: Annual risk-free rate
        annualize: Whether to annualize the ratio

    Returns:
        Sharpe ratio analysis
    """
    if len(returns) < 2:
        return {"error": "Need at least 2 return periods"}

    mean_return = calculate_mean(returns)
    std_return = calculate_std(returns)

    if std_return == 0:
        return {"error": "Zero standard deviation - all returns identical"}

    # Convert annual risk-free to period rate (assuming monthly returns)
    periods_per_year = 12
    period_risk_free = risk_free_rate / periods_per_year

    sharpe = (mean_return - period_risk_free) / std_return

    if annualize:
        sharpe = sharpe * math.sqrt(periods_per_year)
        annualized_return = mean_return * periods_per_year
        annualized_std = std_return * math.sqrt(periods_per_year)
    else:
        annualized_return = mean_return
        annualized_std = std_return

    # Interpret Sharpe ratio
    if sharpe > 2:
        interpretation = "Excellent risk-adjusted returns"
    elif sharpe > 1:
        interpretation = "Good risk-adjusted returns"
    elif sharpe > 0:
        interpretation = "Positive but below-average risk-adjusted returns"
    else:
        interpretation = "Poor risk-adjusted returns"

    return {
        "sharpe_ratio": round(sharpe, 3),
        "mean_return": round(mean_return, 4),
        "std_return": round(std_return, 4),
        "annualized_return": round(annualized_return, 4),
        "annualized_std": round(annualized_std, 4),
        "risk_free_rate": risk_free_rate,
        "periods_analyzed": len(returns),
        "interpretation": interpretation,
    }


def capm_return(
    risk_free_rate: float,
    beta: float,
    market_return: float
) -> Dict[str, Any]:
    """
    Calculate expected return using Capital Asset Pricing Model (CAPM).

    Args:
        risk_free_rate: Risk-free rate
        beta: Asset beta
        market_return: Expected market return

    Returns:
        CAPM analysis
    """
    # CAPM: E(R) = Rf + β(Rm - Rf)
    market_premium = market_return - risk_free_rate
    expected_return = risk_free_rate + beta * market_premium

    # Risk interpretation
    if beta > 1.5:
        risk_profile = "High risk - significantly more volatile than market"
    elif beta > 1:
        risk_profile = "Above-average risk - more volatile than market"
    elif beta > 0.8:
        risk_profile = "Average risk - similar to market"
    elif beta > 0:
        risk_profile = "Below-average risk - less volatile than market"
    else:
        risk_profile = "Negative beta - moves opposite to market"

    return {
        "expected_return": round(expected_return, 4),
        "expected_return_formatted": format_percent(expected_return),
        "risk_free_rate": risk_free_rate,
        "beta": beta,
        "market_return": market_return,
        "market_premium": round(market_premium, 4),
        "risk_premium": round(beta * market_premium, 4),
        "risk_profile": risk_profile,
    }


def beta_estimate(
    asset_returns: List[float],
    market_returns: List[float]
) -> Dict[str, Any]:
    """
    Estimate beta from historical returns.

    Args:
        asset_returns: List of asset returns
        market_returns: List of market returns (same periods)

    Returns:
        Beta estimation
    """
    if len(asset_returns) != len(market_returns):
        return {"error": "Asset and market return lists must be same length"}

    if len(asset_returns) < 3:
        return {"error": "Need at least 3 periods for beta calculation"}

    covariance = calculate_covariance(asset_returns, market_returns)
    market_variance = calculate_std(market_returns) ** 2

    if market_variance == 0:
        return {"error": "Market variance is zero"}

    beta = covariance / market_variance

    # Calculate R-squared (correlation squared)
    asset_std = calculate_std(asset_returns)
    market_std = calculate_std(market_returns)

    if asset_std > 0 and market_std > 0:
        correlation = covariance / (asset_std * market_std)
        r_squared = correlation ** 2
    else:
        r_squared = 0

    return {
        "beta": round(beta, 3),
        "covariance": round(covariance, 6),
        "market_variance": round(market_variance, 6),
        "r_squared": round(r_squared, 3),
        "periods": len(asset_returns),
        "asset_mean_return": round(calculate_mean(asset_returns), 4),
        "market_mean_return": round(calculate_mean(market_returns), 4),
    }


def risk_score(
    debt_to_income: float = None,
    savings_rate: float = None,
    emergency_fund_months: float = None,
    portfolio_concentration: float = None,
    age: int = None,
    stock_allocation: float = None
) -> Dict[str, Any]:
    """
    Calculate overall financial risk score.

    Args:
        debt_to_income: DTI ratio (decimal)
        savings_rate: Monthly savings rate (decimal)
        emergency_fund_months: Months of expenses in emergency fund
        portfolio_concentration: Largest holding as % of portfolio
        age: Investor age
        stock_allocation: Stock allocation (decimal)

    Returns:
        Risk score analysis (0-100, lower is better)
    """
    score = 0
    factors = []
    max_score = 0

    # DTI score (0-20)
    if debt_to_income is not None:
        max_score += 20
        if debt_to_income < 0.20:
            dti_score = 0
        elif debt_to_income < 0.36:
            dti_score = 10
        elif debt_to_income < 0.50:
            dti_score = 15
        else:
            dti_score = 20
        score += dti_score
        factors.append({
            "factor": "Debt-to-Income",
            "value": debt_to_income,
            "score": dti_score,
            "max": 20,
        })

    # Savings rate score (0-15)
    if savings_rate is not None:
        max_score += 15
        if savings_rate >= 0.20:
            sr_score = 0
        elif savings_rate >= 0.10:
            sr_score = 5
        elif savings_rate >= 0.05:
            sr_score = 10
        else:
            sr_score = 15
        score += sr_score
        factors.append({
            "factor": "Savings Rate",
            "value": savings_rate,
            "score": sr_score,
            "max": 15,
        })

    # Emergency fund score (0-20)
    if emergency_fund_months is not None:
        max_score += 20
        if emergency_fund_months >= 6:
            ef_score = 0
        elif emergency_fund_months >= 3:
            ef_score = 10
        elif emergency_fund_months >= 1:
            ef_score = 15
        else:
            ef_score = 20
        score += ef_score
        factors.append({
            "factor": "Emergency Fund",
            "value": emergency_fund_months,
            "score": ef_score,
            "max": 20,
        })

    # Concentration score (0-15)
    if portfolio_concentration is not None:
        max_score += 15
        if portfolio_concentration < 0.10:
            conc_score = 0
        elif portfolio_concentration < 0.25:
            conc_score = 5
        elif portfolio_concentration < 0.50:
            conc_score = 10
        else:
            conc_score = 15
        score += conc_score
        factors.append({
            "factor": "Portfolio Concentration",
            "value": portfolio_concentration,
            "score": conc_score,
            "max": 15,
        })

    # Age-appropriate allocation (0-15)
    if age is not None and stock_allocation is not None:
        max_score += 15
        # Rule of thumb: stocks = 110 - age
        recommended_stocks = max(0, min(1, (110 - age) / 100))
        deviation = abs(stock_allocation - recommended_stocks)

        if deviation < 0.10:
            alloc_score = 0
        elif deviation < 0.20:
            alloc_score = 5
        elif deviation < 0.30:
            alloc_score = 10
        else:
            alloc_score = 15
        score += alloc_score
        factors.append({
            "factor": "Age-Appropriate Allocation",
            "value": stock_allocation,
            "recommended": recommended_stocks,
            "score": alloc_score,
            "max": 15,
        })

    # Normalize to 0-100
    if max_score > 0:
        normalized_score = (score / max_score) * 100
    else:
        normalized_score = 0

    # Risk level
    if normalized_score < 20:
        level = "Low Risk"
    elif normalized_score < 40:
        level = "Moderate Risk"
    elif normalized_score < 60:
        level = "Elevated Risk"
    elif normalized_score < 80:
        level = "High Risk"
    else:
        level = "Very High Risk"

    return {
        "risk_score": round(normalized_score, 1),
        "risk_level": level,
        "raw_score": score,
        "max_possible_score": max_score,
        "factors": factors,
    }


def dti_ratio(
    monthly_debt: float,
    monthly_income: float
) -> Dict[str, Any]:
    """
    Calculate Debt-to-Income ratio.

    Args:
        monthly_debt: Total monthly debt payments
        monthly_income: Gross monthly income

    Returns:
        DTI analysis
    """
    validate_positive(monthly_income, "Monthly income")

    dti = monthly_debt / monthly_income

    if dti < 0.28:
        status = "Excellent - well within guidelines"
    elif dti < 0.36:
        status = "Good - within conventional mortgage guidelines"
    elif dti < 0.43:
        status = "Acceptable - at FHA maximum"
    elif dti < 0.50:
        status = "High - may have difficulty qualifying for loans"
    else:
        status = "Very High - debt burden is unsustainable"

    return {
        "dti_ratio": round(dti, 3),
        "dti_percent": round(dti * 100, 1),
        "monthly_debt": monthly_debt,
        "monthly_income": monthly_income,
        "status": status,
        "remaining_for_debt": round(monthly_income * 0.36 - monthly_debt, 2),
    }


if __name__ == "__main__":
    print("Testing Risk Analysis Tools")
    print("=" * 60)

    # Test Sharpe Ratio
    monthly_returns = [0.02, -0.01, 0.03, 0.01, -0.02, 0.04, 0.01, 0.02, -0.01, 0.03, 0.02, 0.01]
    sharpe = sharpe_ratio(monthly_returns)
    print(f"Sharpe Ratio: {sharpe['sharpe_ratio']}")
    print(f"Interpretation: {sharpe['interpretation']}")

    print("\n" + "=" * 60)
    print("CAPM Expected Return")
    capm = capm_return(risk_free_rate=0.04, beta=1.2, market_return=0.10)
    print(f"Expected Return: {capm['expected_return_formatted']}")
    print(f"Risk Profile: {capm['risk_profile']}")

    print("\n" + "=" * 60)
    print("Risk Score")
    score = risk_score(
        debt_to_income=0.32,
        savings_rate=0.15,
        emergency_fund_months=4,
        portfolio_concentration=0.15,
        age=35,
        stock_allocation=0.70
    )
    print(f"Risk Score: {score['risk_score']}/100")
    print(f"Risk Level: {score['risk_level']}")
