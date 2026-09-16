from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import AppSettings
from app.main import create_app
from app.models import Settings
from app.services.singbox_service import CommandResult

INITIAL_PASSWORD = "Initial-Password-123!"
CHANGED_PASSWORD = "Better-Password-456!"


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.fail_checks = False
        self.restart_failures = 0
        self.active = True

    def __call__(self, command: list[str]) -> CommandResult:
        self.commands.append(command)
        if "version" in command:
            return CommandResult(0, "sing-box version 1.14.1\n")
        if "check" in command:
            if self.fail_checks:
                return CommandResult(1, stderr="invalid password=do-not-leak")
            return CommandResult(0, "configuration is valid\n")
        if "restart" in command:
            if self.restart_failures:
                self.restart_failures -= 1
                return CommandResult(1, stderr="restart failed")
            return CommandResult(0)
        if "is-active" in command:
            return CommandResult(0 if self.active else 3, "active\n" if self.active else "inactive\n")
        return CommandResult(1, stderr="unexpected command")


def make_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    home = tmp_path / "myproxy"
    values: dict[str, object] = {
        "environment": "testing",
        "home": home,
        "database_path": home / "data" / "myproxy.db",
        "singbox_binary": home / ".runtime" / "sing-box" / "sing-box",
        "singbox_config": home / "config" / "sing-box.json",
        "log_dir": home / "logs",
        "backup_dir": home / "backups",
        "run_dir": home / "run",
        "cookie_secure": False,
        "use_sudo": False,
        "systemctl_path": Path("/bin/systemctl"),
        "sudo_path": Path("/usr/bin/sudo"),
        "restart_health_timeout_seconds": 0.01,
        "restart_stability_seconds": 0.0,
        "restart_poll_interval_seconds": 0.001,
        "allowed_hosts": ("testserver",),
    }
    values.update(overrides)
    return AppSettings(**values)


@dataclass(slots=True)
class ClientBundle:
    client: TestClient
    settings: AppSettings
    runner: FakeRunner


@pytest.fixture
def client_bundle(tmp_path: Path) -> ClientBundle:
    settings = make_settings(tmp_path)
    runner = FakeRunner()
    app = create_app(settings, runner=runner, bootstrap_password=INITIAL_PASSWORD)
    with TestClient(app) as client:
        yield ClientBundle(client, settings, runner)


def authenticate(client: TestClient) -> str:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": INITIAL_PASSWORD},
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": INITIAL_PASSWORD, "new_password": CHANGED_PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    return csrf


@pytest.fixture
def authenticated_bundle(client_bundle: ClientBundle) -> tuple[ClientBundle, str]:
    csrf = authenticate(client_bundle.client)
    with client_bundle.client.app.state.database.session_factory.begin() as session:
        settings = session.get(Settings, 1)
        settings.server_ipv4 = "203.0.113.10"
        settings.domain = "panel.example.com"
        settings.certificate_path = "/opt/myproxy/config/tls/cert.pem"
        settings.private_key_path = "/opt/myproxy/config/tls/key.pem"
    return client_bundle, csrf
