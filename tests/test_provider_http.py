import httpx
import pytest

from policyguard.application.provider_http import (
    ProviderRetryPolicy,
    is_transient_provider_error,
    post_with_retry,
)


def response(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://model"))


def test_provider_http_retries_transient_status_and_honors_retry_after(monkeypatch) -> None:
    responses = iter([response(429, "2"), response(200)])
    delays = []
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: next(responses))

    result = post_with_retry(
        "https://model", headers={}, json={}, timeout=1,
        policy=ProviderRetryPolicy(max_attempts=3, base_delay_seconds=0.1),
        sleeper=delays.append,
    )

    assert result.status_code == 200
    assert result.extensions["policyguard_attempts"] == 2
    assert delays == [2.0]


def test_provider_http_does_not_retry_permanent_client_error(monkeypatch) -> None:
    calls = 0

    def permanent_failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        return response(400)

    monkeypatch.setattr(httpx, "post", permanent_failure)
    with pytest.raises(httpx.HTTPStatusError):
        post_with_retry(
            "https://model", headers={}, json={}, timeout=1,
            policy=ProviderRetryPolicy(max_attempts=3), sleeper=lambda _: None,
        )
    assert calls == 1


def test_provider_http_retries_transport_error_then_exhausts(monkeypatch) -> None:
    calls = 0

    def unavailable(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", unavailable)
    with pytest.raises(httpx.ConnectError):
        post_with_retry(
            "https://model", headers={}, json={}, timeout=1,
            policy=ProviderRetryPolicy(max_attempts=2), sleeper=lambda _: None,
        )
    assert calls == 2


def test_provider_error_classification_distinguishes_permanent_status() -> None:
    transient = httpx.HTTPStatusError(
        "busy", request=response(503).request, response=response(503)
    )
    permanent = httpx.HTTPStatusError(
        "bad request", request=response(400).request, response=response(400)
    )

    assert is_transient_provider_error(transient) is True
    assert is_transient_provider_error(permanent) is False
