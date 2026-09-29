"""Start ChainLens.

    python -m chainlens                  # http://127.0.0.1:8000, no password needed
    python -m chainlens --port 9000
    python -m chainlens --host 0.0.0.0   # requires CHAINLENS_AUTH_PASSWORD

Binding to anything other than loopback requires a password, because this build has no
accounts or roles: one password is all that stands between a public URL and every
uploaded file in the case database.
"""
from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from . import auth, config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chainlens", description="Start ChainLens.")
    parser.add_argument(
        "--host",
        # Platforms set HOST; containers listen on all interfaces so the platform's
        # proxy can reach them. Local runs stay on loopback.
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Bind address. Non-loopback requires CHAINLENS_AUTH_PASSWORD.")
    parser.add_argument(
        "--port", type=int,
        # Every platform injects PORT and expects the process to honour it.
        default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Development auto-reload.")
    args = parser.parse_args(argv)

    config.ensure_dirs()

    try:
        auth.enforce_startup_policy(args.host)
    except auth.AuthConfigurationError as exc:
        print(f"\nChainLens refused to start.\n\n{exc}\n", file=sys.stderr)
        return 2

    protected = auth.auth_enabled()
    print(f"ChainLens starting on http://{args.host}:{args.port}")
    print(f"  data directory : {config.DATA_DIR}")
    print(f"  model bundles  : {config.MODEL_DIR}")
    print(f"  frontend build : {config.FRONTEND_DIST}")
    print(f"  authentication : {'password required' if protected else 'disabled (loopback only)'}")
    print("  offline: the application makes no outbound network requests.")

    uvicorn.run(
        "chainlens.api.app:app", host=args.host, port=args.port,
        reload=args.reload, log_level="info",
        # Platform proxies terminate TLS and forward the original client address.
        proxy_headers=True, forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
