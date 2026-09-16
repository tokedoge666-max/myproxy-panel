from __future__ import annotations

from sqlalchemy import select

from app.models import ProxyNode
from app.services import system_status


def test_status_reports_each_expected_listener(authenticated_bundle, monkeypatch) -> None:
    bundle, _csrf = authenticated_bundle
    with bundle.client.app.state.database.session_factory() as session:
        nodes = list(session.scalars(select(ProxyNode).order_by(ProxyNode.id)))
    ports = {node.protocol: node.listen_port for node in nodes}
    monkeypatch.setattr(
        system_status,
        "_listening_ports",
        lambda: {
            "tcp": {ports["shadowsocks"]},
            "udp": set(ports.values()),
        },
    )
    monkeypatch.setattr(
        system_status,
        "_udp_buffer_status",
        lambda: {
            "receive_max_bytes": 16 * 1024 * 1024,
            "send_max_bytes": 16 * 1024 * 1024,
            "recommended_min_bytes": 16 * 1024 * 1024,
            "optimized": True,
        },
    )

    payload = bundle.client.get("/api/v1/system/status").json()
    assert payload["network"]["listener_checks_available"] is True
    assert payload["network"]["udp_buffers"]["optimized"] is True
    assert all(node["status"] == "running" for node in payload["nodes"])
    shadowsocks = next(node for node in payload["nodes"] if node["protocol"] == "shadowsocks")
    assert shadowsocks["listeners"] == {"tcp": True, "udp": True}


def test_status_marks_missing_udp_listener_degraded(authenticated_bundle, monkeypatch) -> None:
    bundle, _csrf = authenticated_bundle
    with bundle.client.app.state.database.session_factory() as session:
        nodes = list(session.scalars(select(ProxyNode).order_by(ProxyNode.id)))
    ports = {node.protocol: node.listen_port for node in nodes}
    monkeypatch.setattr(
        system_status,
        "_listening_ports",
        lambda: {
            "tcp": {ports["shadowsocks"]},
            "udp": {ports["hysteria2"], ports["shadowsocks"]},
        },
    )

    payload = bundle.client.get("/api/v1/system/status").json()
    tuic = next(node for node in payload["nodes"] if node["protocol"] == "tuic")
    assert tuic["status"] == "degraded"
    assert tuic["listeners"] == {"udp": False}
