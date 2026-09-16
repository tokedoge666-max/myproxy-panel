from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_port_table(path: Path, *, tcp: bool) -> set[int] | None:
    try:
        rows = path.read_text(encoding="ascii", errors="strict").splitlines()[1:]
    except (OSError, UnicodeError):
        return None
    ports: set[int] = set()
    for row in rows:
        fields = row.split()
        expected_state = "0A" if tcp else "07"
        if len(fields) < 4 or fields[3] != expected_state:
            continue
        try:
            ports.add(int(fields[1].rsplit(":", 1)[1], 16))
        except (IndexError, ValueError):
            continue
    return ports


def ipv4_listening_ports(
    proc_net: Path = Path("/proc/net"),
) -> dict[str, set[int]] | None:
    """Return host IPv4 listeners, or None when procfs is unavailable."""
    tcp = _read_port_table(proc_net / "tcp", tcp=True)
    udp = _read_port_table(proc_net / "udp", tcp=False)
    if tcp is None or udp is None:
        return None
    return {"tcp": tcp, "udp": udp}


def expected_transports(protocol: str, config: dict[str, Any]) -> tuple[str, ...]:
    if protocol in {"hysteria2", "tuic"}:
        return ("udp",)
    return ("tcp", "udp") if config.get("udp", True) is True else ("tcp",)


def expected_listeners(config: dict[str, Any]) -> set[tuple[str, int]]:
    listeners: set[tuple[str, int]] = set()
    for inbound in config.get("inbounds", []):
        if not isinstance(inbound, dict):
            continue
        inbound_type = inbound.get("type")
        port = inbound.get("listen_port")
        if not isinstance(port, int) or isinstance(port, bool):
            continue
        if inbound_type in {"hysteria2", "tuic"}:
            listeners.add(("udp", port))
        elif inbound_type == "shadowsocks":
            network = inbound.get("network")
            if network in {None, "tcp"}:
                listeners.add(("tcp", port))
            if network in {None, "udp"}:
                listeners.add(("udp", port))
    return listeners


def missing_listeners(
    config: dict[str, Any], listening: dict[str, set[int]]
) -> set[tuple[str, int]]:
    return {
        (transport, port)
        for transport, port in expected_listeners(config)
        if port not in listening.get(transport, set())
    }
