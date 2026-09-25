"""End-to-end API tests: upload, analyse, review, evidence, export."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from chainlens.api.app import create_app
from chainlens.runner import run_manager

from conftest import csv_row, make_csv


@pytest.fixture
def client(isolated_data_dir):
    return TestClient(create_app())


def _wait_for_run(client: TestClient, run_id: str, timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in ("complete", "failed", "cancelled"):
            return body
        time.sleep(0.2)
    run_manager.join(run_id, timeout=5)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_health_reports_offline(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["offline"] is True


def test_docs_are_disabled_for_offline_operation(client):
    # The default docs UI loads assets from a CDN, so it must be off by default.
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_policy_endpoint_publishes_the_scoring_contract(client):
    body = client.get("/api/meta/policy").json()
    assert "0.60" in body["priority_policy"]["formula"]
    assert body["priority_policy"]["not_a_probability"]
    assert "feature_order" in body["feature_manifest"]


def test_case_lifecycle_and_empty_state(client):
    created = client.post("/api/cases", json={"title": "Case A", "description": "d"})
    assert created.status_code == 201
    case_id = created.json()["id"]

    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["case"]["state"] == "draft"
    assert detail["datasets"] == []
    assert detail["has_activity"] is False, "a new case must be distinguishable from an empty one"

    assert client.get("/api/cases/case_missing").status_code == 404


def test_upload_analyse_review_evidence_and_export(client, sample_csv_bytes):
    case_id = client.post("/api/cases", json={"title": "SIH demo"}).json()["id"]

    upload = client.post(
        f"/api/cases/{case_id}/imports",
        files={"file": ("ps3_transactions", sample_csv_bytes, "application/octet-stream")},
        data={"validation_mode": "compatibility", "fee_tolerance_sats": "1"},
    )
    assert upload.status_code == 201, upload.text
    summary = upload.json()
    assert summary["total_rows"] == 10_000
    assert summary["reconciles"] is True
    assert summary["label_fields_removed"] == ["is_planted_suspicious"]
    dataset_id = summary["dataset_id"]

    started = client.post(f"/api/cases/{case_id}/runs", json={"dataset_id": dataset_id})
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    run = _wait_for_run(client, run_id)
    assert run["status"] == "complete", run.get("error")
    assert run["alert_count"] > 0
    assert run["model_status"] == "used"
    assert run["stats"]["coverage"]["transactions"] == 10_000

    listing = client.get(f"/api/runs/{run_id}/alerts", params={"limit": 10}).json()
    assert listing["total"] == run["alert_count"]
    assert len(listing["alerts"]) == 10
    priorities = [a["priority"] for a in listing["alerts"]]
    assert priorities == sorted(priorities, reverse=True)
    assert "pattern" in listing["facets"]

    alert_id = listing["alerts"][0]["id"]
    alert = client.get(f"/api/alerts/{alert_id}").json()
    # Every alert must carry the full anatomy the contract requires.
    for field in ("id", "run_id", "pattern", "role_hypothesis", "priority_components",
                  "evidence_strength", "evidence_reasons", "period_start",
                  "explanation", "caveats", "alternatives", "review_state"):
        assert field in alert, f"alert is missing {field}"
    assert alert["evidence"], "an alert must reference supporting records"

    # Traceability: alert -> evidence -> the original row, in two steps.
    evidence = alert["evidence"][0]
    assert evidence["source_row_id"] is not None
    row = client.get(f"/api/source-rows/{evidence['source_row_id']}").json()
    assert row["row_number"] == evidence["row_number"]
    assert "txid" in row["raw"]

    # Review state transitions are recorded with actor, reason and previous state.
    patched = client.patch(f"/api/alerts/{alert_id}/review",
                           json={"state": "under_review", "reason": "triage",
                                 "expected_state": "new"})
    assert patched.status_code == 200
    assert patched.json()["previous_state"] == "new"
    assert patched.json()["model_unchanged"] is True

    stale = client.patch(f"/api/alerts/{alert_id}/review",
                         json={"state": "relevant", "expected_state": "new"})
    assert stale.status_code == 409, "a stale review write must be rejected"

    note = client.post(f"/api/alerts/{alert_id}/notes", json={"body": "Checked payers."})
    assert note.status_code == 201

    # Graph: bounded neighbourhood, never the whole graph.
    graph = client.get(f"/api/runs/{run_id}/graph",
                       params={"subject": alert["subject_id"], "hops": 1,
                               "node_budget": 120}).json()
    assert graph["meta"]["node_budget"] == 120
    assert graph["nodes"] and len(graph["nodes"]) <= 120
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "transaction" in kinds, "the transaction node must be preserved between addresses"
    assert graph["legend"]

    no_subject = client.get(f"/api/runs/{run_id}/graph")
    assert no_subject.status_code == 400, "the full graph must not be returned by default"

    # Export the selected finding.
    export = client.post(f"/api/cases/{case_id}/exports",
                         json={"run_id": run_id, "alert_ids": [alert_id]})
    assert export.status_code == 201, export.text
    body = export.json()
    assert {"case_summary.pdf", "findings.json", "alerts.csv", "evidence.csv",
            "manifest.json", "LIMITATIONS.txt"} <= set(body["files"])
    manifest = body["manifest"]
    assert manifest["source_file"]["sha256"] == summary["sha256"]
    assert manifest["alert_count"] == 1
    assert manifest["detector_version"]
    assert "not certify" in manifest["integrity_note"]

    pdf = client.get(f"/api/exports/{body['export_id']}/files/case_summary.pdf")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"

    audit = client.get(f"/api/cases/{case_id}/audit").json()
    actions = {e["action"] for e in audit["events"]}
    assert {"case.created", "dataset.imported", "run.started", "run.completed",
            "alert.review_changed", "export.created"} <= actions


def test_run_on_missing_dataset_is_rejected(client):
    case_id = client.post("/api/cases", json={"title": "x"}).json()["id"]
    response = client.post(f"/api/cases/{case_id}/runs", json={"dataset_id": "ds_nope"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DATASET_NOT_FOUND"


def test_unreadable_upload_is_reported_not_crashed(client):
    case_id = client.post("/api/cases", json={"title": "x"}).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/imports",
        files={"file": ("junk.bin", b"\x00\x01\x02 not a table at all", "application/octet-stream")},
    )
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        # Parsed as a degenerate CSV: every row must still be accounted for.
        assert response.json()["reconciles"] is True


def test_empty_upload_is_rejected(client):
    case_id = client.post("/api/cases", json={"title": "x"}).json()["id"]
    response = client.post(f"/api/cases/{case_id}/imports",
                           files={"file": ("empty.csv", b"", "text/csv")})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "EMPTY_UPLOAD"


def test_export_of_incomplete_run_is_refused(client):
    """A run that never completed must never be presented as a result."""
    from chainlens import db

    case_id = client.post("/api/cases", json={"title": "x"}).json()["id"]
    upload = client.post(f"/api/cases/{case_id}/imports",
                         files={"file": ("t.csv", make_csv(csv_row()), "text/csv")})
    dataset_id = upload.json()["dataset_id"]
    run_id = db.new_id("run")
    with db.write_tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, case_id, dataset_id, status, stage) "
            "VALUES (?,?,?,'failed','model')", (run_id, case_id, dataset_id))

    response = client.post(f"/api/cases/{case_id}/exports", json={"run_id": run_id})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUN_NOT_EXPORTABLE"


def test_sensitivity_is_ranking_only_and_leaves_the_alert_untouched(client):
    case_id = client.post("/api/cases", json={"title": "sens"}).json()["id"]
    data = make_csv(*[
        csv_row(txid=f"{i:064x}", inputs=f"P{i}", outputs="COLLECT",
                timestamp=f"2026-08-01T{i:02d}:00:00Z")
        for i in range(1, 12)
    ])
    dataset_id = client.post(f"/api/cases/{case_id}/imports",
                             files={"file": ("c.csv", data, "text/csv")}).json()["dataset_id"]
    run_id = client.post(f"/api/cases/{case_id}/runs",
                         json={"dataset_id": dataset_id}).json()["run_id"]
    _wait_for_run(client, run_id)

    alerts = client.get(f"/api/runs/{run_id}/alerts").json()["alerts"]
    if not alerts:
        pytest.skip("fixture produced no alerts")
    alert = alerts[0]

    response = client.post(f"/api/alerts/{alert['id']}/sensitivity",
                           json={"drop_model_component": True}).json()
    assert response["recalculation_type"] == "ranking_only"
    assert "not rescored" in response["statement"]
    assert response["baseline"]["priority"] == alert["priority"]

    unchanged = client.get(f"/api/alerts/{alert['id']}").json()
    assert unchanged["priority"] == alert["priority"], "the stored alert must not change"
