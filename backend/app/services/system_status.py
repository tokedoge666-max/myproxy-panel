from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProxyNode
from app.services.network_health import expected_transports
from app.services.network_health import ipv4_listening_ports as _listening_ports
from app.services.singbox_service import SingBoxError, SingBoxService

_RECOMMENDED_UDP_BUFFER_BYTES = 16 * 1024 * 1024


def _read_integer(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return None
    return value if value >= 0 else None


def _udp_buffer_status(proc_sys: Path = Path("/proc/sys/net/core")) -> dict[str, Any]:
    receive_max = _read_integer(proc_sys / "rmem_max")
    send_max = _read_integer(proc_sys / "wmem_max")
    optimized = (
        receive_max >= _RECOMMENDED_UDP_BUFFER_BYTES
        and send_max >= _RECOMMENDED_UDP_BUFFER_BYTES
        if receive_max is not None and send_max is not None
        else None
    )
    return {
        "receive_max_bytes": receive_max,
        "send_max_bytes": send_max,
        "recommended_min_bytes": _RECOMMENDED_UDP_BUFFER_BYTES,
        "optimized": optimized,
    }


def _node_runtime_status(
    node: ProxyNode,
    singbox_status: str,
    listening_ports: dict[str, set[int]] | None,
) -> dict[str, Any]:
    config = node.config_json if isinstance(node.config_json, dict) else {}
    expected = expected_transports(node.protocol, config)
    listeners = {
        transport: (
            node.listen_port in listening_ports[transport]
            if listening_ports is not None
            else None
        )
        for transport in expected
    }
    if not node.enabled:
        status = "disabled"
    elif singbox_status != "running":
        status = singbox_status
    elif listening_ports is None:
        status = "unknown"
    elif all(listeners.values()):
        status = "running"
    else:
        status = "degraded"
    return {
        "id": node.id,
        "name": node.name,
        "protocol": node.protocol,
        "enabled": node.enabled,
        "status": status,
        "transport": "+".join(expected),
        "listeners": listeners,
    }


def get_system_status(
    db: Session, service: SingBoxService, disk_path: Path
) -> dict[str, Any]:
    memory = psutil.virtual_memory()
    disk_target = disk_path if disk_path.exists() else Path(disk_path.anchor or "/")
    disk = psutil.disk_usage(str(disk_target))
    try:
        singbox_status = service.status()
    except SingBoxError:
        singbox_status = "unknown"
    try:
        version = service.version()
    except SingBoxError:
        version = "unavailable"
    listening_ports = _listening_ports()
    udp_buffers = _udp_buffer_status()
    nodes = list(db.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "memory_used_mb": round(memory.used / (1024 * 1024)),
        "memory_total_mb": round(memory.total / (1024 * 1024)),
        "disk_percent": disk.percent,
        "uptime_seconds": max(0, round(time.time() - psutil.boot_time())),
        "singbox": {"status": singbox_status, "version": version},
        "network": {
            "listener_checks_available": listening_ports is not None,
            "listener_family": "ipv4",
            "udp_buffers": udp_buffers,
        },
        "nodes": [
            _node_runtime_status(node, singbox_status, listening_ports) for node in nodes
        ],
    }
