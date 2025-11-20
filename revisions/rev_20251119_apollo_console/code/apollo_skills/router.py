"""
router.py - Skill router for Apollo.

Routes queries to appropriate financial skills based on intent.
"""

import logging
from typing import Dict, Any, Optional

from apollo_skills.portfolio import PortfolioSkill
from apollo_skills.taxes import TaxSkill
from apollo_skills.macro import MacroSkill
from apollo_skills.risk import RiskSkill

logger = logging.getLogger(__name__)


class SkillRouter:
    """
    Routes queries to appropriate financial skills.
    """

    def __init__(self):
        """Initialize the skill router with all available skills."""
        self.skills = {
            "portfolio": PortfolioSkill(),
            "investing": PortfolioSkill(),  # Alias
            "taxes": TaxSkill(),
            "tax": TaxSkill(),  # Alias
            "macro": MacroSkill(),
            "risk": RiskSkill(),
            "personal_finance": PortfolioSkill(),  # Maps to portfolio
            "markets": MacroSkill(),  # Maps to macro
        }

        # Intent to skill mapping
        self.intent_map = {
            "investing": "portfolio",
            "personal_finance": "portfolio",
            "risk": "risk",
            "tax": "taxes",
            "macro": "macro",
            "markets": "macro",
        }

    def route(
        self,
        intent: str,
        query: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Route a query to the appropriate skill.

        Args:
            intent: Query intent (from classifier)
            query: User query text
            context: RAG context
            user_data: User-provided data

        Returns:
            Skill analysis result
        """
        # Map intent to skill
        skill_name = self.intent_map.get(intent, intent)
        skill = self.skills.get(skill_name)

        if not skill:
            logger.warning(f"No skill found for intent: {intent}")
            return {
                "error": f"No skill available for intent: {intent}",
                "available_intents": list(self.intent_map.keys())
            }

        # Execute skill analysis
        try:
            result = skill.analyze(query, context, user_data)
            result["routed_to"] = skill_name
            result["original_intent"] = intent
            return result
        except Exception as e:
            logger.exception(f"Skill execution failed: {e}")
            return {
                "error": str(e),
                "skill": skill_name,
                "intent": intent
            }

    def get_skill(self, name: str) -> Optional[Any]:
        """
        Get a specific skill by name.

        Args:
            name: Skill name

        Returns:
            Skill instance or None
        """
        return self.skills.get(name)

    def list_skills(self) -> Dict[str, str]:
        """
        List all available skills.

        Returns:
            Dictionary of skill names and descriptions
        """
        unique_skills = {}
        for name, skill in self.skills.items():
            if skill.name not in unique_skills:
                unique_skills[skill.name] = skill.description
        return unique_skills

    def analyze_with_multiple_skills(
        self,
        query: str,
        context: str = "",
        user_data: Dict[str, Any] = None,
        skills: list = None
    ) -> Dict[str, Any]:
        """
        Analyze a query with multiple skills.

        Args:
            query: User query
            context: RAG context
            user_data: User data
            skills: List of skill names to use

        Returns:
            Combined analysis results
        """
        if not skills:
            # Determine skills based on query content
            skills = self._detect_relevant_skills(query)

        results = {}
        for skill_name in skills:
            skill = self.skills.get(skill_name)
            if skill:
                try:
                    results[skill_name] = skill.analyze(query, context, user_data)
                except Exception as e:
                    results[skill_name] = {"error": str(e)}

        return {
            "multi_skill_analysis": results,
            "skills_used": skills
        }

    def _detect_relevant_skills(self, query: str) -> list:
        """
        Detect which skills are relevant to a query.

        Args:
            query: User query

        Returns:
            List of relevant skill names
        """
        query_lower = query.lower()
        relevant = []

        # Portfolio keywords
        portfolio_kw = ["portfolio", "diversif", "rebalanc", "allocat", "holdings"]
        if any(kw in query_lower for kw in portfolio_kw):
            relevant.append("portfolio")

        # Tax keywords
        tax_kw = ["tax", "capital gain", "harvest", "wash sale", "deduct"]
        if any(kw in query_lower for kw in tax_kw):
            relevant.append("taxes")

        # Macro keywords
        macro_kw = ["gdp", "inflation", "interest rate", "recession", "fed", "economy"]
        if any(kw in query_lower for kw in macro_kw):
            relevant.append("macro")

        # Risk keywords
        risk_kw = ["risk", "sharpe", "volatil", "var", "beta", "drawdown"]
        if any(kw in query_lower for kw in risk_kw):
            relevant.append("risk")

        return relevant if relevant else ["portfolio"]  # Default


# Global router instance
_router = None


def get_router() -> SkillRouter:
    """Get or create the global skill router."""
    global _router
    if _router is None:
        _router = SkillRouter()
    return _router


def route_skill(
    intent: str,
    query: str,
    context: str = "",
    user_data: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Convenience function to route a query to a skill.

    Args:
        intent: Query intent
        query: User query
        context: RAG context
        user_data: User data

    Returns:
        Skill analysis result
    """
    router = get_router()
    return router.route(intent, query, context, user_data)
