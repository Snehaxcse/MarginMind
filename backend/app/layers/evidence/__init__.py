"""Evidence store and append-only audit.

Consequential decisions reference evidence ids. Merchant Agent Trace
(M11) is a read-only reconstruction in app.layers.trace.
"""

from app.layers.evidence.service import record_audit, record_customer_message, record_evidence

__all__ = ["record_audit", "record_customer_message", "record_evidence"]
