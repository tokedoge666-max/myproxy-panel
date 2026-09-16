from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import Database
from app.db.init import initialize_database
from app.models import AuditLog, ProxyNode, Settings
from app.services.singbox_service import (
    ApplyFailed,
    CommandResult,
    ConfigValidationFailed,
    SingBoxError,
    SingBoxService,
)
from app.services.subscription import generate_mihomo_subscription
from conftest import FakeRunner, healthy_listener_checker, make_settings


def _service_fixture(tmp_path: Path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    settings.ensure_runtime_directories()
    database = Database(settings.database_url, settings.database_path)
    initialize_database(database, initial_password="Service-Test-Password-7!")
    with database.session_factory.begin() as session:
        app_settings = session.get(Settings, 1)
        app_settings.domain = "panel.example.com"
        app_settings.certificate_path = "/cert.pem"
        app_settings.private_key_path = "/key.pem"
    runner = FakeRunner()
    service = SingBoxService(settings, runner, listener_checker=healthy_listener_checker)
    return settings, database, runner, service


def test_safe_apply_validates_backs_up_and_restarts(tmp_path: Path) -> None:
    settings, database, runner, service = _service_fixture(tmp_path)
    settings.singbox_config.write_text('{"old": true}\n', encoding="utf-8")
    with database.session_factory() as session:
        result = service.safe_apply(session)
    assert result["status"] == "applied"
    active = json.loads(settings.singbox_config.read_text(encoding="utf-8"))
    assert len(active["inbounds"]) == 3
    backups = service.list_backups()
    assert len(backups) == 1
    assert service._state_path(settings.singbox_backup_dir / backups[0]).is_file()
    backup_data = json.loads(
        (settings.singbox_backup_dir / backups[0]).read_text(encoding="utf-8")
    )
    assert backup_data == {"old": True}
    assert any("check" in command for command in runner.commands)
    assert any("restart" in command for command in runner.commands)
    with database.session_factory() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == "Apply Config"))
    database.dispose()


def test_failed_restart_atomically_restores_previous_config(tmp_path: Path) -> None:
    settings, database, runner, service = _service_fixture(tmp_path)
    old_content = '{"old": true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")
    runner.restart_failures = 1
    with (
        database.session_factory() as session,
        pytest.raises(ApplyFailed, match="restored"),
    ):
        service.safe_apply(session)
    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    with database.session_factory() as session:
        rollback = session.scalar(select(AuditLog).where(AuditLog.action == "Rollback"))
        assert rollback is not None
    database.dispose()


def test_delayed_service_crash_triggers_config_rollback(tmp_path: Path) -> None:
    class DelayedCrashRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.restart_count = 0
            self.status_count = 0

        def __call__(self, command: list[str]) -> CommandResult:
            if "restart" in command:
                self.restart_count += 1
                self.status_count = 0
                return super().__call__(command)
            if "is-active" in command and self.restart_count == 1:
                self.commands.append(command)
                self.status_count += 1
                if self.status_count == 1:
                    return CommandResult(0, "active\n")
                return CommandResult(3, "inactive\n")
            return super().__call__(command)

    settings, database, _runner, _service = _service_fixture(
        tmp_path,
        restart_health_timeout_seconds=0.015,
        restart_stability_seconds=0.003,
        restart_poll_interval_seconds=0.001,
    )
    runner = DelayedCrashRunner()
    service = SingBoxService(settings, runner, listener_checker=healthy_listener_checker)
    old_content = '{"old": true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")

    with (
        database.session_factory() as session,
        pytest.raises(ApplyFailed, match="restored"),
    ):
        service.safe_apply(session)

    assert runner.restart_count == 2
    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    database.dispose()


def test_runner_oserror_after_replace_triggers_config_rollback(tmp_path: Path) -> None:
    class BrokenRunner(FakeRunner):
        def __call__(self, command: list[str]) -> CommandResult:
            if "restart" in command:
                raise OSError("simulated process resource exhaustion")
            return super().__call__(command)

    settings, database, _runner, _service = _service_fixture(tmp_path)
    service = SingBoxService(
        settings,
        BrokenRunner(),
        listener_checker=healthy_listener_checker,
    )
    old_content = '{"old": true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")
    with (
        database.session_factory() as session,
        pytest.raises(ApplyFailed, match="restored"),
    ):
        service.safe_apply(session)
    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    database.dispose()


def test_validation_failure_never_replaces_active_config(tmp_path: Path) -> None:
    settings, database, runner, service = _service_fixture(tmp_path)
    old_content = '{"old": true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")
    runner.fail_checks = True
    with (
        database.session_factory() as session,
        pytest.raises(ConfigValidationFailed) as error,
    ):
        service.safe_apply(session)
    assert "do-not-leak" not in str(error.value)
    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    assert service.list_backups() == []
    database.dispose()


def test_backup_retention_and_restore(tmp_path: Path) -> None:
    settings, database, _runner, service = _service_fixture(tmp_path, backup_limit=2)
    with database.session_factory() as session:
        service.safe_apply(session)
    for _value in range(3):
        with database.session_factory() as session:
            service.backup_current(session)
    backups = service.list_backups()
    assert len(backups) == 2
    assert len(list(settings.singbox_backup_dir.glob("state-*.json"))) == 2

    with database.session_factory() as session:
        restored = service.restore_backup(session, backups[-1])
    assert restored["status"] == "restored"
    assert len(json.loads(settings.singbox_config.read_text(encoding="utf-8"))["inbounds"]) == 3
    database.dispose()


def test_restart_uses_exact_noninteractive_sudo_contract(tmp_path: Path) -> None:
    settings, database, runner, service = _service_fixture(tmp_path, use_sudo=True)
    settings.singbox_config.write_text('{"inbounds":[]}', encoding="utf-8")
    service.restart()
    restart_command = next(command for command in runner.commands if "restart" in command)
    assert restart_command == [
        str(settings.sudo_path),
        "-n",
        str(settings.systemctl_path),
        "restart",
        "myproxy-singbox.service",
    ]
    status_command = next(command for command in runner.commands if "is-active" in command)
    assert status_command[0] == str(settings.systemctl_path)
    assert str(settings.sudo_path) not in status_command
    database.dispose()


def test_restart_requires_expected_ipv4_listener(tmp_path: Path, monkeypatch) -> None:
    settings, database, runner, _service = _service_fixture(tmp_path)
    settings.singbox_config.write_text(
        '{"inbounds":[{"type":"hysteria2","listen_port":8443}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.singbox_service.ipv4_listening_ports",
        lambda: {"tcp": set(), "udp": set()},
    )
    service = SingBoxService(settings, runner)

    with pytest.raises(ApplyFailed, match="IPv4 listeners"):
        service.restart()
    database.dispose()


def test_restart_fails_closed_when_listener_check_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    settings, database, runner, _service = _service_fixture(tmp_path)
    settings.singbox_config.write_text(
        '{"inbounds":[{"type":"hysteria2","listen_port":8443}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.singbox_service.ipv4_listening_ports",
        lambda: None,
    )
    service = SingBoxService(settings, runner)

    with pytest.raises(ApplyFailed, match="IPv4 listeners"):
        service.restart()
    database.dispose()


def test_listener_checker_error_restores_previous_config(tmp_path: Path) -> None:
    settings, database, runner, _service = _service_fixture(tmp_path)
    old_content = '{"old":true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")
    checks = 0

    def flaky_listener_checker(_config: dict) -> set[tuple[str, int]]:
        nonlocal checks
        checks += 1
        if checks == 1:
            raise OSError("simulated procfs failure")
        return set()

    service = SingBoxService(settings, runner, listener_checker=flaky_listener_checker)
    with (
        database.session_factory() as session,
        pytest.raises(ApplyFailed, match="restored"),
    ):
        service.safe_apply(session)

    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    database.dispose()


def test_log_tail_is_bounded_and_redacted(tmp_path: Path) -> None:
    settings, database, _runner, service = _service_fixture(tmp_path)
    (settings.log_dir / "sing-box.log").write_text(
        "first\npassword=supersecret\n/sub/abcdefghijklmnopqrstuvwxyz123456\n",
        encoding="utf-8",
    )
    lines = service.tail_logs(2)
    assert len(lines) == 2
    assert "supersecret" not in "\n".join(lines)
    assert "abcdefghijklmnopqrstuvwxyz123456" not in "\n".join(lines)
    database.dispose()


def test_database_commit_failure_restores_config_and_database(tmp_path: Path) -> None:
    settings, database, _runner, service = _service_fixture(tmp_path)
    with database.session_factory() as session:
        service.safe_apply(session)
    active_before = settings.singbox_config.read_text(encoding="utf-8")

    with database.session_factory() as session:
        node = session.get(ProxyNode, 1)
        old_port = node.listen_port
        node.listen_port += 77
        original_commit = session.commit
        attempts = 0

        def fail_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SQLAlchemyError("simulated commit failure")
            original_commit()

        session.commit = fail_once  # type: ignore[method-assign]
        with pytest.raises(ApplyFailed, match="database commit failed"):
            service.safe_apply(session)

    assert settings.singbox_config.read_text(encoding="utf-8") == active_before
    with database.session_factory() as session:
        assert session.get(ProxyNode, 1).listen_port == old_port
    database.dispose()


def test_prepare_config_commit_failure_restores_previous_file(tmp_path: Path) -> None:
    settings, database, _runner, service = _service_fixture(tmp_path)
    old_content = '{"old": true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")
    with database.session_factory() as session:
        def fail_commit() -> None:
            raise SQLAlchemyError("simulated prepare commit failure")

        session.commit = fail_commit  # type: ignore[method-assign]
        with pytest.raises(ApplyFailed, match="reverted"):
            service.prepare_initial_config(session)
    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    database.dispose()


def test_restore_recovers_nodes_but_keeps_deployment_identity_and_token(tmp_path: Path) -> None:
    settings, database, _runner, service = _service_fixture(tmp_path)
    with database.session_factory() as session:
        service.safe_apply(session)
    with database.session_factory() as session:
        old_node = session.get(ProxyNode, 1)
        old_port = old_node.listen_port
        old_password = old_node.config_json["password"]
        current_settings = session.get(Settings, 1)
        original_token = current_settings.subscription_token

    with database.session_factory() as session:
        node = session.get(ProxyNode, 1)
        node.listen_port += 123
        node.config_json = {**node.config_json, "password": "Changed-Node-Password-123!"}
        service.safe_apply(session)
    old_backup = service.list_backups()[0]
    sidecar = json.loads(
        service._state_path(settings.singbox_backup_dir / old_backup).read_text(encoding="utf-8")
    )
    assert sidecar["schema_version"] == 2
    assert "settings" not in sidecar

    current_token = "current-rotated-subscription-token-1234567890"
    with database.session_factory() as session:
        current_settings = session.get(Settings, 1)
        current_settings.subscription_token = current_token
        current_settings.server_ipv4 = "198.51.100.77"
        current_settings.domain = "new.example.com"
        current_settings.certificate_path = "/new/fullchain.pem"
        current_settings.private_key_path = "/new/privkey.pem"
        current_settings.self_signed_mode = False
        service.safe_apply(session)
    with database.session_factory() as session:
        result = service.restore_backup(session, old_backup)
    assert result["status"] == "restored"

    active = json.loads(settings.singbox_config.read_text(encoding="utf-8"))
    active_hy2 = next(item for item in active["inbounds"] if item["type"] == "hysteria2")
    assert active_hy2["listen_port"] == old_port
    assert active_hy2["users"][0]["password"] == old_password
    assert active_hy2["tls"]["certificate_path"] == "/new/fullchain.pem"
    with database.session_factory() as session:
        restored_node = session.get(ProxyNode, 1)
        restored_settings = session.get(Settings, 1)
        assert restored_node.listen_port == old_port
        assert restored_node.config_json["password"] == old_password
        assert restored_settings.subscription_token == current_token
        assert restored_settings.subscription_token != original_token
        assert restored_settings.server_ipv4 == "198.51.100.77"
        assert restored_settings.domain == "new.example.com"
        assert restored_settings.certificate_path == "/new/fullchain.pem"
        assert restored_settings.private_key_path == "/new/privkey.pem"
        nodes = list(session.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
        subscription = yaml.safe_load(generate_mihomo_subscription(nodes, restored_settings))
    subscribed_hy2 = next(
        item for item in subscription["proxies"] if item["type"] == "hysteria2"
    )
    assert subscribed_hy2["port"] == old_port
    assert subscribed_hy2["password"] == old_password
    database.dispose()


def test_restore_rejects_backup_without_state_sidecar(tmp_path: Path) -> None:
    _settings, database, _runner, service = _service_fixture(tmp_path)
    with database.session_factory() as session:
        service.safe_apply(session)
        backup = service.backup_current(session)
    service._state_path(backup).unlink()
    with (
        database.session_factory() as session,
        pytest.raises(SingBoxError, match="sidecar"),
    ):
        service.restore_backup(session, backup.name)
    database.dispose()


def test_legacy_backup_rejects_reserved_mihomo_node_name() -> None:
    legacy_state = {
        "schema_version": 1,
        "settings": {
            "server_name": "MyProxy Panel",
            "server_ipv4": "203.0.113.10",
            "domain": "old.example.com",
            "certificate_path": "/old/fullchain.pem",
            "private_key_path": "/old/privkey.pem",
            "self_signed_mode": False,
        },
        "nodes": [
            {
                "id": 1,
                "name": "DIRECT",
                "protocol": "shadowsocks",
                "enabled": True,
                "listen_port": 8388,
                "config_json": {"password": "ignored-by-structural-validation"},
                "created_at": "2026-08-07T12:00:00",
                "updated_at": "2026-08-07T12:00:00",
            }
        ],
    }
    with pytest.raises(SingBoxError, match="node name"):
        SingBoxService._validate_state_payload(legacy_state)
