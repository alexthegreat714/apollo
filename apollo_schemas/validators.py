"""
validators.py - Apollo Financial Document Schema Validators

Provides validation classes for each financial document type.
"""

import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ValidationError(Exception):
    """Raised when document validation fails."""
    pass


class BaseValidator(ABC):
    """Base class for all document validators."""

    KIND: str = ""
    REQUIRED_FIELDS: List[str] = []

    def __init__(self):
        self.errors: List[str] = []

    def validate(self, doc: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate a document against the schema.

        Args:
            doc: Document dictionary to validate

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        self.errors = []

        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if field not in doc or doc[field] is None:
                self.errors.append(f"Missing required field: {field}")

        # Validate kind
        if doc.get("kind") != self.KIND:
            self.errors.append(f"Invalid kind: expected '{self.KIND}', got '{doc.get('kind')}'")

        # Validate timestamp format
        if "timestamp" in doc and doc["timestamp"]:
            if not self._validate_datetime(doc["timestamp"]):
                self.errors.append(f"Invalid timestamp format: {doc['timestamp']}")

        # Validate dates
        for date_field in ["period_start", "period_end"]:
            if date_field in doc and doc[date_field]:
                if not self._validate_date(doc[date_field]):
                    self.errors.append(f"Invalid date format for {date_field}: {doc[date_field]}")

        # Validate priority
        if "priority" in doc and doc["priority"] is not None:
            if not isinstance(doc["priority"], int) or not 1 <= doc["priority"] <= 10:
                self.errors.append(f"Priority must be integer 1-10, got: {doc['priority']}")

        # Run type-specific validation
        self._validate_specific(doc)

        return len(self.errors) == 0, self.errors

    @abstractmethod
    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        """Type-specific validation to be implemented by subclasses."""
        pass

    def normalize(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize document fields.

        Args:
            doc: Document to normalize

        Returns:
            Normalized document
        """
        normalized = doc.copy()

        # Generate ID if missing
        if not normalized.get("id"):
            normalized["id"] = f"{self.KIND}_{uuid.uuid4().hex[:12]}"

        # Ensure kind is set
        normalized["kind"] = self.KIND

        # Set default priority if missing
        if "priority" not in normalized or normalized["priority"] is None:
            normalized["priority"] = self._get_default_priority()

        # Normalize timestamp
        if "timestamp" in normalized and normalized["timestamp"]:
            normalized["timestamp"] = self._normalize_datetime(normalized["timestamp"])

        # Normalize text fields
        if "summary" in normalized and normalized["summary"]:
            normalized["summary"] = self._normalize_text(normalized["summary"])

        if "full_text" in normalized and normalized["full_text"]:
            normalized["full_text"] = self._normalize_text(normalized["full_text"])

        # Initialize metadata if missing
        if "metadata" not in normalized:
            normalized["metadata"] = {}

        # Run type-specific normalization
        normalized = self._normalize_specific(normalized)

        return normalized

    def _normalize_specific(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Type-specific normalization. Override in subclasses."""
        return doc

    def _get_default_priority(self) -> int:
        """Get default priority for this document type."""
        return 5

    @staticmethod
    def _validate_datetime(value: str) -> bool:
        """Validate ISO8601 datetime format."""
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _validate_date(value: str) -> bool:
        """Validate ISO8601 date format."""
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _normalize_datetime(value: str) -> str:
        """Normalize datetime to ISO8601 format."""
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.isoformat()
        except (ValueError, AttributeError):
            return value

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text: collapse whitespace, strip."""
        if not text:
            return ""
        # Collapse multiple whitespace to single space
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


class ReportValidator(BaseValidator):
    """Validator for financial reports."""

    KIND = "report"
    REQUIRED_FIELDS = ["id", "kind", "timestamp", "source", "summary", "full_text"]

    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        # Validate summary length
        if doc.get("summary") and len(doc["summary"].split()) > 100:
            self.errors.append("Summary exceeds 100 words")

        # Validate full_text minimum
        if doc.get("full_text") and len(doc["full_text"]) < 50:
            self.errors.append("Full text must be at least 50 characters")

    def _get_default_priority(self) -> int:
        return 5


class StatementValidator(BaseValidator):
    """Validator for financial statements."""

    KIND = "statement"
    REQUIRED_FIELDS = ["id", "kind", "timestamp", "period_start", "period_end", "source", "summary", "full_text"]
    VALID_STATEMENT_TYPES = ["income", "balance_sheet", "cash_flow", "equity"]

    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        # Validate statement type if provided
        metadata = doc.get("metadata", {})
        stmt_type = metadata.get("statement_type")
        if stmt_type and stmt_type not in self.VALID_STATEMENT_TYPES:
            self.errors.append(f"Invalid statement_type: {stmt_type}")

        # Validate quarter if provided
        quarter = metadata.get("quarter")
        if quarter is not None and (not isinstance(quarter, int) or not 1 <= quarter <= 4):
            self.errors.append(f"Quarter must be 1-4, got: {quarter}")

        # Validate period dates exist
        if not doc.get("period_start") or not doc.get("period_end"):
            self.errors.append("Statement requires period_start and period_end")

    def _get_default_priority(self) -> int:
        return 3


class ProjectionValidator(BaseValidator):
    """Validator for financial projections."""

    KIND = "projection"
    REQUIRED_FIELDS = ["id", "kind", "timestamp", "period_start", "period_end", "source", "summary", "full_text"]
    VALID_PROJECTION_TYPES = ["earnings", "revenue", "gdp", "inflation", "interest_rate", "custom"]
    VALID_CONFIDENCE_LEVELS = ["high", "medium", "low"]
    VALID_SCENARIOS = ["base", "bull", "bear"]

    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        metadata = doc.get("metadata", {})

        # Validate projection type
        proj_type = metadata.get("projection_type")
        if proj_type and proj_type not in self.VALID_PROJECTION_TYPES:
            self.errors.append(f"Invalid projection_type: {proj_type}")

        # Validate confidence level
        confidence = metadata.get("confidence_level")
        if confidence and confidence not in self.VALID_CONFIDENCE_LEVELS:
            self.errors.append(f"Invalid confidence_level: {confidence}")

        # Validate scenario
        scenario = metadata.get("scenario")
        if scenario and scenario not in self.VALID_SCENARIOS:
            self.errors.append(f"Invalid scenario: {scenario}")

        # Validate projection period is in the future (warning only)
        if doc.get("period_start"):
            try:
                period_start = datetime.strptime(doc["period_start"], "%Y-%m-%d").date()
                if period_start < date.today():
                    pass  # Could add warning for past projections
            except ValueError:
                pass

    def _get_default_priority(self) -> int:
        return 4


class RegulationValidator(BaseValidator):
    """Validator for financial regulations."""

    KIND = "regulation"
    REQUIRED_FIELDS = ["id", "kind", "timestamp", "source", "summary", "full_text"]
    VALID_REGULATION_TYPES = ["tax", "securities", "banking", "insurance", "other"]
    VALID_STATUSES = ["proposed", "final", "amended", "repealed"]
    VALID_REGULATORY_BODIES = ["SEC", "IRS", "Fed", "CFTC", "FINRA", "OCC", "FDIC"]

    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        metadata = doc.get("metadata", {})

        # Validate regulation type
        reg_type = metadata.get("regulation_type")
        if reg_type and reg_type not in self.VALID_REGULATION_TYPES:
            self.errors.append(f"Invalid regulation_type: {reg_type}")

        # Validate status
        status = metadata.get("status")
        if status and status not in self.VALID_STATUSES:
            self.errors.append(f"Invalid status: {status}")

        # Source should be a known regulatory body (warning only)
        source = doc.get("source", "")
        if source and not any(body in source.upper() for body in self.VALID_REGULATORY_BODIES):
            pass  # Could add warning for unknown regulatory body

    def _get_default_priority(self) -> int:
        return 2


class NewsValidator(BaseValidator):
    """Validator for financial news."""

    KIND = "news"
    REQUIRED_FIELDS = ["id", "kind", "timestamp", "source", "summary", "full_text"]
    VALID_CATEGORIES = ["markets", "economy", "corporate", "policy", "crypto", "commodities"]
    VALID_SENTIMENTS = ["positive", "negative", "neutral"]

    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        metadata = doc.get("metadata", {})

        # Validate category
        category = metadata.get("category")
        if category and category not in self.VALID_CATEGORIES:
            self.errors.append(f"Invalid category: {category}")

        # Validate sentiment
        sentiment = metadata.get("sentiment")
        if sentiment and sentiment not in self.VALID_SENTIMENTS:
            self.errors.append(f"Invalid sentiment: {sentiment}")

        # Validate tickers are uppercase
        tickers = metadata.get("tickers", [])
        for ticker in tickers:
            if ticker != ticker.upper():
                self.errors.append(f"Ticker should be uppercase: {ticker}")

        # Validate URL format if provided
        url = metadata.get("url")
        if url and not url.startswith(("http://", "https://")):
            self.errors.append(f"Invalid URL format: {url}")

    def _normalize_specific(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize tickers to uppercase."""
        if "metadata" in doc and "tickers" in doc["metadata"]:
            doc["metadata"]["tickers"] = [t.upper() for t in doc["metadata"]["tickers"]]
        return doc

    def _get_default_priority(self) -> int:
        return 5


class EducationValidator(BaseValidator):
    """Validator for financial education content."""

    KIND = "education"
    REQUIRED_FIELDS = ["id", "kind", "timestamp", "source", "summary", "full_text"]
    VALID_DIFFICULTIES = ["beginner", "intermediate", "advanced"]

    def _validate_specific(self, doc: Dict[str, Any]) -> None:
        metadata = doc.get("metadata", {})

        # Validate difficulty
        difficulty = metadata.get("difficulty")
        if difficulty and difficulty not in self.VALID_DIFFICULTIES:
            self.errors.append(f"Invalid difficulty: {difficulty}")

        # Validate minimum content length
        if doc.get("full_text") and len(doc["full_text"]) < 200:
            self.errors.append("Educational content should be at least 200 characters")

    def _normalize_specific(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize concepts to lowercase."""
        if "metadata" in doc and "concepts" in doc["metadata"]:
            doc["metadata"]["concepts"] = [c.lower() for c in doc["metadata"]["concepts"]]
        return doc

    def _get_default_priority(self) -> int:
        return 6


# Registry mapping KIND to validator class
VALIDATOR_REGISTRY: Dict[str, type] = {
    "report": ReportValidator,
    "statement": StatementValidator,
    "projection": ProjectionValidator,
    "regulation": RegulationValidator,
    "news": NewsValidator,
    "education": EducationValidator,
}


def get_validator(kind: str) -> BaseValidator:
    """
    Get validator instance for a document kind.

    Args:
        kind: Document kind (report, statement, etc.)

    Returns:
        Validator instance

    Raises:
        ValueError: If kind is not recognized
    """
    validator_class = VALIDATOR_REGISTRY.get(kind)
    if not validator_class:
        raise ValueError(f"Unknown document kind: {kind}. Valid kinds: {list(VALIDATOR_REGISTRY.keys())}")
    return validator_class()


def validate_document(doc: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate a document using the appropriate validator.

    Args:
        doc: Document dictionary

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    kind = doc.get("kind")
    if not kind:
        return False, ["Missing 'kind' field"]

    try:
        validator = get_validator(kind)
        return validator.validate(doc)
    except ValueError as e:
        return False, [str(e)]


def normalize_document(doc: Dict[str, Any], kind: str = None) -> Dict[str, Any]:
    """
    Normalize a document using the appropriate validator.

    Args:
        doc: Document dictionary
        kind: Optional kind override (uses doc['kind'] if not provided)

    Returns:
        Normalized document
    """
    kind = kind or doc.get("kind")
    if not kind:
        raise ValueError("Document kind must be specified")

    validator = get_validator(kind)
    return validator.normalize(doc)


if __name__ == "__main__":
    # Test the validators
    print("Testing Apollo Schema Validators")
    print("=" * 60)

    # Test report validation
    test_report = {
        "kind": "report",
        "timestamp": "2024-12-15T10:30:00Z",
        "source": "Federal Reserve",
        "summary": "Q4 inflation report showing 3.2% CPI increase",
        "full_text": "The Federal Reserve has released its Q4 2024 inflation report. " * 5
    }

    validator = get_validator("report")
    normalized = validator.normalize(test_report)
    is_valid, errors = validator.validate(normalized)

    print(f"Report Validation: {'PASS' if is_valid else 'FAIL'}")
    if errors:
        for error in errors:
            print(f"  - {error}")
    print(f"Generated ID: {normalized['id']}")

    print("\n" + "=" * 60)
    print("Validator Registry:")
    for kind, cls in VALIDATOR_REGISTRY.items():
        print(f"  {kind} -> {cls.__name__}")
