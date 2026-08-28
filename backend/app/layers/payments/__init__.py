"""Payment provider interface.

create_order only after policy + revalidation PASS.
Webhooks are signature-verified and idempotent.
The LLM never handles credentials.
"""
