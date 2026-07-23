"""Download declared official PDFs without treating downloads as ground truth."""

import hashlib
import json
from pathlib import Path

import httpx


def main() -> None:
    root = Path(__file__).parents[3]
    config = json.loads((root / "config/pdf_corpus_sources.json").read_text(encoding="utf-8"))
    target = root / "data/evaluation/pdf/pdfs/real"
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for source in config["sources"]:
            row = {**source, "status": "failed", "error": None}
            try:
                response = client.get(source["url"], headers={"Accept": "application/pdf"})
                content = response.content
                if response.status_code != 200:
                    raise RuntimeError(f"http_{response.status_code}")
                if not content.startswith(b"%PDF-"):
                    raise RuntimeError("response_is_not_pdf")
                path = target / f"{source['id']}.pdf"
                path.write_bytes(content)
                row.update({
                    "status": "downloaded_unlabeled", "path": path.relative_to(root).as_posix(),
                    "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                })
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}:{exc}"
            rows.append(row)
    report = {
        "provenance": "official_publication",
        "label_status": "unlabeled_not_accuracy_evidence",
        "downloaded_count": sum(row["status"] == "downloaded_unlabeled" for row in rows),
        "sources": rows,
    }
    output = root / "data/evaluation/pdf/real-corpus-inventory.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
