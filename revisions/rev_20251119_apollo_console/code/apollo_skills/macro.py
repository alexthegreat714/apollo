"""
macro.py - Macroeconomic analysis skill for Apollo.

Provides GDP forecasts, inflation simulation, and interest rate stress models.
"""

import logging
from typing import Dict, Any, List, Optional
import math

logger = logging.getLogger(__name__)


class MacroSkill:
    """
    Macroeconomic analysis and simulation skill.
    """

    def __init__(self):
        """Initialize the macro skill."""
        self.name = "macro"
        self.description = "GDP forecasts, inflation simulation, interest rate models"

    def analyze(
        self,
        query: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Analyze a macroeconomic query.

        Args:
            query: User query
            context: RAG context
            user_data: User scenario data

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

        if "gdp" in query_lower or "growth" in query_lower:
            result["analysis"]["gdp_forecast"] = self.forecast_gdp(user_data)

        if "inflat" in query_lower:
            result["analysis"]["inflation"] = self.simulate_inflation(user_data)

        if "interest" in query_lower or "rate" in query_lower:
            result["analysis"]["interest_rates"] = self.model_interest_rates(user_data)

        if "recession" in query_lower:
            result["analysis"]["recession_risk"] = self.assess_recession_risk(user_data)

        result["recommendations"] = self._generate_recommendations(result["analysis"])

        return result

    def forecast_gdp(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate GDP growth scenarios.

        Args:
            data: Economic indicators

        Returns:
            GDP forecast scenarios
        """
        # Default baseline assumptions
        current_gdp_growth = data.get("current_gdp_growth", 2.5)
        unemployment = data.get("unemployment", 4.0)
        inflation = data.get("inflation", 3.0)

        # Simple Okun's Law relationship
        # Every 1% above natural unemployment = -2% GDP
        natural_unemployment = 4.5
        unemployment_drag = (unemployment - natural_unemployment) * -2

        # Inflation impact (high inflation = policy tightening = lower growth)
        inflation_target = 2.0
        if inflation > inflation_target:
            inflation_drag = -(inflation - inflation_target) * 0.3
        else:
            inflation_drag = 0

        base_forecast = current_gdp_growth + unemployment_drag + inflation_drag

        scenarios = {
            "optimistic": {
                "growth": round(base_forecast + 1.0, 1),
                "assumptions": "Lower inflation, stable employment"
            },
            "baseline": {
                "growth": round(base_forecast, 1),
                "assumptions": "Current trends continue"
            },
            "pessimistic": {
                "growth": round(base_forecast - 1.5, 1),
                "assumptions": "Inflation persists, labor market weakens"
            }
        }

        return {
            "scenarios": scenarios,
            "inputs": {
                "current_gdp_growth": current_gdp_growth,
                "unemployment": unemployment,
                "inflation": inflation
            }
        }

    def simulate_inflation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulate inflation scenarios.

        Args:
            data: Inflation parameters

        Returns:
            Inflation simulation
        """
        current_cpi = data.get("current_cpi", 3.5)
        fed_rate = data.get("fed_rate", 5.25)
        money_supply_growth = data.get("m2_growth", 2.0)
        years = data.get("projection_years", 3)

        projections = []
        cpi = current_cpi

        for year in range(1, years + 1):
            # Simplified inflation model
            # Higher rates = lower inflation (with lag)
            rate_effect = -(fed_rate - 3.0) * 0.15

            # Money supply effect
            ms_effect = (money_supply_growth - 3.0) * 0.2

            # Mean reversion to 2%
            reversion = (2.0 - cpi) * 0.3

            cpi = max(0, cpi + rate_effect + ms_effect + reversion)

            projections.append({
                "year": year,
                "projected_cpi": round(cpi, 1)
            })

        return {
            "current_cpi": current_cpi,
            "projections": projections,
            "key_drivers": {
                "fed_rate_effect": "Contractionary" if fed_rate > 4 else "Neutral",
                "money_supply": "Stable" if abs(money_supply_growth - 3) < 2 else "Expanding"
            }
        }

    def model_interest_rates(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Model interest rate scenarios and impacts.

        Args:
            data: Rate scenario parameters

        Returns:
            Interest rate analysis
        """
        current_fed_rate = data.get("fed_rate", 5.25)
        inflation = data.get("inflation", 3.5)
        unemployment = data.get("unemployment", 4.0)

        # Taylor Rule estimation
        # Rate = neutral rate + 1.5*(inflation - target) + 0.5*(GDP gap)
        neutral_rate = 2.5
        inflation_target = 2.0
        taylor_rate = neutral_rate + 1.5 * (inflation - inflation_target) + 0.5 * (inflation - inflation_target)

        # Scenario impacts
        scenarios = {
            "rates_rise_100bp": self._calculate_rate_impact(current_fed_rate, 1.0),
            "rates_unchanged": self._calculate_rate_impact(current_fed_rate, 0),
            "rates_fall_50bp": self._calculate_rate_impact(current_fed_rate, -0.5)
        }

        return {
            "current_fed_rate": current_fed_rate,
            "taylor_rule_estimate": round(taylor_rate, 2),
            "rate_vs_taylor": round(current_fed_rate - taylor_rate, 2),
            "scenarios": scenarios,
            "bond_impact": {
                "10yr_sensitivity": "~8% price change per 1% rate change",
                "note": "Longer duration = higher sensitivity"
            }
        }

    def _calculate_rate_impact(self, current_rate: float, change: float) -> Dict[str, Any]:
        """Calculate impact of rate changes."""
        new_rate = current_rate + change

        # Simplified impacts
        mortgage_rate = new_rate + 2.5  # Typical spread
        corporate_bond = new_rate + 1.5

        return {
            "new_fed_rate": new_rate,
            "est_30yr_mortgage": round(mortgage_rate, 2),
            "est_corp_bond_yield": round(corporate_bond, 2),
            "stock_impact": "Negative" if change > 0 else "Positive" if change < 0 else "Neutral",
            "housing_impact": "Cooling" if change > 0 else "Warming" if change < 0 else "Stable"
        }

    def assess_recession_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess recession probability based on indicators.

        Args:
            data: Economic indicators

        Returns:
            Recession risk assessment
        """
        # Indicators
        yield_curve = data.get("10y_2y_spread", 0.5)  # Negative = inverted
        unemployment_trend = data.get("unemployment_trend", 0)  # Positive = rising
        pmi = data.get("pmi", 52)  # Below 50 = contraction
        consumer_confidence = data.get("consumer_confidence", 100)

        # Risk scoring
        risk_score = 0

        # Yield curve inversion (strongest predictor)
        if yield_curve < 0:
            risk_score += 30
        elif yield_curve < 0.25:
            risk_score += 15

        # Rising unemployment
        if unemployment_trend > 0.5:
            risk_score += 20
        elif unemployment_trend > 0:
            risk_score += 10

        # PMI contraction
        if pmi < 50:
            risk_score += 25
        elif pmi < 52:
            risk_score += 10

        # Consumer confidence
        if consumer_confidence < 80:
            risk_score += 15
        elif consumer_confidence < 100:
            risk_score += 5

        # Cap at 100
        risk_score = min(100, risk_score)

        # Assessment
        if risk_score >= 60:
            assessment = "High"
            outlook = "Elevated recession probability within 12 months"
        elif risk_score >= 35:
            assessment = "Moderate"
            outlook = "Mixed signals, monitor closely"
        else:
            assessment = "Low"
            outlook = "Economy appears resilient"

        return {
            "risk_score": risk_score,
            "assessment": assessment,
            "outlook": outlook,
            "key_indicators": {
                "yield_curve": "Inverted" if yield_curve < 0 else "Normal",
                "pmi": "Contraction" if pmi < 50 else "Expansion",
                "consumer_sentiment": "Weak" if consumer_confidence < 80 else "Stable"
            }
        }

    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate macro recommendations."""
        recommendations = []

        # Recession risk recommendations
        rr = analysis.get("recession_risk", {})
        if rr.get("risk_score", 0) >= 50:
            recommendations.append("Consider defensive positioning given elevated recession risk")

        # Interest rate recommendations
        ir = analysis.get("interest_rates", {})
        if ir.get("rate_vs_taylor", 0) < -0.5:
            recommendations.append("Rates below Taylor Rule estimate - expect potential hikes")

        # Inflation recommendations
        inf = analysis.get("inflation", {})
        if inf.get("current_cpi", 0) > 3:
            recommendations.append("Consider TIPS or commodities as inflation hedge")

        return recommendations
