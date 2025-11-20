"""
macro_mode.py - Macroeconomic Analysis Mode for Apollo Deep-Mode

Specialized macroeconomic analysis reasoning module.
"""

from typing import Dict, Any, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MacroAnalysisMode:
    """
    Specialized mode for macroeconomic analysis.
    """

    def __init__(self):
        self.required_data_fields = [
            "inflation_rate",
            "nominal_rate",
            "amount",
            "years"
        ]

    def analyze(
        self,
        question: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Perform macroeconomic analysis.

        Args:
            question: User's macro question
            context: RAG context
            user_data: User data

        Returns:
            Macro analysis results
        """
        from apollo_tools import (
            inflation_adjust, purchasing_power, real_rate
        )

        results = {
            "analysis_type": "macro",
            "inflation_impact": {},
            "interest_rate_analysis": {},
            "recommendations": []
        }

        user_data = user_data or {}

        # Inflation impact analysis
        if "amount" in user_data and "inflation_rate" in user_data:
            years = user_data.get("years", 10)

            # Future purchasing power
            future_impact = inflation_adjust(
                user_data["amount"],
                user_data["inflation_rate"],
                years,
                "future"
            )
            results["inflation_impact"]["future"] = {
                "original": user_data["amount"],
                "purchasing_power_in_future": future_impact["adjusted_amount"],
                "loss": future_impact["purchasing_power_change"],
                "cumulative_inflation": future_impact["cumulative_inflation"]
            }

            # Purchasing power analysis
            pp_result = purchasing_power(
                user_data["amount"],
                user_data.get("start_year", 2024),
                user_data.get("end_year", 2034),
                user_data["inflation_rate"]
            )
            results["inflation_impact"]["purchasing_power"] = pp_result

            # Recommendation based on inflation
            if user_data["inflation_rate"] > 0.03:
                results["recommendations"].append(
                    f"High inflation ({user_data['inflation_rate']*100:.1f}%) - consider inflation-protected securities"
                )

        # Real rate analysis
        if "nominal_rate" in user_data and "inflation_rate" in user_data:
            real_result = real_rate(
                user_data["nominal_rate"],
                user_data["inflation_rate"]
            )
            results["interest_rate_analysis"]["real_rate"] = real_result

            if real_result["real_rate"] < 0:
                results["recommendations"].append(
                    "Negative real rates - cash holdings lose value over time"
                )
            elif real_result["real_rate"] < 0.01:
                results["recommendations"].append(
                    "Low real rates - seek higher-yielding alternatives"
                )

        # Asset class implications from context
        if context:
            results["context_summary"] = context[:500]

            # Simple keyword analysis for recommendations
            context_lower = context.lower()
            if "recession" in context_lower:
                results["recommendations"].append(
                    "Recession concerns - consider defensive positioning"
                )
            if "rate hike" in context_lower or "rate increase" in context_lower:
                results["recommendations"].append(
                    "Rising rates expected - review bond duration exposure"
                )
            if "inflation" in context_lower and "high" in context_lower:
                results["recommendations"].append(
                    "High inflation environment - favor real assets and TIPS"
                )

        return results

    def get_required_fields(self) -> List[str]:
        """Get list of optional data fields for macro analysis."""
        return self.required_data_fields


if __name__ == "__main__":
    print("Testing Macro Analysis Mode")
    print("=" * 60)

    mode = MacroAnalysisMode()

    result = mode.analyze(
        question="How will inflation affect my savings?",
        context="The Fed maintained rates at 5.25% amid persistent inflation concerns.",
        user_data={
            "amount": 100000,
            "inflation_rate": 0.035,
            "nominal_rate": 0.05,
            "years": 10
        }
    )

    impact = result["inflation_impact"]["future"]
    print(f"Original Amount: ${impact['original']:,.2f}")
    print(f"Purchasing Power in 10 years: ${impact['purchasing_power_in_future']:,.2f}")
    print(f"Cumulative Inflation: {impact['cumulative_inflation']:.1f}%")

    real = result["interest_rate_analysis"]["real_rate"]
    print(f"\nReal Rate: {real['real_rate_formatted']}")

    print(f"\nRecommendations:")
    for rec in result["recommendations"]:
        print(f"  - {rec}")
