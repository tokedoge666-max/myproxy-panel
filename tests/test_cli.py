from __future__ import annotations

import json

from app import cli
from app.db import Database
from app.db.init import initialize_database
from app.models import Settings
from app.services.singbox_service import SingBoxService
from conftest import make_settings


def test_cli_exposes_required_commands() -> None:
    parser = cli.build_parser()
    for command in (
        "status",
        "restart",
        "logs",
        "check",
        "backup",
        "restore",
        "info",
        "migrate",
        "prepare-config",
        "configure-deployment",
    ):
        assert parser.parse_args([command]).command == command
    init = parser.parse_args(["init", "--json"])
    assert init.command == "init"
    assert init.json is True


def test_production_cli_does_not_bootstrap_a_missing_database(
    tmp_path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path, environment="production")
    monkeypatch.setattr(cli.AppSettings, "from_env", classmethod(lambda cls: settings))

    for command in ("status", "migrate", "prepare-config"):
        assert cli.main([command]) == 1
        assert "production database is missing" in capsys.readouterr().err
        assert not settings.database_path.exists()


def test_init_json_contract_and_formal_config(tmp_path, monkeypatch, capsys) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(cli.AppSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setenv("SERVER_IP", "203.0.113.10")
    monkeypatch.setenv("DOMAIN", "panel.example.com")
    monkeypatch.setenv("CERTIFICATE_PATH", "/cert.pem")
    monkeypatch.setenv("PRIVATE_KEY_PATH", "/key.pem")

    def fake_prepare(self: SingBoxService, db):
        config = self.render(db)
        self._write_json(self.settings.singbox_config, config)
        db.commit()
        return {"status": "prepared", "validation": "test"}

    monkeypatch.setattr(SingBoxService, "prepare_initial_config", fake_prepare)
    assert cli.main(["init", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"username", "password", "subscription_token"}
    assert output["username"] == "admin"
    assert output["password"]
    assert output["subscription_token"]
    assert settings.singbox_config.exists()

    database = Database(settings.database_url, settings.database_path)
    with database.session_factory() as session:
        stored = session.get(Settings, 1)
        assert stored.domain == "panel.example.com"
        assert stored.server_ipv4 == "203.0.113.10"
    database.dispose()


def test_configure_deployment_updates_existing_settings_without_initializing(
    tmp_path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_runtime_directories()
    database = Database(settings.database_url, settings.database_path)
    initialize_database(database, initial_password="Initial-Password-123!")
    database.dispose()
    monkeypatch.setattr(cli.AppSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setenv("SERVER_IP", "198.51.100.50")
    monkeypatch.setenv("DOMAIN", "new.example.com")
    monkeypatch.setenv("CERTIFICATE_PATH", "/new/fullchain.pem")
    monkeypatch.setenv("PRIVATE_KEY_PATH", "/new/privkey.pem")
    monkeypatch.setenv("MYPROXY_INTERNAL_RECONFIGURE", "1")

    def fake_prepare(self: SingBoxService, db):
        db.commit()
        return {"status": "prepared", "validation": "test"}

    monkeypatch.setattr(SingBoxService, "prepare_initial_config", fake_prepare)
    assert cli.main(["configure-deployment"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "prepared"

    database = Database(settings.database_url, settings.database_path)
    with database.session_factory() as session:
        stored = session.get(Settings, 1)
        assert stored is not None
        assert stored.server_ipv4 == "198.51.100.50"
        assert stored.domain == "new.example.com"
        assert stored.certificate_path == "/new/fullchain.pem"
        assert stored.private_key_path == "/new/privkey.pem"
    database.dispose()

    assert cli.main(["configure-deployment", "--clear-domain"]) == 0
    capsys.readouterr()
    database = Database(settings.database_url, settings.database_path)
    with database.session_factory() as session:
        stored = session.get(Settings, 1)
        assert stored is not None
        assert stored.domain is None
    database.dispose()


def test_configure_deployment_rejects_direct_cli_bypass(
    tmp_path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(cli.AppSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.delenv("MYPROXY_INTERNAL_RECONFIGURE", raising=False)
    assert cli.main(["configure-deployment", "--domain", "bypass.example.com"]) == 1
    assert "deploy/reconfigure.sh" in capsys.readouterr().err


def test_prepare_config_preserves_web_managed_settings(
    tmp_path, monkeypatch, capsys
) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_runtime_directories()
    database = Database(settings.database_url, settings.database_path)
    initialize_database(database, initial_password="Initial-Password-123!")
    with database.session_factory.begin() as session:
        stored = session.get(Settings, 1)
        assert stored is not None
        stored.server_ipv4 = "198.51.100.42"
        stored.domain = "changed.example.com"
        stored.certificate_path = "/changed/fullchain.pem"
        stored.private_key_path = "/changed/privkey.pem"
    database.dispose()

    monkeypatch.setattr(cli.AppSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setenv("SERVER_IP", "203.0.113.10")
    monkeypatch.setenv("DOMAIN", "original.example.com")

    def fake_prepare(self: SingBoxService, db):
        return {"status": "prepared", "validation": "test"}

    monkeypatch.setattr(SingBoxService, "prepare_initial_config", fake_prepare)
    assert cli.main(["prepare-config"]) == 0
    capsys.readouterr()

    database = Database(settings.database_url, settings.database_path)
    with database.session_factory() as session:
        stored = session.get(Settings, 1)
        assert stored is not None
        assert stored.server_ipv4 == "198.51.100.42"
        assert stored.domain == "changed.example.com"
        assert stored.certificate_path == "/changed/fullchain.pem"
        assert stored.private_key_path == "/changed/privkey.pem"
    database.dispose()
