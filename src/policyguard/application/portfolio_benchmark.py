import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean
from time import perf_counter

from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.remediation_evaluation import evaluate_remediation_dataset
from policyguard.application.workflow import ComplianceWorkflowService
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Base, Database
from policyguard.infrastructure.repositories import (
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyWorkflowRepository,
)

RISKY = (
    ("国家级", "article-9"),
    ("最高级", "article-9"),
    ("最好的", "article-9"),
    ("最佳", "article-9"),
    ("100%安全的", "article-4"),
)
PRODUCTS = ("护肤品", "面霜", "食品", "儿童用品", "清洁剂")
FACTS = ("50ml", "30g", "100ml", "2件", "5mg")


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1)
    return round(ordered[index], 3)


def build_remediation_dataset() -> dict:
    samples = []
    for index in range(100):
        phrase, section = RISKY[index % len(RISKY)]
        product = PRODUCTS[index % len(PRODUCTS)]
        fact = FACTS[index % len(FACTS)]
        field = "title" if index % 2 == 0 else "description"
        context = f"系列{index + 1:03d}"
        before = f"{phrase}{product} {fact} {context}"
        samples.append({
            "id": f"remediation-{index + 1:03d}",
            "field": field,
            "before": before,
            "expected_after": f"{product} {fact} {context}",
            "expected_phrase": phrase,
            "expected_section_id": section,
            "review_status": "pending_human_review",
        })
    return {
        "name": "portfolio-remediation-100-v1",
        "label_status": "synthetic_pending_human_review",
        "generation": "deterministic template expansion; not production traffic",
        "limitations": "Measures the bounded phrase-removal baseline only.",
        "samples": samples,
    }


def build_rag_holdout() -> dict:
    positives = [
        ("CN", "article-9", "中国广告能否使用{term}等绝对化用语？"),
        ("CN", "article-11", "引用{term}调查数据时需要说明来源和有效期吗？"),
        ("CN", "article-16", "医疗广告是否可以承诺{term}治疗效果？"),
        ("CN", "article-18", "保健食品能否宣称{term}疾病？"),
        ("CN", "article-24", "教育培训广告能否保证{term}考试？"),
        ("CN", "article-25", "投资广告能否保证{term}收益？"),
        ("US", "ftc-truth-evidence", "What evidence is needed for an ad claiming {term}?"),
        ("US", "ftc-specialized-products", "Should {term} advertising check specialized rules?"),
        ("EU", "eu-ucpd-article-6", "Can false information about {term} mislead a buyer?"),
        ("EU", "eu-ucpd-article-7", "May material {term} information be hidden or delayed?"),
    ]
    terms = ("best", "price", "health", "origin", "results", "risk")
    samples = []
    for index in range(60):
        market, section, template = positives[index % len(positives)]
        samples.append({
            "id": f"holdout-pos-{index + 1:03d}",
            "query": (
                f"Campaign scenario {index + 1}: "
                f"{template.format(term=terms[index % len(terms)])}"
            ),
            "jurisdiction": market,
            "answerable": True,
            "expected_section_id": section,
            "review_status": "pending_human_review",
        })
    negative_templates = (
        "What is the prison sentence for {topic}?",
        "Which tax form applies to {topic}?",
        "What trademark filing fee covers {topic}?",
        "Which customs tariff code applies to {topic}?",
        "What employment contract rule governs {topic}?",
    )
    for index in range(60):
        market = ("CN", "US", "EU")[index % 3]
        samples.append({
            "id": f"holdout-neg-{index + 1:03d}",
            "query": (
                f"Out-of-scope scenario {index + 1}: "
                + negative_templates[index % len(negative_templates)].format(
                    topic=PRODUCTS[index % len(PRODUCTS)]
                )
            ),
            "jurisdiction": market,
            "answerable": False,
            "expected_section_id": None,
            "review_status": "pending_human_review",
        })
    return {
        "name": "portfolio-rag-isolated-120-v1",
        "label_status": "synthetic_pending_human_review",
        "split": "synthetic_stress_set_not_a_legal_holdout",
        "generation": "deterministic templates grounded in active section identifiers",
        "intended_use": "retrieval plumbing and load regression only",
        "legal_quality_metric_eligible": False,
        "limitations": (
            "Template-expanded wording is not independent legal annotation and must not be "
            "used to claim legal retrieval quality."
        ),
        "samples": samples,
    }


def evaluate_rag_holdout(root: Path, dataset: dict) -> dict:
    database = Database("sqlite:///:memory:")
    database.initialize()
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, root / "data/sources")
        retriever = BM25Retriever(repository)
        positive_hits = 0
        negative_candidates = 0
        latencies = []
        predictions = []
        for sample in dataset["samples"]:
            started = perf_counter()
            hits = retriever.search(
                sample["query"], top_k=5,
                scope=KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
            )
            latencies.append((perf_counter() - started) * 1000)
            if sample["answerable"]:
                matched = any(
                    hit.chunk.section_id == sample["expected_section_id"] for hit in hits
                )
                positive_hits += matched
            else:
                matched = None
                negative_candidates += bool(hits)
            predictions.append({
                "id": sample["id"],
                "expected_section_id": sample["expected_section_id"],
                "retrieved_section_ids": [hit.chunk.section_id for hit in hits],
                "expected_hit": matched,
                "candidate_present": bool(hits),
            })
    positives = sum(item["answerable"] for item in dataset["samples"])
    negatives = len(dataset["samples"]) - positives
    return {
        "dataset": dataset["name"],
        "label_status": dataset["label_status"],
        "legal_quality_metric_eligible": dataset.get("legal_quality_metric_eligible", False),
        "metric_status": "synthetic_stress_only_not_legal_quality_evidence",
        "sample_count": len(dataset["samples"]),
        "positive_count": positives,
        "negative_count": negatives,
        "positive_hit_at_5": round(positive_hits / positives, 4),
        "negative_candidate_presence_rate": round(negative_candidates / negatives, 4),
        "mean_latency_ms": round(mean(latencies), 3),
        "p95_latency_ms": percentile(latencies, 0.95),
        "answerability_threshold_applied": False,
        "predictions": predictions,
    }


def build_marketing_cases() -> list[dict]:
    markets = (("CN",), ("US",), ("EU",), ("CN", "US", "EU"))
    rows = []
    for index in range(100):
        phrase, _ = RISKY[index % len(RISKY)]
        rows.append({
            "case_id": f"campaign-{index + 1:03d}",
            "source_type": "synthetic_public_template",
            "product": {
                "external_id": f"PORTFOLIO-{index + 1:03d}",
                "title": f"{phrase}{PRODUCTS[index % len(PRODUCTS)]} {FACTS[index % len(FACTS)]}",
                "description": f"跨境营销开发样本 {index + 1}",
                "category": "all",
                "attributes": {},
            },
            "markets": list(markets[index % len(markets)]),
            "review_status": "pending_human_review",
        })
    return rows


def evaluate_workflow_cases(root: Path, cases: list[dict]) -> dict:
    database = Database("sqlite:///:memory:")
    database.initialize()
    latencies = []
    statuses: dict[str, int] = {}
    evidence_cases = 0
    event_count = 0
    outcomes = []
    with database.session_factory() as session:
        knowledge = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(knowledge, root / "data/sources")
        service = ComplianceWorkflowService(
            knowledge, SqlAlchemyWorkflowRepository(session)
        )
        for case in cases:
            started = perf_counter()
            run = service.execute(
                product=case["product"], markets=case["markets"],
                category="all", channel="all", as_of=None,
            )
            latencies.append((perf_counter() - started) * 1000)
            statuses[run.status.value] = statuses.get(run.status.value, 0) + 1
            evidence_cases += any(
                item["candidate_evidence"] for item in run.result_payload.get("markets", [])
            )
            event_count += len(run.events)
            outcomes.append({
                "case_id": case["case_id"],
                "markets": case["markets"],
                "status": run.status.value,
                "event_count": len(run.events),
                "evidence_markets": [
                    item["market"] for item in run.result_payload.get("markets", [])
                    if item["candidate_evidence"]
                ],
                "latency_ms": round(latencies[-1], 3),
            })
    elapsed = sum(latencies) / 1000
    return {
        "dataset": "portfolio-campaign-100-v1",
        "traffic_type": "synthetic_public_template_not_production",
        "case_count": len(cases),
        "statuses": statuses,
        "evidence_coverage": round(evidence_cases / len(cases), 4),
        "event_count": event_count,
        "mean_latency_ms": round(mean(latencies), 3),
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "throughput_cases_per_second": round(len(cases) / max(elapsed, 0.000001), 2),
        "model_tokens": 0,
        "cost_usd": 0.0,
        "cost_scope": "deterministic local baseline only",
        "outcomes": outcomes,
    }


def evaluate_concurrent_workflows(
    root: Path,
    cases: list[dict],
    workers: int = 8,
    *,
    database_url: str | None = None,
    backend: str = "sqlite",
) -> dict:
    database_path = root / "tmp/portfolio-concurrency.db"
    if database_url is None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        database_path.unlink(missing_ok=True)
        database_url = f"sqlite:///{database_path.as_posix()}"
    elif "benchmark" not in database_url.lower():
        raise ValueError("benchmark_database_url_must_contain_benchmark")
    database = Database(database_url)
    if backend != "sqlite":
        Base.metadata.drop_all(database.engine)
    database.initialize()
    with database.session_factory() as session:
        ingest_source_directory(SqlAlchemyKnowledgeRepository(session), root / "data/sources")

    def execute(case: dict) -> tuple[float, str, bool]:
        started = perf_counter()
        with database.session_factory() as session:
            run = ComplianceWorkflowService(
                SqlAlchemyKnowledgeRepository(session),
                SqlAlchemyWorkflowRepository(session),
            ).execute(
                product=case["product"], markets=case["markets"],
                category="all", channel="all", as_of=None,
            )
        latency = (perf_counter() - started) * 1000
        return latency, run.status.value, run.status.value != "failed"

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(execute, cases))
    elapsed = perf_counter() - started
    latencies = [item[0] for item in results]
    database.engine.dispose()
    if backend == "sqlite":
        database_path.unlink(missing_ok=True)
    return {
        "case_count": len(cases),
        "workers": workers,
        "success_rate": round(sum(item[2] for item in results) / len(results), 4),
        "wall_time_seconds": round(elapsed, 3),
        "throughput_cases_per_second": round(len(cases) / elapsed, 2),
        "mean_latency_ms": round(mean(latencies), 3),
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "p99_latency_ms": percentile(latencies, 0.99),
        "model_tokens": 0,
        "cost_usd": 0.0,
        "backend": backend,
        "scope": (
            f"local {backend} deterministic workflow; excludes network and model latency"
        ),
    }


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_human_review_packet(root: Path, rag: dict, remediation: dict, campaigns: list) -> dict:
    source_inventory = json.loads(
        (root / "data/evidence/v1/source-inventory.json").read_text(encoding="utf-8")
    )
    legal_items = [
        {
            "source_id": item["source_id"],
            "official_url": item["source_url"],
            "content_hash": item["content_hash"],
            "section_count": item["section_count"],
            "structural_review_status": item["structural_review_status"],
            "legal_review_status": item["legal_review_status"],
            "decision": None,
            "reviewer_alias": None,
            "reviewed_at": None,
            "comment": "",
        }
        for item in source_inventory["sources"]
        if item.get("eligible_for_activation")
    ]
    return {
        "schema_version": "policyguard-human-review-packet-v1",
        "status": "pending_real_human_review",
        "automatic_completion": False,
        "counts": {
            "legal_sources": len(legal_items),
            "rag_samples": len(rag["samples"]),
            "remediation_samples": len(remediation["samples"]),
            "campaign_samples": len(campaigns),
            "completed_decisions": 0,
        },
        "instructions": [
            "Verify each expected section against the exact official source text.",
            "Reject ambiguous or scope-dependent labels instead of forcing acceptance.",
            "For remediation, verify meaning preservation and retained product facts.",
            "A legal source may be activated only after an authorized reviewer confirms it.",
        ],
        "legal_source_reviews": legal_items,
        "dataset_reviews": [
            {
                "dataset": rag["name"], "sample_id": item["id"],
                "decision": None, "corrected_section_id": None, "comment": "",
            }
            for item in rag["samples"]
        ] + [
            {
                "dataset": remediation["name"], "sample_id": item["id"],
                "decision": None, "meaning_preserved": None, "comment": "",
            }
            for item in remediation["samples"]
        ] + [
            {
                "dataset": "portfolio-campaign-100-v1", "sample_id": item["case_id"],
                "decision": None, "comment": "",
            }
            for item in campaigns
        ],
    }


def run_portfolio_benchmark(root: Path) -> dict:
    rag = build_rag_holdout()
    remediation = build_remediation_dataset()
    campaigns = build_marketing_cases()
    write_json(root / "data/evaluation/portfolio-rag-isolated-120-v1.json", rag)
    write_json(root / "data/evaluation/portfolio-remediation-100-v1.json", remediation)
    write_json(root / "data/evaluation/portfolio-campaign-100-v1.json", {
        "name": "portfolio-campaign-100-v1",
        "label_status": "synthetic_pending_human_review",
        "samples": campaigns,
    })
    write_json(
        root / "data/evaluation/human-review-packet-v1.json",
        build_human_review_packet(root, rag, remediation, campaigns),
    )
    report = {
        "rag": evaluate_rag_holdout(root, rag),
        "remediation": evaluate_remediation_dataset(
            root / "data/evaluation/portfolio-remediation-100-v1.json"
        ),
        "workflow": evaluate_workflow_cases(root, campaigns),
        "concurrency": evaluate_concurrent_workflows(root, campaigns),
    }
    write_json(root / "data/benchmarks/portfolio-scale-v1.json", report)
    return report
