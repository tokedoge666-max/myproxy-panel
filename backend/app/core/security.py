from __future__ import annotations

import hashlib
import hmac
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

_password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_dummy_password_hash = _password_hasher.hash("MyProxy-Dummy-Password-1!")


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return _password_hasher.hash(password)


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    required_classes = (
        (string.ascii_lowercase, "lowercase letter"),
        (string.ascii_uppercase, "uppercase letter"),
        (string.digits, "digit"),
        (string.punctuation, "special character"),
    )
    for alphabet, label in required_classes:
        if not any(character in alphabet for character in password):
            raise ValueError(f"password must contain at least one {label}")


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def verify_login_password(password_hash: str | None, password: str) -> bool:
    """Always perform one Argon2 verification, including for unknown usernames."""
    verified = verify_password(password_hash or _dummy_password_hash, password)
    return password_hash is not None and verified


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def secrets_match(raw_value: str, expected_digest: str) -> bool:
    return hmac.compare_digest(digest_secret(raw_value), expected_digest)
