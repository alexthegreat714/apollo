"""
replay_retriever.py - Retrieves past reasoning for context.

Provides memory context to the deepmode pipeline.
"""

import logging
from typing import Dict, Any, List, Optional

from apollo_memory.short_term_store import ShortTermStore

logger = logging.getLogger(__name__)


class ReplayRetriever:
    """
    Retrieves relevant past conversations for context enhancement.
    """

    def __init__(self, store: ShortTermStore = None):
        """
        Initialize the replay retriever.

        Args:
            store: Short-term memory store
        """
        self.store = store or ShortTermStore()

    def retrieve(
        self,
        query: str,
        intent: str = None,
        max_memories: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant past conversations.

        Args:
            query: Current query
            intent: Current intent
            max_memories: Maximum memories to return

        Returns:
            List of relevant memory entries
        """
        memories = []

        # Strategy 1: Get by intent match
        if intent:
            intent_matches = self.store.get_by_intent(intent, n=max_memories)
            memories.extend(intent_matches)

        # Strategy 2: Keyword search
        if len(memories) < max_memories:
            keyword_matches = self.store.search(query, n=max_memories - len(memories))
            for match in keyword_matches:
                if match not in memories:
                    memories.append(match)

        # Strategy 3: Recent entries as fallback
        if len(memories) < max_memories:
            recent = self.store.get_recent(n=max_memories - len(memories))
            for entry in recent:
                if entry not in memories:
                    memories.append(entry)

        return memories[:max_memories]

    def format_for_context(self, memories: List[Dict[str, Any]]) -> str:
        """
        Format memories for inclusion in prompt context.

        Args:
            memories: Memory entries

        Returns:
            Formatted string
        """
        if not memories:
            return ""

        lines = ["[Previous Context]"]

        for i, mem in enumerate(memories, 1):
            summary = mem.get("summary", "")
            if summary:
                lines.append(f"{i}. {summary}")

        return "\n".join(lines)

    def get_context_chunks(self, memories: List[Dict[str, Any]]) -> List[str]:
        """
        Extract context chunks from memories.

        Args:
            memories: Memory entries

        Returns:
            List of context chunks
        """
        chunks = []
        for mem in memories:
            mem_chunks = mem.get("context_chunks", [])
            chunks.extend(mem_chunks)
        return chunks

    def analyze_patterns(self) -> Dict[str, Any]:
        """
        Analyze patterns in stored memories.

        Returns:
            Pattern analysis
        """
        stats = self.store.get_stats()

        # Analyze intent distribution
        intent_dist = stats.get("intent_distribution", {})

        # Find dominant intent
        if intent_dist:
            dominant_intent = max(intent_dist.items(), key=lambda x: x[1])
        else:
            dominant_intent = ("unknown", 0)

        # Calculate density of repeated queries
        all_queries = [e.get("query", "") for e in self.store._entries]
        query_words = {}
        for q in all_queries:
            for word in q.lower().split():
                if len(word) > 3:
                    query_words[word] = query_words.get(word, 0) + 1

        # Top repeated terms
        top_terms = sorted(query_words.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total_memories": stats.get("total_entries", 0),
            "dominant_intent": dominant_intent[0],
            "intent_distribution": intent_dist,
            "top_query_terms": dict(top_terms),
            "session_id": stats.get("session_id")
        }


# Global retriever instance
_retriever = None


def get_replay_retriever() -> ReplayRetriever:
    """Get or create the global replay retriever."""
    global _retriever
    if _retriever is None:
        _retriever = ReplayRetriever()
    return _retriever
