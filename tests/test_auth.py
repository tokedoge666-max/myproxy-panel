from __future__ import annotations

from sqlalchemy import select

from app.models import Admin, AuthSession
from conftest import CHANGED_PASSWORD, INITIAL_PASSWORD


def test_login_first_password_change_and_session_storage(client_bundle) -> None:
    client = client_bundle.client
    denied = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert denied.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": INITIAL_PASSWORD},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["user"]["must_change_password"] is True
    assert body["csrf_token"] == login.headers["X-CSRF-Token"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert login.headers["cache-control"] == "no-store"

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert "user" not in me.json()
    assert me.headers["cache-control"] == "no-store"

    assert client.get("/api/v1/nodes").status_code == 403
    missing_csrf = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": INITIAL_PASSWORD, "new_password": CHANGED_PASSWORD},
    )
    assert missing_csrf.status_code == 403

    csrf = body["csrf_token"]
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": INITIAL_PASSWORD, "new_password": CHANGED_PASSWORD},
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False

    with client.app.state.database.session_factory() as session:
        auth_session = session.scalar(select(AuthSession))
        admin = session.scalar(select(Admin))
        assert auth_session is not None
        assert client.cookies.get("myproxy_session") not in auth_session.token_hash
        assert INITIAL_PASSWORD not in admin.password_hash

    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401

    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": INITIAL_PASSWORD},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": CHANGED_PASSWORD},
    ).status_code == 200


def test_csrf_protects_mutations_after_password_change(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    assert client.post("/api/v1/nodes/1/disable").status_code == 403
    assert client.post(
        "/api/v1/nodes/1/disable", headers={"X-CSRF-Token": "wrong"}
    ).status_code == 403
    response = client.post(
        "/api/v1/nodes/1/disable", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_password_complexity_is_enforced(client_bundle) -> None:
    client = client_bundle.client
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": INITIAL_PASSWORD},
    )
    csrf = login.json()["csrf_token"]
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": INITIAL_PASSWORD, "new_password": "onlylowercasepassword"},
    )
    assert response.status_code == 400
    assert "uppercase" in response.json()["detail"]
