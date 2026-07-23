"""Bounded retries for transient model-provider HTTP failures."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class ProviderRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise ValueError("provider_retry_attempts_out_of_range")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("provider_retry_delay_invalid")


def should_try_backup(exc: Exception) -> bool:
    return not (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {401, 403}
    )


def is_transient_provider_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in RETRYABLE_STATUS_CODES
    )


def _retry_delay(
    response: httpx.Response | None,
    attempt: int,
    policy: ProviderRetryPolicy,
) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), policy.max_delay_seconds)
            except ValueError:
                pass
    base = min(policy.base_delay_seconds * (2 ** (attempt - 1)), policy.max_delay_seconds)
    return min(base * (0.8 + random.random() * 0.4), policy.max_delay_seconds)


def post_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    timeout: float,
    policy: ProviderRetryPolicy,
    sleeper: Callable[[float], None] = sleep,
) -> httpx.Response:
    last_error: httpx.TransportError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        response = None
        try:
            response = httpx.post(url, headers=headers, json=json, timeout=timeout)
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                response.extensions["policyguard_attempts"] = attempt
                return response
            if attempt == policy.max_attempts:
                response.raise_for_status()
        except httpx.TransportError as exc:
            last_error = exc
            if attempt == policy.max_attempts:
                raise
        sleeper(_retry_delay(response, attempt, policy))
    if last_error is not None:  # pragma: no cover - loop always raises or returns
        raise last_error
    raise RuntimeError("provider_retry_exhausted")  # pragma: no cover
