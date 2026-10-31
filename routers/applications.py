import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, status

from dependencies import get_admin_key, get_db
from models.requests import ApplicationCreateRequest
from models.responses import ApplicationResponse

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/applications", tags=["Applications"])


@router.post(
    "",
    status_code=201,
    summary="Create application",
    description="Create a new application.",
)
async def create_application(
    data: ApplicationCreateRequest,
    _api_key: Annotated[str, Depends(get_admin_key)],
    db: Annotated[Database, Depends(get_db)],
) -> ApplicationResponse:
    """Create a new application."""
    app_id = app_name = data.name.strip().lower()
    created_id = await db.application.create(application_id=app_id, name=app_name)
    return ApplicationResponse(id=created_id, name=app_name)


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete application",
    description="Delete an unused application.",
)
async def delete_application(
    application_id: Annotated[str, Path(description="The ID of the application")],
    _api_key: Annotated[str, Depends(get_admin_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    """Delete an unused application."""
    await db.application.delete(application_id)
