"""Scoring addresses with a frozen bundle.

Inference never refits. If the bundle is missing, untrusted or its feature schema does
not match, scoring fails explicitly and the run reports "ML unavailable" - it does not
quietly fall back to treating every address as normal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..features import FEATURE_ORDER
from ..features.address_features import assert_no_identity_leak
from .bundle import ModelBundle, ModelLoadError, load_bundle


@dataclass(slots=True)
class ScoreResult:
    """Per-address anomaly scores, plus why any address went unscored."""

    scores: dict[str, float] = field(default_factory=dict)
    percentiles: dict[str, float] = field(default_factory=dict)
    unscored: dict[str, str] = field(default_factory=dict)
    model_version: str = ""
    model_status: str = "unavailable"        # "used" | "unavailable"
    model_error: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.model_status == "used"


def score_addresses(frame: pd.DataFrame, bundle: ModelBundle | None = None,
                    bundle_name: str = "default") -> ScoreResult:
    """Score an address feature frame against a frozen model bundle."""
    try:
        bundle = bundle or load_bundle(bundle_name)
    except ModelLoadError as exc:
        return ScoreResult(model_status="unavailable", model_error=str(exc))

    if list(bundle.feature_order) != list(FEATURE_ORDER):
        return ScoreResult(
            model_status="unavailable",
            model_version=bundle.version,
            model_error=(
                "MODEL_SCHEMA_MISMATCH: the bundle's feature order does not match the "
                f"current feature schema. Bundle has {len(bundle.feature_order)} "
                f"features, the code expects {len(FEATURE_ORDER)}. Retrain the model."),
            manifest=bundle.manifest,
        )

    if frame.empty:
        return ScoreResult(model_status="used", model_version=bundle.version,
                           manifest=bundle.manifest)

    assert_no_identity_leak(frame)

    # An address with a non-finite feature is reported as unscored with a stated reason,
    # never silently imputed to look normal.
    matrix = frame.to_numpy(dtype=float)
    finite = np.isfinite(matrix).all(axis=1)
    addresses = list(frame.index)

    result = ScoreResult(model_version=bundle.version, model_status="used",
                         manifest=bundle.manifest)
    for address, ok in zip(addresses, finite):
        if not ok:
            result.unscored[address] = (
                "One or more required features were not finite for this address; it is "
                "reported as unscored rather than assumed normal.")

    if finite.any():
        usable = matrix[finite]
        scores = bundle.anomaly_scores(usable)
        percentiles = bundle.percentiles(scores)
        scored_addresses = [a for a, ok in zip(addresses, finite) if ok]
        result.scores = {a: float(s) for a, s in zip(scored_addresses, scores)}
        result.percentiles = {a: float(p) for a, p in zip(scored_addresses, percentiles)}
    return result
