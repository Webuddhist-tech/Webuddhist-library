import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Depends, Path, status
from fastapi.responses import RedirectResponse

from dependencies import get_api_key, get_db, get_storage
from models.recording import RecordingCue, RecordingCues, RecordingOutput, RecordingPatch

if TYPE_CHECKING:
    from database import Database
    from storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/recordings", tags=["Recordings"])


@router.get(
    "/{recording_id}",
    summary="Get a recording",
    description="Retrieve metadata for an audio recording.",
    response_model_exclude_none=True,
)
async def get_recording(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> RecordingOutput:
    """Fetch a recording by ID."""
    return await db.recording.get(recording_id)


@router.get(
    "/{recording_id}/audio",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Get recording audio",
    description="Redirect to a short-lived presigned URL for the recording's audio file.",
    response_class=RedirectResponse,
)
async def get_recording_audio(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> RedirectResponse:
    """Redirect to the recording's audio in storage, which serves range requests directly."""
    edition_id, audio_format = await db.recording.get_storage_location(recording_id)
    url = await storage.generate_recording_url(
        edition_id=edition_id,
        recording_id=recording_id,
        extension=audio_format.value,
    )
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get(
    "/{recording_id}/cues",
    summary="Get recording cues",
    description="Retrieve every cue of a recording, ordered by start time.",
)
async def get_recording_cues(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[RecordingCue]:
    return await db.recording.get_cues(recording_id)


@router.put(
    "/{recording_id}/cues",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Replace recording cues",
    description="Replace all cues of a recording.",
)
async def replace_recording_cues(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    data: Annotated[RecordingCues, Body(min_length=1)],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    logger.info("Replacing %d cues for recording %s", len(data), recording_id)
    await db.recording.replace_cues(recording_id, data)


@router.delete(
    "/{recording_id}/cues",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recording cues",
    description="Delete all cues of a recording.",
)
async def delete_recording_cues(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    await db.recording.delete_cues(recording_id)


@router.patch(
    "/{recording_id}",
    summary="Update a recording",
    description="Partially update a recording's metadata.",
    response_model_exclude_none=True,
)
async def update_recording(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    data: RecordingPatch,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> RecordingOutput:
    """Update a recording's metadata. The audio file itself is replaced by deleting and re-uploading."""
    logger.info("Updating recording %s with: %s", recording_id, data.model_dump_json())
    return await db.recording.update(recording_id, data)


@router.delete(
    "/{recording_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recording",
    description="Delete a recording's metadata and its stored audio file.",
)
async def delete_recording(
    recording_id: Annotated[str, Path(description="The ID of the recording")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> None:
    """Delete a recording."""
    edition_id, audio_format = await db.recording.get_storage_location(recording_id)
    await db.recording.delete(recording_id)
    await storage.delete_recording(
        edition_id=edition_id,
        recording_id=recording_id,
        extension=audio_format.value,
    )
