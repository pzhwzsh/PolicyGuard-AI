import json
from dataclasses import asdict
from pathlib import Path

from policyguard.application.abstention_calibration import calibrate_abstention
from policyguard.application.embeddings import DenseRetriever
from policyguard.application.knowledge import ingest_source_directory
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.scripts.benchmark_cross_language import CachedEmbeddingProvider
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    root = Path(__file__).parents[3]
    positives_payload = json.loads(
        (root / "data/evaluation/rag-cross-lingual-zh-en-v1.json").read_text(encoding="utf-8")
    )
    positives = [item for item in positives_payload["samples"] if item["answerable"]]
    negative_payload = json.loads(
        (root / "data/evaluation/rag-cross-lingual-no-answer-near-v1.json")
        .read_text(encoding="utf-8")
    )
    negatives = negative_payload["samples"]
    database = Database("sqlite:///./data/policyguard.db")
    database.initialize()
    rows = []
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, root / "data/sources")
        provider = CachedEmbeddingProvider(
            FastEmbedProvider("jinaai/jina-embeddings-v2-base-zh")
        )
        provider.embed([item["query"] for item in [*positives, *negatives]])
        retriever = DenseRetriever(repository, provider)
        for sample in [*positives, *negatives]:
            results = retriever.search(
                sample["query"], 5,
                KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
            )
            rows.append({
                "sample_id": sample["id"],
                "answerable": sample["answerable"],
                "top_score": results[0].score if results else 0.0,
                "expected_in_top_k": any(
                    item.chunk.section_id == sample.get("expected_section_id")
                    for item in results
                ) if sample["answerable"] else False,
            })
    thresholds = sorted({0.0, 1.0, *(row["top_score"] for row in rows)})
    report = calibrate_abstention(rows, thresholds)
    output = {
        "model": "jinaai/jina-embeddings-v2-base-zh",
        "positive_dataset_status": positives_payload["label_status"],
        "negative_dataset_status": negative_payload["label_status"],
        "positive_count": len(positives), "negative_count": len(negatives),
        "best": asdict(report["best"]),
        "points": [asdict(item) for item in report["points"]],
        "development_only": True,
        "warning": "Do not deploy this threshold before independent human-verified validation.",
    }
    target = root / "data/benchmarks/cross-language-abstention-v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
