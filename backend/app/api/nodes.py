from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_singbox_service, require_csrf, require_ready_admin
from app.core.logging import redact_data
from app.models import Admin, ProxyNode, Settings
from app.schemas.node import NodeCreate, NodeResponse, NodeUpdate
from app.services.audit import record_audit
from app.services.credentials import (
    default_node_config,
    ensure_ss2022_key_matches_method,
    regenerate_node_credentials,
)
from app.services.singbox_config import ConfigBuildError, build_singbox_config
from app.services.singbox_service import (
    ApplyFailed,
    ConfigValidationFailed,
    SingBoxError,
    SingBoxService,
    SingBoxUnavailable,
)

router = APIRouter(prefix="/nodes", tags=["nodes"])


def _merge_config(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in updates.items():
        if value == "[REDACTED]":
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def _validate_node(node: ProxyNode) -> None:
    placeholder_settings = Settings(
        id=1,
        subscription_token="placeholder",
        certificate_path="/tmp/cert.pem",
        private_key_path="/tmp/key.pem",
    )
    build_singbox_config([node], placeholder_settings)


def _response(node: ProxyNode) -> NodeResponse:
    return NodeResponse(
        id=node.id,
        name=node.name,
        protocol=node.protocol,
        enabled=node.enabled,
        listen_port=node.listen_port,
        config_json=redact_data(node.config_json),
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _get_node(db: Session, node_id: int) -> ProxyNode:
    node = db.get(ProxyNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="node name already exists") from exc


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


@router.get("", response_model=list[NodeResponse])
def list_nodes(
    _admin: Admin = Depends(require_ready_admin), db: Session = Depends(get_db)
) -> list[NodeResponse]:
    return [_response(node) for node in db.scalars(select(ProxyNode).order_by(ProxyNode.id))]


@router.get("/{node_id}", response_model=NodeResponse)
def get_node(
    node_id: int,
    _admin: Admin = Depends(require_ready_admin),
    db: Session = Depends(get_db),
) -> NodeResponse:
    return _response(_get_node(db, node_id))


@router.post("", response_model=NodeResponse, status_code=status.HTTP_201_CREATED)
def create_node(
    payload: NodeCreate,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> NodeResponse:
    config = default_node_config(payload.protocol)
    if payload.config_json:
        config = _merge_config(config, payload.config_json)
    if payload.protocol == "shadowsocks":
        try:
            config = ensure_ss2022_key_matches_method(config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    node = ProxyNode(
        name=payload.name,
        protocol=payload.protocol,
        enabled=payload.enabled,
        listen_port=payload.listen_port,
        config_json=config,
    )
    try:
        _validate_node(node)
    except ConfigBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(node)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="node name already exists") from exc
    record_audit(db, "Node Created", {"node_id": node.id, "protocol": node.protocol})
    _apply_or_error(db, service)
    return _response(node)


@router.put("/{node_id}", response_model=NodeResponse)
def update_node(
    node_id: int,
    payload: NodeUpdate,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> NodeResponse:
    node = _get_node(db, node_id)
    changes = payload.model_dump(exclude_unset=True)
    if "config_json" in changes and changes["config_json"] is not None:
        changes["config_json"] = _merge_config(node.config_json, changes["config_json"])
        if node.protocol == "shadowsocks":
            try:
                changes["config_json"] = ensure_ss2022_key_matches_method(
                    changes["config_json"]
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key, value in changes.items():
        if value is not None:
            setattr(node, key, value)
    try:
        _validate_node(node)
    except ConfigBuildError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_audit(db, "Node Updated", {"node_id": node.id})
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="node name already exists") from exc
    _apply_or_error(db, service)
    return _response(node)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(
    node_id: int,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> None:
    node = _get_node(db, node_id)
    record_audit(db, "Node Deleted", {"node_id": node.id, "protocol": node.protocol})
    db.delete(node)
    _apply_or_error(db, service)


def _set_enabled(
    db: Session, service: SingBoxService, node_id: int, enabled: bool
) -> NodeResponse:
    node = _get_node(db, node_id)
    node.enabled = enabled
    record_audit(db, "Node Enabled" if enabled else "Node Disabled", {"node_id": node.id})
    _apply_or_error(db, service)
    return _response(node)


@router.post("/{node_id}/enable", response_model=NodeResponse)
def enable_node(
    node_id: int,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> NodeResponse:
    return _set_enabled(db, service, node_id, True)


@router.post("/{node_id}/disable", response_model=NodeResponse)
def disable_node(
    node_id: int,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> NodeResponse:
    return _set_enabled(db, service, node_id, False)


@router.post("/{node_id}/regenerate-secret", response_model=NodeResponse)
def regenerate_secret(
    node_id: int,
    _admin: Admin = Depends(require_ready_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: SingBoxService = Depends(get_singbox_service),
) -> NodeResponse:
    node = _get_node(db, node_id)
    node.config_json = regenerate_node_credentials(node.protocol, node.config_json)
    record_audit(db, "Secret Regenerated", {"node_id": node.id})
    _apply_or_error(db, service)
    return _response(node)
