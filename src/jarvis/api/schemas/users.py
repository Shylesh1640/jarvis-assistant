"""Request/response schemas for the /users and /roles API (Phase 11)."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr | None = None
    display_name: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=8)
    role_id: str = "user"
    is_active: bool = True


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    display_name: str | None = Field(None, min_length=1, max_length=128)
    password: str | None = Field(None, min_length=8)
    role_id: str | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    user_id: str
    email: str | None
    display_name: str
    role_id: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str | None
    totp_enabled: bool = False


class RoleCreate(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    role_name: str = Field(..., min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    role_name: str | None = Field(None, min_length=1, max_length=64)
    permissions: list[str] | None = None


class RoleResponse(BaseModel):
    role_id: str
    role_name: str
    permissions: list[str]
    created_at: str
    updated_at: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)
    totp_code: str | None = Field(None, min_length=6, max_length=6, pattern=r"^\d{6}$")
    remember_device: bool = False


class LoginResponse(BaseModel):
    user: UserResponse
    session_id: str
    session_token: str
    requires_2fa: bool = False


class RefreshRequest(BaseModel):
    session_id: str
    session_token: str


# 2FA Schemas
class TwoFAEnrollResponse(BaseModel):
    secret: str
    qr_code_uri: str
    recovery_codes: list[str]


class TwoFAVerifyEnrollRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TwoFADisableRequest(BaseModel):
    password: str = Field(..., min_length=1)
    code: str | None = Field(None, min_length=6, max_length=6, pattern=r"^\d{6}$")


class TwoFARegenerateRecoveryRequest(BaseModel):
    password: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


__all__ = [
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "RoleCreate",
    "RoleUpdate",
    "RoleResponse",
    "LoginRequest",
    "LoginResponse",
    "RefreshRequest",
    "TwoFAEnrollResponse",
    "TwoFAVerifyEnrollRequest",
    "TwoFADisableRequest",
    "TwoFARegenerateRecoveryRequest",
]