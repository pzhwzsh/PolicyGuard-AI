import io

from pypdf import PdfWriter

from policyguard.application.document_ingestion import (
    DocumentBlock,
    PdfPlumberLayoutParser,
    ParsedDocument,
    approximate_token_spans,
    rag_chunks,
    validate_pdf_safety,
)


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_blank_pdf_is_staged_for_review_instead_of_faking_text() -> None:
    parsed = PdfPlumberLayoutParser().parse(blank_pdf(), "blank.pdf")
    assert parsed.page_count == 1
    assert parsed.status == "review_required"
    assert "page_1:no_extractable_content" in parsed.warnings
    assert rag_chunks(parsed) == []


def test_table_block_preserves_cells_and_markdown() -> None:
    blocks = PdfPlumberLayoutParser._table_blocks(
        [[
            ["一级表头", "二级表头"],
            ["医疗广告", "批准文件"],
        ]],
        page=2,
    )
    block = blocks[0]
    assert block.block_type == "table"
    assert block.metadata["rows"][1] == ["医疗广告", "批准文件"]
    assert "| 一级表头 | 二级表头 |" in block.markdown


def test_multilevel_header_is_preserved_as_header_paths() -> None:
    block = PdfPlumberLayoutParser._table_blocks(
        [[
            ["审批要求", None, None],
            ["市场", "材料", "期限"],
            ["CN", "批准文件", "一年"],
        ]],
        page=1,
    )[0]
    assert block.metadata["header_row_count"] == 2
    assert block.metadata["header_paths"] == [
        "审批要求 > 市场",
        "审批要求 > 材料",
        "审批要求 > 期限",
    ]
    assert "**审批要求**" in block.markdown


def test_rejects_non_pdf_magic() -> None:
    try:
        PdfPlumberLayoutParser().parse(b"not a pdf", "fake.pdf")
    except ValueError as exc:
        assert str(exc) == "invalid_pdf_magic"
    else:
        raise AssertionError("invalid PDF must be rejected")


def test_heading_detection_handles_roman_and_letter_headings_not_list_items() -> None:
    assert PdfPlumberLayoutParser._looks_like_heading(
        "II. Regulatory Framework", "II. Regulatory Framework"
    )
    assert PdfPlumberLayoutParser._looks_like_heading("A. FTC Authority", "A. FTC Authority")
    assert not PdfPlumberLayoutParser._looks_like_heading(
        "1. Advertising must be truthful; and", "1. Advertising must be truthful; and"
    )


def test_pdf_safety_rejects_active_javascript() -> None:
    try:
        validate_pdf_safety(b"%PDF-1.7\n/JavaScript /OpenAction")
    except ValueError as exc:
        assert "pdf_active_content_rejected" in str(exc)
    else:
        raise AssertionError("active PDF must be rejected")


def test_rag_chunks_use_token_budget_overlap_and_preserve_structure() -> None:
    text = "".join(chr(0x4E00 + index % 100) for index in range(180))
    document = ParsedDocument(
        "long.pdf",
        "a" * 64,
        "test",
        1,
        "parsed",
        (),
        (DocumentBlock(
            "p1-body", 1, "paragraph", text, text, None,
            ("Chapter 1", "Article 2"),
        ),),
    )

    chunks = rag_chunks(document, max_tokens=80, overlap_tokens=20)

    assert [item["token_count"] for item in chunks] == [80, 80, 60]
    assert chunks[1]["token_start"] == 60
    assert chunks[0]["text"][-20:] == chunks[1]["text"][:20]
    assert all(item["section_path"] == ["Chapter 1", "Article 2"] for item in chunks)
    assert all(item["chunking_strategy"] == "structure_token_window_v1" for item in chunks)


def test_approximate_tokens_keep_latin_terms_and_split_cjk_characters() -> None:
    text = "RAG policy-review 政策"
    spans = approximate_token_spans(text)
    tokens = [text[start:end] for start, end in spans]

    assert tokens == ["RAG", "policy-review", "政", "策"]


def test_rag_chunks_reject_invalid_overlap() -> None:
    parsed = ParsedDocument("x.pdf", "b" * 64, "test", 1, "parsed", (), ())
    try:
        rag_chunks(parsed, max_tokens=100, overlap_tokens=100)
    except ValueError as exc:
        assert str(exc) == "invalid_chunk_token_budget"
    else:
        raise AssertionError("invalid overlap must be rejected")
