"""Ingestion contract tests: arrays, exact amounts, validation policy, label removal."""
from __future__ import annotations

from decimal import Decimal

import pytest

from chainlens import db
from chainlens.ingest import ValidationMode, import_dataset, normalize_row
from chainlens.ingest.contract import IssueCode
from chainlens.ingest.normalize import btc_to_sats, strip_label_fields
from chainlens.ingest.sniff import sniff_bytes

from conftest import csv_row, make_csv


# --------------------------------------------------------------------------- amounts
@pytest.mark.parametrize(
    "text,expected",
    [
        ("0", 0),
        ("1", 100_000_000),
        ("0.00000001", 1),
        ("1.21885469", 121_885_469),
        ("21000000.00000000", 2_100_000_000_000_000),
        ("0.10000000", 10_000_000),
    ],
)
def test_btc_to_sats_is_exact(text, expected):
    assert btc_to_sats(text) == expected


def test_btc_to_sats_rejects_sub_satoshi():
    with pytest.raises(ValueError):
        btc_to_sats("0.000000001")


def test_btc_to_sats_avoids_binary_float_error():
    # 0.1 + 0.2 in binary floating point is not 0.3; the Decimal path must be exact.
    assert btc_to_sats("0.1") + btc_to_sats("0.2") == btc_to_sats("0.3")


# ------------------------------------------------------------------- arrays/positions
def test_semicolon_arrays_preserve_order_and_position():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "b" * 64,
        "input_addresses": "A1;A2;A3", "input_amounts": "1.00000000;2.00000000;3.00000000",
        "output_addresses": "B1;B2", "output_amounts": "5.50000000;0.49000000",
        "fee": "0.01000000",
    }
    outcome = normalize_row(raw, 1)
    assert outcome.status == "accepted"
    tx = outcome.transaction
    assert [(e.position, e.address, e.amount_sats) for e in tx.inputs] == [
        (0, "A1", 100_000_000), (1, "A2", 200_000_000), (2, "A3", 300_000_000)]
    assert [(e.position, e.address, e.amount_sats) for e in tx.outputs] == [
        (0, "B1", 550_000_000), (1, "B2", 49_000_000)]
    assert tx.total_in_sats == 600_000_000
    assert tx.residual_sats == 0


def test_repeated_address_occurrences_are_preserved():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "c" * 64,
        "input_addresses": "A1;A1", "input_amounts": "1.00000000;2.00000000",
        "output_addresses": "B1", "output_amounts": "3.00000000", "fee": "0.00000000",
    }
    outcome = normalize_row(raw, 1)
    assert len(outcome.transaction.inputs) == 2
    assert [e.address for e in outcome.transaction.inputs] == ["A1", "A1"]
    assert any(i.code is IssueCode.ADDRESS_REPEATED_IN_ROW for i in outcome.issues)


def test_array_length_mismatch_is_quarantined():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "d" * 64,
        "input_addresses": "A1;A2", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "0.99000000", "fee": "0.01000000",
    }
    outcome = normalize_row(raw, 1)
    assert outcome.status == "quarantined"
    assert outcome.transaction is None
    assert any(i.code is IssueCode.ARRAY_LENGTH_MISMATCH for i in outcome.issues)


def test_invalid_amount_is_quarantined():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "e" * 64,
        "input_addresses": "A1", "input_amounts": "not-a-number",
        "output_addresses": "B1", "output_amounts": "0.99000000", "fee": "0.01000000",
    }
    outcome = normalize_row(raw, 1)
    assert outcome.status == "quarantined"
    assert any(i.code is IssueCode.AMOUNT_INVALID for i in outcome.issues)


def test_missing_required_field_is_quarantined():
    outcome = normalize_row({"timestamp": "2026-08-01T00:00:00Z"}, 1)
    assert outcome.status == "quarantined"
    assert any(i.code is IssueCode.SCHEMA_MISSING_FIELD for i in outcome.issues)


# ----------------------------------------------------------------------------- policy
def test_fee_residual_within_tolerance_is_warning_not_repair():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "f" * 64,
        "input_addresses": "A1", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "0.99000000",
        "fee": "0.00999999",  # one satoshi short
    }
    outcome = normalize_row(raw, 1)
    assert outcome.status == "warning"
    assert outcome.transaction.fee_sats == 999_999, "the supplied fee must be preserved"
    assert outcome.transaction.residual_sats == 1
    assert any(i.code is IssueCode.FEE_RESIDUAL_WITHIN_TOLERANCE for i in outcome.issues)


def test_fee_residual_above_tolerance_differs_by_mode():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "0" * 64,
        "input_addresses": "A1", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "0.99000000", "fee": "0.00500000",
    }
    compat = normalize_row(raw, 1, ValidationMode.COMPATIBILITY)
    assert compat.status == "warning" and compat.transaction is not None
    assert compat.transaction.residual_sats == 500_000

    strict = normalize_row(raw, 1, ValidationMode.STRICT)
    assert strict.status == "quarantined" and strict.transaction is None
    assert any(i.code is IssueCode.FEE_RESIDUAL_ABOVE_TOLERANCE for i in strict.issues)


def test_synthetic_address_is_kept_as_identifier_with_info_issue():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "1" * 64,
        "input_addresses": "1Hh3oveGfuggu9VPfvcCaHRswkNF2ZgfhZ", "input_amounts": "1.00000000",
        "output_addresses": "3Kupkq4U8rLQDD4ayX6icnhRrGadvcVjVo", "output_amounts": "1.00000000",
        "fee": "0.00000000",
    }
    for mode in (ValidationMode.COMPATIBILITY, ValidationMode.STRICT):
        outcome = normalize_row(raw, 1, mode)
        assert outcome.status == "accepted", "a synthetic address must not fail the row"
        issue = next(i for i in outcome.issues if i.code is IssueCode.ADDRESS_CHECKSUM_UNVERIFIED)
        assert issue.severity == "info"


def test_invalid_ip_and_port_are_warnings_not_rejections():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "2" * 64,
        "input_addresses": "A1", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "1.00000000", "fee": "0.00000000",
        "src_ip": "999.1.1.1", "src_port": "70000",
    }
    outcome = normalize_row(raw, 1)
    assert outcome.status == "warning" and outcome.transaction is not None
    assert outcome.transaction.src_ip is None and outcome.transaction.src_port is None
    codes = {i.code for i in outcome.issues}
    assert IssueCode.IP_INVALID in codes and IssueCode.PORT_INVALID in codes


def test_port_8333_is_not_required():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "3" * 64,
        "input_addresses": "A1", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "1.00000000", "fee": "0.00000000",
        "dst_port": "1234",
    }
    outcome = normalize_row(raw, 1, ValidationMode.STRICT)
    assert outcome.status == "accepted"
    assert outcome.transaction.dst_port == 1234


def test_naive_and_offset_timestamps_normalise_to_utc():
    base = {
        "txid": "4" * 64, "input_addresses": "A1", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "1.00000000", "fee": "0.00000000",
    }
    a = normalize_row({**base, "timestamp": "2026-08-01T12:00:00Z"}, 1).transaction
    b = normalize_row({**base, "timestamp": "2026-08-01T14:00:00+02:00"}, 2).transaction
    c = normalize_row({**base, "timestamp": "2026-08-01T12:00:00"}, 3).transaction
    assert a.observed_at == b.observed_at == c.observed_at == "2026-08-01T12:00:00Z"
    assert a.observed_ts == b.observed_ts == c.observed_ts


# ------------------------------------------------------------------------ label safety
def test_label_columns_are_stripped_before_a_record_exists():
    raw = {"txid": "x", "is_planted_suspicious": "1", "pattern_type": "coinjoin_mixing",
           "is_suspicious": "1", "timestamp": "t"}
    sanitised, removed = strip_label_fields(raw)
    assert set(removed) == {"is_planted_suspicious", "pattern_type", "is_suspicious"}
    assert set(sanitised) == {"txid", "timestamp"}


def test_normalized_record_carries_no_label_field():
    raw = {
        "timestamp": "2026-08-01T00:00:00Z", "txid": "5" * 64,
        "input_addresses": "A1", "input_amounts": "1.00000000",
        "output_addresses": "B1", "output_amounts": "1.00000000", "fee": "0.00000000",
        "is_planted_suspicious": "1",
    }
    outcome = normalize_row(raw, 1)
    assert outcome.removed_label_fields == ["is_planted_suspicious"]
    serialised = repr(outcome.transaction)
    assert "planted" not in serialised and "suspicious" not in serialised


# ------------------------------------------------------------------------- file import
def test_import_counts_reconcile_and_label_is_removed(case_id):
    data = make_csv(
        csv_row(txid="a" * 64),
        csv_row(txid="b" * 64, fee="0.00500000"),          # residual above tolerance
        csv_row(txid="c" * 64, inputs="A1;A2", in_amounts="1.00000000"),  # mismatch
    )
    summary = import_dataset(case_id, data, "test.csv", ValidationMode.COMPATIBILITY)
    assert summary.total_rows == 3
    assert summary.reconciles
    assert summary.quarantined == 1
    assert summary.label_fields_removed == ["is_planted_suspicious"]

    stored = db.query("SELECT txid FROM transactions WHERE case_id=?", (case_id,))
    assert {r["txid"] for r in stored} == {"a" * 64, "b" * 64}


def test_sanitised_tables_never_contain_the_label_column(case_id, sample_csv_bytes):
    import_dataset(case_id, sample_csv_bytes, "ps3", ValidationMode.COMPATIBILITY)
    with db.read_conn() as conn:
        for table in ("transactions", "tx_io", "observations"):
            columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert not any("suspicious" in c or "pattern_type" in c or "label" in c
                           for c in columns), f"{table} exposes a label column"


def test_identical_reimport_is_idempotent(case_id):
    data = make_csv(csv_row(txid="a" * 64), csv_row(txid="b" * 64))
    first = import_dataset(case_id, data, "test.csv")
    second = import_dataset(case_id, data, "test.csv")
    assert second.reused_existing is True
    assert second.dataset_id == first.dataset_id
    assert db.query_one("SELECT COUNT(*) n FROM transactions WHERE case_id=?", (case_id,))["n"] == 2


def test_duplicate_observation_is_not_a_second_transaction(case_id):
    row = csv_row(txid="a" * 64, src_ip="10.0.0.1")
    duplicate = csv_row(txid="a" * 64, src_ip="10.9.9.9")  # same payload, new observation
    summary = import_dataset(case_id, make_csv(row, duplicate), "dup.csv")
    assert summary.transactions_stored == 1
    assert summary.observations_stored == 2
    assert summary.reconciles
    codes = {r["code"] for r in db.query(
        "SELECT code FROM validation_issues WHERE dataset_id=?", (summary.dataset_id,))}
    assert IssueCode.DUPLICATE_OBSERVATION.value in codes


def test_conflicting_payload_under_one_txid_is_quarantined(case_id):
    first = csv_row(txid="a" * 64, out_amounts="0.99900000")
    conflict = csv_row(txid="a" * 64, out_amounts="0.50000000", fee="0.50000000")
    summary = import_dataset(case_id, make_csv(first, conflict), "conflict.csv")
    assert summary.transactions_stored == 1, "the first record must not be overwritten"
    assert summary.quarantined == 1
    assert summary.reconciles
    kept = db.query_one("SELECT total_out_sats FROM transactions WHERE case_id=?", (case_id,))
    assert kept["total_out_sats"] == 99_900_000


def test_source_rows_are_archived_with_row_numbers(case_id):
    summary = import_dataset(case_id, make_csv(csv_row(), csv_row(txid="b" * 64)), "t.csv")
    rows = db.query("SELECT row_number, raw_json FROM source_rows WHERE dataset_id=? ORDER BY row_number",
                    (summary.dataset_id,))
    assert [r["row_number"] for r in rows] == [1, 2]
    # The restricted archive keeps the original row verbatim, label column included.
    assert "is_planted_suspicious" in rows[0]["raw_json"]


# ------------------------------------------------------------------------------ sniff
def test_sniffer_identifies_extensionless_csv_with_bom_and_crlf():
    data = b"\xef\xbb\xbf" + make_csv(csv_row())
    result = sniff_bytes(data, "no_extension")
    assert result.fmt == "csv"
    assert result.had_bom is True
    assert result.encoding == "utf-8-sig"
    assert result.newline == "crlf"
    assert "timestamp" in result.header


def test_sniffer_identifies_json_and_xml():
    assert sniff_bytes(b'[{"txid":"a"}]').fmt == "json"
    assert sniff_bytes(b"<transactions><transaction/></transactions>").fmt == "xml"


def test_real_sample_imports_with_expected_shape(case_id, sample_csv_bytes):
    """The supplied file must import fully, with every row accounted for."""
    summary = import_dataset(case_id, sample_csv_bytes, "ps3_transactions",
                             ValidationMode.COMPATIBILITY)
    assert summary.total_rows == 10_000
    assert summary.reconciles
    assert summary.quarantined == 0
    assert summary.transactions_stored == 10_000
    assert summary.label_fields_removed == ["is_planted_suspicious"]
