import pytest

from policyguard.application.guardrails import default_guardrail_policy


def test_guardrails_reject_unknown_jurisdiction_and_tool() -> None:
    policy = default_guardrail_policy()
    with pytest.raises(ValueError, match="unsupported_jurisdictions:CA"):
        policy.validate_jurisdictions(["US", "CA"])
    with pytest.raises(ValueError, match="agent_tool_not_allowed:publish_product"):
        policy.validate_tools([{"name": "publish_product"}])


def test_guardrails_require_evidence_review_and_preserve_numeric_facts() -> None:
    policy = default_guardrail_policy()
    product = {"title": "国家级护肤品 50ml", "description": ""}
    valid = {
        "external_side_effect": False,
        "operations": [{
            "operation": "replace_field", "field": "title",
            "before": product["title"], "after": "护肤品 50ml",
            "legal_basis": [{"section_id": "article-9"}],
            "meaning_preservation": {"requires_human_review": True},
        }],
    }
    policy.validate_remediation_plan(valid, product)
    invalid = {**valid, "operations": [{**valid["operations"][0], "after": "护肤品"}]}
    with pytest.raises(ValueError, match="remediation_protected_fact_removed:50ml"):
        policy.validate_remediation_plan(invalid, product)


def test_guardrails_allow_numeric_token_inside_removed_risky_claim() -> None:
    policy = default_guardrail_policy()
    product = {"title": "国家级护肤品，100%安全，容量500ml", "description": ""}
    operation = {
        "operation": "replace_field",
        "field": "title",
        "before": product["title"],
        "after": "护肤品，容量500ml",
        "removed_phrases": ["国家级", "100%安全"],
        "legal_basis": [{"section_id": "article-9"}],
        "meaning_preservation": {"requires_human_review": True},
    }

    policy.validate_remediation_plan(
        {"external_side_effect": False, "operations": [operation]}, product
    )

    without_verified_spec = {**operation, "after": "护肤品"}
    with pytest.raises(ValueError, match="remediation_protected_fact_removed:500ml"):
        policy.validate_remediation_plan(
            {"external_side_effect": False, "operations": [without_verified_spec]}, product
        )


def test_guardrails_reject_uncited_or_external_rewrite() -> None:
    policy = default_guardrail_policy()
    product = {"title": "国家级产品", "description": ""}
    operation = {
        "operation": "replace_field", "field": "title",
        "before": product["title"], "after": "产品",
        "legal_basis": [], "meaning_preservation": {"requires_human_review": True},
    }
    with pytest.raises(ValueError, match="remediation_legal_basis_required"):
        policy.validate_remediation_plan(
            {"external_side_effect": False, "operations": [operation]}, product
        )
    with pytest.raises(ValueError, match="remediation_external_side_effect_forbidden"):
        policy.validate_remediation_plan(
            {"external_side_effect": True, "operations": [{**operation, "legal_basis": [{}]}]},
            product,
        )
