"""Build a reproducible evidence package for a selected case and run.

The package contains everything needed to understand a finding and to reproduce the run
that produced it: the alert cards, the exact source rows behind them, the dataset hash,
the detector and model versions, the configuration, and the stated limitations.

What it is not: the manifest lets someone detect that the package changed. It is not a
legal certification, it does not establish the authenticity of the underlying data, and
it is not tamper-proof against anyone who can write to the filesystem.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .. import config
from ..db import audit, loads, new_id, now_iso, query, query_one, write_tx
from ..scoring.priority import PRIORITY_POLICY

EXPORT_FORMAT_VERSION = "chainlens-export/v1"

LIMITATIONS: list[str] = [
    "The supplied records contain no previous-output references, no output spending "
    "indices, no block height and no virtual transaction size. Exact UTXO tracing and "
    "fee-rate calculation are therefore not possible, and any continuity between "
    "transactions shown here is inferred from address reuse and timing.",
    "A network observation records where a record was seen. It does not establish where "
    "a transaction originated, and it never establishes who controls an address.",
    "Clustering is a hypothesis drawn from repeated co-spending. It is not verified "
    "ownership, and collaborative transactions are excluded from it because the "
    "common-input assumption does not hold for them.",
    "Review priority orders an analyst's queue. It is not a probability of wrongdoing.",
    "Anomaly percentiles rank an address against the population the model was fitted "
    "on. They are not calibrated probabilities.",
    "Roles describe a position in an observed payment structure, not conduct. A payment "
    "participant is commonly the injured party.",
    "Addresses in the supplied synthetic dataset do not verify as real-chain addresses "
    "and are treated as opaque identifiers.",
    "This package allows changes to be detected against the hashes recorded in it. It "
    "is not a legal certification, and it is not tamper-proof against anyone with "
    "filesystem access.",
]


@dataclass(slots=True)
class ExportResult:
    export_id: str
    directory: Path
    files: list[str]
    manifest: dict[str, Any]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect(run_id: str, alert_ids: list[str] | None) -> tuple[dict, dict, list[dict]]:
    run = query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise LookupError(f"run {run_id} not found")
    dataset = query_one("SELECT * FROM datasets WHERE id=?", (run["dataset_id"],))
    case = query_one("SELECT * FROM cases WHERE id=?", (run["case_id"],))

    sql = "SELECT * FROM alerts WHERE run_id=?"
    params: list[Any] = [run_id]
    if alert_ids:
        sql += f" AND id IN ({','.join('?' * len(alert_ids))})"
        params.extend(alert_ids)
    sql += " ORDER BY priority DESC, id ASC"

    alerts: list[dict] = []
    for row in query(sql, tuple(params)):
        alert = dict(row)
        alert["priority_components"] = loads(row["priority_components"], {})
        alert["evidence_reasons"] = loads(row["evidence_reasons"], [])
        alert["alternatives"] = loads(row["alternatives"], [])
        alert["caveats"] = loads(row["caveats"], [])
        alert["indicators"] = loads(row["indicators"], {})
        alert["evidence"] = [
            {**dict(e), "detail": loads(e["detail_json"], {})}
            for e in query("SELECT * FROM alert_evidence WHERE alert_id=? ORDER BY id",
                           (row["id"],))
        ]
        for item in alert["evidence"]:
            item.pop("detail_json", None)
        alert["notes"] = [dict(n) for n in query(
            "SELECT author, body, created_at FROM notes WHERE alert_id=? ORDER BY id",
            (row["id"],))]
        alert["review_history"] = [dict(r) for r in query(
            "SELECT actor, from_state, to_state, reason, at FROM review_events"
            " WHERE alert_id=? ORDER BY id", (row["id"],))]
        alerts.append(alert)
    return dict(case or {}), dict(dataset or {}), alerts


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _pdf(path: Path, case: dict, dataset: dict, run: dict, alerts: list[dict],
         generated_at: str) -> None:
    """Render the case summary. Every number comes from the stored run."""
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8.5, leading=11.5,
                          alignment=TA_LEFT, spaceAfter=4)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=10,
                           textColor=colors.HexColor("#475569"))
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=17, leading=21,
                        textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11.5, leading=14,
                        textColor=colors.HexColor("#0f172a"), spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=9.5, leading=12,
                        textColor=colors.HexColor("#1e293b"), spaceBefore=6, spaceAfter=2)

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"ChainLens case summary - {case.get('title', '')}",
        author="ChainLens (offline)",
    )
    story: list[Any] = []

    story.append(Paragraph("ChainLens case summary", h1))
    story.append(Paragraph(
        "Investigative working document. Findings are behavioural observations with "
        "stated uncertainty; none of them establishes identity or intent.", small))
    story.append(Spacer(1, 6))

    stats = loads(run.get("stats_json"), {})
    meta = [
        ["Case", case.get("title", "")],
        ["Case ID", case.get("id", "")],
        ["Analysis run", run.get("id", "")],
        ["Run completed", run.get("finished_at") or "-"],
        ["Package generated", generated_at],
        ["Source file", dataset.get("original_name", "")],
        ["Source SHA-256", dataset.get("sha256", "")],
        ["Rows imported", str(dataset.get("total_rows", ""))],
        ["Validation mode", dataset.get("validation_mode", "")],
        ["Detector version", run.get("detector_version", "")],
        ["Model version", f"{run.get('model_version') or 'none'} ({run.get('model_status', '')})"],
        ["Alerts in package", str(len(alerts))],
    ]
    table = Table(meta, colWidths=[38 * mm, 136 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
    ]))
    story.append(table)

    story.append(Paragraph("Dataset quality", h2))
    story.append(Paragraph(
        f"{dataset.get('accepted_clean', 0)} rows accepted without warning, "
        f"{dataset.get('accepted_warning', 0)} accepted with warnings and "
        f"{dataset.get('quarantined', 0)} quarantined, reconciling to "
        f"{dataset.get('total_rows', 0)} input rows. "
        f"{stats.get('coverage', {}).get('transactions_with_amount_discrepancy', 0)} "
        f"transactions have amounts that do not reconcile with their supplied fee; these "
        f"are data-quality findings and are not treated as suspicious behaviour.", body))
    if dataset.get("label_column_removed"):
        story.append(Paragraph(
            "An evaluation label column was present in the source file and was removed at "
            "the ingestion boundary, before any analysable record was created.", body))

    story.append(Paragraph("Scoring policy", h2))
    story.append(Paragraph(
        f"{PRIORITY_POLICY['formula']}. Rules combine by {PRIORITY_POLICY['rule_combination']}. "
        f"Bands: high {PRIORITY_POLICY['bands']['high']}, medium "
        f"{PRIORITY_POLICY['bands']['medium']}, low {PRIORITY_POLICY['bands']['low']}. "
        f"{PRIORITY_POLICY['not_a_probability']}", body))

    story.append(Paragraph("Findings", h2))
    if not alerts:
        story.append(Paragraph("No alerts were selected for this package.", body))

    for index, alert in enumerate(alerts, start=1):
        block: list[Any] = [Paragraph(
            f"{index}. {alert['pattern_label']} &mdash; {alert['subject_type']} "
            f"{alert['subject_id']}", h3)]
        block.append(Paragraph(
            f"Alert {alert['id']} | priority {alert['priority']:.2f} "
            f"({alert['priority_band']}) | evidence strength {alert['evidence_strength']} "
            f"| role {alert['role_hypothesis']} | review state {alert['review_state']} "
            f"| period {alert['period_start']} to {alert['period_end']}", small))
        block.append(Paragraph("<b>What was observed.</b> " + alert["explanation"], body))
        if alert["alternatives"]:
            block.append(Paragraph("<b>Other explanations that fit.</b> "
                                   + " ".join(alert["alternatives"]), body))
        if alert["caveats"]:
            block.append(Paragraph("<b>Limitations.</b> " + " ".join(alert["caveats"]), body))
        if alert["evidence_reasons"]:
            block.append(Paragraph("<b>Why this evidence strength.</b> "
                                   + " ".join(alert["evidence_reasons"]), body))
        rows = alert["evidence"][:12]
        if rows:
            data = [["Kind", "TXID", "Source row"]] + [
                [r["kind"], (r["txid"] or "")[:32], str(r["row_number"] or "")] for r in rows]
            evidence_table = Table(data, colWidths=[20 * mm, 118 * mm, 20 * mm])
            evidence_table.setStyle(TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 6.8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            block.append(Spacer(1, 2))
            block.append(evidence_table)
            if len(alert["evidence"]) > len(rows):
                block.append(Paragraph(
                    f"{len(alert['evidence']) - len(rows)} further supporting records are "
                    f"listed in evidence.csv.", small))
        for note in alert["notes"]:
            block.append(Paragraph(
                f"<b>Analyst note</b> ({note['author']}, {note['created_at']}): "
                f"{note['body']}", body))
        block.append(Spacer(1, 4))
        story.append(KeepTogether(block))

    story.append(PageBreak())
    story.append(Paragraph("Limitations of this package", h2))
    for item in LIMITATIONS:
        story.append(Paragraph("&bull; " + item, body))

    doc.build(story)


def build_export(case_id: str, run_id: str, alert_ids: list[str] | None = None,
                 actor: str = "local") -> ExportResult:
    """Create the export bundle on disk and return its manifest."""
    config.ensure_dirs()
    run_row = query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run_row is None:
        raise LookupError(f"run {run_id} not found")
    if run_row["status"] != "complete":
        raise ValueError(
            f"run {run_id} is '{run_row['status']}'. Only a completed run can be "
            f"exported, so that a partial analysis is never presented as a result.")

    case, dataset, alerts = _collect(run_id, alert_ids)
    export_id = new_id("exp")
    directory = config.EXPORT_DIR / export_id
    directory.mkdir(parents=True, exist_ok=True)
    generated_at = now_iso()
    run = dict(run_row)

    # --- CSV: one row per alert, one row per supporting record ---------------------
    _write_csv(directory / "alerts.csv", [
        {
            "alert_id": a["id"], "subject_type": a["subject_type"],
            "subject_id": a["subject_id"], "pattern": a["pattern"],
            "pattern_label": a["pattern_label"], "role_hypothesis": a["role_hypothesis"],
            "priority": a["priority"], "priority_band": a["priority_band"],
            "evidence_strength": a["evidence_strength"],
            "period_start": a["period_start"], "period_end": a["period_end"],
            "review_state": a["review_state"], "explanation": a["explanation"],
        } for a in alerts
    ], ["alert_id", "subject_type", "subject_id", "pattern", "pattern_label",
        "role_hypothesis", "priority", "priority_band", "evidence_strength",
        "period_start", "period_end", "review_state", "explanation"])

    evidence_rows = [
        {
            "alert_id": a["id"], "subject_id": a["subject_id"], "kind": e["kind"],
            "txid": e["txid"], "source_row_number": e["row_number"],
            "source_row_id": e["source_row_id"],
            "detail": json.dumps(e["detail"], sort_keys=True),
        }
        for a in alerts for e in a["evidence"]
    ]
    _write_csv(directory / "evidence.csv", evidence_rows,
               ["alert_id", "subject_id", "kind", "txid", "source_row_number",
                "source_row_id", "detail"])

    # --- JSON: the full findings with their components -------------------------------
    findings_payload = {
        "export_format": EXPORT_FORMAT_VERSION,
        "generated_at": generated_at,
        "case": {k: case.get(k) for k in ("id", "title", "description", "state")},
        "run": {
            "id": run["id"], "status": run["status"],
            "started_at": run["started_at"], "finished_at": run["finished_at"],
            "detector_version": run["detector_version"],
            "model_version": run["model_version"], "model_status": run["model_status"],
            "configuration": loads(run["config_json"], {}),
            "statistics": loads(run["stats_json"], {}),
        },
        "dataset": {
            "id": dataset.get("id"), "original_name": dataset.get("original_name"),
            "sha256": dataset.get("sha256"), "byte_size": dataset.get("byte_size"),
            "detected_format": dataset.get("detected_format"),
            "validation_mode": dataset.get("validation_mode"),
            "total_rows": dataset.get("total_rows"),
            "accepted_clean": dataset.get("accepted_clean"),
            "accepted_warning": dataset.get("accepted_warning"),
            "quarantined": dataset.get("quarantined"),
            "label_column_removed": bool(dataset.get("label_column_removed")),
            "schema": loads(dataset.get("schema_json"), {}),
        },
        "alerts": alerts,
        "limitations": LIMITATIONS,
        "rerun_instructions": {
            "note": (
                "Re-importing the same source file in the same validation mode and "
                "running with the configuration recorded above reproduces these "
                "findings and their identifiers."),
            "source_sha256": dataset.get("sha256"),
            "validation_mode": dataset.get("validation_mode"),
            "detector_version": run["detector_version"],
            "model_version": run["model_version"],
            "command": (
                "python -m chainlens.ml.train --input <training file> && "
                "start the application, import the source file and run an analysis "
                "with the recorded configuration"),
        },
    }
    (directory / "findings.json").write_text(
        json.dumps(findings_payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

    (directory / "LIMITATIONS.txt").write_text(
        "ChainLens evidence package - limitations\n"
        "========================================\n\n"
        + "\n\n".join(f"- {item}" for item in LIMITATIONS) + "\n",
        encoding="utf-8")

    _pdf(directory / "case_summary.pdf", case, dataset, run, alerts, generated_at)

    # --- manifest over everything written -------------------------------------------
    files = sorted(p.name for p in directory.iterdir() if p.name != "manifest.json")
    manifest = {
        "export_format": EXPORT_FORMAT_VERSION,
        "export_id": export_id,
        "generated_at": generated_at,
        "generated_by": actor,
        "case_id": case_id,
        "run_id": run_id,
        "alert_count": len(alerts),
        "selected_alert_ids": alert_ids or "all alerts in the run",
        "source_file": {
            "name": dataset.get("original_name"),
            "sha256": dataset.get("sha256"),
            "byte_size": dataset.get("byte_size"),
        },
        "detector_version": run["detector_version"],
        "model_version": run["model_version"],
        "model_status": run["model_status"],
        "configuration": loads(run["config_json"], {}),
        "priority_policy": PRIORITY_POLICY,
        "files": {name: _sha256_file(directory / name) for name in files},
        "integrity_note": (
            "The digests above let a recipient detect that a file in this package has "
            "changed. They do not certify the package legally, do not establish the "
            "authenticity of the source data, and are not tamper-proof against anyone "
            "who can write to this directory."),
        "timestamp_note": (
            "generated_at is when this package was produced. It is not an event time; "
            "event times are the observation timestamps inside the findings."),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    with write_tx() as conn:
        conn.execute(
            "INSERT INTO exports (id, case_id, run_id, directory, files_json, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (export_id, case_id, run_id, str(directory),
             json.dumps(files + ["manifest.json"]), generated_at))
        audit(conn, "export.created", case_id,
              {"export_id": export_id, "run_id": run_id, "alerts": len(alerts)}, actor)

    return ExportResult(export_id, directory, files + ["manifest.json"], manifest)
