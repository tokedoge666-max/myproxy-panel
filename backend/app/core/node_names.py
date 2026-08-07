from __future__ import annotations

RESERVED_MIHOMO_NAMES = {
    "compatible",
    "direct",
    "global",
    "pass",
    "pass-rule",
    "proxy",
    "reject",
    "reject-drop",
}


def normalize_node_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("node name must not be blank")
    if normalized.casefold() in RESERVED_MIHOMO_NAMES:
        raise ValueError("node name is reserved by Mihomo")
    return normalized
