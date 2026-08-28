"""Deterministic fixture provider. Not an LLM. Not a source of commercial truth."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any

from app.providers.llm.errors import ProviderError
from app.schemas.intent import BudgetIntent, IntentExtractionResult, ShopperIntent
from app.schemas.vocabulary import BudgetType

HERO_UTTERANCE = (
    'Farewell next week. I\'m 5\'2", hate tight clothes around my waist '
    "and have no clue what to wear. ₹2,500 max."
)
DINNER_UTTERANCE = "I need something nice for dinner."
NAVY_BUDGET_UTTERANCE = "Under ₹2000, preferably navy."
LEATHER_UTTERANCE = "₹2500 max, no leather."
FORCE_FAIL_UTTERANCE = "__PROVIDER_ERROR__"

_COMMA_THOUSANDS = re.compile(r",(?=\d{3})")
_WHITESPACE = re.compile(r"\s+")


def normalize_utterance(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).strip().lower()
    value = value.replace("₹", "rs ")
    value = value.replace("‘", "'").replace("’", "'").replace("`", "'")
    value = value.replace("“", '"').replace("”", '"')
    value = _COMMA_THOUSANDS.sub("", value)
    return _WHITESPACE.sub(" ", value)


def _unknown_extraction() -> IntentExtractionResult:
    return IntentExtractionResult(
        intent=ShopperIntent(),
        confidence=0.2,
        missing_fields=[
            "occasion",
            "budget",
            "height",
            "usual_size",
            "fit_preferences",
            "style_preferences",
            "goal",
        ],
        ambiguities=["utterance_not_in_stub_fixtures"],
    )


def _fixtures() -> dict[str, IntentExtractionResult]:
    hero = IntentExtractionResult(
        intent=ShopperIntent(
            occasion="farewell",
            budget=BudgetIntent(amount=Decimal("2500"), type=BudgetType.HARD, currency="INR"),
            height="5ft2",
            fit_preferences=["relaxed_waist"],
            # Demo fixture only — not inferred from the sentence. Matches spec §6 / CUS-001.
            style_preferences=["elegant", "youthful"],
            goal="complete_outfit",
        ),
        confidence=0.91,
        missing_fields=["usual_size"],
        ambiguities=[],
    )
    dinner = IntentExtractionResult(
        intent=ShopperIntent(occasion="dinner"),
        confidence=0.48,
        missing_fields=["budget", "height", "usual_size", "fit_preferences", "goal"],
        ambiguities=["nice_is_underspecified"],
    )
    navy = IntentExtractionResult(
        intent=ShopperIntent(
            budget=BudgetIntent(amount=Decimal("2000"), type=BudgetType.HARD, currency="INR"),
            colour_preferences=["navy"],
        ),
        confidence=0.82,
        missing_fields=["occasion", "usual_size", "goal"],
        ambiguities=[],
    )
    leather = IntentExtractionResult(
        intent=ShopperIntent(
            budget=BudgetIntent(amount=Decimal("2500"), type=BudgetType.HARD, currency="INR"),
            excluded_materials=["leather"],
        ),
        confidence=0.88,
        missing_fields=["occasion", "usual_size", "goal"],
        ambiguities=[],
    )
    return {
        normalize_utterance(HERO_UTTERANCE): hero,
        normalize_utterance(DINNER_UTTERANCE): dinner,
        normalize_utterance(NAVY_BUDGET_UTTERANCE): navy,
        normalize_utterance(LEATHER_UTTERANCE): leather,
    }


class StubLLMProvider:
    """Maps known utterances to fixtures. Unknown utterances stay unknown."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._fixtures = _fixtures()

    def complete_structured(
        self,
        *,
        schema_name: str,
        instructions: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        _ = instructions
        if self._fail:
            raise ProviderError("stub_forced_failure", "StubLLMProvider is in fail mode.")
        if schema_name != "intent_extraction":
            raise ProviderError("unsupported_schema", f"Unsupported schema: {schema_name}")
        message = input_payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ProviderError("missing_message", "input_payload.message is required.")
        if normalize_utterance(message) == normalize_utterance(FORCE_FAIL_UTTERANCE):
            raise ProviderError("stub_forced_failure", "StubLLMProvider refused this utterance.")
        fixture = self._fixtures.get(normalize_utterance(message), _unknown_extraction())
        return fixture.model_dump(mode="json")
