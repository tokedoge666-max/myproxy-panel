from __future__ import annotations

from sqlalchemy import inspect

from app.db import Database
from app.db.init import initialize_database
from app.db.migrations import upgrade_database
from conftest import make_settings


def test_alembic_upgrade_is_idempotent(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_runtime_directories()
    upgrade_database(settings.database_url)
    upgrade_database(settings.database_url)
    database = Database(settings.database_url, settings.database_path)
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "admins",
        "auth_sessions",
        "settings",
        "proxy_nodes",
        "audit_logs",
        "alembic_version",
    }.issubset(tables)
    database.dispose()


def test_current_unversioned_schema_can_be_adopted(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_runtime_directories()
    database = Database(settings.database_url, settings.database_path)
    initialize_database(database, initial_password="Migration-Test-Password-7!")
    assert "alembic_version" not in inspect(database.engine).get_table_names()
    database.dispose()
    upgrade_database(settings.database_url)
    migrated = Database(settings.database_url, settings.database_path)
    assert "alembic_version" in inspect(migrated.engine).get_table_names()
    migrated.dispose()
