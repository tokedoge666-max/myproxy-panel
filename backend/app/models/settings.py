from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    server_name: Mapped[str] = mapped_column(String(128), default="MyProxy Panel", nullable=False)
    server_ipv4: Mapped[str | None] = mapped_column(String(45), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    certificate_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    private_key_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    subscription_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    self_signed_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
