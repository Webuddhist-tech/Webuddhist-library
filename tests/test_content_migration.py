import pytest

from database.content_migration import migrate_content
from database.content_state import read_state
from exceptions import DataNotFoundError, DataValidationError
from identifier import generate_id
from models.edition import EditionInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def legacy(db, storage, content="legacy བོད་"):
    text = await db.text.create(TextInput(title={"en": "Migration"}, language="en", category_id="category"))
    edition = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition, text, len(content))
    await storage.store_base_text(text, edition, content)
    async with db.get_session() as session:
        await (await session.run("MATCH (e:Edition {id: $id}) REMOVE e.content_key, e.revision, e.content_length", id=edition)).consume()
    return edition


async def test_content_migration_audits_then_resumes_without_changing_ids(test_database, mock_storage):
    db, storage = test_database, mock_storage
    edition = await legacy(db, storage)
    assert await migrate_content(db, storage) == 1
    async with db.get_session() as session:
        with pytest.raises(DataValidationError, match="migration"):
            await session.execute_read(read_state, edition)
    assert await migrate_content(db, storage, apply=True) == 1
    assert await migrate_content(db, storage, apply=True) == 1
    async with db.get_session() as session:
        state = await session.execute_read(read_state, edition)
    assert state.object_key.startswith("base_texts/")
    assert len(storage._storage) == 1  # Migration reuses the existing object.
    assert state.length == len("legacy བོད་") and state.revision == 0
    assert await storage.read_text(state.object_key) == "legacy བོད་"


async def test_content_migration_refuses_missing_objects_and_invalid_spans(test_database, mock_storage):
    db, storage = test_database, mock_storage
    edition = await legacy(db, storage)
    objects = dict(storage._storage)
    storage._storage.clear()
    with pytest.raises(DataNotFoundError):
        await migrate_content(db, storage, apply=True)
    storage._storage.update(objects)
    async with db.get_session() as session:
        await (await session.run("""
            MATCH (e:Edition {id: $id}), (kind:MarkType {name: 'yigchung'})
            CREATE (m:Mark {id: 'bad'})-[:MARK_OF]->(e), (m)-[:HAS_TYPE]->(kind),
                   (:Span {start: 0, end: 999})-[:SPAN_OF]->(m)
        """, id=edition)).consume()
    with pytest.raises(DataValidationError, match="annotations outside"):
        await migrate_content(db, storage, apply=True)


async def test_empty_store_content_migration(test_database, mock_storage):
    assert await migrate_content(test_database, mock_storage, apply=True) == 0


async def test_content_migration_completes_partial_legacy_state(test_database, mock_storage):
    db, storage = test_database, mock_storage
    edition = await legacy(db, storage)
    async with db.get_session() as session:
        await (await session.run("""
            MATCH (e:Edition {id: $id})-[:EDITION_OF]->(t:Text)
            SET e.content_key = 'base_texts/' + t.id + '/' + e.id + '.txt', e.revision = 0
        """, id=edition)).consume()
    assert await migrate_content(db, storage, apply=True) == 1
    async with db.get_session() as session:
        assert (await session.execute_read(read_state, edition)).length == len("legacy བོད་")
