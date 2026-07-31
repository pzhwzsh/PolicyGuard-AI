"""Local email authentication with short-lived verification codes and server sessions."""

import hashlib
import hmac
import secrets
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from policyguard.infrastructure.database import (
    EmailVerificationRecord,
    UserRecord,
    UserSessionRecord,
)

PBKDF2_ITERATIONS = 600_000
SESSION_TTL = timedelta(days=7)
CODE_TTL = timedelta(minutes=10)
SEND_COOLDOWN = timedelta(seconds=60)
LOCK_DURATION = timedelta(minutes=15)


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 320 or email.count("@") != 1:
        raise ValueError("email_invalid")
    local, domain = email.split("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("email_invalid")
    return email


def validate_password(password: str, email: str = "") -> None:
    if len(password) < 10 or len(password) > 128:
        raise ValueError("password_length_invalid")
    if any(char.isspace() for char in password):
        raise ValueError("password_whitespace_forbidden")
    required = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
    )
    if sum(required) < 3:
        raise ValueError("password_complexity_insufficient")
    if email and email.split("@", 1)[0].casefold() in password.casefold():
        raise ValueError("password_contains_email")
    if password.casefold() in {"password123", "qwerty12345", "1234567890"}:
        raise ValueError("password_too_common")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _code_hash(email: str, purpose: str, code: str, pepper: str) -> str:
    payload = f"{email}|{purpose}|{code}".encode()
    return hmac.new(pepper.encode(), payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class MailSettings:
    host: str
    port: int
    username: str
    authorization_code: str
    from_email: str


class QQEmailSender:
    def __init__(self, settings: MailSettings) -> None:
        self.settings = settings

    def send_verification(self, email: str, code: str) -> None:
        credentials = (
            self.settings.username,
            self.settings.authorization_code,
            self.settings.from_email,
        )
        if not all(credentials):
            raise RuntimeError("smtp_not_configured")
        message = EmailMessage()
        message["Subject"] = "PolicyGuard 注册验证码"
        message["From"] = self.settings.from_email
        message["To"] = email
        message.set_content(f"你的验证码是 {code}，10 分钟内有效。请勿转发给任何人。")
        with smtplib.SMTP_SSL(self.settings.host, self.settings.port, timeout=10) as smtp:
            smtp.login(self.settings.username, self.settings.authorization_code)
            smtp.send_message(message)


class LocalAuthService:
    def __init__(self, session: Session, pepper: str, sender) -> None:
        self.session = session
        self.pepper = pepper
        self.sender = sender

    def request_code(self, raw_email: str, purpose: str = "register") -> None:
        email = normalize_email(raw_email)
        now = datetime.now(UTC)
        if purpose != "register":
            raise ValueError("verification_purpose_invalid")
        if self.session.scalar(select(UserRecord).where(UserRecord.email == email)):
            # Keep the public response indistinguishable from an unregistered address.
            return
        recent = self.session.scalar(
            select(EmailVerificationRecord)
            .where(EmailVerificationRecord.email == email)
            .order_by(EmailVerificationRecord.created_at.desc())
        )
        if recent and _aware(recent.created_at) > now - SEND_COOLDOWN:
            raise ValueError("verification_code_cooldown")
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.sender.send_verification(email, code)
        self.session.add(
            EmailVerificationRecord(
                id=uuid4().hex,
                email=email,
                purpose=purpose,
                code_hash=_code_hash(email, purpose, code, self.pepper),
                expires_at=now + CODE_TTL,
            )
        )
        self.session.commit()

    def register(self, raw_email: str, password: str, code: str) -> tuple[UserRecord, str]:
        email = normalize_email(raw_email)
        validate_password(password, email)
        now = datetime.now(UTC)
        if self.session.scalar(select(UserRecord).where(UserRecord.email == email)):
            raise ValueError("email_already_registered")
        verification = self.session.scalar(
            select(EmailVerificationRecord)
            .where(
                EmailVerificationRecord.email == email,
                EmailVerificationRecord.purpose == "register",
                EmailVerificationRecord.consumed_at.is_(None),
            )
            .order_by(EmailVerificationRecord.created_at.desc())
        )
        if not verification or _aware(verification.expires_at) <= now:
            raise ValueError("verification_code_expired")
        if verification.attempts >= 5:
            raise ValueError("verification_attempts_exceeded")
        verification.attempts += 1
        if not hmac.compare_digest(
            verification.code_hash, _code_hash(email, "register", code.strip(), self.pepper)
        ):
            self.session.commit()
            raise ValueError("verification_code_invalid")
        verification.consumed_at = now
        user = UserRecord(id=uuid4().hex, email=email, password_hash=hash_password(password))
        self.session.add(user)
        token = self._create_session(user, now)
        self.session.commit()
        return user, token

    def login(self, raw_email: str, password: str) -> tuple[UserRecord, str]:
        email = normalize_email(raw_email)
        now = datetime.now(UTC)
        user = self.session.scalar(select(UserRecord).where(UserRecord.email == email))
        if user and user.locked_until and _aware(user.locked_until) > now:
            raise ValueError("account_temporarily_locked")
        if not user or not user.active or not verify_password(password, user.password_hash):
            if user:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= 5:
                    user.locked_until = now + LOCK_DURATION
                    user.failed_login_attempts = 0
                self.session.commit()
            raise ValueError("credentials_invalid")
        user.failed_login_attempts = 0
        user.locked_until = None
        token = self._create_session(user, now)
        self.session.commit()
        return user, token

    def authenticate(self, token: str) -> UserRecord | None:
        if not token:
            return None
        now = datetime.now(UTC)
        record = self.session.scalar(
            select(UserSessionRecord).where(UserSessionRecord.token_hash == _token_hash(token))
        )
        if not record or record.revoked_at or _aware(record.expires_at) <= now:
            return None
        user = self.session.get(UserRecord, record.user_id)
        return user if user and user.active else None

    def logout(self, token: str) -> None:
        record = self.session.scalar(
            select(UserSessionRecord).where(UserSessionRecord.token_hash == _token_hash(token))
        )
        if record and not record.revoked_at:
            record.revoked_at = datetime.now(UTC)
            self.session.commit()

    def _create_session(self, user: UserRecord, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        self.session.add(
            UserSessionRecord(
                id=uuid4().hex,
                user_id=user.id,
                token_hash=_token_hash(token),
                expires_at=now + SESSION_TTL,
            )
        )
        return token


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
