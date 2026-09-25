"""Train the address-level Isolation Forest.

Run:
    python -m chainlens.ml.train --input data/samples/ps3_transactions

What this does and does not claim
---------------------------------
Isolation Forest is unsupervised: it is never shown a label, so there is nothing for a
label to leak into during fitting. It learns what is *typical* for this population and
ranks addresses by how easily they are isolated from it.

Fitting and scoring the same population is transductive. Results obtained that way are
exploratory, and the evaluator labels them as such. Holding out entities is supported
with --holdout-fraction, which splits by address so that no address contributes to both
the fitted preprocessing and the held-out measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import QuantileTransformer

from .. import config
from ..db import init_db, new_id, now_iso, write_tx
from ..features import FEATURE_ORDER, FEATURE_SCHEMA_VERSION, build_feature_frame, feature_manifest
from ..features.address_features import assert_no_identity_leak
from ..graphx.model import load_case_data
from ..ingest import ValidationMode, import_dataset
from ..detectors.config import CoinJoinConfig
from ..detectors.structure import is_coinjoin_like
from .bundle import bundle_path, save_bundle

#: Fixed so that a rerun with the same data and settings produces the same model.
RANDOM_SEED = 42

#: Documented Isolation Forest defaults. Not tuned against any answer key - no label was
#: consulted to choose them.
DEFAULT_PARAMS = {
    "n_estimators": 200,
    "max_samples": "auto",
    "contamination": "auto",
    "bootstrap": False,
    "n_jobs": 1,
}


def build_pipeline(params: dict, n_quantiles: int = 1000) -> tuple[Pipeline, IsolationForest]:
    """Preprocessing + estimator, saved together so inference matches training.

    A quantile transform is used because the raw features mix counts, log-values and
    hour spans on very different scales, and Isolation Forest splits on raw thresholds.
    ``params`` is not modified.
    """
    return Pipeline([
        ("impute", SimpleImputer(strategy="constant", fill_value=0.0)),
        ("scale", QuantileTransformer(
            n_quantiles=n_quantiles, output_distribution="normal",
            subsample=1_000_000, random_state=RANDOM_SEED)),
    ]), IsolationForest(random_state=RANDOM_SEED, **params)


def _load_features_from_file(path: Path, mode: ValidationMode) -> tuple:
    """Import a file into a temporary training case and extract its features."""
    init_db()
    case_id = new_id("train")
    with write_tx() as conn:
        conn.execute(
            "INSERT INTO cases (id,title,description,state,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (case_id, f"Model training: {path.name}", "Temporary case for model fitting.",
             "archived", now_iso(), now_iso()),
        )
    data_bytes = path.read_bytes()
    summary = import_dataset(case_id, data_bytes, path.name, mode)
    data = load_case_data(case_id, summary.dataset_id)

    coinjoin_config = CoinJoinConfig()
    collaborative = {r.index for r in data.transactions
                     if is_coinjoin_like(r, coinjoin_config)[0]}
    frame = build_feature_frame(data, collaborative)
    return frame, summary, data, case_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the ChainLens anomaly model.")
    parser.add_argument("--input", required=True, type=Path,
                        help="Transaction file to fit the development model on.")
    parser.add_argument("--name", default="default", help="Bundle name under model_artifacts/.")
    parser.add_argument("--mode", default="compatibility",
                        choices=[m.value for m in ValidationMode])
    parser.add_argument("--holdout-fraction", type=float, default=0.0,
                        help="Fraction of ADDRESSES held out from fitting (0 = transductive).")
    parser.add_argument("--n-estimators", type=int, default=DEFAULT_PARAMS["n_estimators"])
    parser.add_argument("--contamination", default="auto")
    args = parser.parse_args(argv)

    if not args.input.exists():
        parser.error(f"input file not found: {args.input}")
    config.ensure_dirs()

    print(f"Reading {args.input} ...")
    frame, summary, data, case_id = _load_features_from_file(args.input, ValidationMode(args.mode))
    assert_no_identity_leak(frame)
    print(f"  {summary.total_rows} rows -> {len(frame)} addresses, "
          f"{len(FEATURE_ORDER)} features")

    rng = np.random.default_rng(RANDOM_SEED)
    addresses = np.array(frame.index)
    if args.holdout_fraction > 0:
        mask = rng.random(len(addresses)) >= args.holdout_fraction
        fit_addresses = addresses[mask]
        holdout_addresses = addresses[~mask]
    else:
        fit_addresses = addresses
        holdout_addresses = np.array([], dtype=object)

    fit_frame = frame.loc[fit_addresses]
    params = {**DEFAULT_PARAMS, "n_estimators": args.n_estimators,
              "contamination": args.contamination}
    n_quantiles = min(1000, max(10, len(fit_frame)))
    preprocessor, estimator = build_pipeline(params, n_quantiles)

    started = time.perf_counter()
    matrix = fit_frame.to_numpy(dtype=float)
    transformed = preprocessor.fit_transform(matrix)
    estimator.fit(transformed)
    fit_seconds = time.perf_counter() - started

    # Freeze the reference distribution on the fitted population only.
    reference = np.sort(-estimator.score_samples(transformed))

    manifest = {
        "model_version": f"if-{FEATURE_SCHEMA_VERSION.split('/')[-1]}-{datetime.now(timezone.utc):%Y%m%d}-{new_id('m').split('_')[1][:6]}",
        "algorithm": "sklearn.ensemble.IsolationForest",
        "trained_at": now_iso(),
        "random_seed": RANDOM_SEED,
        "parameters": dict(params),
        "preprocessing": ["SimpleImputer(constant 0.0)",
                          f"QuantileTransformer(normal, n_quantiles={n_quantiles})"],
        "feature_order": list(FEATURE_ORDER),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_manifest": feature_manifest(),
        "score_direction": "higher_is_more_unusual",
        "score_direction_note": (
            "sklearn score_samples returns higher-is-more-normal; the bundle negates it "
            "once so every displayed score and percentile is higher-is-more-unusual."),
        "reference_distribution": {
            "size": int(reference.size),
            "source": "development fit population",
            "tie_policy": "midpoint of the tied rank block",
        },
        "training_data": {
            "file_name": args.input.name,
            "file_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
            "rows": summary.total_rows,
            "validation_mode": args.mode,
            "addresses_total": int(len(addresses)),
            "addresses_fitted": int(len(fit_addresses)),
            "addresses_held_out": int(len(holdout_addresses)),
            "holdout_fraction": args.holdout_fraction,
            "holdout_unit": "address (entity-disjoint)",
        },
        "held_out_addresses": sorted(holdout_addresses.tolist()),
        "fit_seconds": round(fit_seconds, 3),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
        },
        "validation_status": (
            "EXPLORATORY. Fitted on the supplied synthetic dataset. Scores describe how "
            "unusual an address looks relative to this population; they are not "
            "calibrated probabilities and are not a measurement of unseen-data "
            "performance."
            if args.holdout_fraction <= 0 else
            "Entity-disjoint holdout: the held-out addresses listed in this manifest did "
            "not contribute to the fitted preprocessing, the model or the reference "
            "distribution."),
        "labels_used": "none - Isolation Forest is unsupervised and no label column reaches this pipeline",
    }

    directory = bundle_path(args.name)
    manifest = save_bundle(directory, estimator, preprocessor, reference, manifest)

    print(f"\nSaved bundle -> {directory}")
    print(f"  model_version : {manifest['model_version']}")
    print(f"  fit time      : {fit_seconds:.2f}s on {len(fit_frame)} addresses")
    print(f"  integrity     : {manifest['integrity_sha256'][:16]}...")
    print(f"  status        : {manifest['validation_status'].split('.')[0]}")

    with write_tx() as conn:
        conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
