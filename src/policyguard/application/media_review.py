"""Grounded image compliance review with OCR-region provenance."""

from __future__ import annotations

import base64
import io
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from policyguard.application.media_ingestion import parse_media_with_sidecar
from policyguard.application.provider_http import ProviderRetryPolicy, post_with_retry


def review_image(path: Path, settings, *, markets: list[str], category: str) -> dict[str, Any]:
    """Review one image and return findings anchored to normalized image coordinates."""
    regions: list[dict[str, Any]] = []
    warnings: list[str] = []
    width, height = _image_size(path)
    if settings.rapidocr_base_url:
        try:
            ocr_payload = parse_media_with_sidecar(
                path,
                base_url=settings.rapidocr_base_url,
                api_key=settings.document_parser_api_key,
            )
            regions = _extract_regions(ocr_payload, width=width, height=height)
        except Exception as exc:
            warnings.append(f"ocr_unavailable:{type(exc).__name__}")

    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return {
            "status": "review_unavailable",
            "summary": "视觉模型未配置，已保留 OCR 结果供人工检查。",
            "regions": regions,
            "findings": [],
            "warnings": warnings + ["vision_model_not_configured"],
        }

    image_url = _image_data_url(path)
    response = post_with_retry(
        settings.llm_base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "temperature": 0,
            "reasoning_effort": settings.llm_reasoning_effort,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是电商图片合规审核员。只标记图片里可以精确定位的风险文字，"
                        "不要扩展或猜测未出现的内容。返回 JSON："
                        '{"summary":string,"findings":[{"region_id":string|null,'
                        '"exact_text":string,"category":string,"severity":"high|medium|low",'
                        '"reason":string,"suggested_text":string,"bbox":[x1,y1,x2,y2]}]}。'
                        "bbox 使用 0-1000 归一化坐标并紧贴文字。suggested_text 必须保留原意，"
                        "只删除或弱化不可验证的绝对化、医疗、保证、价格、专利或认证表述。"
                        "没有明确风险就返回空 findings。不得生成法律条文或来源。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "markets": markets,
                                    "category": category or "all",
                                    "ocr_regions": regions,
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        },
        timeout=max(float(settings.llm_timeout_seconds), 90),
        policy=ProviderRetryPolicy(
            settings.provider_max_attempts, settings.provider_backoff_seconds
        ),
    )
    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("image_review_invalid_json") from exc
    findings = _normalize_findings(parsed.get("findings", []), regions)
    return {
        "status": "review_required" if findings else "passed",
        "summary": str(parsed.get("summary") or ("发现风险" if findings else "未发现明确风险")),
        "regions": regions,
        "findings": findings,
        "warnings": warnings,
        "model": settings.llm_model,
        "usage": payload.get("usage", {}),
    }


def _image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    content = path.read_bytes()
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(content)).convert("RGB")
        image.thumbnail((1600, 1600))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        content = output.getvalue()
        media_type = "image/jpeg"
    except Exception:
        pass
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return 1000, 1000


def _extract_regions(payload: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            text = value.get("text") or value.get("content") or value.get("transcription")
            box = value.get("bbox") or value.get("box") or value.get("points")
            if isinstance(text, str) and text.strip() and box is not None:
                bbox = _bbox(box, width=width, height=height)
                if bbox:
                    regions.append(
                        {
                            "id": f"region-{len(regions) + 1}",
                            "text": text.strip(),
                            "bbox": bbox,
                            "confidence": value.get("confidence") or value.get("score"),
                        }
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload.get("frames", payload) if isinstance(payload, dict) else payload)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for item in regions:
        key = (item["text"], tuple(item["bbox"]))
        if key not in seen:
            seen.add(key)
            item["id"] = f"region-{len(unique) + 1}"
            unique.append(item)
    return unique[:200]


def _bbox(value: Any, *, width: int | None = None, height: int | None = None) -> list[int] | None:
    numbers: list[float] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (list, tuple)):
                numbers.extend(
                    float(number)
                    for number in item[:2]
                    if isinstance(number, (int, float))
                )
            elif isinstance(item, (int, float)):
                numbers.append(float(item))
    if len(numbers) < 4:
        return None
    if len(numbers) > 4:
        xs = numbers[0::2]
        ys = numbers[1::2]
        numbers = [min(xs), min(ys), max(xs), max(ys)]
    x1, y1, x2, y2 = numbers[:4]
    maximum = max(abs(x1), abs(y1), abs(x2), abs(y2), 1)
    if maximum <= 1.5:
        numbers = [number * 1000 for number in (x1, y1, x2, y2)]
    elif width and height and (maximum > 1000 or width != 1000 or height != 1000):
        numbers = [x1 / width * 1000, y1 / height * 1000, x2 / width * 1000, y2 / height * 1000]
    return [max(0, min(1000, round(number))) for number in numbers]


def _normalize_findings(items: Any, regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    by_id = {item["id"]: item for item in regions}
    findings: list[dict[str, Any]] = []
    for index, item in enumerate(items[:30], start=1):
        if not isinstance(item, dict) or not str(item.get("exact_text", "")).strip():
            continue
        region = by_id.get(str(item.get("region_id")))
        bbox = _bbox(item.get("bbox")) or (region or {}).get("bbox")
        if not bbox:
            continue
        exact_text = str(item["exact_text"]).strip()[:500]
        category = str(item.get("category") or "other")[:80]
        suggestion = _safe_suggestion(
            exact_text,
            str(item.get("suggested_text") or "").strip()[:500],
            category,
        )
        findings.append(
            {
                "id": f"finding-{index}",
                "region_id": region.get("id") if region else None,
                "exact_text": exact_text,
                "category": category,
                "severity": (
                    item.get("severity")
                    if item.get("severity") in {"high", "medium", "low"}
                    else "medium"
                ),
                "reason": str(item.get("reason") or "需要人工确认")[:500],
                "suggested_text": suggestion or exact_text,
                "bbox": bbox,
            }
        )
    return findings


def _safe_suggestion(exact_text: str, suggestion: str, category: str) -> str:
    exact_normalized = unicodedata.normalize("NFKC", exact_text).replace(" ", "")
    suggestion_normalized = unicodedata.normalize("NFKC", suggestion).replace(" ", "")
    risky_markers = (
        "100%",
        "百分之百",
        "国家级",
        "最高级",
        "最佳",
        "第一",
        "永久",
        "绝对",
        "保证",
        "治愈",
        "根治",
    )
    approximation_markers = ("接近", "近乎", "几乎", "无限接近", "约等于", "差不多")
    percentage_risk = "100%" in exact_normalized or "百分之百" in exact_normalized
    suggestion_has_percentage = bool(re.search(r"\d+(?:\.\d+)?%", suggestion_normalized))
    unsafe = (
        not suggestion
        or suggestion_normalized == exact_normalized
        or any(marker in suggestion_normalized for marker in risky_markers)
        or (
            percentage_risk
            and (
                suggestion_has_percentage
                or any(marker in suggestion_normalized for marker in approximation_markers)
            )
        )
    )
    if not unsafe:
        return suggestion
    normalized = category.lower()
    if "medical" in normalized or "医疗" in category or "疾病" in category:
        return "用于日常健康管理，实际效果因人而异"
    if "guarantee" in normalized or "保证" in category or "承诺" in category:
        return "结果受实际条件影响，请以最终情况为准"
    if "price" in normalized or "价格" in category:
        return "实际价格与优惠以页面展示为准"
    if "patent" in normalized or "专利" in category:
        return "相关专利信息以有效证明材料为准"
    if "cert" in normalized or "认证" in category:
        return "相关认证信息以有效证明材料为准"
    return "有助于改善，实际效果因人而异"
