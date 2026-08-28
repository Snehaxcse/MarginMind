"""Evidence store and append-only audit / agent trace.

Consequential decisions reference evidence ids. Merchant traces are
projections of audit_events for a session.
"""

from app.layers.evidence.service import record_audit, record_customer_message, record_evidence

__all__ = ["record_audit", "record_customer_message", "record_evidence"]
