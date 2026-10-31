import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import content_service
from content_service import create_edition, edit_content
from database.content_state import read_state
from exceptions import DataConflictError, DataValidationError
from identifier import generate_id
from models.annotation import NoteInput, PaginationInput
from models.content_operation import ContentOperation
from models.enums import AudioFormat
from models.person import PersonInput, PersonOutput, PersonPatch
from models.recording import RecordingInput, RecordingOutput, RecordingPatch
from models.requests import EditionRequestModel
from models.text import TextInput, TextOutput, TextPatch
from tests.graph_assertions import snapshot_graph

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Objects:
    def __init__(self):
        self.objects = {}
        self.before_put = None

    async def put_immutable(self, key, body, content_type):
        if self.before_put:
            await self.before_put()
        previous = self.objects.setdefault(key, body)
        assert previous == body

    async def read_text(self, key):
        return self.objects[key].decode()


async def fixture(db):
    storage = Objects()
    text = await db.text.create(TextInput(title={"en": "Lifecycle"}, language="en", category_id="category"))
    data = EditionRequestModel(metadata={"type": "critical"}, content="abcdef", segmentation={"segments": [{"lines": [{"start": 0, "end": 6}]}]})
    edition = await create_edition(db, storage, text, data)
    return storage, text, data, edition


async def state(db, edition):
    async with db.get_session() as session:
        return await session.execute_read(read_state, edition)


def insert(text="XYZ"):
    return ContentOperation.model_validate({"type": "insert", "position": 3, "text": text})


async def test_each_edit_uses_fresh_storage_and_preserves_the_previous_content(test_database):
    db = test_database
    storage, _, _, edition = await fixture(db)
    original = await state(db, edition)
    await edit_content(db, storage, edition, insert())
    first = await state(db, edition)
    await edit_content(db, storage, edition, insert())
    current = await state(db, edition)
    assert current.revision == original.revision + 2
    assert len({original.object_key, first.object_key, current.object_key}) == 3
    assert await storage.read_text(original.object_key) == "abcdef"
    assert await storage.read_text(first.object_key) == "abcXYZdef"
    assert await storage.read_text(current.object_key) == "abcXYZXYZdef"
    lines = (await db.annotation.segmentation.get_all_segments_by_edition(edition))[0].lines
    assert [(line.start, line.end) for line in lines] == [(0, 12)]


async def test_failed_graph_commit_leaves_old_object_and_spans_unchanged(test_database, monkeypatch):
    db = test_database
    storage, _, _, edition = await fixture(db)
    original = await state(db, edition)
    real_adjust = db.span.adjust_with_transaction

    async def fail_after_adjust(*args):
        await real_adjust(*args)
        raise RuntimeError("after spans")

    monkeypatch.setattr(db.span, "adjust_with_transaction", fail_after_adjust)
    with pytest.raises(RuntimeError, match="after spans"):
        await edit_content(db, storage, edition, insert())
    assert await state(db, edition) == original
    assert len(storage.objects) == 2  # The failed upload is intentionally left in storage.
    assert (await db.annotation.segmentation.get_all_segments_by_edition(edition))[0].lines[0].end == 6
    monkeypatch.setattr(db.span, "adjust_with_transaction", real_adjust)
    await edit_content(db, storage, edition, insert())
    assert await storage.read_text((await state(db, edition)).object_key) == "abcXYZdef"


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failure_after_pointer_update_rolls_back_both_aligned_editions(test_database, monkeypatch, failure):
    from models.alignment import EditionAlignmentInput

    db = test_database
    storage, _, data, edition = await fixture(db)
    text = await db.text.create(TextInput(title={"en": "Neighbor"}, language="en", category_id="category"))
    neighbor = await create_edition(db, storage, text, data)
    async with db.get_session() as session:
        await (await session.run("MATCH (s:Segment) SET s.reference = '1'")).consume()
    await db.alignment.replace(edition, neighbor, EditionAlignmentInput(alignments=[{
        "source_segment_reference": "1", "target_segment_reference": "1"
    }]))
    original = [await state(db, item) for item in (edition, neighbor)]
    segments = await db.annotation.segmentation.get_all_segments_by_edition(edition)
    advance_revision = content_service.advance_revision

    async def fail_after_neighbor_revision(tx, edition_id):
        await advance_revision(tx, edition_id)
        raise failure("after pointer and revisions")

    monkeypatch.setattr(content_service, "advance_revision", fail_after_neighbor_revision)
    with pytest.raises(failure, match="after pointer and revisions"):
        await edit_content(db, storage, edition, insert())
    assert [await state(db, item) for item in (edition, neighbor)] == original
    assert await db.annotation.segmentation.get_all_segments_by_edition(edition) == segments
    assert await storage.read_text(original[0].object_key) == "abcdef"


async def test_failed_edition_creation_rolls_back_edition_and_segmentation(test_database, monkeypatch):
    from database.annotation.segmentation_database import SegmentationDatabase

    db = test_database
    storage, _, data, _ = await fixture(db)
    text = await db.text.create(TextInput(title={"en": "New edition"}, language="en", category_id="category"))
    before = await snapshot_graph(db)
    add = SegmentationDatabase.add_with_transaction

    async def fail_after_segmentation(*args):
        await add(*args)
        raise RuntimeError("after segmentation")

    monkeypatch.setattr(SegmentationDatabase, "add_with_transaction", fail_after_segmentation)
    with pytest.raises(RuntimeError, match="after segmentation"):
        await create_edition(db, storage, text, data)
    assert await snapshot_graph(db) == before
    assert await db.edition.get_all(text) == []
    async with db.get_session() as session:
        result = await session.run("MATCH (s:Segmentation) RETURN count(s) AS count")
        assert (await result.single())["count"] == 1
    assert len(storage.objects) == 2


async def test_failed_edition_deletion_restores_annotations(test_database, monkeypatch):
    from database.edition_database import EditionDatabase
    from neo4j import AsyncManagedTransaction

    db = test_database
    _, _, _, edition = await fixture(db)
    await db.annotation.note.add_durchen(edition, NoteInput(span={"start": 1, "end": 3}, text="note"))
    original = await state(db, edition)
    segments = await db.annotation.segmentation.get_all_segments_by_edition(edition)
    notes = await db.annotation.note.get_all(edition)
    run = AsyncManagedTransaction.run

    async def fail_after_deletion(tx, query, *args, **kwargs):
        result = await run(tx, query, *args, **kwargs)
        if query == EditionDatabase.DELETE_QUERY:
            await result.consume()
            raise RuntimeError("after deletion")
        return result

    monkeypatch.setattr(AsyncManagedTransaction, "run", fail_after_deletion)
    with pytest.raises(RuntimeError, match="after deletion"):
        await db.edition.delete(edition)
    assert await state(db, edition) == original
    assert await db.annotation.segmentation.get_all_segments_by_edition(edition) == segments
    assert await db.annotation.note.get_all(edition) == notes


@pytest.mark.parametrize("kind,output,patch", [
    ("person", PersonOutput, PersonPatch(name={"en": "Changed"}, wiki="Q-changed")),
    ("text", TextOutput, TextPatch(title={"en": "Changed"}, wiki="Q-changed")),
    ("recording", RecordingOutput, RecordingPatch(title={"en": "Changed"}, date="2026")),
])
async def test_failed_update_response_rolls_back_metadata(test_database, monkeypatch, kind, output, patch):
    db = test_database
    _, entity_id, _, edition = await fixture(db)
    if kind == "person":
        entity_id = await db.person.create(PersonInput(name={"en": "Original"}))
    elif kind == "recording":
        entity_id = await db.recording.add(
            edition, RecordingInput(contributions=[{"type": "ai", "id": "tts", "role": "narrator"}]),
            generate_id(), AudioFormat.MP3, 5,
        )
    repository = getattr(db, kind)
    original = await repository.get(entity_id)
    with monkeypatch.context() as patcher:
        patcher.setattr(output, "model_validate", Mock(side_effect=RuntimeError("invalid response")))
        with pytest.raises(RuntimeError, match="invalid response"):
            await repository.update(entity_id, patch)
    assert await repository.get(entity_id) == original


async def test_concurrent_edits_never_overwrite_each_others_state(test_database):
    db = test_database
    storage, _, _, edition = await fixture(db)
    barrier = asyncio.Barrier(2)
    storage.before_put = barrier.wait
    results = await asyncio.gather(
        *(edit_content(db, storage, edition, insert(value)) for value in ("X", "Y")),
        return_exceptions=True,
    )
    assert sum(isinstance(result, DataConflictError) for result in results) == 1, results
    current = await state(db, edition)
    assert await storage.read_text(current.object_key) in ("abcXdef", "abcYdef")
    assert current.length == 7


async def test_annotation_change_invalidates_an_edit_prepared_before_it(test_database):
    db = test_database
    storage, _, _, edition = await fixture(db)

    async def annotate():
        await db.annotation.note.add_durchen(edition, NoteInput(span={"start": 1, "end": 3}, text="note"))

    storage.before_put = annotate
    with pytest.raises(DataConflictError, match="changed"):
        await edit_content(db, storage, edition, insert())
    assert await storage.read_text((await state(db, edition)).object_key) == "abcdef"
    notes = await db.annotation.note.get_all(edition)
    assert len(notes) == 1 and notes[0].span.end == 3


async def test_concurrent_annotations_preserve_both_revision_increments(test_database):
    db = test_database
    _, _, _, edition = await fixture(db)
    original = await state(db, edition)
    await asyncio.gather(*(
        db.annotation.note.add_durchen(edition, NoteInput(span={"start": 1, "end": 3}, text=text))
        for text in ("first", "second")
    ))
    assert (await state(db, edition)).revision == original.revision + 2
    assert {note.text for note in await db.annotation.note.get_all(edition)} == {"first", "second"}


async def test_invalid_annotation_rolls_back_its_revision_increment(test_database):
    db = test_database
    _, _, _, edition = await fixture(db)
    original = await state(db, edition)
    with pytest.raises(DataValidationError):
        await db.annotation.note.add_durchen(edition, NoteInput(span={"start": 1, "end": 7}, text="invalid"))
    assert await state(db, edition) == original
    assert await db.annotation.note.get_all(edition) == []


async def test_cancelled_upload_never_changes_graph(test_database):
    db = test_database
    storage, _, _, edition = await fixture(db)
    original = await state(db, edition)
    storage.before_put = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await edit_content(db, storage, edition, insert())
    assert await state(db, edition) == original


async def test_empty_edit_is_rejected_before_upload(test_database):
    db = test_database
    storage, _, _, edition = await fixture(db)
    original = await state(db, edition)
    with pytest.raises(DataValidationError, match="at least one"):
        await edit_content(db, storage, edition, ContentOperation.model_validate({"type": "delete", "start": 0, "end": 6}))
    assert len(storage.objects) == 1
    assert await state(db, edition) == original


@pytest.mark.parametrize("operation", [
    {"type": "delete", "start": 0, "end": 3},
    {"type": "replace", "start": 0, "end": 6, "text": "X"},
])
async def test_edit_emptying_a_page_leaves_content_and_annotations_unchanged(test_database, operation):
    db = test_database
    storage, _, _, edition = await fixture(db)
    pagination_id = await db.annotation.pagination.add(edition, PaginationInput(volumes=[{"pages": [
        {"reference": "1", "lines": [{"start": 0, "end": 3}]},
        {"reference": "2", "lines": [{"start": 3, "end": 6}]},
    ]}]))
    original = await state(db, edition)
    pagination = await db.annotation.pagination.get(pagination_id)
    segments = await db.annotation.segmentation.get_all_segments_by_edition(edition)
    with pytest.raises(DataValidationError, match="every page"):
        await edit_content(db, storage, edition, ContentOperation.model_validate(operation))
    assert await state(db, edition) == original
    assert await storage.read_text(original.object_key) == "abcdef"
    assert await db.annotation.pagination.get(pagination_id) == pagination
    assert await db.annotation.segmentation.get_all_segments_by_edition(edition) == segments


async def test_aligned_edition_edit_invalidates_pending_edit(test_database):
    from models.alignment import EditionAlignmentInput

    db = test_database
    storage, _, data, first = await fixture(db)
    text = await db.text.create(TextInput(title={"en": "Aligned edition"}, language="en", category_id="category"))
    second = await create_edition(db, storage, text, data)
    # Stable references are resolved by the public alignment writer.
    async with db.get_session() as session:
        await (await session.run("MATCH (s:Segment) SET s.reference = '1'")).consume()
    await db.alignment.replace(first, second, EditionAlignmentInput(alignments=[{
        "source_segment_reference": "1", "target_segment_reference": "1"
    }]))
    original = await state(db, second)

    async def change_neighbor():
        storage.before_put = None
        await edit_content(db, storage, first, insert("neighbor"))

    storage.before_put = change_neighbor
    with pytest.raises(DataConflictError, match="changed"):
        await edit_content(db, storage, second, insert())
    current = await state(db, second)
    assert current.revision == original.revision + 1
    assert current.object_key == original.object_key
    assert await storage.read_text(current.object_key) == "abcdef"


async def test_recording_upload_failure_and_empty_audio_never_publish_metadata(test_database):
    from io import BytesIO
    from fastapi import UploadFile
    from starlette.datastructures import Headers

    db = test_database
    storage, _, _, edition = await fixture(db)
    metadata = RecordingInput(contributions=[{"type": "ai", "id": "tts", "role": "narrator"}])
    audio = UploadFile(BytesIO(b"audio"), size=5, headers=Headers({"content-type": "audio/mpeg"}))
    storage.before_put = AsyncMock(side_effect=OSError("upload interrupted"))
    with pytest.raises(OSError, match="interrupted"):
        await content_service.create_recording(db, storage, edition, metadata, audio)
    assert await db.recording.get_all(edition) == []
    audio = UploadFile(BytesIO(), size=0, headers=Headers({"content-type": "audio/mpeg"}))
    with pytest.raises(DataValidationError, match="empty"):
        await content_service.create_recording(db, storage, edition, metadata, audio)
    storage.before_put.assert_awaited_once()


async def test_recording_database_failure_rolls_back_metadata_after_upload(test_database, monkeypatch):
    from io import BytesIO
    from fastapi import UploadFile
    from starlette.datastructures import Headers
    from database.contribution_database import ContributionDatabase

    db = test_database
    storage, _, _, edition = await fixture(db)
    create = ContributionDatabase.create_with_transaction
    before = await snapshot_graph(db)

    async def fail_after_contribution(*args):
        await create(*args)
        raise RuntimeError("after contribution")

    monkeypatch.setattr(ContributionDatabase, "create_with_transaction", fail_after_contribution)
    audio = UploadFile(BytesIO(b"audio"), size=5, headers=Headers({"content-type": "audio/mpeg"}))
    metadata = RecordingInput(title={"en": "Reading"}, contributions=[{"type": "ai", "id": "tts", "role": "narrator"}])
    with pytest.raises(RuntimeError, match="after contribution"):
        await content_service.create_recording(db, storage, edition, metadata, audio)
    assert await snapshot_graph(db) == before
    assert await db.recording.get_all(edition) == []
    async with db.get_session() as session:
        result = await session.run("MATCH (n) WHERE n:Contribution OR n:AI RETURN count(n) AS count")
        assert (await result.single())["count"] == 0
    assert len(storage.objects) == 2  # The uploaded audio is unused, not published.


async def test_related_read_validates_remote_coordinates(test_database, monkeypatch):
    from models.alignment import EditionAlignmentInput

    db = test_database
    storage, _, data, first = await fixture(db)
    text = await db.text.create(TextInput(title={"en": "Read neighbor"}, language="en", category_id="category"))
    second = await create_edition(db, storage, text, data)
    async with db.get_session() as session:
        await (await session.run("MATCH (s:Segment) SET s.reference = '1'")).consume()
    await db.alignment.replace(first, second, EditionAlignmentInput(alignments=[{
        "source_segment_reference": "1", "target_segment_reference": "1"
    }]))
    origin = (await db.annotation.segmentation.get_all_segments_by_edition(first))[0]
    # The route resolves the starting segment inside the validated read, not from
    # coordinates captured before entering it.
    assert await db.segment.get_related(first, [(100, 200)], starting_segment_id=origin.id)
    resolve = db.segment._resolve_segment_page

    async def interrupted_read(*args, **kwargs):
        result = await resolve(*args, **kwargs)
        await edit_content(db, storage, second, insert())
        return result

    monkeypatch.setattr(db.segment, "_resolve_segment_page", interrupted_read)
    with pytest.raises(DataConflictError, match="changed"):
        await db.segment.get_related(first, [], starting_segment_id=origin.id)
