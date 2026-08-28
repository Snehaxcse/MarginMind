"""LLM provider abstraction.

MVP live implementation (later milestone): Google Gemini API, free tier.
Deterministic fallback: StubLLMProvider. Do not add a Gemini client here yet.

Consequential reasoning must return structured objects. Callers validate
those objects against schemas. The provider is never a source of prices,
inventory, SKUs, offer eligibility, or policy outcomes.
"""

from typing import Any, Protocol


class LLMProvider(Protocol):
    def complete_structured(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a dict. The orchestrator must Pydantic-validate it."""
        ...
