from pathlib import Path

from policyguard.application.cross_language_evaluation import (
    load_cross_language_dataset,
    measure_retriever,
)
from policyguard.scripts.benchmark_cross_language import CachedEmbeddingProvider
from policyguard.domain.models import PolicyChunk, SearchHit


ROOT = Path(__file__).parents[1]


class ExpectedRetriever:
    def search(self, query, top_k, scope):
        section = "ftc-truth-evidence" if scope.jurisdiction == "US" else "eu-ucpd-article-6"
        return [SearchHit(PolicyChunk(
            id="chunk", document_id="doc", document_title="title",
            section_id=section, heading="heading", text="text",
            source_url="https://example.test", jurisdiction=scope.jurisdiction,
        ), score=1.0, rank=1)]


def test_cross_language_dataset_is_ai_audited_but_pending_human_review() -> None:
    dataset = load_cross_language_dataset(
        ROOT / "data/evaluation/rag-cross-lingual-zh-en-v1.json"
    )
    assert dataset["label_status"] == "ai_reviewed_pending_human_verification"
    assert dataset["ai_source_audit"]["reviewed_samples"] == 22
    assert dataset["ai_source_audit"]["revised_for_source_fidelity"] == 2
    assert dataset["ai_source_audit"]["human_verified_samples"] == 0
    assert len(dataset["samples"]) == 22
    assert all(item["review_status"] == "pending_human_review" for item in dataset["samples"])


def test_measurement_reports_real_hits_and_failures() -> None:
    samples = [
        {"query": "美国广告是否真实", "jurisdiction": "US", "answerable": True,
         "expected_section_id": "ftc-truth-evidence"},
        {"query": "欧盟虚假功效", "jurisdiction": "EU", "answerable": True,
         "expected_section_id": "eu-ucpd-article-6"},
        {"query": "罚款多少", "jurisdiction": "EU", "answerable": False,
         "expected_section_id": None},
    ]
    result = measure_retriever("expected", ExpectedRetriever(), samples)
    assert result.sample_count == 3
    assert result.answerable_count == 2
    assert result.hit_rate_at_5 == 1.0
    assert result.mrr == 1.0
    assert result.failures == 0
    assert result.no_answer_count == 1
    assert result.no_answer_candidate_presence_rate == 1.0


def test_cached_embedding_provider_batches_and_reuses_queries() -> None:
    class Provider:
        provider_name = "fake"
        model_name = "fake-model"

        def __init__(self):
            self.calls = []

        def embed(self, texts):
            self.calls.append(texts)
            return [[float(len(text))] for text in texts]

    provider = Provider()
    cached = CachedEmbeddingProvider(provider)
    assert cached.embed(["one", "two", "one"]) == [[3.0], [3.0], [3.0]]
    assert cached.embed(["two", "three"]) == [[3.0], [5.0]]
    assert provider.calls == [["one", "two"], ["three"]]
