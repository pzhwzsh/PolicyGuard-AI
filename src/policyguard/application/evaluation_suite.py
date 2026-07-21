import json
from dataclasses import asdict
from pathlib import Path

from policyguard.application.knowledge import evaluate_retriever
from policyguard.domain.models import KnowledgeFilter


def load_evaluation_suite(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    seen = set()
    for entry in manifest["datasets"]:
        dataset = json.loads((path.parent / entry["path"]).read_text(encoding="utf-8"))
        for sample in dataset["samples"]:
            key = (sample["query"], sample["jurisdiction"])
            if key in seen:
                raise ValueError("evaluation_suite_duplicate_query")
            seen.add(key)
            rows.append({**sample, "slice": entry["slice"], "answerable": entry["answerable"]})
    return {"manifest": manifest, "samples": rows}


def evaluate_suite(retriever, path: Path, top_k: int = 5) -> dict:
    suite = load_evaluation_suite(path)
    positives = {}
    for entry in suite["manifest"]["datasets"]:
        if entry["answerable"]:
            positives[entry["slice"]] = asdict(
                evaluate_retriever(retriever, path.parent / entry["path"], top_k=top_k)
            )
    negatives = {}
    for slice_name in ("negative-far", "negative-near"):
        rows = [item for item in suite["samples"] if item["slice"] == slice_name]
        top_scores = []
        candidate_count = 0
        for sample in rows:
            hits = retriever.search(
                sample["query"], top_k=top_k,
                scope=KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
            )
            if hits:
                candidate_count += 1
                top_scores.append(hits[0].score)
        negatives[slice_name] = {
            "sample_count": len(rows),
            "candidate_presence_rate": round(candidate_count / max(len(rows), 1), 4),
            "mean_top_score": round(sum(top_scores) / max(len(top_scores), 1), 6),
            "note": "Candidate presence is diagnostic only; it is not a no-answer decision.",
        }
    return {
        "sample_count": len(suite["samples"]),
        "answerable_count": sum(item["answerable"] for item in suite["samples"]),
        "no_answer_count": sum(not item["answerable"] for item in suite["samples"]),
        "positive_metrics": positives,
        "negative_diagnostics": negatives,
        "limitations": suite["manifest"]["limitations"],
    }
