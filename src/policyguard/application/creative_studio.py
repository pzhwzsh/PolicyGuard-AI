"""Evidence-grounded advertising copy and deterministic SKU asset production."""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

ABSOLUTE_CLAIMS = (
    "100%安全", "绝对安全", "国家级", "最高级", "最佳", "第一", "顶级",
    "永久", "零风险", "包治", "根治", "完全无副作用",
)
CERTIFICATION_TERMS = ("认证", "官方", "国家", "专利", "检测报告", "临床")
PLATFORM_SPECS = {
    "amazon": {"width": 2000, "height": 2000, "main_background": "white"},
    "tiktok_shop": {"width": 1080, "height": 1080, "main_background": "white"},
    "shopee": {"width": 1200, "height": 1200, "main_background": "white"},
    "temu": {"width": 1600, "height": 1600, "main_background": "white"},
    "taobao": {"width": 800, "height": 800, "main_background": "white"},
    "generic": {"width": 1200, "height": 1200, "main_background": "white"},
}


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise ValueError("creative_identifier_invalid")
    return value


def _fact_corpus(payload: dict[str, Any]) -> str:
    return " ".join(
        [payload["product_name"], payload.get("brand", "")]
        + [f"{item['name']} {item['value']}" for item in payload.get("verified_facts", [])]
        + [
            f"{key} {value}"
            for sku in payload.get("skus", [])
            for key, value in sku.get("attributes", {}).items()
        ]
    )


def validate_creative_payload(payload: dict[str, Any]) -> None:
    platform = payload.get("platform", "generic")
    if platform not in PLATFORM_SPECS:
        raise ValueError("creative_platform_not_supported")
    _safe_id(payload["external_id"])
    if not payload.get("skus"):
        raise ValueError("creative_sku_required")
    for sku in payload["skus"]:
        _safe_id(sku["sku_id"])
    for fact in payload.get("verified_facts", []):
        text = f"{fact['name']} {fact['value']}"
        if any(term in text for term in CERTIFICATION_TERMS) and not fact.get(
            "evidence_reference"
        ):
            raise ValueError("creative_sensitive_fact_requires_evidence")


def validate_ad_copy(copy: str, payload: dict[str, Any]) -> list[str]:
    issues = [f"absolute_claim:{term}" for term in ABSOLUTE_CLAIMS if term in copy]
    corpus = _fact_corpus(payload)
    for number in re.findall(r"\d+(?:\.\d+)?%?", copy):
        if number not in corpus:
            issues.append(f"unverified_number:{number}")
    for term in CERTIFICATION_TERMS:
        if term in copy and term not in corpus:
            issues.append(f"unverified_sensitive_term:{term}")
    return issues


def generate_grounded_ad_copy(payload: dict[str, Any], count: int = 3) -> list[dict[str, Any]]:
    validate_creative_payload(payload)
    facts = [item["value"].strip() for item in payload.get("verified_facts", [])]
    product = payload["product_name"].strip()
    brand = payload.get("brand", "").strip()
    prefix = f"{brand} " if brand else ""
    templates = [
        f"{prefix}{product}，{facts[0]}" if facts else f"{prefix}{product}，了解真实商品信息",
        f"{product}｜{'｜'.join(facts[:2])}" if facts else f"{product}｜按需选择合适 SKU",
        f"为日常选择提供清晰信息：{product}，{'，'.join(facts[:2])}"
        if facts else f"为日常选择提供清晰信息：{product}",
        f"{prefix}{product}，规格透明，按实际需求选择",
    ]
    rows = []
    for text in templates:
        normalized = text.strip("，｜ ")[:160]
        if normalized in {item["text"] for item in rows}:
            continue
        issues = validate_ad_copy(normalized, payload)
        rows.append({
            "text": normalized,
            "status": "passed" if not issues else "blocked",
            "issues": issues,
            "generation": "deterministic_verified_fact_template_v1",
            "policy_evidence": payload.get("policy_evidence", []),
            "automatic_publish": False,
        })
        if len(rows) >= count:
            break
    return rows


def scene_generation_request(payload: dict[str, Any]) -> dict[str, Any]:
    facts = "; ".join(item["value"] for item in payload.get("verified_facts", []))
    return {
        "task": "identity_preserving_product_scene_edit",
        "prompt": (
            f"Place the supplied real product image in a clean commercial scene. Product: "
            f"{payload['product_name']}. Verified visual facts only: {facts or 'none'}. "
            "Do not change product shape, color, packaging, labels, logos, quantities, or SKU. "
            "Do not add certifications, awards, medical imagery, before/after claims, or text."
        ),
        "negative_prompt": (
            "changed packaging, changed logo, extra product, fake certification, medical claim, "
            "before and after, watermark, unreadable text"
        ),
        "requires_source_image": True,
        "requires_identity_review": True,
        "automatic_publish": False,
    }


class CreativeStudioWorkspace:
    def __init__(self, upload_root: Path) -> None:
        self.root = (upload_root / "creatives").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{24}", project_id):
            raise LookupError("creative_project_not_found")
        path = (self.root / project_id).resolve()
        if path.parent != self.root:
            raise LookupError("creative_project_not_found")
        return path

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_creative_payload(payload)
        project_id = sha256(
            f"{payload['external_id']}:{uuid4()}".encode()
        ).hexdigest()[:24]
        directory = self.directory(project_id)
        directory.mkdir(parents=True)
        manifest = {
            "project_id": project_id,
            "revision": 0,
            "status": "awaiting_source_image",
            "created_at": datetime.now(UTC).isoformat(),
            "payload": payload,
            "platform_spec": PLATFORM_SPECS[payload["platform"]],
            "copy_candidates": generate_grounded_ad_copy(
                payload, int(payload.get("copy_count", 3))
            ),
            "scene_request": scene_generation_request(payload),
            "assets": [],
            "review": None,
        }
        self._save(directory, manifest)
        return manifest

    def load(self, project_id: str) -> dict[str, Any]:
        path = self.directory(project_id) / "manifest.json"
        if not path.is_file():
            raise LookupError("creative_project_not_found")
        return json.loads(path.read_text(encoding="utf-8"))

    def add_source_image(
        self,
        project_id: str,
        *,
        content: bytes,
        suffix: str,
        expected_revision: int,
        reviewer: str,
    ) -> dict[str, Any]:
        manifest = self.load(project_id)
        if manifest["revision"] != expected_revision:
            raise RuntimeError("creative_project_revision_conflict")
        if suffix not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("creative_source_image_type_not_supported")
        from PIL import Image

        try:
            with Image.open(io.BytesIO(content)) as candidate:
                candidate.verify()
        except Exception as exc:
            raise ValueError("creative_source_image_invalid") from exc
        image = Image.open(io.BytesIO(content)).convert("RGBA")
        if image.width < 300 or image.height < 300:
            raise ValueError("creative_source_image_too_small")
        directory = self.directory(project_id)
        source_path = directory / f"source{suffix}"
        source_path.write_bytes(content)
        assets = self._render_assets(directory, image, manifest)
        manifest.update({
            "revision": expected_revision + 1,
            "status": "review_required",
            "source_image": source_path.name,
            "source_image_sha256": sha256(content).hexdigest(),
            "source_uploaded_by": reviewer,
            "assets": assets,
        })
        self._save(directory, manifest)
        return manifest

    def review(
        self,
        project_id: str,
        *,
        expected_revision: int,
        reviewer: str,
        decision: str,
        approved_copy_indexes: list[int],
        comment: str,
    ) -> dict[str, Any]:
        manifest = self.load(project_id)
        if manifest["revision"] != expected_revision:
            raise RuntimeError("creative_project_revision_conflict")
        if manifest["status"] != "review_required":
            raise RuntimeError("creative_project_not_reviewable")
        candidates = manifest["copy_candidates"]
        if decision == "approve":
            if not approved_copy_indexes:
                raise ValueError("creative_approved_copy_required")
            if any(index < 0 or index >= len(candidates) for index in approved_copy_indexes):
                raise ValueError("creative_copy_index_invalid")
            if any(candidates[index]["status"] != "passed" for index in approved_copy_indexes):
                raise ValueError("creative_blocked_copy_cannot_be_approved")
        manifest.update({
            "revision": expected_revision + 1,
            "status": "approved" if decision == "approve" else "rejected",
            "review": {
                "reviewer": reviewer,
                "decision": decision,
                "approved_copy_indexes": approved_copy_indexes,
                "comment": comment,
                "reviewed_at": datetime.now(UTC).isoformat(),
            },
        })
        self._save(self.directory(project_id), manifest)
        return manifest

    def add_scene_candidate(
        self,
        project_id: str,
        *,
        content: bytes,
        expected_revision: int,
        provider_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self.load(project_id)
        if manifest["revision"] != expected_revision:
            raise RuntimeError("creative_project_revision_conflict")
        if manifest["status"] != "review_required":
            raise RuntimeError("creative_project_not_ready_for_scene")
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(content))
            image.verify()
        except Exception as exc:
            raise ValueError("creative_scene_image_invalid") from exc
        expected_size = (
            manifest["platform_spec"]["width"],
            manifest["platform_spec"]["height"],
        )
        with Image.open(io.BytesIO(content)) as image:
            if image.size != expected_size:
                raise ValueError("creative_scene_image_dimensions_invalid")
        directory = self.directory(project_id)
        filename = "scene-candidate.png"
        (directory / filename).write_bytes(content)
        manifest["assets"] = [
            item for item in manifest["assets"] if item["type"] != "scene_candidate"
        ] + [{
            "type": "scene_candidate",
            "filename": filename,
            "width": manifest["platform_spec"]["width"],
            "height": manifest["platform_spec"]["height"],
            "source_preserved": "pending_human_identity_review",
            "text_overlay": False,
            "provider": provider_metadata,
        }]
        manifest["revision"] = expected_revision + 1
        manifest["scene_request"]["status"] = "generated_pending_identity_review"
        self._save(directory, manifest)
        return manifest

    def package(self, project_id: str) -> Path:
        manifest = self.load(project_id)
        if manifest["status"] != "approved":
            raise RuntimeError("creative_project_not_approved")
        directory = self.directory(project_id)
        package = directory / "approved-assets.zip"
        approved = manifest["review"]["approved_copy_indexes"]
        copy_export = [manifest["copy_candidates"][index] for index in approved]
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "approved-copy.json",
                json.dumps(copy_export, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "audit-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            for asset in manifest["assets"]:
                path = directory / asset["filename"]
                if path.is_file():
                    archive.write(path, f"images/{path.name}")
        return package

    def _render_assets(self, directory: Path, source, manifest: dict) -> list[dict[str, Any]]:
        from PIL import Image, ImageDraw, ImageFont

        spec = manifest["platform_spec"]
        payload = manifest["payload"]
        width, height = spec["width"], spec["height"]
        assets = []
        fitted = source.copy()
        fitted.thumbnail((int(width * 0.78), int(height * 0.78)))
        main = Image.new("RGB", (width, height), "white")
        main.paste(
            fitted,
            ((width - fitted.width) // 2, (height - fitted.height) // 2),
            fitted,
        )
        main_name = f"{payload['external_id']}-main.jpg"
        main.save(directory / main_name, quality=94)
        assets.append({
            "type": "main_image",
            "filename": main_name,
            "width": width,
            "height": height,
            "source_preserved": True,
            "text_overlay": False,
        })
        font = ImageFont.load_default()
        for font_path in (
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            if Path(font_path).is_file():
                font = ImageFont.truetype(font_path, max(20, width // 45))
                break
        for sku in payload["skus"]:
            card = main.copy()
            draw = ImageDraw.Draw(card)
            panel_height = max(120, height // 7)
            brand_color = payload.get("brand_primary_color", "#173e2c")
            draw.rectangle((0, height - panel_height, width, height), fill="white")
            draw.rectangle((0, height - panel_height, 16, height), fill=brand_color)
            label = f"SKU {sku['sku_id']} | {sku['label']}"
            attributes = " | ".join(
                f"{key}: {value}" for key, value in sku.get("attributes", {}).items()
            )
            try:
                draw.text(
                    (width * 0.06, height - panel_height + 28),
                    label,
                    fill="black",
                    font=font,
                )
                draw.text(
                    (width * 0.06, height - panel_height + 68),
                    attributes[:180],
                    fill="#333333",
                    font=font,
                )
            except UnicodeEncodeError:
                draw.text(
                    (width * 0.06, height - panel_height + 28),
                    label.encode("ascii", "replace").decode(),
                    fill="black",
                    font=ImageFont.load_default(),
                )
            name = f"{payload['external_id']}-{sku['sku_id']}.jpg"
            card.save(directory / name, quality=92)
            assets.append({
                "type": "sku_card",
                "sku_id": sku["sku_id"],
                "filename": name,
                "width": width,
                "height": height,
                "source_preserved": True,
                "text_overlay": True,
            })
        return assets

    @staticmethod
    def _save(directory: Path, manifest: dict[str, Any]) -> None:
        path = directory / "manifest.json"
        temporary = directory / "manifest.tmp"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def delete_unapproved(self, project_id: str) -> None:
        manifest = self.load(project_id)
        if manifest["status"] == "approved":
            raise RuntimeError("approved_creative_deletion_requires_retention_workflow")
        shutil.rmtree(self.directory(project_id))
