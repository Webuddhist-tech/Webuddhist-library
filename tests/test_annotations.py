# pylint: disable=redefined-outer-name
"""
Integration tests for v2 annotation endpoints using real Neo4j test instance.

Tests endpoints:
- GET /v2/editions/{edition_id}/segmentation
- GET /v2/paginations/{pagination_id}
- GET /v2/durchens/{note_id}
- GET /v2/yigchungs/{mark_id}
- GET /v2/bibliographic/{bibliographic_id}
- DELETE /v2/editions/{edition_id}/segmentation
- DELETE /v2/paginations/{pagination_id}
- DELETE /v2/durchens/{note_id}
- DELETE /v2/yigchungs/{mark_id}
- DELETE /v2/bibliographic/{bibliographic_id}

Requires environment variables:
- NEO4J_TEST_URI: Neo4j test instance URI
- NEO4J_TEST_PASSWORD: Password for test instance
"""

import logging

import pytest
from identifier import generate_id
from models.annotation import (
    AnnotationMetadata,
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
from models.base import LocalizedString
from models.contribution import PersonContributionInput
from models.edition import EditionInput, EditionType
from models.enums import BibliographyType, ContributorRole
from models.person import PersonInput
from models.text import TextInput

logger = logging.getLogger(__name__)


@pytest.fixture
def test_person_data() -> PersonInput:
    """Sample person data for testing"""
    return PersonInput(
        name=LocalizedString({"en": "Test Author", "bo": "སློབ་དཔོན།"}),
        bdrc="P123456",
    )


@pytest.mark.asyncio(loop_scope="session")
class TestAnnotationsEndpoints:
    """Base class with helper methods for annotation tests"""

    async def _create_test_person(self, db, person_data: PersonInput) -> str:
        """Helper to create a test person in the database"""
        return await db.person.create(person_data)

    async def _create_test_text(self, db, person_id: str, title: LocalizedString | None = None) -> str:
        """Helper to create a test text"""
        if title is None:
            title = LocalizedString({"en": "Test text", "bo": "བརྟག་དཔྱད།"})
        text_data = TextInput(
            category_id="category",
            title=title,
            language="bo",
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        return await db.text.create(text_data)

    async def _create_test_edition(
        self,
        db,
        text_id: str,
        content: str = "Sample text content",
        edition_type: EditionType = EditionType.DIPLOMATIC,
        bdrc: str | None = None,
    ) -> str:
        """Helper to create a edition for testing (no base text storage needed for annotation tests).

        Diplomatic editions must have exactly one pagination (enforced by a Neo4j trigger), so one is
        created in the same transaction. Critical editions have no such requirement and are created
        without a pagination, allowing callers to attach their own.
        """
        edition_id = generate_id()
        is_diplomatic = edition_type == EditionType.DIPLOMATIC
        edition_data = EditionInput(
            type=edition_type,
            bdrc=(bdrc or f"W{edition_id[:8]}") if is_diplomatic else None,
            source="Test Source",
        )
        pagination = None
        if is_diplomatic:
            pagination = PaginationInput(
                volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=max(len(content), 1))])])]
            )
        await db.edition.create(
            edition_data, edition_id, text_id, content_length=max(len(content), 1), pagination=pagination
        )
        return edition_id


class TestGetSegmentation(TestAnnotationsEndpoints):
    """Tests for GET /v2/editions/{edition_id}/segmentation"""

    async def test_get_segmentation_success(self, client, test_database, test_person_data):
        """Test successful segmentation retrieval"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        segmentation = SegmentationInput(
            segments=[
                SegmentInput(lines=[Span(start=0, end=5)]),
                SegmentInput(lines=[Span(start=5, end=10)]),
            ]
        )
        segmentation_id = await test_database.annotation.segmentation.add(edition_id, segmentation)

        response = await client.get(f"/v2/editions/{edition_id}/segmentation")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == segmentation_id
        assert body["edition_id"] == edition_id
        assert body["text_id"] == text_id

        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        data = segments_response.json()["items"]
        assert len(data) == 2
        assert all("segmentation_id" not in segment for segment in data)
        assert all("edition_id" not in segment for segment in data)
        assert all("text_id" not in segment for segment in data)

    async def test_get_segmentation_not_found(self, client, test_database):
        """Test segmentation retrieval with non-existent ID"""
        response = await client.get("/v2/editions/nonexistent_id/segmentation")

        assert response.status_code == 404
        assert "error" in response.json()

    async def test_get_segmentation_with_multiple_spans(self, client, test_database, test_person_data):
        """Test segmentation with segments containing multiple spans"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789ABCDEF")

        segmentation = SegmentationInput(
            segments=[
                SegmentInput(lines=[Span(start=0, end=4), Span(start=4, end=8)]),
                SegmentInput(lines=[Span(start=8, end=16)]),
            ]
        )
        await test_database.annotation.segmentation.add(edition_id, segmentation)

        response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")

        assert response.status_code == 200
        data = response.json()["items"]
        assert len(data) == 2
        assert len(data[0]["lines"]) == 2



class TestDeleteSegmentation(TestAnnotationsEndpoints):

    async def test_delete_segmentation_success(self, client, test_database, test_person_data):
        """Test successful segmentation deletion"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        segmentation = SegmentationInput(
            segments=[SegmentInput(reference="source-1", lines=[Span(start=0, end=10)])]
        )
        segmentation_id = await test_database.annotation.segmentation.add(edition_id, segmentation)

        get_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert get_response.status_code == 200

        response = await client.delete(f"/v2/editions/{edition_id}/segmentation")

        assert response.status_code == 204

        verify_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert verify_response.status_code == 404

    async def test_delete_segmentation_not_found(self, client, test_database):
        """Test deleting non-existent segmentation (should succeed silently)"""
        response = await client.delete("/v2/editions/nonexistent_id/segmentation")

        assert response.status_code == 404

    async def test_delete_segmentation_idempotent(self, client, test_database, test_person_data):
        """Test that deleting the same segmentation twice is idempotent"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        segmentation = SegmentationInput(
            segments=[SegmentInput(lines=[Span(start=0, end=10)])]
        )
        await test_database.annotation.segmentation.add(edition_id, segmentation)

        first_delete = await client.delete(f"/v2/editions/{edition_id}/segmentation")
        assert first_delete.status_code == 204

        second_delete = await client.delete(f"/v2/editions/{edition_id}/segmentation")
        assert second_delete.status_code == 204
        assert (await client.get(f"/v2/editions/{edition_id}/segmentation")).status_code == 404

    async def test_delete_segmentation_removes_alignment_relationships(self, client, test_database, test_person_data):
        """Deleting a segmentation should remove direct alignment relationships."""
        person_id = await self._create_test_person(test_database, test_person_data)
        source_text_id = await self._create_test_text(test_database, person_id)
        target_text_id = await self._create_test_text(
            test_database,
            person_id,
            title=LocalizedString({"en": "Target", "bo": "དམིགས།"}),
        )
        source_edition_id = await self._create_test_edition(
            test_database, source_text_id, "Source text"
        )
        target_edition_id = await self._create_test_edition(
            test_database, target_text_id, "Target text"
        )

        await test_database.annotation.segmentation.add(
            source_edition_id,
            SegmentationInput(segments=[SegmentInput(reference="source-1", lines=[Span(start=0, end=11)])]),
        )
        await test_database.annotation.segmentation.add(
            target_edition_id,
            SegmentationInput(segments=[SegmentInput(reference="target-1", lines=[Span(start=0, end=11)])]),
        )
        source_segment = (
            await test_database.annotation.segmentation.get_segments_by_edition(source_edition_id, offset=0, limit=1)
        )[0]
        target_segment = (
            await test_database.annotation.segmentation.get_segments_by_edition(target_edition_id, offset=0, limit=1)
        )[0]

        response = await client.put(
            f"/v2/editions/{source_edition_id}/alignments/{target_edition_id}",
            json={"alignments": [{
                "source_segment_reference": source_segment.reference,
                "target_segment_reference": target_segment.reference,
            }]},
        )
        assert response.status_code == 204

        response = await client.delete(f"/v2/editions/{source_edition_id}/segmentation")
        assert response.status_code == 204

        response = await client.get(f"/v2/editions/{source_edition_id}/alignments/{target_edition_id}")
        assert response.status_code == 200
        assert response.json()["items"] == []

class TestGetPagination(TestAnnotationsEndpoints):
    """Tests for GET /v2/paginations/{pagination_id}"""

    async def test_get_pagination_success(self, client, test_database, test_person_data):
        """Test successful pagination retrieval"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination = PaginationInput(
            volumes=[
                Volume(
                    pages=[
                        Page(reference="1a", lines=[Span(start=0, end=8)]),
                        Page(reference="1b", lines=[Span(start=8, end=16)]),
                    ]
                )
            ]
        )
        pagination_id = await test_database.annotation.pagination.add(edition_id, pagination)

        response = await client.get(f"/v2/paginations/{pagination_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == pagination_id
        assert data["edition_id"] == edition_id
        assert data["text_id"] == text_id
        assert "volumes" in data
        assert len(data["volumes"]) == 1
        assert len(data["volumes"][0]["pages"]) == 2
        assert data["volumes"][0]["pages"][0]["reference"] == "1a"

    async def test_get_pagination_not_found(self, client, test_database):
        """Test pagination retrieval with non-existent ID"""
        response = await client.get("/v2/paginations/nonexistent_id")

        assert response.status_code == 404
        assert "error" in response.json()

    async def test_get_pagination_with_multiple_lines_per_page(self, client, test_database, test_person_data):
        """Test pagination with pages containing multiple line spans"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination = PaginationInput(
            volumes=[
                Volume(
                    pages=[
                        Page(
                            reference="1a",
                            lines=[Span(start=0, end=4), Span(start=4, end=8)],
                        ),
                    ]
                )
            ]
        )
        pagination_id = await test_database.annotation.pagination.add(edition_id, pagination)

        response = await client.get(f"/v2/paginations/{pagination_id}")

        assert response.status_code == 200
        data = response.json()
        assert len(data["volumes"][0]["pages"][0]["lines"]) == 2


class TestDeletePagination(TestAnnotationsEndpoints):
    """Tests for DELETE /v2/paginations/{pagination_id}"""

    async def test_delete_pagination_success(self, client, test_database, test_person_data):
        """Test successful pagination deletion"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789", edition_type=EditionType.CRITICAL
        )

        pagination = PaginationInput(
            volumes=[
                Volume(
                    pages=[Page(reference="1a", lines=[Span(start=0, end=10)])]
                )
            ]
        )
        pagination_id = await test_database.annotation.pagination.add(edition_id, pagination)

        get_response = await client.get(f"/v2/paginations/{pagination_id}")
        assert get_response.status_code == 200

        response = await client.delete(f"/v2/paginations/{pagination_id}")

        assert response.status_code == 204

        verify_response = await client.get(f"/v2/paginations/{pagination_id}")
        assert verify_response.status_code == 404

    async def test_delete_pagination_not_found(self, client, test_database):
        """Test deleting non-existent pagination (should succeed silently)"""
        response = await client.delete("/v2/paginations/nonexistent_id")

        assert response.status_code == 204


class TestGetDurchen(TestAnnotationsEndpoints):
    """Tests for GET /v2/durchens/{note_id}"""

    async def _setup_note_type(self, test_database) -> None:
        """Ensure NoteType node exists for durchen"""
        async with test_database.get_session() as session:
            await session.run("MERGE (:NoteType {name: 'durchen'})")

    async def test_get_durchen_success(self, client, test_database, test_person_data):
        """Test successful durchen note retrieval"""
        await self._setup_note_type(test_database)
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        note = NoteInput(span=Span(start=0, end=5), text="Variant reading note")
        note_id = await test_database.annotation.note.add_durchen(edition_id, note)

        response = await client.get(f"/v2/durchens/{note_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == note_id
        assert data["edition_id"] == edition_id
        assert data["text_id"] == text_id
        assert data["text"] == "Variant reading note"
        assert data["span"]["start"] == 0
        assert data["span"]["end"] == 5

    async def test_get_durchen_not_found(self, client, test_database):
        """Test durchen retrieval with non-existent ID"""
        response = await client.get("/v2/durchens/nonexistent_id")

        assert response.status_code == 404
        assert "error" in response.json()


class TestDeleteDurchen(TestAnnotationsEndpoints):
    """Tests for DELETE /v2/durchens/{note_id}"""

    async def _setup_note_type(self, test_database) -> None:
        """Ensure NoteType node exists for durchen"""
        async with test_database.get_session() as session:
            await session.run("MERGE (:NoteType {name: 'durchen'})")

    async def test_delete_durchen_success(self, client, test_database, test_person_data):
        """Test successful durchen note deletion"""
        await self._setup_note_type(test_database)
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        note = NoteInput(span=Span(start=0, end=5), text="Note to delete")
        note_id = await test_database.annotation.note.add_durchen(edition_id, note)

        get_response = await client.get(f"/v2/durchens/{note_id}")
        assert get_response.status_code == 200

        response = await client.delete(f"/v2/durchens/{note_id}")

        assert response.status_code == 204

        verify_response = await client.get(f"/v2/durchens/{note_id}")
        assert verify_response.status_code == 404

    async def test_delete_durchen_not_found(self, client, test_database):
        """Test deleting non-existent durchen (should succeed silently)"""
        response = await client.delete("/v2/durchens/nonexistent_id")

        assert response.status_code == 204


class TestYigchung(TestAnnotationsEndpoints):
    """Tests for the yigchung mark annotation endpoints."""

    async def test_yigchung_lifecycle(self, client, test_database, test_person_data):
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        create_response = await client.post(
            f"/v2/editions/{edition_id}/yigchungs",
            json=[
                {"span": {"start": 2, "end": 5}},
                {"span": {"start": 6, "end": 9}},
            ],
        )
        assert create_response.status_code == 201
        mark_ids = [item["id"] for item in create_response.json()]
        assert len(mark_ids) == 2

        list_response = await client.get(f"/v2/editions/{edition_id}/yigchungs")
        assert list_response.status_code == 200
        assert list_response.json() == [
            {
                "id": mark_ids[0],
                "edition_id": edition_id,
                "text_id": text_id,
                "span": {"start": 2, "end": 5},
                "metadata": None,
            },
            {
                "id": mark_ids[1],
                "edition_id": edition_id,
                "text_id": text_id,
                "span": {"start": 6, "end": 9},
                "metadata": None,
            },
        ]

        get_response = await client.get(f"/v2/yigchungs/{mark_ids[0]}")
        assert get_response.status_code == 200
        assert get_response.json() == list_response.json()[0]

        delete_response = await client.delete(f"/v2/yigchungs/{mark_ids[0]}")
        assert delete_response.status_code == 204
        assert (await client.get(f"/v2/yigchungs/{mark_ids[0]}")).status_code == 404

    async def test_create_yigchungs_rejects_empty_array(self, client):
        response = await client.post("/v2/editions/edition-id/yigchungs", json=[])

        assert response.status_code == 422

    async def test_get_yigchung_not_found(self, client, test_database):
        response = await client.get("/v2/yigchungs/nonexistent_id")

        assert response.status_code == 404
        assert "error" in response.json()

    async def test_delete_yigchung_not_found(self, client, test_database):
        response = await client.delete("/v2/yigchungs/nonexistent_id")

        assert response.status_code == 204


class TestGetBibliographic(TestAnnotationsEndpoints):
    """Tests for GET /v2/bibliographic/{bibliographic_id}"""

    async def _setup_bibliography_types(self, test_database) -> None:
        """Ensure BibliographyType nodes exist"""
        async with test_database.get_session() as session:
            await session.run("MERGE (:BibliographyType {name: 'colophon'})")
            await session.run("MERGE (:BibliographyType {name: 'incipit'})")

    async def test_get_bibliographic_success(self, client, test_database, test_person_data):
        """Test successful bibliographic metadata retrieval"""
        await self._setup_bibliography_types(test_database)
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        item = BibliographicMetadataInput(span=Span(start=0, end=10), type=BibliographyType.COLOPHON)
        bibliographic_id = await test_database.annotation.bibliographic.add(edition_id, item)

        response = await client.get(f"/v2/bibliographic/{bibliographic_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == bibliographic_id
        assert data["edition_id"] == edition_id
        assert data["text_id"] == text_id
        assert data["type"] == "colophon"
        assert data["span"]["start"] == 0
        assert data["span"]["end"] == 10

    async def test_get_bibliographic_not_found(self, client, test_database):
        """Test bibliographic retrieval with non-existent ID"""
        response = await client.get("/v2/bibliographic/nonexistent_id")

        assert response.status_code == 404
        assert "error" in response.json()

    async def test_get_bibliographic_different_types(self, client, test_database, test_person_data):
        """Test bibliographic metadata with different types"""
        await self._setup_bibliography_types(test_database)
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789ABCDEF")

        items = [
            BibliographicMetadataInput(span=Span(start=0, end=8), type=BibliographyType.COLOPHON),
            BibliographicMetadataInput(span=Span(start=8, end=16), type=BibliographyType.INCIPIT),
        ]
        bibliographic_ids = [
            await test_database.annotation.bibliographic.add(edition_id, items[0]),
            await test_database.annotation.bibliographic.add(edition_id, items[1])
        ]

        responses = [
            await client.get(f"/v2/bibliographic/{bibliographic_ids[0]}"),
            await client.get(f"/v2/bibliographic/{bibliographic_ids[1]}")
        ]
        
        types = [resp.json()["type"] for resp in responses]
        assert "colophon" in types
        assert "incipit" in types
        assert len(types) == 2


class TestDeleteBibliographic(TestAnnotationsEndpoints):
    """Tests for DELETE /v2/bibliographic/{bibliographic_id}"""

    async def _setup_bibliography_types(self, test_database) -> None:
        """Ensure BibliographyType nodes exist"""
        async with test_database.get_session() as session:
            await session.run("MERGE (:BibliographyType {name: 'colophon'})")

    async def test_delete_bibliographic_success(self, client, test_database, test_person_data):
        """Test successful bibliographic metadata deletion"""
        await self._setup_bibliography_types(test_database)
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        item = BibliographicMetadataInput(span=Span(start=0, end=10), type=BibliographyType.COLOPHON)
        bibliographic_id = await test_database.annotation.bibliographic.add(edition_id, item)

        get_response = await client.get(f"/v2/bibliographic/{bibliographic_id}")
        assert get_response.status_code == 200

        response = await client.delete(f"/v2/bibliographic/{bibliographic_id}")

        assert response.status_code == 204

        verify_response = await client.get(f"/v2/bibliographic/{bibliographic_id}")
        assert verify_response.status_code == 404

    async def test_delete_bibliographic_not_found(self, client, test_database):
        """Test deleting non-existent bibliographic (should succeed silently)"""
        response = await client.delete("/v2/bibliographic/nonexistent_id")

        assert response.status_code == 204


class TestAddAnnotationSpanBounds(TestAnnotationsEndpoints):
    """Tests that annotation spans must fit the edition's content length"""

    @pytest.mark.parametrize("inline", [False, True])
    @pytest.mark.parametrize(
        ("spans", "status"),
        [
            ([(0, 5), (6, 10)], 422),
            ([(0, 6), (5, 10)], 422),
            ([(5, 10), (0, 5)], 422),
            ([(0, 10), (5, 5)], 422),
            ([(0, 0), (0, 5), (5, 5), (5, 10)], 201),
            ([(2, 5), (5, 8)], 201),
        ],
    )
    async def test_segmentation_requires_contiguous_segments(
        self, client, test_database, test_person_data, inline, spans, status
    ):
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        segmentation = {"segments": [{"lines": [{"start": start, "end": end}]} for start, end in spans]}
        if inline:
            response = await client.post(
                f"/v2/texts/{text_id}/editions",
                json={"metadata": {"type": "critical"}, "content": "0123456789", "segmentation": segmentation},
            )
        else:
            edition_id = await self._create_test_edition(
                test_database, text_id, "0123456789", edition_type=EditionType.CRITICAL
            )
            response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=segmentation)
        assert response.status_code == status, response.text
        if status == 422:
            assert "contiguous" in response.text

    async def test_post_segmentation_rejects_span_beyond_content(self, client, test_database, test_person_data):
        """Test that a segmentation span past the end of the content is rejected"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789", edition_type=EditionType.CRITICAL
        )

        response = await client.post(
            f"/v2/editions/{edition_id}/segmentation",
            json={"segments": [{"lines": [{"start": 0, "end": 99}]}]},
        )

        assert response.status_code == 422
        assert "error" in response.json()

    async def test_post_durchen_rejects_span_beyond_content(self, client, test_database, test_person_data):
        """Test that a durchen note span past the end of the content is rejected"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        response = await client.post(
            f"/v2/editions/{edition_id}/durchens",
            json=[
                {"span": {"start": 0, "end": 5}, "text": "Valid note"},
                {"span": {"start": 0, "end": 99}, "text": "Invalid note"},
            ],
        )

        assert response.status_code == 422
        list_response = await client.get(f"/v2/editions/{edition_id}/durchens")
        assert list_response.status_code == 200
        assert list_response.json() == []

    async def test_post_yigchung_rejects_span_beyond_content(self, client, test_database, test_person_data):
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "0123456789")

        response = await client.post(
            f"/v2/editions/{edition_id}/yigchungs",
            json=[
                {"span": {"start": 0, "end": 5}},
                {"span": {"start": 0, "end": 99}},
            ],
        )

        assert response.status_code == 422
        list_response = await client.get(f"/v2/editions/{edition_id}/yigchungs")
        assert list_response.status_code == 200
        assert list_response.json() == []

    async def test_post_segmentation_accepts_span_at_content_end(self, client, test_database, test_person_data):
        """Test that a span ending exactly at the content length is accepted"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789", edition_type=EditionType.CRITICAL
        )

        response = await client.post(
            f"/v2/editions/{edition_id}/segmentation",
            json={"segments": [{"lines": [{"start": 0, "end": 10}]}]},
        )

        assert response.status_code == 201
        stored = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert stored.status_code == 200
        assert [segment["lines"] for segment in stored.json()["items"]] == [[{"start": 0, "end": 10}]]


class TestAddAnnotationEditionNotFound(TestAnnotationsEndpoints):
    """Tests for annotation creation with non-existent edition"""

    async def test_add_bibliographic_edition_not_found(self, test_database):
        """Test that adding bibliographic metadata with non-existent edition raises DataNotFoundError"""
        from exceptions import DataNotFoundError

        async with test_database.get_session() as session:
            await session.run("MERGE (:BibliographyType {name: 'colophon'})")

        item = BibliographicMetadataInput(span=Span(start=0, end=10), type=BibliographyType.COLOPHON)

        with pytest.raises(DataNotFoundError) as exc_info:
            await test_database.annotation.bibliographic.add("nonexistent_edition_id", item)

        assert "Edition with ID 'nonexistent_edition_id' not found" in str(exc_info.value)

    async def test_add_segmentation_edition_not_found(self, test_database):
        """Test that adding segmentation with non-existent edition raises DataNotFoundError"""
        from exceptions import DataNotFoundError

        segmentation = SegmentationInput(
            segments=[SegmentInput(lines=[Span(start=0, end=10)])]
        )

        with pytest.raises(DataNotFoundError) as exc_info:
            await test_database.annotation.segmentation.add("nonexistent_edition_id", segmentation)

        assert "Edition with ID 'nonexistent_edition_id' not found" in str(exc_info.value)

    async def test_add_pagination_edition_not_found(self, test_database):
        """Test that adding pagination with non-existent edition raises DataNotFoundError"""
        from exceptions import DataNotFoundError

        pagination = PaginationInput(
            volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=10)])])]
        )

        with pytest.raises(DataNotFoundError) as exc_info:
            await test_database.annotation.pagination.add("nonexistent_edition_id", pagination)

        assert "Edition with ID 'nonexistent_edition_id' not found" in str(exc_info.value)

    async def test_add_note_edition_not_found(self, test_database):
        """Test that adding durchen notes with non-existent edition raises DataNotFoundError"""
        from exceptions import DataNotFoundError

        async with test_database.get_session() as session:
            await session.run("MERGE (:NoteType {name: 'durchen'})")

        note = NoteInput(span=Span(start=0, end=10), text="Test note")

        with pytest.raises(DataNotFoundError) as exc_info:
            await test_database.annotation.note.add_durchen("nonexistent_edition_id", note)

        assert "Edition with ID 'nonexistent_edition_id' not found" in str(exc_info.value)

    async def test_add_yigchung_edition_not_found(self, test_database):
        from exceptions import DataNotFoundError

        mark = MarkInput(span=Span(start=0, end=10))

        with pytest.raises(DataNotFoundError) as exc_info:
            await test_database.annotation.mark.add_yigchung("nonexistent_edition_id", mark)

        assert "Edition with ID 'nonexistent_edition_id' not found" in str(exc_info.value)


class TestDeleteEditionWithAnnotations(TestAnnotationsEndpoints):
    """Tests for edition deletion with all annotation types"""

    async def test_delete_edition_deletes_all_annotations(self, client, test_database, test_person_data):
        """Test that deleting a edition deletes all associated annotations"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        source_edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEFGHIJ", edition_type=EditionType.CRITICAL
        )
        target_edition_id = await self._create_test_edition(
            test_database, text_id, "KLMNOPQRSTUVWXYZ0123"
        )

        segmentation = SegmentationInput(
            segments=[SegmentInput(reference="source-1", lines=[Span(start=0, end=10)])]
        )
        await test_database.annotation.segmentation.add(source_edition_id, segmentation)

        pagination = PaginationInput(
            volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=10)])])]
        )
        pagination_id = await test_database.annotation.pagination.add(source_edition_id, pagination)

        bibliographic_item = BibliographicMetadataInput(span=Span(start=0, end=5), type=BibliographyType.COLOPHON)
        bibliographic_id = await test_database.annotation.bibliographic.add(source_edition_id, bibliographic_item)

        note_item = NoteInput(span=Span(start=5, end=10), text="Test note")
        note_id = await test_database.annotation.note.add_durchen(source_edition_id, note_item)

        mark_item = MarkInput(span=Span(start=10, end=15))
        mark_id = await test_database.annotation.mark.add_yigchung(source_edition_id, mark_item)

        await test_database.annotation.segmentation.add(
            target_edition_id,
            SegmentationInput(segments=[SegmentInput(reference="target-1", lines=[Span(start=0, end=10)])]),
        )
        source_segment = (
            await test_database.annotation.segmentation.get_segments_by_edition(source_edition_id, offset=0, limit=1)
        )[0]
        target_segment = (
            await test_database.annotation.segmentation.get_segments_by_edition(target_edition_id, offset=0, limit=1)
        )[0]
        alignment_response = await client.put(
            f"/v2/editions/{source_edition_id}/alignments/{target_edition_id}",
            json={"alignments": [{
                "source_segment_reference": source_segment.reference,
                "target_segment_reference": target_segment.reference,
            }]},
        )
        assert alignment_response.status_code == 204

        assert (await client.get(f"/v2/editions/{source_edition_id}/segmentation")).status_code == 200
        assert (await client.get(f"/v2/paginations/{pagination_id}")).status_code == 200
        assert (await client.get(f"/v2/bibliographic/{bibliographic_id}")).status_code == 200
        assert (await client.get(f"/v2/durchens/{note_id}")).status_code == 200
        assert (await client.get(f"/v2/yigchungs/{mark_id}")).status_code == 200
        alignment_get_response = await client.get(
            f"/v2/editions/{source_edition_id}/alignments/{target_edition_id}"
        )
        assert alignment_get_response.status_code == 200
        assert len(alignment_get_response.json()["items"]) == 1

        await test_database.edition.delete(source_edition_id)

        assert (await client.get(f"/v2/editions/{source_edition_id}/segmentation")).status_code == 404
        assert (await client.get(f"/v2/paginations/{pagination_id}")).status_code == 404
        assert (await client.get(f"/v2/bibliographic/{bibliographic_id}")).status_code == 404
        assert (await client.get(f"/v2/durchens/{note_id}")).status_code == 404
        assert (await client.get(f"/v2/yigchungs/{mark_id}")).status_code == 404
        alignment_get_response = await client.get(
            f"/v2/editions/{source_edition_id}/alignments/{target_edition_id}"
        )
        assert alignment_get_response.status_code == 404


class TestAnnotationEdgeCases(TestAnnotationsEndpoints):
    """Edge case tests for annotations endpoints"""

    async def test_special_characters_in_id(self, client, test_database):
        """Test handling of special characters in annotation IDs"""
        response = await client.get("/v2/editions/id-with-special%20chars/segmentation")

        assert response.status_code == 404

    async def test_very_long_id(self, client, test_database):
        """Test handling of very long annotation IDs"""
        long_id = "a" * 1000
        response = await client.get(f"/v2/editions/{long_id}/segmentation")

        assert response.status_code == 404


class TestAnnotationRoundTrip(TestAnnotationsEndpoints):
    """Round-trip tests: create via database, retrieve via API, delete via API"""

    async def test_segmentation_round_trip(self, client, test_database, test_person_data):
        """Test full lifecycle of a segmentation annotation"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(test_database, text_id, "Round trip test content")

        segmentation = SegmentationInput(
            segments=[
                SegmentInput(lines=[Span(start=0, end=10)]),
                SegmentInput(lines=[Span(start=10, end=23)]),
            ]
        )
        segmentation_id = await test_database.annotation.segmentation.add(edition_id, segmentation)

        get_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == segmentation_id

        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        data = segments_response.json()["items"]
        assert len(data) == 2

        delete_response = await client.delete(f"/v2/editions/{edition_id}/segmentation")
        assert delete_response.status_code == 204

        verify_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert verify_response.status_code == 404

    async def test_pagination_round_trip(self, client, test_database, test_person_data):
        """Test full lifecycle of a pagination annotation"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "Pagination test content", edition_type=EditionType.CRITICAL
        )

        pagination = PaginationInput(
            volumes=[
                Volume(
                    pages=[
                        Page(reference="1a", lines=[Span(start=0, end=11)]),
                        Page(reference="1b", lines=[Span(start=11, end=23)]),
                    ],
                )
            ]
        )
        pagination_id = await test_database.annotation.pagination.add(edition_id, pagination)

        get_response = await client.get(f"/v2/paginations/{pagination_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == pagination_id
        assert data["volumes"][0]["index"] == None
        assert len(data["volumes"][0]["pages"]) == 2

        delete_response = await client.delete(f"/v2/paginations/{pagination_id}")
        assert delete_response.status_code == 204

        verify_response = await client.get(f"/v2/paginations/{pagination_id}")
        assert verify_response.status_code == 404


class TestAddPagination(TestAnnotationsEndpoints):
    """Tests for POST /v2/editions/{edition_id}/pagination with multiple volumes"""

    async def test_add_pagination_multiple_volumes_success(self, client, test_database, test_person_data):
        """Test successful pagination creation with multiple volumes"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0" * 32, edition_type=EditionType.CRITICAL
        )

        # Volumes carve up one base text, so volume 2 continues where volume 1 ends.
        pagination_data = {
            "volumes": [
                {
                    "index": 1,
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                        {"reference": "1b", "lines": [{"start": 8, "end": 16}]},
                    ]
                },
                {
                    "index": 2,
                    "pages": [
                        {"reference": "2a", "lines": [{"start": 16, "end": 24}]},
                        {"reference": "2b", "lines": [{"start": 24, "end": 32}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 201
        pagination_id = response.json()["id"]

        # Test that GET returns all volumes
        get_response = await client.get(f"/v2/paginations/{pagination_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        
        assert "volumes" in data
        assert len(data["volumes"]) == 2
        
        # Verify first volume
        assert data["volumes"][0]["index"] == 1
        assert len(data["volumes"][0]["pages"]) == 2
        assert data["volumes"][0]["pages"][0]["reference"] == "1a"
        assert data["volumes"][0]["pages"][1]["reference"] == "1b"
        
        # Verify second volume
        assert data["volumes"][1]["index"] == 2
        assert len(data["volumes"][1]["pages"]) == 2
        assert data["volumes"][1]["pages"][0]["reference"] == "2a"
        assert data["volumes"][1]["pages"][1]["reference"] == "2b"

    async def test_add_pagination_overlapping_volumes_fails(self, client, test_database, test_person_data):
        """Test that pagination creation fails when two volumes cover overlapping offsets"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0" * 32, edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {
                    "index": 1,
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                        {"reference": "1b", "lines": [{"start": 8, "end": 16}]},
                    ]
                },
                {
                    "index": 2,
                    "pages": [
                        {"reference": "2a", "lines": [{"start": 12, "end": 20}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 422
        errors = response.json()["detail"]
        assert any("overlap" in str(error).lower() for error in errors)

    async def test_add_pagination_volumes_ordered_against_index_fails(
        self, client, test_database, test_person_data
    ):
        """Test that pagination creation fails when a later volume covers earlier offsets"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0" * 32, edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {
                    "index": 1,
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 16, "end": 24}]},
                    ]
                },
                {
                    "index": 2,
                    "pages": [
                        {"reference": "2a", "lines": [{"start": 0, "end": 8}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 422

    async def test_add_pagination_adjacent_volumes_succeed(self, client, test_database, test_person_data):
        """Test that a volume starting exactly where the previous one ends is accepted"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0" * 16, edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {"index": 1, "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 8}]}]},
                {"index": 2, "pages": [{"reference": "2a", "lines": [{"start": 8, "end": 16}]}]},
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 201
        stored = await test_database.annotation.pagination.get_all(edition_id)
        assert stored is not None
        assert [(volume.index, [(page.reference, [(line.start, line.end) for line in page.lines]) for page in volume.pages]) for volume in stored.volumes] == [(1, [("1a", [(0, 8)])]), (2, [("2a", [(8, 16)])])]

    @pytest.mark.parametrize("lines", [[], [{"start": 8, "end": 8}], [{"start": 8, "end": 8}] * 2])
    async def test_add_pagination_with_blank_page_fails(self, client, test_database, test_person_data, lines):
        """Pages must cover text, even when surrounded by valid pages."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0" * 16, edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                        {"reference": "1b", "lines": lines},
                        {"reference": "2a", "lines": [{"start": 8, "end": 16}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 422, response.json()
        assert await test_database.annotation.pagination.get_all(edition_id) is None

        response = await client.post(
            f"/v2/texts/{text_id}/editions",
            json={
                "content": "0" * 16,
                "metadata": {"type": "diplomatic", "bdrc": "Wempty"},
                "pagination": pagination_data,
            },
        )
        assert response.status_code == 422, response.json()

    async def test_add_pagination_multiple_volumes_without_index_fails(self, client, test_database, test_person_data):
        """Test that pagination creation fails when multiple volumes don't specify indexes"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                    ]
                },
                {
                    "pages": [
                        {"reference": "2a", "lines": [{"start": 0, "end": 10}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 422
        errors = response.json()["detail"]
        
        # Should have validation error about missing index or invalid sequence
        assert any("index" in str(error).lower() or "sequence" in str(error).lower() for error in errors)

    async def test_add_pagination_multiple_volumes_same_index_fails(self, client, test_database, test_person_data):
        """Test that pagination creation fails when multiple volumes have the same index"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {
                    "index": 1,
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                    ]
                },
                {
                    "index": 1,
                    "pages": [
                        {"reference": "2a", "lines": [{"start": 0, "end": 10}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 422
        errors = response.json()["detail"]
        
        # Should have validation error about duplicate indexes
        assert any("unique" in str(error).lower() or "duplicate" in str(error).lower() for error in errors)

    async def test_add_pagination_one_volume_without_index_among_many_fails(
        self, client, test_database, test_person_data
    ):
        """Test that a null index is rejected as soon as there is more than one volume"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {"index": 34, "pages": [{"reference": "F.1.a", "lines": [{"start": 0, "end": 8}]}]},
                {"index": None, "pages": [{"reference": "F.1.b", "lines": [{"start": 8, "end": 16}]}]},
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 422
        assert any("multiple volumes must have indexes" in str(error) for error in response.json()["detail"])

    async def test_add_pagination_non_continuous_indexes_succeeds(self, client, test_database, test_person_data):
        """Test that volume indexes may skip numbers, since they are volume numbers in the source collection"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {"index": 35, "pages": [{"reference": "F.1.a", "lines": [{"start": 8, "end": 16}]}]},
                {"index": 32, "pages": [{"reference": "F.146.a", "lines": [{"start": 0, "end": 8}]}]},
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 201, response.json()

        data = (await client.get(f"/v2/paginations/{response.json()['id']}")).json()
        assert [volume["index"] for volume in data["volumes"]] == [32, 35]

    async def test_add_pagination_single_volume_with_index_succeeds(self, client, test_database, test_person_data):
        """Test that a text found in one volume of a larger collection keeps that volume's number"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [{"index": 34, "pages": [{"reference": "F.144.b", "lines": [{"start": 0, "end": 8}]}]}]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 201, response.json()

        data = (await client.get(f"/v2/paginations/{response.json()['id']}")).json()
        assert data["volumes"][0]["index"] == 34

    async def test_add_pagination_single_volume_without_index_succeeds(self, client, test_database, test_person_data):
        """Test that single volume without index succeeds (defaults to 0)"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            test_database, text_id, "0123456789ABCDEF", edition_type=EditionType.CRITICAL
        )

        pagination_data = {
            "volumes": [
                {
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                    ]
                }
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/pagination", json=pagination_data)
        assert response.status_code == 201
        pagination_id = response.json()["id"]

        # Verify the volume was created with default index 0
        get_response = await client.get(f"/v2/paginations/{pagination_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        
        assert len(data["volumes"]) == 1
        assert data["volumes"][0]["index"] == None
