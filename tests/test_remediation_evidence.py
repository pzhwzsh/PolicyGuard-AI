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


def test_rewrite_removes_guaranteed_effect_claim_and_passes_residual_check() -> None:
    product = {
        "title": "国家级护肤品，100%安全",
        "description": "最好的配方，保证所有人使用后立即见效",
    }
    tool = SuggestConservativeRewriteTool()

    result = tool.execute({"product": product, "evidence": []})
    rewritten = {**product}
    for operation in result.output["operations"]:
        rewritten[operation["field"]] = operation["after"]

    assert rewritten == {"title": "护肤品", "description": "配方"}
    assert "保证所有人使用后立即见效" in {
        phrase
        for operation in result.output["operations"]
        for phrase in operation["removed_phrases"]
    }
    residual = tool.execute({"product": rewritten, "evidence": []})
    assert residual.output["operations"] == []
