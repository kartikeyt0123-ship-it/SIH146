"""Start ChainLens.

    python -m chainlens            # serve on http://127.0.0.1:8000
    python -m chainlens --port 9000

Binds to localhost by default: this build is for one trusted local operator and has no
authentication.
"""
from __future__ import annotations

import argparse

import uvicorn

from . import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chainlens", description="Start ChainLens locally.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Defaults to localhost; this build has no auth.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Development auto-reload.")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    print(f"ChainLens starting on http://{args.host}:{args.port}")
    print(f"  data directory : {config.DATA_DIR}")
    print(f"  model bundles  : {config.MODEL_DIR}")
    print(f"  frontend build : {config.FRONTEND_DIST}")
    print("  offline: the application makes no outbound network requests.")
    uvicorn.run("chainlens.api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
