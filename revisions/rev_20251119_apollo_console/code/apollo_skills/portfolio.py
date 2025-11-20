"""
portfolio.py - Portfolio analysis skill for Apollo.

Provides portfolio optimization, diversification analysis, and rebalancing.
"""

import logging
from typing import Dict, Any, List, Optional
import math

logger = logging.getLogger(__name__)


class PortfolioSkill:
    """
    Portfolio analysis and optimization skill.
    """

    def __init__(self):
        """Initialize the portfolio skill."""
        self.name = "portfolio"
        self.description = "Portfolio optimization, diversification, and rebalancing"

    def analyze(
        self,
        query: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Analyze a portfolio-related query.

        Args:
            query: User query
            context: RAG context
            user_data: User portfolio data

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

        # Determine what type of analysis to perform
        query_lower = query.lower()

        if "diversif" in query_lower:
            result["analysis"]["diversification"] = self.analyze_diversification(user_data)

        if "rebalanc" in query_lower:
            result["analysis"]["rebalancing"] = self.suggest_rebalancing(user_data)

        if "optim" in query_lower:
            result["analysis"]["optimization"] = self.optimize_allocation(user_data)

        if "allocat" in query_lower:
            result["analysis"]["allocation"] = self.analyze_allocation(user_data)

        # Add general recommendations
        result["recommendations"] = self._generate_recommendations(result["analysis"])

        return result

    def analyze_diversification(self, portfolio: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze portfolio diversification.

        Args:
            portfolio: Portfolio holdings

        Returns:
            Diversification analysis
        """
        holdings = portfolio.get("holdings", {})

        if not holdings:
            return {
                "score": 0,
                "assessment": "No holdings data provided",
                "suggestions": ["Provide portfolio holdings for analysis"]
            }

        # Calculate basic diversification metrics
        total_value = sum(holdings.values())
        num_holdings = len(holdings)

        # Concentration analysis
        concentrations = {k: v / total_value for k, v in holdings.items()}
        max_concentration = max(concentrations.values()) if concentrations else 0

        # Herfindahl-Hirschman Index (HHI)
        hhi = sum(c ** 2 for c in concentrations.values())

        # Diversification score (0-100)
        if hhi > 0:
            effective_n = 1 / hhi
            div_score = min(100, (effective_n / max(num_holdings, 1)) * 100)
        else:
            div_score = 0

        # Assessment
        if div_score >= 80:
            assessment = "Well diversified"
        elif div_score >= 60:
            assessment = "Moderately diversified"
        elif div_score >= 40:
            assessment = "Somewhat concentrated"
        else:
            assessment = "Highly concentrated"

        return {
            "score": round(div_score, 1),
            "assessment": assessment,
            "num_holdings": num_holdings,
            "max_concentration": round(max_concentration * 100, 1),
            "hhi": round(hhi, 4),
            "effective_positions": round(effective_n if hhi > 0 else 0, 1)
        }

    def suggest_rebalancing(self, portfolio: Dict[str, Any]) -> Dict[str, Any]:
        """
        Suggest portfolio rebalancing actions.

        Args:
            portfolio: Current portfolio with target allocations

        Returns:
            Rebalancing suggestions
        """
        current = portfolio.get("current_holdings", {})
        target = portfolio.get("target_allocation", {})

        if not current or not target:
            return {
                "actions": [],
                "note": "Provide current_holdings and target_allocation for rebalancing"
            }

        total_value = sum(current.values())
        actions = []

        for asset, target_pct in target.items():
            current_value = current.get(asset, 0)
            current_pct = (current_value / total_value * 100) if total_value > 0 else 0
            target_value = total_value * (target_pct / 100)

            diff_pct = current_pct - target_pct
            diff_value = current_value - target_value

            if abs(diff_pct) > 1:  # Only suggest if >1% off target
                action = "sell" if diff_value > 0 else "buy"
                actions.append({
                    "asset": asset,
                    "action": action,
                    "amount": abs(round(diff_value, 2)),
                    "current_pct": round(current_pct, 1),
                    "target_pct": target_pct,
                    "deviation": round(diff_pct, 1)
                })

        # Sort by absolute deviation
        actions.sort(key=lambda x: abs(x["deviation"]), reverse=True)

        return {
            "actions": actions,
            "total_portfolio_value": round(total_value, 2)
        }

    def optimize_allocation(self, portfolio: Dict[str, Any]) -> Dict[str, Any]:
        """
        Suggest optimized allocation based on risk tolerance.

        Args:
            portfolio: Portfolio with risk profile

        Returns:
            Optimized allocation suggestion
        """
        risk_tolerance = portfolio.get("risk_tolerance", "moderate")
        age = portfolio.get("age", 40)

        # Classic age-based allocation
        stock_pct = max(20, min(90, 100 - age))
        bond_pct = 100 - stock_pct

        # Adjust for risk tolerance
        if risk_tolerance == "aggressive":
            stock_pct = min(95, stock_pct + 15)
            bond_pct = 100 - stock_pct
        elif risk_tolerance == "conservative":
            stock_pct = max(20, stock_pct - 15)
            bond_pct = 100 - stock_pct

        return {
            "suggested_allocation": {
                "stocks": stock_pct,
                "bonds": bond_pct
            },
            "basis": f"Age-based rule with {risk_tolerance} risk adjustment",
            "rationale": f"At age {age} with {risk_tolerance} risk tolerance"
        }

    def analyze_allocation(self, portfolio: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze current allocation by asset class.

        Args:
            portfolio: Portfolio holdings

        Returns:
            Allocation breakdown
        """
        holdings = portfolio.get("holdings", {})
        asset_classes = portfolio.get("asset_classes", {})

        if not holdings:
            return {"note": "No holdings provided"}

        total = sum(holdings.values())

        # Group by asset class
        class_totals = {}
        for asset, value in holdings.items():
            asset_class = asset_classes.get(asset, "other")
            class_totals[asset_class] = class_totals.get(asset_class, 0) + value

        # Calculate percentages
        allocation = {
            k: round(v / total * 100, 1)
            for k, v in class_totals.items()
        }

        return {
            "allocation_pct": allocation,
            "total_value": round(total, 2)
        }

    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on analysis."""
        recommendations = []

        # Diversification recommendations
        div = analysis.get("diversification", {})
        if div.get("score", 100) < 60:
            recommendations.append("Consider adding more positions to improve diversification")
        if div.get("max_concentration", 0) > 25:
            recommendations.append("Reduce largest position to decrease concentration risk")

        # Rebalancing recommendations
        rebal = analysis.get("rebalancing", {})
        if rebal.get("actions"):
            recommendations.append("Portfolio needs rebalancing to match target allocation")

        return recommendations
