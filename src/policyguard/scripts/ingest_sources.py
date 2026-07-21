from pathlib import Path

from policyguard.application.knowledge import ingest_source_directory
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    with database.session_factory() as session:
        count = ingest_source_directory(
            SqlAlchemyKnowledgeRepository(session), Path(settings.source_dir)
        )
    print(f"ingested_documents={count}")


if __name__ == "__main__":
    main()

