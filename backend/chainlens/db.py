"""SQLite access layer.

Why this shape: the prototype has exactly one writer process but several logical
writers inside it (the HTTP handlers and the analysis worker thread). SQLite tolerates
that only if writes are serialised, so every write goes through :func:`write_tx`, which
holds a single process-wide lock and an IMMEDIATE transaction. Reads use short-lived
connections in WAL mode and do not take the lock.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

_WRITE_LOCK = threading.RLock()
_INIT_DONE = False
_INIT_LOCK = threading.Lock()


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or config.DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(force: bool = False) -> None:
    """Create tables if they do not exist. Safe to call repeatedly."""
    global _INIT_DONE
    with _INIT_LOCK:
        if _INIT_DONE and not force:
            return
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        with _WRITE_LOCK:
            conn = _connect()
            try:
                conn.executescript(schema)
            finally:
                conn.close()
        _INIT_DONE = True


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx() -> Iterator[sqlite3.Connection]:
    """Serialised write transaction. Commits on success, rolls back on exception."""
    init_db()
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()


def query(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    with read_conn() as conn:
        return list(conn.execute(sql, params))


def query_one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    with read_conn() as conn:
        return conn.execute(sql, params).fetchone()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def audit(conn: sqlite3.Connection, action: str, case_id: str | None = None,
          detail: dict[str, Any] | None = None, actor: str = "local") -> None:
    """Append an audit event inside an existing write transaction.

    Application-level append-only. This is not tamper-proof against anyone with
    filesystem access to the database, and is not presented as such.
    """
    conn.execute(
        "INSERT INTO audit_events (case_id, actor, action, detail_json, at) VALUES (?,?,?,?,?)",
        (case_id, actor, action, json.dumps(detail or {}, sort_keys=True), now_iso()),
    )
