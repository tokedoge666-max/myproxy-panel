from __future__ import annotations

import ipaddress
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.?$"
)


class SettingsUpdate(BaseModel):
    server_name: str | None = Field(default=None, min_length=1, max_length=128)
    server_ipv4: str | None = None
    domain: str | None = None
    certificate_path: str | None = Field(default=None, max_length=1024)
    private_key_path: str | None = Field(default=None, max_length=1024)
    self_signed_mode: bool | None = None

    @field_validator("server_name")
    @classmethod
    def validate_server_name(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("server_name must not be blank or null")
        return value.strip()

    @field_validator("server_ipv4")
    @classmethod
    def validate_ipv4(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        return str(ipaddress.IPv4Address(value))

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        value = value.strip().rstrip(".")
        if "://" in value or "/" in value or not _DOMAIN_RE.fullmatch(value):
            raise ValueError("domain must be a hostname without scheme or path")
        return value.lower()

    @field_validator("certificate_path", "private_key_path")
    @classmethod
    def validate_linux_absolute_path(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if "\x00" in value or "\n" in value or "\r" in value or not value.startswith("/"):
            raise ValueError("TLS paths must be absolute Linux paths without control characters")
        return value

    @model_validator(mode="after")
    def validate_tls_paths(self) -> SettingsUpdate:
        if self.self_signed_mode is False:
            supplied_cert = self.certificate_path is not None
            supplied_key = self.private_key_path is not None
            if supplied_cert != supplied_key:
                raise ValueError("certificate and private key paths must be supplied together")
        return self


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_name: str
    server_ipv4: str | None
    domain: str | None
    certificate_path: str | None
    private_key_path: str | None
    self_signed_mode: bool
    created_at: datetime
    updated_at: datetime
