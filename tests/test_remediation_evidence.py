from policyguard.application.tools import SuggestConservativeRewriteTool


def test_rewrite_is_minimal_cited_and_non_mutating() -> None:
    product = {"title": "国家级护肤品 50ml", "description": "适合日常使用"}
    result = SuggestConservativeRewriteTool().execute({
        "product": product,
        "evidence": [{
            "market": "CN",
            "candidate_evidence": [{
                "section_id": "article-9",
                "heading": "Article 9",
                "text": "Advertising shall not use national-level wording.",
                "source_url": "https://example.test/article-9",
            }],
        }],
    })
    operation = result.output["operations"][0]
    assert operation["before"] == "国家级护肤品 50ml"
    assert operation["after"] == "护肤品 50ml"
    assert operation["claim_spans"] == [{"text": "国家级", "start": 0, "end": 3}]
    assert operation["legal_basis"][0]["section_id"] == "article-9"
    assert operation["meaning_preservation"]["strategy"] == "minimal_lexical_deletion"
    assert product["title"] == "国家级护肤品 50ml"
    assert result.output["external_side_effect"] is False
