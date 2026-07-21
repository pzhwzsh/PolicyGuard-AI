import json

from policyguard.application.query_rewrite import (
    OpenAICompatibleQueryRewriter,
    RewrittenQuery,
    is_cross_language_query,
    query_language,
    should_rewrite,
)


def test_query_rewrite_parser_preserves_original_and_limits_expansions() -> None:
    items = [{"query_id": "q-1", "query": "Can an ad claim best?", "jurisdiction": "CN"}]
    content = json.dumps(
        {
            "rewrites": [
                {
                    "query_id": "q-1",
                    "canonical_query": "广告 最佳 最高级 禁止用语",
                    "alternatives": ["广告法 绝对化用语", "国家级 最佳", "ignored"],
                    "legal_terms": ["最佳", "最高级", "绝对化用语"],
                }
            ]
        }
    )
    rewrite = OpenAICompatibleQueryRewriter.parse(content, items)[0]
    assert rewrite.original_query == "Can an ad claim best?"
    assert len(rewrite.alternatives) == 2
    assert rewrite.retrieval_queries()[0] == rewrite.original_query
    assert rewrite.canonical_query in rewrite.retrieval_queries()


def test_rewrite_gate_targets_cross_language_or_weak_retrieval() -> None:
    assert should_rewrite("Can this ad claim best?", "CN", 0.7) is True
    assert should_rewrite("广告能否使用最佳", "CN", 0.7) is False
    assert should_rewrite("truthful claims", "US", 0.3) is True
    assert should_rewrite("这段广告在美国是否违法", "US", 0.9) is True
    assert should_rewrite("这段广告在欧盟是否违法", "EU", 0.9) is True
    assert should_rewrite("Are these claims truthful?", "US", 0.9) is False


def test_language_detection_is_bidirectional_and_unknown_is_not_forced() -> None:
    assert query_language("美国广告是否允许绝对化宣传") == "zh"
    assert query_language("Are absolute advertising claims allowed?") == "en"
    assert query_language("100%") == "unknown"
    assert is_cross_language_query("美国广告是否允许绝对化宣传", "US") is True
    assert is_cross_language_query("Are absolute claims allowed?", "CN") is True
    assert is_cross_language_query("100%", "US") is False


def test_drift_detector_finds_added_actor_penalty_and_number() -> None:
    rewrite = RewrittenQuery(
        query_id="q-1",
        original_query="Can this claim be used?",
        canonical_query="advertiser liability and fine of 1000 for this claim",
        alternatives=(),
        legal_terms=(),
        jurisdiction="US",
    )
    assert rewrite.added_constraints() == ["advertiser", "fine", "liability", "1000"]
