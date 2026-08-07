from __future__ import annotations

import base64
import secrets
import string
import uuid
from copy import deepcopy
from typing import Any

_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_.~"
SS2022_METHOD_KEY_LENGTHS = {
    "2022-blake3-aes-128-gcm": 16,
    "2022-blake3-aes-256-gcm": 32,
    "2022-blake3-chacha20-poly1305": 32,
}


def generate_password(length: int = 32) -> str:
    if length < 20:
        raise ValueError("secure passwords must contain at least 20 characters")
    characters = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("-_.~"),
    ]
    characters.extend(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - 4))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def generate_subscription_token() -> str:
    # token_urlsafe(32) carries at least 256 bits of entropy.
    return secrets.token_urlsafe(32)


def generate_uuid4() -> str:
    return str(uuid.uuid4())


def generate_ss2022_key(length: int = 16) -> str:
    if length not in {16, 32}:
        raise ValueError("SS2022 keys must contain 16 or 32 bytes")
    return base64.b64encode(secrets.token_bytes(length)).decode("ascii")


def default_node_config(protocol: str) -> dict[str, Any]:
    if protocol == "hysteria2":
        return {
            "user": "admin",
            "password": generate_password(),
            "obfs": {"type": "salamander", "password": generate_password()},
        }
    if protocol == "tuic":
        return {
            "uuid": generate_uuid4(),
            "password": generate_password(),
            "congestion_control": "bbr",
            "zero_rtt_handshake": False,
            "heartbeat": "10s",
        }
    if protocol == "shadowsocks":
        return {
            "method": "2022-blake3-aes-128-gcm",
            "password": generate_ss2022_key(),
            "udp": True,
        }
    raise ValueError(f"unsupported protocol: {protocol}")


def regenerate_node_credentials(protocol: str, config: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(config)
    if protocol == "hysteria2":
        updated["password"] = generate_password()
        obfs = dict(updated.get("obfs") or {})
        obfs.update({"type": "salamander", "password": generate_password()})
        updated["obfs"] = obfs
    elif protocol == "tuic":
        updated["uuid"] = generate_uuid4()
        updated["password"] = generate_password()
    elif protocol == "shadowsocks":
        method = updated.get("method", "2022-blake3-aes-128-gcm")
        try:
            key_length = SS2022_METHOD_KEY_LENGTHS[method]
        except KeyError as exc:
            raise ValueError(f"unsupported Shadowsocks 2022 method: {method}") from exc
        updated["password"] = generate_ss2022_key(key_length)
    else:
        raise ValueError(f"unsupported protocol: {protocol}")
    return updated


def is_valid_ss2022_key(value: str, expected_length: int = 16) -> bool:
    try:
        return len(base64.b64decode(value, validate=True)) == expected_length
    except (ValueError, base64.binascii.Error):
        return False


def ensure_ss2022_key_matches_method(config: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(config)
    method = updated.get("method", "2022-blake3-aes-128-gcm")
    try:
        expected_length = SS2022_METHOD_KEY_LENGTHS[method]
    except KeyError as exc:
        raise ValueError(f"unsupported Shadowsocks 2022 method: {method}") from exc
    password = updated.get("password")
    if not isinstance(password, str) or not is_valid_ss2022_key(password, expected_length):
        updated["password"] = generate_ss2022_key(expected_length)
    return updated
