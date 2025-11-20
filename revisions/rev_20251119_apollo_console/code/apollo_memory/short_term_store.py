"""
short_term_store.py - Session memory storage for Apollo.

Stores conversation summaries and context for replay.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import hashlib

logger = logging.getLogger(__name__)


class ShortTermStore:
    """
    Stores recent queries, answers, and context chunks for memory replay.
    """

    def __init__(
        self,
        persist_dir: str = None,
        max_entries: int = 100,
        session_id: str = None
    ):
        """
        Initialize the short-term store.

        Args:
            persist_dir: Directory for persistence
            max_entries: Maximum entries to keep
            session_id: Session identifier
        """
        root = Path(__file__).resolve().parents[1]
        self.persist_dir = Path(persist_dir) if persist_dir else root / "rag_data" / "memory"
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.max_entries = max_entries
        self.session_id = session_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        self._memory_file = self.persist_dir / f"session_{self.session_id}.jsonl"
        self._entries: List[Dict[str, Any]] = []

        # Load existing entries
        self._load()

    def _load(self) -> None:
        """Load entries from disk."""
        if self._memory_file.exists():
            try:
                with self._memory_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            self._entries.append(json.loads(line))
                logger.info(f"Loaded {len(self._entries)} memory entries")
            except Exception as e:
                logger.error(f"Failed to load memory: {e}")

    def _save_entry(self, entry: Dict[str, Any]) -> None:
        """Save a single entry to disk."""
        try:
            with self._memory_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to save memory entry: {e}")

    def store(
        self,
        query: str,
        answer: str,
        context_chunks: List[str] = None,
        intent: str = None,
        metadata: Dict[str, Any] = None
    ) -> str:
        """
        Store a conversation exchange.

        Args:
            query: User query
            answer: System answer
            context_chunks: RAG context used
            intent: Query intent
            metadata: Additional metadata

        Returns:
            Entry ID
        """
        # Generate entry ID
        entry_id = hashlib.md5(
            f"{query}{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:12]

        # Create summary
        summary = self._create_summary(query, answer)

        entry = {
            "id": entry_id,
            "ts": datetime.utcnow().isoformat() + "Z",
            "query": query,
            "answer": answer[:500],  # Truncate long answers
            "summary": summary,
            "context_chunks": context_chunks[:3] if context_chunks else [],
            "intent": intent,
            "metadata": metadata or {}
        }

        self._entries.append(entry)
        self._save_entry(entry)

        # Prune if needed
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

        logger.info(f"Stored memory entry {entry_id}")
        return entry_id

    def _create_summary(self, query: str, answer: str) -> str:
        """Create a brief summary of the exchange."""
        # Simple extractive summary
        query_part = query[:100] if len(query) > 100 else query

        # Extract first sentence of answer
        sentences = answer.split('.')
        answer_part = sentences[0] + '.' if sentences else answer[:100]

        return f"Q: {query_part} A: {answer_part}"

    def get_recent(self, n: int = 5) -> List[Dict[str, Any]]:
        """
        Get the N most recent entries.

        Args:
            n: Number of entries

        Returns:
            List of entries
        """
        return self._entries[-n:]

    def get_by_intent(self, intent: str, n: int = 5) -> List[Dict[str, Any]]:
        """
        Get entries by intent.

        Args:
            intent: Intent to filter
            n: Maximum entries

        Returns:
            List of matching entries
        """
        matches = [e for e in self._entries if e.get("intent") == intent]
        return matches[-n:]

    def search(self, query: str, n: int = 5) -> List[Dict[str, Any]]:
        """
        Search entries by query similarity (simple keyword match).

        Args:
            query: Search query
            n: Maximum results

        Returns:
            Matching entries
        """
        query_words = set(query.lower().split())

        scored = []
        for entry in self._entries:
            entry_words = set(entry.get("query", "").lower().split())
            overlap = len(query_words & entry_words)
            if overlap > 0:
                scored.append((overlap, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:n]]

    def get_all_summaries(self) -> List[str]:
        """Get all entry summaries."""
        return [e.get("summary", "") for e in self._entries]

    def clear(self) -> int:
        """
        Clear all entries.

        Returns:
            Number of entries cleared
        """
        count = len(self._entries)
        self._entries = []

        if self._memory_file.exists():
            self._memory_file.unlink()

        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        intent_counts = {}
        for entry in self._entries:
            intent = entry.get("intent", "unknown")
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

        return {
            "total_entries": len(self._entries),
            "session_id": self.session_id,
            "intent_distribution": intent_counts,
            "oldest": self._entries[0].get("ts") if self._entries else None,
            "newest": self._entries[-1].get("ts") if self._entries else None
        }
