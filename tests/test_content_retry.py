"""Exercise replay and interleavings against real transaction rollback."""

import io
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers
from neo4j import AsyncSession
from neo4j.exceptions import TransientError

import database.content_state as content_state
from content_service import create_edition, create_recording, edit_content
from database.content_state import read_at_revision
from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from models.annotation import NoteInput
from models.recording import RecordingInput
from tests.test_content_lifecycle import fixture, state, insert
from tests.test_graph_scenarios import graph_edition, align

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("kind", ["edition", "recording", "edit"])
async def test_managed_transaction_replay_does_not_repeat_upload_or_change_identity(test_database, monkeypatch, kind):
    db = test_database
    storage, text, data, edition = await fixture(db)
    before = await state(db, edition)
    uploads = AsyncMock(wraps=storage.put_immutable)
    monkeypatch.setattr(storage, "put_immutable", uploads)
    original = AsyncSession.execute_write
    attempts = []

    async def replay_once(session, callback, *args, **kwargs):
        async def interrupted(tx, *callback_args, **callback_kwargs):
            result = await callback(tx, *callback_args, **callback_kwargs)
            attempts.append(result)
            if len(attempts) == 1:
                raise TransientError("audit retry after mutations, before commit")
            return result

        return await original(session, interrupted, *args, **kwargs)

    if kind == "edition":
        from models.text import TextInput

        text = await db.text.create(TextInput(title={"en": "Replay edition"}, language="en", category_id="category"))
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute_write", replay_once)
        if kind == "edition":
            result = await create_edition(db, storage, text, data)
        elif kind == "recording":
            audio = UploadFile(io.BytesIO(b"audio"), size=5, headers=Headers({"content-type": "audio/mpeg"}))
            result = await create_recording(
                db,
                storage,
                edition,
                RecordingInput(contributions=[{"type": "ai", "id": "tts", "role": "narrator"}]),
                audio,
            )
        else:
            result = await edit_content(db, storage, edition, insert("X"))
    assert attempts == [result, result]
    uploads.assert_awaited_once()
    if kind == "edition":
        assert [e.id for e in await db.edition.get_all(text)] == [result]
        assert (await state(db, result)).object_key == uploads.await_args.args[0]
    elif kind == "recording":
        assert [r.id for r in await db.recording.get_all(edition)] == [result]
        assert result in uploads.await_args.args[0]
    else:
        current = await state(db, edition)
        assert current.revision == before.revision + 1
        assert current.length == 7
        assert await storage.read_text(current.object_key) == "abcXdef"
        assert [
            (l.start, l.end)
            for s in await db.annotation.segmentation.get_all_segments_by_edition(edition)
            for l in s.lines
        ] == [(0, 7)]


@pytest.mark.parametrize("always_changes", [False, True])
async def test_consistent_read_retries_then_returns_or_conflicts(test_database, always_changes):
    db = test_database
    _, _, _, edition = await fixture(db)
    reads = []

    async def read(tx):
        observed = await content_state.read_state(tx, edition)
        reads.append(observed.revision)
        if always_changes or len(reads) == 1:
            await db.annotation.note.add_durchen(
                edition, NoteInput(span={"start": 1, "end": 2}, text=f"Revision {len(reads)}")
            )
        return observed.revision

    async with db.get_session() as session:
        if always_changes:
            with pytest.raises(DataConflictError, match="changed while reading"):
                await session.execute_read(read_at_revision, edition, read)
            assert len(reads) == 3
        else:
            snapshot, value = await session.execute_read(read_at_revision, edition, read)
            assert len(reads) == 2
            assert value == snapshot.revision == reads[-1] == reads[0] + 1


async def test_late_alignment_during_lock_acquisition_rejects_edit(test_database, monkeypatch):
    db = test_database
    storage, _, _, edition = await fixture(db)
    original = await state(db, edition)
    # Assign the pre-existing segmentation a stable reference for the concurrent alignment.
    async with db.get_session() as session:
        await (
            await session.run(
                'MATCH (:Edition {id:$id})-[:HAS_SEGMENTATION]->(:Segmentation)<-[:SEGMENT_OF]-(s:Segment) SET s.reference="0"',
                id=edition,
            )
        ).consume()
    remote, _ = await graph_edition(db, "late")
    lock = content_state.lock_nodes
    attached = False

    async def attach_before_lock(tx, label, ids):
        nonlocal attached
        if not attached:
            attached = True
            await align(db, edition, remote)
        await lock(tx, label, ids)

    monkeypatch.setattr(content_state, "lock_nodes", attach_before_lock)
    with pytest.raises(DataConflictError, match="Alignment changed while acquiring"):
        await edit_content(db, storage, edition, insert())
    current = await state(db, edition)
    assert current.object_key == original.object_key
    assert await storage.read_text(current.object_key) == "abcdef"
    assert len(await db.alignment.get(edition, remote, offset=0, limit=20)) == 1


async def test_deleted_edition_cannot_be_republished_by_prepared_edit(test_database):
    db = test_database
    storage, _, _, edition = await fixture(db)
    storage.before_put = lambda: db.edition.delete(edition)
    with pytest.raises(DataNotFoundError):
        await edit_content(db, storage, edition, insert())
    with pytest.raises(DataNotFoundError):
        await db.edition.get(edition)
    async with db.get_session() as session:
        assert (await (await session.run("MATCH (s:Span) RETURN count(s) AS count")).single())["count"] == 0


async def test_annotation_waiting_for_deleted_edition_does_not_leave_orphans(test_database, monkeypatch):
    import database.annotation.single_span as single_span

    db = test_database
    _, _, _, edition = await fixture(db)
    touch = single_span.touch_edition

    async def delete_before_touch(tx, edition_id):
        await db.edition.delete(edition_id)
        return await touch(tx, edition_id)

    monkeypatch.setattr(single_span, "touch_edition", delete_before_touch)
    with pytest.raises(DataNotFoundError):
        await db.annotation.note.add_durchen(edition, NoteInput(span={"start": 1, "end": 2}, text="Unpublished"))
    async with db.get_session() as session:
        assert (await (await session.run("MATCH (n:Note) RETURN count(n) AS count")).single())["count"] == 0


async def test_corrupt_object_length_rejects_edit_before_upload(test_database, monkeypatch):
    db = test_database
    storage, _, _, edition = await fixture(db)
    before = await state(db, edition)
    segments = await db.annotation.segmentation.get_all_segments_by_edition(edition)
    storage.objects[before.object_key] = b"incorrect content length"
    uploads = AsyncMock(wraps=storage.put_immutable)
    monkeypatch.setattr(storage, "put_immutable", uploads)
    with pytest.raises(DataValidationError, match="Stored content length differs"):
        await edit_content(db, storage, edition, insert())
    assert await state(db, edition) == before
    assert await db.annotation.segmentation.get_all_segments_by_edition(edition) == segments
    uploads.assert_not_awaited()


async def test_search_rechecks_revision_after_resolving_segments(test_database, monkeypatch):
    from neo4j import AsyncManagedTransaction
    from content_search.service import _candidates, _join_segments

    db = test_database
    storage, _, _, edition = await fixture(db)
    snapshot = await state(db, edition)
    hit = {
        "_source": {
            "edition_id": edition,
            "text_id": snapshot.text_id,
            "object_key": snapshot.object_key,
            "offset": 0,
            "content": "abcdef",
        }
    }
    candidates = list(_candidates(hit, "abc", "exact"))
    assert len(await _join_segments(db, candidates)) == 1
    run = AsyncManagedTransaction.run
    changed = []

    async def change_before_recheck(tx, query, *args, **kwargs):
        if "MATCH (e:Edition) WHERE e.id IN $ids RETURN e.id AS id, e.revision AS revision" in query and not changed:
            changed.append(True)
            await db.annotation.note.add_durchen(
                edition, NoteInput(span={"start": 1, "end": 2}, text="Concurrent annotation")
            )
        return await run(tx, query, *args, **kwargs)

    monkeypatch.setattr(AsyncManagedTransaction, "run", change_before_recheck)
    assert await _join_segments(db, candidates) == []
    assert changed == [True]


async def test_search_preparation_rejects_corrupt_content_length(test_database):
    from content_search import ContentSearchService
    from types import SimpleNamespace

    db = test_database
    storage, _, _, edition = await fixture(db)
    snapshot = await state(db, edition)
    storage.objects[snapshot.object_key] = b"bad"
    with pytest.raises(DataValidationError, match="Stored content length differs"):
        await ContentSearchService(client=SimpleNamespace(), index_name="test").prepare_documents(edition, db, storage)
