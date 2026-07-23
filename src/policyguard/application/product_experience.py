"""Product workspace, safety intake, readiness, and publish preflight controls."""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from policyguard.application.creative_studio import (
    ABSOLUTE_CLAIMS,
    PLATFORM_SPECS,
    validate_ad_copy,
)
from policyguard.application.security import detect_prompt_injection
from policyguard.infrastructure.database import BackgroundJobRecord

PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "cn_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "cn_id": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}
BLOCKED_FILE_SIGNATURES = (
    (b"MZ", "windows_executable"),
    (b"\x7fELF", "elf_executable"),
    (b"#!", "script_executable"),
)
SAFE_UPLOAD_SUFFIXES = {
    ".csv", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".mp4", ".webm"
}
PLATFORM_COPY_LIMITS = {
    "amazon": 200,
    "tiktok_shop": 150,
    "shopee": 120,
    "temu": 160,
    "taobao": 60,
    "generic": 200,
}


def scan_text_safety(value: Any) -> dict[str, Any]:
    text_value = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    pii = {
        name: len(pattern.findall(text_value))
        for name, pattern in PII_PATTERNS.items()
        if pattern.search(text_value)
    }
    injection = detect_prompt_injection(text_value)
    return {
        "safe_for_model": not injection and not pii,
        "prompt_injection": injection,
        "pii": pii,
        "recommended_action": (
            "block" if injection else "redact_before_model" if pii else "allow"
        ),
    }


def scan_upload(filename: str, content: bytes, *, max_bytes: int) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    issues: list[str] = []
    if suffix not in SAFE_UPLOAD_SUFFIXES:
        issues.append("file_type_not_allowlisted")
    if len(content) > max_bytes:
        issues.append("file_size_limit_exceeded")
    for signature, label in BLOCKED_FILE_SIGNATURES:
        if content.startswith(signature):
            issues.append(label)
    if content.startswith(b"PK") and suffix not in {".xlsx"}:
        issues.append("archive_not_allowed")
    if b"/JavaScript" in content[:2_000_000] or b"/Launch" in content[:2_000_000]:
        issues.append("active_pdf_content_detected")
    return {
        "status": "blocked" if issues else "passed",
        "filename": Path(filename).name,
        "size_bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "issues": issues,
        "malware_engine": "signature_and_structure_baseline",
        "requires_external_antivirus": True,
    }


def validate_outbound_url(url: str, allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("outbound_url_requires_https")
    hostname = parsed.hostname.casefold()
    if allowed_hosts and hostname not in {item.casefold() for item in allowed_hosts}:
        raise ValueError("outbound_host_not_allowlisted")
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError("outbound_host_resolution_failed") from exc
    if any(
        ipaddress.ip_address(address).is_private
        or ipaddress.ip_address(address).is_loopback
        or ipaddress.ip_address(address).is_link_local
        or ipaddress.ip_address(address).is_reserved
        for address in addresses
    ):
        raise ValueError("outbound_private_network_blocked")


class ProductWorkspace:
    def __init__(self, upload_root: Path, tenant: str) -> None:
        tenant_key = sha256(tenant.encode("utf-8")).hexdigest()[:24]
        self.root = (upload_root / "products" / tenant_key).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            (json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("*.json")),
            key=lambda item: item["updated_at"],
            reverse=True,
        )

    def get(self, external_id: str) -> dict[str, Any]:
        path = self._path(external_id)
        if not path.is_file():
            raise LookupError("product_not_found")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, payload: dict[str, Any], expected_revision: int | None) -> dict[str, Any]:
        path = self._path(payload["external_id"])
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        current_revision = existing["revision"] if existing else 0
        if existing and expected_revision is None:
            raise RuntimeError("product_expected_revision_required")
        if expected_revision is not None and expected_revision != current_revision:
            raise RuntimeError("product_revision_conflict")
        now = datetime.now(UTC).isoformat()
        product = {
            **payload,
            "revision": current_revision + 1,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "content_sha256": sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return product

    def delete(self, external_id: str, expected_revision: int) -> dict[str, Any]:
        product = self.get(external_id)
        if product["revision"] != expected_revision:
            raise RuntimeError("product_revision_conflict")
        path = self._path(external_id)
        path.unlink()
        return {"external_id": external_id, "deleted": True, "recoverable": False}

    def _path(self, external_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", external_id):
            raise ValueError("product_identifier_invalid")
        path = (self.root / f"{external_id}.json").resolve()
        if path.parent != self.root:
            raise ValueError("product_identifier_invalid")
        return path


def publish_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    platform = payload.get("platform", "generic")
    if platform not in PLATFORM_SPECS:
        raise ValueError("creative_platform_not_supported")
    checks: list[dict[str, Any]] = []

    def add(code: str, status: str, title: str, detail: str) -> None:
        checks.append({"code": code, "status": status, "title": title, "detail": detail})

    copy = payload.get("copy", "")
    creative_payload = payload.get("creative_payload", payload)
    copy_issues = validate_ad_copy(copy, creative_payload)
    add(
        "copy_grounding",
        "red" if copy_issues else "green",
        "文案事实约束",
        "、".join(copy_issues) if copy_issues else "数字和敏感声明均可追溯到已验证事实",
    )
    limit = PLATFORM_COPY_LIMITS[platform]
    add(
        "copy_length",
        "red" if len(copy) > limit else "green",
        "平台文案长度",
        f"{len(copy)}/{limit} 字符",
    )
    absolute = [term for term in ABSOLUTE_CLAIMS if term in copy]
    add(
        "prohibited_claims",
        "red" if absolute else "green",
        "禁限用表达",
        "、".join(absolute) if absolute else "未发现绝对化表达",
    )
    expected = PLATFORM_SPECS[platform]
    assets = payload.get("assets", [])
    wrong_size = [
        item.get("filename", "unknown")
        for item in assets
        if (item.get("width"), item.get("height"))
        != (expected["width"], expected["height"])
    ]
    add(
        "asset_dimensions",
        "red" if wrong_size else "green" if assets else "yellow",
        "图片尺寸",
        "尺寸不符：" + "、".join(wrong_size) if wrong_size else (
            f"符合 {expected['width']}×{expected['height']}" if assets else "尚未提供图片"
        ),
    )
    identity_pending = any(
        item.get("source_preserved") == "pending_human_identity_review" for item in assets
    )
    add(
        "product_identity",
        "yellow" if identity_pending else "green",
        "商品身份一致性",
        "AI 场景图需要人工对比包装、Logo、颜色和数量" if identity_pending else "未发现待确认项",
    )
    evidence = creative_payload.get("policy_evidence", [])
    add(
        "policy_evidence",
        "green" if evidence else "yellow",
        "法规证据",
        f"已关联 {len(evidence)} 条候选依据，仍需人工确认" if evidence else "未关联平台法规依据",
    )
    text_safety = scan_text_safety(copy)
    add(
        "privacy_and_injection",
        "red" if not text_safety["safe_for_model"] else "green",
        "隐私与提示注入",
        text_safety["recommended_action"],
    )
    totals = {
        status: sum(item["status"] == status for item in checks)
        for status in ("red", "yellow", "green")
    }
    return {
        "decision": "blocked" if totals["red"] else "human_review" if totals["yellow"] else "ready",
        "automatic_publish": False,
        "checks": checks,
        "summary": totals,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def system_readiness(settings, session: Session) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(component: str, status: str, detail: str, action: str = "") -> None:
        checks.append({
            "component": component,
            "status": status,
            "detail": detail,
            "action": action,
        })

    try:
        session.execute(text("SELECT 1"))
        add("database", "ready", "数据库连接正常")
    except Exception as exc:  # pragma: no cover - exercised only during outage
        add("database", "blocked", type(exc).__name__, "检查 DATABASE_URL")
    upload_root = Path(settings.upload_dir).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(upload_root)
    free_gb = round(usage.free / 1024**3, 2)
    add(
        "storage",
        "ready" if free_gb >= 2 else "warning",
        f"可用空间 {free_gb} GB",
        "清理过期上传" if free_gb < 2 else "",
    )
    add(
        "llm",
        "ready" if settings.llm_base_url and settings.llm_model else "optional",
        settings.llm_model or "未配置，使用确定性流程",
    )
    add(
        "image_generation",
        "ready"
        if settings.image_generation_base_url and settings.image_generation_model
        else "optional",
        settings.image_generation_model or "未配置，主图与 SKU 图仍可用",
    )
    add(
        "ocr",
        "ready" if settings.rapidocr_base_url else "optional",
        settings.rapidocr_base_url or "未配置",
    )
    add(
        "authentication",
        "ready" if settings.admin_api_key else "warning",
        "API 密钥保护已启用" if settings.admin_api_key else "本地演示模式未启用登录保护",
        "生产环境配置 ADMIN_API_KEY" if not settings.admin_api_key else "",
    )
    job_counts = dict(
        session.execute(
            select(BackgroundJobRecord.status, func.count()).group_by(BackgroundJobRecord.status)
        ).all()
    )
    add(
        "worker_queue",
        "warning" if job_counts.get("failed", 0) else "ready",
        f"排队 {job_counts.get('queued', 0)}，"
        f"运行 {job_counts.get('running', 0)}，"
        f"失败 {job_counts.get('failed', 0)}",
    )
    return {
        "overall": "blocked" if any(item["status"] == "blocked" for item in checks) else (
            "attention" if any(item["status"] == "warning" for item in checks) else "ready"
        ),
        "checks": checks,
        "checked_at": datetime.now(UTC).isoformat(),
    }
