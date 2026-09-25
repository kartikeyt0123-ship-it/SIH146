"""HTTP routes for the local analyst application."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from .. import config
from ..db import audit, loads, new_id, now_iso, query, query_one, write_tx
from ..detectors.base import RULE_SEVERITY
from ..detectors.config import DetectorConfig
from ..exporting import build_export
from ..features import feature_manifest
from ..graphx.build import (
    address_node,
    build_graph,
    graph_to_payload,
    neighbourhood,
    tx_node,
)
from ..graphx.model import load_case_data
from ..ingest import ValidationMode, import_dataset
from ..ingest.adapters import AdapterError
from ..ingest.contract import ISSUE_TEXT, IssueCode
from ..runner import run_manager
from ..scoring.priority import PRIORITY_POLICY, review_priority
from .schemas import (
    CaseCreate,
    ClusterDecision,
    ExportRequest,
    NoteCreate,
    ReviewUpdate,
    RunCreate,
    SensitivityRequest,
)

router = APIRouter()

REVIEW_STATES = ("new", "under_review", "relevant", "false_positive", "inconclusive")
CASE_STATES = ("draft", "analysing", "ready_for_review", "archived")

#: Graph views are bounded by default. The whole graph is never returned.
DEFAULT_NODE_BUDGET = 300
MAX_NODE_BUDGET = 1500


def _error(status: int, code: str, message: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status,
                         detail={"code": code, "message": message, **detail})


# --------------------------------------------------------------------------- health
@router.get("/health")
def health() -> dict[str, Any]:
    from ..ml.bundle import ModelLoadError, load_bundle

    model: dict[str, Any]
    try:
        bundle = load_bundle()
        model = {"status": "available", "version": bundle.version,
                 "feature_schema": bundle.feature_schema_version,
                 "validation_status": bundle.manifest.get("validation_status")}
    except ModelLoadError as exc:
        model = {"status": "unavailable", "error": str(exc)}
    return {
        "status": "ok",
        "offline": True,
        "network_calls": "none - the application makes no outbound requests",
        "model": model,
        "detector_version": DetectorConfig().version,
    }


@router.get("/meta/policy")
def policy() -> dict[str, Any]:
    """The scoring policy, detector defaults and feature contract, for the UI."""
    return {
        "priority_policy": PRIORITY_POLICY,
        "rule_severity_defaults": RULE_SEVERITY,
        "detector_config_defaults": DetectorConfig().to_dict(),
        "feature_manifest": feature_manifest(),
        "issue_codes": {c.value: ISSUE_TEXT[c] for c in IssueCode},
        "review_states": list(REVIEW_STATES),
    }


# ---------------------------------------------------------------------------- cases
@router.get("/cases")
def list_cases() -> list[dict[str, Any]]:
    rows = query(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM datasets d WHERE d.case_id=c.id) dataset_count,
                  (SELECT COUNT(*) FROM runs r WHERE r.case_id=c.id) run_count,
                  (SELECT COUNT(*) FROM alerts a WHERE a.case_id=c.id) alert_count,
                  (SELECT r.id FROM runs r WHERE r.case_id=c.id AND r.status='complete'
                     ORDER BY r.finished_at DESC LIMIT 1) latest_run_id
           FROM cases c ORDER BY c.updated_at DESC""")
    return [dict(r) for r in rows]


@router.post("/cases", status_code=201)
def create_case(payload: CaseCreate) -> dict[str, Any]:
    case_id = new_id("case")
    stamp = now_iso()
    with write_tx() as conn:
        conn.execute(
            "INSERT INTO cases (id,title,description,state,created_at,updated_at)"
            " VALUES (?,?,?,'draft',?,?)",
            (case_id, payload.title.strip(), payload.description.strip(), stamp, stamp))
        audit(conn, "case.created", case_id, {"title": payload.title})
    return dict(query_one("SELECT * FROM cases WHERE id=?", (case_id,)))


@router.get("/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    case = query_one("SELECT * FROM cases WHERE id=?", (case_id,))
    if case is None:
        raise _error(404, "CASE_NOT_FOUND", f"No case with id {case_id}.")

    datasets = [dict(r) for r in query(
        "SELECT * FROM datasets WHERE case_id=? ORDER BY imported_at DESC", (case_id,))]
    for dataset in datasets:
        dataset["schema"] = loads(dataset.pop("schema_json", None), {})
        dataset["issue_counts"] = {
            r["code"]: r["n"] for r in query(
                "SELECT code, COUNT(*) n FROM validation_issues WHERE dataset_id=?"
                " GROUP BY code ORDER BY code", (dataset["id"],))}
        dataset["reconciles"] = (
            dataset["total_rows"]
            == dataset["accepted_clean"] + dataset["accepted_warning"] + dataset["quarantined"])

    runs = [dict(r) for r in query(
        "SELECT * FROM runs WHERE case_id=? ORDER BY started_at DESC", (case_id,))]
    for run in runs:
        run["config"] = loads(run.pop("config_json", None), {})
        run["stats"] = loads(run.pop("stats_json", None), {})

    alert_counts = {r["priority_band"]: r["n"] for r in query(
        "SELECT priority_band, COUNT(*) n FROM alerts WHERE case_id=? GROUP BY priority_band",
        (case_id,))}
    return {"case": dict(case), "datasets": datasets, "runs": runs,
            "alert_counts_by_band": alert_counts,
            "has_activity": bool(datasets)}


@router.delete("/cases/{case_id}", status_code=200)
def delete_case(case_id: str) -> dict[str, Any]:
    """Delete a case and everything in it. Deliberate and explicit; never automatic."""
    if query_one("SELECT id FROM cases WHERE id=?", (case_id,)) is None:
        raise _error(404, "CASE_NOT_FOUND", f"No case with id {case_id}.")
    with write_tx() as conn:
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
        audit(conn, "case.deleted", case_id,
              {"note": "Exported packages already written to disk are not removed."})
    return {"deleted": case_id,
            "note": ("The case, its imported records, runs and alerts are removed. "
                     "Evidence packages already written to disk are retained and must "
                     "be deleted separately.")}


# -------------------------------------------------------------------------- imports
@router.post("/cases/{case_id}/imports", status_code=201)
async def create_import(
    case_id: str,
    file: UploadFile = File(...),
    validation_mode: str = Form(ValidationMode.COMPATIBILITY.value),
    fee_tolerance_sats: int = Form(1),
) -> dict[str, Any]:
    if query_one("SELECT id FROM cases WHERE id=?", (case_id,)) is None:
        raise _error(404, "CASE_NOT_FOUND", f"No case with id {case_id}.")
    try:
        mode = ValidationMode(validation_mode)
    except ValueError:
        raise _error(400, "INVALID_VALIDATION_MODE",
                     f"validation_mode must be one of {[m.value for m in ValidationMode]}.")

    data = await file.read()
    if not data:
        raise _error(400, "EMPTY_UPLOAD", "The uploaded file is empty.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise _error(413, "UPLOAD_TOO_LARGE",
                     f"The file exceeds the {config.MAX_UPLOAD_BYTES} byte limit.")

    try:
        summary = import_dataset(case_id, data, file.filename or "upload", mode,
                                 fee_tolerance_sats)
    except AdapterError as exc:
        raise _error(422, "FILE_UNREADABLE", str(exc))

    return summary.to_dict()


@router.get("/datasets/{dataset_id}/issues")
def dataset_issues(dataset_id: str, code: str | None = None,
                   limit: int = Query(100, ge=1, le=1000),
                   offset: int = Query(0, ge=0)) -> dict[str, Any]:
    sql = "SELECT * FROM validation_issues WHERE dataset_id=?"
    params: list[Any] = [dataset_id]
    if code:
        sql += " AND code=?"
        params.append(code)
    total = query_one(f"SELECT COUNT(*) n FROM ({sql})", tuple(params))["n"]
    rows = query(sql + " ORDER BY row_number, id LIMIT ? OFFSET ?",
                 tuple(params) + (limit, offset))
    return {"total": total, "limit": limit, "offset": offset,
            "issues": [dict(r) for r in rows]}


@router.get("/datasets/{dataset_id}/rows")
def dataset_rows(dataset_id: str, status: str | None = None,
                 limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """Paginated source rows. This is the analyst's view of the original file."""
    sql = "SELECT id, row_number, status, raw_json FROM source_rows WHERE dataset_id=?"
    params: list[Any] = [dataset_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    total = query_one(f"SELECT COUNT(*) n FROM ({sql})", tuple(params))["n"]
    rows = query(sql + " ORDER BY row_number LIMIT ? OFFSET ?",
                 tuple(params) + (limit, offset))
    return {"total": total, "limit": limit, "offset": offset, "rows": [
        {"id": r["id"], "row_number": r["row_number"], "status": r["status"],
         "raw": loads(r["raw_json"], {})} for r in rows]}


@router.get("/source-rows/{source_row_id}")
def source_row(source_row_id: int) -> dict[str, Any]:
    """One original row, reached from an alert in at most two steps."""
    row = query_one(
        "SELECT s.*, d.original_name, d.sha256 FROM source_rows s"
        " JOIN datasets d ON d.id = s.dataset_id WHERE s.id=?", (source_row_id,))
    if row is None:
        raise _error(404, "SOURCE_ROW_NOT_FOUND", f"No source row {source_row_id}.")
    issues = [dict(r) for r in query(
        "SELECT code, severity, field, message FROM validation_issues WHERE source_row_id=?",
        (source_row_id,))]
    return {
        "id": row["id"], "row_number": row["row_number"], "status": row["status"],
        "raw": loads(row["raw_json"], {}),
        "dataset": {"original_name": row["original_name"], "sha256": row["sha256"]},
        "issues": issues,
        "note": ("This is the original uploaded row, including any evaluation label "
                 "column. Label columns are removed before analysis and never reach a "
                 "detector or the model."),
    }


# ----------------------------------------------------------------------------- runs
@router.post("/cases/{case_id}/runs", status_code=202)
def create_run(case_id: str, payload: RunCreate) -> dict[str, Any]:
    if query_one("SELECT id FROM cases WHERE id=?", (case_id,)) is None:
        raise _error(404, "CASE_NOT_FOUND", f"No case with id {case_id}.")
    dataset = query_one("SELECT * FROM datasets WHERE id=? AND case_id=?",
                        (payload.dataset_id, case_id))
    if dataset is None:
        raise _error(404, "DATASET_NOT_FOUND",
                     f"No dataset {payload.dataset_id} in case {case_id}.")
    if dataset["status"] != "complete":
        raise _error(409, "DATASET_NOT_READY",
                     f"Dataset {payload.dataset_id} is '{dataset['status']}'.")

    detector_config = DetectorConfig.from_dict(payload.detector_config)
    run_id = new_id("run")
    with write_tx() as conn:
        conn.execute(
            """INSERT INTO runs (id, case_id, dataset_id, status, stage, progress,
                   config_json, detector_version, dataset_sha256)
               VALUES (?,?,?,'queued','queued',0.0,?,?,?)""",
            (run_id, case_id, payload.dataset_id,
             json.dumps(detector_config.to_dict(), sort_keys=True),
             detector_config.version, dataset["sha256"]))
        audit(conn, "run.started", case_id,
              {"run_id": run_id, "dataset_id": payload.dataset_id})

    run_manager.start(run_id, case_id, payload.dataset_id, detector_config,
                      payload.model_name)
    return {"run_id": run_id, "status": "queued",
            "poll": f"/api/runs/{run_id}"}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise _error(404, "RUN_NOT_FOUND", f"No run with id {run_id}.")
    payload = dict(run)
    payload["config"] = loads(payload.pop("config_json", None), {})
    payload["stats"] = loads(payload.pop("stats_json", None), {})
    payload["alert_count"] = query_one(
        "SELECT COUNT(*) n FROM alerts WHERE run_id=?", (run_id,))["n"]
    payload["is_running"] = run_manager.is_running(run_id)
    return payload


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    run = query_one("SELECT status FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise _error(404, "RUN_NOT_FOUND", f"No run with id {run_id}.")
    if run["status"] in ("complete", "failed", "cancelled"):
        raise _error(409, "RUN_NOT_CANCELLABLE",
                     f"Run {run_id} has already finished with status '{run['status']}'.")
    run_manager.cancel(run_id)
    return {"run_id": run_id, "cancelling": True}


# --------------------------------------------------------------------------- alerts
@router.get("/runs/{run_id}/alerts")
def list_alerts(
    run_id: str,
    pattern: str | None = None,
    band: str | None = None,
    evidence_strength: str | None = None,
    review_state: str | None = None,
    subject: str | None = None,
    role: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if query_one("SELECT id FROM runs WHERE id=?", (run_id,)) is None:
        raise _error(404, "RUN_NOT_FOUND", f"No run with id {run_id}.")

    clauses = ["run_id = ?"]
    params: list[Any] = [run_id]
    for column, value in (("pattern", pattern), ("priority_band", band),
                          ("evidence_strength", evidence_strength),
                          ("review_state", review_state), ("role_hypothesis", role)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if subject:
        clauses.append("subject_id LIKE ?")
        params.append(f"%{subject}%")

    where = " WHERE " + " AND ".join(clauses)
    total = query_one(f"SELECT COUNT(*) n FROM alerts{where}", tuple(params))["n"]
    # Ordering includes the id so pagination is stable across requests.
    rows = query(
        f"SELECT * FROM alerts{where} ORDER BY priority DESC, id ASC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset))

    alerts = []
    for row in rows:
        alert = dict(row)
        alert["priority_components"] = loads(alert.pop("priority_components", None), {})
        alert["evidence_reasons"] = loads(alert.pop("evidence_reasons", None), [])
        alert["alternatives"] = loads(alert.pop("alternatives", None), [])
        alert["caveats"] = loads(alert.pop("caveats", None), [])
        alert["indicators"] = loads(alert.pop("indicators", None), {})
        alert["evidence_count"] = query_one(
            "SELECT COUNT(*) n FROM alert_evidence WHERE alert_id=?", (row["id"],))["n"]
        alerts.append(alert)

    facets = {
        "pattern": {r["pattern"]: r["n"] for r in query(
            "SELECT pattern, COUNT(*) n FROM alerts WHERE run_id=? GROUP BY pattern",
            (run_id,))},
        "priority_band": {r["priority_band"]: r["n"] for r in query(
            "SELECT priority_band, COUNT(*) n FROM alerts WHERE run_id=? GROUP BY priority_band",
            (run_id,))},
        "evidence_strength": {r["evidence_strength"]: r["n"] for r in query(
            "SELECT evidence_strength, COUNT(*) n FROM alerts WHERE run_id=?"
            " GROUP BY evidence_strength", (run_id,))},
        "review_state": {r["review_state"]: r["n"] for r in query(
            "SELECT review_state, COUNT(*) n FROM alerts WHERE run_id=? GROUP BY review_state",
            (run_id,))},
        "role_hypothesis": {r["role_hypothesis"]: r["n"] for r in query(
            "SELECT role_hypothesis, COUNT(*) n FROM alerts WHERE run_id=?"
            " GROUP BY role_hypothesis", (run_id,))},
    }
    return {"total": total, "limit": limit, "offset": offset, "alerts": alerts,
            "facets": facets}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str) -> dict[str, Any]:
    row = query_one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if row is None:
        raise _error(404, "ALERT_NOT_FOUND", f"No alert with id {alert_id}.")
    alert = dict(row)
    alert["priority_components"] = loads(alert.pop("priority_components", None), {})
    alert["evidence_reasons"] = loads(alert.pop("evidence_reasons", None), [])
    alert["alternatives"] = loads(alert.pop("alternatives", None), [])
    alert["caveats"] = loads(alert.pop("caveats", None), [])
    alert["indicators"] = loads(alert.pop("indicators", None), {})
    alert["evidence"] = [
        {**{k: v for k, v in dict(e).items() if k != "detail_json"},
         "detail": loads(e["detail_json"], {})}
        for e in query("SELECT * FROM alert_evidence WHERE alert_id=? ORDER BY id",
                       (alert_id,))]
    alert["notes"] = [dict(n) for n in query(
        "SELECT * FROM notes WHERE alert_id=? ORDER BY id", (alert_id,))]
    alert["review_history"] = [dict(r) for r in query(
        "SELECT * FROM review_events WHERE alert_id=? ORDER BY id", (alert_id,))]

    score = query_one(
        "SELECT * FROM address_scores WHERE run_id=? AND address=?",
        (row["run_id"], row["subject_id"]))
    alert["address_score"] = (
        {**dict(score), "features": loads(score["features_json"], {})} if score else None)
    if alert["address_score"]:
        alert["address_score"].pop("features_json", None)
    return alert


@router.patch("/alerts/{alert_id}/review")
def update_review(alert_id: str, payload: ReviewUpdate) -> dict[str, Any]:
    if payload.state not in REVIEW_STATES:
        raise _error(400, "INVALID_REVIEW_STATE",
                     f"state must be one of {list(REVIEW_STATES)}.")
    row = query_one("SELECT review_state, case_id FROM alerts WHERE id=?", (alert_id,))
    if row is None:
        raise _error(404, "ALERT_NOT_FOUND", f"No alert with id {alert_id}.")
    current = row["review_state"]
    if payload.expected_state is not None and payload.expected_state != current:
        raise _error(409, "REVIEW_STATE_CONFLICT",
                     f"The alert is now '{current}', not '{payload.expected_state}'. "
                     f"Reload it and try again.", current_state=current)

    stamp = now_iso()
    with write_tx() as conn:
        conn.execute("UPDATE alerts SET review_state=? WHERE id=?", (payload.state, alert_id))
        conn.execute(
            "INSERT INTO review_events (alert_id, actor, from_state, to_state, reason, at)"
            " VALUES (?,?,?,?,?,?)",
            (alert_id, payload.actor, current, payload.state, payload.reason, stamp))
        audit(conn, "alert.review_changed", row["case_id"], {
            "alert_id": alert_id, "from": current, "to": payload.state,
            "reason": payload.reason,
            "note": ("An analyst decision is recorded against the alert. It does not "
                     "change any model score and does not retrain anything."),
        }, payload.actor)
    return {"alert_id": alert_id, "review_state": payload.state,
            "previous_state": current, "at": stamp,
            "model_unchanged": True}


@router.post("/alerts/{alert_id}/notes", status_code=201)
def add_note(alert_id: str, payload: NoteCreate) -> dict[str, Any]:
    row = query_one("SELECT case_id FROM alerts WHERE id=?", (alert_id,))
    if row is None:
        raise _error(404, "ALERT_NOT_FOUND", f"No alert with id {alert_id}.")
    stamp = now_iso()
    with write_tx() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (alert_id, author, body, created_at) VALUES (?,?,?,?)",
            (alert_id, payload.author, payload.body, stamp))
        note_id = cursor.lastrowid
        audit(conn, "alert.note_added", row["case_id"],
              {"alert_id": alert_id, "note_id": note_id}, payload.author)
    return {"id": note_id, "alert_id": alert_id, "author": payload.author,
            "body": payload.body, "created_at": stamp}


@router.post("/alerts/{alert_id}/sensitivity")
def sensitivity(alert_id: str, payload: SensitivityRequest) -> dict[str, Any]:
    """Recalculate this alert's priority with a component removed.

    This is a ranking recalculation only. The model is not refitted, features are not
    recomputed and the stored alert is not modified.
    """
    row = query_one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if row is None:
        raise _error(404, "ALERT_NOT_FOUND", f"No alert with id {alert_id}.")
    components = loads(row["priority_components"], {})
    baseline_percentile = components.get("anomaly_percentile")
    baseline_severity = components.get("rule_severity", 0.0)

    severity = 0.0 if payload.drop_rule_component else (
        payload.override_rule_severity
        if payload.override_rule_severity is not None else baseline_severity)
    percentile = None if payload.drop_model_component else baseline_percentile

    revised = review_priority(
        rule_severity=severity,
        anomaly_percentile=percentile,
        unscored_reason=("Model component removed for this comparison."
                         if payload.drop_model_component else None),
    )
    changes = []
    if payload.drop_model_component:
        changes.append("anomaly percentile removed")
    if payload.drop_rule_component:
        changes.append("rule severity removed")
    if payload.override_rule_severity is not None and not payload.drop_rule_component:
        changes.append(f"rule severity overridden to {payload.override_rule_severity}")

    return {
        "alert_id": alert_id,
        "baseline": {"priority": row["priority"], "band": row["priority_band"],
                     "components": components},
        "revised": {"priority": revised.priority, "band": revised.band,
                    "components": revised.components},
        "delta": round(revised.priority - row["priority"], 2),
        "changes": changes or ["no change requested"],
        "recalculation_type": "ranking_only",
        "statement": (
            "Only the priority ranking was recalculated from the stored score "
            "components. The model was not rescored, features were not recomputed and "
            "no retraining took place. The stored alert is unchanged."),
    }


# ---------------------------------------------------------------------------- graph
@router.get("/runs/{run_id}/graph")
def run_graph(
    run_id: str,
    subject: str | None = None,
    txid: str | None = None,
    hops: int = Query(1, ge=0, le=3),
    node_budget: int = Query(DEFAULT_NODE_BUDGET, ge=10, le=MAX_NODE_BUDGET),
    include_observations: bool = True,
) -> dict[str, Any]:
    run = query_one("SELECT case_id, dataset_id FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise _error(404, "RUN_NOT_FOUND", f"No run with id {run_id}.")
    if not subject and not txid:
        raise _error(400, "SUBJECT_REQUIRED",
                     "Supply a subject address or txid. The full graph is never "
                     "returned by default; views are bounded neighbourhoods.")

    data = load_case_data(run["case_id"], run["dataset_id"])
    graph = build_graph(data, include_observations=include_observations)
    seeds = [address_node(subject)] if subject else []
    if txid:
        seeds.append(tx_node(txid))

    sub, meta = neighbourhood(graph, seeds, hops=hops, node_budget=node_budget)
    payload = graph_to_payload(sub, meta)

    # Attach the run's scores so the interface can shade address nodes without a
    # second request, and mark which nodes carry an alert.
    scores = {r["address"]: {"anomaly_percentile": r["anomaly_percentile"],
                             "scored": bool(r["scored"])}
              for r in query("SELECT address, anomaly_percentile, scored FROM address_scores"
                             " WHERE run_id=?", (run_id,))}
    alerted = {r["subject_id"]: {"priority": r["priority"], "band": r["priority_band"],
                                 "pattern": r["pattern"]}
               for r in query("SELECT subject_id, priority, priority_band, pattern FROM alerts"
                              " WHERE run_id=? ORDER BY priority DESC", (run_id,))}
    for node in payload["nodes"]:
        key = node.get("address") or node.get("txid")
        if node.get("kind") == "address" and key:
            node["score"] = scores.get(key)
            node["alert"] = alerted.get(key)
        elif node.get("kind") == "transaction" and key:
            node["alert"] = alerted.get(key)
    return payload


@router.get("/runs/{run_id}/addresses/{address}")
def address_detail(run_id: str, address: str) -> dict[str, Any]:
    run = query_one("SELECT case_id, dataset_id FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise _error(404, "RUN_NOT_FOUND", f"No run with id {run_id}.")
    data = load_case_data(run["case_id"], run["dataset_id"])
    activity = data.addresses.get(address)
    if activity is None:
        raise _error(404, "ADDRESS_NOT_FOUND",
                     f"Address {address} does not appear in this run's records.")

    records = [data.tx(i) for i in activity.tx_indices]
    score = query_one("SELECT * FROM address_scores WHERE run_id=? AND address=?",
                      (run_id, address))
    endpoints: dict[str, int] = {}
    for record in records:
        for obs in record.observations:
            if obs.src_ip:
                endpoints[obs.src_ip] = endpoints.get(obs.src_ip, 0) + 1

    return {
        "address": address,
        "observed_activity": {
            "transaction_count": len(records),
            "received_sats": activity.received_sats,
            "spent_sats": activity.spent_sats,
            "incoming_transactions": len(set(activity.receives)),
            "outgoing_transactions": len(set(activity.spends)),
            "first_seen": records[0].observed_at if records else None,
            "last_seen": records[-1].observed_at if records else None,
        },
        "balance_note": (
            "These totals cover the imported period only. They are not a wallet balance "
            "and not a complete history for this address."),
        "score": ({**dict(score), "features": loads(score["features_json"], {})}
                  if score else None),
        "alerts": [dict(r) for r in query(
            "SELECT id, pattern, pattern_label, role_hypothesis, priority, priority_band,"
            " evidence_strength, review_state FROM alerts"
            " WHERE run_id=? AND subject_id=? ORDER BY priority DESC", (run_id, address))],
        "transactions": [
            {"txid": r.txid, "observed_at": r.observed_at, "row_number": r.row_number,
             "source_row_id": r.source_row_id,
             "direction": ("both" if address in r.input_addresses and address in r.output_addresses
                           else "out" if address in r.input_addresses else "in"),
             "input_count": r.input_count, "output_count": r.output_count,
             "amount_sats": sum(a for ad, a in r.outputs if ad == address)
                            or sum(a for ad, a in r.inputs if ad == address),
             "amount_discrepancy": r.has_amount_discrepancy}
            for r in records[:500]],
        "transactions_truncated": len(records) > 500,
        "network_observations": {
            "endpoints": sorted(endpoints.items(), key=lambda kv: -kv[1])[:25],
            "note": ("Endpoints where records involving this address were observed. This "
                     "is not evidence of transaction origin or address ownership."),
        },
    }


@router.get("/runs/{run_id}/clusters")
def list_clusters(run_id: str) -> list[dict[str, Any]]:
    rows = query("SELECT * FROM cluster_hypotheses WHERE run_id=? ORDER BY strength DESC",
                 (run_id,))
    return [{
        "id": r["id"], "members": loads(r["members_json"], []),
        "edges": loads(r["edges_json"], []), "rule_version": r["rule_version"],
        "strength": r["strength"], "exceptions": loads(r["exceptions"], []),
        "decision": r["decision"], "decision_note": r["decision_note"],
    } for r in rows]


@router.patch("/clusters/{cluster_id}")
def decide_cluster(cluster_id: str, payload: ClusterDecision) -> dict[str, Any]:
    if payload.decision not in ("open", "accepted", "rejected"):
        raise _error(400, "INVALID_DECISION",
                     "decision must be open, accepted or rejected.")
    row = query_one("SELECT run_id FROM cluster_hypotheses WHERE id=?", (cluster_id,))
    if row is None:
        raise _error(404, "CLUSTER_NOT_FOUND", f"No cluster {cluster_id}.")
    with write_tx() as conn:
        conn.execute("UPDATE cluster_hypotheses SET decision=?, decision_note=? WHERE id=?",
                     (payload.decision, payload.note, cluster_id))
        audit(conn, "cluster.decided", None, {
            "cluster_id": cluster_id, "decision": payload.decision,
            "note": ("The analyst decision is recorded alongside the machine output. "
                     "The original hypothesis and its supporting records are unchanged."),
        }, payload.actor)
    return {"cluster_id": cluster_id, "decision": payload.decision,
            "raw_observations_unchanged": True}


# -------------------------------------------------------------------------- exports
@router.post("/cases/{case_id}/exports", status_code=201)
def create_export(case_id: str, payload: ExportRequest) -> dict[str, Any]:
    if query_one("SELECT id FROM cases WHERE id=?", (case_id,)) is None:
        raise _error(404, "CASE_NOT_FOUND", f"No case with id {case_id}.")
    try:
        result = build_export(case_id, payload.run_id, payload.alert_ids, payload.actor)
    except LookupError as exc:
        raise _error(404, "RUN_NOT_FOUND", str(exc))
    except ValueError as exc:
        raise _error(409, "RUN_NOT_EXPORTABLE", str(exc))
    return {"export_id": result.export_id, "directory": str(result.directory),
            "files": result.files, "manifest": result.manifest}


@router.get("/exports/{export_id}/files/{filename}")
def download_export_file(export_id: str, filename: str):
    from fastapi.responses import FileResponse

    row = query_one("SELECT directory FROM exports WHERE id=?", (export_id,))
    if row is None:
        raise _error(404, "EXPORT_NOT_FOUND", f"No export {export_id}.")
    directory = config.EXPORT_DIR / export_id
    target = (directory / filename).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        raise _error(400, "INVALID_PATH", "The requested file is outside the export.")
    if not target.is_file():
        raise _error(404, "FILE_NOT_FOUND", f"No file {filename} in export {export_id}.")
    return FileResponse(target, filename=filename)


@router.get("/cases/{case_id}/audit")
def case_audit(case_id: str, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    rows = query(
        "SELECT * FROM audit_events WHERE case_id=? ORDER BY id DESC LIMIT ?",
        (case_id, limit))
    return {
        "events": [{**dict(r), "detail": loads(r["detail_json"], {})} for r in rows],
        "note": ("Audit history is append-only at application level. It is not "
                 "tamper-proof against anyone with filesystem access to the database."),
    }
