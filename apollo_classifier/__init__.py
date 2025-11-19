"""
Apollo Financial Intent Classifier

Multi-label intent classification for financial queries.
"""

from .classifier import (
    FinancialClassifier,
    classify_message,
    INTENT_TYPES,
)

__all__ = [
    "FinancialClassifier",
    "classify_message",
    "INTENT_TYPES",
]
