"""Deterministic Policy Engine.

validate_action(action, context) -> PolicyDecision

The LLM cannot override this result. Checks use database-backed prices,
inventory, margin, offers, budgets, and approval state.
"""
