"""
Authentication middleware.

The middleware is what guards the static UI, /docs and /openapi.json — none of
which is a route a dependency could have covered — so the tests below check those
paths explicitly rather than only an /api endpoint.
"""

import base64

import pytest
from starlette.testclient import TestClient

from web import settings
from web.auth import AuthConfigError, check_auth_config
from web.server import create_app


@pytest.fixture
def auth_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_USERNAME", "operator")
    monkeypatch.setattr(settings, "AUTH_PASSWORD", "correct-horse")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return create_app()


def _basic(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_rejects_anonymous_request(auth_enabled) -> None:
    with TestClient(auth_enabled) as client:
        response = client.get("/api/history")

    assert response.status_code == 401
    # Without the challenge a browser shows an error instead of a login prompt.
    assert response.headers["WWW-Authenticate"].startswith("Basic realm=")


def test_accepts_correct_credentials(auth_enabled) -> None:
    with TestClient(auth_enabled) as client:
        response = client.get("/api/history", headers=_basic("operator", "correct-horse"))

    assert response.status_code == 200


@pytest.mark.parametrize(
    "username, password",
    [
        ("operator", "wrong"),
        ("wrong", "correct-horse"),
        ("", ""),
    ],
)
def test_rejects_bad_credentials(auth_enabled, username, password) -> None:
    with TestClient(auth_enabled) as client:
        response = client.get("/api/history", headers=_basic(username, password))

    assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "Bearer sometoken",         # wrong scheme
        "Basic",                    # no credentials
        "Basic !!!not-base64!!!",   # undecodable
        "Basic " + base64.b64encode(b"no-colon-here").decode(),
    ],
)
def test_rejects_malformed_authorization_header(auth_enabled, header) -> None:
    with TestClient(auth_enabled) as client:
        response = client.get("/api/history", headers={"Authorization": header})

    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/", "/docs", "/openapi.json"])
def test_guards_docs_and_static_ui(auth_enabled, path) -> None:
    """These are mounts, not routes — a per-route dependency would leave them open."""
    with TestClient(auth_enabled) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=_basic("operator", "correct-horse")).status_code == 200


def test_health_stays_open_for_orchestrator_probes(auth_enabled) -> None:
    with TestClient(auth_enabled) as client:
        response = client.get("/api/health")

    assert response.status_code == 200


def test_disabled_auth_lets_everything_through(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with TestClient(create_app()) as client:
        assert client.get("/api/history").status_code == 200


def test_enabled_without_credentials_fails_at_startup(monkeypatch) -> None:
    """
    Starting anyway would look identical from outside to a wrong password, and the
    deployment that misconfigures this is the one least able to debug it.
    """
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_USERNAME", "operator")
    monkeypatch.setattr(settings, "AUTH_PASSWORD", "")

    with pytest.raises(AuthConfigError):
        check_auth_config()
