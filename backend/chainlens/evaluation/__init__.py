"""Offline evaluation. The only component that ever opens a ground-truth file."""

from .evaluate import (  # noqa: F401
    SUSPICIOUS_PATTERN_MAPPING,
    EvaluationReport,
    evaluate_predictions,
)
