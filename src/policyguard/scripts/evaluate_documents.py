import json
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean
from time import perf_counter

from policyguard.application.document_ingestion import (
    PdfPlumberLayoutParser,
    parsed_document_from_json,
)
from policyguard.application.document_quality import evaluate_document_extraction


def main() -> None:
    root = Path(__file__).parents[3]
    dataset = root / "data/evaluation/pdf/manifest.json"
    if not dataset.exists():
        print("SKIPPED: no labeled PDF manifest at data/evaluation/pdf/manifest.json")
        return
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    reports = []
    for sample in payload.get("samples", []):
        expected = parsed_document_from_json(
            json.loads((dataset.parent / sample["expected_json"]).read_text(encoding="utf-8"))
        )
        actual_path = dataset.parent / sample["actual_json"]
        if sample.get("regenerate_actual") or not actual_path.exists():
            started = perf_counter()
            actual = PdfPlumberLayoutParser().parse(
                (dataset.parent / sample["pdf"]).read_bytes(), Path(sample["pdf"]).name
            )
            sample["elapsed_ms"] = (perf_counter() - started) * 1000
            actual_path.parent.mkdir(parents=True, exist_ok=True)
            actual_path.write_text(
                json.dumps(actual.canonical_json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            actual = parsed_document_from_json(
                json.loads(actual_path.read_text(encoding="utf-8"))
            )
        pages = set(sample.get("pages", []))
        if pages:
            expected = replace(
                expected,
                page_count=len(pages),
                blocks=tuple(block for block in expected.blocks if block.page in pages),
            )
            actual = replace(
                actual,
                blocks=tuple(block for block in actual.blocks if block.page in pages),
            )
        report = evaluate_document_extraction(
            expected,
            actual,
            elapsed_ms=float(sample["elapsed_ms"]),
            corrected_block_ids=set(sample.get("corrected_block_ids", [])),
        )
        reports.append({"sample_id": sample["sample_id"], **asdict(report)})
    if not reports:
        print("SKIPPED: PDF manifest contains no labeled samples")
        return
    numeric = [key for key in reports[0] if key != "sample_id"]
    aggregate = {
        key: round(mean(item[key] for item in reports if item[key] is not None), 4)
        for key in numeric
        if any(item[key] is not None for item in reports)
    }
    output = {"sample_count": len(reports), "aggregate": aggregate, "samples": reports}
    target = root / "data/benchmarks/pdf-quality.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
