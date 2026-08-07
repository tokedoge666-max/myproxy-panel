from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import AppSettings
from app.core.logging import redact_text
from app.core.node_names import normalize_node_name
from app.models import ProxyNode, Settings
from app.services.audit import record_audit
from app.services.singbox_config import ConfigBuildError, build_singbox_config


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return redact_text((self.stdout + "\n" + self.stderr).strip())


class SingBoxError(RuntimeError):
    pass


class SingBoxUnavailable(SingBoxError):
    pass


class ConfigValidationFailed(SingBoxError):
    pass


class ApplyFailed(SingBoxError):
    pass


Runner = Callable[[list[str]], CommandResult]


def _default_runner(command: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SingBoxUnavailable(redact_text(str(exc))) from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class SingBoxService:
    _apply_lock = threading.RLock()

    def __init__(self, settings: AppSettings, runner: Runner | None = None) -> None:
        self.settings = settings
        self.runner = runner or _default_runner

    def _run(self, command: list[str]) -> CommandResult:
        try:
            return self.runner(command)
        except SingBoxError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise SingBoxUnavailable(redact_text(str(exc))) from exc

    @contextmanager
    def _operation_lock(self, timeout_seconds: float = 15.0) -> Iterator[None]:
        self.settings.run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.settings.run_dir / "sing-box.apply.lock"
        with self._apply_lock, lock_path.open("a+b") as lock_file:
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            if os.name != "nt":
                lock_path.chmod(0o600)
            deadline = time.monotonic() + timeout_seconds
            locked = False
            while not locked:
                try:
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ApplyFailed(
                            "another sing-box configuration operation is in progress"
                        ) from exc
                    time.sleep(0.1)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def version(self) -> str:
        result = self._run([str(self.settings.singbox_binary), "version"])
        if result.returncode != 0:
            raise SingBoxUnavailable(result.output or "sing-box version failed")
        return result.output.splitlines()[0] if result.output else "unknown"

    def status(self) -> str:
        result = self._run(
            [
                str(self.settings.systemctl_path),
                "is-active",
                self.settings.singbox_service_name,
            ]
        )
        return "running" if result.returncode == 0 and result.stdout.strip() == "active" else "stopped"

    def restart(self) -> None:
        command = [
            str(self.settings.systemctl_path),
            "restart",
            self.settings.singbox_service_name,
        ]
        if self.settings.use_sudo:
            command = [str(self.settings.sudo_path), "-n", *command]
        result = self._run(command)
        if result.returncode != 0:
            raise ApplyFailed(result.output or "sing-box restart failed")

        deadline = time.monotonic() + self.settings.restart_health_timeout_seconds
        active_since: float | None = None
        while True:
            now = time.monotonic()
            if self.status() == "running":
                active_since = now if active_since is None else active_since
                if now - active_since >= self.settings.restart_stability_seconds:
                    return
            else:
                active_since = None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self.settings.restart_poll_interval_seconds, remaining))
        raise ApplyFailed("sing-box did not remain active after restart")

    def render(self, db: Session) -> dict[str, Any]:
        db.flush()
        settings = db.get(Settings, 1)
        if settings is None:
            raise SingBoxError("settings are not initialized")
        nodes = list(db.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
        return build_singbox_config(
            nodes, settings, log_path=str(self.settings.log_dir / "sing-box.log")
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_json(path: Path, config: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                json.dump(config, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, path)
            SingBoxService._fsync_directory(path.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

    def generate_staging(self, db: Session) -> Path:
        self._write_json(self.settings.staging_config, self.render(db))
        return self.settings.staging_config

    def validate_file(self, path: Path) -> CommandResult:
        result = self._run(
            [str(self.settings.singbox_binary), "check", "-c", str(path)]
        )
        if result.returncode != 0:
            raise ConfigValidationFailed(result.output or "sing-box check failed")
        return result

    def check_generated(self, db: Session) -> CommandResult:
        with self._operation_lock():
            path = self.generate_staging(db)
            try:
                return self.validate_file(path)
            except ConfigValidationFailed as exc:
                db.rollback()
                record_audit(db, "Config Validation Failed", {"error": str(exc)})
                db.commit()
                raise

    def prepare_initial_config(self, db: Session) -> dict[str, str]:
        """Generate, validate, and install config without restarting systemd."""
        with self._operation_lock():
            self.generate_staging(db)
            result = self.validate_file(self.settings.staging_config)
            had_previous_config = self.settings.singbox_config.exists()
            previous_config: Path | None = None
            if had_previous_config:
                fd, previous_name = tempfile.mkstemp(
                    prefix=".prepare-previous.", suffix=".json", dir=self.settings.run_dir
                )
                os.close(fd)
                previous_config = Path(previous_name)
                try:
                    shutil.copyfile(self.settings.singbox_config, previous_config)
                    if os.name != "nt":
                        previous_config.chmod(0o600)
                except OSError:
                    previous_config.unlink(missing_ok=True)
                    raise
            try:
                self._install_staging()
                record_audit(db, "Initial Config Generated", {})
                db.commit()
            except Exception as exc:
                db.rollback()
                rollback_error: str | None = None
                try:
                    if previous_config is not None:
                        self._atomic_copy(previous_config, self.settings.singbox_config)
                    elif not had_previous_config:
                        self.settings.singbox_config.unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_error = str(rollback_exc)
                message = "database commit failed; prepared configuration was reverted"
                if rollback_error:
                    message += f"; config rollback also failed: {rollback_error}"
                raise ApplyFailed(message) from exc
            finally:
                if previous_config is not None:
                    previous_config.unlink(missing_ok=True)
            return {"status": "prepared", "validation": result.output or "ok"}

    def _backup_path(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        candidate = self.settings.singbox_backup_dir / f"config-{timestamp}.json"
        suffix = 1
        while candidate.exists():
            candidate = self.settings.singbox_backup_dir / f"config-{timestamp}-{suffix}.json"
            suffix += 1
        return candidate

    @staticmethod
    def _state_path(config_backup: Path) -> Path:
        return config_backup.with_name(config_backup.name.replace("config-", "state-", 1))

    @staticmethod
    def _snapshot_state(db: Session) -> dict[str, Any]:
        # A separate session deliberately observes the last committed DB state. During
        # auto-apply, the caller session may already contain the proposed new state.
        with Session(bind=db.get_bind(), expire_on_commit=False) as snapshot_db:
            settings = snapshot_db.get(Settings, 1)
            if settings is None:
                raise SingBoxError("settings are not initialized")
            nodes = list(
                snapshot_db.scalars(select(ProxyNode).order_by(ProxyNode.id)).all()
            )
            return {
                "schema_version": 2,
                "nodes": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "protocol": node.protocol,
                        "enabled": node.enabled,
                        "listen_port": node.listen_port,
                        "config_json": node.config_json,
                        "created_at": node.created_at.isoformat(),
                        "updated_at": node.updated_at.isoformat(),
                    }
                    for node in nodes
                ],
            }

    @staticmethod
    def _validate_state_payload(state: Any) -> dict[str, Any]:
        if not isinstance(state, dict) or state.get("schema_version") not in {1, 2}:
            raise SingBoxError("unsupported or invalid backup state")
        nodes = state.get("nodes")
        if not isinstance(nodes, list):
            raise SingBoxError("invalid backup state structure")
        if state["schema_version"] == 1:
            settings = state.get("settings")
            required_settings = {
                "server_name",
                "server_ipv4",
                "domain",
                "certificate_path",
                "private_key_path",
                "self_signed_mode",
            }
            if not isinstance(settings, dict) or set(settings) != required_settings:
                raise SingBoxError("invalid legacy settings backup state")
        elif set(state) != {"schema_version", "nodes"}:
            raise SingBoxError("invalid backup state fields")
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for node in nodes:
            if not isinstance(node, dict):
                raise SingBoxError("invalid node backup state")
            required_node = {
                "id",
                "name",
                "protocol",
                "enabled",
                "listen_port",
                "config_json",
                "created_at",
                "updated_at",
            }
            if set(node) != required_node:
                raise SingBoxError("invalid node backup fields")
            if (
                not isinstance(node["id"], int)
                or node["id"] <= 0
                or not isinstance(node["name"], str)
                or node["protocol"] not in {"hysteria2", "tuic", "shadowsocks"}
                or not isinstance(node["enabled"], bool)
                or not isinstance(node["listen_port"], int)
                or not 1024 <= node["listen_port"] <= 65535
                or not isinstance(node["config_json"], dict)
            ):
                raise SingBoxError("invalid node backup values")
            try:
                normalized_name = normalize_node_name(node["name"])
            except ValueError as exc:
                raise SingBoxError("invalid node name in backup state") from exc
            if normalized_name != node["name"]:
                raise SingBoxError("non-canonical node name in backup state")
            if node["id"] in seen_ids or node["name"] in seen_names:
                raise SingBoxError("duplicate node in backup state")
            seen_ids.add(node["id"])
            seen_names.add(node["name"])
            try:
                datetime.fromisoformat(node["created_at"])
                datetime.fromisoformat(node["updated_at"])
            except (TypeError, ValueError) as exc:
                raise SingBoxError("invalid node timestamp in backup state") from exc
        return state

    @classmethod
    def _restore_database_state(cls, db: Session, state: dict[str, Any]) -> None:
        state = cls._validate_state_payload(state)
        # Deployment identity, TLS settings and the subscription token intentionally
        # remain current. They are coordinated with Nginx/app.env outside this backup.
        db.execute(delete(ProxyNode))
        db.flush()
        for item in state["nodes"]:
            db.add(
                ProxyNode(
                    id=item["id"],
                    name=item["name"],
                    protocol=item["protocol"],
                    enabled=item["enabled"],
                    listen_port=item["listen_port"],
                    config_json=item["config_json"],
                    created_at=datetime.fromisoformat(item["created_at"]),
                    updated_at=datetime.fromisoformat(item["updated_at"]),
                )
            )
        db.flush()

    def backup_current(self, db: Session) -> Path | None:
        source = self.settings.singbox_config
        if not source.exists():
            return None
        state = self._snapshot_state(db)
        self.settings.singbox_backup_dir.mkdir(parents=True, exist_ok=True)
        destination = self._backup_path()
        state_path = self._state_path(destination)
        try:
            self._atomic_copy(source, destination)
            self._write_json(state_path, state)
        except (OSError, SingBoxError):
            destination.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
            raise
        self._prune_backups()
        return destination

    def create_backup(self, db: Session) -> Path | None:
        with self._operation_lock():
            return self.backup_current(db)

    def _prune_backups(self) -> None:
        backups = sorted(
            self.settings.singbox_backup_dir.glob("config-*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[self.settings.backup_limit :]:
            old_backup.unlink(missing_ok=True)
            self._state_path(old_backup).unlink(missing_ok=True)
        existing_configs = {item.name for item in backups[: self.settings.backup_limit]}
        for orphan_state in self.settings.singbox_backup_dir.glob("state-*.json"):
            matching_config = orphan_state.name.replace("state-", "config-", 1)
            if matching_config not in existing_configs:
                orphan_state.unlink(missing_ok=True)

    def list_backups(self) -> list[str]:
        return [
            item.name
            for item in sorted(
                self.settings.singbox_backup_dir.glob("config-*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        ]

    def backup_info(self) -> list[dict[str, str | int]]:
        output: list[dict[str, str | int]] = []
        for name in self.list_backups():
            path = self.settings.singbox_backup_dir / name
            stat = path.stat()
            created_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
            output.append(
                {"name": name, "created_at": created_at, "size_bytes": stat.st_size}
            )
        return output

    def _install_staging(self) -> None:
        destination = self.settings.singbox_config
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            shutil.copyfile(self.settings.staging_config, temporary_path)
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, destination)
            self._fsync_directory(destination.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _atomic_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary_path)
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, destination)
            SingBoxService._fsync_directory(destination.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _formal_config_matches(self, expected: dict[str, Any]) -> bool:
        path = self.settings.singbox_config
        if not path.is_file() or path.is_symlink():
            return False
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            return self._canonical_json(current) == self._canonical_json(expected)
        except (OSError, UnicodeError, TypeError, ValueError):
            return False

    def _restore_config_after_failure(
        self, previous_backup: Path | None, had_previous_config: bool
    ) -> str | None:
        errors: list[str] = []
        try:
            if previous_backup is not None:
                self._atomic_copy(previous_backup, self.settings.singbox_config)
            elif not had_previous_config:
                self.settings.singbox_config.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"config restore failed: {exc}")
        try:
            self.restart()
        except SingBoxError as exc:
            errors.append(f"rollback restart failed: {exc}")
        return "; ".join(errors) or None

    def _apply_staging_locked(
        self,
        db: Session,
        *,
        audit_action: str,
        result_status: str,
    ) -> dict[str, Any]:
        try:
            validation = self.validate_file(self.settings.staging_config)
        except ConfigValidationFailed as exc:
            db.rollback()
            record_audit(db, "Config Validation Failed", {"error": str(exc)})
            db.commit()
            raise

        previous_backup = self.backup_current(db)
        had_previous_config = self.settings.singbox_config.exists()
        self._install_staging()
        try:
            self.restart()
        except SingBoxError as exc:
            db.rollback()
            rollback_error = self._restore_config_after_failure(
                previous_backup, had_previous_config
            )
            record_audit(
                db,
                "Rollback",
                {"reason": str(exc), "rollback_error": rollback_error},
            )
            db.commit()
            message = "sing-box restart failed; previous configuration restored"
            if rollback_error:
                message += f"; {rollback_error}"
            raise ApplyFailed(message) from exc

        try:
            record_audit(
                db,
                audit_action,
                {"backup": previous_backup.name if previous_backup else None},
            )
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            rollback_error = self._restore_config_after_failure(
                previous_backup, had_previous_config
            )
            try:
                record_audit(
                    db,
                    "Rollback",
                    {
                        "reason": "database commit failed after service restart",
                        "rollback_error": rollback_error,
                    },
                )
                db.commit()
            except SQLAlchemyError:
                db.rollback()
            message = "database commit failed; previous configuration restored"
            if rollback_error:
                message += f"; {rollback_error}"
            raise ApplyFailed(message) from exc
        return {
            "status": result_status,
            "backup": previous_backup.name if previous_backup else None,
            "validation": validation.output or "ok",
        }

    def safe_apply(self, db: Session) -> dict[str, Any]:
        with self._operation_lock():
            self.generate_staging(db)
            return self._apply_staging_locked(
                db, audit_action="Apply Config", result_status="applied"
            )

    def reconcile_with_database(self, db: Session) -> dict[str, Any]:
        """Converge the formal config to committed DB state after an interrupted apply."""
        with self._operation_lock():
            expected = self.render(db)
            reconciliation_pending = os.path.lexists(self.settings.reconcile_marker)
            if self._formal_config_matches(expected) and not reconciliation_pending:
                return {"status": "in-sync"}
            if not reconciliation_pending:
                self._write_json(
                    self.settings.reconcile_marker,
                    {"schema_version": 1, "state": "pending"},
                )
            self._write_json(self.settings.staging_config, expected)
            result = self._apply_staging_locked(
                db,
                audit_action="Startup Config Reconciled",
                result_status="reconciled",
            )
            self.settings.reconcile_marker.unlink()
            self._fsync_directory(self.settings.reconcile_marker.parent)
            return result

    def restore_backup(self, db: Session, backup_name: str | None = None) -> dict[str, Any]:
        with self._operation_lock():
            backups = self.list_backups()
            selected = backup_name or (backups[0] if backups else None)
            if not selected or selected not in backups or Path(selected).name != selected:
                raise SingBoxError("backup not found")
            source = self.settings.singbox_backup_dir / selected
            state_source = self._state_path(source)
            if not state_source.is_file():
                raise SingBoxError("backup state sidecar is missing; refusing unsafe restore")
            try:
                selected_config = json.loads(source.read_text(encoding="utf-8"))
                selected_state = self._validate_state_payload(
                    json.loads(state_source.read_text(encoding="utf-8"))
                )
            except (OSError, TypeError, ValueError) as exc:
                raise SingBoxError("backup files are unreadable or invalid") from exc
            if not isinstance(selected_config, dict):
                raise SingBoxError("backup configuration is invalid")
            had_current_config = self.settings.singbox_config.exists()
            current_backup = self.backup_current(db)
            installed = False
            try:
                self._restore_database_state(db, selected_state)
                regenerated_config = self.render(db)
                self._write_json(self.settings.staging_config, regenerated_config)
                self.validate_file(self.settings.staging_config)
                self._install_staging()
                installed = True
                self.restart()
                record_audit(db, "Restore Backup", {"source": selected})
                db.commit()
            except (
                ConfigBuildError,
                SingBoxError,
                SQLAlchemyError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                db.rollback()
                rollback_error: str | None = None
                if installed:
                    if current_backup:
                        self._atomic_copy(current_backup, self.settings.singbox_config)
                    elif not had_current_config:
                        self.settings.singbox_config.unlink(missing_ok=True)
                    try:
                        self.restart()
                    except SingBoxError as rollback_exc:
                        rollback_error = str(rollback_exc)
                try:
                    record_audit(
                        db,
                        "Rollback",
                        {
                            "reason": str(exc),
                            "source": selected,
                            "rollback_error": rollback_error,
                        },
                    )
                    db.commit()
                except SQLAlchemyError:
                    db.rollback()
                raise ApplyFailed(
                    "restore failed; current configuration and deployment identity recovered"
                ) from exc
            return {"status": "restored", "backup": selected}

    def tail_logs(self, lines: int = 200) -> list[str]:
        log_path = self.settings.log_dir / "sing-box.log"
        if not log_path.exists():
            return []
        with log_path.open("r", encoding="utf-8", errors="replace") as stream:
            return [redact_text(line.rstrip("\r\n")) for line in deque(stream, maxlen=lines)]
