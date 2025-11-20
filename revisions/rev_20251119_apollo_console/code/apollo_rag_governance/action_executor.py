"""
action_executor.py - Executes approved RAG improvement actions.

All actions require explicit user approval before execution.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from apollo_rag_governance.actions import (
    RAGImprovementAction,
    ActionType,
    ActionStatus,
    ActionProposal
)

logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    Executes approved RAG improvement actions.

    All actions are logged to rag_actions.log.
    Actions MUST NOT auto-apply unless explicitly approved.
    """

    def __init__(self, log_path: Optional[Path] = None):
        """
        Initialize the action executor.

        Args:
            log_path: Path to the action log file
        """
        root = Path(__file__).resolve().parents[1]
        default_log = root / "logs" / "rag_actions.log"
        self.log_path = Path(log_path) if log_path else default_log
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Pending proposals storage
        self._pending_proposals: Dict[str, ActionProposal] = {}

        # Load KIND_WEIGHTS reference
        try:
            from apollo_rag.retriever import KIND_WEIGHTS, update_kind_weights
            self._kind_weights = KIND_WEIGHTS
            self._update_weights_func = update_kind_weights
        except ImportError:
            self._kind_weights = {}
            self._update_weights_func = None

    def _log_action(self, action: RAGImprovementAction, result: Dict[str, Any]) -> None:
        """Log action execution to file."""
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "action_id": action.action_id,
            "action_type": action.action_type,
            "status": action.status,
            "result": result
        }
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to log action: {e}")

    def store_proposal(self, proposal: ActionProposal) -> str:
        """
        Store a proposal for later approval.

        Args:
            proposal: The action proposal

        Returns:
            Proposal ID
        """
        self._pending_proposals[proposal.proposal_id] = proposal
        logger.info(f"Stored proposal {proposal.proposal_id} with {len(proposal.actions)} actions")
        return proposal.proposal_id

    def get_pending_proposals(self) -> List[Dict[str, Any]]:
        """Get all pending proposals."""
        return [p.to_dict() for p in self._pending_proposals.values()]

    def get_proposal(self, proposal_id: str) -> Optional[ActionProposal]:
        """Get a specific proposal by ID."""
        return self._pending_proposals.get(proposal_id)

    def approve_action(self, proposal_id: str, action_id: str) -> bool:
        """
        Approve a specific action within a proposal.

        Args:
            proposal_id: Proposal ID
            action_id: Action ID

        Returns:
            True if approved successfully
        """
        proposal = self._pending_proposals.get(proposal_id)
        if not proposal:
            return False

        for action in proposal.actions:
            if action.action_id == action_id:
                action.approve()
                logger.info(f"Approved action {action_id}")
                return True
        return False

    def reject_action(self, proposal_id: str, action_id: str) -> bool:
        """
        Reject a specific action within a proposal.

        Args:
            proposal_id: Proposal ID
            action_id: Action ID

        Returns:
            True if rejected successfully
        """
        proposal = self._pending_proposals.get(proposal_id)
        if not proposal:
            return False

        for action in proposal.actions:
            if action.action_id == action_id:
                action.reject()
                logger.info(f"Rejected action {action_id}")
                return True
        return False

    def delete_proposal(self, proposal_id: str) -> bool:
        """
        Delete a proposal entirely.

        Args:
            proposal_id: Proposal ID

        Returns:
            True if deleted successfully
        """
        if proposal_id in self._pending_proposals:
            del self._pending_proposals[proposal_id]
            logger.info(f"Deleted proposal {proposal_id}")
            return True
        return False

    def execute_action(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """
        Execute a single approved action.

        Args:
            action: The action to execute

        Returns:
            Execution result
        """
        if action.status != ActionStatus.APPROVED.value:
            return {
                "success": False,
                "error": f"Action must be approved before execution. Current status: {action.status}"
            }

        result = {"success": False, "error": "Unknown action type"}

        try:
            if action.action_type == ActionType.ADJUST_WEIGHTS.value:
                result = self._execute_weight_adjustment(action)
            elif action.action_type == ActionType.ADD_SYNONYMS.value:
                result = self._execute_add_synonyms(action)
            elif action.action_type == ActionType.REEMBED.value:
                result = self._execute_reembed(action)
            elif action.action_type == ActionType.RE_CHUNK.value:
                result = self._execute_rechunk(action)
            elif action.action_type == ActionType.RE_INGEST_FILE.value:
                result = self._execute_reingest(action)
            elif action.action_type == ActionType.RE_INGEST_CATEGORY.value:
                result = self._execute_reingest_category(action)
            elif action.action_type == ActionType.EXPAND_CATEGORY.value:
                result = self._execute_expand_category(action)
            elif action.action_type == ActionType.FLAG_STALE.value:
                result = self._execute_flag_stale(action)
            else:
                result = {"success": False, "error": f"Unknown action type: {action.action_type}"}

            # Update action status
            if result.get("success"):
                action.mark_applied()
            else:
                action.mark_failed()

        except Exception as e:
            logger.exception(f"Action execution failed: {e}")
            result = {"success": False, "error": str(e)}
            action.mark_failed()

        # Log the result
        self._log_action(action, result)
        return result

    def execute_approved_actions(self, proposal_id: str) -> List[Dict[str, Any]]:
        """
        Execute all approved actions in a proposal.

        Args:
            proposal_id: Proposal ID

        Returns:
            List of execution results
        """
        proposal = self._pending_proposals.get(proposal_id)
        if not proposal:
            return [{"success": False, "error": "Proposal not found"}]

        results = []
        for action in proposal.get_approved_actions():
            result = self.execute_action(action)
            results.append({
                "action_id": action.action_id,
                "action_type": action.action_type,
                **result
            })

        return results

    # ==========================================================================
    # Action Implementations
    # ==========================================================================

    def _execute_weight_adjustment(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a weight adjustment action."""
        payload = action.payload
        kind = payload.get("kind")
        proposed_weight = payload.get("proposed_weight")

        if not kind or proposed_weight is None:
            return {"success": False, "error": "Missing kind or proposed_weight"}

        if self._update_weights_func:
            self._update_weights_func({kind: proposed_weight})
            return {
                "success": True,
                "message": f"Updated weight for '{kind}' to {proposed_weight}"
            }
        else:
            return {"success": False, "error": "Weight update function not available"}

    def _execute_add_synonyms(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute an add-synonyms action."""
        payload = action.payload
        target_term = payload.get("target_term")
        synonyms = payload.get("synonyms", [])

        if not target_term or not synonyms:
            return {"success": False, "error": "Missing target_term or synonyms"}

        # Store synonyms in a config file for future use
        synonyms_path = Path(__file__).resolve().parents[1] / "config" / "synonyms.json"
        synonyms_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {}
        if synonyms_path.exists():
            try:
                with open(synonyms_path, "r") as f:
                    existing = json.load(f)
            except Exception:
                pass

        existing[target_term] = list(set(existing.get(target_term, []) + synonyms))

        with open(synonyms_path, "w") as f:
            json.dump(existing, f, indent=2)

        return {
            "success": True,
            "message": f"Added {len(synonyms)} synonyms for '{target_term}'"
        }

    def _execute_reembed(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a re-embedding action."""
        payload = action.payload
        target = payload.get("target")
        scope = payload.get("scope", "category")

        # This would trigger a re-embedding process
        # For now, we log the intent and return success
        return {
            "success": True,
            "message": f"Queued re-embedding for {scope}: {target}",
            "note": "Re-embedding requires manual execution via ingestion pipeline"
        }

    def _execute_rechunk(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a re-chunking action."""
        payload = action.payload
        target = payload.get("target")
        proposed_size = payload.get("proposed_chunk_size")

        return {
            "success": True,
            "message": f"Queued re-chunking for '{target}' with size {proposed_size}",
            "note": "Re-chunking requires manual execution via ingestion pipeline"
        }

    def _execute_reingest(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a re-ingestion action for a specific file."""
        payload = action.payload
        target = payload.get("target")

        return {
            "success": True,
            "message": f"Queued re-ingestion for: {target}",
            "note": "Re-ingestion requires manual execution"
        }

    def _execute_reingest_category(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a re-ingestion action for a category."""
        payload = action.payload
        target = payload.get("target")

        return {
            "success": True,
            "message": f"Queued category re-ingestion: {target}",
            "note": "Category re-ingestion requires manual execution"
        }

    def _execute_expand_category(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a category expansion action."""
        payload = action.payload
        target = payload.get("target")

        return {
            "success": True,
            "message": f"Flagged category for expansion: {target}",
            "note": "Expansion requires manual document addition"
        }

    def _execute_flag_stale(self, action: RAGImprovementAction) -> Dict[str, Any]:
        """Execute a stale document flag action."""
        payload = action.payload
        document_id = payload.get("document_id")
        age_days = payload.get("age_days")

        # Log the stale flag
        stale_log = self.log_path.parent / "stale_documents.jsonl"
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "document_id": document_id,
            "age_days": age_days,
            "action_id": action.action_id
        }

        with stale_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return {
            "success": True,
            "message": f"Flagged document as stale: {document_id}"
        }


# Global executor instance
_executor = None


def get_executor() -> ActionExecutor:
    """Get or create the global action executor."""
    global _executor
    if _executor is None:
        _executor = ActionExecutor()
    return _executor
