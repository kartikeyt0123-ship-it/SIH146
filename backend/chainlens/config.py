"""Runtime configuration.

Everything is local-first: paths default to a `chainlens_data` directory beside the
repository so the application runs with no external services and no network access.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


#: Root for everything the application writes at runtime.
DATA_DIR = _env_path("CHAINLENS_DATA_DIR", REPO_DIR / "chainlens_data")

#: SQLite database file holding cases, sanitised records, runs and alerts.
DB_PATH = _env_path("CHAINLENS_DB", DATA_DIR / "chainlens.sqlite3")

#: Restricted archive of the exact bytes that were uploaded. Analysis workers never
#: read from here; they read the sanitised rows in SQLite.
SOURCE_ARCHIVE_DIR = DATA_DIR / "source_archive"

#: Frozen prediction artifacts written by a run, consumed by the offline evaluator.
PREDICTIONS_DIR = DATA_DIR / "predictions"

#: Generated export bundles (PDF / JSON / CSV).
EXPORT_DIR = DATA_DIR / "exports"

#: Trained model bundles produced by `python -m chainlens.ml.train`.
MODEL_DIR = _env_path("CHAINLENS_MODEL_DIR", BACKEND_DIR / "model_artifacts")

#: Compiled frontend, served by FastAPI in the packaged application.
FRONTEND_DIST = _env_path("CHAINLENS_FRONTEND_DIST", REPO_DIR / "frontend" / "dist")

#: Upload ceiling. The supplied sample is ~2.8 MB; 256 MB is a generous local bound.
MAX_UPLOAD_BYTES = int(os.environ.get("CHAINLENS_MAX_UPLOAD_BYTES", 256 * 1024 * 1024))

#: Disable FastAPI's CDN-backed documentation UI so the app is fully offline.
ENABLE_API_DOCS = os.environ.get("CHAINLENS_ENABLE_DOCS", "0") == "1"


def ensure_dirs() -> None:
    for path in (DATA_DIR, SOURCE_ARCHIVE_DIR, PREDICTIONS_DIR, EXPORT_DIR, MODEL_DIR):
        path.mkdir(parents=True, exist_ok=True)
