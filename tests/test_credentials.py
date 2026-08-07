from __future__ import annotations

import base64
import uuid

from app.services.credentials import (
    generate_password,
    generate_ss2022_key,
    generate_subscription_token,
    generate_uuid4,
    is_valid_ss2022_key,
)


def test_generated_credentials_are_random_and_well_formed() -> None:
    passwords = {generate_password() for _ in range(20)}
    tokens = {generate_subscription_token() for _ in range(20)}
    assert len(passwords) == 20
    assert min(map(len, passwords)) >= 20
    assert len(tokens) == 20
    assert min(map(len, tokens)) >= 43

    generated_uuid = uuid.UUID(generate_uuid4())
    assert generated_uuid.version == 4

    key = generate_ss2022_key()
    assert is_valid_ss2022_key(key)
    assert len(base64.b64decode(key, validate=True)) == 16


def test_invalid_ss2022_keys_are_rejected() -> None:
    assert not is_valid_ss2022_key("not-base64")
    assert not is_valid_ss2022_key(base64.b64encode(b"short").decode())


def test_ss2022_32_byte_key_generation() -> None:
    key = generate_ss2022_key(32)
    assert is_valid_ss2022_key(key, 32)
    assert len(base64.b64decode(key, validate=True)) == 32
