from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_csrf, require_ready_admin
from app.models import Admin, Settings
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_settings(db: Session) -> Settings:
    settings = db.get(Settings, 1)
    if settings is None:
        raise HTTPException(status_code=500, detail="settings are not initialized")
    return settings


@router.get("", response_model=SettingsResponse)
def get_settings(
    _admin: Admin = Depends(require_ready_admin), db: Session = Depends(get_db)
) -> SettingsResponse:
    return SettingsResponse.model_validate(_get_settings(db))


@router.put("", response_model=SettingsResponse)
def update_settings(
    payload: SettingsUpdate,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SettingsResponse:
    settings = _get_settings(db)
    changes = payload.model_dump(exclude_unset=True)
    deployment_managed = {
        "server_ipv4",
        "domain",
        "certificate_path",
        "private_key_path",
        "self_signed_mode",
    }
    changed_deployment_fields = sorted(
        key
        for key in deployment_managed.intersection(changes)
        if changes[key] != getattr(settings, key)
    )
    if changed_deployment_fields:
        raise HTTPException(
            status_code=409,
            detail=(
                "deployment-managed settings require deploy/reconfigure.sh: "
                + ", ".join(changed_deployment_fields)
            ),
        )
    for key, value in changes.items():
        setattr(settings, key, value)
    record_audit(
        db,
        "Settings Updated",
        {
            "server_name": settings.server_name,
            "domain": settings.domain,
            "self_signed_mode": settings.self_signed_mode,
        },
    )
    db.commit()
    return SettingsResponse.model_validate(settings)
