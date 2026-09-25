"""Shared fixtures. Every test runs against a throwaway database and data directory."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from chainlens import config, db  # noqa: E402

SAMPLES = BACKEND.parent / "data" / "samples"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point every writable path at a per-test temporary directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(config, "SOURCE_ARCHIVE_DIR", tmp_path / "source_archive")
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path / "predictions")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(db, "_INIT_DONE", False)
    config.ensure_dirs()
    db.init_db(force=True)
    yield tmp_path
    monkeypatch.setattr(db, "_INIT_DONE", False)


@pytest.fixture
def case_id() -> str:
    """A case to import into."""
    cid = db.new_id("case")
    with db.write_tx() as conn:
        conn.execute(
            "INSERT INTO cases (id, title, description, state, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (cid, "Test case", "", "draft", db.now_iso(), db.now_iso()),
        )
    return cid


@pytest.fixture(scope="session")
def sample_csv_bytes() -> bytes:
    path = SAMPLES / "ps3_transactions"
    if not path.exists():
        pytest.skip(f"sample dataset not present at {path}")
    return path.read_bytes()


@pytest.fixture(scope="session")
def sample_ground_truth() -> Path:
    path = SAMPLES / "ps4_ground_truth"
    if not path.exists():
        pytest.skip(f"ground truth not present at {path}")
    return path


CSV_HEADER = (
    "timestamp,src_ip,src_port,dst_ip,dst_port,txid,input_addresses,output_addresses,"
    "input_amounts,output_amounts,fee,script_type,is_planted_suspicious"
)


def csv_row(
    txid: str = "a" * 64,
    timestamp: str = "2026-08-01T00:00:00Z",
    inputs: str = "A1",
    outputs: str = "B1",
    in_amounts: str = "1.00000000",
    out_amounts: str = "0.99900000",
    fee: str = "0.00100000",
    script_type: str = "P2PKH",
    src_ip: str = "10.0.0.1",
    src_port: str = "40000",
    dst_ip: str = "10.0.0.2",
    dst_port: str = "8333",
    planted: str = "0",
) -> str:
    return ",".join([timestamp, src_ip, src_port, dst_ip, dst_port, txid, inputs,
                     outputs, in_amounts, out_amounts, fee, script_type, planted])


def make_csv(*rows: str) -> bytes:
    return ("\r\n".join([CSV_HEADER, *rows]) + "\r\n").encode("utf-8")
