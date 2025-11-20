"""
controller.py - Deep-Mode Controller for Apollo

Orchestrates multi-step financial reasoning with tools and RAG.
"""

import os
import sys
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_rag_governance.proposal_engine import RAGProposalEngine


class DeepModeController:
    """
    Orchestrates deep-mode financial reasoning.

    Deep-mode performs multi-step analysis:
    1. Fetch RAG context
    2. Call relevant tools
    3. Build structured prompt
    4. Generate response with model
    """

    def __init__(
        self,
        ollama_host: str = None,
        model_name: str = None,
        max_steps: int = 5,
        timeout: int = 120
    ):
        """
        Initialize deep-mode controller.

        Args:
            ollama_host: Ollama API host
            model_name: Model name
            max_steps: Maximum reasoning steps
            timeout: Request timeout
        """
        self.ollama_host = ollama_host or os.getenv("OLLAMA_URL", "http://localhost:11434")
        # Use APOLLO_GENERATION_MODEL for the answering LLM, not embeddings
        self.model_name = model_name or os.getenv("APOLLO_GENERATION_MODEL", "Fino1-8B.Q6_K")
        self.max_steps = max_steps
        self.timeout = timeout

        # Load templates
        self.templates = {}
        template_dir = Path(__file__).parent / "templates"
        for template_file in template_dir.glob("*.txt"):
            name = template_file.stem
            self.templates[name] = template_file.read_text()
        self.rag_engine = RAGProposalEngine()

    def analyze(
        self,
        question: str,
        intent: str,
        context: str = "",
        tool_results: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Perform deep-mode analysis.

        Args:
            question: User question
            intent: Financial intent (risk, investing, macro)
            context: RAG context
            tool_results: Results from financial tools

        Returns:
            Analysis result
        """
        # Select template
        template = self.templates.get(intent, self.templates.get("investing", ""))

        if not template:
            # Fallback to basic prompt
            template = """
            Context: {context}
            Tool Results: {tool_results}
            Question: {question}
            Answer clearly and directly.
            """

        # Format tool results
        tool_text = self._format_tool_results(tool_results)

        # Build prompt
        prompt = template.format(
            context=context if context else "(No context available)",
            tool_results=tool_text if tool_text else "(No tool results)",
            question=question
        )

        # Call model
        response = self._call_model(prompt)

        return {
            "response": response,
            "intent": intent,
            "context_used": bool(context),
            "tools_used": bool(tool_results),
            "model": self.model_name,
        }

    def run(
        self,
        question: str,
        intent: str,
        rag_results: List[Dict[str, Any]] = None,
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Run complete deep-mode analysis with tool invocation.

        Args:
            question: User question
            intent: Financial intent
            rag_results: RAG search results
            user_data: User-provided financial data

        Returns:
            Complete analysis result
        """
        # Step 1: Extract context from RAG
        context = self._extract_context(rag_results)

        # Step 2: Determine and run tools
        tool_results = self._run_tools(question, intent, user_data)

        # Step 3: Generate analysis
        result = self.analyze(question, intent, context, tool_results)

        if context:
            proposal = self.rag_engine.generate_recommendations()
            if proposal.get("recommendations") and intent in {"markets", "personal_finance", "investing", "risk", "macro"}:
                result["rag_proposals"] = proposal

        return result

    def _extract_context(self, rag_results: List[Dict[str, Any]]) -> str:
        """Extract text context from RAG results."""
        if not rag_results:
            return ""

        context_parts = []
        for hit in rag_results[:5]:  # Limit to top 5
            text = hit.get("text") or hit.get("document", "")
            if text:
                context_parts.append(text.strip())

        return "\n\n".join(context_parts)

    def _run_tools(
        self,
        question: str,
        intent: str,
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Run relevant financial tools based on intent and data.

        Args:
            question: User question
            intent: Financial intent
            user_data: User-provided data for tools

        Returns:
            Tool results dictionary
        """
        if not user_data:
            return {}

        results = {}

        try:
            # Import tools
            from apollo_tools import (
                risk_score, dti_ratio, sharpe_ratio, capm_return,
                compound_growth, real_growth, four_percent_rule,
                retirement_horizon, federal_tax, capital_gains_tax,
                portfolio_rebalance, inflation_adjust
            )

            # Risk-related tools
            if intent == "risk":
                if "monthly_debt" in user_data and "monthly_income" in user_data:
                    results["dti"] = dti_ratio(
                        user_data["monthly_debt"],
                        user_data["monthly_income"]
                    )

                risk_params = {}
                if "debt_to_income" in user_data:
                    risk_params["debt_to_income"] = user_data["debt_to_income"]
                if "savings_rate" in user_data:
                    risk_params["savings_rate"] = user_data["savings_rate"]
                if "emergency_fund_months" in user_data:
                    risk_params["emergency_fund_months"] = user_data["emergency_fund_months"]

                if risk_params:
                    results["risk_score"] = risk_score(**risk_params)

            # Investing tools
            elif intent == "investing":
                if "principal" in user_data and "annual_rate" in user_data and "years" in user_data:
                    results["compound_growth"] = compound_growth(
                        user_data["principal"],
                        user_data["annual_rate"],
                        user_data["years"],
                        user_data.get("contributions", 0)
                    )

                if "inflation_rate" in user_data and "principal" in user_data:
                    results["real_growth"] = real_growth(
                        user_data["principal"],
                        user_data.get("annual_rate", 0.07),
                        user_data["inflation_rate"],
                        user_data.get("years", 10)
                    )

                if "current_holdings" in user_data and "target_allocation" in user_data:
                    results["rebalance"] = portfolio_rebalance(
                        user_data["current_holdings"],
                        user_data["target_allocation"]
                    )

            # Macro tools
            elif intent == "macro":
                if "amount" in user_data and "inflation_rate" in user_data:
                    results["inflation_adjust"] = inflation_adjust(
                        user_data["amount"],
                        user_data["inflation_rate"],
                        user_data.get("years", 10)
                    )

            # Tax tools
            elif intent == "tax":
                if "gross_income" in user_data:
                    results["federal_tax"] = federal_tax(
                        user_data["gross_income"],
                        user_data.get("filing_status", "single")
                    )

                if "capital_gains" in user_data:
                    results["cap_gains_tax"] = capital_gains_tax(
                        user_data["capital_gains"],
                        user_data.get("gross_income", 0)
                    )

            # Retirement tools
            if "portfolio_value" in user_data:
                results["four_percent_rule"] = four_percent_rule(
                    user_data["portfolio_value"],
                    user_data.get("annual_expenses")
                )

        except Exception as e:
            results["error"] = str(e)

        return results

    def _format_tool_results(self, tool_results: Dict[str, Any]) -> str:
        """Format tool results for prompt."""
        if not tool_results:
            return ""

        lines = []
        for tool_name, result in tool_results.items():
            if isinstance(result, dict):
                # Extract key values
                key_items = []
                for key, value in result.items():
                    if key not in ["schedule", "yearly_breakdown", "projections", "yearly_projection"]:
                        if isinstance(value, float):
                            key_items.append(f"{key}: {value:.2f}")
                        else:
                            key_items.append(f"{key}: {value}")
                lines.append(f"{tool_name}: {', '.join(key_items[:8])}")  # Limit items
            else:
                lines.append(f"{tool_name}: {result}")

        return "\n".join(lines)

    def _call_model(self, prompt: str) -> str:
        """Call Ollama model with prompt."""
        try:
            response = requests.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json().get("response", "")

        except requests.exceptions.RequestException as e:
            return f"Error calling model: {e}"


if __name__ == "__main__":
    print("Testing Deep-Mode Controller")
    print("=" * 60)

    controller = DeepModeController()

    # Test with sample data
    result = controller.run(
        question="What's my financial risk level?",
        intent="risk",
        user_data={
            "debt_to_income": 0.35,
            "savings_rate": 0.12,
            "emergency_fund_months": 3
        }
    )

    print(f"Intent: {result['intent']}")
    print(f"Tools used: {result['tools_used']}")
    print(f"\nResponse:\n{result['response'][:500]}...")
