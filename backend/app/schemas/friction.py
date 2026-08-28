"""Typed friction diagnosis outputs. Closed vocabulary only. No FIX / actions."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vocabulary import FrictionType, SessionEventType


class SessionSignalInput(BaseModel):
    """Structured session activity. Stored as a session event + evidence row."""

    model_config = ConfigDict(extra="forbid")

    event_type: SessionEventType
    sku: str | None = None
    sku_b: str | None = None
    product_ref_id: str | None = None
    reason: str | None = None
    dimension: str | None = None
    choice_count: int | None = Field(default=None, ge=0)
    text: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    size: str | None = None


class FrictionDiagnosisResult(BaseModel):
    """WHAT / WHY only. FIX belongs to the Growth Decision Engine."""

    model_config = ConfigDict(extra="forbid")

    friction_type: FrictionType
    confidence: Decimal = Field(ge=0, le=1)
    evidence_ref_ids: list[str] = Field(min_length=1)
    reason_codes: list[str] = Field(default_factory=list)
    summary: str
    why: str
    status: str
    ref_id: str | None = None


class FrictionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ref_id: str
    primary: FrictionDiagnosisResult
    secondary: list[FrictionDiagnosisResult] = Field(default_factory=list)
    diagnoses: list[FrictionDiagnosisResult]
