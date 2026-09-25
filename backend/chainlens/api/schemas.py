"""Request and response models for the local API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..ingest.contract import ValidationMode


class ErrorDetail(BaseModel):
    """Structured error body. ``code`` is stable; ``message`` is for the analyst."""

    code: str
    message: str
    row_number: int | None = None
    field: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class CaseOut(BaseModel):
    id: str
    title: str
    description: str
    state: str
    created_at: str
    updated_at: str
    dataset_count: int = 0
    run_count: int = 0
    alert_count: int = 0
    latest_run_id: str | None = None


class ImportRequest(BaseModel):
    validation_mode: ValidationMode = ValidationMode.COMPATIBILITY
    fee_tolerance_sats: int = Field(default=1, ge=0, le=100_000_000)


class RunCreate(BaseModel):
    dataset_id: str
    detector_config: dict[str, Any] | None = None
    model_name: str = "default"


class ReviewUpdate(BaseModel):
    """Version-checked review change. ``expected_state`` guards against a stale view."""

    state: str
    reason: str = Field(default="", max_length=2000)
    expected_state: str | None = None
    actor: str = "analyst"


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    author: str = "analyst"


class ClusterDecision(BaseModel):
    decision: str
    note: str = Field(default="", max_length=2000)
    actor: str = "analyst"


class ExportRequest(BaseModel):
    run_id: str
    alert_ids: list[str] | None = None
    actor: str = "local"


class SensitivityRequest(BaseModel):
    """A what-if comparison against an immutable alert.

    Only the ranking is recalculated. The model is not refitted and features are not
    recomputed, and the response states exactly that.
    """

    drop_model_component: bool = False
    drop_rule_component: bool = False
    override_rule_severity: float | None = Field(default=None, ge=0, le=100)
