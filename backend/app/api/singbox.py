from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    get_db,
    get_singbox_service,
    require_csrf,
    require_ready_admin,
)
from app.models import Admin
from app.services.audit import record_audit
from app.services.singbox_config import ConfigBuildError
from app.services.singbox_service import (
    ApplyFailed,
    ConfigValidationFailed,
    SingBoxError,
    SingBoxService,
    SingBoxUnavailable,
)

router = APIRouter(prefix="/singbox", tags=["sing-box"])


class RestoreRequest(BaseModel):
    backup: str | None = Field(default=None, max_length=255)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ConfigBuildError, ConfigValidationFailed)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, SingBoxUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ApplyFailed):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/status")
def status_endpoint(
    _admin: Admin = Depends(require_ready_admin),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, str]:
    try:
        return {"status": service.status(), "version": service.version()}
    except SingBoxError as exc:
        raise _http_error(exc) from exc


@router.post("/check")
def check_endpoint(
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, object]:
    try:
        result = service.check_generated(db)
        return {"valid": True, "output": result.output or "ok"}
    except (ConfigBuildError, SingBoxError) as exc:
        raise _http_error(exc) from exc


@router.post("/apply")
def apply_endpoint(
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, object]:
    try:
        return service.safe_apply(db)
    except (ConfigBuildError, SingBoxError) as exc:
        raise _http_error(exc) from exc


@router.post("/restart")
def restart_endpoint(
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, str]:
    try:
        service.restart()
    except SingBoxError as exc:
        raise _http_error(exc) from exc
    record_audit(db, "Restart", {})
    db.commit()
    return {"status": "running"}


@router.post("/restore")
def restore_endpoint(
    payload: RestoreRequest | None = None,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, object]:
    try:
        return service.restore_backup(db, payload.backup if payload else None)
    except SingBoxError as exc:
        raise _http_error(exc) from exc


@router.get("/backups")
def backups_endpoint(
    _admin: Admin = Depends(require_ready_admin),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, list[dict[str, str | int]]]:
    return {"backups": service.backup_info()}


@router.get("/logs")
def logs_endpoint(
    lines: int = Query(default=200, ge=1, le=1000),
    _admin: Admin = Depends(require_ready_admin),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, object]:
    output = service.tail_logs(lines)
    return {"lines": output, "count": len(output)}
