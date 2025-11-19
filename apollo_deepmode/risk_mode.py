"""
risk_mode.py - Risk Analysis Mode for Apollo Deep-Mode

Specialized risk analysis reasoning module.
"""

from typing import Dict, Any, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RiskAnalysisMode:
    """
    Specialized mode for financial risk analysis.
    """

    def __init__(self):
        self.required_data_fields = [
            "debt_to_income",
            "savings_rate",
            "emergency_fund_months",
            "portfolio_concentration",
            "age",
            "stock_allocation"
        ]

    def analyze(
        self,
        question: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Perform risk analysis.

        Args:
            question: User's risk question
            context: RAG context
            user_data: User financial data

        Returns:
            Risk analysis results
        """
        from apollo_tools import risk_score, dti_ratio, sharpe_ratio

        results = {
            "analysis_type": "risk",
            "metrics": {},
            "assessment": {},
            "recommendations": []
        }

        user_data = user_data or {}

        # Calculate risk score if data available
        risk_params = {}
        for field in self.required_data_fields:
            if field in user_data:
                risk_params[field] = user_data[field]

        if risk_params:
            score_result = risk_score(**risk_params)
            results["metrics"]["risk_score"] = score_result
            results["assessment"]["overall_risk"] = score_result["risk_level"]

            # Generate recommendations based on factors
            for factor in score_result.get("factors", []):
                if factor["score"] > factor["max"] * 0.5:
                    results["recommendations"].append(
                        f"Improve {factor['factor']}: current score {factor['score']}/{factor['max']}"
                    )

        # DTI analysis
        if "monthly_debt" in user_data and "monthly_income" in user_data:
            dti_result = dti_ratio(user_data["monthly_debt"], user_data["monthly_income"])
            results["metrics"]["dti"] = dti_result
            results["assessment"]["debt_burden"] = dti_result["status"]

            if dti_result["dti_ratio"] > 0.36:
                results["recommendations"].append(
                    f"Reduce debt-to-income from {dti_result['dti_percent']}% to below 36%"
                )

        # Portfolio risk
        if "returns" in user_data:
            sharpe_result = sharpe_ratio(user_data["returns"])
            results["metrics"]["sharpe_ratio"] = sharpe_result
            results["assessment"]["return_quality"] = sharpe_result.get("interpretation", "")

        # Add context-based insights
        if context:
            results["context_summary"] = context[:500]

        return results

    def get_required_fields(self) -> List[str]:
        """Get list of optional data fields for risk analysis."""
        return self.required_data_fields


if __name__ == "__main__":
    print("Testing Risk Analysis Mode")
    print("=" * 60)

    mode = RiskAnalysisMode()

    result = mode.analyze(
        question="Am I taking too much risk?",
        user_data={
            "debt_to_income": 0.40,
            "savings_rate": 0.08,
            "emergency_fund_months": 2,
            "monthly_debt": 3000,
            "monthly_income": 7500
        }
    )

    print(f"Overall Risk: {result['assessment'].get('overall_risk', 'N/A')}")
    print(f"Debt Burden: {result['assessment'].get('debt_burden', 'N/A')}")
    print(f"\nRecommendations:")
    for rec in result["recommendations"]:
        print(f"  - {rec}")
