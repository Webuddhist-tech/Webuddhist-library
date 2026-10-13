import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Path, Query, UploadFile, status

from content_search import ContentSearchService
from dependencies import get_api_key, get_content_search, get_db, get_storage
from exceptions import DataValidationError
from identifier import generate_id
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
from models.content_operation import ContentOperation, DeleteOperation, InsertOperation, ReplaceOperation
from models.edition import EditionOutput
from models.enums import AudioFormat
from models.recording import RecordingInput, RecordingOutput
from models.requests import AnnotationSegmentsPaginationParams
from models.responses import IdResponse, PaginatedResponse, PresignedUrlResponse

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
    edition = await db.edition.get(edition_id=edition_id)
    base_text = await storage.retrieve_base_text(text_id=edition.text_id, edition_id=edition_id)

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
    edition = await db.edition.get(edition_id=edition_id)
    issued_at = datetime.now(UTC)
    url = await storage.generate_base_text_url(
        text_id=edition.text_id,
        edition_id=edition_id,
        expires_in=CONTENT_URL_EXPIRATION_SECONDS,
    )
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
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: SegmentationInput,
    background_tasks: BackgroundTasks,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    content_search: Annotated[ContentSearchService, Depends(get_content_search)],
) -> IdResponse:
    """Add a segmentation annotation to an edition."""
    annotation_id = await db.annotation.segmentation.add(edition_id, data)
    background_tasks.add_task(content_search.index_edition, edition_id, db, storage)
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
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    background_tasks: BackgroundTasks,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    content_search: Annotated[ContentSearchService, Depends(get_content_search)],
) -> None:
    await db.annotation.segmentation.delete_by_edition(edition_id)
    background_tasks.add_task(content_search.index_edition, edition_id, db, storage)


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
    """Add an audio recording to an edition."""
    data = RecordingInput.model_validate_json(metadata)

    audio_format = AudioFormat.from_content_type(audio.content_type)
    if audio_format is None:
        raise DataValidationError(
            f"Unsupported audio content type '{audio.content_type}'; "
            f"supported formats: {', '.join(sorted(AudioFormat))}"
        )

    content = await audio.read()
    if not content:
        raise DataValidationError("Audio file is empty")

    recording_id = generate_id()
    logger.info("Adding recording %s to edition %s (%d bytes)", recording_id, edition_id, len(content))

    await db.recording.add(
        edition_id=edition_id,
        recording=data,
        recording_id=recording_id,
        audio_format=audio_format,
        size_bytes=len(content),
    )

    try:
        await storage.store_recording(
            edition_id=edition_id,
            recording_id=recording_id,
            extension=audio_format.value,
            audio=content,
            content_type=audio_format.content_type,
        )
    except Exception:
        logger.exception("S3 write failed for recording %s, removing its metadata", recording_id)
        await db.recording.delete(recording_id)
        raise

    return IdResponse(id=recording_id)


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
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    content_search: Annotated[ContentSearchService, Depends(get_content_search)],
) -> None:
    logger.info("Deleting edition with edition ID: %s", edition_id)
    await db.edition.get(edition_id=edition_id)
    await db.edition.delete(edition_id)
    await content_search.delete_edition(edition_id)


@router.patch(
    "/{edition_id}/content",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Patch edition content",
    description="Apply a text operation (INSERT, DELETE, or REPLACE) to the edition's content.",
)
async def patch_content(
    edition_id: Annotated[str, Path(description="The ID of the edition")],
    data: ContentOperation,
    background_tasks: BackgroundTasks,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    content_search: Annotated[ContentSearchService, Depends(get_content_search)],
) -> None:
    op = data.operation
    logger.info("Applying %s operation to edition %s", op.type, edition_id)

    edition = await db.edition.get(edition_id=edition_id)

    if isinstance(op, InsertOperation):
        await db.span.adjust_spans_for_insert(
            edition_id=edition_id,
            position=op.position,
            length=len(op.text),
        )
        try:
            await storage.apply_insert(
                text_id=edition.text_id,
                edition_id=edition_id,
                position=op.position,
                text=op.text,
            )
        except Exception:
            logger.exception("S3 write failed after span adjustment for INSERT on %s, compensating", edition_id)
            await db.span.adjust_spans_for_delete(
                edition_id=edition_id,
                start=op.position,
                end=op.position + len(op.text),
            )
            raise
    elif isinstance(op, DeleteOperation):
        await db.span.adjust_spans_for_delete(
            edition_id=edition_id,
            start=op.start,
            end=op.end,
        )
        try:
            await storage.apply_delete(
                text_id=edition.text_id,
                edition_id=edition_id,
                start=op.start,
                end=op.end,
            )
        except Exception:
            logger.exception("S3 write failed after span adjustment for DELETE on %s, compensating", edition_id)
            await db.span.adjust_spans_for_insert(
                edition_id=edition_id,
                position=op.start,
                length=op.end - op.start,
            )
            raise
    elif isinstance(op, ReplaceOperation):
        await db.span.adjust_spans_for_replace(
            edition_id=edition_id,
            start=op.start,
            end=op.end,
            new_len=len(op.text),
        )
        try:
            await storage.apply_replace(
                text_id=edition.text_id,
                edition_id=edition_id,
                start=op.start,
                end=op.end,
                text=op.text,
            )
        except Exception:
            logger.exception("S3 write failed after span adjustment for REPLACE on %s, compensating", edition_id)
            await db.span.adjust_spans_for_replace(
                edition_id=edition_id,
                start=op.start,
                end=op.start + len(op.text),
                new_len=op.end - op.start,
            )
            raise

    background_tasks.add_task(content_search.index_edition, edition_id, db, storage)
