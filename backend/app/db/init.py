from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.security import hash_password
from app.db import Database
from app.models import Admin, ProxyNode, Settings
from app.services.audit import record_audit
from app.services.credentials import (
    default_node_config,
    generate_password,
    generate_subscription_token,
)

DEFAULT_NODES = (
    ("LA-HY2", "hysteria2", 8443),
    ("LA-TUIC", "tuic", 10443),
    ("LA-SS2022", "shadowsocks", 8388),
)


@dataclass(frozen=True, slots=True)
class InitialCredentials:
    username: str
    temporary_password: str | None
    subscription_token: str | None


def initialize_database(
    database: Database,
    *,
    initial_password: str | None = None,
    username: str = "admin",
) -> InitialCredentials:
    database.create_all()
    created_password: str | None = None
    created_token: str | None = None

    with database.session_factory.begin() as db:
        admin_count = db.scalar(select(func.count(Admin.id))) or 0
        if admin_count > 1:
            raise RuntimeError("single-admin invariant violated")
        admin = db.scalar(select(Admin).limit(1))
        if admin is None:
            created_password = initial_password or generate_password()
            admin = Admin(
                username=username,
                password_hash=hash_password(created_password),
                must_change_password=True,
            )
            db.add(admin)
            record_audit(db, "System Initialized", {"username": username})

        settings = db.get(Settings, 1)
        first_install = settings is None
        if settings is None:
            created_token = generate_subscription_token()
            settings = Settings(id=1, subscription_token=created_token)
            db.add(settings)

        if first_install:
            for name, protocol, port in DEFAULT_NODES:
                db.add(
                    ProxyNode(
                        name=name,
                        protocol=protocol,
                        enabled=True,
                        listen_port=port,
                        config_json=default_node_config(protocol),
                    )
                )

    if database.database_path and database.database_path.exists() and os.name != "nt":
        database.database_path.chmod(0o600)
    return InitialCredentials(username, created_password, created_token)


def validate_initialized_database(database: Database) -> None:
    with database.session_factory() as db:
        admin_count = db.scalar(select(func.count(Admin.id))) or 0
        settings = db.get(Settings, 1)
        if admin_count != 1 or settings is None:
            raise RuntimeError(
                "production database is not initialized; restore a backup or run the installer"
            )
