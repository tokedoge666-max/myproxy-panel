from __future__ import annotations

import json

import pytest

from app.models import ProxyNode, Settings
from app.services.credentials import default_node_config
from app.services.singbox_config import ConfigBuildError, build_singbox_config


def _settings() -> Settings:
    return Settings(
        id=1,
        subscription_token="token",
        domain="panel.example.com",
        certificate_path="/etc/letsencrypt/live/panel/fullchain.pem",
        private_key_path="/etc/letsencrypt/live/panel/privkey.pem",
    )


def _nodes() -> list[ProxyNode]:
    return [
        ProxyNode(
            id=1,
            name="LA-HY2",
            protocol="hysteria2",
            enabled=True,
            listen_port=8443,
            config_json=default_node_config("hysteria2"),
        ),
        ProxyNode(
            id=2,
            name="LA-TUIC",
            protocol="tuic",
            enabled=True,
            listen_port=10443,
            config_json=default_node_config("tuic"),
        ),
        ProxyNode(
            id=3,
            name="LA-SS2022",
            protocol="shadowsocks",
            enabled=True,
            listen_port=8388,
            config_json=default_node_config("shadowsocks"),
        ),
    ]


def test_singbox_113_config_has_only_supported_fields() -> None:
    config = build_singbox_config(_nodes(), _settings(), log_path="/opt/myproxy/logs/sing-box.log")
    assert [item["type"] for item in config["inbounds"]] == [
        "hysteria2",
        "tuic",
        "shadowsocks",
    ]
    hy2, tuic, shadowsocks = config["inbounds"]
    assert hy2["obfs"]["type"] == "salamander"
    assert hy2["tls"]["enabled"] is True
    assert tuic["zero_rtt_handshake"] is False
    assert tuic["heartbeat"] == "10s"
    assert "network" not in shadowsocks
    assert shadowsocks["method"] == "2022-blake3-aes-128-gcm"
    serialized = json.dumps(config)
    for unsupported in ("gecko", "bbr_profile", "realm", "min_packet_size", "max_packet_size"):
        assert unsupported not in serialized


def test_disabled_node_is_not_generated() -> None:
    nodes = _nodes()
    nodes[1].enabled = False
    config = build_singbox_config(nodes, _settings())
    assert [inbound["type"] for inbound in config["inbounds"]] == [
        "hysteria2",
        "shadowsocks",
    ]


def test_tls_and_duplicate_port_validation() -> None:
    settings = _settings()
    settings.certificate_path = None
    with pytest.raises(ConfigBuildError, match="certificate_path"):
        build_singbox_config(_nodes(), settings)
    nodes = _nodes()
    nodes[1].listen_port = nodes[0].listen_port
    with pytest.raises(ConfigBuildError, match="duplicate"):
        build_singbox_config(nodes, _settings())


def test_protocol_specific_validation() -> None:
    nodes = _nodes()
    nodes[0].config_json["obfs"]["type"] = "gecko"
    with pytest.raises(ConfigBuildError, match="salamander"):
        build_singbox_config(nodes, _settings())
    nodes = _nodes()
    nodes[1].config_json["uuid"] = "not-a-uuid"
    with pytest.raises(ConfigBuildError, match="UUID"):
        build_singbox_config(nodes, _settings())


def test_ss_udp_false_restricts_inbound_to_tcp() -> None:
    nodes = _nodes()
    nodes[2].config_json["udp"] = False
    config = build_singbox_config(nodes, _settings())
    ss = next(inbound for inbound in config["inbounds"] if inbound["type"] == "shadowsocks")
    assert ss["network"] == "tcp"


@pytest.mark.parametrize(
    ("node_index", "field"),
    ((0, "ignore_client_bandwidth"), (1, "zero_rtt_handshake"), (2, "udp")),
)
def test_boolean_protocol_options_reject_string_values(node_index: int, field: str) -> None:
    nodes = _nodes()
    nodes[node_index].config_json[field] = "false"
    with pytest.raises(ConfigBuildError, match="must be a boolean"):
        build_singbox_config(nodes, _settings())
