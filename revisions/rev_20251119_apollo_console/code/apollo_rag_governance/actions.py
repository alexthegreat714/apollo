"""
actions.py - RAG Improvement Action Schema and Types.

Defines structured improvement actions for the auto-RAG governance system.
"""

import json
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
from enum import Enum


class ActionType(str, Enum):
    """Types of RAG improvement actions."""
    ADD_SYNONYMS = "add_synonyms"
    ADJUST_WEIGHTS = "adjust_weights"
    REEMBED = "reembed"
    RE_CHUNK = "re_chunk"
    RE_INGEST_FILE = "re_ingest_file"
    RE_INGEST_CATEGORY = "re_ingest_category"
    EXPAND_CATEGORY = "expand_category"
    FLAG_STALE = "flag_stale"
    UPDATE_EMBEDDING_MODEL = "update_embedding_model"
    PRUNE_DUPLICATES = "prune_duplicates"
    OPTIMIZE_CHUNK_SIZE = "optimize_chunk_size"


class ActionStatus(str, Enum):
    """Status of an improvement action."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


@dataclass
class RAGImprovementAction:
    """
    Represents a single RAG improvement action.

    Attributes:
        action_type: Type of improvement action
        description: Human-readable description
        payload: Action-specific parameters
        requires_approval: Whether user must approve before execution
        priority: Action priority (high, medium, low)
        created_at: Timestamp when action was created
        status: Current status of the action
        action_id: Unique identifier for the action
    """
    action_type: str
    description: str
    payload: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = True
    priority: str = "medium"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    status: str = ActionStatus.PENDING.value
    action_id: str = field(default_factory=lambda: f"action_{int(datetime.utcnow().timestamp() * 1000)}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert action to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert action to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RAGImprovementAction":
        """Create action from dictionary."""
        return cls(
            action_type=data.get("action_type", ""),
            description=data.get("description", ""),
            payload=data.get("payload", {}),
            requires_approval=data.get("requires_approval", True),
            priority=data.get("priority", "medium"),
            created_at=data.get("created_at", datetime.utcnow().isoformat() + "Z"),
            status=data.get("status", ActionStatus.PENDING.value),
            action_id=data.get("action_id", f"action_{int(datetime.utcnow().timestamp() * 1000)}")
        )

    def approve(self) -> None:
        """Mark action as approved."""
        self.status = ActionStatus.APPROVED.value

    def reject(self) -> None:
        """Mark action as rejected."""
        self.status = ActionStatus.REJECTED.value

    def mark_applied(self) -> None:
        """Mark action as successfully applied."""
        self.status = ActionStatus.APPLIED.value

    def mark_failed(self) -> None:
        """Mark action as failed."""
        self.status = ActionStatus.FAILED.value


@dataclass
class ActionProposal:
    """
    A collection of related improvement actions.

    Attributes:
        actions: List of improvement actions
        overall_priority: Priority of the entire proposal
        reason: Why this proposal was generated
        created_at: When the proposal was created
        proposal_id: Unique identifier
    """
    actions: List[RAGImprovementAction] = field(default_factory=list)
    overall_priority: str = "medium"
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    proposal_id: str = field(default_factory=lambda: f"proposal_{int(datetime.utcnow().timestamp() * 1000)}")

    def add_action(self, action: RAGImprovementAction) -> None:
        """Add an action to the proposal."""
        self.actions.append(action)
        # Update overall priority if action is higher priority
        priority_order = {"high": 3, "medium": 2, "low": 1}
        if priority_order.get(action.priority, 0) > priority_order.get(self.overall_priority, 0):
            self.overall_priority = action.priority

    def to_dict(self) -> Dict[str, Any]:
        """Convert proposal to dictionary."""
        return {
            "proposal_id": self.proposal_id,
            "actions": [a.to_dict() for a in self.actions],
            "overall_priority": self.overall_priority,
            "reason": self.reason,
            "created_at": self.created_at,
            "action_count": len(self.actions)
        }

    def get_pending_actions(self) -> List[RAGImprovementAction]:
        """Get all pending actions."""
        return [a for a in self.actions if a.status == ActionStatus.PENDING.value]

    def get_approved_actions(self) -> List[RAGImprovementAction]:
        """Get all approved actions."""
        return [a for a in self.actions if a.status == ActionStatus.APPROVED.value]


# =============================================================================
# Action Factory Functions
# =============================================================================

def create_synonym_action(
    target_term: str,
    synonyms: List[str],
    reason: str = ""
) -> RAGImprovementAction:
    """Create an add-synonyms action."""
    return RAGImprovementAction(
        action_type=ActionType.ADD_SYNONYMS.value,
        description=f"Add synonyms for '{target_term}': {', '.join(synonyms)}",
        payload={
            "target_term": target_term,
            "synonyms": synonyms
        },
        priority="low",
        requires_approval=True
    )


def create_weight_adjustment_action(
    kind: str,
    current_weight: float,
    proposed_weight: float,
    reason: str = ""
) -> RAGImprovementAction:
    """Create a weight adjustment action."""
    return RAGImprovementAction(
        action_type=ActionType.ADJUST_WEIGHTS.value,
        description=f"Adjust weight for '{kind}': {current_weight:.2f} → {proposed_weight:.2f}",
        payload={
            "kind": kind,
            "current_weight": current_weight,
            "proposed_weight": proposed_weight,
            "reason": reason
        },
        priority="medium",
        requires_approval=True
    )


def create_reembed_action(
    target: str,
    reason: str = "",
    scope: str = "category"
) -> RAGImprovementAction:
    """Create a re-embedding action."""
    return RAGImprovementAction(
        action_type=ActionType.REEMBED.value,
        description=f"Re-embed documents: {target}",
        payload={
            "target": target,
            "scope": scope,
            "reason": reason
        },
        priority="high",
        requires_approval=True
    )


def create_rechunk_action(
    target: str,
    current_chunk_size: int,
    proposed_chunk_size: int,
    reason: str = ""
) -> RAGImprovementAction:
    """Create a re-chunking action."""
    return RAGImprovementAction(
        action_type=ActionType.RE_CHUNK.value,
        description=f"Re-chunk '{target}': {current_chunk_size} → {proposed_chunk_size} tokens",
        payload={
            "target": target,
            "current_chunk_size": current_chunk_size,
            "proposed_chunk_size": proposed_chunk_size,
            "reason": reason
        },
        priority="high",
        requires_approval=True
    )


def create_reingest_action(
    target: str,
    action_type: str = ActionType.RE_INGEST_FILE.value,
    reason: str = ""
) -> RAGImprovementAction:
    """Create a re-ingestion action."""
    return RAGImprovementAction(
        action_type=action_type,
        description=f"Re-ingest: {target}",
        payload={
            "target": target,
            "reason": reason
        },
        priority="medium",
        requires_approval=True
    )


def create_stale_flag_action(
    document_id: str,
    age_days: int,
    reason: str = ""
) -> RAGImprovementAction:
    """Create a stale document flag action."""
    return RAGImprovementAction(
        action_type=ActionType.FLAG_STALE.value,
        description=f"Flag stale document: {document_id} ({age_days} days old)",
        payload={
            "document_id": document_id,
            "age_days": age_days,
            "reason": reason
        },
        priority="low",
        requires_approval=True
    )
