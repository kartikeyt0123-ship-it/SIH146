"""Isolation Forest bundle: training, freezing a reference distribution, inference."""

from .bundle import BUNDLE_FORMAT, ModelBundle, bundle_path, load_bundle  # noqa: F401
from .infer import ScoreResult, score_addresses  # noqa: F401
