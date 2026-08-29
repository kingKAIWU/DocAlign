from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from apps.api.db import Database


def upgrade_database(database: Database) -> None:
    """Upgrade the exact local database connection before serving requests."""

    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
