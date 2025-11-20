"""
apollo_skills - Financial skill modules for Apollo.

Provides specialized analysis capabilities for different financial domains.
"""

from apollo_skills.portfolio import PortfolioSkill
from apollo_skills.taxes import TaxSkill
from apollo_skills.macro import MacroSkill
from apollo_skills.risk import RiskSkill
from apollo_skills.router import route_skill, SkillRouter

__all__ = [
    "PortfolioSkill",
    "TaxSkill",
    "MacroSkill",
    "RiskSkill",
    "route_skill",
    "SkillRouter"
]
