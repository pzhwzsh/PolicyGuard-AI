import json
from datetime import UTC, datetime
from pathlib import Path


def variants(query: str) -> list[tuple[str, str]]:
    chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
    if chinese:
        return [
            (query, "original"),
            (f"请问，{query.rstrip('？?')}？", "polite_wrapper"),
            (f"跨境电商商品页面中，{query.rstrip('？?')}？", "commerce_context"),
            (query.replace("，", " ").replace("？", ""), "punctuation_removed"),
        ]
    return [
        (query, "original"),
        (f"For an ecommerce product page, {query[0].lower() + query[1:]}", "commerce_context"),
        (f"Please answer this advertising-law question: {query}", "instruction_wrapper"),
        (query.replace("?", "").replace(",", ""), "punctuation_removed"),
    ]


def main() -> None:
    root = Path(__file__).parents[3]
    samples = []
    for filename in ("rag-baseline.json", "rag-hard-v1.json"):
        dataset = json.loads(
            (root / "data/evaluation" / filename).read_text(encoding="utf-8")
        )
        for index, sample in enumerate(dataset["samples"]):
            for variant_index, (query, transformation) in enumerate(variants(sample["query"])):
                samples.append({
                    **sample,
                    "query": query,
                    "sample_id": f"{filename.removesuffix('.json')}-{index}-{variant_index}",
                    "source_dataset": filename,
                    "transformation": transformation,
                    "synthetic": transformation != "original",
                })
    output = {
        "name": "rag-retrieval-robustness-v1",
        "created_at": datetime.now(UTC).date().isoformat(),
        "labeling": (
            "Deterministic variants derived from 45 human-authored positive questions. "
            "Use only for retrieval robustness, never as independent human labels."
        ),
        "sample_count": len(samples),
        "samples": samples,
    }
    target = root / "data/evaluation/rag-robustness-v1.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(samples)} robustness samples to {target}")


if __name__ == "__main__":
    main()
