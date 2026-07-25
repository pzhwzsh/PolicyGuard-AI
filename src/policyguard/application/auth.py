"""OIDC identity validation and role authorization."""

from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWKClient

ROLE_ORDER = {"user": 0, "reviewer": 1, "admin": 2}


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant: str
    roles: frozenset[str]

    def permits(self, required: str) -> bool:
        threshold = ROLE_ORDER[required]
        return any(ROLE_ORDER.get(role, -1) >= threshold for role in self.roles)


def principal_from_claims(
    claims: dict,
    *,
    roles_claim: str = "roles",
    tenant_claim: str = "tenant_id",
) -> Principal:
    subject = str(claims.get("sub", "")).strip()
    tenant = str(claims.get(tenant_claim, "")).strip()
    raw_roles = claims.get(roles_claim, [])
    if isinstance(raw_roles, str):
        raw_roles = raw_roles.replace(",", " ").split()
    if not subject or not tenant or not isinstance(raw_roles, (list, tuple, set)):
        raise ValueError("oidc_required_claim_missing")
    roles = frozenset(str(role).strip().casefold() for role in raw_roles)
    if not roles.intersection(ROLE_ORDER):
        roles = frozenset({"user"})
    return Principal(subject=subject, tenant=tenant, roles=roles)


class OIDCAuthenticator:
    """Discover an issuer once and validate signed access tokens against its JWKS."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        roles_claim: str = "roles",
        tenant_claim: str = "tenant_id",
        timeout_seconds: float = 5,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.roles_claim = roles_claim
        self.tenant_claim = tenant_claim
        self.timeout_seconds = timeout_seconds
        self._client: PyJWKClient | None = None
        self._lock = Lock()
        parsed = urlparse(self.issuer)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("oidc_issuer_https_required")
        if not audience:
            raise ValueError("oidc_audience_required")

    def _jwks_client(self) -> PyJWKClient:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                configuration_url = f"{self.issuer}/.well-known/openid-configuration"
                response = httpx.get(configuration_url, timeout=self.timeout_seconds)
                response.raise_for_status()
                jwks_uri = str(response.json().get("jwks_uri", ""))
                if not jwks_uri.startswith("https://") and not jwks_uri.startswith(
                    "http://localhost"
                ):
                    raise ValueError("oidc_jwks_uri_invalid")
                self._client = PyJWKClient(jwks_uri, cache_jwk_set=True, lifespan=300)
        return self._client

    def authenticate(self, token: str) -> Principal:
        if not token:
            raise ValueError("bearer_token_required")
        signing_key = self._jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
        return principal_from_claims(
            claims,
            roles_claim=self.roles_claim,
            tenant_claim=self.tenant_claim,
        )


def bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise ValueError("bearer_token_required")
    return token.strip()
