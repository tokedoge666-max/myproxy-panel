from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AppSettings:
    environment: str = "production"
    home: Path = Path("/opt/myproxy")
    database_path: Path = Path("/opt/myproxy/data/myproxy.db")
    singbox_binary: Path = Path("/opt/myproxy/.runtime/sing-box/sing-box")
    singbox_config: Path = Path("/opt/myproxy/config/sing-box.json")
    log_dir: Path = Path("/opt/myproxy/logs")
    backup_dir: Path = Path("/opt/myproxy/backups")
    run_dir: Path = Path("/opt/myproxy/run")
    session_cookie: str = "myproxy_session"
    csrf_cookie: str = "XSRF-TOKEN"
    session_ttl_seconds: int = 28_800
    cookie_secure: bool = True
    singbox_service_name: str = "myproxy-singbox.service"
    systemctl_path: Path = Path("/bin/systemctl")
    sudo_path: Path = Path("/usr/bin/sudo")
    use_sudo: bool = True
    singbox_version: str = "1.13.16"
    backup_limit: int = 20
    restart_health_timeout_seconds: float = 4.0
    restart_stability_seconds: float = 1.5
    restart_poll_interval_seconds: float = 0.25
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def staging_config(self) -> Path:
        return self.run_dir / "sing-box.staging.json"

    @property
    def reconcile_marker(self) -> Path:
        return self.run_dir / "sing-box.reconcile.pending"

    @property
    def singbox_backup_dir(self) -> Path:
        return self.backup_dir / "sing-box"

    @classmethod
    def from_env(cls) -> AppSettings:
        home = Path(os.getenv("MYPROXY_HOME", "/opt/myproxy"))
        environment = os.getenv("MYPROXY_ENV", "production")
        raw_allowed_hosts = os.getenv("MYPROXY_ALLOWED_HOSTS")
        if raw_allowed_hosts:
            allowed_hosts = tuple(
                host.strip() for host in raw_allowed_hosts.split(",") if host.strip()
            )
        elif environment == "production":
            allowed_hosts = ("localhost", "127.0.0.1")
        else:
            allowed_hosts = ("*",)
        return cls(
            environment=environment,
            home=home,
            database_path=Path(os.getenv("MYPROXY_DB", str(home / "data/myproxy.db"))),
            singbox_binary=Path(
                os.getenv("MYPROXY_SINGBOX", str(home / ".runtime/sing-box/sing-box"))
            ),
            singbox_config=Path(
                os.getenv("MYPROXY_SINGBOX_CONFIG", str(home / "config/sing-box.json"))
            ),
            log_dir=Path(os.getenv("MYPROXY_LOG_DIR", str(home / "logs"))),
            backup_dir=Path(os.getenv("MYPROXY_BACKUP_DIR", str(home / "backups"))),
            run_dir=Path(os.getenv("MYPROXY_RUN_DIR", str(home / "run"))),
            session_ttl_seconds=int(os.getenv("MYPROXY_SESSION_TTL", "28800")),
            cookie_secure=_env_bool("MYPROXY_COOKIE_SECURE", environment == "production"),
            singbox_service_name=os.getenv(
                "MYPROXY_SINGBOX_SERVICE", "myproxy-singbox.service"
            ),
            systemctl_path=Path(os.getenv("MYPROXY_SYSTEMCTL", "/bin/systemctl")),
            sudo_path=Path(os.getenv("MYPROXY_SUDO", "/usr/bin/sudo")),
            use_sudo=_env_bool("MYPROXY_USE_SUDO", environment == "production"),
            singbox_version=os.getenv("MYPROXY_SINGBOX_VERSION", "1.13.16"),
            backup_limit=max(1, int(os.getenv("MYPROXY_BACKUP_LIMIT", "20"))),
            allowed_hosts=allowed_hosts,
        )

    def ensure_runtime_directories(self) -> None:
        for path in (
            self.database_path.parent,
            self.singbox_config.parent,
            self.log_dir,
            self.singbox_backup_dir,
            self.run_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings.from_env()
