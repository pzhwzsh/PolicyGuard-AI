import json
import re
from pathlib import Path

from policyguard.application.tools import SuggestConservativeRewriteTool


_FACT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ml|mL|g|kg|mg|件)?")


def evaluate_remediation_dataset(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    tool = SuggestConservativeRewriteTool()
    span_hits = citation_hits = fact_hits = after_hits = residual_hits = 0
    positives = 0
    for sample in dataset["samples"]:
        product = {"title": "", "description": ""}
        product[sample["field"]] = sample["before"]
        evidence = [] if not sample["expected_section_id"] else [{
            "market": "CN", "candidate_evidence": [{
                "section_id": sample["expected_section_id"], "heading": "Article 9",
                "text": "Advertising wording restriction.",
                "source_url": "https://example.test/article-9",
            }],
        }]
        result = tool.execute({"product": product, "evidence": evidence})
        operations = result.output["operations"]
        if sample["expected_phrase"] is None:
            residual_hits += not operations
            continue
        positives += 1
        if not operations:
            continue
        operation = operations[0]
        after_hits += operation["after"] == sample["expected_after"]
        span_hits += any(item["text"] == sample["expected_phrase"]
                         for item in operation["claim_spans"])
        citation_hits += any(item["section_id"] == sample["expected_section_id"]
                             for item in operation["legal_basis"])
        protected = set(_FACT.findall(sample["before"])) - set(
            _FACT.findall(sample["expected_phrase"] or "")
        )
        fact_hits += protected.issubset(
            set(_FACT.findall(operation["after"]))
        )
        residual = tool.execute({"product": {**product, sample["field"]: operation["after"]}})
        residual_hits += not residual.output["operations"]
    total = len(dataset["samples"])
    return {
        "dataset": dataset["name"], "label_status": dataset["label_status"],
        "sample_count": total, "positive_count": positives,
        "exact_after_rate": after_hits / positives,
        "claim_span_accuracy": span_hits / positives,
        "citation_section_accuracy": citation_hits / positives,
        "protected_fact_retention": fact_hits / positives,
        "residual_baseline_pass_rate": residual_hits / total,
        "external_side_effect": False,
        "limitations": dataset["limitations"],
    }
