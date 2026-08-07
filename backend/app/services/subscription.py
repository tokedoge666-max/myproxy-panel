from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

import yaml

from app.models import ProxyNode, Settings
from app.services.singbox_config import ConfigBuildError


def _server(settings: Settings) -> str:
    server = settings.domain or settings.server_ipv4
    if not server:
        raise ConfigBuildError("domain or server IPv4 is required for subscriptions")
    return server


def _heartbeat_ms(value: Any) -> int:
    if not isinstance(value, str):
        return 10_000
    if value.endswith("ms"):
        return int(value[:-2])
    if value.endswith("s"):
        return int(value[:-1]) * 1_000
    if value.endswith("m"):
        return int(value[:-1]) * 60_000
    return 10_000


def _boolean(config: dict[str, Any], key: str, default: bool, node_name: str) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ConfigBuildError(f"{node_name}: {key} must be a boolean")
    return value


def build_mihomo_proxies(
    nodes: Iterable[ProxyNode], settings: Settings
) -> list[dict[str, Any]]:
    server = _server(settings)
    sni = settings.domain or server
    skip_verify = bool(settings.self_signed_mode)
    proxies: list[dict[str, Any]] = []
    for node in nodes:
        if not node.enabled:
            continue
        config = node.config_json
        common: dict[str, Any] = {
            "name": node.name,
            "server": server,
            "port": node.listen_port,
        }
        if node.protocol == "hysteria2":
            obfs = config.get("obfs") or {}
            proxy = {
                **common,
                "type": "hysteria2",
                "password": config["password"],
                "obfs": "salamander",
                "obfs-password": obfs["password"],
                "sni": sni,
                "skip-cert-verify": skip_verify,
            }
            if config.get("up_mbps") is not None:
                proxy["up"] = config["up_mbps"]
            if config.get("down_mbps") is not None:
                proxy["down"] = config["down_mbps"]
            proxies.append(proxy)
        elif node.protocol == "tuic":
            raw_uuid = config.get("uuid")
            if not isinstance(raw_uuid, str):
                raise ConfigBuildError(f"{node.name}: uuid is required")
            try:
                parsed_uuid = uuid.UUID(raw_uuid)
            except ValueError as exc:
                raise ConfigBuildError(f"{node.name}: invalid TUIC UUID") from exc
            if parsed_uuid.version != 4:
                raise ConfigBuildError(f"{node.name}: TUIC UUID must be version 4")
            proxies.append(
                {
                    **common,
                    "type": "tuic",
                    "uuid": str(parsed_uuid),
                    "password": config["password"],
                    "sni": sni,
                    "skip-cert-verify": skip_verify,
                    "congestion-controller": config.get("congestion_control", "bbr"),
                    "udp-relay-mode": "native",
                    "reduce-rtt": _boolean(
                        config, "zero_rtt_handshake", False, node.name
                    ),
                    "heartbeat-interval": _heartbeat_ms(config.get("heartbeat", "10s")),
                }
            )
        elif node.protocol == "shadowsocks":
            proxies.append(
                {
                    **common,
                    "type": "ss",
                    "cipher": config.get("method", "2022-blake3-aes-128-gcm"),
                    "password": config["password"],
                    "udp": _boolean(config, "udp", True, node.name),
                }
            )
        else:
            raise ConfigBuildError(f"unsupported protocol: {node.protocol}")
    return proxies


def _dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )


def generate_mihomo_subscription(
    nodes: Iterable[ProxyNode], settings: Settings
) -> str:
    proxies = build_mihomo_proxies(nodes, settings)
    names = [proxy["name"] for proxy in proxies]
    return _dump_yaml(
        {
            "mixed-port": 7890,
            "allow-lan": False,
            "mode": "rule",
            "log-level": "info",
            "ipv6": False,
            "proxies": proxies,
            "proxy-groups": [
                {"name": "Proxy", "type": "select", "proxies": [*names, "DIRECT"]}
            ],
            "rules": ["MATCH,Proxy"],
        }
    )


def generate_provider_subscription(
    nodes: Iterable[ProxyNode], settings: Settings
) -> str:
    return _dump_yaml({"proxies": build_mihomo_proxies(nodes, settings)})
