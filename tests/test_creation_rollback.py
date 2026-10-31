"""Failed multi-step creates must leave the entire existing graph unchanged."""

import pytest
from neo4j import AsyncManagedTransaction

from database.annotation.table_of_contents_database import TableOfContentsDatabase
from exceptions import DataConflictError, DataValidationError, InvalidRequestError
from identifier import generate_id
from models.annotation import PaginationInput, SegmentationInput, TableOfContentsInput
from models.category import CategoryInput
from models.edition import EditionInput
from models.person import PersonInput
from models.tag import TagInput
from models.text import TextInput
from tests.graph_assertions import snapshot_graph

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("identifier", ["bdrc", "wiki"])
async def test_duplicate_person_identifier_leaves_no_names(test_database, identifier):
    db = test_database
    external_id = {identifier: "existing-person"}
    await db.person.create(PersonInput(
        name={"en": "Existing person", "bo": "མི་"},
        alt_names=[{"en": "Existing alias"}], **external_id,
    ))
    before = await snapshot_graph(db)

    # Primary and alternative names are written before the uniqueness constraint fails.
    with pytest.raises(DataConflictError):
        await db.person.create(PersonInput(
            name={"en": "Rejected person", "bo": "མི་གཞན་"},
            alt_names=[{"en": "Rejected alias", "bo": "མིང་གཞན་"}], **external_id,
        ))

    assert await snapshot_graph(db) == before


@pytest.mark.parametrize("identifier", ["bdrc", "wiki"])
async def test_duplicate_text_identifier_leaves_no_work_or_titles(test_database, identifier):
    db = test_database
    external_id = {identifier: "existing-text"}
    await db.text.create(TextInput(
        title={"en": "Existing text"}, language="en", category_id="category", **external_id,
    ))
    before = await snapshot_graph(db)

    with pytest.raises(DataConflictError):
        await db.text.create(TextInput(
            title={"en": "Rejected text", "bo": "དཔེ་ཆ་"},
            alt_titles=[{"en": "Rejected alternative title"}],
            language="en", category_id="category", **external_id,
        ))

    assert await snapshot_graph(db) == before


@pytest.mark.parametrize("relation", [None, "translation_of", "commentary_of"], ids=[
    "original", "translation", "commentary",
])
async def test_missing_tag_rolls_back_text_and_contributions(test_database, relation):
    db = test_database
    person = await db.person.create(PersonInput(name={"en": "Existing author"}))
    original = await db.text.create(TextInput(
        title={"bo": "དཔེ་ཆ་"}, language="bo", category_id="category",
    ))
    fields = {relation: original} if relation else {}
    if relation != "translation_of":
        fields["category_id"] = "category"
    before = await snapshot_graph(db)

    # Tags are checked after the text, titles, category link and contributions are written.
    # Translations use an existing Work; originals and commentaries create a new one.
    with pytest.raises(DataValidationError, match="Referenced tags do not exist"):
        await db.text.create(TextInput(
            title={"en": "Rejected text"}, alt_titles=[{"en": "Rejected alias"}], language="en",
            contributions=[
                {"type": "person", "id": person, "role": "author"},
                {"type": "ai", "id": "new-ai-contributor", "role": "translator"},
            ],
            tag_ids=["missing-tag"], **fields,
        ), application="test_application")

    assert await snapshot_graph(db) == before


@pytest.mark.parametrize("entity", ["category", "tag"])
async def test_invalid_description_language_rolls_back_title(test_database, entity):
    db = test_database
    model = CategoryInput if entity == "category" else TagInput
    fields = {"parent_id": "category"} if entity == "category" else {}
    before = await snapshot_graph(db)

    # The valid title is persisted before the description's language is checked.
    with pytest.raises(InvalidRequestError, match="xx"):
        await getattr(db, entity).create(model(
            title={"en": "Rejected title", "bo": "མིང་"},
            description={"xx": "Unknown language"}, **fields,
        ), "test_application")

    assert await snapshot_graph(db) == before


async def test_invalid_pagination_rolls_back_new_text_edition_and_segmentation(test_database):
    db = test_database
    before = await snapshot_graph(db)

    # Pagination fails after the nested text, edition and segmentation have all been written.
    with pytest.raises(DataValidationError, match="Offsets extend to 7"):
        await db.edition.create(
            EditionInput(
                type="critical", source="Rollback test source",
                incipit_title={"en": "Rejected incipit"},
                alt_incipit_titles=[{"en": "Rejected alternate incipit"}],
            ),
            generate_id(), generate_id(), content_length=6,
            text=TextInput(
                title={"en": "Rejected edition text"}, alt_titles=[{"en": "Rejected title alias"}],
                language="en", category_id="category",
                contributions=[{"type": "ai", "id": "edition-ai", "role": "author"}],
            ),
            segmentation=SegmentationInput(
                segments=[{"lines": [{"start": 0, "end": 6}]}], metadata={"name": "Segments"},
            ),
            pagination=PaginationInput(volumes=[{
                "pages": [{"reference": "1", "lines": [{"start": 0, "end": 7}]}],
            }]),
        )

    assert await snapshot_graph(db) == before


async def test_failure_after_toc_hierarchy_rolls_back_sections_names_and_revision(test_database, monkeypatch):
    db = test_database
    text = await db.text.create(TextInput(title={"en": "TOC rollback"}, language="en", category_id="category"))
    edition = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition, text, 6)
    before = await snapshot_graph(db)
    run = AsyncManagedTransaction.run

    async def fail_after_hierarchy(tx, query, *args, **kwargs):
        result = await run(tx, query, *args, **kwargs)
        if query == TableOfContentsDatabase.CREATE_HIERARCHY_QUERY:
            await result.consume()
            raise RuntimeError("after TOC hierarchy")
        return result

    monkeypatch.setattr(AsyncManagedTransaction, "run", fail_after_hierarchy)
    with pytest.raises(RuntimeError, match="after TOC hierarchy"):
        await db.annotation.table_of_contents.add(edition, TableOfContentsInput(
            metadata={"name": "Rejected TOC"},
            sections=[{
                "title": {"en": "Parent", "bo": "མིང་"}, "summary": {"en": "Parent summary"},
                "span": {"start": 0, "end": 6},
                "subsections": [{
                    "title": {"en": "Child"}, "summary": {"en": "Child summary"},
                    "span": {"start": 1, "end": 3},
                }],
            }],
        ))

    assert await snapshot_graph(db) == before
