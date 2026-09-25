"""Import a dataset into a case.

Guarantees:
  * the exact uploaded bytes are archived before anything is parsed;
  * every input row lands in exactly one of accepted / warning / quarantined, and the
    three counts always reconcile to the total;
  * re-importing identical bytes into the same case is a no-op, not a duplicate;
  * the same TXID seen again with an identical payload is an extra observation, while a
    conflicting payload is quarantined rather than overwriting the first.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..db import audit, new_id, now_iso, query_one, write_tx
from .adapters import AdapterError, iter_rows
from .contract import (
    DEFAULT_FEE_TOLERANCE_SATS,
    ISSUE_TEXT,
    IssueCode,
    RowIssue,
    ValidationMode,
)
from .normalize import normalize_row, payload_hash
from .sniff import sniff_bytes

#: Rows are flushed to SQLite in blocks of this size to keep memory flat.
BATCH_ROWS = 2000


@dataclass(slots=True)
class ImportSummary:
    dataset_id: str
    case_id: str
    original_name: str
    sha256: str
    byte_size: int
    detected_format: str
    validation_mode: str
    total_rows: int = 0
    accepted_clean: int = 0
    accepted_warning: int = 0
    quarantined: int = 0
    transactions_stored: int = 0
    observations_stored: int = 0
    label_fields_removed: list[str] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    schema: dict[str, Any] = field(default_factory=dict)
    reused_existing: bool = False

    @property
    def reconciles(self) -> bool:
        return self.total_rows == self.accepted_clean + self.accepted_warning + self.quarantined

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "case_id": self.case_id,
            "original_name": self.original_name,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "detected_format": self.detected_format,
            "validation_mode": self.validation_mode,
            "total_rows": self.total_rows,
            "accepted_clean": self.accepted_clean,
            "accepted_warning": self.accepted_warning,
            "quarantined": self.quarantined,
            "transactions_stored": self.transactions_stored,
            "observations_stored": self.observations_stored,
            "label_fields_removed": self.label_fields_removed,
            "issue_counts": self.issue_counts,
            "issue_text": {c: ISSUE_TEXT[IssueCode(c)] for c in self.issue_counts},
            "schema": self.schema,
            "reconciles": self.reconciles,
            "reused_existing": self.reused_existing,
        }


def archive_source(data: bytes, sha256: str) -> Path:
    """Store the uploaded bytes verbatim in the restricted archive."""
    config.ensure_dirs()
    path = config.SOURCE_ARCHIVE_DIR / f"{sha256}.bin"
    if not path.exists():
        path.write_bytes(data)
    return path


def import_dataset(
    case_id: str,
    data: bytes,
    original_name: str,
    mode: ValidationMode = ValidationMode.COMPATIBILITY,
    fee_tolerance_sats: int = DEFAULT_FEE_TOLERANCE_SATS,
) -> ImportSummary:
    sha256 = hashlib.sha256(data).hexdigest()

    existing = query_one(
        "SELECT * FROM datasets WHERE case_id=? AND sha256=? AND validation_mode=? AND status='complete'",
        (case_id, sha256, mode.value),
    )
    if existing is not None:
        summary = ImportSummary(
            dataset_id=existing["id"], case_id=case_id, original_name=existing["original_name"],
            sha256=sha256, byte_size=existing["byte_size"],
            detected_format=existing["detected_format"], validation_mode=mode.value,
            total_rows=existing["total_rows"], accepted_clean=existing["accepted_clean"],
            accepted_warning=existing["accepted_warning"], quarantined=existing["quarantined"],
            schema=json.loads(existing["schema_json"] or "{}"), reused_existing=True,
        )
        return summary

    sniff = sniff_bytes(data, original_name)
    archive_path = archive_source(data, sha256)
    dataset_id = new_id("ds")

    summary = ImportSummary(
        dataset_id=dataset_id, case_id=case_id, original_name=original_name, sha256=sha256,
        byte_size=len(data), detected_format=sniff.fmt, validation_mode=mode.value,
        schema={
            "columns": sniff.header,
            "delimiter": sniff.delimiter,
            "encoding": sniff.encoding,
            "had_bom": sniff.had_bom,
            "newline": sniff.newline,
            "note": sniff.note,
            "amount_unit": "BTC decimal in source, stored as integer satoshis",
            "array_separator": ";",
            "timestamp_policy": "Parsed as timezone-aware UTC; naive values read as UTC.",
            "fee_tolerance_sats": fee_tolerance_sats,
        },
    )

    with write_tx() as conn:
        conn.execute(
            """INSERT INTO datasets (id, case_id, original_name, archive_path, sha256, byte_size,
                                     detected_format, validation_mode, imported_at, schema_json, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,'importing')""",
            (dataset_id, case_id, original_name, str(archive_path), sha256, len(data),
             sniff.fmt, mode.value, now_iso(), json.dumps(summary.schema, sort_keys=True)),
        )

        try:
            rows = iter_rows(data, sniff)
        except AdapterError as exc:
            conn.execute("UPDATE datasets SET status='failed' WHERE id=?", (dataset_id,))
            audit(conn, "dataset.import_failed", case_id,
                  {"dataset_id": dataset_id, "error": str(exc)})
            raise

        # txid -> (payload_hash, transaction rowid, row_number) within this case
        seen: dict[str, tuple[str, int, int]] = {}
        for row in conn.execute(
            "SELECT t.txid, t.payload_hash, t.id, s.row_number FROM transactions t "
            "JOIN source_rows s ON s.id = t.source_row_id WHERE t.case_id=?",
            (case_id,),
        ):
            seen[row["txid"]] = (row["payload_hash"], row["id"], row["row_number"])

        label_fields: set[str] = set()
        issue_counts: dict[str, int] = {}
        pending_io: list[tuple] = []

        def record_issues(source_row_id: int | None, row_number: int, issues: list[RowIssue]) -> None:
            for issue in issues:
                issue_counts[issue.code.value] = issue_counts.get(issue.code.value, 0) + 1
                conn.execute(
                    """INSERT INTO validation_issues
                       (dataset_id, source_row_id, row_number, code, severity, field, message)
                       VALUES (?,?,?,?,?,?,?)""",
                    (dataset_id, source_row_id, row_number, issue.code.value,
                     issue.severity, issue.field, issue.message),
                )

        try:
            for row_number, raw in rows:
                summary.total_rows += 1
                outcome = normalize_row(raw, row_number, mode, fee_tolerance_sats)
                label_fields.update(outcome.removed_label_fields)

                status = outcome.status
                tx = outcome.transaction
                issues = list(outcome.issues)
                duplicate_observation = False

                if tx is not None:
                    phash = payload_hash(tx)
                    prior = seen.get(tx.txid)
                    if prior is not None:
                        if prior[0] == phash:
                            duplicate_observation = True
                            issues.append(RowIssue(
                                IssueCode.DUPLICATE_OBSERVATION, "warning",
                                ISSUE_TEXT[IssueCode.DUPLICATE_OBSERVATION]
                                + f" First seen at row {prior[2]}.", "txid"))
                            status = "warning"
                        else:
                            issues.append(RowIssue(
                                IssueCode.TXID_CONFLICT, "error",
                                ISSUE_TEXT[IssueCode.TXID_CONFLICT]
                                + f" Conflicts with row {prior[2]}.", "txid"))
                            status = "quarantined"
                            tx = None

                cursor = conn.execute(
                    "INSERT INTO source_rows (dataset_id, row_number, raw_json, status) VALUES (?,?,?,?)",
                    (dataset_id, row_number,
                     json.dumps(outcome.raw, sort_keys=True, ensure_ascii=False), status),
                )
                source_row_id = int(cursor.lastrowid)
                record_issues(source_row_id, row_number, issues)

                if status == "quarantined":
                    summary.quarantined += 1
                    continue
                if status == "warning":
                    summary.accepted_warning += 1
                else:
                    summary.accepted_clean += 1

                assert tx is not None
                if duplicate_observation:
                    tx_rowid = seen[tx.txid][1]
                else:
                    cursor = conn.execute(
                        """INSERT INTO transactions
                           (case_id, dataset_id, txid, observed_at, observed_ts, fee_sats,
                            total_in_sats, total_out_sats, residual_sats, input_count, output_count,
                            script_type, quality_status, payload_hash, source_row_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (case_id, dataset_id, tx.txid, tx.observed_at, tx.observed_ts, tx.fee_sats,
                         tx.total_in_sats, tx.total_out_sats, tx.residual_sats,
                         len(tx.inputs), len(tx.outputs), tx.script_type,
                         "warning" if status == "warning" else "clean",
                         payload_hash(tx), source_row_id),
                    )
                    tx_rowid = int(cursor.lastrowid)
                    seen[tx.txid] = (payload_hash(tx), tx_rowid, row_number)
                    summary.transactions_stored += 1
                    pending_io.extend(
                        (tx_rowid, case_id, e.side, e.position, e.address, e.amount_sats)
                        for e in tx.entries
                    )
                    if len(pending_io) >= BATCH_ROWS:
                        conn.executemany(
                            "INSERT INTO tx_io (transaction_id, case_id, side, position, address, amount_sats)"
                            " VALUES (?,?,?,?,?,?)", pending_io)
                        pending_io.clear()

                conn.execute(
                    """INSERT OR IGNORE INTO observations
                       (transaction_id, case_id, source_row_id, observed_at, observed_ts,
                        src_ip, src_port, dst_ip, dst_port)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (tx_rowid, case_id, source_row_id, tx.observed_at, tx.observed_ts,
                     tx.src_ip, tx.src_port, tx.dst_ip, tx.dst_port),
                )
                summary.observations_stored += 1

            if pending_io:
                conn.executemany(
                    "INSERT INTO tx_io (transaction_id, case_id, side, position, address, amount_sats)"
                    " VALUES (?,?,?,?,?,?)", pending_io)
                pending_io.clear()
        except AdapterError as exc:
            conn.execute("UPDATE datasets SET status='failed' WHERE id=?", (dataset_id,))
            audit(conn, "dataset.import_failed", case_id,
                  {"dataset_id": dataset_id, "error": str(exc)})
            raise

        summary.label_fields_removed = sorted(label_fields)
        if summary.label_fields_removed:
            # One dataset-level record: the removal applies uniformly to every row.
            names = ", ".join(summary.label_fields_removed)
            conn.execute(
                """INSERT INTO validation_issues
                   (dataset_id, source_row_id, row_number, code, severity, field, message)
                   VALUES (?, NULL, NULL, ?, 'info', ?, ?)""",
                (dataset_id, IssueCode.LABEL_COLUMN_REMOVED.value, names,
                 ISSUE_TEXT[IssueCode.LABEL_COLUMN_REMOVED]
                 + f" Removed before any canonical record was created: {names}."
                   f" Affected all {summary.total_rows} rows."),
            )
            issue_counts[IssueCode.LABEL_COLUMN_REMOVED.value] = 1
        summary.issue_counts = dict(sorted(issue_counts.items()))
        summary.schema["label_fields_removed"] = summary.label_fields_removed

        conn.execute(
            """UPDATE datasets SET total_rows=?, accepted_clean=?, accepted_warning=?,
                   quarantined=?, label_column_removed=?, schema_json=?, status='complete'
               WHERE id=?""",
            (summary.total_rows, summary.accepted_clean, summary.accepted_warning,
             summary.quarantined, 1 if summary.label_fields_removed else 0,
             json.dumps(summary.schema, sort_keys=True), dataset_id),
        )
        conn.execute("UPDATE cases SET updated_at=? WHERE id=?", (now_iso(), case_id))
        audit(conn, "dataset.imported", case_id, {
            "dataset_id": dataset_id, "sha256": sha256, "rows": summary.total_rows,
            "mode": mode.value, "label_fields_removed": summary.label_fields_removed,
        })

    if not summary.reconciles:  # defensive: the counts must always add up
        raise RuntimeError(
            f"Import counts do not reconcile for dataset {dataset_id}: "
            f"{summary.total_rows} != {summary.accepted_clean}+{summary.accepted_warning}"
            f"+{summary.quarantined}")
    return summary
