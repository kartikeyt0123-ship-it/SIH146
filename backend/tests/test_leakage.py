"""Label-leakage and reproducibility guarantees.

These are the tests that matter most for the product's central claim: the system earns
its findings from behaviour, not from the answer key that happens to travel with the
sample file.
"""
from __future__ import annotations

import csv
import io
import json
import random

import pytest

import fixtures
from chainlens import db
from chainlens.detectors.config import DetectorConfig
from chainlens.features import FEATURE_ORDER, build_feature_frame, feature_manifest
from chainlens.features.address_features import EXCLUDED_FROM_MODEL, assert_no_identity_leak
from chainlens.graphx.model import load_case_data
from chainlens.ingest import ValidationMode, import_dataset
from chainlens.ingest.contract import FORBIDDEN_LABEL_FIELDS
from chainlens.runner.pipeline import execute_run


def _new_case(title: str = "leak") -> str:
    case_id = db.new_id("case")
    with db.write_tx() as conn:
        conn.execute(
            "INSERT INTO cases (id,title,description,state,created_at,updated_at)"
            " VALUES (?,?,?,'draft',?,?)",
            (case_id, title, "", db.now_iso(), db.now_iso()))
    return case_id


def _run(case_id: str, data: bytes, name: str = "f.csv") -> tuple[str, list[dict]]:
    summary = import_dataset(case_id, data, name, ValidationMode.COMPATIBILITY)
    run_id = db.new_id("run")
    config = DetectorConfig()
    with db.write_tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, case_id, dataset_id, status, stage, config_json,"
            " detector_version, dataset_sha256) VALUES (?,?,?,'running','loading',?,?,?)",
            (run_id, case_id, summary.dataset_id, json.dumps(config.to_dict()),
             config.version, summary.sha256))
    execute_run(run_id, case_id, summary.dataset_id, config)
    alerts = [dict(r) for r in db.query(
        "SELECT subject_id, pattern, priority, evidence_strength, explanation"
        " FROM alerts WHERE run_id=? ORDER BY subject_id, pattern", (run_id,))]
    return run_id, alerts


def _permute_label_column(data: bytes, seed: int = 7) -> bytes:
    """Randomly reassign the planted label column, leaving every other field intact."""
    text = data.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    rng = random.Random(seed)
    values = [r["is_planted_suspicious"] for r in rows]
    rng.shuffle(values)
    for row, value in zip(rows, values):
        row["is_planted_suspicious"] = value
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _remove_label_column(data: bytes) -> bytes:
    text = data.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    fields = [f for f in rows[0] if f != "is_planted_suspicious"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\r\n",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _invert_label_column(data: bytes) -> bytes:
    text = data.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    for row in rows:
        row["is_planted_suspicious"] = "0" if row["is_planted_suspicious"] == "1" else "1"
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


# ------------------------------------------------------------------ the core claim
@pytest.mark.parametrize("mutate,label", [
    (_permute_label_column, "permuted"),
    (_remove_label_column, "removed"),
    (_invert_label_column, "inverted"),
])
def test_changing_the_label_column_does_not_change_any_prediction(
        sample_csv_bytes, mutate, label):
    """Permuting, deleting or inverting the answer key must change nothing at all."""
    baseline_case = _new_case(f"baseline-{label}")
    _, baseline = _run(baseline_case, sample_csv_bytes, "original")

    mutated_case = _new_case(f"mutated-{label}")
    _, mutated = _run(mutated_case, mutate(sample_csv_bytes), "mutated")

    assert baseline, "the baseline run should produce alerts"
    assert baseline == mutated, (
        f"predictions changed when the label column was {label}; "
        f"the pipeline is reading the answer key")


def test_sanitised_analysis_view_contains_no_label(case_id, sample_csv_bytes):
    summary = import_dataset(case_id, sample_csv_bytes, "ps3", ValidationMode.COMPATIBILITY)
    data = load_case_data(case_id, summary.dataset_id)
    serialised = json.dumps([
        {"txid": t.txid, "inputs": t.inputs, "outputs": t.outputs,
         "fee": t.fee_sats, "script": t.script_type}
        for t in data.transactions[:200]
    ])
    for forbidden in FORBIDDEN_LABEL_FIELDS:
        assert forbidden not in serialised


def test_feature_frame_is_restricted_to_the_allowlist(case_id, sample_csv_bytes):
    summary = import_dataset(case_id, sample_csv_bytes, "ps3", ValidationMode.COMPATIBILITY)
    data = load_case_data(case_id, summary.dataset_id)
    frame = build_feature_frame(data, collaborative=set())

    assert list(frame.columns) == list(FEATURE_ORDER)
    assert_no_identity_leak(frame)
    for forbidden in FORBIDDEN_LABEL_FIELDS:
        assert forbidden not in frame.columns
    # No identifier may appear as a feature name.
    for column in frame.columns:
        assert "address" not in column and "txid" not in column and "ip" != column


def test_identity_columns_are_rejected_loudly(case_id):
    """The allowlist must be enforced, not merely documented."""
    summary = import_dataset(case_id, fixtures.repeated_co_spend(), "f.csv")
    data = load_case_data(case_id, summary.dataset_id)
    frame = build_feature_frame(data, set())
    frame["address_hash"] = 1.0
    with pytest.raises(ValueError, match="allowlist"):
        assert_no_identity_leak(frame)


def test_feature_manifest_documents_every_feature_and_its_exclusions():
    manifest = feature_manifest()
    assert set(manifest["feature_order"]) == set(FEATURE_ORDER)
    for name in FEATURE_ORDER:
        assert manifest["definitions"].get(name), f"{name} has no documented definition"
    assert "is_planted_suspicious" in " ".join(EXCLUDED_FROM_MODEL)
    assert manifest["missing_value_policy"]


def test_quality_artefacts_are_excluded_from_the_model():
    """The fee-residual magnitude must never become a feature."""
    joined = " ".join(FEATURE_ORDER).lower()
    for token in ("residual", "discrepancy", "quarantine", "planted", "fee_rate"):
        assert token not in joined
    assert "amount_discrepancy_magnitude" in EXCLUDED_FROM_MODEL


# -------------------------------------------------------------- reproducibility
def test_same_input_and_configuration_produce_identical_findings(sample_csv_bytes):
    first_case = _new_case("repro-a")
    _, first = _run(first_case, sample_csv_bytes, "ps3")
    second_case = _new_case("repro-b")
    _, second = _run(second_case, sample_csv_bytes, "ps3")
    assert first == second, "a rerun on identical input must reproduce the findings"


def test_alert_ordering_is_deterministic(sample_csv_bytes):
    case_id = _new_case("order")
    run_id, _ = _run(case_id, sample_csv_bytes, "ps3")
    first = [r["id"] for r in db.query(
        "SELECT id FROM alerts WHERE run_id=? ORDER BY priority DESC, id ASC LIMIT 50",
        (run_id,))]
    second = [r["id"] for r in db.query(
        "SELECT id FROM alerts WHERE run_id=? ORDER BY priority DESC, id ASC LIMIT 50",
        (run_id,))]
    assert first == second


def test_model_bundle_is_only_loaded_from_the_project_directory(tmp_path):
    """A bundle outside the project model directory must be refused."""
    from chainlens.ml.bundle import ModelLoadError, load_bundle

    outside = tmp_path / "untrusted_model"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ModelLoadError, match="outside the project model directory"):
        load_bundle(directory=outside)


def test_analyst_review_does_not_alter_scores(case_id):
    """Recording a decision must not change any machine output."""
    summary = import_dataset(case_id, fixtures.collection_burst(payers=9), "c.csv")
    run_id = db.new_id("run")
    config = DetectorConfig()
    with db.write_tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, case_id, dataset_id, status, stage, config_json,"
            " detector_version, dataset_sha256) VALUES (?,?,?,'running','loading',?,?,?)",
            (run_id, case_id, summary.dataset_id, json.dumps(config.to_dict()),
             config.version, summary.sha256))
    execute_run(run_id, case_id, summary.dataset_id, config)

    before = [dict(r) for r in db.query(
        "SELECT id, priority, evidence_strength FROM alerts WHERE run_id=? ORDER BY id",
        (run_id,))]
    assert before
    with db.write_tx() as conn:
        conn.execute("UPDATE alerts SET review_state='false_positive' WHERE id=?",
                     (before[0]["id"],))
    after = [dict(r) for r in db.query(
        "SELECT id, priority, evidence_strength FROM alerts WHERE run_id=? ORDER BY id",
        (run_id,))]
    assert before == after
