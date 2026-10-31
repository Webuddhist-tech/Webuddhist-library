import asyncio

import pytest
from neo4j.exceptions import ClientError

from database.migrations import audit_schema, migrate
from database.database_validator import DatabaseValidator
from database.text_database import TextDatabase
from identifier import generate_id
from exceptions import DataValidationError
from models.category import CategoryInput
from models.tag import TagInput
from models.text import TextInput, TextOutput, TextPatch

pytestmark = pytest.mark.asyncio(loop_scope="session")


def contend_before(monkeypatch, target, method):
    original = getattr(target, method)
    barrier = asyncio.Barrier(2)
    arrivals = 0
    async def simultaneous(*args, **kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals <= 2:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original(*args, **kwargs)
    monkeypatch.setattr(target, method, staticmethod(simultaneous) if isinstance(target, type) else simultaneous)




@pytest.mark.parametrize("label", ["LocalizedText", "Contribution"])
async def test_idless_nodes_cannot_skip_required_relationships(test_database, label):
    async with test_database.get_session() as session:
        with pytest.raises(ClientError, match="enforce_"):
            await (await session.run(f"CREATE (:{label})")).consume()


async def test_updates_cannot_invert_spans(test_database):
    db = test_database
    text = await db.text.create(TextInput(title={"en": "Mutable spans"}, language="en", category_id="category"))
    from models.edition import EditionInput
    from models.annotation import SegmentationInput
    edition = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition, text, 20,
                            segmentation=SegmentationInput(segments=[{"lines": [{"start": 2, "end": 5}]}]))
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_span_start_lte_end"):
            await (await session.run("MATCH (s:Span) SET s.start = 8")).consume()


async def test_title_uniqueness_survives_concurrent_creates_and_updates(test_database, monkeypatch):
    contend_before(monkeypatch, DatabaseValidator, "validate_text_creation")
    db = test_database
    title = TextInput(title={"en": "Contended title"}, language="en", category_id="category")
    results = await asyncio.gather(db.text.create(title), db.text.create(title), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    error = errors[0]
    assert (isinstance(error, DataValidationError) and "Contended title" in str(error) and "already exists" in str(error)) or (isinstance(error, ClientError) and "enforce_text_title_unique: Duplicate title+language exists" in str(error)), results
    second = await db.text.create(TextInput(title={"en": "Second title"}, language="en", category_id="category"))
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_text_title_unique"):
            await (await session.run("MATCH (:Text {id: $id})-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(t:LocalizedText) SET t.text = 'Contended title'", id=second)).consume()


async def test_concurrent_title_changes_cannot_claim_the_same_title(test_database, monkeypatch):
    contend_before(monkeypatch, TextDatabase, "_get_for_update")
    db = test_database
    ids = [await db.text.create(TextInput(title={"en": title}, language="en", category_id="category"))
           for title in ("First title", "Second title")]
    results = await asyncio.gather(
        *(db.text.update(text_id, TextPatch(title={"en": "Shared title"})) for text_id in ids),
        return_exceptions=True,
    )
    assert sum(isinstance(result, TextOutput) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], ClientError), results
    message = str(errors[0])
    if "will wait indefinitely" in message:
        # APOC may wrap a lock conflict as a ClientError. A fresh attempt must
        # then reject the duplicate title, rather than passing on any error.
        assert "enforce_text_title_unique" in message
        assert errors[0].code == "Neo.ClientError.Transaction.TransactionHookFailed"
        loser = next(text_id for text_id, result in zip(ids, results) if isinstance(result, Exception))
        with pytest.raises(ClientError, match="enforce_text_title_unique: Duplicate title\\+language exists"):
            await db.text.update(loser, TextPatch(title={"en": "Shared title"}))
    else:
        assert "enforce_text_title_unique: Duplicate title+language exists" in message
    titles = [(await db.text.get(text_id)).title.root for text_id in ids]
    assert titles.count({"en": "Shared title"}) == 1
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            assert titles[index] == {"en": ("First title", "Second title")[index]}


async def test_title_language_relationship_updates_cannot_create_duplicates(test_database):
    db = test_database
    for language in ("en", "en-US"):
        text_id = await db.text.create(TextInput(
            title={language: "Same spelling"}, language="en", category_id="category",
        ))
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_text_title_unique"):
            await (await session.run("""
                MATCH (:Text {id: $id})-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
                      -[language:HAS_LANGUAGE]->(:Language)
                SET language.bcp47 = 'EN'
            """, id=text_id)).consume()
    assert (await db.text.get(text_id)).title.root == {"en-US": "Same spelling"}


@pytest.mark.parametrize("entity,model", [("tag", TagInput), ("category", CategoryInput)])
async def test_taxonomy_title_check_serializes_with_create(test_database, entity, model, monkeypatch):
    repository = getattr(test_database, entity)
    contend_before(monkeypatch, repository, "_validate_not_exists_tx")
    value = model(title={"en": "Contended"})
    results = await asyncio.gather(repository.create(value, "test_application"), repository.create(value, "test_application"), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], DataValidationError), results
    async with test_database.get_session() as session:
        assert (await (await session.run(f"MATCH (n:{entity.title()})-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(title:LocalizedText {{text: 'Contended'}}) RETURN count(n) AS count")).single())["count"] == 1


async def test_migration_is_repeatable_and_audited(test_database):
    db = test_database
    assert await audit_schema(db._driver, db._database) == {}
    await migrate(db._driver, db._database)
    await migrate(db._driver, db._database)
    assert await audit_schema(db._driver, db._database) == {}


@pytest.mark.parametrize("second_start", [4, 6])
@pytest.mark.parametrize("same_segment", [False, True])
async def test_schema_upgrade_rejects_legacy_segmentation_overlap_or_gap(test_database, second_start, same_segment):
    from models.annotation import SegmentationInput
    from models.edition import EditionInput

    db = test_database
    text = await db.text.create(TextInput(title={"en": "Legacy segmentation"}, language="en", category_id="category"))
    edition = generate_id()
    lines = [{"start": 0, "end": 5}, {"start": 5, "end": 10}]
    segments = [{"lines": lines}] if same_segment else [{"lines": [line]} for line in lines]
    await db.edition.create(EditionInput(type="critical"), edition, text, 10, segmentation=SegmentationInput(segments=segments))
    segmentation = await db.annotation.segmentation.get_by_edition(edition)
    async with db.get_session() as session:
        await (await session.run("MATCH (s:Span {start: 5}) SET s.start = $start", start=second_start)).consume()
    violations = await audit_schema(db._driver, db._database)
    assert violations["noncontiguous_segmentation"] == [segmentation.id]
    with pytest.raises(RuntimeError, match="Schema audit failed"):
        await migrate(db._driver, db._database)


async def test_failed_migration_can_resume(test_database, monkeypatch):
    import database.migrations as migrations
    db = test_database
    install = migrations.install_triggers

    async def interrupted(*args):
        raise RuntimeError("interrupted installation")

    monkeypatch.setattr(migrations, "install_triggers", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await migrate(db._driver, db._database)
    monkeypatch.setattr(migrations, "install_triggers", install)
    await migrate(db._driver, db._database)
    assert await audit_schema(db._driver, db._database) == {}


async def test_updates_cannot_change_recording_role_or_toc_parent_bounds(test_database):
    from models.annotation import TableOfContentsInput
    from models.edition import EditionInput
    from models.enums import AudioFormat
    from models.recording import RecordingInput

    db = test_database
    text = await db.text.create(TextInput(title={"en": "Mutable semantics"}, language="en", category_id="category"))
    edition = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition, text, 20)
    await db.recording.add(edition, RecordingInput(contributions=[{"type": "ai", "id": "tts", "role": "narrator"}]), generate_id(), AudioFormat.MP3, 10)
    toc = TableOfContentsInput(sections=[{"title": {"en": "Parent"}, "span": {"start": 0, "end": 20}, "subsections": [{"title": {"en": "Child"}, "span": {"start": 5, "end": 10}}]}])
    await db.annotation.table_of_contents.add(edition, toc)
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_recording_contribution_narrator"):
            await (await session.run("MATCH (r:RoleType {name: 'narrator'}) SET r.name = 'unsupported-role'")).consume()
        with pytest.raises(ClientError, match="enforce_toc_hierarchy"):
            await (await session.run("MATCH (:TableOfContentsSection)-[:SUBSECTION_OF]->(parent)<-[:SPAN_OF]-(s:Span) SET s.end = 8")).consume()


async def test_installed_trigger_rejects_relationship_deletion_and_rolls_back(test_database):
    from models.person import PersonInput
    db = test_database
    person = await db.person.create(PersonInput(name={'en':'Required name'}))
    original = await db.person.get(person)
    async with db.get_session() as session:
        with pytest.raises(ClientError, match='enforce_person_has_name'):
            await (await session.run('MATCH (:Person {id:$id})-[r:HAS_NAME]->() DELETE r',id=person)).consume()
    assert await db.person.get(person) == original


@pytest.mark.parametrize('rows', [0,2])
async def test_invalid_create_result_rolls_back_real_transaction(test_database, monkeypatch, rows):
    from neo4j.exceptions import ResultNotSingleError
    db = test_database
    query = f'CREATE (a:Application {{id:$application_id,name:$name}}) WITH a UNWIND {list(range(rows))} AS duplicate RETURN a.id AS id'
    monkeypatch.setattr(db.application, 'CREATE_QUERY', query)
    with pytest.raises(ResultNotSingleError):
        await db.application.create('unpublished','Unpublished')
    assert not await db.application.exists('unpublished')
