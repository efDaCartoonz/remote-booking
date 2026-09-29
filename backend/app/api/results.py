from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.admin.repository import AdministrativeRepository, ConnectionResult
from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.db import get_db

router = APIRouter(prefix="/api/v1/cards", tags=["cards"])


class ActiveResult(BaseModel):
    code: int
    name: str


class ActiveResultsResponse(BaseModel):
    items: list[ActiveResult]


def get_active_results(
    connection: Annotated[object, Depends(get_db)],
) -> list[ConnectionResult]:
    return AdministrativeRepository(connection).list_active_results()


@router.get("/results", response_model=ActiveResultsResponse)
def list_active_results(
    _user: Annotated[
        UserAuthRecord,
        Depends(require_roles(int(RoleId.L2), int(RoleId.MANAGER))),
    ],
    results: Annotated[list[ConnectionResult], Depends(get_active_results)],
) -> ActiveResultsResponse:
    return ActiveResultsResponse(
        items=[ActiveResult(code=item.code, name=item.name) for item in results]
    )
