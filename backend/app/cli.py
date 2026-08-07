from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings
from app.core.logging import configure_logging
from app.db import Database
from app.db.init import InitialCredentials, initialize_database, validate_initialized_database
from app.db.migrations import upgrade_database
from app.models import ProxyNode, Settings
from app.schemas.settings import SettingsUpdate
from app.services.singbox_service import SingBoxError, SingBoxService
from app.services.system_status import get_system_status


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _env_bool(*names: str) -> bool | None:
    value = _env(*names)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database(settings: AppSettings) -> Database:
    settings.ensure_runtime_directories()
    configure_logging(settings.log_dir)
    return Database(settings.database_url, settings.database_path)


def _require_existing_production_database(settings: AppSettings) -> None:
    if settings.environment == "production" and (
        not settings.database_path.is_file() or settings.database_path.is_symlink()
    ):
        raise RuntimeError(
            "production database is missing; restore a backup before using the CLI"
        )


def _serialize_initial(credentials: InitialCredentials) -> dict[str, Any]:
    return {
        "username": credentials.username,
        "password": credentials.temporary_password,
        "subscription_token": credentials.subscription_token,
    }


def _configure_deployment_settings(session: Session, args: argparse.Namespace) -> None:
    settings = session.get(Settings, 1)
    if settings is None:
        raise RuntimeError("settings are not initialized")
    server_ip = args.server_ip or _env("MYPROXY_SERVER_IP", "SERVER_IP")
    clear_domain = bool(getattr(args, "clear_domain", False))
    domain = None if clear_domain else args.domain or _env("MYPROXY_DOMAIN", "DOMAIN")
    certificate = args.certificate_path or _env(
        "MYPROXY_CERTIFICATE_PATH", "CERTIFICATE_PATH"
    )
    private_key = args.private_key_path or _env(
        "MYPROXY_PRIVATE_KEY_PATH", "PRIVATE_KEY_PATH"
    )
    self_signed = (
        True if args.self_signed else _env_bool("MYPROXY_SELF_SIGNED_MODE", "SELF_SIGNED_MODE")
    )
    validated = SettingsUpdate(
        server_ipv4=server_ip,
        domain=domain,
        certificate_path=certificate,
        private_key_path=private_key,
        self_signed_mode=self_signed,
    )
    for key, value in validated.model_dump(exclude_none=True).items():
        setattr(settings, key, value)
    if clear_domain:
        settings.domain = None


def command_init(args: argparse.Namespace, settings: AppSettings) -> int:
    database = _database(settings)
    upgrade_database(settings.database_url)
    credentials = initialize_database(
        database,
        initial_password=args.admin_password or _env("MYPROXY_ADMIN_PASSWORD"),
    )
    result = _serialize_initial(credentials)
    try:
        with database.session_factory() as session:
            _configure_deployment_settings(session, args)
            SingBoxService(settings).prepare_initial_config(session)
    except Exception as exc:
        # If initialization created secrets, stdout is their only intentional one-time
        # disclosure even when a later config check fails. They never enter logs.
        result["error"] = str(exc)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Initialization failed: {exc}", file=sys.stderr)
            if credentials.temporary_password:
                print(json.dumps(result, ensure_ascii=False))
        database.dispose()
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("MyProxy database and sing-box config initialized.")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    database.dispose()
    return 0


def _with_initialized_database(settings: AppSettings) -> tuple[Database, SingBoxService]:
    _require_existing_production_database(settings)
    database = _database(settings)
    try:
        if settings.environment == "production":
            validate_initialized_database(database)
        else:
            database.create_all()
    except Exception:
        database.dispose()
        raise
    return database, SingBoxService(settings)


def command_status(_args: argparse.Namespace, settings: AppSettings) -> int:
    database, service = _with_initialized_database(settings)
    with database.session_factory() as session:
        output = get_system_status(session, service, settings.home)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    database.dispose()
    return 0


def command_restart(_args: argparse.Namespace, settings: AppSettings) -> int:
    database, service = _with_initialized_database(settings)
    try:
        service.restart()
        print("sing-box: running")
        return 0
    finally:
        database.dispose()


def command_logs(args: argparse.Namespace, settings: AppSettings) -> int:
    database, service = _with_initialized_database(settings)
    try:
        print("\n".join(service.tail_logs(args.lines)))
        return 0
    finally:
        database.dispose()


def command_check(_args: argparse.Namespace, settings: AppSettings) -> int:
    database, service = _with_initialized_database(settings)
    try:
        with database.session_factory() as session:
            result = service.check_generated(session)
        print(result.output or "sing-box config: valid")
        return 0
    finally:
        database.dispose()


def command_backup(_args: argparse.Namespace, settings: AppSettings) -> int:
    database, service = _with_initialized_database(settings)
    try:
        with database.session_factory() as session:
            backup = service.create_backup(session)
        if backup is None:
            print("No active config to back up.", file=sys.stderr)
            return 1
        print(backup)
        return 0
    finally:
        database.dispose()


def command_restore(args: argparse.Namespace, settings: AppSettings) -> int:
    database, service = _with_initialized_database(settings)
    try:
        with database.session_factory() as session:
            result = service.restore_backup(session, args.backup)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        database.dispose()


def command_info(_args: argparse.Namespace, settings: AppSettings) -> int:
    database, _service = _with_initialized_database(settings)
    with database.session_factory() as session:
        app_settings = session.get(Settings, 1)
        nodes = list(session.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
        output = {
            "home": str(settings.home),
            "database": str(settings.database_path),
            "singbox_binary": str(settings.singbox_binary),
            "singbox_expected_version": settings.singbox_version,
            "server": {
                "name": app_settings.server_name if app_settings else None,
                "ipv4": app_settings.server_ipv4 if app_settings else None,
                "domain": app_settings.domain if app_settings else None,
                "self_signed_mode": app_settings.self_signed_mode if app_settings else None,
            },
            "nodes": [
                {
                    "id": node.id,
                    "name": node.name,
                    "protocol": node.protocol,
                    "enabled": node.enabled,
                    "listen_port": node.listen_port,
                }
                for node in nodes
            ],
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    database.dispose()
    return 0


def command_migrate(_args: argparse.Namespace, settings: AppSettings) -> int:
    _require_existing_production_database(settings)
    settings.ensure_runtime_directories()
    upgrade_database(settings.database_url)
    if settings.environment == "production":
        database = Database(settings.database_url, settings.database_path)
        try:
            validate_initialized_database(database)
        finally:
            database.dispose()
    print("Database migration: head")
    return 0


def command_prepare_config(_args: argparse.Namespace, settings: AppSettings) -> int:
    """Rebuild the checked on-disk config without changing persisted panel settings."""
    _require_existing_production_database(settings)
    database = _database(settings)
    try:
        upgrade_database(settings.database_url)
        if settings.environment == "production":
            validate_initialized_database(database)
        service = SingBoxService(settings)
        with database.session_factory() as session:
            result = service.prepare_initial_config(session)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        database.dispose()


def command_configure_deployment(args: argparse.Namespace, settings: AppSettings) -> int:
    """Apply explicit deployment identity/TLS inputs to an existing installation."""
    if os.getenv("MYPROXY_INTERNAL_RECONFIGURE") != "1":
        raise RuntimeError(
            "configure-deployment is internal; use sudo bash deploy/reconfigure.sh"
        )
    _require_existing_production_database(settings)
    database = _database(settings)
    try:
        upgrade_database(settings.database_url)
        if settings.environment == "production":
            validate_initialized_database(database)
        service = SingBoxService(settings)
        with database.session_factory() as session:
            if session.get(Settings, 1) is None:
                raise RuntimeError("settings are not initialized; run init first")
            _configure_deployment_settings(session, args)
            result = service.prepare_initial_config(session)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        database.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myproxy-backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize DB and checked config")
    init_parser.add_argument("--json", action="store_true")
    init_parser.add_argument("--admin-password")
    init_parser.add_argument("--server-ip")
    init_parser.add_argument("--domain")
    init_parser.add_argument("--certificate-path")
    init_parser.add_argument("--private-key-path")
    init_parser.add_argument("--self-signed", action="store_true")
    init_parser.set_defaults(handler=command_init)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(handler=command_status)
    restart_parser = subparsers.add_parser("restart")
    restart_parser.set_defaults(handler=command_restart)
    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("--lines", type=int, default=200)
    logs_parser.set_defaults(handler=command_logs)
    check_parser = subparsers.add_parser("check")
    check_parser.set_defaults(handler=command_check)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.set_defaults(handler=command_backup)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("backup", nargs="?")
    restore_parser.set_defaults(handler=command_restore)
    info_parser = subparsers.add_parser("info")
    info_parser.set_defaults(handler=command_info)
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.set_defaults(handler=command_migrate)
    prepare_parser = subparsers.add_parser(
        "prepare-config", help="render, validate and install config without changing settings"
    )
    prepare_parser.set_defaults(handler=command_prepare_config)
    configure_parser = subparsers.add_parser(
        "configure-deployment",
        help="update deployment-managed identity/TLS settings and checked config",
    )
    configure_parser.add_argument("--server-ip")
    domain_group = configure_parser.add_mutually_exclusive_group()
    domain_group.add_argument("--domain")
    domain_group.add_argument("--clear-domain", action="store_true")
    configure_parser.add_argument("--certificate-path")
    configure_parser.add_argument("--private-key-path")
    configure_parser.add_argument("--self-signed", action="store_true")
    configure_parser.set_defaults(handler=command_configure_deployment)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = AppSettings.from_env()
    try:
        return int(args.handler(args, settings))
    except (SingBoxError, RuntimeError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
