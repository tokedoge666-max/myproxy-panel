from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "csrf",
    "password",
    "password_hash",
    "private_key",
    "private_key_path",
    "secret",
    "subscription_token",
    "token",
    "uuid",
}
_SUBSCRIPTION_RE = re.compile(r"(/sub/)[A-Za-z0-9_-]{12,}")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*")
_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?(?:password|token|authorization|cookie|private[_ -]?key|uuid)"
    r"[\"']?\s*[:=]\s*)(?:([\"'])(.*?)\2|([^\s,;&}\]]+))"
)
_UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)


def redact_text(value: str) -> str:
    value = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", value)
    value = _UUID_RE.sub("[REDACTED UUID]", value)
    value = _SUBSCRIPTION_RE.sub(r"\1[REDACTED]", value)
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    return _KEY_VALUE_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}"
            if match.group(2)
            else f"{match.group(1)}[REDACTED]"
        ),
        value,
    )


def redact_data(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact_data(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_data(record.args)
            else:
                record.args = tuple(
                    redact_text(str(item)) if isinstance(item, str) else item
                    for item in record.args
                )
        return True


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        "%Y-%m-%dT%H:%M:%SZ",
    )
    api_path = (log_dir / "api.log").resolve()
    app_logger = logging.getLogger("myproxy")
    app_logger.disabled = False
    app_logger.setLevel(logging.INFO)
    for existing in list(app_logger.handlers):
        if isinstance(existing, RotatingFileHandler):
            app_logger.removeHandler(existing)
            existing.close()
    handler = RotatingFileHandler(
        api_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    handler.addFilter(RedactingFilter())
    app_logger.addHandler(handler)
    app_logger.propagate = False

    audit_path = (log_dir / "audit.log").resolve()
    audit_logger = logging.getLogger("myproxy.audit")
    audit_logger.disabled = False
    audit_logger.setLevel(logging.INFO)
    for existing in list(audit_logger.handlers):
        if isinstance(existing, RotatingFileHandler):
            audit_logger.removeHandler(existing)
            existing.close()
    audit_handler = RotatingFileHandler(
        audit_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    audit_handler.setFormatter(formatter)
    audit_handler.addFilter(RedactingFilter())
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False

    # Uvicorn's default access line includes the raw subscription path/token.
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactingFilter) for item in access_logger.filters):
        access_logger.addFilter(RedactingFilter())
