from __future__ import annotations

from pathlib import Path

from app.services.network_health import expected_listeners, ipv4_listening_ports


def test_ipv4_listener_parser_excludes_connected_udp_socket(tmp_path: Path) -> None:
    (tmp_path / "tcp").write_text(
        "sl local_address rem_address st\n"
        "0: 00000000:20FB 00000000:0000 0A\n",
        encoding="ascii",
    )
    (tmp_path / "udp").write_text(
        "sl local_address rem_address st\n"
        "0: 00000000:28CB 00000000:0000 07\n"
        "1: 00000000:20C4 0100007F:0035 01\n",
        encoding="ascii",
    )

    listeners = ipv4_listening_ports(tmp_path)

    assert listeners == {"tcp": {8443}, "udp": {10443}}


def test_expected_listeners_follow_protocol_network_mode() -> None:
    config = {
        "inbounds": [
            {"type": "hysteria2", "listen_port": 8443},
            {"type": "tuic", "listen_port": 10443},
            {"type": "shadowsocks", "listen_port": 8388},
            {"type": "shadowsocks", "listen_port": 8389, "network": "tcp"},
        ]
    }

    assert expected_listeners(config) == {
        ("udp", 8443),
        ("udp", 10443),
        ("tcp", 8388),
        ("udp", 8388),
        ("tcp", 8389),
    }
