import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request, status

from dependencies import RequiredAppHeader, get_api_key, get_db
from exceptions import DataNotFoundError
from models.responses import IdResponse
from models.tag import TagInput, TagOutput
from search_updates import update_search

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/tags", tags=["Tags"])


@router.get(
    "",
    summary="List tags",
    description="Retrieve all tags for an application.",
)
async def get_tags(
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> list[TagOutput]:
    """List all tags for an application."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    return await db.tag.get_all(application=x_application)


@router.post(
    "",
    status_code=201,
    summary="Create tag",
    description="Create a new tag for an application.",
)
async def create_tag(
    data: TagInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> IdResponse:
    """Create a new tag."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    tag_id = await db.tag.create(data, application=x_application)
    return IdResponse(id=tag_id)


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tag",
    description="Delete a tag from an application.",
)
async def delete_tag(
    request: Request,
    background_tasks: BackgroundTasks,
    tag_id: Annotated[str, Path(description="The ID of the tag")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> None:
    """Delete a tag."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    work_ids = await db.tag.delete(tag_id, application=x_application)
    for work_id in work_ids:
        background_tasks.add_task(update_search, request, "work", work_id)
