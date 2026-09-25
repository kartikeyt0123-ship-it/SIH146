"""A local background worker for analysis runs, with progress polling.

One worker thread at a time executes a run, so the CPU-heavy work never blocks an HTTP
request and SQLite never sees two concurrent writers. Progress is written to the run row
and polled by the interface; there is no message broker and no external service.
"""
from __future__ import annotations

import json
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any

from ..db import audit, now_iso, query_one, write_tx
from ..detectors.config import DetectorConfig
from .pipeline import RunCancelled, execute_run


@dataclass
class RunHandle:
    run_id: str
    case_id: str
    thread: threading.Thread | None = None
    cancel_flag: threading.Event = field(default_factory=threading.Event)


class RunManager:
    """Starts, tracks and cancels analysis runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handles: dict[str, RunHandle] = {}
        #: Only one analysis executes at a time on this local installation.
        self._slot = threading.Semaphore(1)

    def start(
        self,
        run_id: str,
        case_id: str,
        dataset_id: str,
        detector_config: DetectorConfig,
        model_name: str = "default",
    ) -> RunHandle:
        handle = RunHandle(run_id=run_id, case_id=case_id)
        with self._lock:
            self._handles[run_id] = handle

        def target() -> None:
            self._slot.acquire()
            try:
                self._execute(handle, dataset_id, detector_config, model_name)
            finally:
                self._slot.release()

        thread = threading.Thread(target=target, name=f"chainlens-run-{run_id}", daemon=True)
        handle.thread = thread
        thread.start()
        return handle

    def _execute(self, handle: RunHandle, dataset_id: str,
                 detector_config: DetectorConfig, model_name: str) -> None:
        run_id, case_id = handle.run_id, handle.case_id
        with write_tx() as conn:
            conn.execute(
                "UPDATE runs SET status='running', stage='loading', progress=0.0, started_at=?"
                " WHERE id=?", (now_iso(), run_id))
            conn.execute("UPDATE cases SET state='analysing', updated_at=? WHERE id=?",
                         (now_iso(), case_id))

        def on_progress(stage: str, progress: float, detail: dict[str, Any]) -> None:
            with write_tx() as conn:
                conn.execute("UPDATE runs SET stage=?, progress=? WHERE id=?",
                             (stage, progress, run_id))

        try:
            execute_run(
                run_id=run_id, case_id=case_id, dataset_id=dataset_id,
                detector_config=detector_config, model_name=model_name,
                on_progress=on_progress,
                should_cancel=handle.cancel_flag.is_set,
            )
            with write_tx() as conn:
                conn.execute("UPDATE cases SET state='ready_for_review', updated_at=? WHERE id=?",
                             (now_iso(), case_id))
        except RunCancelled as exc:
            self._fail(run_id, case_id, "cancelled", str(exc))
        except Exception as exc:  # noqa: BLE001 - the run row must record any failure
            detail = f"{type(exc).__name__}: {exc}"
            self._fail(run_id, case_id, "failed", detail, traceback.format_exc())

    def _fail(self, run_id: str, case_id: str, status: str, error: str,
              trace: str | None = None) -> None:
        """Record a failed or cancelled run, leaving no partial results visible.

        Any alerts the run wrote are removed, because a failed run must never be shown
        as though it produced results, and the previous run's alerts must never be
        presented as this run's output.
        """
        with write_tx() as conn:
            conn.execute("DELETE FROM alerts WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM address_scores WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM cluster_hypotheses WHERE run_id=?", (run_id,))
            conn.execute(
                "UPDATE runs SET status=?, stage=?, finished_at=?, error=? WHERE id=?",
                (status, status, now_iso(), error, run_id))
            conn.execute("UPDATE cases SET state='draft', updated_at=? WHERE id=?",
                         (now_iso(), case_id))
            audit(conn, f"run.{status}", case_id,
                  {"run_id": run_id, "error": error, "traceback": trace})

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None:
            return False
        handle.cancel_flag.set()
        return True

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(run_id)
        return bool(handle and handle.thread and handle.thread.is_alive())

    def join(self, run_id: str, timeout: float | None = None) -> None:
        """Wait for a run to finish. Used by tests and the command-line entry points."""
        with self._lock:
            handle = self._handles.get(run_id)
        if handle and handle.thread:
            handle.thread.join(timeout)


#: Process-wide manager used by the API.
run_manager = RunManager()
