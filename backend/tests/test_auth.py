"""Authentication and the deployment safety interlock.

The interlock matters more than it looks: the failure it prevents is silent. An instance
bound to a public address with no password looks perfectly healthy while exposing every
uploaded file and the whole case database.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from chainlens import auth
from chainlens.api.app import create_app

PASSWORD = "a-sufficiently-long-test-password"


@pytest.fixture
def protected_client(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("CHAINLENS_AUTH_PASSWORD", PASSWORD)
    auth._attempts.clear()
    return TestClient(create_app())


@pytest.fixture
def open_client(isolated_data_dir, monkeypatch):
    monkeypatch.delenv("CHAINLENS_AUTH_PASSWORD", raising=False)
    auth._attempts.clear()
    return TestClient(create_app())


# ------------------------------------------------------------------ startup interlock
def test_public_bind_without_password_is_refused(monkeypatch):
    monkeypatch.delenv("CHAINLENS_AUTH_PASSWORD", raising=False)
    with pytest.raises(auth.AuthConfigurationError, match="without authentication"):
        auth.enforce_startup_policy("0.0.0.0")


def test_weak_password_is_refused_for_a_public_bind(monkeypatch):
    monkeypatch.setenv("CHAINLENS_AUTH_PASSWORD", "short")
    with pytest.raises(auth.AuthConfigurationError, match="too short"):
        auth.enforce_startup_policy("0.0.0.0")


def test_loopback_needs_no_password(monkeypatch):
    monkeypatch.delenv("CHAINLENS_AUTH_PASSWORD", raising=False)
    for host in ("127.0.0.1", "::1", "localhost"):
        auth.enforce_startup_policy(host)  # must not raise


def test_public_bind_with_a_strong_password_is_allowed(monkeypatch):
    monkeypatch.setenv("CHAINLENS_AUTH_PASSWORD", PASSWORD)
    auth.enforce_startup_policy("0.0.0.0")


# --------------------------------------------------------------------- session tokens
def test_token_round_trips(isolated_data_dir):
    assert auth.verify_token(auth.issue_token()) is True


def test_expired_token_is_rejected(isolated_data_dir):
    issued_long_ago = time.time() - auth.SESSION_TTL_SECONDS - 60
    assert auth.verify_token(auth.issue_token(now=issued_long_ago)) is False


def test_tampered_token_is_rejected(isolated_data_dir):
    token = auth.issue_token()
    payload, _, signature = token.partition(".")
    assert auth.verify_token(f"{payload}x.{signature}") is False
    assert auth.verify_token(f"{payload}.{signature}x") is False
    assert auth.verify_token("nonsense") is False
    assert auth.verify_token(None) is False


def test_token_signed_with_another_secret_is_rejected(isolated_data_dir, monkeypatch):
    monkeypatch.setenv("CHAINLENS_SESSION_SECRET", "secret-one")
    token = auth.issue_token()
    monkeypatch.setenv("CHAINLENS_SESSION_SECRET", "secret-two")
    assert auth.verify_token(token) is False


def test_generated_secret_persists_so_restarts_do_not_log_everyone_out(
        isolated_data_dir, monkeypatch):
    monkeypatch.delenv("CHAINLENS_SESSION_SECRET", raising=False)
    first = auth.session_secret()
    assert auth.session_secret() == first, "the secret must be stable across calls"
    token = auth.issue_token()
    assert auth.verify_token(token)


# --------------------------------------------------------------------- the HTTP gate
def test_open_instance_needs_no_login(open_client):
    status = open_client.get("/api/auth/status").json()
    assert status["auth_required"] is False
    assert status["authenticated"] is True
    assert open_client.get("/api/cases").status_code == 200


def test_protected_instance_refuses_the_api_without_a_session(protected_client):
    response = protected_client.get("/api/cases")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("path", ["/api/health", "/api/auth/status"])
def test_probe_endpoints_stay_open(protected_client, path):
    """A platform must be able to tell 'starting' from 'broken' without credentials."""
    assert protected_client.get(path).status_code == 200


def test_frontend_shell_is_served_so_the_login_screen_can_render(protected_client):
    assert protected_client.get("/").status_code == 200


def test_wrong_password_is_rejected(protected_client):
    response = protected_client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert protected_client.get("/api/cases").status_code == 401


def test_correct_password_opens_the_api(protected_client):
    response = protected_client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert protected_client.get("/api/cases").status_code == 200

    status = protected_client.get("/api/auth/status").json()
    assert status["auth_required"] is True and status["authenticated"] is True


def test_session_cookie_is_httponly(protected_client):
    response = protected_client.post("/api/auth/login", json={"password": PASSWORD})
    header = response.headers.get("set-cookie", "")
    assert auth.COOKIE_NAME in header
    assert "HttpOnly" in header, "the cookie must not be readable from JavaScript"
    assert "samesite=lax" in header.lower()


def test_cookie_is_marked_secure_only_over_https(isolated_data_dir, monkeypatch):
    """Secure follows the real scheme.

    Hardcoding it would break plain-HTTP deployments silently: the browser accepts the
    cookie, never sends it back, and login appears to succeed while every subsequent
    request is rejected.
    """
    monkeypatch.setenv("CHAINLENS_AUTH_PASSWORD", PASSWORD)
    auth._attempts.clear()
    app = create_app()

    with TestClient(app, base_url="http://testserver") as plain:
        header = plain.post("/api/auth/login", json={"password": PASSWORD}) \
            .headers.get("set-cookie", "")
        assert "Secure" not in header
        assert plain.get("/api/cases").status_code == 200, "login must work over HTTP"

    auth._attempts.clear()
    with TestClient(app, base_url="https://testserver") as secure:
        header = secure.post("/api/auth/login", json={"password": PASSWORD}) \
            .headers.get("set-cookie", "")
        assert "Secure" in header
        assert secure.get("/api/cases").status_code == 200


def test_forwarded_proto_marks_the_cookie_secure_behind_a_proxy(protected_client):
    """Platforms terminate TLS and forward the original scheme in a header."""
    header = protected_client.post(
        "/api/auth/login", json={"password": PASSWORD},
        headers={"x-forwarded-proto": "https"},
    ).headers.get("set-cookie", "")
    assert "Secure" in header


def test_logout_closes_the_session(protected_client):
    protected_client.post("/api/auth/login", json={"password": PASSWORD})
    assert protected_client.get("/api/cases").status_code == 200
    protected_client.post("/api/auth/logout")
    assert protected_client.get("/api/cases").status_code == 401


def test_repeated_failures_are_rate_limited(protected_client):
    codes = [
        protected_client.post("/api/auth/login", json={"password": "bad"}).status_code
        for _ in range(auth.MAX_ATTEMPTS + 2)
    ]
    assert codes[0] == 401
    assert 429 in codes, "a public URL gets scanned; guessing must become expensive"


def test_rate_limit_does_not_leak_the_password(protected_client):
    for _ in range(auth.MAX_ATTEMPTS + 1):
        protected_client.post("/api/auth/login", json={"password": "bad"})
    body = protected_client.post("/api/auth/login", json={"password": "bad"}).text
    assert PASSWORD not in body


def test_uploads_are_blocked_without_a_session(protected_client):
    """The gate must cover writes, not only reads."""
    response = protected_client.post(
        "/api/cases", json={"title": "should not be created"})
    assert response.status_code == 401


# ------------------------------------------------------------- open demonstration
def test_public_demo_waives_the_interlock(monkeypatch):
    """An open public instance must be chosen deliberately, never reached by omission."""
    monkeypatch.delenv("CHAINLENS_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("CHAINLENS_PUBLIC_DEMO", "1")
    auth.enforce_startup_policy("0.0.0.0")  # must not raise


def test_interlock_still_fires_without_the_explicit_waiver(monkeypatch):
    monkeypatch.delenv("CHAINLENS_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("CHAINLENS_PUBLIC_DEMO", "0")
    with pytest.raises(auth.AuthConfigurationError):
        auth.enforce_startup_policy("0.0.0.0")


def test_demo_instance_serves_the_api_and_announces_itself(isolated_data_dir, monkeypatch):
    monkeypatch.delenv("CHAINLENS_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("CHAINLENS_PUBLIC_DEMO", "1")
    client = TestClient(create_app())

    assert client.get("/api/cases").status_code == 200, "an open demo needs no login"

    status = client.get("/api/auth/status").json()
    assert status["auth_required"] is False
    assert status["public_demo"] is True
    assert "upload" in status["public_demo_notice"].lower()


def test_a_password_takes_precedence_over_the_demo_flag(isolated_data_dir, monkeypatch):
    """Setting both must protect the instance, not open it."""
    monkeypatch.setenv("CHAINLENS_AUTH_PASSWORD", PASSWORD)
    monkeypatch.setenv("CHAINLENS_PUBLIC_DEMO", "1")
    auth._attempts.clear()
    client = TestClient(create_app())

    assert client.get("/api/cases").status_code == 401
    status = client.get("/api/auth/status").json()
    assert status["auth_required"] is True
    assert status["public_demo"] is False
