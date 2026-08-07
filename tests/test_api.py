from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.db.init import initialize_database
from app.main import create_app
from app.models import Settings
from app.services.singbox_service import ConfigValidationFailed, SingBoxService
from conftest import INITIAL_PASSWORD, FakeRunner, make_settings


def _configure_tls(client) -> None:
    with client.app.state.database.session_factory.begin() as session:
        settings = session.get(Settings, 1)
        settings.server_ipv4 = "203.0.113.10"
        settings.domain = "panel.example.com"
        settings.certificate_path = "/cert.pem"
        settings.private_key_path = "/key.pem"


def _initialize_production_database(settings) -> Database:
    settings.ensure_runtime_directories()
    database = Database(settings.database_url, settings.database_path)
    initialize_database(database, initial_password=INITIAL_PASSWORD)
    with database.session_factory.begin() as session:
        app_settings = session.get(Settings, 1)
        app_settings.server_ipv4 = "203.0.113.10"
        app_settings.domain = "panel.example.com"
        app_settings.certificate_path = "/cert.pem"
        app_settings.private_key_path = "/key.pem"
    return database


def _render_database_config(settings, database: Database) -> dict:
    with database.session_factory() as session:
        return SingBoxService(settings, FakeRunner()).render(session)


def test_health_is_minimal_and_public(client_bundle) -> None:
    response = client_bundle.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "version" not in response.text
    assert client_bundle.runner.commands == []


def test_settings_system_and_singbox_api(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    settings_response = client.put(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={
            "server_name": "Personal Edge",
            "server_ipv4": "203.0.113.10",
            "domain": "panel.example.com",
            "certificate_path": "/opt/myproxy/config/tls/cert.pem",
            "private_key_path": "/opt/myproxy/config/tls/key.pem",
        },
    )
    assert settings_response.status_code == 200, settings_response.text
    assert settings_response.json()["server_name"] == "Personal Edge"
    assert "subscription_token" not in settings_response.json()

    system = client.get("/api/v1/system/status")
    assert system.status_code == 200
    assert set(system.json()) >= {
        "cpu_percent",
        "memory_percent",
        "memory_used_mb",
        "memory_total_mb",
        "disk_percent",
        "uptime_seconds",
        "singbox",
    }

    check = client.post("/api/v1/singbox/check", headers={"X-CSRF-Token": csrf})
    assert check.status_code == 200, check.text
    assert check.json()["valid"] is True
    apply = client.post("/api/v1/singbox/apply", headers={"X-CSRF-Token": csrf})
    assert apply.status_code == 200, apply.text
    assert apply.json()["status"] == "applied"
    assert client.get("/api/v1/singbox/status").json()["status"] == "running"
    client.post("/api/v1/singbox/apply", headers={"X-CSRF-Token": csrf})
    backups = client.get("/api/v1/singbox/backups")
    assert backups.status_code == 200
    item = backups.json()["backups"][0]
    assert set(item) == {"name", "created_at", "size_bytes"}
    assert item["name"].startswith("config-")
    assert item["size_bytes"] > 0


def test_settings_rejects_null_server_name(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    response = bundle.client.put(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={"server_name": None},
    )
    assert response.status_code == 422


def test_regenerate_all_credentials_marks_config_pending(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    _configure_tls(client)
    response = client.post(
        "/api/v1/credentials/regenerate-all", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["config_pending"] is False
    assert "/sub/" in response.json()["mihomo_url"]


def test_production_app_does_not_publish_docs(tmp_path) -> None:
    settings = make_settings(tmp_path, environment="production")
    database = _initialize_production_database(settings)
    expected = _render_database_config(settings, database)
    settings.singbox_config.write_text(
        json.dumps(expected, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    database.dispose()
    runner = FakeRunner()
    app = create_app(settings, runner=runner, bootstrap_password=INITIAL_PASSWORD)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    assert runner.commands == []


def test_production_startup_reconciles_divergent_config_from_database(tmp_path) -> None:
    settings = make_settings(tmp_path, environment="production")
    database = _initialize_production_database(settings)
    expected = _render_database_config(settings, database)
    settings.singbox_config.write_text('{"stale":true}\n', encoding="utf-8")
    database.dispose()

    runner = FakeRunner()
    app = create_app(settings, runner=runner)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert json.loads(settings.singbox_config.read_text(encoding="utf-8")) == expected
    assert any("check" in command for command in runner.commands)
    assert any("restart" in command for command in runner.commands)
    assert not settings.reconcile_marker.exists()


def test_production_startup_finishes_interrupted_reconcile_restart(tmp_path) -> None:
    settings = make_settings(tmp_path, environment="production")
    database = _initialize_production_database(settings)
    expected = _render_database_config(settings, database)
    SingBoxService._write_json(settings.singbox_config, expected)
    SingBoxService._write_json(
        settings.reconcile_marker, {"schema_version": 1, "state": "pending"}
    )
    database.dispose()

    runner = FakeRunner()
    app = create_app(settings, runner=runner)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert any("check" in command for command in runner.commands)
    assert any("restart" in command for command in runner.commands)
    assert not settings.reconcile_marker.exists()


def test_production_reconcile_failure_prevents_startup_and_keeps_config(tmp_path) -> None:
    settings = make_settings(tmp_path, environment="production")
    database = _initialize_production_database(settings)
    old_content = '{"stale":true}\n'
    settings.singbox_config.write_text(old_content, encoding="utf-8")
    database.dispose()

    runner = FakeRunner()
    runner.fail_checks = True
    app = create_app(settings, runner=runner)
    with pytest.raises(ConfigValidationFailed):
        with TestClient(app):
            pass

    assert settings.singbox_config.read_text(encoding="utf-8") == old_content
    assert not any("restart" in command for command in runner.commands)
    assert settings.reconcile_marker.is_file()


def test_production_missing_database_fails_closed(tmp_path) -> None:
    settings = make_settings(tmp_path, environment="production")
    app = create_app(settings, runner=FakeRunner())
    with pytest.raises(RuntimeError, match="database is missing"):
        with TestClient(app):
            pass
    assert not settings.database_path.exists()


def test_untrusted_host_is_rejected(client_bundle) -> None:
    response = client_bundle.client.get("/health", headers={"host": "evil.example"})
    assert response.status_code == 400


def test_tls_paths_must_be_absolute_linux_paths(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    invalid = bundle.client.put(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={"certificate_path": "relative/cert.pem", "private_key_path": "/key.pem"},
    )
    assert invalid.status_code == 422
    valid = bundle.client.put(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={
            "certificate_path": "/opt/myproxy/config/tls/cert.pem",
            "private_key_path": "/opt/myproxy/config/tls/key.pem",
        },
    )
    assert valid.status_code == 200, valid.text
    deployment_change = bundle.client.put(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={
            "certificate_path": "/new/fullchain.pem",
            "private_key_path": "/new/privkey.pem",
        },
    )
    assert deployment_change.status_code == 409
    assert "reconfigure.sh" in deployment_change.json()["detail"]
