import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request, status

from catalog_search import CatalogSearchService
from content_service import create_edition as publish_edition
from dependencies import OptionalAppHeader, get_api_key, get_catalog_search, get_db, get_storage
from exceptions import ServiceUnavailableError
from models.edition import EditionOutput
from models.requests import EditionRequestModel, EditionsQueryParams, TextsQueryParams
from models.responses import IdResponse, PaginatedResponse
from models.text import TextInput, TextOutput, TextPatch
from search_updates import update_search

if TYPE_CHECKING:
    from database import Database
    from storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/texts", tags=["Texts"])


@router.get(
    "",
    summary="List all texts",
    description="Retrieve a paginated list of texts (texts) with optional filters.",
)
async def get_all_texts(
    params: Annotated[TextsQueryParams, Query()],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    catalog_search: Annotated[CatalogSearchService | None, Depends(get_catalog_search)],
    x_application: OptionalAppHeader = None,
) -> PaginatedResponse[TextOutput]:
    """List all texts with optional filtering."""
    if params.title:
        if catalog_search is None:
            raise ServiceUnavailableError("Catalog search is required for text title search")
        texts = await catalog_search.search_texts(
            db=db,
            query=params.title,
            application=x_application,
            filters=params,
            offset=params.offset,
            limit=params.limit + 1,
        )
        return PaginatedResponse.from_items(texts, offset=params.offset, limit=params.limit)

    texts = await db.text.get_all(
        offset=params.offset,
        limit=params.limit + 1,
        filters=params,  # TextsQueryParams inherits from TextFilter, so this works
        application=x_application,
    )
    return PaginatedResponse.from_items(texts, offset=params.offset, limit=params.limit)


@router.get(
    "/{text_id}",
    summary="Get a text",
    description="Retrieve a single text (text) by its ID.",
    responses={404: {"description": "Text not found"}},
)
async def get_text(
    text_id: Annotated[str, Path(description="The ID of the text to retrieve")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> TextOutput:
    """Fetch a text (text) by its ID."""
    return await db.text.get(text_id=text_id, application=x_application)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a text",
    description="Create a new text (text) with the provided data.",
)
async def create_text(
    request: Request,
    background_tasks: BackgroundTasks,
    data: TextInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> IdResponse:
    """Create a new text (text)."""
    text_id = await db.text.create(data, application=x_application)
    background_tasks.add_task(update_search, request, "text", text_id)
    logger.info("Successfully created text with ID: %s", text_id)
    return IdResponse(id=text_id)


@router.get(
    "/{text_id}/editions",
    summary="List editions for a text",
    description="Retrieve all editions (editions) for a given text.",
)
async def get_editions(
    text_id: Annotated[str, Path(description="The ID of the text")],
    params: Annotated[EditionsQueryParams, Query()],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[EditionOutput]:
    """List all editions for a text."""
    return await db.edition.get_all(
        text_id=text_id,
        edition_type=params.edition_type,
    )


@router.post(
    "/{text_id}/editions",
    status_code=status.HTTP_201_CREATED,
    summary="Create an edition",
    description="Create a new edition (edition) for a text.",
)
async def create_edition(
    request: Request,
    background_tasks: BackgroundTasks,
    text_id: Annotated[str, Path(description="The ID of the text")],
    data: EditionRequestModel,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> IdResponse:
    """Create a new edition for a text."""
    edition_id = await publish_edition(db, storage, text_id, data)
    background_tasks.add_task(update_search, request, "edition", edition_id)
    return IdResponse(id=edition_id)


@router.patch(
    "/{text_id}",
    summary="Update a text",
    description="Partially update a text with the provided data.",
)
async def update_text(
    request: Request,
    background_tasks: BackgroundTasks,
    text_id: Annotated[str, Path(description="The ID of the text to update")],
    data: TextPatch,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> TextOutput:
    """Update a text."""
    logger.info("Updating text %s with: %s", text_id, data.model_dump_json())
    text = await db.text.update(text_id, data, application=x_application)
    background_tasks.add_task(update_search, request, "text", text_id)
    return text


@router.delete(
    "/{text_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete text",
    description="Delete a text if it has no editions or incoming text relationships.",
)
async def delete_text(
    request: Request,
    background_tasks: BackgroundTasks,
    text_id: Annotated[str, Path(description="The ID of the text to delete")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    """Delete a text."""
    await db.text.delete(text_id)
    background_tasks.add_task(update_search, request, "text", text_id)


@router.post(
    "/{text_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add tag to text",
    description="Add a tag to a text.",
)
async def tag_text(
    request: Request,
    background_tasks: BackgroundTasks,
    text_id: Annotated[str, Path(description="The ID of the text")],
    tag_id: Annotated[str, Path(description="The ID of the tag to add")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> None:
    """Add a tag to a text."""
    work_id = await db.text.get_work_id(text_id)
    await db.tag.tag_work(work_id, tag_id, application=x_application)
    background_tasks.add_task(update_search, request, "work", work_id)


@router.delete(
    "/{text_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tag from text",
    description="Remove a tag from a text.",
)
async def untag_text(
    request: Request,
    background_tasks: BackgroundTasks,
    text_id: Annotated[str, Path(description="The ID of the text")],
    tag_id: Annotated[str, Path(description="The ID of the tag to remove")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> None:
    """Remove a tag from a text."""
    work_id = await db.text.get_work_id(text_id)
    await db.tag.untag_work(work_id, tag_id, application=x_application)
    background_tasks.add_task(update_search, request, "work", work_id)
