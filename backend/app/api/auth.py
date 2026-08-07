from __future__ import annotations

import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_auth_context, get_db, require_csrf
from app.core.config import AppSettings
from app.core.security import (
    digest_secret,
    generate_csrf_token,
    generate_session_token,
    hash_password,
    password_needs_rehash,
    validate_password_strength,
    verify_login_password,
    verify_password,
)
from app.db import utcnow
from app.models import Admin, AuthSession
from app.schemas.auth import (
    AdminResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["authentication"])
_login_verification_slots = threading.BoundedSemaphore(value=2)


def _set_auth_cookies(
    response: Response,
    settings: AppSettings,
    session_token: str,
    csrf_token: str,
) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": "strict",
        "path": "/",
        "max_age": settings.session_ttl_seconds,
    }
    response.set_cookie(
        settings.session_cookie, session_token, httponly=True, **common
    )
    response.set_cookie(settings.csrf_cookie, csrf_token, httponly=False, **common)
    response.headers["X-CSRF-Token"] = csrf_token
    response.headers["Cache-Control"] = "no-store"


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    settings: AppSettings = request.app.state.settings
    admin = db.scalar(select(Admin).where(Admin.username == payload.username))
    if not _login_verification_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
            headers={"Retry-After": "1"},
        )
    try:
        authenticated = verify_login_password(
            admin.password_hash if admin is not None else None, payload.password
        )
    finally:
        _login_verification_slots.release()
    if admin is None or not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password"
        )
    if password_needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(payload.password)

    now = utcnow()
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    session_token = generate_session_token()
    csrf_token = generate_csrf_token()
    auth_session = AuthSession(
        admin=admin,
        token_hash=digest_secret(session_token),
        csrf_token_hash=digest_secret(csrf_token),
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
    )
    admin.last_login = now
    db.add(auth_session)
    record_audit(db, "Login", {"admin_id": admin.id})
    db.commit()
    _set_auth_cookies(response, settings, session_token, csrf_token)
    return LoginResponse(user=AdminResponse.model_validate(admin), csrf_token=csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_auth_context),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    settings: AppSettings = request.app.state.settings
    context.session.revoked_at = utcnow()
    record_audit(db, "Logout", {"admin_id": context.admin.id})
    db.commit()
    response.delete_cookie(settings.session_cookie, path="/")
    response.delete_cookie(settings.csrf_cookie, path="/")


@router.get("/me", response_model=AdminResponse)
def me(context: AuthContext = Depends(get_auth_context)) -> AdminResponse:
    return AdminResponse.model_validate(context.admin)


@router.post("/change-password", response_model=AdminResponse)
def change_password(
    payload: ChangePasswordRequest,
    context: AuthContext = Depends(get_auth_context),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AdminResponse:
    admin = context.admin
    if not verify_password(admin.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail="current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="new password must be different")
    if admin.username.lower() in payload.new_password.lower():
        raise HTTPException(status_code=400, detail="new password must not contain username")
    try:
        validate_password_strength(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    admin.password_hash = hash_password(payload.new_password)
    admin.must_change_password = False
    db.execute(
        update(AuthSession)
        .where(AuthSession.admin_id == admin.id, AuthSession.id != context.session.id)
        .values(revoked_at=utcnow())
    )
    record_audit(db, "Password Changed", {"admin_id": admin.id})
    db.commit()
    return AdminResponse.model_validate(admin)
