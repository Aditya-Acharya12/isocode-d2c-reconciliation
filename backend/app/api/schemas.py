from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.permissions import Permission, Role
from app.models.enums import PartnerType


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: Role


class SessionOut(BaseModel):
    user: UserOut
    permissions: list[Permission]
    demo_mode: bool


class WorkspaceSummaryOut(BaseModel):
    partners: int
    sales_channels: int
    uploaded_files: int
    orders: int
    reconciliation_runs: int
    open_claims: int


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: PartnerType
    settlement_terms_days: int
    return_grace_days: int
    is_active: bool
    updated_at: datetime


class PartnerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    type: PartnerType
    settlement_terms_days: int = Field(ge=0, le=365)
    return_grace_days: int = Field(ge=0, le=365)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Name must have at least 2 characters")
        return value


class PartnerUpdate(BaseModel):
    settlement_terms_days: int | None = Field(default=None, ge=0, le=365)
    return_grace_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_email: str | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - PEP 695 syntax needs Python 3.12
    items: list[T]
    total: int
    limit: int
    offset: int


class UploadProbeOut(BaseModel):
    bytes_received: int
    sha256: str
    duration_ms: int
