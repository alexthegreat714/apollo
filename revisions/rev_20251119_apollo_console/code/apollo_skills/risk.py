"""
risk.py - Risk analysis skill for Apollo.

Provides Sharpe ratio, volatility, and VaR calculations.
"""

import logging
from typing import Dict, Any, List, Optional
import math

logger = logging.getLogger(__name__)


class RiskSkill:
    """
    Risk analysis and metrics skill.
    """

    def __init__(self):
        """Initialize the risk skill."""
        self.name = "risk"
        self.description = "Sharpe ratio, volatility, VaR analysis"

    def analyze(
        self,
        query: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Analyze a risk-related query.

        Args:
            query: User query
            context: RAG context
            user_data: User portfolio/return data

        Returns:
            Analysis result
        """
        user_data = user_data or {}
        result = {
            "skill": self.name,
            "analysis": {},
            "recommendations": [],
            "context_used": bool(context)
        }

        query_lower = query.lower()

        if "sharpe" in query_lower:
            result["analysis"]["sharpe_ratio"] = self.calculate_sharpe_ratio(user_data)

        if "volatil" in query_lower or "std" in query_lower:
            result["analysis"]["volatility"] = self.calculate_volatility(user_data)

        if "var" in query_lower or "value at risk" in query_lower:
            result["analysis"]["var"] = self.calculate_var(user_data)

        if "beta" in query_lower:
            result["analysis"]["beta"] = self.calculate_beta(user_data)

        if "drawdown" in query_lower:
            result["analysis"]["drawdown"] = self.calculate_max_drawdown(user_data)

        result["recommendations"] = self._generate_recommendations(result["analysis"])

        return result

    def calculate_sharpe_ratio(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate Sharpe ratio.

        Args:
            data: Return data

        Returns:
            Sharpe ratio analysis
        """
        returns = data.get("returns", [])
        risk_free_rate = data.get("risk_free_rate", 0.04)  # Annualized

        if not returns or len(returns) < 2:
            # Use provided values
            avg_return = data.get("annual_return", 0.08)
            std_dev = data.get("annual_volatility", 0.15)
        else:
            # Calculate from returns
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
            std_dev = math.sqrt(variance)

            # Annualize if monthly returns
            if data.get("frequency") == "monthly":
                avg_return = avg_return * 12
                std_dev = std_dev * math.sqrt(12)

        if std_dev == 0:
            sharpe = 0
        else:
            sharpe = (avg_return - risk_free_rate) / std_dev

        # Interpretation
        if sharpe >= 2:
            interpretation = "Excellent risk-adjusted returns"
        elif sharpe >= 1:
            interpretation = "Good risk-adjusted returns"
        elif sharpe >= 0.5:
            interpretation = "Acceptable risk-adjusted returns"
        elif sharpe >= 0:
            interpretation = "Marginal risk-adjusted returns"
        else:
            interpretation = "Negative excess returns"

        return {
            "sharpe_ratio": round(sharpe, 2),
            "excess_return": round(avg_return - risk_free_rate, 4),
            "volatility": round(std_dev, 4),
            "risk_free_rate": risk_free_rate,
            "interpretation": interpretation
        }

    def calculate_volatility(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate volatility metrics.

        Args:
            data: Return data

        Returns:
            Volatility analysis
        """
        returns = data.get("returns", [])

        if not returns or len(returns) < 2:
            return {
                "note": "Provide returns list for volatility calculation",
                "annual_volatility": data.get("annual_volatility", 0.15)
            }

        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)

        # Annualize
        freq = data.get("frequency", "monthly")
        if freq == "monthly":
            annual_vol = std_dev * math.sqrt(12)
        elif freq == "daily":
            annual_vol = std_dev * math.sqrt(252)
        else:
            annual_vol = std_dev

        # Risk categorization
        if annual_vol < 0.10:
            risk_level = "Low"
        elif annual_vol < 0.20:
            risk_level = "Moderate"
        elif annual_vol < 0.30:
            risk_level = "High"
        else:
            risk_level = "Very High"

        return {
            "period_volatility": round(std_dev, 4),
            "annual_volatility": round(annual_vol, 4),
            "risk_level": risk_level,
            "frequency": freq,
            "data_points": len(returns)
        }

    def calculate_var(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate Value at Risk.

        Args:
            data: Portfolio and return data

        Returns:
            VaR analysis
        """
        portfolio_value = data.get("portfolio_value", 100000)
        returns = data.get("returns", [])
        confidence = data.get("confidence", 0.95)
        horizon_days = data.get("horizon_days", 1)

        if not returns or len(returns) < 10:
            # Use parametric method with given volatility
            annual_vol = data.get("annual_volatility", 0.15)
            daily_vol = annual_vol / math.sqrt(252)
        else:
            # Calculate from returns
            avg = sum(returns) / len(returns)
            variance = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
            daily_vol = math.sqrt(variance)

            if data.get("frequency") == "monthly":
                daily_vol = daily_vol / math.sqrt(21)

        # Z-score for confidence level
        if confidence == 0.99:
            z_score = 2.326
        elif confidence == 0.95:
            z_score = 1.645
        else:
            z_score = 1.282  # 90%

        # Scale for horizon
        horizon_vol = daily_vol * math.sqrt(horizon_days)

        # VaR calculation
        var_pct = z_score * horizon_vol
        var_amount = portfolio_value * var_pct

        return {
            "var_percentage": round(var_pct * 100, 2),
            "var_amount": round(var_amount, 2),
            "portfolio_value": portfolio_value,
            "confidence_level": confidence,
            "horizon_days": horizon_days,
            "interpretation": f"{confidence:.0%} confidence you won't lose more than ${var_amount:,.0f} in {horizon_days} day(s)"
        }

    def calculate_beta(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate portfolio beta.

        Args:
            data: Portfolio and market returns

        Returns:
            Beta analysis
        """
        portfolio_returns = data.get("portfolio_returns", [])
        market_returns = data.get("market_returns", [])

        if not portfolio_returns or not market_returns:
            beta = data.get("beta", 1.0)
            return {
                "beta": beta,
                "note": "Using provided beta value",
                "interpretation": self._interpret_beta(beta)
            }

        if len(portfolio_returns) != len(market_returns):
            return {"error": "Portfolio and market returns must have same length"}

        n = len(portfolio_returns)
        if n < 2:
            return {"error": "Need at least 2 data points"}

        # Calculate covariance and market variance
        port_avg = sum(portfolio_returns) / n
        mkt_avg = sum(market_returns) / n

        covariance = sum(
            (portfolio_returns[i] - port_avg) * (market_returns[i] - mkt_avg)
            for i in range(n)
        ) / (n - 1)

        mkt_variance = sum((r - mkt_avg) ** 2 for r in market_returns) / (n - 1)

        if mkt_variance == 0:
            return {"error": "Market variance is zero"}

        beta = covariance / mkt_variance

        return {
            "beta": round(beta, 2),
            "interpretation": self._interpret_beta(beta),
            "data_points": n
        }

    def _interpret_beta(self, beta: float) -> str:
        """Interpret beta value."""
        if beta > 1.5:
            return "Highly aggressive - significantly more volatile than market"
        elif beta > 1.1:
            return "Aggressive - more volatile than market"
        elif beta >= 0.9:
            return "Neutral - moves with market"
        elif beta >= 0.5:
            return "Defensive - less volatile than market"
        else:
            return "Very defensive - minimal market correlation"

    def calculate_max_drawdown(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate maximum drawdown.

        Args:
            data: Portfolio value history

        Returns:
            Drawdown analysis
        """
        values = data.get("portfolio_values", [])

        if not values or len(values) < 2:
            return {"note": "Provide portfolio_values list for drawdown calculation"}

        max_drawdown = 0
        peak = values[0]
        trough_idx = 0
        peak_idx = 0

        for i, value in enumerate(values):
            if value > peak:
                peak = value
                peak_idx = i

            drawdown = (peak - value) / peak

            if drawdown > max_drawdown:
                max_drawdown = drawdown
                trough_idx = i

        # Recovery (if applicable)
        recovered = False
        if trough_idx < len(values) - 1:
            for i in range(trough_idx + 1, len(values)):
                if values[i] >= peak:
                    recovered = True
                    break

        return {
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "peak_value": round(peak, 2),
            "trough_value": round(values[trough_idx], 2),
            "recovered": recovered,
            "data_points": len(values)
        }

    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate risk recommendations."""
        recommendations = []

        # Sharpe ratio recommendations
        sr = analysis.get("sharpe_ratio", {})
        if sr.get("sharpe_ratio", 1) < 0.5:
            recommendations.append("Consider strategies with better risk-adjusted returns")

        # Volatility recommendations
        vol = analysis.get("volatility", {})
        if vol.get("annual_volatility", 0) > 0.25:
            recommendations.append("High volatility detected - consider hedging strategies")

        # VaR recommendations
        var = analysis.get("var", {})
        var_pct = var.get("var_percentage", 0)
        if var_pct > 5:
            recommendations.append(f"VaR of {var_pct:.1f}% suggests significant downside risk")

        # Beta recommendations
        beta = analysis.get("beta", {})
        if beta.get("beta", 1) > 1.3:
            recommendations.append("High beta portfolio - will amplify market movements")

        return recommendations
