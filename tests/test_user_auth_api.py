from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from policyguard.api.main import create_app
from policyguard.application.user_auth import QQEmailSender
from policyguard.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    messages: list[tuple[str, str]] = []
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_CODE_PEPPER", "test-pepper-with-enough-entropy")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        QQEmailSender,
        "send_verification",
        lambda _sender, email, code: messages.append((email, code)),
    )
    app = create_app(f"sqlite:///{(tmp_path / 'api-auth.db').as_posix()}")
    with TestClient(app, base_url="https://testserver") as client:
        yield client, messages


def register(client: TestClient, messages: list[tuple[str, str]], email: str) -> None:
    response = client.post("/api/v1/auth/verification-code", json={"email": email})
    assert response.status_code == 202
    assert messages[-1][0] == email
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "SafePassword2026",
            "verification_code": messages[-1][1],
        },
    )
    assert response.status_code == 200
    assert response.json()["email"] == email


def test_registration_cookie_logout_and_email_enumeration_resistance(auth_client) -> None:
    client, messages = auth_client
    register(client, messages, "person@example.com")

    cookie = client.cookies.get("pg_session")
    assert cookie
    cookie_header = client.post(
        "/api/v1/auth/login", json={"email": "person@example.com", "password": "SafePassword2026"}
    ).headers["set-cookie"]
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert client.get("/api/v1/auth/me").status_code == 200

    sent_count = len(messages)
    repeated = client.post("/api/v1/auth/verification-code", json={"email": "person@example.com"})
    assert repeated.status_code == 202
    assert repeated.json() == {"accepted": True}
    assert len(messages) == sent_count

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_bad_code_lockout_and_cross_site_write_rejection(auth_client) -> None:
    client, messages = auth_client
    client.post("/api/v1/auth/verification-code", json={"email": "person@example.com"})
    bad_code = client.post(
        "/api/v1/auth/register",
        json={
            "email": "person@example.com",
            "password": "SafePassword2026",
            "verification_code": "000000",
        },
    )
    assert bad_code.status_code == 400
    register_code = messages[-1][1]
    assert (
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "person@example.com",
                "password": "SafePassword2026",
                "verification_code": register_code,
            },
        ).status_code
        == 200
    )

    cross_site = client.put(
        "/api/v1/products/SKU-1",
        headers={"Origin": "https://attacker.example"},
        json={"external_id": "SKU-1", "name": "Demo", "category": "beauty"},
    )
    assert cross_site.status_code == 403
    assert cross_site.json()["detail"] == "cross_site_request_rejected"
    same_site = client.put(
        "/api/v1/products/SKU-1",
        headers={"Origin": "https://testserver"},
        json={"external_id": "SKU-1", "name": "Demo", "category": "beauty"},
    )
    assert same_site.status_code == 200

    client.post("/api/v1/auth/logout")
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.com", "password": "WrongPassword2026"},
        )
        assert response.status_code == 401
    locked = client.post(
        "/api/v1/auth/login",
        json={"email": "person@example.com", "password": "SafePassword2026"},
    )
    assert locked.status_code == 423


def test_production_requires_login_and_isolates_user_products(auth_client) -> None:
    client, messages = auth_client
    assert client.get("/api/v1/products").status_code == 401
    assert client.get("/api/v1/auth/status").json() == {
        "authenticated": False,
        "required": True,
    }

    register(client, messages, "first@example.com")
    first_session = client.cookies.get("pg_session")
    product = {"external_id": "SKU-1", "name": "Private", "category": "beauty"}
    assert client.put("/api/v1/products/SKU-1", json=product).status_code == 200

    client.cookies.clear()
    register(client, messages, "second@example.com")
    assert client.get("/api/v1/products").json() == []
    assert client.get("/api/v1/products/SKU-1").status_code == 404

    client.cookies.set("pg_session", first_session)
    assert client.get("/api/v1/products/SKU-1").json()["name"] == "Private"
