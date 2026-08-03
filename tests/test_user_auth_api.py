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
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "SafePassword2026",
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

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_registration_rejects_legacy_code_and_cross_site_write_rejection(auth_client) -> None:
    client, messages = auth_client
    legacy_payload = client.post(
        "/api/v1/auth/register",
        json={
            "email": "person@example.com",
            "password": "SafePassword2026",
            "verification_code": "000000",
        },
    )
    assert legacy_payload.status_code == 422
    register(client, messages, "person@example.com")

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


def test_new_user_can_create_only_ten_compliance_workflows(auth_client) -> None:
    client, messages = auth_client
    register(client, messages, "quota@example.com")
    payload = {
        "product": {
            "external_id": "SKU-QUOTA",
            "title": "普通商品标题",
            "description": "普通商品描述",
            "category": "beauty",
            "attributes": {},
        },
        "markets": ["CN"],
        "category": "beauty",
        "channel": "all",
    }
    for index in range(10):
        payload["product"]["external_id"] = f"SKU-QUOTA-{index}"
        assert client.post("/api/v1/workflows/compliance", json=payload).status_code == 201

    exhausted = client.post("/api/v1/workflows/compliance", json=payload)
    assert exhausted.status_code == 429
    assert exhausted.json()["detail"] == "workflow_quota_exhausted"
    account = client.get("/api/v1/auth/me").json()
    assert account["workflow_uses"] == 10
    assert account["workflow_limit"] == 10
    assert account["workflow_remaining"] == 0
