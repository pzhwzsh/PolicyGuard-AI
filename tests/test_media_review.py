import io
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from policyguard.api.main import create_app
from policyguard.application import media_review


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (800, 400), "white").save(output, format="PNG")
    return output.getvalue()


def test_ocr_regions_are_normalized_to_image_coordinates() -> None:
    payload = {
        "frames": [
            {"regions": [{"text": "100%有效", "bbox": [80, 40, 400, 120], "score": 0.98}]}
        ]
    }
    regions = media_review._extract_regions(payload, width=800, height=400)
    assert regions == [
        {
            "id": "region-1",
            "text": "100%有效",
            "bbox": [100, 100, 500, 300],
            "confidence": 0.98,
        }
    ]


def test_review_image_anchors_model_finding_to_ocr_region(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "creative.png"
    path.write_bytes(image_bytes())
    monkeypatch.setattr(
        media_review,
        "parse_media_with_sidecar",
        lambda *args, **kwargs: {
            "frames": [{"regions": [{"text": "100%有效", "bbox": [80, 40, 400, 120]}]}]
        },
    )

    class Response:
        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"summary":"发现绝对化表述","findings":['
                                '{"region_id":"region-1","exact_text":"100%有效",'
                                '"category":"absolute_claim","severity":"high",'
                                '"reason":"无法验证绝对效果","suggested_text":"有助于改善"}]}'
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 12},
            }

    monkeypatch.setattr(media_review, "post_with_retry", lambda *args, **kwargs: Response())
    settings = SimpleNamespace(
        rapidocr_base_url="http://ocr",
        media_ocr_enabled=True,
        document_parser_api_key="",
        llm_base_url="http://llm",
        llm_api_key="key",
        llm_model="vision-model",
        llm_reasoning_effort="low",
        llm_timeout_seconds=10,
        provider_max_attempts=1,
        provider_backoff_seconds=0,
    )
    result = media_review.review_image(path, settings, markets=["CN"], category="美妆")
    assert result["status"] == "review_required"
    assert result["findings"][0]["bbox"] == [100, 100, 500, 300]
    assert result["findings"][0]["suggested_text"] == "有助于改善"


def test_risky_percentage_is_not_preserved_in_suggestion() -> None:
    for exact, suggestion in [
        ("100%安全", "接近100%安全"),
        ("100％安全", "近乎百分之百安全"),
        ("百分之百有效", "99.9%有效"),
    ]:
        findings = media_review._normalize_findings(
            [
                {
                    "exact_text": exact,
                    "category": "absolute_claim",
                    "severity": "medium",
                    "reason": "绝对化表述",
                    "suggested_text": suggestion,
                    "bbox": [100, 100, 400, 200],
                }
            ],
            [],
        )
        assert findings[0]["suggested_text"] == "有助于改善，实际效果因人而异"


def test_media_review_endpoint_persists_original(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "policyguard.api.main.review_image",
        lambda *args, **kwargs: {
            "status": "review_required",
            "summary": "发现风险",
            "regions": [],
            "findings": [
                {
                    "id": "finding-1",
                    "exact_text": "100%有效",
                    "category": "absolute_claim",
                    "severity": "high",
                    "reason": "无法验证",
                    "suggested_text": "有助于改善",
                    "bbox": [100, 100, 500, 300],
                }
            ],
            "warnings": [],
        },
    )
    with TestClient(create_app(f"sqlite:///{tmp_path / 'media.db'}")) as client:
        response = client.post(
            "/api/v1/media/review",
            data={"markets": "CN,US", "category": "美妆"},
            files={"file": ("creative.png", image_bytes(), "image/png")},
        )
        assert response.status_code == 200
        payload = response.json()
        asset = client.get(payload["asset_url"])
    assert payload["markets"] == ["CN", "US"]
    assert payload["findings"][0]["bbox"] == [100, 100, 500, 300]
    assert asset.status_code == 200
    assert asset.content.startswith(b"\x89PNG")
