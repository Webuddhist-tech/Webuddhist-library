"""Regression tests for Neo4j create-result handling.

Create methods must verify the identifiers returned by Neo4j rather than assuming
that submitted identifiers were persisted.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import database.annotation.pagination_database as pagination_module
import database.annotation.segmentation_database as segmentation_module
import database.annotation.single_span as single_span_module
import database.annotation.table_of_contents_database as toc_module
import database.nomen_database as nomen_module
import database.person_database as person_module
import database.text_database as text_module
from database.annotation.bibliographic_database import BibliographicDatabase
from database.annotation.mark_database import MarkDatabase
from database.annotation.note_database import NoteDatabase
from database.annotation.pagination_database import PaginationDatabase
from database.annotation.segmentation_database import SegmentationDatabase
from database.annotation.table_of_contents_database import TableOfContentsDatabase
from database.api_key_database import ApiKeyDatabase
from database.application_database import ApplicationDatabase
from database.category_database import CategoryDatabase
from database.content_state import ContentState
from database.database_validator import DatabaseValidator
from database.edition_database import EditionDatabase
from database.language_database import LanguageDatabase
from database.nomen_database import NomenDatabase
from database.person_database import PersonDatabase
from database.recording_database import RecordingDatabase
from database.tag_database import TagDatabase
from database.text_database import TextDatabase
from models.annotation import (
    BibliographicMetadataInput,
    MarkInput,
    NoteInput,
    Page,
    PaginationInput,
    SegmentationInput,
    SegmentInput,
    Span,
    Volume,
)
from models.enums import AudioFormat, BibliographyType, MarkType


class TrackingResult:
    def __init__(self, record):
        self.record = record
        self.single_strict_args = []

    async def data(self):
        return []

    async def consume(self):
        return None

    async def single(self, strict=False):
        self.single_strict_args.append(strict)
        return self.record


class FakeTransaction:
    def __init__(self, results_by_query, default_record=None):
        self.results_by_query = results_by_query
        self.default_record = default_record

    async def run(self, query, **_params):
        result = self.results_by_query.get(query)
        if result is not None:
            return result
        return TrackingResult(self.default_record)


class FakeSession:
    def __init__(self, tx):
        self.tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute_write(self, callback, *args, **kwargs):
        return await callback(self.tx, *args, **kwargs)


class FakeDatabase:
    def __init__(self, tx):
        self.session = FakeSession(tx)
        self.application = SimpleNamespace(exists=AsyncMock(return_value=True))

    def get_session(self):
        return self.session


def assert_strict_neo4j_id(result, returned_id, field):
    assert result.single_strict_args == [True]
    assert returned_id == result.record[field]


@pytest.fixture
def skip_span_validation(monkeypatch):
    state = ContentState("edition-id", "text-id", "key", 1, 0)
    for module in (single_span_module, pagination_module, segmentation_module, toc_module):
        monkeypatch.setattr(module, "touch_edition", AsyncMock(return_value=state))


async def test_note_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-note-id")
    create_result = TrackingResult({"id": "neo4j-note-id"})
    tx = FakeTransaction({NoteDatabase.CREATE_QUERY: create_result})

    returned_id = await NoteDatabase.add_with_transaction(
        tx,
        "edition-id",
        NoteInput(span=Span(start=0, end=1), text="note"),
        "durchen",
    )

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_bibliographic_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-bibliographic-id")
    create_result = TrackingResult({"id": "neo4j-bibliographic-id"})
    tx = FakeTransaction({BibliographicDatabase.CREATE_QUERY: create_result})

    returned_id = await BibliographicDatabase.add_with_transaction(
        tx,
        "edition-id",
        BibliographicMetadataInput(
            span=Span(start=0, end=1),
            type=BibliographyType.COLOPHON,
        ),
    )

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_mark_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-mark-id")
    create_result = TrackingResult({"id": "neo4j-mark-id"})
    tx = FakeTransaction({MarkDatabase.CREATE_QUERY: create_result})

    returned_id = await MarkDatabase.add_with_transaction(
        tx,
        "edition-id",
        MarkInput(span=Span(start=0, end=1)),
        MarkType.YIGCHUNG,
    )

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_pagination_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(pagination_module, "generate_id", lambda: "submitted-pagination-id")
    create_result = TrackingResult({"id": "neo4j-pagination-id"})
    tx = FakeTransaction({PaginationDatabase.CREATE_QUERY: create_result})
    pagination = PaginationInput(
        volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=1)])])]
    )

    returned_id = await PaginationDatabase.add_with_transaction(tx, "edition-id", pagination)

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_segmentation_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(segmentation_module, "generate_id", lambda: "submitted-segmentation-id")
    create_result = TrackingResult({"id": "neo4j-segmentation-id", "segment_count": 1})
    tx = FakeTransaction({SegmentationDatabase.CREATE_QUERY: create_result})
    segmentation = SegmentationInput(
        segments=[SegmentInput(lines=[Span(start=0, end=1)])]
    )

    returned_id = await SegmentationDatabase.add_with_transaction(tx, "edition-id", segmentation)

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_table_of_contents_create_strictly_returns_neo4j_id(
    monkeypatch,
    skip_span_validation,
):
    monkeypatch.setattr(toc_module, "generate_id", lambda: "submitted-toc-id")
    monkeypatch.setattr(
        TableOfContentsDatabase,
        "_flatten_sections",
        AsyncMock(return_value=[{"id": "section-id", "parent_id": None}]),
    )
    create_result = TrackingResult({"id": "neo4j-toc-id"})
    sections_result = TrackingResult({"count": 1})
    hierarchy_result = TrackingResult({"count": 0})
    tx = FakeTransaction(
        {
            TableOfContentsDatabase.CREATE_TOC_QUERY: create_result,
            TableOfContentsDatabase.CREATE_SECTIONS_QUERY: sections_result,
            TableOfContentsDatabase.CREATE_HIERARCHY_QUERY: hierarchy_result,
        }
    )
    toc = SimpleNamespace(
        max_end=1,
        metadata=None,
        sections=[SimpleNamespace()],
    )

    returned_id = await TableOfContentsDatabase.add_with_transaction(tx, "edition-id", toc)

    assert_strict_neo4j_id(create_result, returned_id, "id")


@pytest.mark.parametrize("returned_ids", [["submitted-nomen-id"], [], ["unexpected-id"]])
async def test_nomen_create_verifies_persisted_ids(monkeypatch, returned_ids):
    monkeypatch.setattr(DatabaseValidator, "validate_language_codes_exist", AsyncMock())
    monkeypatch.setattr(nomen_module, "generate_id", lambda: "submitted-nomen-id")
    create_result = SimpleNamespace(data=AsyncMock(return_value=[{"id": value} for value in returned_ids]))
    tx = FakeTransaction({NomenDatabase.CREATE_MANY_QUERY: create_result})

    if returned_ids == ["submitted-nomen-id"]:
        assert await NomenDatabase.create_with_transaction(tx, {"en": "Title"}) == "submitted-nomen-id"
    else:
        with pytest.raises(RuntimeError, match="Incomplete nomen creation"):
            await NomenDatabase.create_with_transaction(tx, {"en": "Title"})


async def test_person_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(person_module, "generate_id", lambda: "submitted-person-id")
    monkeypatch.setattr(
        person_module.NomenDatabase,
        "create_with_transaction",
        AsyncMock(return_value="nomen-id"),
    )
    create_result = TrackingResult({"person_id": "neo4j-person-id"})
    database = FakeDatabase(FakeTransaction({PersonDatabase.CREATE_QUERY: create_result}))
    person = SimpleNamespace(
        name=SimpleNamespace(root={"en": "Person"}),
        alt_names=None,
        bdrc=None,
        wiki=None,
    )

    returned_id = await PersonDatabase(database).create(person)

    assert_strict_neo4j_id(create_result, returned_id, "person_id")


@pytest.mark.parametrize(
    ("translation_of", "commentary_of", "create_query"),
    [
        (None, None, TextDatabase.CREATE_STANDALONE_QUERY),
        ("target-text-id", None, TextDatabase.CREATE_TRANSLATION_QUERY),
        (None, "target-text-id", TextDatabase.CREATE_COMMENTARY_QUERY),
    ],
)
async def test_text_create_strictly_returns_neo4j_id(
    monkeypatch,
    translation_of,
    commentary_of,
    create_query,
):
    monkeypatch.setattr(TextDatabase, "_validate_related_text", AsyncMock())
    monkeypatch.setattr(DatabaseValidator, "validate_text_creation", AsyncMock())
    monkeypatch.setattr(DatabaseValidator, "validate_language_code_exists", AsyncMock())
    monkeypatch.setattr(DatabaseValidator, "validate_category_exists", AsyncMock())
    monkeypatch.setattr(
        text_module.NomenDatabase,
        "create_with_transaction",
        AsyncMock(return_value="nomen-id"),
    )
    monkeypatch.setattr(text_module, "generate_id", lambda: "submitted-work-id")
    create_result = TrackingResult({"text_id": "neo4j-text-id", "work_id": "neo4j-work-id"})
    tx = FakeTransaction({create_query: create_result})
    text = SimpleNamespace(
        language="en",
        alt_titles=None,
        title=SimpleNamespace(root={"en": "Text"}),
        bdrc=None,
        wiki=None,
        date=None,
        translation_of=translation_of,
        commentary_of=commentary_of,
        license=SimpleNamespace(value="public"),
        category_id=None,
        contributions=[],
        tag_ids=[],
    )

    returned_id = await TextDatabase.create_with_transaction(tx, text, "submitted-text-id")

    assert_strict_neo4j_id(create_result, returned_id, "text_id")


async def test_edition_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(EditionDatabase, "_validate_create", AsyncMock())
    create_result = TrackingResult({"edition_id": "neo4j-edition-id"})
    tx = FakeTransaction({EditionDatabase.CREATE_QUERY: create_result})
    edition = SimpleNamespace(
        incipit_title=None,
        bdrc=None,
        wiki=None,
        type=SimpleNamespace(value="critical"),
        colophon=None,
        source=None,
    )

    returned_id = await EditionDatabase.create_with_transaction(
        tx,
        edition,
        "text-id",
        "submitted-edition-id",
        1,
    )

    assert_strict_neo4j_id(create_result, returned_id, "edition_id")


async def test_recording_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(DatabaseValidator, "validate_edition_exists", AsyncMock())
    monkeypatch.setattr(
        DatabaseValidator,
        "validate_contribution_references",
        AsyncMock(),
    )
    create_result = TrackingResult({"recording_id": "neo4j-recording-id"})
    tx = FakeTransaction({RecordingDatabase.CREATE_QUERY: create_result})
    recording = SimpleNamespace(
        title=None,
        language=None,
        duration_ms=None,
        date=None,
        license=SimpleNamespace(value="public"),
        contributions=[],
    )

    returned_id = await RecordingDatabase.add_with_transaction(
        tx,
        "edition-id",
        recording,
        "submitted-recording-id",
        AudioFormat.MP3,
        10,
    )

    assert_strict_neo4j_id(create_result, returned_id, "recording_id")


async def test_application_create_strictly_returns_neo4j_id():
    create_result = TrackingResult({"id": "neo4j-application-id"})
    database = FakeDatabase(
        FakeTransaction({ApplicationDatabase.CREATE_QUERY: create_result})
    )

    returned_id = await ApplicationDatabase(database).create(
        "submitted-application-id",
        "Application",
    )

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_language_create_strictly_returns_neo4j_id():
    create_result = TrackingResult({"code": "neo4j-language-code"})
    database = FakeDatabase(FakeTransaction({LanguageDatabase.CREATE_QUERY: create_result}))

    returned_id = await LanguageDatabase(database).create(
        "submitted-language-code",
        "Language",
    )

    assert_strict_neo4j_id(create_result, returned_id, "code")


async def test_api_key_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(ApiKeyDatabase, "_generate_api_key", staticmethod(lambda: "raw-key"))
    create_result = TrackingResult({"id": "neo4j-key-id"})
    database = FakeDatabase(FakeTransaction({ApiKeyDatabase.CREATE_QUERY: create_result}))

    returned_id, _raw_key = await ApiKeyDatabase(database).create(
        "submitted-key-id",
        "Key",
        "key@example.com",
    )

    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_category_create_keeps_strict_neo4j_id_contract(monkeypatch):
    create_result = TrackingResult({"category_id": "neo4j-category-id"})
    database = FakeDatabase(FakeTransaction({CategoryDatabase.CREATE_QUERY: create_result}))
    category_database = CategoryDatabase(database)
    monkeypatch.setattr(category_database, "_validate_not_exists_tx", AsyncMock())
    monkeypatch.setattr(
        "database.category_database.NomenDatabase.create_with_transaction",
        AsyncMock(return_value="nomen-id"),
    )
    category = SimpleNamespace(
        parent_id=None,
        title=SimpleNamespace(root={"en": "Category"}),
        description=None,
    )

    returned_id = await category_database.create(category, "application-id")

    assert_strict_neo4j_id(create_result, returned_id, "category_id")


async def test_tag_create_keeps_strict_neo4j_id_contract(monkeypatch):
    create_result = TrackingResult({"tag_id": "neo4j-tag-id"})
    database = FakeDatabase(FakeTransaction({TagDatabase.CREATE_QUERY: create_result}))
    tag_database = TagDatabase(database)
    monkeypatch.setattr(tag_database, "_validate_not_exists_tx", AsyncMock())
    monkeypatch.setattr(
        "database.tag_database.NomenDatabase.create_with_transaction",
        AsyncMock(return_value="nomen-id"),
    )
    tag = SimpleNamespace(
        title=SimpleNamespace(root={"en": "Tag"}),
        description=None,
    )

    returned_id = await tag_database.create(tag, "application-id")

    assert_strict_neo4j_id(create_result, returned_id, "tag_id")
