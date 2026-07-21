"""Ground-truth PDF extraction metrics; runtime confidence is not treated as accuracy."""

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from policyguard.application.document_ingestion import ParsedDocument


@dataclass(frozen=True, slots=True)
class DocumentQualityReport:
    text_accuracy: float
    heading_f1: float
    table_cell_accuracy: float | None
    reading_order_accuracy: float
    page_latency_ms: float
    manual_correction_rate: float

    def as_dict(self) -> dict:
        return asdict(self)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _f1(expected: set[str], actual: set[str]) -> float:
    if not expected and not actual:
        return 1.0
    if not expected or not actual:
        return 0.0
    overlap = len(expected & actual)
    precision = overlap / len(actual)
    recall = overlap / len(expected)
    return 2 * precision * recall / (precision + recall) if overlap else 0.0


def _table_cells(document: ParsedDocument) -> list[str]:
    return [
        _normalized(str(cell))
        for block in document.blocks
        if block.block_type == "table"
        for row in block.metadata.get("rows", [])
        for cell in row
    ]


def evaluate_document_extraction(
    expected: ParsedDocument,
    actual: ParsedDocument,
    *,
    elapsed_ms: float,
    corrected_block_ids: set[str] | None = None,
) -> DocumentQualityReport:
    expected_text = _normalized("\n".join(block.text for block in expected.blocks))
    actual_text = _normalized("\n".join(block.text for block in actual.blocks))
    expected_headings = {
        _normalized(block.text) for block in expected.blocks if block.block_type == "heading"
    }
    actual_headings = {
        _normalized(block.text) for block in actual.blocks if block.block_type == "heading"
    }
    expected_cells = _table_cells(expected)
    actual_cells = _table_cells(actual)
    table_accuracy = (
        SequenceMatcher(None, expected_cells, actual_cells).ratio()
        if expected_cells or actual_cells
        else None
    )
    actual_stream = _normalized("\n".join(block.text for block in actual.blocks))
    expected_positions = []
    for block in expected.blocks:
        anchor = _normalized(block.text)[:80]
        position = actual_stream.find(anchor)
        if position >= 0:
            expected_positions.append(position)
    ordered_pairs = 0
    correct_pairs = 0
    for left_index, left in enumerate(expected_positions):
        for right in expected_positions[left_index + 1 :]:
            ordered_pairs += 1
            correct_pairs += left < right
    order_accuracy = (
        correct_pairs / ordered_pairs
        if ordered_pairs
        else float(len(expected_positions) == len(expected.blocks) <= 1)
    )
    corrections = corrected_block_ids or set()
    return DocumentQualityReport(
        text_accuracy=round(SequenceMatcher(None, expected_text, actual_text).ratio(), 4),
        heading_f1=round(_f1(expected_headings, actual_headings), 4),
        table_cell_accuracy=round(table_accuracy, 4) if table_accuracy is not None else None,
        reading_order_accuracy=round(order_accuracy, 4),
        page_latency_ms=round(elapsed_ms / max(actual.page_count, 1), 2),
        manual_correction_rate=round(len(corrections) / max(len(expected.blocks), 1), 4),
    )
