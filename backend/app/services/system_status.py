from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProxyNode
from app.services.singbox_service import SingBoxError, SingBoxService


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
    nodes = list(db.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "memory_used_mb": round(memory.used / (1024 * 1024)),
        "memory_total_mb": round(memory.total / (1024 * 1024)),
        "disk_percent": disk.percent,
        "uptime_seconds": max(0, round(time.time() - psutil.boot_time())),
        "singbox": {"status": singbox_status, "version": version},
        "nodes": [
            {
                "id": node.id,
                "name": node.name,
                "protocol": node.protocol,
                "enabled": node.enabled,
                "status": singbox_status if node.enabled else "disabled",
            }
            for node in nodes
        ],
    }
