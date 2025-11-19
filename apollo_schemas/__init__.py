"""
Apollo Financial Document Schemas

This package provides JSON schemas and Python validators for
financial document types used in Apollo's RAG system.

Document Types:
    - report: Financial reports and analyses
    - statement: Financial statements (income, balance sheet, etc.)
    - projection: Financial projections and forecasts
    - regulation: Regulatory documents and guidelines
    - news: Financial news articles
    - education: Educational financial content
"""

from .validators import (
    BaseValidator,
    ReportValidator,
    StatementValidator,
    ProjectionValidator,
    RegulationValidator,
    NewsValidator,
    EducationValidator,
    VALIDATOR_REGISTRY,
    get_validator,
    validate_document,
    normalize_document,
    ValidationError,
)

__all__ = [
    "BaseValidator",
    "ReportValidator",
    "StatementValidator",
    "ProjectionValidator",
    "RegulationValidator",
    "NewsValidator",
    "EducationValidator",
    "VALIDATOR_REGISTRY",
    "get_validator",
    "validate_document",
    "normalize_document",
    "ValidationError",
]
