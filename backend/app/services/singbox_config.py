from __future__ import annotations

import base64
import re
import uuid
from collections.abc import Iterable
from typing import Any

from app.models import ProxyNode, Settings
from app.services.credentials import SS2022_METHOD_KEY_LENGTHS


class ConfigBuildError(ValueError):
    pass


_DURATION_RE = re.compile(r"^[1-9][0-9]*(?:ms|s|m|h)$")


def _required_string(config: dict[str, Any], key: str, node_name: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigBuildError(f"{node_name}: {key} is required")
    return value


def _boolean(config: dict[str, Any], key: str, default: bool, node_name: str) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ConfigBuildError(f"{node_name}: {key} must be a boolean")
    return value


def _duration(config: dict[str, Any], key: str, default: str, node_name: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not _DURATION_RE.fullmatch(value):
        raise ConfigBuildError(f"{node_name}: invalid {key} duration")
    return value


def _tls_config(settings: Settings) -> dict[str, Any]:
    if not settings.certificate_path or not settings.private_key_path:
        raise ConfigBuildError(
            "certificate_path and private_key_path are required while HY2 or TUIC is enabled"
        )
    return {
        "enabled": True,
        "certificate_path": settings.certificate_path,
        "key_path": settings.private_key_path,
    }


def _tag(node: ProxyNode) -> str:
    prefix = {
        "hysteria2": "hy2",
        "tuic": "tuic",
        "shadowsocks": "ss2022",
    }[node.protocol]
    return f"{prefix}-{node.id or 'new'}-in"


def _hysteria2_inbound(node: ProxyNode, settings: Settings) -> dict[str, Any]:
    config = node.config_json
    password = _required_string(config, "password", node.name)
    if len(password) < 16:
        raise ConfigBuildError(f"{node.name}: Hysteria2 password is too short")
    obfs = config.get("obfs")
    if not isinstance(obfs, dict) or obfs.get("type") != "salamander":
        raise ConfigBuildError(f"{node.name}: Hysteria2 obfs must be salamander")
    obfs_password = _required_string(obfs, "password", node.name)
    if len(obfs_password) < 16:
        raise ConfigBuildError(f"{node.name}: Hysteria2 obfs password is too short")
    user = config.get("user", "admin")
    if not isinstance(user, str) or not user:
        raise ConfigBuildError(f"{node.name}: user must be a non-empty string")

    inbound: dict[str, Any] = {
        "type": "hysteria2",
        "tag": _tag(node),
        "listen": "0.0.0.0",
        "listen_port": node.listen_port,
        "obfs": {"type": "salamander", "password": obfs_password},
        "users": [{"name": user, "password": password}],
        "tls": _tls_config(settings),
        "udp_timeout": _duration(config, "udp_timeout", "5m", node.name),
    }
    for key in ("up_mbps", "down_mbps"):
        value = config.get(key)
        if value is not None:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ConfigBuildError(f"{node.name}: {key} must be a positive integer")
            inbound[key] = value
    has_bandwidth = "up_mbps" in inbound or "down_mbps" in inbound
    ignore_client_bandwidth = _boolean(
        config, "ignore_client_bandwidth", not has_bandwidth, node.name
    )
    if has_bandwidth and ignore_client_bandwidth:
        raise ConfigBuildError(
            f"{node.name}: ignore_client_bandwidth conflicts with up_mbps/down_mbps"
        )
    inbound["ignore_client_bandwidth"] = ignore_client_bandwidth
    if ignore_client_bandwidth:
        inbound["bbr_profile"] = "standard"
    return inbound


def _tuic_inbound(node: ProxyNode, settings: Settings) -> dict[str, Any]:
    config = node.config_json
    raw_uuid = _required_string(config, "uuid", node.name)
    try:
        parsed_uuid = uuid.UUID(raw_uuid)
    except ValueError as exc:
        raise ConfigBuildError(f"{node.name}: invalid TUIC UUID") from exc
    if parsed_uuid.version != 4:
        raise ConfigBuildError(f"{node.name}: TUIC UUID must be version 4")
    password = _required_string(config, "password", node.name)
    if len(password) < 16:
        raise ConfigBuildError(f"{node.name}: TUIC password is too short")
    congestion = config.get("congestion_control", "cubic")
    if congestion not in {"bbr", "cubic", "new_reno"}:
        raise ConfigBuildError(f"{node.name}: unsupported congestion control")
    heartbeat = config.get("heartbeat", "10s")
    if not isinstance(heartbeat, str) or not _DURATION_RE.fullmatch(heartbeat):
        raise ConfigBuildError(f"{node.name}: invalid heartbeat duration")
    user = config.get("user", "admin")
    if not isinstance(user, str) or not user:
        raise ConfigBuildError(f"{node.name}: user must be a non-empty string")
    return {
        "type": "tuic",
        "tag": _tag(node),
        "listen": "0.0.0.0",
        "listen_port": node.listen_port,
        "users": [{"name": user, "uuid": str(parsed_uuid), "password": password}],
        "congestion_control": congestion,
        "auth_timeout": _duration(config, "auth_timeout", "3s", node.name),
        "zero_rtt_handshake": _boolean(config, "zero_rtt_handshake", False, node.name),
        "heartbeat": heartbeat,
        "tls": _tls_config(settings),
        "udp_timeout": _duration(config, "udp_timeout", "5m", node.name),
    }


def _shadowsocks_inbound(node: ProxyNode) -> dict[str, Any]:
    config = node.config_json
    method = config.get("method", "2022-blake3-aes-128-gcm")
    if method not in SS2022_METHOD_KEY_LENGTHS:
        raise ConfigBuildError(f"{node.name}: unsupported Shadowsocks 2022 method")
    password = _required_string(config, "password", node.name)
    try:
        decoded = base64.b64decode(password, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ConfigBuildError(f"{node.name}: Shadowsocks password is not valid Base64") from exc
    if len(decoded) != SS2022_METHOD_KEY_LENGTHS[method]:
        raise ConfigBuildError(f"{node.name}: Shadowsocks key length does not match method")
    # Omitting network is intentional: sing-box then accepts both TCP and UDP.
    inbound = {
        "type": "shadowsocks",
        "tag": _tag(node),
        "listen": "0.0.0.0",
        "listen_port": node.listen_port,
        "method": method,
        "password": password,
        "udp_timeout": _duration(config, "udp_timeout", "5m", node.name),
        "tcp_keep_alive": _duration(config, "tcp_keep_alive", "2m", node.name),
        "tcp_keep_alive_interval": _duration(
            config, "tcp_keep_alive_interval", "30s", node.name
        ),
    }
    if not _boolean(config, "udp", True, node.name):
        inbound["network"] = "tcp"
    return inbound


def build_singbox_config(
    nodes: Iterable[ProxyNode], settings: Settings, *, log_path: str | None = None
) -> dict[str, Any]:
    enabled_nodes = [node for node in nodes if node.enabled]
    ports: set[int] = set()
    inbounds: list[dict[str, Any]] = []
    for node in enabled_nodes:
        if node.listen_port in ports:
            raise ConfigBuildError(f"duplicate enabled listen port: {node.listen_port}")
        ports.add(node.listen_port)
        if node.protocol == "hysteria2":
            inbounds.append(_hysteria2_inbound(node, settings))
        elif node.protocol == "tuic":
            inbounds.append(_tuic_inbound(node, settings))
        elif node.protocol == "shadowsocks":
            inbounds.append(_shadowsocks_inbound(node))
        else:
            raise ConfigBuildError(f"unsupported protocol: {node.protocol}")

    log_config: dict[str, Any] = {"level": "info", "timestamp": True}
    if log_path:
        log_config["output"] = log_path
    return {
        "log": log_config,
        "inbounds": inbounds,
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {"final": "direct"},
    }
