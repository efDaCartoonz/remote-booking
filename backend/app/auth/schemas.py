from pydantic import BaseModel, Field


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


class LoginResponse(BaseModel):
    user: CurrentUserResponse
