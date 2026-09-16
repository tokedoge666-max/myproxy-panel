from __future__ import annotations

import uuid

import pytest
import yaml
from sqlalchemy import select

from app.models import ProxyNode, Settings
from app.services.singbox_config import ConfigBuildError
from app.services.subscription import (
    build_mihomo_proxies,
    generate_mihomo_subscription,
    generate_provider_subscription,
)


def _prepare_subscription(client) -> str:
    with client.app.state.database.session_factory.begin() as session:
        settings = session.get(Settings, 1)
        settings.domain = "panel.example.com"
        settings.server_ipv4 = "203.0.113.10"
        settings.certificate_path = "/cert.pem"
        settings.private_key_path = "/key.pem"
        return settings.subscription_token


def test_mihomo_and_provider_generation(client_bundle) -> None:
    client = client_bundle.client
    token = _prepare_subscription(client)
    with client.app.state.database.session_factory() as session:
        settings = session.get(Settings, 1)
        nodes = list(session.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
        mihomo = yaml.safe_load(generate_mihomo_subscription(nodes, settings))
        provider = yaml.safe_load(generate_provider_subscription(nodes, settings))

    assert [proxy["name"] for proxy in mihomo["proxies"]] == [
        "LA-HY2",
        "LA-TUIC",
        "LA-SS2022",
    ]
    assert mihomo["proxy-groups"] == [
        {
            "name": "Proxy",
            "type": "select",
            "proxies": ["LA-HY2", "LA-TUIC", "LA-SS2022", "DIRECT"],
        }
    ]
    tuic = next(proxy for proxy in mihomo["proxies"] if proxy["type"] == "tuic")
    hy2 = next(proxy for proxy in mihomo["proxies"] if proxy["type"] == "hysteria2")
    assert all(proxy["ip-version"] == "ipv4-prefer" for proxy in mihomo["proxies"])
    assert hy2["handshake-timeout"] == 15
    assert hy2["bbr-profile"] == "standard"
    assert hy2["alpn"] == ["h3"]
    assert "uuid" in tuic and "password" in tuic and "token" not in tuic
    assert tuic["reduce-rtt"] is False
    assert tuic["heartbeat-interval"] == 10_000
    assert tuic["request-timeout"] == 10_000
    assert tuic["alpn"] == ["h3"]
    assert list(provider) == ["proxies"]
    assert provider["proxies"] == mihomo["proxies"]

    response = client.get(f"/sub/{token}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/yaml")
    assert yaml.safe_load(response.text)["rules"] == ["MATCH,Proxy"]
    provider_response = client.get(f"/sub/{token}?format=provider")
    assert list(yaml.safe_load(provider_response.text)) == ["proxies"]
    assert client.get("/sub/invalid-token").status_code == 404
    assert client.get(f"/sub/{token}?format=singbox").status_code == 400


def test_hysteria_server_bandwidth_is_not_copied_to_clients(client_bundle) -> None:
    client = client_bundle.client
    _prepare_subscription(client)
    with client.app.state.database.session_factory() as session:
        settings = session.get(Settings, 1)
        node = session.scalar(select(ProxyNode).where(ProxyNode.protocol == "hysteria2"))
        node.config_json = {
            **node.config_json,
            "up_mbps": 100,
            "down_mbps": 200,
            "ignore_client_bandwidth": False,
        }
        proxy = build_mihomo_proxies([node], settings)[0]
    assert "up" not in proxy
    assert "down" not in proxy


def test_disabled_nodes_and_self_signed_mode(client_bundle) -> None:
    client = client_bundle.client
    with client.app.state.database.session_factory.begin() as session:
        settings = session.get(Settings, 1)
        settings.server_ipv4 = "203.0.113.10"
        settings.self_signed_mode = True
        node = session.scalar(select(ProxyNode).where(ProxyNode.protocol == "tuic"))
        node.enabled = False
        token = settings.subscription_token
    parsed = yaml.safe_load(client.get(f"/sub/{token}").text)
    assert "LA-TUIC" not in [proxy["name"] for proxy in parsed["proxies"]]
    assert all(proxy.get("skip-cert-verify", True) for proxy in parsed["proxies"] if proxy["type"] != "ss")


def test_ss_udp_flag_is_reflected_in_subscription(client_bundle) -> None:
    client = client_bundle.client
    with client.app.state.database.session_factory.begin() as session:
        settings = session.get(Settings, 1)
        settings.server_ipv4 = "203.0.113.10"
        node = session.scalar(select(ProxyNode).where(ProxyNode.protocol == "shadowsocks"))
        node.config_json = {**node.config_json, "udp": False}
        token = settings.subscription_token
    parsed = yaml.safe_load(client.get(f"/sub/{token}").text)
    shadowsocks = next(proxy for proxy in parsed["proxies"] if proxy["type"] == "ss")
    assert shadowsocks["udp"] is False


def test_subscription_rejects_string_boolean_flags(client_bundle) -> None:
    client = client_bundle.client
    with client.app.state.database.session_factory() as session:
        settings = session.get(Settings, 1)
        settings.server_ipv4 = "203.0.113.10"
        nodes = list(session.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
        nodes[2].config_json = {**nodes[2].config_json, "udp": "false"}
        with pytest.raises(ConfigBuildError, match="must be a boolean"):
            build_mihomo_proxies(nodes, settings)


def test_subscription_canonicalizes_tuic_uuid(client_bundle) -> None:
    client = client_bundle.client
    expected = uuid.uuid4()
    with client.app.state.database.session_factory() as session:
        settings = session.get(Settings, 1)
        settings.server_ipv4 = "203.0.113.10"
        node = session.scalar(select(ProxyNode).where(ProxyNode.protocol == "tuic"))
        node.config_json = {**node.config_json, "uuid": "{" + expected.hex.upper() + "}"}
        proxy = build_mihomo_proxies([node], settings)[0]
    assert proxy["uuid"] == str(expected)


def test_token_rotation_invalidates_old_url(authenticated_bundle) -> None:
    bundle, csrf = authenticated_bundle
    client = bundle.client
    old_token = _prepare_subscription(client)
    info = client.get("/api/v1/subscription")
    assert old_token in info.json()["mihomo_url"]
    rotated = client.post(
        "/api/v1/subscription/rotate", headers={"X-CSRF-Token": csrf}
    )
    assert rotated.status_code == 200
    new_url = rotated.json()["mihomo_url"]
    assert old_token not in new_url
    assert client.get(f"/sub/{old_token}").status_code == 404
    new_token = new_url.rsplit("/", 1)[-1]
    assert client.get(f"/sub/{new_token}").status_code == 200
