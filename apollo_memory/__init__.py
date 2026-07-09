"""
apollo_memory — Persistent user profile and session memory for Apollo.

Stores structured financial facts about the user across sessions so Apollo
can give grounded, personalised answers instead of generic advice.

Kinds (all stored in AgentRAG with high priority):
  user_profile    (priority 0.95) — income, debts, holdings, risk, goals
  user_preference (priority 0.90) — response style, depth, topics
  financial_event (priority 0.85) — logged events, decisions, changes

Usage:
    from apollo_memory import store, extractor, context
"""
