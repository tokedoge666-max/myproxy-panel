from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import AppSettings
from app.core.security import digest_secret, secrets_match
from app.db import utcnow
from app.models import Admin, AuthSession
from app.services.singbox_service import SingBoxService


def get_db(request: Request) -> Iterator[Session]:
    yield from request.app.state.database.session()


def get_app_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_singbox_service(request: Request) -> SingBoxService:
    return request.app.state.singbox_service


@dataclass(slots=True)
class AuthContext:
    admin: Admin
    session: AuthSession


def get_auth_context(
    request: Request, db: Session = Depends(get_db)
) -> AuthContext:
    app_settings: AppSettings = request.app.state.settings
    raw_token = request.cookies.get(app_settings.session_cookie)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    auth_session = db.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.admin))
        .where(AuthSession.token_hash == digest_secret(raw_token))
    )
    now = utcnow()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    return AuthContext(auth_session.admin, auth_session)


def get_current_admin(context: AuthContext = Depends(get_auth_context)) -> Admin:
    return context.admin


def require_ready_admin(context: AuthContext = Depends(get_auth_context)) -> Admin:
    if context.admin.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="password change required"
        )
    return context.admin


def require_csrf(
    request: Request, context: AuthContext = Depends(get_auth_context)
) -> None:
    settings: AppSettings = request.app.state.settings
    header_token = request.headers.get("X-CSRF-Token")
    cookie_token = request.cookies.get(settings.csrf_cookie)
    if (
        not header_token
        or not cookie_token
        or header_token != cookie_token
        or not secrets_match(header_token, context.session.csrf_token_hash)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
