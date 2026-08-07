from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

_EXPECTED_COLUMNS = {
    "admins": {
        "id",
        "username",
        "password_hash",
        "must_change_password",
        "created_at",
        "last_login",
    },
    "auth_sessions": {
        "id",
        "admin_id",
        "token_hash",
        "csrf_token_hash",
        "created_at",
        "expires_at",
        "revoked_at",
    },
    "settings": {
        "id",
        "server_name",
        "server_ipv4",
        "domain",
        "certificate_path",
        "private_key_path",
        "subscription_token",
        "self_signed_mode",
        "created_at",
        "updated_at",
    },
    "proxy_nodes": {
        "id",
        "name",
        "protocol",
        "enabled",
        "listen_port",
        "config_json",
        "created_at",
        "updated_at",
    },
    "audit_logs": {"id", "action", "detail", "created_at"},
}


def _alembic_config(database_url: str) -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _is_current_unversioned_schema(database_url: str) -> bool:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        if "alembic_version" in table_names or not set(_EXPECTED_COLUMNS).issubset(table_names):
            return False
        return all(
            expected.issubset(
                {column["name"] for column in inspector.get_columns(table_name)}
            )
            for table_name, expected in _EXPECTED_COLUMNS.items()
        )
    finally:
        engine.dispose()


def upgrade_database(database_url: str) -> None:
    config = _alembic_config(database_url)
    if _is_current_unversioned_schema(database_url):
        command.stamp(config, "head")
    else:
        command.upgrade(config, "head")
