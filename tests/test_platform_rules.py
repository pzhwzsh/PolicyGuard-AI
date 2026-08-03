from policyguard.application.platform_rules import evaluate_platform_text


def test_common_absolute_claim_is_checked_without_upload() -> None:
    findings = evaluate_platform_text(
        {"title": "", "description": "本产品100%安全，永久有效"}, "generic"
    )
    assert {item["matched_text"] for item in findings} == {"100%", "永久"}


def test_platform_rules_produce_different_results_for_same_copy() -> None:
    product = {"title": "限时活动", "description": "加微信购买，保证收益"}
    douyin = evaluate_platform_text(product, "douyin")
    taobao = evaluate_platform_text(product, "taobao")
    assert {item["rule_code"] for item in douyin} == {
        "DOUYIN-TRAFFIC", "DOUYIN-INCOME"
    }
    assert taobao == []


def test_amazon_specific_certification_claim() -> None:
    findings = evaluate_platform_text(
        {"title": "FDA approved supplement", "description": ""}, "amazon"
    )
    assert findings[0]["rule_code"] == "AMAZON-CERTIFICATION"
