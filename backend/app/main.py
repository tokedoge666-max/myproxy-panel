from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api import auth, nodes, singbox, subscription, system
from app.api import settings as settings_api
from app.core.config import AppSettings, get_settings
from app.core.logging import configure_logging
from app.db import Database
from app.db.init import initialize_database, validate_initialized_database
from app.db.migrations import upgrade_database
from app.services.singbox_service import ListenerChecker, Runner, SingBoxService


def create_app(
    settings: AppSettings | None = None,
    *,
    runner: Runner | None = None,
    listener_checker: ListenerChecker | None = None,
    bootstrap_password: str | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    database = Database(runtime_settings.database_url, runtime_settings.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            runtime_settings.ensure_runtime_directories()
            configure_logging(runtime_settings.log_dir)
            if runtime_settings.environment == "production":
                if (
                    not runtime_settings.database_path.is_file()
                    or runtime_settings.database_path.is_symlink()
                ):
                    raise RuntimeError(
                        "production database is missing; restore a backup before starting the API"
                    )
                upgrade_database(runtime_settings.database_url)
                validate_initialized_database(database)
                with database.session_factory() as session:
                    application.state.singbox_service.reconcile_with_database(session)
                credentials = None
            else:
                credentials = initialize_database(
                    database,
                    initial_password=bootstrap_password or os.getenv("MYPROXY_ADMIN_PASSWORD"),
                )
            application.state.initial_credentials = credentials
            yield
        finally:
            database.dispose()

    docs_enabled = runtime_settings.environment != "production"
    application = FastAPI(
        title="MyProxy Panel API",
        version=__version__,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.database = database
    application.state.singbox_service = SingBoxService(
        runtime_settings,
        runner=runner,
        listener_checker=listener_checker,
    )
    if runtime_settings.allowed_hosts != ("*",):
        application.add_middleware(
            TrustedHostMiddleware, allowed_hosts=list(runtime_settings.allowed_hosts)
        )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith(("/api/", "/sub/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @application.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    api_prefix = "/api/v1"
    application.include_router(auth.router, prefix=api_prefix)
    application.include_router(nodes.router, prefix=api_prefix)
    application.include_router(settings_api.router, prefix=api_prefix)
    application.include_router(system.router, prefix=api_prefix)
    application.include_router(singbox.router, prefix=api_prefix)
    application.include_router(subscription.api_router, prefix=api_prefix)
    application.include_router(subscription.public_router)
    return application


app = create_app()
