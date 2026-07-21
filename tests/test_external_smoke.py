from pathlib import Path
from types import SimpleNamespace

import httpx

from policyguard.application.external_smoke import run_external_smoke


ROOT = Path(__file__).parents[1]


def test_external_smoke_redacts_credentials_and_reports_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(404, json={"detail": "missing"})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, content=b"x" * 200)

    settings = SimpleNamespace(
        llm_base_url="https://relay.test/v1", llm_api_key="secret", llm_model="model",
        embedding_base_url="https://relay.test/v1", embedding_api_key="secret",
        embedding_model="embedding", rapidocr_base_url="https://ocr.test",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_external_smoke(settings, ROOT, client)
    assert report["failed"] == 1
    assert report["blocked"] == 0
    embedding = next(item for item in report["checks"] if item["name"] == "embedding")
    assert embedding["status_code"] == 404
    assert report["contains_credentials"] is False
    assert "secret" not in str(report)
    assert report["strict_success"] is False


def test_source_access_controls_are_reported_without_masking_real_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.samr.gov.cn":
            return httpx.Response(403, content=b"access denied")
        if request.url.host == "www.ftc.gov":
            return httpx.Response(404, content=b"missing")
        return httpx.Response(200, content=b"x" * 200)

    settings = SimpleNamespace(
        llm_base_url="", llm_api_key="", llm_model="",
        embedding_base_url="", embedding_api_key="", embedding_model="",
        rapidocr_base_url="",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_external_smoke(settings, ROOT, client)

    assert report["blocked"] == 1
    assert report["failed"] == 4
    assert report["strict_success"] is False
