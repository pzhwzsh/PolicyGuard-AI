from pathlib import Path

import pytest

from policyguard.application.media_ingestion import stage_media_result, validate_media


def test_media_magic_validation_and_frame_provenance(tmp_path: Path) -> None:
    content = b"\x89PNG\r\n\x1a\n" + b"content"
    validate_media(content, ".png")
    source = tmp_path / "image.png"
    source.write_bytes(content)
    manifest = stage_media_result(
        tmp_path / "uploads",
        source,
        {"frames": [{"frame_index": 0, "timestamp_seconds": 0, "text": "claim"}]},
    )
    assert manifest["frame_count"] == 1
    assert manifest["automatic_publish"] is False


def test_media_validation_rejects_extension_magic_mismatch() -> None:
    with pytest.raises(ValueError, match="media_magic_invalid"):
        validate_media(b"not an image", ".png")
