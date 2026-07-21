from policyguard.application.document_ingestion import DocumentBlock, ParsedDocument
from policyguard.application.document_quality import evaluate_document_extraction


def document(blocks: tuple[DocumentBlock, ...]) -> ParsedDocument:
    return ParsedDocument("x.pdf", "hash", "test", 2, "parsed", (), blocks)


def block(block_id: str, block_type: str, text: str, rows=None) -> DocumentBlock:
    return DocumentBlock(
        block_id, 1, block_type, text, text, None, metadata={"rows": rows or []}
    )


def test_document_quality_uses_ground_truth_and_reports_each_dimension() -> None:
    expected = document(
        (
            block("h1", "heading", "Article One"),
            block("p1", "paragraph", "Advertising must be truthful."),
            block("t1", "table", "A B", [["A", "B"], ["1", "2"]]),
        )
    )
    actual = document(
        (
            block("p1", "paragraph", "Advertising must be truthful."),
            block("h1", "heading", "Article One"),
            block("t1", "table", "A B", [["A", "B"], ["1", "wrong"]]),
        )
    )
    report = evaluate_document_extraction(
        expected, actual, elapsed_ms=100, corrected_block_ids={"t1"}
    )
    assert report.heading_f1 == 1.0
    assert report.table_cell_accuracy == 0.75
    assert report.reading_order_accuracy < 1.0
    assert report.page_latency_ms == 50.0
    assert report.manual_correction_rate == 0.3333
