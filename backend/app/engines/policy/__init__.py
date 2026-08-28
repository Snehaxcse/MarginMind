"""Deterministic Policy Engine.

validate_action(proposal, session) -> PolicyDecision

Proposal is not permission. The LLM cannot override this result.
Checks use database-backed prices, inventory, margin, offers, budgets,
and exact-version approval state. This module never executes.
"""

from app.engines.policy.engine import (
    PolicyValidationResult,
    get_policy_decision,
    list_policy_decisions,
    validate_action,
)
from app.engines.policy.policies import MerchantPolicySet, load_merchant_policies

__all__ = [
    "MerchantPolicySet",
    "PolicyValidationResult",
    "get_policy_decision",
    "list_policy_decisions",
    "load_merchant_policies",
    "validate_action",
]
