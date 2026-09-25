"""The model bundle: what gets saved, and the rules for loading it back.

A bundle holds the fitted Isolation Forest, the preprocessing fitted with it, the frozen
reference score distribution, and the manifest describing how it was made. Preprocessing
travels with the model so inference cannot silently differ from training.

Loading is deliberately restrictive. joblib unpickling executes code, so a bundle is
loaded only from the project's own model directory and only if its manifest and integrity
digest match. The application never loads a user-supplied model file.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .. import config

#: Bundle layout version. A mismatch is a hard failure, never a silent fallback.
BUNDLE_FORMAT = "chainlens-model/v1"

MODEL_FILE = "model.joblib"
MANIFEST_FILE = "manifest.json"
REFERENCE_FILE = "reference_scores.npy"


class ModelLoadError(RuntimeError):
    """Raised when a bundle is absent, untrusted or does not match its manifest."""


@dataclass(slots=True)
class ModelBundle:
    version: str
    estimator: Any
    preprocessor: Any
    feature_order: list[str]
    reference_scores: np.ndarray
    manifest: dict[str, Any]

    @property
    def feature_schema_version(self) -> str:
        return str(self.manifest.get("feature_schema_version", ""))

    def anomaly_scores(self, matrix: np.ndarray) -> np.ndarray:
        """Anomaly score where **larger always means more unusual**.

        scikit-learn's ``score_samples`` returns higher values for more normal points, so
        it is negated here once, at the single place scores are produced.
        """
        transformed = self.preprocessor.transform(matrix)
        return -self.estimator.score_samples(transformed)

    def percentiles(self, scores: np.ndarray) -> np.ndarray:
        """Rank scores against the frozen reference distribution, as 0-100.

        Ties resolve to the midpoint of the tied block so that identical behaviour
        receives an identical percentile regardless of input ordering.
        """
        reference = self.reference_scores
        if reference.size == 0:
            return np.full(scores.shape, np.nan)
        left = np.searchsorted(reference, scores, side="left")
        right = np.searchsorted(reference, scores, side="right")
        return ((left + right) / 2.0) / reference.size * 100.0


def digest_of(paths: list[Path]) -> str:
    """SHA-256 over the bundle's files, for the integrity check and the export manifest."""
    sha = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        sha.update(path.name.encode("utf-8"))
        sha.update(path.read_bytes())
    return sha.hexdigest()


def bundle_path(name: str = "default") -> Path:
    return config.MODEL_DIR / name


def save_bundle(
    directory: Path,
    estimator: Any,
    preprocessor: Any,
    reference_scores: np.ndarray,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump({"estimator": estimator, "preprocessor": preprocessor},
                directory / MODEL_FILE, compress=3)
    np.save(directory / REFERENCE_FILE, np.sort(np.asarray(reference_scores, dtype=float)))
    manifest = {**manifest, "bundle_format": BUNDLE_FORMAT}
    # The digest covers the model and reference files; it is written into the manifest
    # afterwards, so it is not a hash of itself.
    manifest["integrity_sha256"] = digest_of(
        [directory / MODEL_FILE, directory / REFERENCE_FILE])
    (directory / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def load_bundle(name: str = "default", directory: Path | None = None) -> ModelBundle:
    """Load a trusted bundle. Raises :class:`ModelLoadError` rather than degrading."""
    target = directory or bundle_path(name)
    try:
        resolved = target.resolve()
        model_root = config.MODEL_DIR.resolve()
        resolved.relative_to(model_root)
    except (ValueError, OSError) as exc:
        raise ModelLoadError(
            f"Refusing to load a model from outside the project model directory: {target}"
        ) from exc

    manifest_path = resolved / MANIFEST_FILE
    model_path = resolved / MODEL_FILE
    reference_path = resolved / REFERENCE_FILE
    if not (manifest_path.exists() and model_path.exists() and reference_path.exists()):
        raise ModelLoadError(
            f"No complete model bundle at {resolved}. Train one with: "
            f"python -m chainlens.ml.train")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("bundle_format") != BUNDLE_FORMAT:
        raise ModelLoadError(
            f"Model bundle format {manifest.get('bundle_format')!r} does not match the "
            f"expected {BUNDLE_FORMAT!r}.")

    expected = manifest.get("integrity_sha256")
    actual = digest_of([model_path, reference_path])
    if expected and expected != actual:
        raise ModelLoadError(
            "Model bundle failed its integrity check; it does not match its manifest.")

    payload = joblib.load(model_path)
    return ModelBundle(
        version=str(manifest.get("model_version", "unknown")),
        estimator=payload["estimator"],
        preprocessor=payload["preprocessor"],
        feature_order=list(manifest.get("feature_order", [])),
        reference_scores=np.load(reference_path),
        manifest=manifest,
    )
