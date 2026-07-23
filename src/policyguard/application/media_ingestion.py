"""Image/video OCR adapter with frame-level provenance."""

import base64
import json
from hashlib import sha256
from pathlib import Path

from policyguard.application.provider_http import ProviderRetryPolicy, post_with_retry

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def validate_media(content: bytes, suffix: str) -> None:
    if suffix not in MEDIA_TYPES:
        raise ValueError("media_type_not_supported")
    valid = (
        (suffix == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8\xff"))
        or (suffix == ".mp4" and b"ftyp" in content[:32])
        or (suffix == ".webm" and content.startswith(b"\x1aE\xdf\xa3"))
    )
    if not valid:
        raise ValueError("media_magic_invalid")


def parse_media_with_sidecar(
    path: Path,
    *,
    base_url: str,
    api_key: str = "",
    retry_policy: ProviderRetryPolicy | None = None,
) -> dict:
    if not base_url:
        raise RuntimeError("media_ocr_sidecar_not_configured")
    headers = {"X-Parser-Key": api_key} if api_key else {}
    response = post_with_retry(
        base_url.rstrip("/") + "/parse-media",
        headers=headers,
        json={
            "filename": path.name,
            "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
        timeout=180,
        policy=retry_policy or ProviderRetryPolicy(),
    )
    return response.json()


def stage_media_result(upload_root: Path, input_path: Path, result: dict) -> dict:
    content = input_path.read_bytes()
    media_id = sha256(content).hexdigest()[:24]
    directory = upload_root / "media" / media_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("original" + input_path.suffix.lower())
    if not target.exists():
        target.write_bytes(content)
    manifest = {
        "media_id": media_id,
        "status": "review_required",
        "filename": input_path.name,
        "content_hash": sha256(content).hexdigest(),
        "frame_count": len(result.get("frames", [])),
        "frames": result.get("frames", []),
        "warnings": result.get("warnings", []),
        "automatic_publish": False,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
