import pytest
import pytest_asyncio

from database.migrations import audit_schema, migrate
from exceptions import DataValidationError
from identifier import generate_id
from models.annotation import NoteInput, PaginationInput, SegmentationInput
from models.edition import EditionInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def edition(test_database):
    db = test_database
    text = await db.text.create(TextInput(title={"en": "Integrity"}, language="en", category_id="category"))
    edition = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition, text, content_length=30)
    return edition


@pytest.mark.parametrize("metadata", [None, {}, {"name": "Provenance"}])
async def test_collection_metadata_round_trip_and_cleanup(test_database, edition, metadata):
    db = test_database
    pagination = PaginationInput(volumes=[{"pages": [{"reference": "1", "lines": [{"start": 0, "end": 30}]}], "metadata": metadata}], metadata=metadata)
    pagination_id = await db.annotation.pagination.add(edition, pagination)
    read = await db.annotation.pagination.get(pagination_id)
    assert read.metadata == pagination.metadata
    assert read.volumes == pagination.volumes
    segmentation = SegmentationInput(segments=[{"lines": [{"start": 0, "end": 30}]}], metadata=metadata)
    await db.annotation.segmentation.add(edition, segmentation)
    assert (await db.annotation.segmentation.get_by_edition(edition)).metadata == segmentation.metadata
    await db.edition.delete(edition)
    from exceptions import DataNotFoundError
    with pytest.raises(DataNotFoundError):
        await db.edition.get(edition)
    with pytest.raises(DataNotFoundError):
        await db.annotation.pagination.get(pagination_id)
    with pytest.raises(DataNotFoundError):
        await db.annotation.segmentation.get_by_edition(edition)
    async with db.get_session() as session:
        record = await (await session.run("MATCH (m:AnnotationMetadata) RETURN count(m) AS count")).single()
        assert record["count"] == 0


async def test_zero_width_markers_visible_in_reads(test_database, edition):
    db = test_database
    await db.annotation.segmentation.add(edition, SegmentationInput(segments=[{"lines": [{"start": 0, "end": 0}, {"start": 0, "end": 5}]}, {"lines": [{"start": 5, "end": 5}]}]))
    segments = await db.annotation.segmentation.get_all_segments_by_edition(edition)
    assert len(segments) == 2
    assert [(s.start, s.end) for s in segments[0].lines] == [(0, 0), (0, 5)]
    assert (await db.segment.get(segments[1].id)).lines == segments[1].lines
    note = NoteInput(span={"start": 5, "end": 5}, text="Marker")
    note_id = await db.annotation.note.add_durchen(edition, note)
    assert (await db.annotation.note.get(note_id)).span == note.span
    assert [item.id for item in await db.annotation.note.get_all(edition)] == [note_id]


@pytest.mark.parametrize("edit,expected", [("insert", [(0, 13), (13, 23), (23, 33)]), ("replace", [(0, 5), (5, 8), (8, 13)]), ("delete", [(0, 5), (5, 10), (10, 20)])])
async def test_page_edits_preserve_continuity_and_references(test_database, edition, edit, expected):
    db = test_database
    pages = [{"reference": str(i), "lines": [{"start": a, "end": b}]} for i, (a, b) in enumerate([(0, 10), (10, 20), (20, 30)])]
    pagination_id = await db.annotation.pagination.add(edition, PaginationInput(volumes=[{"pages": pages}]))
    if edit == "insert":
        async with db.get_session() as session:
            await session.execute_write(db.span.adjust_with_transaction, edition, 0, 0, 3)
    elif edit == "replace":
        async with db.get_session() as session:
            await session.execute_write(db.span.adjust_with_transaction, edition, 5, 25, 3)
    else:
        async with db.get_session() as session:
            await session.execute_write(db.span.adjust_with_transaction, edition, 5, 15, 0)
    read = await db.annotation.pagination.get(pagination_id)
    assert [p.reference for p in read.volumes[0].pages] == [str(i) for i in range(3)]
    assert [(p.span.start, p.span.end) for p in read.volumes[0].pages] == expected


@pytest.mark.parametrize("start,expected", [
    (2, [(4, 5), (5, 7), (7, 9)]),
    (5, [(5, 8), (8, 10), (10, 12)]),
])
async def test_replacement_preserves_leading_annotation_boundary(test_database, edition, start, expected):
    db = test_database
    groups = [
        {"lines": [{"start": 5, "end": 8}, {"start": 8, "end": 10}]},
        {"lines": [{"start": 10, "end": 12}]},
    ]
    await db.annotation.segmentation.add(edition, SegmentationInput(segments=groups))
    pagination_id = await db.annotation.pagination.add(edition, PaginationInput(volumes=[{
        "pages": [group | {"reference": str(i)} for i, group in enumerate(groups)],
    }]))
    async with db.get_session() as session:
        await session.execute_write(db.span.adjust_with_transaction, edition, start, 7, 2)
    segments = await db.annotation.segmentation.get_all_segments_by_edition(edition)
    pages = (await db.annotation.pagination.get(pagination_id)).volumes[0].pages
    for entities in (segments, pages):
        assert len(entities) == 2
        assert [(line.start, line.end) for entity in entities for line in entity.lines] == expected


@pytest.mark.parametrize("start,end,new_len", [(10, 20, 0), (0, 20, 1), (5, 25, 0)])
async def test_edits_cannot_empty_a_volume_page(test_database, edition, start, end, new_len):
    db = test_database
    volumes = [{"index": i + 1, "pages": [{"reference": str(i), "lines": [{"start": i * 10, "end": (i + 1) * 10}]}]} for i in range(3)]
    pagination_id = await db.annotation.pagination.add(edition, PaginationInput(volumes=volumes))
    before = await db.annotation.pagination.get(pagination_id)
    async with db.get_session() as session:
        with pytest.raises(DataValidationError, match="every page"):
            await session.execute_write(db.span.adjust_with_transaction, edition, start, end, new_len)
    assert await db.annotation.pagination.get(pagination_id) == before
    await db.edition.delete(edition)
    async with db.get_session() as session:
        record = await (await session.run("MATCH (n:Volume|Page|Span) RETURN count(n) AS count")).single()
        assert record["count"] == 0


async def test_schema_upgrade_rejects_legacy_empty_pages(test_database, edition):
    db = test_database
    await db.annotation.pagination.add(edition, PaginationInput(volumes=[{"pages": [
        {"reference": "1", "lines": [{"start": 0, "end": 30}]},
    ]}]))
    async with db.get_session() as session:
        page = await (await session.run("""
            MATCH (span:Span)-[:SPAN_OF]->(page:Page)
            SET span.end = span.start
            RETURN page.id AS id
        """)).single(strict=True)
    violations = await audit_schema(db._driver, db._database)
    assert violations["empty_pagination"] == [page["id"]]
    with pytest.raises(RuntimeError, match="Schema audit failed"):
        await migrate(db._driver, db._database)


async def test_empty_legacy_volume_and_imported_attributes_can_be_deleted(test_database, edition):
    db = test_database
    async with db.get_session() as session:
        await session.run("""MATCH (e:Edition {id: $id})
            CREATE (:Volume {id: 'empty'})-[:VOLUME_OF]->(:Pagination {id: 'legacy'})-[:PAGINATION_OF]->(e)
            CREATE (a:Attribute {id: 'imported'})-[:ATTRIBUTE_OF]->(e)
            CREATE (a)-[:HAS_TYPE]->(:AttributeType {name: 'legacy'})
            CREATE (:Span {start: 0, end: 1})-[:SPAN_OF]->(a)
            CREATE (a)-[:HAS_METADATA]->(:AnnotationMetadata {id: 'attribute-metadata'})""", id=edition)
    await db.edition.delete(edition)
    async with db.get_session() as session:
        record = await (await session.run("MATCH (n:Attribute|Volume|Pagination|Span|AnnotationMetadata) RETURN count(n) AS count")).single()
        assert record["count"] == 0


@pytest.mark.parametrize(
    "operation,expected",
    [
        ({"type": "insert", "position": 0, "text": "XX"}, [(0, 0), (0, 7), (7, 7), (7, 12)]),
        ({"type": "delete", "start": 2, "end": 8}, [(0, 0), (0, 2), (2, 2), (2, 4)]),
        ({"type": "replace", "start": 2, "end": 8, "text": "X"}, [(0, 0), (0, 3), (3, 3), (3, 5)]),
    ],
)
async def test_marker_only_segments_stay_empty_through_content_edits(test_database, mock_storage, operation, expected):
    from content_service import create_edition, edit_content
    from models.requests import EditionRequestModel
    from models.content_operation import ContentOperation

    db = test_database
    text = await db.text.create(TextInput(title={"en": "Marker edits"}, language="en", category_id="category"))
    data = EditionRequestModel(
        metadata={"type": "critical"},
        content="0123456789",
        segmentation={
            "segments": [
                {"reference": str(i), "lines": [{"start": a, "end": b}]}
                for i, (a, b) in enumerate([(0, 0), (0, 5), (5, 5), (5, 10)])
            ]
        },
    )
    edition_id = await create_edition(db, mock_storage, text, data)
    before = await db.annotation.segmentation.get_all_segments_by_edition(edition_id)
    await edit_content(db, mock_storage, edition_id, ContentOperation.model_validate(operation))
    after = await db.annotation.segmentation.get_all_segments_by_edition(edition_id)
    assert [s.id for s in after] == [s.id for s in before]
    assert [(line.start, line.end) for s in after for line in s.lines] == expected


@pytest.mark.parametrize(
    "operation,new_length",
    [({"type": "delete", "start": 0, "end": 10}, 10), ({"type": "replace", "start": 0, "end": 10, "text": "XYZ"}, 13)],
)
async def test_nested_toc_content_edits_remove_subtree_and_localizations(
    test_database, mock_storage, operation, new_length
):
    from content_service import create_edition, edit_content
    from models.requests import EditionRequestModel
    from models.content_operation import ContentOperation
    from models.annotation import TableOfContentsInput

    db = test_database
    text = await db.text.create(TextInput(title={"en": "Nested TOC edit"}, language="en", category_id="category"))
    edition_id = await create_edition(
        db,
        mock_storage,
        text,
        EditionRequestModel(
            metadata={"type": "critical"},
            content="0123456789abcdefghij",
            segmentation={"segments": [{"lines": [{"start": 0, "end": 20}]}]},
        ),
    )
    toc_id = await db.annotation.table_of_contents.add(
        edition_id,
        TableOfContentsInput(
            sections=[
                {
                    "title": {"en": "Removed parent"},
                    "summary": {"en": "Removed summary"},
                    "span": {"start": 0, "end": 10},
                    "subsections": [
                        {
                            "title": {"en": "Removed child"},
                            "summary": {"en": "Removed child summary"},
                            "span": {"start": 2, "end": 8},
                        }
                    ],
                },
                {"title": {"en": "Surviving sibling"}, "span": {"start": 10, "end": 20}},
            ]
        ),
    )
    before = await db.annotation.table_of_contents.get(toc_id)
    sibling = before.sections[1]
    async with db.get_session() as session:
        localized = await (
            await session.run(
                "MATCH (s:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {id:$id}) WHERE s.id <> $sibling MATCH (s)-[:HAS_TITLE|HAS_SUMMARY]->(n:Nomen)-[:HAS_LOCALIZATION]->(l:LocalizedText) RETURN collect(DISTINCT elementId(n))+collect(DISTINCT elementId(l)) AS ids",
                id=toc_id,
                sibling=sibling.id,
            )
        ).single()
    assert len(localized["ids"]) == 8
    await edit_content(db, mock_storage, edition_id, ContentOperation.model_validate(operation))
    result = await db.annotation.table_of_contents.get(toc_id)
    assert len(result.sections) == 1
    assert result.sections[0].id == sibling.id
    assert result.sections[0].title == sibling.title
    assert (result.sections[0].span.start, result.sections[0].span.end) == (new_length - 10, new_length)
    async with db.get_session() as session:
        assert (
            await (
                await session.run("MATCH (n) WHERE elementId(n) IN $ids RETURN count(n) AS count", ids=localized["ids"])
            ).single()
        )["count"] == 0


async def test_edit_sequence_preserves_independent_content_and_span_invariants(test_database, mock_storage):
    import random
    from content_service import create_edition, edit_content
    from models.requests import EditionRequestModel
    from models.content_operation import ContentOperation

    db = test_database
    text = await db.text.create(TextInput(title={"en": "Edit sequence"}, language="en", category_id="category"))
    content = "abcdefghijklmnopqrst"
    edition_id = await create_edition(
        db,
        mock_storage,
        text,
        EditionRequestModel(
            metadata={"type": "critical"},
            content=content,
            segmentation={"segments": [{"lines": [{"start": i, "end": i + 5}]} for i in range(0, 20, 5)]},
        ),
    )
    rng = random.Random(9127)
    keys = set()
    for step in range(18):
        kind = ("insert", "delete", "replace")[step % 3]
        start = rng.randrange(len(content))
        end = rng.randrange(start + 1, len(content) + 1)
        if kind == "delete" and start == 0 and end == len(content):
            kind = "insert"
        if kind == "insert":
            data = {"type": kind, "position": start, "text": "🙂x"}
            content = content[:start] + "🙂x" + content[start:]
        else:
            data = {"type": kind, "start": start, "end": end}
            replacement = "བོད་" if kind == "replace" else ""
            if replacement:
                data["text"] = replacement
            content = content[:start] + replacement + content[end:]
        await edit_content(db, mock_storage, edition_id, ContentOperation.model_validate(data))
        from tests.test_content_lifecycle import state

        snapshot = await state(db, edition_id)
        assert snapshot.object_key not in keys
        keys.add(snapshot.object_key)
        assert await mock_storage.read_text(snapshot.object_key) == content
        assert snapshot.length == len(content)
        segments = await db.annotation.segmentation.get_all_segments_by_edition(edition_id)
        lines = [line for segment in segments for line in segment.lines]
        assert lines and lines[0].start == 0 and lines[-1].end == len(content)
        assert all(0 <= line.start <= line.end <= len(content) for line in lines)
        assert all(left.end == right.start for left, right in zip(lines, lines[1:]))
