from datetime import UTC, datetime
from uuid import uuid4

from policyguard.application.ports import ComplianceRepository
from policyguard.domain.models import (
    SEVERITY_SCORE,
    CheckResult,
    ComplianceRule,
    Finding,
    Product,
)


class ComplianceCheckService:
    """V0 baseline: deterministic matching with no model calls."""

    def __init__(self, repository: ComplianceRepository) -> None:
        self.repository = repository

    def execute(self, product: Product) -> CheckResult:
        findings = tuple(
            finding
            for rule in self.repository.list_active_rules()
            if (finding := self._apply_rule(product, rule)) is not None
        )
        risk_level = max(
            (finding.severity for finding in findings),
            key=SEVERITY_SCORE.__getitem__,
            default=None,
        )
        result = CheckResult(
            id=str(uuid4()),
            external_id=product.external_id,
            status="review_required" if findings else "passed",
            risk_level=risk_level,
            findings=findings,
            created_at=datetime.now(UTC),
        )
        return self.repository.save_check(product, result)

    @staticmethod
    def _apply_rule(product: Product, rule: ComplianceRule) -> Finding | None:
        value = ComplianceCheckService._field_value(product, rule.target_field)
        if rule.pattern.casefold() not in value.casefold():
            return None
        return Finding(
            rule_code=rule.code,
            rule_version=rule.version,
            severity=rule.severity,
            field_name=rule.target_field,
            matched_text=rule.pattern,
            evidence=f"字段 {rule.target_field} 命中规则关键词：{rule.pattern}",
            suggestion=rule.suggestion,
            source_url=rule.source_url,
        )

    @staticmethod
    def _field_value(product: Product, field_name: str) -> str:
        if field_name == "title":
            return product.title
        if field_name == "description":
            return product.description
        if field_name == "category":
            return product.category
        if field_name.startswith("attributes."):
            key = field_name.removeprefix("attributes.")
            return str(product.attributes.get(key, ""))
        return ""

