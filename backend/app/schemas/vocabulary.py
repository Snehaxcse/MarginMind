"""Closed vocabularies for MarginMind.

These sets are part of the architecture contract. Engines, API, frontend,
and eval must not invent values outside these enums.

Stdlib only in Milestone 0 (no Pydantic dependency installed yet).
"""

from enum import Enum


class BoundedAction(str, Enum):
    RECOMMEND = "RECOMMEND"
    BUILD_BASKET = "BUILD_BASKET"
    GUIDE_CONFIDENCE = "GUIDE_CONFIDENCE"
    SIMPLIFY_CHOICES = "SIMPLIFY_CHOICES"
    FIND_ALTERNATIVE = "FIND_ALTERNATIVE"
    REBUILD_BASKET = "REBUILD_BASKET"
    APPLY_AUTHORIZED_OFFER = "APPLY_AUTHORIZED_OFFER"
    NO_UPSELL = "NO_UPSELL"
    REQUEST_CHECKOUT = "REQUEST_CHECKOUT"
    STOP = "STOP"


class FrictionType(str, Enum):
    FIT_UNCERTAINTY = "FIT_UNCERTAINTY"
    STYLE_UNCERTAINTY = "STYLE_UNCERTAINTY"
    COLOUR_UNCERTAINTY = "COLOUR_UNCERTAINTY"
    BUDGET_MISMATCH = "BUDGET_MISMATCH"
    PRICE_HESITATION = "PRICE_HESITATION"
    SIZE_UNAVAILABLE = "SIZE_UNAVAILABLE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    CHOICE_OVERLOAD = "CHOICE_OVERLOAD"
    BASKET_INCOMPLETE = "BASKET_INCOMPLETE"
    CATALOGUE_GAP = "CATALOGUE_GAP"
    CHECKOUT_HESITATION = "CHECKOUT_HESITATION"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class ActionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    BLOCKED = "BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class AutonomyLevel(str, Enum):
    AUTO = "AUTO"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    NEVER_AUTONOMOUS = "NEVER_AUTONOMOUS"


class PolicyVerdict(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class PolicyCheckName(str, Enum):
    HARD_BUDGET = "HARD_BUDGET"
    INVENTORY = "INVENTORY"
    SKU_EXISTS = "SKU_EXISTS"
    PRODUCT_ACTIVE = "PRODUCT_ACTIVE"
    VARIANT_ACTIVE = "VARIANT_ACTIVE"
    MARGIN = "MARGIN"
    AUTHORIZED_OFFER = "AUTHORIZED_OFFER"
    OFFER_ACTIVE = "OFFER_ACTIVE"
    OFFER_ELIGIBILITY = "OFFER_ELIGIBILITY"
    OFFER_STACKING = "OFFER_STACKING"
    MERCHANT_RESTRICTIONS = "MERCHANT_RESTRICTIONS"
    CUSTOMER_APPROVAL_REQUIRED = "CUSTOMER_APPROVAL_REQUIRED"
    NO_SILENT_BASKET_CHANGE = "NO_SILENT_BASKET_CHANGE"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "N/A"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    GRANTED = "granted"
    REJECTED = "rejected"


class ConstraintKind(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class BudgetType(str, Enum):
    HARD = "HARD"
    FLEXIBLE = "FLEXIBLE"


class Actor(str, Enum):
    CUSTOMER = "customer"
    MERCHANT = "merchant"
    SYSTEM = "system"


class SessionEventType(str, Enum):
    CUSTOMER_MESSAGE = "customer_message"
    INTENT_EXTRACTED = "intent_extracted"
    PROVIDER_FAILED = "provider_failed"
    PRODUCT_VIEWED = "product_viewed"
    SIZE_GUIDE_OPENED = "size_guide_opened"
    PRODUCT_COMPARED = "product_compared"
    RECOMMENDATION_REJECTED = "recommendation_rejected"
    REJECTION_REASON_RECORDED = "rejection_reason_recorded"
    BASKET_UPDATED = "basket_updated"
    BASKET_OVER_HARD_BUDGET = "basket_over_hard_budget"
    SIZE_UNAVAILABLE_OBSERVED = "size_unavailable_observed"
    PRODUCT_OOS_OBSERVED = "product_oos_observed"
    CHOICES_SHOWN = "choices_shown"
    CHECKOUT_STARTED = "checkout_started"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    FIT_QUESTION_ASKED = "fit_question_asked"
    PRICE_QUESTION_ASKED = "price_question_asked"


class EvidenceKind(str, Enum):
    CUSTOMER_MESSAGE = "customer_message"
    SESSION_SIGNAL = "session_signal"
    BASKET_SNAPSHOT = "basket_snapshot"
    INVENTORY_SNAPSHOT = "inventory_snapshot"
    FRICTION_EVALUATION = "friction_evaluation"
    POLICY_DECISION = "policy_decision"


class PolicyCode(str, Enum):
    RESPECT_HARD_BUDGET = "RESPECT_HARD_BUDGET"
    ONLY_REAL_INVENTORY = "ONLY_REAL_INVENTORY"
    ONLY_AUTHORISED_OFFERS = "ONLY_AUTHORISED_OFFERS"
    NO_SILENT_BASKET_CHANGES = "NO_SILENT_BASKET_CHANGES"
    APPROVAL_REQUIRED_BEFORE_CHECKOUT = "APPROVAL_REQUIRED_BEFORE_CHECKOUT"
    MIN_MARGIN_PERCENT = "MIN_MARGIN_PERCENT"
    MAX_DISCOUNT_PERCENT = "MAX_DISCOUNT_PERCENT"
    OFFER_STACKING_ALLOWED = "OFFER_STACKING_ALLOWED"


class PolicyValueType(str, Enum):
    BOOLEAN = "BOOLEAN"
    NUMERIC = "NUMERIC"
    TEXT = "TEXT"


class DiscountType(str, Enum):
    PERCENT = "PERCENT"
    AMOUNT = "AMOUNT"


class ProductCategory(str, Enum):
    TOPS = "tops"
    TROUSERS = "trousers"
    DRESSES = "dresses"
    ACCESSORIES = "accessories"


class CheckoutState(str, Enum):
    DRAFT_BASKET = "DRAFT_BASKET"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED_UNVERIFIED = "APPROVED_UNVERIFIED"
    REVALIDATING = "REVALIDATING"
    READY_FOR_PAYMENT = "READY_FOR_PAYMENT"
    REVALIDATION_FAILED = "REVALIDATION_FAILED"
    ORDER_CREATED = "ORDER_CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    VERIFIED = "VERIFIED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    ABANDONED = "ABANDONED"
