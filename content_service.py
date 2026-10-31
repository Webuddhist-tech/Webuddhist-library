"""Immutable uploads precede a single graph transaction; rollback keeps the previous content active."""

from typing import TYPE_CHECKING

from database.content_state import advance_revision, lock_connected_editions, read_state
from exceptions import DataConflictError, DataValidationError
from identifier import generate_id
from models.content_operation import ContentOperation, DeleteOperation, InsertOperation
from models.enums import AudioFormat

if TYPE_CHECKING:
    from fastapi import UploadFile
    from neo4j import AsyncManagedTransaction

    from database import Database
    from models.recording import RecordingInput
    from models.requests import EditionRequestModel
    from storage import Storage


async def create_edition(db: Database, storage: Storage, text_id: str, data: EditionRequestModel) -> str:
    edition_id = generate_id()
    key = f"objects/{edition_id}"
    await storage.put_immutable(key, data.content.encode(), "text/plain; charset=utf-8")

    return await db.edition.create(
        data.metadata,
        edition_id,
        text_id,
        len(data.content),
        segmentation=data.segmentation,
        pagination=data.pagination,
        content_key=key,
    )


async def edit_content(db: Database, storage: Storage, edition_id: str, data: ContentOperation) -> None:
    async with db.get_session() as session:
        state = await session.execute_read(read_state, edition_id)
    edit = data.operation
    start, end = (edit.position, edit.position) if isinstance(edit, InsertOperation) else (edit.start, edit.end)
    replacement = "" if isinstance(edit, DeleteOperation) else edit.text
    state.validate_span(end)
    if state.length + len(replacement) - (end - start) <= 0:
        raise DataValidationError("Content edits must leave at least one character")
    content = await storage.read_text(state.object_key)
    if len(content) != state.length:
        raise DataValidationError("Stored content length differs from edition metadata")
    key = f"objects/{generate_id()}"
    updated = content[:start] + replacement + content[end:]
    await storage.put_immutable(key, updated.encode(), "text/plain; charset=utf-8")

    async def write(tx: AsyncManagedTransaction) -> None:
        related = await lock_connected_editions(tx, edition_id)
        current = await read_state(tx, edition_id)
        if current.revision != state.revision:
            raise DataConflictError(f"Edition changed: expected revision {state.revision}, current {current.revision}")
        await db.span.adjust_with_transaction(tx, edition_id, start, end, len(replacement))
        await tx.run(
            """
            MATCH (e:Edition {id: $id})
            SET e.content_key = $key, e.content_length = $length, e.revision = e.revision + 1
            """,
            id=edition_id,
            key=key,
            length=len(updated),
        )
        for related_id in related:
            await advance_revision(tx, related_id)

    async with db.get_session() as session:
        await session.execute_write(write)


async def create_recording(
    db: Database, storage: Storage, edition_id: str, metadata: RecordingInput, audio: UploadFile
) -> str:
    audio_format = AudioFormat.from_content_type(audio.content_type)
    if audio_format is None:
        raise DataValidationError(f"Unsupported audio content type '{audio.content_type}'")
    size = audio.size
    if not size:
        raise DataValidationError("Audio file is empty or its size is unavailable")
    await audio.seek(0)
    recording_id = generate_id()
    key = f"recordings/{edition_id}/{recording_id}.{audio_format.value}"
    await storage.put_immutable(key, audio.file, audio_format.content_type)
    return await db.recording.add(edition_id, metadata, recording_id, audio_format, size)
