import json
from pathlib import Path

from policyguard.application.knowledge import (
    BM25Retriever,
    evaluate_retriever,
    ingest_source_directory,
    tokenize_zh,
)
from policyguard.domain.models import (
    KnowledgeFilter,
    PolicyDocument,
    PolicyScope,
    PolicySection,
)
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def test_hard_evaluation_labels_reference_real_source_sections() -> None:
    root = Path(__file__).parents[1]
    dataset = json.loads((root / "data/evaluation/rag-hard-v1.json").read_text(encoding="utf-8"))
    source_ids = set()
    for path in (root / "data/sources").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_ids.update(section["section_id"] for section in payload["sections"])
    samples = dataset["samples"]
    assert len(samples) == 30
    assert len({(item["query"], item["jurisdiction"]) for item in samples}) == 30
    assert all(item["expected_section_id"] in source_ids for item in samples)


PROJECT_ROOT = Path(__file__).parents[1]


def test_chinese_tokenizer_contains_unigrams_and_bigrams() -> None:
    tokens = tokenize_zh("国家级 product-100")

    assert "国" in tokens
    assert "国家" in tokens
    assert "product" in tokens
    assert "100" in tokens


def test_ingestion_search_and_evaluation(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'knowledge.db').as_posix()}")
    database.initialize()

    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingested = ingest_source_directory(repository, PROJECT_ROOT / "data" / "sources")
        assert ingested == 3
        assert repository.document_count() == 3
        assert len(repository.list_chunks()) == 13

        retriever = BM25Retriever(repository)
        results = retriever.search(
            "广告可以使用国家级吗",
            top_k=3,
            scope=KnowledgeFilter(jurisdiction="CN"),
        )
        assert results[0].chunk.section_id == "article-9"
        assert results[0].chunk.source_url.startswith("https://www.samr.gov.cn/")

        metrics = evaluate_retriever(
            retriever,
            PROJECT_ROOT / "data" / "evaluation" / "rag-baseline.json",
            top_k=5,
        )
        assert metrics.sample_count == 15
        assert metrics.hit_rate_at_k >= 0.8
        assert metrics.mean_reciprocal_rank >= 0.7


def test_new_version_deactivates_current_but_remains_available_historically(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'versions.db').as_posix()}")
    database.initialize()
    scope_v1 = PolicyScope("US", "all", "all", "guidance", "en", "original", "2020-01-01")
    scope_v2 = PolicyScope("US", "all", "all", "guidance", "en", "original", "2024-01-01")
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        repository.activate_document_version(PolicyDocument(
            "v1", "Rule v1", "https://official.test/rule", "Regulator", "v1",
            "2020-01-01", "2020-01-01", "hash1",
            (PolicySection("s1", "Old", "old wording marker"),), (scope_v1,),
        ))
        repository.activate_document_version(PolicyDocument(
            "v2", "Rule v2", "https://official.test/rule", "Regulator", "v2",
            "2024-01-01", "2024-01-01", "hash2",
            (PolicySection("s2", "New", "new wording marker"),), (scope_v2,),
        ))
        current = repository.list_chunks(KnowledgeFilter("US"))
        historical = repository.list_chunks(KnowledgeFilter("US", as_of="2022-01-01"))
    assert [item.section_id for item in current] == ["s2"]
    assert [item.section_id for item in historical] == ["s1"]
