from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import redact_data
from app.models import AuditLog


def record_audit(
    db: Session, action: str, detail: dict[str, Any] | None = None
) -> AuditLog:
    safe_detail = redact_data(detail or {})
    entry = AuditLog(
        action=action,
        detail=json.dumps(safe_detail, ensure_ascii=False, separators=(",", ":")),
    )
    db.add(entry)
    db.flush()
    logging.getLogger("myproxy.audit").info("%s %s", action, entry.detail)
    return entry
