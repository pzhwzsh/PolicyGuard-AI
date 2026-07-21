import json
from pathlib import Path

from policyguard.application.evaluation_suite import evaluate_suite
from policyguard.application.knowledge import BM25Retriever
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    root = Path(__file__).parents[3]
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    with database.session_factory() as session:
        report = evaluate_suite(
            BM25Retriever(SqlAlchemyKnowledgeRepository(session)),
            root / "data/evaluation/suite-v1.json",
        )
    target = root / "data/benchmarks/rag-suite-bm25.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
