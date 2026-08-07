from __future__ import annotations

import logging
import uuid

from app.core.logging import configure_logging, redact_text
from app.core.security import (
    digest_secret,
    hash_password,
    secrets_match,
    verify_password,
)
from app.db import Database
from app.db.init import initialize_database
from app.services.audit import record_audit
from conftest import make_settings


def test_password_hash_and_secret_digest() -> None:
    password = "A-Strong-Test-Password-7!"
    password_hash = hash_password(password)
    assert password not in password_hash
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, password)
    assert not verify_password(password_hash, "wrong-password")
    digest = digest_secret("secret")
    assert "secret" not in digest
    assert secrets_match("secret", digest)


def test_text_redaction_covers_urls_uuid_and_private_keys() -> None:
    raw_uuid = str(uuid.uuid4())
    private_key = "-----BEGIN PRIVATE KEY-----\nABCDEF\n-----END PRIVATE KEY-----"
    original = f"/sub/abcdefghijklmnopqrstuvwxyz123456 {raw_uuid} {private_key} password=hunter2"
    redacted = redact_text(original)
    assert "abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert raw_uuid not in redacted
    assert "ABCDEF" not in redacted
    assert "hunter2" not in redacted


def test_text_redaction_covers_json_secrets() -> None:
    original = '{"password":"secret-pass","token":"secret-token","cookie":"sid=secret"}'
    redacted = redact_text(original)
    assert "secret-pass" not in redacted
    assert "secret-token" not in redacted
    assert "sid=secret" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_audit_database_and_file_are_redacted(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_runtime_directories()
    configure_logging(settings.log_dir)
    database = Database(settings.database_url, settings.database_path)
    initialize_database(database, initial_password="Audit-Initial-Password-7!")
    secret_uuid = str(uuid.uuid4())
    with database.session_factory.begin() as session:
        entry = record_audit(
            session,
            "Test Action",
            {"password": "never-log-this", "message": secret_uuid},
        )
        assert "never-log-this" not in entry.detail
        assert secret_uuid not in entry.detail
    for handler in logging.getLogger("myproxy.audit").handlers:
        handler.flush()
    content = (settings.log_dir / "audit.log").read_text(encoding="utf-8")
    assert "Test Action" in content
    assert "never-log-this" not in content
    assert secret_uuid not in content
    database.dispose()
