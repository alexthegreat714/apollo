"""
investing_mode.py - Investing Analysis Mode for Apollo Deep-Mode

Specialized investment analysis reasoning module.
"""

from typing import Dict, Any, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class InvestingAnalysisMode:
    """
    Specialized mode for investment analysis.
    """

    def __init__(self):
        self.required_data_fields = [
            "principal",
            "annual_rate",
            "years",
            "contributions",
            "inflation_rate",
            "current_holdings",
            "target_allocation"
        ]

    def analyze(
        self,
        question: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Perform investment analysis.

        Args:
            question: User's investment question
            context: RAG context
            user_data: User financial data

        Returns:
            Investment analysis results
        """
        from apollo_tools import (
            compound_growth, real_growth, portfolio_rebalance,
            drift_analysis, capm_return
        )

        results = {
            "analysis_type": "investing",
            "projections": {},
            "recommendations": [],
            "metrics": {}
        }

        user_data = user_data or {}

        # Compound growth projection
        if all(k in user_data for k in ["principal", "annual_rate", "years"]):
            growth_result = compound_growth(
                user_data["principal"],
                user_data["annual_rate"],
                user_data["years"],
                user_data.get("contributions", 0),
                user_data.get("contribution_frequency", "monthly")
            )
            results["projections"]["nominal"] = {
                "future_value": growth_result["future_value"],
                "total_growth": growth_result["total_growth"],
                "growth_percent": growth_result["growth_percent"]
            }

            # Real (inflation-adjusted) growth
            inflation = user_data.get("inflation_rate", 0.03)
            real_result = real_growth(
                user_data["principal"],
                user_data["annual_rate"],
                inflation,
                user_data["years"],
                user_data.get("contributions", 0)
            )
            results["projections"]["real"] = {
                "real_future_value": real_result["real_future_value"],
                "purchasing_power": real_result["purchasing_power_today"],
                "real_rate": real_result["real_rate"]
            }

            # Recommendation based on growth
            if growth_result["growth_percent"] < 50:
                results["recommendations"].append(
                    "Consider increasing contributions to accelerate growth"
                )

        # Portfolio rebalancing
        if "current_holdings" in user_data and "target_allocation" in user_data:
            rebalance_result = portfolio_rebalance(
                user_data["current_holdings"],
                user_data["target_allocation"],
                user_data.get("contribution", 0)
            )
            results["metrics"]["rebalance"] = {
                "trades_needed": rebalance_result["trade_count"],
                "total_buys": rebalance_result["total_buys"],
                "total_sells": rebalance_result["total_sells"]
            }

            drift_result = drift_analysis(
                user_data["current_holdings"],
                user_data["target_allocation"]
            )
            results["metrics"]["drift"] = {
                "max_drift": drift_result["max_drift"],
                "needs_rebalance": drift_result["needs_rebalance"]
            }

            if drift_result["needs_rebalance"]:
                results["recommendations"].append(
                    f"Portfolio drift of {drift_result['max_drift']}% exceeds threshold - rebalance recommended"
                )

        # CAPM expected return
        if "beta" in user_data:
            capm_result = capm_return(
                user_data.get("risk_free_rate", 0.04),
                user_data["beta"],
                user_data.get("market_return", 0.10)
            )
            results["metrics"]["expected_return"] = {
                "capm_return": capm_result["expected_return"],
                "risk_profile": capm_result["risk_profile"]
            }

        # Add context
        if context:
            results["context_summary"] = context[:500]

        return results

    def get_required_fields(self) -> List[str]:
        """Get list of optional data fields for investment analysis."""
        return self.required_data_fields


if __name__ == "__main__":
    print("Testing Investing Analysis Mode")
    print("=" * 60)

    mode = InvestingAnalysisMode()

    result = mode.analyze(
        question="How much will I have in 30 years?",
        user_data={
            "principal": 50000,
            "annual_rate": 0.08,
            "years": 30,
            "contributions": 500,
            "inflation_rate": 0.03
        }
    )

    nominal = result["projections"]["nominal"]
    real = result["projections"]["real"]

    print(f"Nominal Future Value: ${nominal['future_value']:,.2f}")
    print(f"Real Future Value: ${real['real_future_value']:,.2f}")
    print(f"Purchasing Power (Today's $): ${real['purchasing_power']:,.2f}")
    print(f"\nRecommendations:")
    for rec in result["recommendations"]:
        print(f"  - {rec}")
