"""Single-operator authentication.

The local build needs no login: it binds to localhost and serves one trusted operator.
A deployed build is different - the URL is reachable by anyone - so a password is
required before the application will listen on a non-loopback address.

Design notes:

* One shared password, supplied as ``CHAINLENS_AUTH_PASSWORD``. This is a single-operator
  prototype, not a multi-user system: there are no accounts, roles or per-user data.
* The session is a signed cookie, not server state, so it survives a container restart
  and needs no session store.
* The signing secret is generated once and persisted, so restarting the container does
  not silently log everyone out.
* Login attempts are rate limited per client, because a public URL will be scanned.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

COOKIE_NAME = "chainlens_session"

#: How long a login lasts before the operator must sign in again.
SESSION_TTL_SECONDS = int(os.environ.get("CHAINLENS_SESSION_TTL", 12 * 3600))

#: Brute-force resistance. A public URL gets scanned; this keeps guessing expensive
#: without locking the real operator out for long.
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW_SECONDS = 300

#: Minimum password length accepted at startup. A deployed instance protected by
#: "admin" is not protected.
MIN_PASSWORD_LENGTH = 12


class AuthConfigurationError(RuntimeError):
    """Raised when the deployment is configured in a way that would be unsafe."""


def configured_password() -> str | None:
    """The operator password, or None when authentication is disabled."""
    value = os.environ.get("CHAINLENS_AUTH_PASSWORD", "").strip()
    return value or None


def auth_enabled() -> bool:
    return configured_password() is not None


def _secret_path() -> Path:
    return config.DATA_DIR / "session_secret"


def session_secret() -> bytes:
    """The cookie signing key.

    Taken from ``CHAINLENS_SESSION_SECRET`` when supplied. Otherwise generated once and
    stored beside the database, so sessions survive a restart. A generated secret is not
    shared between replicas - this build runs as a single instance.
    """
    supplied = os.environ.get("CHAINLENS_SESSION_SECRET", "").strip()
    if supplied:
        return supplied.encode("utf-8")

    path = _secret_path()
    if path.exists():
        return path.read_bytes()

    config.ensure_dirs()
    generated = secrets.token_bytes(32)
    path.write_bytes(generated)
    try:  # best effort on POSIX; Windows ignores the mode
        path.chmod(0o600)
    except OSError:
        pass
    return generated


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(now: float | None = None) -> str:
    """Create a signed session token."""
    issued = int(now if now is not None else time.time())
    payload = json.dumps({"iat": issued, "exp": issued + SESSION_TTL_SECONDS},
                         separators=(",", ":")).encode("utf-8")
    signature = hmac.new(session_secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_token(token: str | None, now: float | None = None) -> bool:
    """Check a session token's signature and expiry, in constant time."""
    if not token or "." not in token:
        return False
    encoded_payload, _, encoded_signature = token.partition(".")
    try:
        payload = _unb64(encoded_payload)
        signature = _unb64(encoded_signature)
    except (ValueError, TypeError):
        return False

    expected = hmac.new(session_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        claims = json.loads(payload)
    except ValueError:
        return False
    return float(claims.get("exp", 0)) > (now if now is not None else time.time())


def check_password(candidate: str) -> bool:
    """Compare against the configured password in constant time."""
    expected = configured_password()
    if expected is None:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


@dataclass
class _Attempts:
    count: int = 0
    first_at: float = 0.0


_attempts: dict[str, _Attempts] = {}


def rate_limited(client: str, now: float | None = None) -> bool:
    """True when this client has failed too many logins recently."""
    moment = now if now is not None else time.time()
    record = _attempts.get(client)
    if record is None:
        return False
    if moment - record.first_at > ATTEMPT_WINDOW_SECONDS:
        del _attempts[client]
        return False
    return record.count >= MAX_ATTEMPTS


def record_failure(client: str, now: float | None = None) -> None:
    moment = now if now is not None else time.time()
    record = _attempts.get(client)
    if record is None or moment - record.first_at > ATTEMPT_WINDOW_SECONDS:
        _attempts[client] = _Attempts(count=1, first_at=moment)
    else:
        record.count += 1


def clear_failures(client: str) -> None:
    _attempts.pop(client, None)


def retry_after(client: str, now: float | None = None) -> int:
    record = _attempts.get(client)
    if record is None:
        return 0
    moment = now if now is not None else time.time()
    return max(0, int(ATTEMPT_WINDOW_SECONDS - (moment - record.first_at)))


def is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", "127.0.0.0/8"}


def public_demo_mode() -> bool:
    """True when the operator has deliberately opened the instance to everyone.

    Running a public instance with no login is a legitimate choice for a demonstration
    of synthetic data. It is required to be stated explicitly rather than achieved by
    forgetting to set a password, because those two situations look identical from the
    outside and only one of them is intended.
    """
    return os.environ.get("CHAINLENS_PUBLIC_DEMO", "0").strip() == "1"


def enforce_startup_policy(host: str) -> None:
    """Refuse to listen on a public address without a usable password.

    This interlock exists because the failure it prevents is silent: an instance bound
    to 0.0.0.0 with no password looks completely healthy while exposing every uploaded
    file, the case database and the source archive to anyone who finds the URL.

    ``CHAINLENS_PUBLIC_DEMO=1`` waives it deliberately.
    """
    if is_loopback(host):
        return

    password = configured_password()
    if password is None:
        if public_demo_mode():
            return
        raise AuthConfigurationError(
            f"Refusing to bind to {host} without authentication.\n\n"
            "This build has no accounts or roles, so a public instance is protected by "
            "one password. Set CHAINLENS_AUTH_PASSWORD to a strong value and restart.\n\n"
            "To run locally without a password, bind to 127.0.0.1 instead.\n"
            "To run an open public demonstration on purpose, set "
            "CHAINLENS_PUBLIC_DEMO=1 - and read what that means first: anyone who finds "
            "the URL can upload files, read every case on the instance and export "
            "evidence from it."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthConfigurationError(
            f"CHAINLENS_AUTH_PASSWORD is too short ({len(password)} characters). "
            f"Use at least {MIN_PASSWORD_LENGTH}. A public URL is scanned automatically "
            f"within minutes of going live."
        )
