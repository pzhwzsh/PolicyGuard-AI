import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).parents[1]


def migration_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["database_url"] = url
    return config


def test_sqlite_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    database_path.unlink(missing_ok=True)
    url = f"sqlite:///{database_path.as_posix()}"
    config = migration_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"workflow_runs", "agent_memories", "evaluation_reviews"}.issubset(tables)
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="PostgreSQL service not configured")
def test_postgres_migration_round_trip() -> None:
    url = os.environ["POSTGRES_TEST_URL"]
    config = migration_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"policy_documents", "workflow_runs", "evaluation_reviews"}.issubset(tables)
    engine.dispose()
