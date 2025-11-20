"""
apollo_console - Apollo Console Module

Provides conversation logging and console API utilities.
"""

from .logging import (
    start_conversation,
    append_turn,
    load_conversation,
    list_conversations,
    ConversationTurn,
    ConversationSummary
)

__all__ = [
    'start_conversation',
    'append_turn',
    'load_conversation',
    'list_conversations',
    'ConversationTurn',
    'ConversationSummary'
]
