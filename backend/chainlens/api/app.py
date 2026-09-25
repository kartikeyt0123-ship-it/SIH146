"""The FastAPI application.

In the packaged build this single process serves both the API and the compiled
frontend, binds to localhost and makes no outbound network requests. FastAPI's own
documentation UI pulls its assets from a CDN, so it is disabled unless explicitly
enabled for development.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..db import init_db
from .routes import router

#: Development origins for the Vite dev server. The packaged build serves the frontend
#: from this same process and needs none of these.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app() -> FastAPI:
    config.ensure_dirs()
    init_db()

    app = FastAPI(
        title="ChainLens",
        version="0.1.0",
        description=(
            "Offline Bitcoin investigation platform. Runs entirely on the local "
            "machine and makes no outbound network requests."),
        # The default docs pull Swagger UI assets from a CDN, which would break the
        # offline guarantee, so they are off unless explicitly enabled.
        docs_url="/api/docs" if config.ENABLE_API_DOCS else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if config.ENABLE_API_DOCS else None,
    )

    if os.environ.get("CHAINLENS_DEV", "0") == "1":
        app.add_middleware(
            CORSMiddleware, allow_origins=DEV_ORIGINS, allow_credentials=False,
            allow_methods=["*"], allow_headers=["*"],
        )

    app.include_router(router, prefix="/api")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Return a structured error instead of leaking a stack trace to the browser."""
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR",
                     "message": f"{type(exc).__name__}: {exc}",
                     "path": str(request.url.path)},
        )

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the compiled frontend, if it has been built."""
    dist: Path = config.FRONTEND_DIST
    index = dist / "index.html"
    if not index.exists():
        @app.get("/")
        def frontend_missing() -> dict[str, Any]:
            return {
                "status": "api_only",
                "message": (
                    "The compiled frontend was not found. Build it with "
                    "'npm install && npm run build' in the frontend directory, or run "
                    "the Vite dev server on port 5173."),
                "expected_at": str(dist),
                "api_health": "/api/health",
            }
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index_page() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """Serve a real file when one exists, otherwise the SPA entry point.

        An unknown path under /api must never fall through to the HTML page: a client
        calling a mistyped endpoint would otherwise receive a 200 and a page of markup
        instead of an error it can act on.
        """
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"code": "ENDPOINT_NOT_FOUND",
                         "message": f"No API endpoint at /{full_path}."},
            )
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            return FileResponse(index)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
