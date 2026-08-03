from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from policyguard.application.user_auth import (
    LocalAuthService,
    hash_password,
    validate_password,
    verify_password,
)
from policyguard.infrastructure.database import Database, EmailVerificationRecord


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_verification(self, email: str, code: str) -> None:
        self.messages.append((email, code))


def service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'auth.db'}")
    database.initialize()
    session = database.session_factory()
    sender = RecordingSender()
    return session, sender, LocalAuthService(session, "test-pepper", sender)


def test_password_policy_and_hash_round_trip() -> None:
    validate_password("SafePassword2026", "person@example.com")
    encoded = hash_password("SafePassword2026")
    assert encoded != "SafePassword2026"
    assert verify_password("SafePassword2026", encoded)
    assert not verify_password("WrongPassword2026", encoded)
    with pytest.raises(ValueError, match="password_length_invalid"):
        validate_password("Short1A")
    with pytest.raises(ValueError, match="password_whitespace_forbidden"):
        validate_password("Unsafe Password2026")


def test_register_login_authenticate_and_logout(tmp_path) -> None:
    session, sender, auth = service(tmp_path)
    try:
        user, register_token = auth.register(" Person@Example.com ", "SafePassword2026")
        assert user.email == "person@example.com"
        assert auth.authenticate(register_token).id == user.id

        _, login_token = auth.login(user.email, "SafePassword2026")
        assert auth.authenticate(login_token).email == user.email
        auth.logout(login_token)
        assert auth.authenticate(login_token) is None
    finally:
        session.close()


def test_verification_code_endpoint_retains_send_cooldown(tmp_path) -> None:
    session, sender, auth = service(tmp_path)
    try:
        auth.request_code("person@example.com")
        with pytest.raises(ValueError, match="verification_code_cooldown"):
            auth.request_code("person@example.com")
        record = session.scalar(select(EmailVerificationRecord))
        assert record.attempts == 0
    finally:
        session.close()


def test_login_lockout_after_repeated_failures(tmp_path) -> None:
    session, sender, auth = service(tmp_path)
    try:
        user, _ = auth.register("person@example.com", "SafePassword2026")
        for _ in range(5):
            with pytest.raises(ValueError, match="credentials_invalid"):
                auth.login(user.email, "WrongPassword2026")
        assert user.locked_until is not None
        assert user.locked_until.replace(tzinfo=UTC) > datetime.now(UTC)
        with pytest.raises(ValueError, match="account_temporarily_locked"):
            auth.login(user.email, "SafePassword2026")
    finally:
        session.close()
