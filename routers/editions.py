import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Path, Query, Request, UploadFile, status

from content_service import create_recording, edit_content
from database.content_state import read_state
from dependencies import get_api_key, get_db, get_storage
from models.alignment import EditionAlignmentOutput
from models.annotation import (
    BibliographicMetadataInput,
    BibliographicMetadataOutput,
    MarkInput,
    MarkOutput,
    NoteInput,
    NoteOutput,
    PaginationInput,
    PaginationOutput,
    SegmentationInput,
    SegmentationOutput,
    SegmentOutput,
    TableOfContentsInput,
    TableOfContentsOutput,
)
from models.content_operation import ContentOperation
from models.edition import EditionOutput
from models.recording import RecordingInput, RecordingOutput
from models.requests import AnnotationSegmentsPaginationParams
from models.responses import IdResponse, PaginatedResponse, PresignedUrlResponse
from search_updates import update_search

if TYPE_CHECKING:
    from database import Database
    from storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/editions", tags=["Editions"])

CONTENT_URL_EXPIRATION_SECONDS = 3600


@router.get(
    "/{edition_id}",
    summary="Get edition metadata",
    description="Retrieve metadata for a specific edition.",
)
async def get_metadata(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> EditionOutput:
    """Fetch metadata for an edition."""
    logger.info("Fetching metadata for edition %s", edition_id)
    return await db.edition.get(edition_id=edition_id)


@router.get(
    "/{edition_id}/content",
    summary="Get edition content",
    description="Retrieve the base text content of an edition.",
)
async def get_content(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    span_start: Annotated[int | None, Query(description="Start position for text slice")] = None,
    span_end: Annotated[int | None, Query(description="End position for text slice")] = None,
) -> str:
    """Fetch base text content for an edition."""
    async with db.get_session() as session:
        state = await session.execute_read(read_state, edition_id)
    base_text = await storage.read_text(state.object_key)

    if span_start is not None and span_end is not None:
        base_text = base_text[span_start:span_end]

    return base_text


@router.get(
    "/{edition_id}/content/url",
    summary="Get edition content URL",
    description="Generate a short-lived presigned URL for downloading the full base text directly from storage.",
)
async def get_content_url(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> PresignedUrlResponse:
    """Generate a direct download URL for an edition's full base text."""
    async with db.get_session() as session:
        state = await session.execute_read(read_state, edition_id)
    issued_at = datetime.now(UTC)
    url = await storage.generate_url(state.object_key, expires_in=CONTENT_URL_EXPIRATION_SECONDS)
    return PresignedUrlResponse(
        url=url,
        expires_at=issued_at + timedelta(seconds=CONTENT_URL_EXPIRATION_SECONDS),
    )


@router.get(
    "/{edition_id}/segmentation",
    summary="Get edition segmentation",
    description="Retrieve the edition's segmentation.",
    response_model_exclude_none=True,
)
async def get_segmentation_annotation(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> SegmentationOutput:
    return await db.annotation.segmentation.get_by_edition(edition_id)


@router.post(
    "/{edition_id}/segmentation",
    status_code=status.HTTP_201_CREATED,
    summary="Add edition segmentation",
    description="Add the edition's segmentation.",
)
async def post_segmentation_annotation(
    request: Request,
    background_tasks: BackgroundTasks,
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: SegmentationInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> IdResponse:
    """Add a segmentation annotation to an edition."""
    annotation_id = await db.annotation.segmentation.add(edition_id, data)
    background_tasks.add_task(update_search, request, "edition", edition_id)
    return IdResponse(id=annotation_id)


@router.get(
    "/{edition_id}/segmentation/segments",
    summary="Get edition segmentation segments",
    description="Retrieve paginated segments for the edition's segmentation.",
    response_model_exclude_none=True,
)
async def get_segmentation_segments(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    params: Annotated[AnnotationSegmentsPaginationParams, Query()],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> PaginatedResponse[SegmentOutput]:
    segments = await db.annotation.segmentation.get_segments_by_edition(
        edition_id,
        offset=params.offset,
        limit=params.limit + 1,
    )
    return PaginatedResponse.from_items(segments, offset=params.offset, limit=params.limit)


@router.delete(
    "/{edition_id}/segmentation",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete edition segmentation",
    description="Delete the edition's segmentation.",
)
async def delete_segmentation_annotation(
    request: Request,
    background_tasks: BackgroundTasks,
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    await db.annotation.segmentation.delete_by_edition(edition_id)
    background_tasks.add_task(update_search, request, "edition", edition_id)


@router.get(
    "/{edition_id}/alignments",
    summary="Get edition alignments",
    description="Retrieve directional alignment contexts that include this edition.",
)
async def get_alignment_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[EditionAlignmentOutput]:
    return await db.alignment.get_all_for_edition(edition_id)


@router.get(
    "/{edition_id}/pagination",
    summary="Get pagination annotations",
    description="Retrieve pagination annotation for an edition.",
)
async def get_pagination_annotation(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> PaginationOutput | None:
    return await db.annotation.pagination.get_all(edition_id)


@router.post(
    "/{edition_id}/pagination",
    status_code=status.HTTP_201_CREATED,
    summary="Add pagination annotation",
    description="Add a pagination annotation to an edition.",
)
async def post_pagination_annotation(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: PaginationInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> IdResponse:
    """Add a pagination annotation to an edition."""
    annotation_id = await db.annotation.pagination.add(edition_id, data)
    return IdResponse(id=annotation_id)


@router.get(
    "/{edition_id}/table-of-contents",
    summary="Get table of contents annotations",
    description="Retrieve all table of contents annotations for an edition.",
    response_model_exclude_none=True,
)
async def get_table_of_contents_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[TableOfContentsOutput]:
    return await db.annotation.table_of_contents.get_all(edition_id)


@router.post(
    "/{edition_id}/table-of-contents",
    status_code=status.HTTP_201_CREATED,
    summary="Add table of contents annotation",
    description="Add a table of contents annotation to an edition.",
)
async def post_table_of_contents_annotation(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: TableOfContentsInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> IdResponse:
    annotation_id = await db.annotation.table_of_contents.add(edition_id, data)
    return IdResponse(id=annotation_id)


@router.get(
    "/{edition_id}/bibliographic",
    summary="Get bibliographic metadata annotations",
    description="Retrieve all bibliographic metadata annotations for an edition.",
)
async def get_bibliographic_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[BibliographicMetadataOutput]:
    return await db.annotation.bibliographic.get_all(edition_id)


@router.post(
    "/{edition_id}/bibliographic",
    status_code=status.HTTP_201_CREATED,
    summary="Add bibliographic metadata annotation",
    description="Add bibliographic metadata annotation to an edition.",
)
async def post_bibliographic_annotation(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: BibliographicMetadataInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> IdResponse:
    annotation_id = await db.annotation.bibliographic.add(edition_id, data)
    return IdResponse(id=annotation_id)


@router.get(
    "/{edition_id}/durchens",
    summary="Get durchen annotations",
    description="Retrieve all durchen annotations for an edition.",
)
async def get_durchen_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[NoteOutput]:
    return await db.annotation.note.get_all(edition_id, "durchen")


@router.post(
    "/{edition_id}/durchens",
    status_code=status.HTTP_201_CREATED,
    summary="Add durchen annotations",
    description="Atomically add one or more durchen annotations to an edition.",
)
async def post_durchen_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: Annotated[list[NoteInput], Body(min_length=1)],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[IdResponse]:
    annotation_ids = await db.annotation.note.add_durchens(edition_id, data)
    return [IdResponse(id=annotation_id) for annotation_id in annotation_ids]


@router.get(
    "/{edition_id}/yigchungs",
    summary="Get yigchung annotations",
    description="Retrieve all yigchung mark annotations for an edition.",
)
async def get_yigchung_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[MarkOutput]:
    return await db.annotation.mark.get_all(edition_id)


@router.post(
    "/{edition_id}/yigchungs",
    status_code=status.HTTP_201_CREATED,
    summary="Add yigchung annotations",
    description="Atomically add one or more span-only yigchung mark annotations to an edition.",
)
async def post_yigchung_annotations(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: Annotated[list[MarkInput], Body(min_length=1)],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[IdResponse]:
    annotation_ids = await db.annotation.mark.add_yigchungs(edition_id, data)
    return [IdResponse(id=annotation_id) for annotation_id in annotation_ids]


@router.get(
    "/{edition_id}/recordings",
    summary="Get edition recordings",
    description="Retrieve all audio recordings of an edition.",
    response_model_exclude_none=True,
)
async def get_recordings(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[RecordingOutput]:
    return await db.recording.get_all(edition_id)


@router.post(
    "/{edition_id}/recordings",
    status_code=status.HTTP_201_CREATED,
    summary="Add edition recording",
    description="Upload an audio recording of an edition, with its metadata as a JSON form field.",
)
async def post_recording(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    metadata: Annotated[str, Form(description="JSON-encoded recording metadata")],
    audio: Annotated[UploadFile, File(description="Audio file")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> IdResponse:
    data = RecordingInput.model_validate_json(metadata)
    return IdResponse(id=await create_recording(db, storage, edition_id, data, audio))


@router.get(
    "/{edition_id}/related",
    summary="Get related editions",
    description="Find editions related to a given edition.",
)
async def get_related_editions(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[EditionOutput]:
    logger.info("Finding related editions for edition ID: %s", edition_id)
    return await db.edition.get_related(edition_id)


@router.delete(
    "/{edition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete edition",
    description="Delete edition metadata and associated database annotations.",
)
async def delete_edition(
    request: Request,
    background_tasks: BackgroundTasks,
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    logger.info("Deleting edition with edition ID: %s", edition_id)
    await db.edition.delete(edition_id)
    background_tasks.add_task(update_search, request, "edition", edition_id)


@router.patch(
    "/{edition_id}/content",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Patch edition content",
    description="Apply a text operation (INSERT, DELETE, or REPLACE) to the edition's content.",
)
async def patch_content(
    request: Request,
    background_tasks: BackgroundTasks,
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: ContentOperation,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> None:
    await edit_content(db, storage, edition_id, data)
    background_tasks.add_task(update_search, request, "edition", edition_id)
