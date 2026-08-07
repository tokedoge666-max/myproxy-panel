from __future__ import annotations

import base64

from sqlalchemy import select

from app.db.init import initialize_database
from app.models import ProxyNode


def test_default_nodes_and_crud(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    nodes = client.get("/api/v1/nodes")
    assert nodes.status_code == 200
    assert [node["name"] for node in nodes.json()] == ["LA-HY2", "LA-TUIC", "LA-SS2022"]
    assert nodes.json()[0]["config_json"]["password"] == "[REDACTED]"
    assert nodes.json()[1]["config_json"]["uuid"] == "[REDACTED]"

    update = client.put(
        "/api/v1/nodes/1",
        headers={"X-CSRF-Token": csrf},
        json={"listen_port": 9443, "config_json": {"up_mbps": 100}},
    )
    assert update.status_code == 200, update.text
    assert update.json()["listen_port"] == 9443
    assert update.json()["config_json"]["up_mbps"] == 100

    created = client.post(
        "/api/v1/nodes",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Extra-SS",
            "protocol": "shadowsocks",
            "listen_port": 9393,
        },
    )
    assert created.status_code == 201, created.text
    node_id = created.json()["id"]
    assert created.json()["config_json"]["password"] == "[REDACTED]"

    duplicate = client.post(
        "/api/v1/nodes",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Extra-SS", "protocol": "shadowsocks", "listen_port": 9494},
    )
    assert duplicate.status_code == 409

    disabled = client.post(
        f"/api/v1/nodes/{node_id}/disable", headers={"X-CSRF-Token": csrf}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    enabled = client.post(
        f"/api/v1/nodes/{node_id}/enable", headers={"X-CSRF-Token": csrf}
    )
    assert enabled.json()["enabled"] is True

    deleted = client.delete(
        f"/api/v1/nodes/{node_id}", headers={"X-CSRF-Token": csrf}
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/nodes/{node_id}").status_code == 404


def test_regenerate_secret_changes_database_without_disclosure(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    with client.app.state.database.session_factory() as session:
        before = session.scalar(select(ProxyNode).where(ProxyNode.id == 2)).config_json.copy()
    response = client.post(
        "/api/v1/nodes/2/regenerate-secret", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["config_json"]["password"] == "[REDACTED]"
    with client.app.state.database.session_factory() as session:
        after = session.scalar(select(ProxyNode).where(ProxyNode.id == 2)).config_json
    assert before["password"] != after["password"]
    assert before["uuid"] != after["uuid"]


def test_invalid_node_configuration_is_rejected(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    response = bundle.client.put(
        "/api/v1/nodes/1",
        headers={"X-CSRF-Token": csrf},
        json={"config_json": {"password": "short"}},
    )
    assert response.status_code == 422


def test_redacted_round_trip_never_overwrites_credentials(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    listed = client.get("/api/v1/nodes").json()
    with client.app.state.database.session_factory() as session:
        before = {
            node.id: node.config_json.copy()
            for node in session.scalars(select(ProxyNode).order_by(ProxyNode.id)).all()
        }
    for node in listed:
        response = client.put(
            f"/api/v1/nodes/{node['id']}",
            headers={"X-CSRF-Token": csrf},
            json={
                "listen_port": node["listen_port"] + 100,
                "config_json": node["config_json"],
            },
        )
        assert response.status_code == 200, response.text
    with client.app.state.database.session_factory() as session:
        after = {
            node.id: node.config_json
            for node in session.scalars(select(ProxyNode).order_by(ProxyNode.id)).all()
        }
    assert before == after


def test_ss_method_change_rotates_to_matching_key_length(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    response = bundle.client.put(
        "/api/v1/nodes/3",
        headers={"X-CSRF-Token": csrf},
        json={"config_json": {"method": "2022-blake3-aes-256-gcm"}},
    )
    assert response.status_code == 200, response.text
    with bundle.client.app.state.database.session_factory() as session:
        node = session.get(ProxyNode, 3)
        assert node.config_json["method"] == "2022-blake3-aes-256-gcm"
        assert len(base64.b64decode(node.config_json["password"], validate=True)) == 32


def test_create_ss_aes256_generates_32_byte_key(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    response = bundle.client.post(
        "/api/v1/nodes",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Extra-SS256",
            "protocol": "shadowsocks",
            "listen_port": 9394,
            "config_json": {"method": "2022-blake3-aes-256-gcm"},
        },
    )
    assert response.status_code == 201, response.text
    node_id = response.json()["id"]
    with bundle.client.app.state.database.session_factory() as session:
        node = session.get(ProxyNode, node_id)
        assert len(base64.b64decode(node.config_json["password"], validate=True)) == 32


def test_reserved_mihomo_names_and_privileged_ports_are_rejected(
    authenticated_bundle,
) -> None:
    bundle, csrf = authenticated_bundle
    for name in (
        "DIRECT",
        "REJECT",
        "REJECT-DROP",
        "COMPATIBLE",
        "PASS",
        "PASS-RULE",
        "Proxy",
        "GLOBAL",
    ):
        response = bundle.client.post(
            "/api/v1/nodes",
            headers={"X-CSRF-Token": csrf},
            json={"name": name, "protocol": "shadowsocks", "listen_port": 9393},
        )
        assert response.status_code == 422, name

    low_port = bundle.client.post(
        "/api/v1/nodes",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Low-Port", "protocol": "shadowsocks", "listen_port": 443},
    )
    assert low_port.status_code == 422


def test_startup_initialization_does_not_recreate_deleted_default_protocol(
    authenticated_bundle,
) -> None:
    bundle, csrf = authenticated_bundle
    deleted = bundle.client.delete(
        "/api/v1/nodes/2", headers={"X-CSRF-Token": csrf}
    )
    assert deleted.status_code == 204

    initialize_database(bundle.client.app.state.database)

    remaining = bundle.client.get("/api/v1/nodes")
    assert remaining.status_code == 200
    assert all(node["protocol"] != "tuic" for node in remaining.json())


def test_failed_auto_apply_rolls_back_node_state_and_credentials(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    initial_apply = client.post(
        "/api/v1/singbox/apply", headers={"X-CSRF-Token": csrf}
    )
    assert initial_apply.status_code == 200
    active_before = bundle.settings.singbox_config.read_text(encoding="utf-8")
    with client.app.state.database.session_factory() as session:
        node = session.get(ProxyNode, 1)
        old_port = node.listen_port
        old_config = node.config_json.copy()

    bundle.runner.restart_failures = 1
    failed_update = client.put(
        "/api/v1/nodes/1",
        headers={"X-CSRF-Token": csrf},
        json={"listen_port": old_port + 333},
    )
    assert failed_update.status_code == 500
    with client.app.state.database.session_factory() as session:
        node = session.get(ProxyNode, 1)
        assert node.listen_port == old_port
        assert node.config_json == old_config
    assert bundle.settings.singbox_config.read_text(encoding="utf-8") == active_before

    bundle.runner.restart_failures = 1
    failed_regeneration = client.post(
        "/api/v1/nodes/1/regenerate-secret", headers={"X-CSRF-Token": csrf}
    )
    assert failed_regeneration.status_code == 500
    with client.app.state.database.session_factory() as session:
        node = session.get(ProxyNode, 1)
        assert node.config_json == old_config
    assert bundle.settings.singbox_config.read_text(encoding="utf-8") == active_before
