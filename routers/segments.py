import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from database.content_state import read_at_revision
from dependencies import OptionalAppHeader, get_api_key, get_db, get_storage
from exceptions import DataNotFoundError
from models.annotation import SegmentWithContextOutput
from models.requests import DirectRelatedSegmentsQueryParams
from models.responses import PaginatedResponse

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database import Database
    from database.content_state import ContentState
    from storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/segments", tags=["Segments"])


@router.get(
    "/{segment_id}/related",
    summary="Get related segments",
    description="Retrieve segments related to a given segment.",
)
async def get_related(
    segment_id: Annotated[str, Path(description="The ID of the segment")],
    params: Annotated[DirectRelatedSegmentsQueryParams, Query()],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> PaginatedResponse[SegmentWithContextOutput]:
    """Get related segments."""
    try:
        segment = await db.segment.get(segment_id)
    except DataNotFoundError:
        return PaginatedResponse.from_items([], offset=params.offset, limit=params.limit)
    segments = await db.segment.get_related(
        edition_id=segment.edition_id,
        spans=[],
        starting_segment_id=segment_id,
        application=x_application,
        offset=params.offset,
        limit=params.limit + 1,
        filters=params,
    )
    return PaginatedResponse.from_items(segments, offset=params.offset, limit=params.limit)


@router.get(
    "/{segment_id}/content",
    summary="Get segment content",
    description="Retrieve the text content of a segment.",
)
async def get_segment_content(
    segment_id: Annotated[str, Path(description="The ID of the segment")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    x_application: OptionalAppHeader = None,
) -> str:
    """Get segment content."""

    async def read(tx: AsyncManagedTransaction) -> tuple[ContentState, SegmentWithContextOutput]:
        result = await tx.run(
            """
            MATCH (:Segment {id: $id})-[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(e:Edition)
            RETURN e.id AS id
            """,
            id=segment_id,
        )
        parent = await result.single()
        if parent is None:
            raise DataNotFoundError(f"Segment '{segment_id}' not found")
        return await read_at_revision(
            tx, parent["id"], lambda tx: db.segment.get_with_transaction(tx, segment_id, x_application)
        )

    async with db.get_session() as session:
        state, segment = await session.execute_read(read)
    base_text = await storage.read_text(state.object_key)
    return base_text[segment.span.start : segment.span.end]


@router.post(
    "/{segment_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add tag to segment",
    description="Add a tag to a segment.",
)
async def tag_segment(
    segment_id: Annotated[str, Path(description="The ID of the segment")],
    tag_id: Annotated[str, Path(description="The ID of the tag")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> None:
    """Add a tag to a segment."""
    await db.tag.tag_segment(segment_id, tag_id, application=x_application)


@router.delete(
    "/{segment_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tag from segment",
    description="Remove a tag from a segment.",
)
async def untag_segment(
    segment_id: Annotated[str, Path(description="The ID of the segment")],
    tag_id: Annotated[str, Path(description="The ID of the tag")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> None:
    """Remove a tag from a segment."""
    await db.tag.untag_segment(segment_id, tag_id, application=x_application)


@router.get(
    "/{segment_id}",
    summary="Get segment",
    description="Retrieve a segment with edition, text, segmentation, lines, and tag context.",
    response_model_exclude_none=True,
)
async def get_segment(
    segment_id: Annotated[str, Path(description="The ID of the segment")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: OptionalAppHeader = None,
) -> SegmentWithContextOutput:
    """Get a segment with context."""
    return await db.segment.get(segment_id, application=x_application)
