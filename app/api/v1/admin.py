"""Platform admin area (`/admin`): every workspace, for the people who run RecoEngine.

Needs a dashboard session of a user with `is_platform_admin` (granted with
scripts/platform_admin.py). Separate from `/tenants`, which is for scripts holding the
ADMIN_API_KEY.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.middleware.auth import PlatformAdminDep
from app.schemas.admin import (
    LimitsUpdate,
    PlatformOverview,
    SuspendRequest,
    WorkspaceDetail,
    WorkspaceList,
    WorkspaceSort,
    WorkspaceStatus,
)
from app.schemas.common import ErrorResponse
from app.services.admin_service import AdminServiceDep

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "No such workspace"}
}


@router.get("/overview", summary="Platform totals")
async def overview(_: PlatformAdminDep, service: AdminServiceDep) -> PlatformOverview:
    return await service.overview()


@router.get("/workspaces", summary="Every workspace, with usage")
async def list_workspaces(
    _: PlatformAdminDep,
    service: AdminServiceDep,
    search: Annotated[str | None, Query(max_length=200, description="Name or email")] = None,
    status: WorkspaceStatus | None = None,
    sort: WorkspaceSort = WorkspaceSort.NEWEST,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> WorkspaceList:
    return await service.list_workspaces(
        search=search, status=status, sort=sort, page=page, page_size=page_size
    )


@router.get("/workspaces/{tenant_id}", summary="One workspace in detail", responses=_NOT_FOUND)
async def get_workspace(
    tenant_id: uuid.UUID, _: PlatformAdminDep, service: AdminServiceDep
) -> WorkspaceDetail:
    return await service.get_workspace(tenant_id)


@router.post(
    "/workspaces/{tenant_id}/suspend",
    summary="Suspend a workspace",
    responses={
        **_NOT_FOUND,
        400: {"model": ErrorResponse, "description": "Your own workspace"},
    },
)
async def suspend_workspace(
    tenant_id: uuid.UUID,
    payload: SuspendRequest,
    admin: PlatformAdminDep,
    service: AdminServiceDep,
) -> WorkspaceDetail:
    return await service.suspend(tenant_id, payload.reason, admin)


@router.post(
    "/workspaces/{tenant_id}/activate", summary="Reactivate a workspace", responses=_NOT_FOUND
)
async def activate_workspace(
    tenant_id: uuid.UUID, admin: PlatformAdminDep, service: AdminServiceDep
) -> WorkspaceDetail:
    return await service.activate(tenant_id, admin)


@router.patch("/workspaces/{tenant_id}/limits", summary="Set limits", responses=_NOT_FOUND)
async def update_limits(
    tenant_id: uuid.UUID,
    payload: LimitsUpdate,
    admin: PlatformAdminDep,
    service: AdminServiceDep,
) -> WorkspaceDetail:
    return await service.update_limits(tenant_id, payload, admin)
