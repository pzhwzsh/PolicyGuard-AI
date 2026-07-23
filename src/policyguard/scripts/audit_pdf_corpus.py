import json
from pathlib import Path

from policyguard.application.document_corpus import audit_pdf_manifest


def main() -> None:
    root = Path(__file__).parents[3]
    report = audit_pdf_manifest(root / "data/evaluation/pdf/manifest.json")
    target = root / "data/benchmarks/pdf-corpus-audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
