"""Canonical PDF layout JSON plus derived Markdown for RAG."""

from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    block_id: str
    page: int
    block_type: str
    text: str
    markdown: str
    bbox: tuple[float, float, float, float] | None
    section_path: tuple[str, ...] = ()
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    filename: str
    content_hash: str
    parser: str
    page_count: int
    status: str
    warnings: tuple[str, ...]
    blocks: tuple[DocumentBlock, ...]

    def canonical_json(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "content_hash": self.content_hash,
            "parser": self.parser,
            "page_count": self.page_count,
            "status": self.status,
            "warnings": list(self.warnings),
            "blocks": [asdict(block) for block in self.blocks],
        }

    def markdown(self) -> str:
        return "\n\n".join(block.markdown for block in self.blocks if block.markdown)


class DocumentParser(Protocol):
    def parse(self, content: bytes, filename: str) -> ParsedDocument: ...


PDF_ACTIVE_MARKERS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/OpenAction",
    b"/RichMedia",
)


def validate_pdf_safety(content: bytes) -> None:
    if not content.startswith(b"%PDF"):
        raise ValueError("invalid_pdf_magic")
    markers = [marker.decode("ascii") for marker in PDF_ACTIVE_MARKERS if marker in content]
    if markers:
        raise ValueError(f"pdf_active_content_rejected:{','.join(markers)}")


def parsed_document_from_json(payload: dict[str, Any]) -> ParsedDocument:
    """Validate the stable sidecar contract shared by MinerU/Paddle adapters."""
    required = {"filename", "content_hash", "parser", "page_count", "status", "blocks"}
    if not required.issubset(payload):
        raise ValueError("parser_sidecar_invalid_payload")
    blocks = tuple(
        DocumentBlock(
            block_id=str(item["block_id"]),
            page=int(item["page"]),
            block_type=str(item["block_type"]),
            text=str(item.get("text", "")),
            markdown=str(item.get("markdown", "")),
            bbox=tuple(item["bbox"]) if item.get("bbox") is not None else None,
            section_path=tuple(item.get("section_path", [])),
            confidence=float(item.get("confidence", 1.0)),
            metadata=dict(item.get("metadata", {})),
        )
        for item in payload["blocks"]
    )
    return ParsedDocument(
        filename=str(payload["filename"]),
        content_hash=str(payload["content_hash"]),
        parser=str(payload["parser"]),
        page_count=int(payload["page_count"]),
        status=str(payload["status"]),
        warnings=tuple(str(value) for value in payload.get("warnings", [])),
        blocks=blocks,
    )


@dataclass(frozen=True, slots=True)
class HttpParserAdapter:
    """Adapter for an isolated parser service implementing POST /parse."""

    name: str
    base_url: str
    api_key: str = ""
    timeout_seconds: float = 180

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            self.base_url.rstrip("/") + "/parse",
            headers=headers,
            files={"file": (filename, content, "application/pdf")},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        parsed = parsed_document_from_json(response.json())
        if parsed.content_hash != sha256(content).hexdigest():
            raise ValueError("parser_sidecar_content_hash_mismatch")
        return parsed


class DocumentIngestionRouter:
    """Route native, scanned, and structurally complex PDFs without hiding degradation."""

    def __init__(
        self,
        native: DocumentParser,
        mineru: DocumentParser | None = None,
        ocr: DocumentParser | None = None,
        rapidocr: DocumentParser | None = None,
        table: DocumentParser | None = None,
    ) -> None:
        self.native = native
        self.mineru = mineru
        self.ocr = ocr
        self.rapidocr = rapidocr
        self.table = table

    def parse(self, content: bytes, filename: str) -> tuple[ParsedDocument, dict[str, Any]]:
        native = self.native.parse(content, filename)
        needs_ocr = any("scan_requires_ocr" in item for item in native.warnings)
        needs_table = any("complex_tables" in item for item in native.warnings)
        candidates = (
            [
                ("rapidocr", self.rapidocr),
                ("paddleocr", self.ocr),
                ("mineru", self.mineru),
            ]
            if needs_ocr
            else [("pp_structure", self.table), ("mineru", self.mineru)]
            if needs_table
            else []
        )
        failures = []
        for route, parser in candidates:
            if parser is None:
                continue
            try:
                parsed = parser.parse(content, filename)
                return parsed, self._route_detail(route, parsed, failures)
            except Exception as exc:
                failures.append({"route": route, "error": type(exc).__name__})
        route = "native_review_required" if candidates or needs_ocr or needs_table else "native"
        return native, self._route_detail(route, native, failures)

    @staticmethod
    def _route_detail(
        route: str, parsed: ParsedDocument, failures: list[dict[str, str]]
    ) -> dict[str, Any]:
        confidences = [block.confidence for block in parsed.blocks]
        return {
            "route": route,
            "parser": parsed.parser,
            "mean_block_confidence": (
                round(sum(confidences) / len(confidences), 4) if confidences else 0.0
            ),
            "review_required": parsed.status != "parsed",
            "adapter_failures": failures,
        }


def configured_document_router(settings) -> DocumentIngestionRouter:
    def adapter(name: str, base_url: str) -> HttpParserAdapter | None:
        return (
            HttpParserAdapter(name, base_url, settings.document_parser_api_key)
            if base_url
            else None
        )

    return DocumentIngestionRouter(
        PdfPlumberLayoutParser(),
        mineru=adapter("mineru", settings.mineru_base_url),
        ocr=adapter("paddleocr", settings.paddleocr_base_url),
        rapidocr=adapter("rapidocr", settings.rapidocr_base_url),
        table=adapter("pp_structure", settings.ppstructure_base_url),
    )


class PdfPlumberLayoutParser:
    """Fast path for native PDFs; complex pages are flagged for MinerU/OCR review."""

    def __init__(self, max_pages: int = 300) -> None:
        self.max_pages = max_pages

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        if not content.startswith(b"%PDF"):
            raise ValueError("invalid_pdf_magic")
        try:
            import pdfplumber
        except ImportError as exc:
            raise RuntimeError("install policyguard-ai[pdf] first") from exc
        blocks: list[DocumentBlock] = []
        warnings: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if len(pdf.pages) > self.max_pages:
                raise ValueError("pdf_page_limit_exceeded")
            for page_number, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text(layout=True) or "").strip()
                tables = page.extract_tables() or []
                if tables and text:
                    text = self._strip_table_lines(text, tables)
                if text:
                    blocks.extend(self._text_blocks(text, page_number, page.width, page.height))
                if tables:
                    blocks.extend(self._table_blocks(tables, page_number))
                if page.images:
                    blocks.extend(self._image_blocks(page.images, page_number))
                if not text and page.images:
                    warnings.append(f"page_{page_number}:scan_requires_ocr")
                elif not text:
                    warnings.append(f"page_{page_number}:no_extractable_content")
                if len(tables) > 1:
                    warnings.append(f"page_{page_number}:complex_tables_require_structure_review")
        status = "review_required" if warnings else "parsed"
        return ParsedDocument(
            filename=filename,
            content_hash=sha256(content).hexdigest(),
            parser="pdfplumber_layout_v1",
            page_count=len(pdf.pages),
            status=status,
            warnings=tuple(warnings),
            blocks=tuple(blocks),
        )

    @staticmethod
    def _text_blocks(text: str, page: int, width: float, height: float) -> list[DocumentBlock]:
        blocks = []
        section_path: list[str] = []
        for index, raw in enumerate(re.split(r"\n\s*\n", text)):
            value = "\n".join(line.rstrip() for line in raw.splitlines()).strip()
            if not value:
                continue
            first_line = value.splitlines()[0].strip()
            heading = PdfPlumberLayoutParser._looks_like_heading(first_line, value)
            if heading:
                section_path = [first_line]
            markdown = f"## {value}" if heading else value
            blocks.append(
                DocumentBlock(
                    block_id=f"p{page}-text-{index}",
                    page=page,
                    block_type="heading" if heading else "paragraph",
                    text=value,
                    markdown=markdown,
                    bbox=(0.0, 0.0, float(width), float(height)),
                    section_path=tuple(section_path),
                )
            )
        return blocks

    @staticmethod
    def _looks_like_heading(first_line: str, block_text: str) -> bool:
        if len(first_line) > 100 or "\n" in block_text:
            return False
        if re.match(r"^[IVXLCDM]+\.\s+\S", first_line):
            return True
        if re.match(r"^[A-Z]\.\s+\S", first_line):
            return True
        if re.match(r"^第[一二三四五六七八九十百0-9]+[章节条]\s*", first_line):
            return True
        if re.match(r"^\d+(?:\.\d+)+\.?\s+\S", first_line):
            return True
        if re.match(r"^\d+\.\s+\S", first_line):
            return len(first_line) <= 60 and not re.search(
                r"(?:;|,|\band|\bor)\s*$", first_line, flags=re.IGNORECASE
            )
        return False

    @staticmethod
    def _table_blocks(tables: list[list[list[str | None]]], page: int) -> list[DocumentBlock]:
        blocks = []
        for index, table in enumerate(tables):
            rows = [[str(cell or "").strip() for cell in row] for row in table if row]
            if not rows:
                continue
            width = max(len(row) for row in rows)
            normalized = [row + [""] * (width - len(row)) for row in rows]
            multi_level = len(normalized) > 1 and any(not cell for cell in normalized[0])
            header_row_count = 2 if multi_level else 1
            header = normalized[header_row_count - 1]
            parent_headers = []
            current_parent = ""
            for cell in normalized[0]:
                if cell:
                    current_parent = cell
                parent_headers.append(current_parent)
            header_paths = [
                " > ".join(part for part in (parent_headers[col], header[col]) if part)
                if multi_level
                else header[col]
                for col in range(width)
            ]
            markdown_rows = [
                *([f"**{normalized[0][0]}**", ""] if multi_level else []),
                "| " + " | ".join(header) + " |",
                "| " + " | ".join(["---"] * width) + " |",
                *[
                    "| " + " | ".join(row) + " |"
                    for row in normalized[header_row_count:]
                ],
            ]
            blocks.append(
                DocumentBlock(
                    block_id=f"p{page}-table-{index}",
                    page=page,
                    block_type="table",
                    text="\n".join(" | ".join(row) for row in normalized),
                    markdown="\n".join(markdown_rows),
                    bbox=None,
                    metadata={
                        "rows": normalized,
                        "row_count": len(normalized),
                        "column_count": width,
                        "header_row_count": header_row_count,
                        "header_paths": header_paths,
                    },
                )
            )
        return blocks

    @staticmethod
    def _strip_table_lines(text: str, tables: list[list[list[str | None]]]) -> str:
        table_rows = [
            [str(cell or "").strip() for cell in row if str(cell or "").strip()]
            for table in tables
            for row in table
            if row
        ]
        kept = []
        for line in text.splitlines():
            normalized_line = " ".join(line.split())
            is_table_line = any(
                (len(row) == 1 and normalized_line == row[0])
                or (len(row) >= 2 and sum(cell in normalized_line for cell in row) >= 2)
                for row in table_rows
            )
            if not is_table_line:
                kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _image_blocks(images: list[dict], page: int) -> list[DocumentBlock]:
        return [
            DocumentBlock(
                block_id=f"p{page}-image-{index}",
                page=page,
                block_type="image",
                text="",
                markdown=f"![PDF image on page {page}](asset://page-{page}-image-{index})",
                bbox=(
                    float(image.get("x0", 0)),
                    float(image.get("top", 0)),
                    float(image.get("x1", 0)),
                    float(image.get("bottom", 0)),
                ),
                confidence=0.0,
                metadata={"requires_vision": True},
            )
            for index, image in enumerate(images)
        ]


TOKEN_SPAN_PATTERN = re.compile(
    r"[\u4e00-\u9fff]|[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*|[^\s]"
)


def approximate_token_spans(text: str) -> list[tuple[int, int]]:
    """Return deterministic CJK-aware token spans without binding to one embedding vendor."""
    return [(match.start(), match.end()) for match in TOKEN_SPAN_PATTERN.finditer(text)]


def rag_chunks(
    document: ParsedDocument,
    max_tokens: int = 800,
    overlap_tokens: int = 100,
) -> list[dict[str, Any]]:
    if max_tokens < 1 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("invalid_chunk_token_budget")
    chunks = []
    for block in document.blocks:
        if not block.text and block.block_type == "image":
            continue
        spans = approximate_token_spans(block.text)
        token_start = 0
        while token_start < len(spans):
            token_end = min(token_start + max_tokens, len(spans))
            char_start = 0 if token_start == 0 else spans[token_start][0]
            char_end = len(block.text) if token_end == len(spans) else spans[token_end - 1][1]
            text = block.text[char_start:char_end].strip()
            if text:
                chunks.append({
                    "chunk_id": f"{document.content_hash[:12]}-{block.block_id}-{char_start}",
                    "text": text,
                    "markdown": block.markdown if len(spans) <= max_tokens else text,
                    "page": block.page,
                    "block_type": block.block_type,
                    "section_path": list(block.section_path),
                    "source_hash": document.content_hash,
                    "parser": document.parser,
                    "chunking_strategy": "structure_token_window_v1",
                    "token_start": token_start,
                    "token_end": token_end,
                    "token_count": token_end - token_start,
                    "overlap_tokens": 0 if token_start == 0 else overlap_tokens,
                    "char_start": char_start,
                    "char_end": char_end,
                })
            if token_end == len(spans):
                break
            token_start = token_end - overlap_tokens
    return chunks


def stage_parsed_document(
    document: ParsedDocument,
    route: dict[str, Any],
    upload_dir: Path,
) -> tuple[str, list[dict[str, Any]]]:
    document_id = document.content_hash[:24]
    staged = upload_dir / document_id
    staged.mkdir(parents=True, exist_ok=True)
    chunks = rag_chunks(document)
    (staged / "document.json").write_text(
        json.dumps(document.canonical_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (staged / "document.md").write_text(document.markdown(), encoding="utf-8")
    (staged / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest_path = staged / "manifest.json"
    existing = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    existing.update({
        "document_id": document_id,
        "activation_status": existing.get("activation_status", "staged"),
        "created_at": existing.get("created_at", datetime.now(UTC).isoformat()),
        "parser_route": route,
        "revision": existing.get("revision", 0),
    })
    manifest_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return document_id, chunks
