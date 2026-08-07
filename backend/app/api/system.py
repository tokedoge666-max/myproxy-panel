from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_singbox_service, require_ready_admin
from app.models import Admin
from app.services.singbox_service import SingBoxService
from app.services.system_status import get_system_status

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
def system_status(
    request: Request,
    _admin: Admin = Depends(require_ready_admin),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, object]:
    return get_system_status(db, service, request.app.state.settings.home)
