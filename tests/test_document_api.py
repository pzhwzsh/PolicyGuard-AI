import io
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from policyguard.api.main import create_app
from policyguard.application.document_ingestion import DocumentBlock, ParsedDocument
from policyguard.config import get_settings


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_upload_is_staged_with_canonical_outputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/documents/parse",
            files={"file": ("blank.pdf", blank_pdf(), "application/pdf")},
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["activation_status"] == "staged"
    assert payload["status"] == "review_required"
    staged = tmp_path / "data/uploads" / payload["document_id"]
    assert (staged / "document.json").exists()
    assert (staged / "document.md").exists()
    assert (staged / "chunks.json").exists()


def test_pdf_upload_rejects_wrong_content_type(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/documents/parse",
            files={"file": ("fake.txt", b"not pdf", "text/plain")},
        )
    assert response.status_code == 415


def test_reviewed_pdf_can_be_activated_and_searched(tmp_path: Path, monkeypatch) -> None:
    content = blank_pdf()

    class ParsedRouter:
        def parse(self, uploaded: bytes, filename: str):
            return (
                ParsedDocument(
                    filename=filename,
                    content_hash=__import__("hashlib").sha256(uploaded).hexdigest(),
                    parser="test_layout_v1",
                    page_count=1,
                    status="parsed",
                    warnings=(),
                    blocks=(
                        DocumentBlock(
                            block_id="p1-text-0",
                            page=1,
                            block_type="paragraph",
                            text="Synthetic activation marker regulation text",
                            markdown="Synthetic activation marker regulation text",
                            bbox=(0.0, 0.0, 100.0, 100.0),
                            section_path=("Article 1",),
                        ),
                    ),
                ),
                {
                    "route": "native",
                    "parser": "test_layout_v1",
                    "mean_block_confidence": 1.0,
                    "review_required": False,
                    "adapter_failures": [],
                },
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "policyguard.api.main.configured_document_router", lambda settings: ParsedRouter()
    )
    app = create_app(f"sqlite:///{(tmp_path / 'activate.db').as_posix()}")
    with TestClient(app) as client:
        parsed = client.post(
            "/api/v1/documents/parse",
            files={"file": ("official.pdf", content, "application/pdf")},
        ).json()
        response = client.post(
            f"/api/v1/documents/{parsed['document_id']}/approve",
            json={
                "title": "Official test regulation",
                "publisher": "Test regulator",
                "source_url": "https://regulator.example/official.pdf",
                "version": "2026-01",
                "published_at": "2026-01-01",
                "jurisdiction": "US",
                "effective_from": "2026-01-01",
                "reviewer": "reviewer-1",
            },
        )
        search = client.get(
            "/api/v1/knowledge/search",
            params={"q": "activation marker", "market": "US"},
        )
    assert response.status_code == 200
    assert response.json()["activation_status"] == "active"
    assert response.json()["activated_chunks"] == 1
    assert search.status_code == 200
    assert any("activation marker" in item["text"] for item in search.json()["results"])


def test_unresolved_pdf_cannot_be_activated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{(tmp_path / 'review.db').as_posix()}")
    with TestClient(app) as client:
        parsed = client.post(
            "/api/v1/documents/parse",
            files={"file": ("blank.pdf", blank_pdf(), "application/pdf")},
        ).json()
        response = client.post(
            f"/api/v1/documents/{parsed['document_id']}/approve",
            json={
                "title": "Blank",
                "publisher": "Test",
                "source_url": "https://example.com/blank.pdf",
                "version": "1",
                "published_at": "2026-01-01",
                "jurisdiction": "CN",
                "effective_from": "2026-01-01",
                "reviewer": "reviewer-1",
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "document_requires_structure_review"


def test_async_pdf_upload_is_idempotently_queued(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{(tmp_path / 'async.db').as_posix()}")
    content = blank_pdf()
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/documents/parse-async",
            files={"file": ("blank.pdf", content, "application/pdf")},
        )
        second = client.post(
            "/api/v1/documents/parse-async",
            files={"file": ("blank.pdf", content, "application/pdf")},
        )
        status_response = client.get(f"/api/v1/jobs/{first.json()['id']}")
    assert first.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert status_response.json()["status"] == "queued"


def test_pdf_correction_endpoint_resolves_review_warning(tmp_path: Path, monkeypatch) -> None:
    from policyguard.application.document_ingestion import DocumentBlock, ParsedDocument

    class ReviewRouter:
        def parse(self, uploaded: bytes, filename: str):
            return ParsedDocument(
                filename, __import__("hashlib").sha256(uploaded).hexdigest(), "test", 1,
                "review_required", ("page_1:structure_review",),
                (DocumentBlock("p1", 1, "paragraph", "wr0ng", "wr0ng", None),),
            ), {"route": "native_review_required", "parser": "test",
                "mean_block_confidence": 0.5, "review_required": True,
                "adapter_failures": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "policyguard.api.main.configured_document_router", lambda settings: ReviewRouter()
    )
    app = create_app(f"sqlite:///{(tmp_path / 'correct.db').as_posix()}")
    with TestClient(app) as client:
        parsed = client.post(
            "/api/v1/documents/parse",
            files={"file": ("review.pdf", blank_pdf(), "application/pdf")},
        ).json()
        corrected = client.patch(
            f"/api/v1/documents/{parsed['document_id']}",
            json={
                "expected_revision": 0, "reviewer": "reviewer",
                "corrections": [{"block_id": "p1", "text": "correct"}],
                "resolved_warnings": ["page_1:structure_review"],
            },
        )
    assert corrected.status_code == 200
    assert corrected.json()["document"]["status"] == "parsed"
    assert corrected.json()["correction_rate"] == 1.0


def test_staged_document_deletion_requires_admin_reviewer_and_removes_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    app = create_app(f"sqlite:///{(tmp_path / 'delete.db').as_posix()}")
    try:
        with TestClient(app) as client:
            parsed = client.post(
                "/api/v1/documents/parse",
                files={"file": ("delete.pdf", blank_pdf(), "application/pdf")},
            ).json()
            path = f"/api/v1/documents/{parsed['document_id']}"
            body = {
                "expected_revision": 0,
                "reviewer": "alice",
                "reason": "user requested deletion",
            }
            denied = client.request("DELETE", path, json=body)
            deleted = client.request(
                "DELETE",
                path,
                headers={"X-Admin-Key": "secret", "X-Reviewer": "alice"},
                json=body,
            )
        assert denied.status_code == 401
        assert deleted.status_code == 200
        assert deleted.json()["deleted_file_count"] >= 4
        assert not (tmp_path / "data/uploads" / parsed["document_id"]).exists()
    finally:
        get_settings.cache_clear()
