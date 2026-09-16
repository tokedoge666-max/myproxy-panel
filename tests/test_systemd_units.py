from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _restricted_address_families(unit_name: str) -> set[str]:
    unit = (REPOSITORY_ROOT / "deploy/systemd" / unit_name).read_text(encoding="utf-8")
    return set(
        next(
            line.removeprefix("RestrictAddressFamilies=").split()
            for line in unit.splitlines()
            if line.startswith("RestrictAddressFamilies=")
        )
    )


def test_singbox_unit_allows_only_required_address_families() -> None:
    assert _restricted_address_families("myproxy-singbox.service") == {
        "AF_UNIX",
        "AF_INET",
        "AF_INET6",
        "AF_NETLINK",
    }


def test_api_unit_does_not_allow_netlink() -> None:
    assert _restricted_address_families("myproxy-api.service") == {
        "AF_UNIX",
        "AF_INET",
        "AF_INET6",
    }
