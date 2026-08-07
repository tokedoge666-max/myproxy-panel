from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_singbox_service, require_csrf, require_ready_admin
from app.models import Admin, ProxyNode, Settings
from app.services.audit import record_audit
from app.services.credentials import generate_subscription_token, regenerate_node_credentials
from app.services.singbox_config import ConfigBuildError
from app.services.singbox_service import (
    ApplyFailed,
    ConfigValidationFailed,
    SingBoxError,
    SingBoxService,
    SingBoxUnavailable,
)
from app.services.subscription import (
    generate_mihomo_subscription,
    generate_provider_subscription,
)

api_router = APIRouter(tags=["subscription"])
public_router = APIRouter(tags=["subscription"])


def _settings(db: Session) -> Settings:
    value = db.get(Settings, 1)
    if value is None:
        raise HTTPException(status_code=500, detail="settings are not initialized")
    return value


def _base_url(settings: Settings, request: Request) -> str:
    if settings.domain:
        return f"https://{settings.domain}"
    return str(request.base_url).rstrip("/")


def _subscription_info(settings: Settings, request: Request) -> dict[str, str | bool]:
    root = _base_url(settings, request)
    path = f"{root}/sub/{settings.subscription_token}"
    return {
        "mihomo_url": path,
        "provider_url": f"{path}?format=provider",
        "self_signed_mode": settings.self_signed_mode,
    }


def _apply_or_error(db: Session, service: SingBoxService) -> None:
    try:
        service.safe_apply(db)
    except (ConfigBuildError, ConfigValidationFailed) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SingBoxUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ApplyFailed, SingBoxError) as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@api_router.get("/subscription")
def subscription_info(
    request: Request,
    _admin: Admin = Depends(require_ready_admin),
    db: Session = Depends(get_db),
) -> dict[str, str | bool]:
    return _subscription_info(_settings(db), request)


@api_router.post("/subscription/rotate")
def rotate_subscription(
    request: Request,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, str | bool]:
    settings = _settings(db)
    settings.subscription_token = generate_subscription_token()
    record_audit(db, "Subscription Token Rotated", {})
    db.commit()
    return _subscription_info(settings, request)


@api_router.post("/credentials/regenerate-all")
def regenerate_all_credentials(
    request: Request,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> dict[str, object]:
    nodes = list(db.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
    for node in nodes:
        node.config_json = regenerate_node_credentials(node.protocol, node.config_json)
    settings = _settings(db)
    settings.subscription_token = generate_subscription_token()
    record_audit(db, "All Credentials Regenerated", {"node_count": len(nodes)})
    _apply_or_error(db, service)
    return {**_subscription_info(settings, request), "config_pending": False}


@public_router.get("/sub/{token}")
def public_subscription(
    token: str,
    db: Session = Depends(get_db),
    output_format: str = Query(default="mihomo", alias="format"),
) -> Response:
    settings = _settings(db)
    if not hmac.compare_digest(token, settings.subscription_token):
        raise HTTPException(status_code=404, detail="subscription not found")
    nodes = list(db.scalars(select(ProxyNode).order_by(ProxyNode.id)).all())
    try:
        if output_format == "mihomo":
            content = generate_mihomo_subscription(nodes, settings)
        elif output_format == "provider":
            content = generate_provider_subscription(nodes, settings)
        else:
            raise HTTPException(status_code=400, detail="unsupported subscription format")
    except ConfigBuildError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/yaml; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
