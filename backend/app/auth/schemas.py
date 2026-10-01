from pydantic import BaseModel, Field, field_validator
from zoneinfo import ZoneInfo


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)


class RoleResponse(BaseModel):
    id: int
    name: str


class CurrentUserResponse(BaseModel):
    id: int
    username: str
    full_name: str
    email: str | None
    roles: list[RoleResponse]
    timezone: str = "Asia/Yekaterinburg"


class LoginResponse(BaseModel):
    user: CurrentUserResponse


class TimezoneResponse(BaseModel):
    user_id: int
    timezone: str


class TimezoneUpdateRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_iana_timezone(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("invalid_timezone")
        try:
            ZoneInfo(trimmed)
        except Exception:
            raise ValueError("invalid_timezone")
        return trimmed
