from policyguard.application.service import ComplianceCheckService
from policyguard.domain.models import CheckResult, ComplianceRule, Product, Severity


class MemoryRepository:
    def __init__(self, rules: list[ComplianceRule]) -> None:
        self.rules = rules
        self.saved: CheckResult | None = None

    def list_active_rules(self) -> list[ComplianceRule]:
        return self.rules

    def save_check(self, product: Product, result: CheckResult) -> CheckResult:
        self.saved = result
        return result

    def get_check(self, check_id: str) -> CheckResult | None:
        return self.saved if self.saved and self.saved.id == check_id else None


def demo_rule() -> ComplianceRule:
    return ComplianceRule(
        code="TEST-001",
        version="1.0.0",
        title="test",
        pattern="100%安全",
        target_field="title",
        severity=Severity.HIGH,
        suggestion="remove claim",
        source_url=None,
        is_demo=True,
    )


def test_detects_matching_rule() -> None:
    repository = MemoryRepository([demo_rule()])
    product = Product(
        external_id="sku-1",
        title="这是一款100%安全的商品",
        description="",
        category="demo",
    )

    result = ComplianceCheckService(repository).execute(product)

    assert result.status == "review_required"
    assert result.risk_level == Severity.HIGH
    assert result.findings[0].rule_code == "TEST-001"


def test_passes_when_no_rule_matches() -> None:
    repository = MemoryRepository([demo_rule()])
    product = Product(
        external_id="sku-2",
        title="普通商品",
        description="",
        category="demo",
    )

    result = ComplianceCheckService(repository).execute(product)

    assert result.status == "passed"
    assert result.risk_level is None
    assert result.findings == ()

