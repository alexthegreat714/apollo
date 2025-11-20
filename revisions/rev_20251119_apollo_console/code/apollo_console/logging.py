"""
logging.py - Conversation Logging for Apollo Console

File-based conversation logging using JSONL format.
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path


# Default log directory
CONVERSATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "logs",
    "conversations"
)


@dataclass
class ConversationTurn:
    """A single turn in a conversation."""
    conversation_id: str
    timestamp_utc: str
    role: str  # "user" or "assistant"
    message: str
    retrieved_kinds: Optional[List[str]] = None
    retrieved_ids: Optional[List[str]] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ConversationSummary:
    """Summary information about a conversation."""
    conversation_id: str
    first_timestamp: str
    last_timestamp: str
    turn_count: int
    preview: str  # First user message preview


def _get_conversation_path(conversation_id: str) -> str:
    """Get the file path for a conversation."""
    return os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.jsonl")


def _ensure_dir():
    """Ensure the conversations directory exists."""
    Path(CONVERSATIONS_DIR).mkdir(parents=True, exist_ok=True)


def start_conversation() -> str:
    """
    Create a new conversation and return its ID.

    Returns:
        New conversation ID
    """
    _ensure_dir()

    # Generate unique conversation ID with timestamp prefix
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    conversation_id = f"conv_{timestamp}_{unique_id}"

    # Create empty file to reserve the ID
    path = _get_conversation_path(conversation_id)
    Path(path).touch()

    return conversation_id


def append_turn(
    conversation_id: str,
    role: str,
    message: str,
    retrieved_kinds: Optional[List[str]] = None,
    retrieved_ids: Optional[List[str]] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Append a turn to a conversation.

    Args:
        conversation_id: The conversation ID
        role: "user" or "assistant"
        message: The message content
        retrieved_kinds: List of document kinds retrieved
        retrieved_ids: List of document IDs retrieved
        notes: Additional notes about the turn
        metadata: Additional metadata
    """
    _ensure_dir()

    turn = ConversationTurn(
        conversation_id=conversation_id,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        role=role,
        message=message,
        retrieved_kinds=retrieved_kinds,
        retrieved_ids=retrieved_ids,
        notes=notes,
        metadata=metadata
    )

    path = _get_conversation_path(conversation_id)

    # Convert to dict and write as JSONL
    turn_dict = asdict(turn)
    # Remove None values for cleaner output
    turn_dict = {k: v for k, v in turn_dict.items() if v is not None}

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(turn_dict, ensure_ascii=False) + "\n")


def load_conversation(conversation_id: str) -> List[Dict[str, Any]]:
    """
    Load all turns from a conversation.

    Args:
        conversation_id: The conversation ID

    Returns:
        List of turn dictionaries
    """
    path = _get_conversation_path(conversation_id)

    if not os.path.exists(path):
        return []

    turns = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return turns


def list_conversations(limit: int = 20) -> List[Dict[str, Any]]:
    """
    List recent conversations with summary info.

    Args:
        limit: Maximum number of conversations to return

    Returns:
        List of conversation summaries
    """
    _ensure_dir()

    # Get all conversation files
    conv_dir = Path(CONVERSATIONS_DIR)
    if not conv_dir.exists():
        return []

    files = list(conv_dir.glob("conv_*.jsonl"))

    # Sort by modification time (most recent first)
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    summaries = []
    for file_path in files[:limit]:
        conversation_id = file_path.stem

        # Read turns
        turns = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if not turns:
            continue

        # Build summary
        first_user_msg = ""
        for turn in turns:
            if turn.get("role") == "user":
                first_user_msg = turn.get("message", "")[:100]
                break

        summary = ConversationSummary(
            conversation_id=conversation_id,
            first_timestamp=turns[0].get("timestamp_utc", ""),
            last_timestamp=turns[-1].get("timestamp_utc", ""),
            turn_count=len(turns),
            preview=first_user_msg
        )

        summaries.append(asdict(summary))

    return summaries


def get_conversation_exists(conversation_id: str) -> bool:
    """Check if a conversation exists."""
    path = _get_conversation_path(conversation_id)
    return os.path.exists(path)


if __name__ == "__main__":
    # Quick test
    print("Testing conversation logging...")

    # Start a new conversation
    conv_id = start_conversation()
    print(f"Created conversation: {conv_id}")

    # Add some turns
    append_turn(conv_id, "user", "What is inflation?")
    append_turn(
        conv_id,
        "assistant",
        "Inflation is the rate at which prices increase over time.",
        retrieved_kinds=["education", "macro"],
        retrieved_ids=["doc1", "doc2"],
        notes="from RAG"
    )
    append_turn(conv_id, "user", "How does it affect investments?")
    append_turn(
        conv_id,
        "assistant",
        "Inflation erodes purchasing power, which affects investment returns.",
        metadata={"model": "Fino1-8B_Q6_K", "latency_ms": 1234}
    )

    # Load and print
    turns = load_conversation(conv_id)
    print(f"\nLoaded {len(turns)} turns:")
    for turn in turns:
        print(f"  [{turn['role']}]: {turn['message'][:50]}...")

    # List conversations
    convs = list_conversations(limit=5)
    print(f"\nRecent conversations: {len(convs)}")
    for c in convs:
        print(f"  {c['conversation_id']}: {c['turn_count']} turns")

    print("\nTest complete!")
