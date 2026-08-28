"""LLM provider package.

Protocol: LLMProvider. Planned MVP live adapter: Gemini (free tier).
Fallback: StubLLMProvider. Structured outputs only. No Gemini client in M3.
"""

from app.providers.llm.base import LLMProvider
from app.providers.llm.errors import ProviderError
from app.providers.llm.stub import StubLLMProvider

__all__ = ["LLMProvider", "ProviderError", "StubLLMProvider"]
