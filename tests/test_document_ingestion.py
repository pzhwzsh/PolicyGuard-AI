import io

from pypdf import PdfWriter

from policyguard.application.document_ingestion import (
    PdfPlumberLayoutParser,
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
