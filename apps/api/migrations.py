from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from apps.api.db import Database


def upgrade_database(database: Database) -> None:
    """Upgrade the exact local database connection before serving requests."""

    config = _migration_config()
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def database_revisions(database: Database) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return current and expected schema heads without exposing the database URL."""

    config = _migration_config()
    expected = tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
    with database.engine.connect() as connection:
        current = tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
    return current, expected


def _migration_config() -> Config:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    return config
