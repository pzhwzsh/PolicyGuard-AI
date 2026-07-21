from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from policyguard.application.cross_language_evaluation import load_cross_language_dataset
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import EvaluationReviewRecord


class EvaluationReviewService:
    def __init__(self, session: Session, knowledge_repository, dataset_path: Path) -> None:
        self.session = session
        self.knowledge = knowledge_repository
        self.dataset_path = dataset_path

    def list_items(self) -> list[dict]:
        dataset = load_cross_language_dataset(self.dataset_path)
        records = self.session.scalars(
            select(EvaluationReviewRecord).where(
                EvaluationReviewRecord.dataset == dataset["name"]
            )
        ).all()
        by_sample = {record.sample_id: record for record in records}
        return [self._item(dataset["name"], sample, by_sample.get(sample["id"]))
                for sample in dataset["samples"]]

    def review(
        self, sample_id: str, decision: str, reviewer: str,
        expected_section_id: str | None, comment: str,
    ) -> dict:
        dataset = load_cross_language_dataset(self.dataset_path)
        sample = next((item for item in dataset["samples"] if item["id"] == sample_id), None)
        if sample is None:
            raise LookupError("evaluation_sample_not_found")
        selected = expected_section_id if decision == "correct" else sample["expected_section_id"]
        if decision == "reject":
            selected = None
        if decision in {"accept", "correct"} and sample["answerable"]:
            if not selected:
                raise ValueError("evaluation_expected_section_required")
            section_ids = {
                chunk.section_id for chunk in self.knowledge.list_chunks(
                    KnowledgeFilter(jurisdiction=sample["jurisdiction"])
                )
            }
            if selected not in section_ids:
                raise ValueError("evaluation_expected_section_not_active")
        record = self.session.scalar(select(EvaluationReviewRecord).where(
            EvaluationReviewRecord.dataset == dataset["name"],
            EvaluationReviewRecord.sample_id == sample_id,
        ))
        if record is None:
            record = EvaluationReviewRecord(dataset=dataset["name"], sample_id=sample_id)
            self.session.add(record)
        record.decision = decision
        record.expected_section_id = selected
        record.reviewer = reviewer
        record.comment = comment
        record.reviewed_at = datetime.now(UTC)
        self.session.commit()
        return self._item(dataset["name"], sample, record)

    @staticmethod
    def _item(dataset: str, sample: dict, record: EvaluationReviewRecord | None) -> dict:
        return {
            "dataset": dataset,
            "sample_id": sample["id"],
            "query": sample["query"],
            "jurisdiction": sample["jurisdiction"],
            "answerable": sample["answerable"],
            "proposed_section_id": sample["expected_section_id"],
            "review_status": "human_reviewed" if record else "pending_human_review",
            "decision": record.decision if record else None,
            "reviewed_section_id": record.expected_section_id if record else None,
            "reviewer": record.reviewer if record else None,
            "comment": record.comment if record else "",
            "reviewed_at": record.reviewed_at if record else None,
        }
