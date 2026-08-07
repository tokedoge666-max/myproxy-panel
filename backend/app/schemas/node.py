from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.node_names import normalize_node_name

Protocol = Literal["hysteria2", "tuic", "shadowsocks"]


class NodeNameMixin(BaseModel):
    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return normalize_node_name(value) if isinstance(value, str) else value


class NodeCreate(NodeNameMixin):
    name: str = Field(min_length=1, max_length=128)
    protocol: Protocol
    enabled: bool = True
    listen_port: int = Field(ge=1024, le=65535)
    config_json: dict[str, Any] | None = None


class NodeUpdate(NodeNameMixin):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    listen_port: int | None = Field(default=None, ge=1024, le=65535)
    config_json: dict[str, Any] | None = None


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    protocol: Protocol
    enabled: bool
    listen_port: int
    config_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
