# pylint: disable=redefined-outer-name
"""
Integration tests for v2/editions endpoints using real Neo4j test instance.

Tests endpoints:
- GET /v2/editions/{edition_id}/content
- GET /v2/editions/{edition_id}/content/url
- GET /v2/editions/{edition_id}
- POST /v2/texts/{text_id}/editions
- POST /v2/editions/{edition_id}/segmentation
- POST /v2/editions/{edition_id}/pagination
- POST /v2/editions/{edition_id}/bibliographic
- POST /v2/editions/{edition_id}/durchen
- GET /v2/editions/{edition_id}/segmentation
- GET /v2/editions/{edition_id}/pagination
- GET /v2/editions/{edition_id}/bibliographic
- GET /v2/editions/{edition_id}/durchen
- GET /v2/editions/{edition_id}/related

Requires environment variables:
- NEO4J_TEST_URI: Neo4j test instance URI
- NEO4J_TEST_PASSWORD: Password for test instance
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from database.content_state import read_state
from identifier import generate_id
from models.annotation import Page, PaginationInput, Span, Volume
from models.base import LocalizedString
from models.contribution import PersonContributionInput
from models.edition import EditionInput, EditionType
from models.enums import ContributorRole
from models.text import TextInput
from models.person import PersonInput
logger = logging.getLogger(__name__)


@pytest.fixture
async def test_person_data() -> PersonInput:
    """Sample person data for testing"""
    return PersonInput(
        name=LocalizedString({"en": "Test Author", "bo": "སློབ་དཔོན།"}),
        bdrc="P123456",
    )


@pytest.mark.asyncio(loop_scope="session")
class TestEditionsEndpoints:
    """Integration tests for v2/editions endpoints"""

    async def _create_test_person(self, db, person_data: PersonInput) -> str:
        """Helper to create a test person in the database"""
        return await db.person.create(person_data)

    async def _create_test_text(self, db, person_id, title: LocalizedString | None = None):
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

    async def _create_test_edition(self, client, text_id, content="Sample text content", edition_type=EditionType.DIPLOMATIC, bdrc=None):
        """Helper to create a edition via API (uses injected storage)"""
        edition_data = {
            "content": content,
            "metadata": {
                "type": edition_type.value,
                "bdrc": bdrc or f"W{generate_id()[:8]}",
                "source": "Test Source",
            },
        }
        if edition_type == EditionType.DIPLOMATIC:
            edition_data["pagination"] = {
                "volumes": [
                    {
                        "pages": [{"reference": "1a", "lines": [{"start": 0, "end": len(content)}]}]
                    }
                ]
            }
        elif edition_type == EditionType.CRITICAL:
            edition_data["segmentation"] = {
                "segments": [{"lines": [{"start": 0, "end": len(content)}]}]
            }
        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert response.status_code == 201, f"Failed to create edition: {response.json()}"
        return response.json()["id"]


@pytest.mark.asyncio(loop_scope="session")
class TestGetEditionContent(TestEditionsEndpoints):
    """Tests for GET /v2/editions/{edition_id}/content"""

    async def test_get_content_success(self, client, test_database, test_person_data):
        """Test successful content retrieval"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Sample Tibetan text content")

        response = await client.get(f"/v2/editions/{edition_id}/content")

        assert response.status_code == 200
        assert response.json() == "Sample Tibetan text content"

    async def test_get_content_not_found(self, client, test_database):
        """Test content retrieval with non-existent edition ID"""
        response = await client.get("/v2/editions/non-existent-id/content")

        assert response.status_code == 404
        assert "error" in response.json()

    async def test_get_content_with_span(self, client, test_database, test_person_data):
        """Test content retrieval with span parameters"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789ABCDEF")

        response = await client.get(f"/v2/editions/{edition_id}/content?span_start=5&span_end=10")

        assert response.status_code == 200
        assert response.json() == "56789"

    async def test_get_content_tibetan_text(self, client, test_database, test_person_data):
        """Test content retrieval with Tibetan text"""
        tibetan_content = "བོད་སྐད་ཀྱི་ཡིག་ཆ།"
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, tibetan_content)

        response = await client.get(f"/v2/editions/{edition_id}/content")

        assert response.status_code == 200
        assert response.json() == tibetan_content

    async def test_get_content_url(self, client, test_database, test_person_data):
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Direct download content")
        before = datetime.now(UTC)

        response = await client.get(f"/v2/editions/{edition_id}/content/url")

        assert response.status_code == 200
        data = response.json()
        async with test_database.get_session() as session:
            state = await session.execute_read(read_state, edition_id)
        assert data["url"] == f"https://mock-s3.example.com/{state.object_key}?signed=1&expires_in=3600"
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert before + timedelta(seconds=3600) <= expires_at <= datetime.now(UTC) + timedelta(seconds=3600)

    async def test_get_content_url_for_missing_edition(self, client):
        response = await client.get("/v2/editions/non-existent-id/content/url")

        assert response.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
class TestGetEditionMetadata(TestEditionsEndpoints):
    """Tests for GET /v2/editions/{edition_id}"""

    async def test_get_metadata_success(self, client, test_database, test_person_data):
        """Test successful metadata retrieval with all expected fields"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id)

        response = await client.get(f"/v2/editions/{edition_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == edition_id
        assert data["text_id"] == text_id
        assert data["type"] == "diplomatic"
        assert data["bdrc"] is not None
        assert data["source"] == "Test Source"

    async def test_get_metadata_not_found(self, client, test_database):
        """Test metadata retrieval with non-existent edition ID"""
        response = await client.get("/v2/editions/non-existent-id")

        assert response.status_code == 404
        assert "error" in response.json()

    async def test_get_metadata_with_all_fields(self, client, test_database, test_person_data):
        """Test metadata retrieval with all optional fields populated"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = EditionInput(
            type=EditionType.DIPLOMATIC,
            bdrc="W12345",
            wiki="Q123456",
            source="Test Source",
            colophon="Test colophon text",
            incipit_title=LocalizedString({"en": "Opening words", "bo": "དབུ་ཚིག"}),
            alt_incipit_titles=[LocalizedString({"en": "Alt incipit", "bo": "མཚན་བྱང་གཞན།"})],
        )
        edition_id = generate_id()
        pagination = PaginationInput(
            volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=1)])])]
        )
        await test_database.edition.create(edition_data, edition_id, text_id, content_length=1, pagination=pagination)

        response = await client.get(f"/v2/editions/{edition_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["bdrc"] == "W12345"
        assert data["wiki"] == "Q123456"
        assert data["colophon"] == "Test colophon text"
        assert data["incipit_title"]["en"] == "Opening words"


@pytest.mark.asyncio(loop_scope="session")
class TestCreateEdition(TestEditionsEndpoints):
    """Tests for POST /v2/texts/{text_id}/editions"""

    async def test_create_edition_success(self, client, test_database, test_person_data):
        """Test successful edition creation and verify via GET"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "This is the text content.",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W12345",
                "source": "Test Source",
            },
            "pagination": {
                "volumes": [
                    {
                        "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 25}]}]
                    }
                ]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)

        assert response.status_code == 201
        edition_id = response.json()["id"]
        assert edition_id is not None

        get_response = await client.get(f"/v2/editions/{edition_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["id"] == edition_id
        assert get_data["type"] == "diplomatic"
        assert get_data["bdrc"] == "W12345"
        assert get_data["source"] == "Test Source"
        assert get_data["text_id"] == text_id

    async def test_create_edition_with_pagination(self, client, test_database, test_person_data):
        """Test edition creation with pagination annotation"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "This is the text content for pagination test.",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W12345",
                "source": "Test Source",
            },
            "pagination": {
                "volumes": [{
                    "pages": [
                        {"reference": "1a", "lines": [{"start": 0, "end": 20}]},
                        {"reference": "1b", "lines": [{"start": 20, "end": 45}]},
                    ],
                }]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)

        assert response.status_code == 201
        edition_id = response.json()["id"]

        annotations_response = await client.get(f"/v2/editions/{edition_id}/pagination")
        assert annotations_response.status_code == 200

        # Verify the pagination data matches what was sent
        pagination_response = annotations_response.json()
        assert pagination_response["volumes"][0]["pages"] == [
            {"reference": "1a", "lines": [{"start": 0, "end": 20}]},
            {"reference": "1b", "lines": [{"start": 20, "end": 45}]},
        ]

    async def test_create_edition_missing_body(self, client, test_database, test_person_data):
        """Test edition creation with missing request body"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        response = await client.post(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_edition_empty_body(self, client, test_database, test_person_data):
        """Test edition creation with empty request body"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        response = await client.post(f"/v2/texts/{text_id}/editions", json={})

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_edition_invalid_text_id(self, client, test_database):
        """Test edition creation with non-existent text ID"""
        edition_data = {
            "content": "Test content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W12345",
                "source": "Test Source",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 12}]}]
                }]
            },
        }

        response = await client.post("/v2/texts/non-existent-id/editions", json=edition_data)

        assert response.status_code == 422
        assert "error" in response.json()

    async def test_create_edition_malformed_json(self, client, test_database, test_person_data):
        """Test edition creation with malformed JSON"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        response = await client.post(
            f"/v2/texts/{text_id}/editions",
            content="{invalid json}",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_edition_invalid_extra_field(self, client, test_database, test_person_data):
        """Test edition creation with invalid extra field (author)"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Test content",
            "author": {"type": "person", "id": "some-id"},
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W12345",
                "source": "Test Source",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 12}]}]
                }]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_critical_edition_success(self, client, test_database, test_person_data):
        """Test creating a critical edition"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Critical edition content",
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 24}]}]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)

        assert response.status_code == 201
        edition_id = response.json()["id"]

        metadata_response = await client.get(f"/v2/editions/{edition_id}")
        assert metadata_response.json()["type"] == "critical"

    async def test_create_second_critical_edition_fails(self, client, test_database, test_person_data):
        """Test that only one critical edition is allowed per text"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        first_edition_data = {
            "content": "First critical edition",
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 22}]}]
            },
        }

        first_response = await client.post(f"/v2/texts/{text_id}/editions", json=first_edition_data)
        assert first_response.status_code == 201

        second_edition_data = {
            "content": "Second critical edition",
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 23}]}]
            },
        }
        second_response = await client.post(f"/v2/texts/{text_id}/editions", json=second_edition_data)

        assert second_response.status_code == 422
        assert "error" in second_response.json()

    async def test_create_edition_round_trip(self, client, test_database, test_person_data):
        """Test creating an edition and retrieving it"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Round trip test content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W12345",
                "wiki": "Q123456",
                "source": "Test Source",
                "colophon": "Test colophon",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 23}]}]
                }]
            },
        }

        post_response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert post_response.status_code == 201
        edition_id = post_response.json()["id"]

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.status_code == 200
        assert content_response.json() == edition_data["content"]

        metadata_response = await client.get(f"/v2/editions/{edition_id}")
        assert metadata_response.status_code == 200
        metadata = metadata_response.json()
        assert metadata["bdrc"] == "W12345"
        assert metadata["wiki"] == "Q123456"
        assert metadata["colophon"] == "Test colophon"

    async def test_create_edition_preserves_surrounding_whitespace(self, client, test_database, test_person_data):
        """Content must be stored verbatim so annotation offsets stay valid"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        content = "\n  Namo tassa bhagavato arahato sammāsambuddhassa\n"
        edition_data = {
            "content": content,
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": len(content)}]}]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert response.status_code == 201, response.json()
        edition_id = response.json()["id"]

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.status_code == 200
        assert content_response.json() == content

    async def test_create_edition_rejects_span_beyond_content(self, client, test_database, test_person_data):
        """Spans that extend past the end of the content are rejected"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Short content",
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 99}]}]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert response.status_code == 422

    async def test_create_edition_rejects_utf8_byte_offsets(self, client, test_database, test_person_data):
        """Byte offsets overshoot code point offsets for Tibetan and are rejected"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        content = "བདེ་ལེགས"
        edition_data = {
            "content": content,
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": len(content.encode("utf-8"))}]}]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert response.status_code == 422

    async def test_create_edition_rejects_blank_content(self, client, test_database, test_person_data):
        """Whitespace-only content is rejected even though it is not stripped"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "   \n  ",
            "metadata": {
                "type": "critical",
                "source": "Test Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 6}]}]
            },
        }

        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
class TestEditionAnnotations(TestEditionsEndpoints):
    """Tests for POST/GET /v2/editions/{edition_id}/segmentation"""

    async def test_post_segmentation_annotation(self, client, test_database, test_person_data):
        """Test adding segmentation annotation and retrieving it"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789")

        annotation_data = {
            "segments": [
                {"lines": [{"start": 0, "end": 5}]},
                {"lines": [{"start": 5, "end": 10}]},
            ]
        }

        post_response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=annotation_data)
        assert post_response.status_code == 201
        segmentation_id = post_response.json()["id"]

        get_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == segmentation_id
        assert data["edition_id"] == edition_id
        assert data["text_id"] == text_id

        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        assert len(segments_response.json()["items"]) == 2

    async def test_get_segmentation_annotations_with_pagination(self, client, test_database, test_person_data):
        """Test edition segmentation segment rows support pagination."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789")

        annotation_data = {
            "segments": [
                {"lines": [{"start": 0, "end": 5}]},
                {"lines": [{"start": 5, "end": 10}]},
            ]
        }
        post_response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=annotation_data)
        assert post_response.status_code == 201

        first_page = await client.get(f"/v2/editions/{edition_id}/segmentation/segments?limit=1")
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert len(first_body["items"]) == 1
        assert first_body["has_more"] is True
        assert first_body["offset"] == 0
        assert first_body["limit"] == 1

        second_page = await client.get(f"/v2/editions/{edition_id}/segmentation/segments?limit=1&offset=1")
        assert second_page.status_code == 200
        second_body = second_page.json()
        assert len(second_body["items"]) == 1
        assert second_body["has_more"] is False
        assert second_body["offset"] == 1
        assert second_body["limit"] == 1

        all_items = (await client.get(f"/v2/editions/{edition_id}/segmentation/segments")).json()["items"]
        assert [item["id"] for item in first_body["items"] + second_body["items"]] == [item["id"] for item in all_items]
        assert len({item["id"] for item in all_items}) == 2
        assert [item["lines"] for item in all_items] == [[{"start": 0, "end": 5}], [{"start": 5, "end": 10}]]

    async def test_post_segmentation_annotation_with_multiple_lines(self, client, test_database, test_person_data):
        """Test one segment can contain multiple line spans through the API and Neo4j storage."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789")

        annotation_data = {
            "segments": [
                {"lines": [{"start": 0, "end": 5}, {"start": 5, "end": 10}]},
            ]
        }

        post_response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=annotation_data)
        assert post_response.status_code == 201
        segmentation_id = post_response.json()["id"]

        get_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert get_response.status_code == 200
        segment = get_response.json()["items"][0]
        assert segment["lines"] == [{"start": 0, "end": 5}, {"start": 5, "end": 10}]

        async with test_database.get_session() as session:
            result = await session.run(
                """
                MATCH (:Segmentation {id: $segmentation_id})<-[:SEGMENT_OF]-(segment:Segment)
                MATCH (span:Span)-[:SPAN_OF]->(segment)
                RETURN count(span) AS span_count
                """,
                segmentation_id=segmentation_id,
            )
            record = await result.single()
        assert record["span_count"] == 2

    async def test_post_pagination_annotation(self, client, test_database, test_person_data):
        """Test adding pagination annotation and retrieving it"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_data = EditionInput(
            type=EditionType.CRITICAL,
            source="Test Source",
        )
        edition_id = generate_id()
        await test_database.edition.create(edition_data, edition_id, text_id, content_length=16)

        annotation_data = {
            "volumes": [{
                "pages": [
                    {"reference": "1a", "lines": [{"start": 0, "end": 8}]},
                    {"reference": "1b", "lines": [{"start": 8, "end": 16}]},
                ]
            }]
        }

        post_response = await client.post(f"/v2/editions/{edition_id}/pagination", json=annotation_data)
        assert post_response.status_code == 201

        get_response = await client.get(f"/v2/editions/{edition_id}/pagination")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["edition_id"] == edition_id
        assert data["text_id"] == text_id
        assert "volumes" in data

    async def test_post_duplicate_pagination_rejected(self, client, test_database, test_person_data):
        """Test that adding a second pagination to a edition is rejected"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789ABCDEF")

        annotation_data = {
            "volumes": [{
                "pages": [
                    {"reference": "2a", "lines": [{"start": 0, "end": 8}]},
                    {"reference": "2b", "lines": [{"start": 8, "end": 16}]},
                ]
            }]
        }

        post_response = await client.post(f"/v2/editions/{edition_id}/pagination", json=annotation_data)
        assert post_response.status_code == 409


    async def test_post_annotation_missing_body(self, client, test_database, test_person_data):
        """Test posting annotation with missing body"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id)

        response = await client.post(f"/v2/editions/{edition_id}/segmentation")

        assert response.status_code == 422

    async def test_post_bibliographic_metadata_annotation(self, client, test_database, test_person_data):
        """Test adding bibliographic metadata annotation and retrieving it"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789ABCDEF")

        annotation_data = {
            "span": {"start": 0, "end": 8}, "type": "colophon",
        }

        post_response = await client.post(f"/v2/editions/{edition_id}/bibliographic", json=annotation_data)
        assert post_response.status_code == 201

        get_response = await client.get(f"/v2/editions/{edition_id}/bibliographic")
        assert get_response.status_code == 200
        data = get_response.json()
        assert len(data) == 1
        assert data[0]["edition_id"] == edition_id
        assert data[0]["text_id"] == text_id
        assert data[0]["type"] == "colophon"
        assert data[0]["span"]["start"] == 0
        assert data[0]["span"]["end"] == 8

    async def test_post_durchen_notes_annotation(self, client, test_database, test_person_data):
        """Test adding durchen notes annotation and retrieving it"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789ABCDEF")

        annotation_data = [
            {"span": {"start": 0, "end": 5}, "text": "First note"},
            {"span": {"start": 8, "end": 12}, "text": "Second note"},
        ]

        post_response = await client.post(f"/v2/editions/{edition_id}/durchens", json=annotation_data)
        assert post_response.status_code == 201
        note_ids = [item["id"] for item in post_response.json()]
        assert len(note_ids) == 2

        get_response = await client.get(f"/v2/editions/{edition_id}/durchens")
        assert get_response.status_code == 200
        data = get_response.json()
        assert len(data) == 2
        assert [item["id"] for item in data] == note_ids
        assert data[0]["edition_id"] == edition_id
        assert data[0]["text_id"] == text_id
        assert data[0]["text"] == "First note"
        assert data[0]["span"]["start"] == 0
        assert data[0]["span"]["end"] == 5
        assert data[1]["text"] == "Second note"
        assert data[1]["span"] == {"start": 8, "end": 12}

    async def test_post_durchen_notes_rejects_empty_array(self, client):
        response = await client.post("/v2/editions/edition-id/durchens", json=[])

        assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
class TestRelatedEditions(TestEditionsEndpoints):
    """Tests for GET /v2/editions/{edition_id}/related"""

    async def test_get_related_no_relations(self, client, test_database, test_person_data):
        """Test getting related editions when none exist"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id)

        response = await client.get(f"/v2/editions/{edition_id}/related")

        assert response.status_code == 200
        assert response.json() == []

    async def test_get_related_via_translation(self, client, test_database, test_person_data):
        """Test getting related editions via translation relationship"""
        person_id = await self._create_test_person(test_database, test_person_data)

        original_text_id = await self._create_test_text(
            test_database, person_id, title=LocalizedString({"bo": "བོད་སྐད།", "en": "Tibetan Text"})
        )
        original_edition_id = await self._create_test_edition(client, original_text_id, "Original content"
        )

        translation_data = TextInput(

            title=LocalizedString({"en": "English Translation"}),
            language="en",
            translation_of=original_text_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.TRANSLATOR)],
        )
        translation_text_id = await test_database.text.create(translation_data)
        translation_edition_id = await self._create_test_edition(client, translation_text_id, "Translated content"
        )

        response = await client.get(f"/v2/editions/{original_edition_id}/related")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == translation_edition_id

    async def test_get_related_via_commentary(self, client, test_database, test_person_data):
        """Test getting related editions via commentary relationship"""
        person_id = await self._create_test_person(test_database, test_person_data)

        root_text_id = await self._create_test_text(
            test_database, person_id, title=LocalizedString({"bo": "རྩ་བ།", "en": "Root Text"})
        )
        root_edition_id = await self._create_test_edition(client, root_text_id, "Root text content"
        )

        commentary_data = TextInput(
            category_id="category",
            title=LocalizedString({"bo": "འགྲེལ་པ།", "en": "Commentary"}),
            language="bo",
            commentary_of=root_text_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        commentary_text_id = await test_database.text.create(commentary_data)
        commentary_edition_id = await self._create_test_edition(client, commentary_text_id, "Commentary content"
        )

        response = await client.get(f"/v2/editions/{root_edition_id}/related")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == commentary_edition_id

    async def test_get_related_bidirectional(self, client, test_database, test_person_data):
        """Test that related editions work bidirectionally (from translation to original)"""
        person_id = await self._create_test_person(test_database, test_person_data)

        original_text_id = await self._create_test_text(
            test_database, person_id, title=LocalizedString({"bo": "བོད་སྐད།", "en": "Tibetan Text"})
        )
        original_edition_id = await self._create_test_edition(client, original_text_id, "Original content"
        )

        translation_data = TextInput(

            title=LocalizedString({"en": "English Translation"}),
            language="en",
            translation_of=original_text_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.TRANSLATOR)],
        )
        translation_text_id = await test_database.text.create(translation_data)
        translation_edition_id = await self._create_test_edition(client, translation_text_id, "Translated content"
        )

        response = await client.get(f"/v2/editions/{translation_edition_id}/related")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == original_edition_id

    async def test_get_related_multiple_relations(self, client, test_database, test_person_data):
        """Test getting related editions with multiple relations (translation + commentary)"""
        person_id = await self._create_test_person(test_database, test_person_data)

        original_text_id = await self._create_test_text(
            test_database, person_id, title=LocalizedString({"bo": "བོད་སྐད།", "en": "Tibetan Text"})
        )
        original_edition_id = await self._create_test_edition(client, original_text_id, "Original content"
        )

        translation_data = TextInput(

            title=LocalizedString({"en": "English Translation"}),
            language="en",
            translation_of=original_text_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.TRANSLATOR)],
        )
        translation_text_id = await test_database.text.create(translation_data)
        translation_edition_id = await self._create_test_edition(client, translation_text_id, "Translated content"
        )

        commentary_data = TextInput(
            category_id="category",
            title=LocalizedString({"bo": "འགྲེལ་པ།", "en": "Commentary"}),
            language="bo",
            commentary_of=original_text_id,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
        commentary_text_id = await test_database.text.create(commentary_data)
        commentary_edition_id = await self._create_test_edition(client, commentary_text_id, "Commentary content"
        )

        response = await client.get(f"/v2/editions/{original_edition_id}/related")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        related_ids = {item["id"] for item in data}
        assert translation_edition_id in related_ids
        assert commentary_edition_id in related_ids


@pytest.mark.asyncio(loop_scope="session")
class TestDeleteEdition(TestEditionsEndpoints):
    """Tests for DELETE /v2/editions/{edition_id}"""

    async def test_delete_edition_success(self, client, test_database, test_person_data):
        """Test successful edition deletion"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id)

        metadata_response = await client.get(f"/v2/editions/{edition_id}")
        assert metadata_response.status_code == 200

        response = await client.delete(f"/v2/editions/{edition_id}")

        assert response.status_code == 204

        get_response = await client.get(f"/v2/editions/{edition_id}")
        assert get_response.status_code == 404

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.status_code == 404

    async def test_delete_edition_not_found(self, client, test_database):
        """Test deleting a non-existent edition returns 404 with error message"""
        response = await client.delete("/v2/editions/non-existent-id")

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_delete_edition_with_annotations(self, client, test_database, test_person_data):
        """Test deleting an edition that has annotations"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789")

        segmentation_data = {
            "segments": [{"lines": [{"start": 0, "end": 10}]}]
        }
        post_response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=segmentation_data)
        assert post_response.status_code == 201

        annotations_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert annotations_response.status_code == 200
        annotations_data = annotations_response.json()
        segmentation_id = annotations_data["id"]

        segmentation_response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert segmentation_response.status_code == 200

        response = await client.delete(f"/v2/editions/{edition_id}")

        assert response.status_code == 204

        get_response = await client.get(f"/v2/editions/{edition_id}")
        assert get_response.status_code == 404

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.status_code == 404

        annotations_after = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert annotations_after.status_code == 404

        segmentation_after = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert segmentation_after.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
class TestPatchContent(TestEditionsEndpoints):
    """Integration tests for PATCH /v2/editions/{edition_id}/content endpoint."""

    async def _get_content_length(self, test_database, edition_id):
        """Read the internal content_length, which is deliberately absent from the API."""
        async with test_database.get_session() as session:
            result = await session.run(
                "MATCH (m:Edition {id: $edition_id}) RETURN m.content_length AS content_length",
                edition_id=edition_id,
            )
            record = await result.single()
            return record["content_length"]

    async def test_patch_content_insert_success(self, client, test_database, test_person_data):
        """Test successful insert operation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 6, "text": "Beautiful "}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello Beautiful World"

    async def test_patch_content_rejects_offset_beyond_content(self, client, test_database, test_person_data):
        """Test that an operation reaching past the end of the content is rejected."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 100, "text": "XY"}
        )

        assert response.status_code == 422

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello World"

    async def test_patch_content_updates_content_length(self, client, test_database, test_person_data):
        """Test that content_length tracks the text through patches that grow and shrink it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        grow = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 6, "text": "Beautiful "}
        )
        assert grow.status_code == 204
        assert await self._get_content_length(test_database, edition_id) == len("Hello Beautiful World")

        shrink = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 6, "end": 16}
        )
        assert shrink.status_code == 204
        assert await self._get_content_length(test_database, edition_id) == len("Hello World")

        # Spans are now validated against the shrunken length
        accepted = await client.post(
            f"/v2/editions/{edition_id}/durchens",
            json=[{"span": {"start": 0, "end": 11}, "text": "note"}],
        )
        assert accepted.status_code == 201

        rejected = await client.post(
            f"/v2/editions/{edition_id}/durchens",
            json=[{"span": {"start": 0, "end": 12}, "text": "note"}],
        )
        assert rejected.status_code == 422

    async def test_patch_content_preserves_whitespace_in_payload(self, client, test_database, test_person_data):
        """Patch text must be applied verbatim so offsets after it stay valid"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "AB")

        inserted = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 1, "text": "\n  x  \n"}
        )
        assert inserted.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "A\n  x  \nB"
        assert await self._get_content_length(test_database, edition_id) == len("A\n  x  \nB")

        # Whitespace-only payloads are legitimate edits, not empty input
        replaced = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 1, "end": 8, "text": " "}
        )
        assert replaced.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "A B"

        empty = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 0, "text": ""}
        )
        assert empty.status_code == 422

    async def test_patch_content_restores_length_when_storage_fails(
        self, client, test_database, test_person_data, mock_storage
    ):
        """Test that a failed storage write leaves content_length as it was."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        async def fail(*_args, **_kwargs):
            raise RuntimeError("storage unavailable")

        original = mock_storage.put_immutable
        mock_storage.put_immutable = fail
        try:
            # A deployed client receives 500: Starlette sends the error response and then re-raises
            # so test clients can inspect the cause. Matching it proves the router preserved state and
            # re-raised our failure rather than swallowing or replacing it.
            with pytest.raises(RuntimeError, match="storage unavailable"):
                await client.patch(
                    f"/v2/editions/{edition_id}/content",
                    json={"type": "insert", "position": 6, "text": "Beautiful "}
                )
        finally:
            mock_storage.put_immutable = original

        assert await self._get_content_length(test_database, edition_id) == len("Hello World")

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello World"

    async def test_patch_content_delete_success(self, client, test_database, test_person_data):
        """Test successful delete operation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello Beautiful World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 6, "end": 16}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello World"

    async def test_patch_content_delete_reanchors_each_span_of_multiline_segment(
        self, client, test_database, test_person_data
    ):
        """Deleting content must adjust each line span without collapsing its segment."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(
            client,
            text_id,
            "0123456789ABCDEFGHIJ",
            edition_type=EditionType.COLLATED,
        )
        segmentation_response = await client.post(
            f"/v2/editions/{edition_id}/segmentation",
            json={
                "segments": [
                    {"lines": [{"start": 0, "end": 5}, {"start": 5, "end": 10}]},
                    {"lines": [{"start": 10, "end": 20}]},
                ]
            },
        )
        assert segmentation_response.status_code == 201

        patch_response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 2, "end": 4},
        )
        assert patch_response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.status_code == 200
        assert content_response.json() == "01456789ABCDEFGHIJ"

        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        segments = segments_response.json()["items"]
        assert [segment["lines"] for segment in segments] == [
            [{"start": 0, "end": 3}, {"start": 3, "end": 8}],
            [{"start": 8, "end": 18}],
        ]

        patch_response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 0, "end": 3},
        )
        assert patch_response.status_code == 204

        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        updated_segments = segments_response.json()["items"]
        assert updated_segments[0]["id"] == segments[0]["id"]
        assert [segment["lines"] for segment in updated_segments] == [
            [{"start": 0, "end": 5}],
            [{"start": 5, "end": 15}],
        ]

    @pytest.mark.parametrize("annotation_kind", ["segmentation", "pagination"])
    @pytest.mark.parametrize(
        ("start", "end", "text", "expected_lines"),
        [
            pytest.param(1, 10, "XX", [[(0, 3)], [(3, 13)]], id="encompassed-line"),
            pytest.param(4, 10, "ZZZ", [[(0, 7)], [(7, 17)]], id="partially-overlapped-line"),
            pytest.param(5, 6, "Z", [[(0, 5), (5, 10)], [(10, 20)]], id="boundary-same-length"),
            pytest.param(5, 7, "Z", [[(0, 5), (5, 9)], [(9, 19)]], id="boundary-shrink"),
            pytest.param(0, 10, "XYZ", [[(0, 3)], [(3, 13)]], id="exact-entity"),
            pytest.param(0, 7, "ZZ", [[(0, 2), (2, 5)], [(5, 15)]], id="starts-at-entity-boundary"),
        ],
    )
    async def test_patch_content_replace_preserves_multiline_contiguity(
        self,
        client,
        test_database,
        test_person_data,
        annotation_kind,
        start,
        end,
        text,
        expected_lines,
    ):
        """Replacement must remap all line boundaries consistently within an entity."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        metadata = {"type": "collated", "source": "Test Source"}
        if annotation_kind == "segmentation":
            annotation = {
                "segments": [
                    {"reference": "S1", "lines": [{"start": 0, "end": 5}, {"start": 5, "end": 10}]},
                    {"reference": "S2", "lines": [{"start": 10, "end": 20}]},
                ]
            }
        else:
            metadata |= {"type": "diplomatic", "bdrc": f"W{generate_id()[:8]}"}
            annotation = {
                "volumes": [{
                    "pages": [
                        {"reference": "P1", "lines": [{"start": 0, "end": 5}, {"start": 5, "end": 10}]},
                        {"reference": "P2", "lines": [{"start": 10, "end": 20}]},
                    ]
                }]
            }

        create_response = await client.post(
            f"/v2/texts/{text_id}/editions",
            json={"content": "0123456789ABCDEFGHIJ", "metadata": metadata, annotation_kind: annotation},
        )
        assert create_response.status_code == 201
        edition_id = create_response.json()["id"]
        original_segment_id = None
        if annotation_kind == "segmentation":
            original_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
            assert original_response.status_code == 200
            original_segment_id = original_response.json()["items"][0]["id"]

        patch_response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": start, "end": end, "text": text},
        )
        assert patch_response.status_code == 204

        if annotation_kind == "segmentation":
            annotation_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
            entities = annotation_response.json().get("items", [])
        else:
            annotation_response = await client.get(f"/v2/editions/{edition_id}/pagination")
            entities = annotation_response.json().get("volumes", [{}])[0].get("pages", [])

        assert annotation_response.status_code == 200, annotation_response.json()
        assert [entity["reference"] for entity in entities] == (
            ["S1", "S2"] if annotation_kind == "segmentation" else ["P1", "P2"]
        )
        if original_segment_id is not None:
            assert entities[0]["id"] == original_segment_id
        assert [
            [(line["start"], line["end"]) for line in entity["lines"]]
            for entity in entities
        ] == expected_lines

    async def test_patch_content_replace_tracks_first_encompassed_segment_by_entity(
        self, client, test_database, test_person_data
    ):
        """An encompassed line must not consume the survivor slot for a later whole segment."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        create_response = await client.post(
            f"/v2/texts/{text_id}/editions",
            json={
                "content": "0123456789ABCDEF",
                "metadata": {"type": "collated", "source": "Test Source"},
                "segmentation": {
                    "segments": [
                        {"reference": "S1", "lines": [{"start": 0, "end": 4}, {"start": 4, "end": 8}]},
                        {"reference": "S2", "lines": [{"start": 8, "end": 10}]},
                        {"reference": "S3", "lines": [{"start": 10, "end": 12}]},
                        {"reference": "S4", "lines": [{"start": 12, "end": 16}]},
                    ]
                },
            },
        )
        assert create_response.status_code == 201
        edition_id = create_response.json()["id"]

        original_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert original_response.status_code == 200
        original_ids = {segment["reference"]: segment["id"] for segment in original_response.json()["items"]}

        patch_response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 2, "end": 14, "text": "XX"},
        )
        assert patch_response.status_code == 204

        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        segments = segments_response.json()["items"]
        assert [segment["reference"] for segment in segments] == ["S1", "S2", "S4"]
        assert {segment["reference"]: segment["id"] for segment in segments} == {
            reference: original_ids[reference] for reference in ("S1", "S2", "S4")
        }
        assert [segment["lines"] for segment in segments] == [
            [{"start": 0, "end": 2}],
            [{"start": 2, "end": 4}],
            [{"start": 4, "end": 6}],
        ]

    async def test_patch_content_replace_success(self, client, test_database, test_person_data):
        """Test successful replace operation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 6, "end": 11, "text": "Universe"}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello Universe"

    async def test_patch_content_not_found(self, client, test_database):
        """Test patch on non-existent edition returns 404."""
        response = await client.patch(
            "/v2/editions/non-existent-id/content",
            json={"type": "insert", "position": 0, "text": "test"}
        )

        assert response.status_code == 404

    async def test_patch_content_insert_missing_position(self, client, test_database, test_person_data):
        """Test insert without position fails validation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "text": "test"}
        )

        assert response.status_code == 422

    async def test_patch_content_insert_missing_text(self, client, test_database, test_person_data):
        """Test insert without text fails validation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 0}
        )

        assert response.status_code == 422

    async def test_patch_content_delete_missing_start(self, client, test_database, test_person_data):
        """Test delete without start fails validation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "end": 5}
        )

        assert response.status_code == 422

    async def test_patch_content_delete_start_gte_end(self, client, test_database, test_person_data):
        """Test delete with start >= end fails validation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 10, "end": 5}
        )

        assert response.status_code == 422

    async def test_patch_content_replace_missing_text(self, client, test_database, test_person_data):
        """Test replace without text fails validation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 0, "end": 5}
        )

        assert response.status_code == 422

    async def test_patch_content_invalid_type(self, client, test_database, test_person_data):
        """Test invalid operation type fails validation."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "invalid", "position": 0, "text": "test"}
        )

        assert response.status_code == 422

    async def test_patch_content_malformed_json(self, client, test_database, test_person_data):
        """Test patch with malformed JSON body returns 422."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            content="{bad json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422

    async def test_patch_content_empty_body(self, client, test_database, test_person_data):
        """Test patch with empty body fails."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(f"/v2/editions/{edition_id}/content", json={})

        assert response.status_code == 422

    async def test_patch_content_insert_at_position_zero(self, client, test_database, test_person_data):
        """Test insert at position 0 (beginning of text)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 0, "text": "Hello "}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello World"

    async def test_patch_content_insert_at_end(self, client, test_database, test_person_data):
        """Test insert at end of text."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 5, "text": " World"}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello World"

    async def test_patch_content_delete_entire_content(self, client, test_database, test_person_data):
        """Test deleting entire content."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 0, "end": 5}
        )

        assert response.status_code == 422

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello"

    async def test_patch_content_replace_with_longer_text(self, client, test_database, test_person_data):
        """Test replace with longer text."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hi World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 0, "end": 2, "text": "Hello"}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hello World"

    async def test_patch_content_replace_with_shorter_text(self, client, test_database, test_person_data):
        """Test replace with shorter text."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 0, "end": 5, "text": "Hi"}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "Hi World"

    async def test_patch_content_tibetan_text(self, client, test_database, test_person_data):
        """Test operations with Tibetan text."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "བོད་སྐད།")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 0, "text": "ཀ་"}
        )

        assert response.status_code == 204

        content_response = await client.get(f"/v2/editions/{edition_id}/content")
        assert content_response.json() == "ཀ་བོད་སྐད།"

    async def test_patch_content_insert_extra_fields_rejected(self, client, test_database, test_person_data):
        """Test insert with extra fields (start/end) is rejected."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 0, "text": "test", "start": 0, "end": 5}
        )

        assert response.status_code == 422

    async def test_patch_content_delete_extra_fields_rejected(self, client, test_database, test_person_data):
        """Test delete with extra fields (position/text) is rejected."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "Hello World")

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 0, "end": 5, "text": "Hello"}
        )

        assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
class TestPatchContentWithSegmentation(TestEditionsEndpoints):
    """Integration tests for PATCH /content with segmentation span adjustments."""

    async def _create_edition_with_segmentation(self, client, test_database, person_id, content, segments):
        """Helper to create edition with segmentation."""
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, content)

        segmentation_data = {
            "segments": [{"lines": [{"start": s[0], "end": s[1]}]} for s in segments]
        }
        post_response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=segmentation_data)
        assert post_response.status_code == 201

        return edition_id, text_id

    async def _get_segmentation_spans(self, client, edition_id):
        """Helper to get segmentation spans."""
        response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert response.status_code == 200
        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        data = segments_response.json()["items"]
        return [(s["lines"][0]["start"], s["lines"][0]["end"]) for s in data]

    async def test_insert_shifts_segments_after(self, client, test_database, test_person_data):
        """Insert should shift segments that come after the insert position."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 2, "text": "XX"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 7) in spans
        assert (7, 12) in spans

    async def test_insert_at_boundary_expands_previous_segment(self, client, test_database, test_person_data):
        """Insert at segment boundary should expand the previous segment (auto-grow)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 5, "text": "XX"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 7) in spans
        assert (7, 12) in spans

    async def test_insert_at_position_zero_expands_first_segment(self, client, test_database, test_person_data):
        """Insert at position 0 should expand first segment (special case)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 0, "text": "XX"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 7) in spans
        assert (7, 12) in spans

    async def test_delete_shifts_segments_after(self, client, test_database, test_person_data):
        """Delete should shift segments that come after the deleted range."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 1, "end": 3}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 3) in spans
        assert (3, 8) in spans

    async def test_delete_entire_segment_removes_it(self, client, test_database, test_person_data):
        """Delete that exactly matches a segment should remove it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 0, "end": 5}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert len(spans) == 1
        assert (0, 5) in spans

    async def test_replace_exact_match_preserves_segment(self, client, test_database, test_person_data):
        """Replace that exactly matches a segment should preserve it with new size."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 0, "end": 5, "text": "ABCDEFGH"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 8) in spans
        assert (8, 13) in spans

    async def test_replace_encompassing_multiple_segments_keeps_first_deletes_rest(
        self, client, test_database, test_person_data
    ):
        """Replace encompassing multiple segments should keep first and delete subsequent."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            segments=[(0, 4), (4, 8), (8, 12), (12, 16)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 2, "end": 14, "text": "XX"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 2) in spans
        assert (2, 4) in spans
        assert (4, 6) in spans
        assert len(spans) == 3

    async def test_delete_after_segment_leaves_unchanged(self, client, test_database, test_person_data):
        """Delete operation after a segment should leave it unchanged (covers line 36)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            segments=[(0, 5), (5, 16)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 6, "end": 9}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 5) in spans
        assert (5, 13) in spans

    async def test_replace_after_segment_leaves_unchanged(self, client, test_database, test_person_data):
        """Replace operation after a segment should leave it unchanged (covers line 61)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            segments=[(0, 5), (5, 16)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 6, "end": 9, "text": "XXXX"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 5) in spans
        assert (5, 17) in spans

    async def test_replace_inside_segment_expands_it(self, client, test_database, test_person_data):
        """Replace inside a segment should expand/shrink it (covers line 71)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_segmentation(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            segments=[(0, 16)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 5, "end": 10, "text": "XX"}
        )
        assert response.status_code == 204

        spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 13) in spans


@pytest.mark.asyncio(loop_scope="session")
class TestPatchContentWithAnnotations(TestEditionsEndpoints):
    """Integration tests for PATCH /content with non-continuous annotations (notes)."""

    async def _setup_note_type(self, test_database):
        """Ensure NoteType node exists for durchen."""
        async with test_database.get_session() as session:
            await session.run("MERGE (:NoteType {name: 'durchen'})")

    async def _create_edition_with_notes(self, client, test_database, person_id, content, notes):
        """Helper to create edition with durchen notes.

        Args:
            notes: List of (start, end, text) tuples
        """
        from models.annotation import NoteInput, Span

        await self._setup_note_type(test_database)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, content)

        note_inputs = [NoteInput(span=Span(start=n[0], end=n[1]), text=n[2]) for n in notes]
        note_ids = []
        for note_input in note_inputs:
            note_id = await test_database.annotation.note.add_durchen(edition_id, note_input)
            note_ids.append(note_id)

        return edition_id, text_id, note_ids

    async def _get_note_span(self, client, note_id):
        """Helper to get a note's span."""
        response = await client.get(f"/v2/durchens/{note_id}")
        if response.status_code == 404:
            return None
        assert response.status_code == 200
        data = response.json()
        return (data["span"]["start"], data["span"]["end"])

    async def test_insert_before_note_shifts_it(self, client, test_database, test_person_data):
        """Insert before a note should shift the note."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(5, 8, "Note on 567")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 2, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (7, 10)

    async def test_insert_inside_note_expands_it(self, client, test_database, test_person_data):
        """Insert inside a note should expand it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(3, 7, "Note on 3456")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 5, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (3, 9)

    async def test_insert_at_note_start_shifts_it(self, client, test_database, test_person_data):
        """Insert at note start boundary should shift it (not expand)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(5, 8, "Note on 567")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 5, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (7, 10)

    async def test_insert_at_note_end_unchanged(self, client, test_database, test_person_data):
        """Insert at note end boundary should leave it unchanged."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(3, 6, "Note on 345")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 6, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (3, 6)

    async def test_insert_after_note_unchanged(self, client, test_database, test_person_data):
        """Insert after a note should leave it unchanged."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(2, 5, "Note on 234")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 8, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (2, 5)

    async def test_delete_exact_match_deletes_note(self, client, test_database, test_person_data):
        """Delete that exactly matches a note should delete it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(3, 7, "Note on 3456")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 3, "end": 7}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span is None

    async def test_delete_encompassing_note_deletes_it(self, client, test_database, test_person_data):
        """Delete that encompasses a note should delete it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(4, 6, "Note on 45")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 2, "end": 8}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span is None

    async def test_delete_partial_overlap_trims_note(self, client, test_database, test_person_data):
        """Delete overlapping note start should trim it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(4, 8, "Note on 4567")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 2, "end": 6}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (2, 4)

    async def test_replace_exact_match_deletes_note(self, client, test_database, test_person_data):
        """Replace that exactly matches a note should delete it."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789",
            notes=[(3, 7, "Note on 3456")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 3, "end": 7, "text": "XXXX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span is None

    async def test_replace_partial_overlap_trims_note(self, client, test_database, test_person_data):
        """Replace overlapping note start should trim the note (not delete it)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            notes=[(5, 12, "Note spanning 567890AB")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 2, "end": 8, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (4, 8)

    async def test_replace_after_note_leaves_unchanged(self, client, test_database, test_person_data):
        """Replace operation after a note should leave it unchanged (covers line 90)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            notes=[(0, 5, "Note at start")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 10, "end": 14, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (0, 5)

    async def test_replace_before_note_shifts_it(self, client, test_database, test_person_data):
        """Replace operation before a note should shift it (covers line 92)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            notes=[(10, 14, "Note at end")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 2, "end": 5, "text": "XXXXXXXX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (15, 19)

    async def test_replace_inside_note_expands_it(self, client, test_database, test_person_data):
        """Replace inside a note should expand/shrink it (covers line 96)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            notes=[(0, 16, "Note spanning all")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 5, "end": 10, "text": "XX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (0, 13)

    async def test_replace_overlaps_note_end_trims_it(self, client, test_database, test_person_data):
        """Replace overlapping note end should trim it (covers lines 99-100)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            notes=[(2, 12, "Note in middle")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 8, "end": 12, "text": "XXXXXX"}
        )
        assert response.status_code == 204

        span = await self._get_note_span(client, note_ids[0])
        assert span == (2, 14)

    async def test_multiple_overlapping_notes(self, client, test_database, test_person_data):
        """Multiple overlapping notes should all be adjusted correctly."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_notes(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            notes=[
                (2, 6, "Note 1"),
                (4, 10, "Note 2"),
                (8, 14, "Note 3")
            ]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 5, "end": 9}
        )
        assert response.status_code == 204

        span1 = await self._get_note_span(client, note_ids[0])
        span2 = await self._get_note_span(client, note_ids[1])
        span3 = await self._get_note_span(client, note_ids[2])

        # Note 1 (2,6): delete [5,9) overlaps end → trims to (2, 5)
        assert span1 == (2, 5)
        # Note 2 (4,10): delete [5,9) is inside → shrinks by del_len → (4, 6)
        assert span2 == (4, 6)
        # Note 3 (8,14): delete [5,9) overlaps start → shifts to (5, 10)
        assert span3 == (5, 10)

    async def test_insert_before_yigchung_shifts_it(self, client, test_database, test_person_data):
        """Yigchung marks use the same non-continuous span adjustment as notes."""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, "0123456789")

        create_response = await client.post(
            f"/v2/editions/{edition_id}/yigchungs",
            json=[{"span": {"start": 5, "end": 8}}],
        )
        assert create_response.status_code == 201
        mark_id = create_response.json()[0]["id"]

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 2, "text": "XX"},
        )
        assert response.status_code == 204

        get_response = await client.get(f"/v2/yigchungs/{mark_id}")
        assert get_response.status_code == 200
        assert get_response.json()["span"] == {"start": 7, "end": 10}


@pytest.mark.asyncio(loop_scope="session")
class TestPatchContentWithSegmentationAndAnnotations(TestEditionsEndpoints):
    """Integration tests for PATCH /content affecting both segmentations and annotations."""

    async def _setup_note_type(self, test_database):
        """Ensure NoteType node exists for durchen."""
        async with test_database.get_session() as session:
            await session.run("MERGE (:NoteType {name: 'durchen'})")

    async def _create_edition_with_both(
        self, client, test_database, person_id, content, segments, notes
    ):
        """Helper to create edition with segmentation and notes."""
        from models.annotation import NoteInput, Span

        await self._setup_note_type(test_database)
        text_id = await self._create_test_text(test_database, person_id)
        edition_id = await self._create_test_edition(client, text_id, content)

        segmentation_data = {
            "segments": [{"lines": [{"start": s[0], "end": s[1]}]} for s in segments]
        }
        post_response = await client.post(f"/v2/editions/{edition_id}/segmentation", json=segmentation_data)
        assert post_response.status_code == 201

        note_inputs = [NoteInput(span=Span(start=n[0], end=n[1]), text=n[2]) for n in notes]
        note_ids = []
        for note_input in note_inputs:
            note_id = await test_database.annotation.note.add_durchen(edition_id, note_input)
            note_ids.append(note_id)

        return edition_id, text_id, note_ids

    async def _get_segmentation_spans(self, client, edition_id):
        """Helper to get segmentation spans."""
        response = await client.get(f"/v2/editions/{edition_id}/segmentation")
        assert response.status_code == 200
        segments_response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
        assert segments_response.status_code == 200
        data = segments_response.json()["items"]
        return [(s["lines"][0]["start"], s["lines"][0]["end"]) for s in data]

    async def _get_note_span(self, client, note_id):
        """Helper to get a note's span."""
        response = await client.get(f"/v2/durchens/{note_id}")
        if response.status_code == 404:
            return None
        assert response.status_code == 200
        data = response.json()
        return (data["span"]["start"], data["span"]["end"])

    async def test_insert_affects_both_segmentation_and_notes(self, client, test_database, test_person_data):
        """Insert should adjust both segmentation spans and note spans."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_both(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)],
            notes=[(2, 4, "Note on 23"), (7, 9, "Note on 78")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 3, "text": "XX"}
        )
        assert response.status_code == 204

        seg_spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 7) in seg_spans
        assert (7, 12) in seg_spans

        note1_span = await self._get_note_span(client, note_ids[0])
        note2_span = await self._get_note_span(client, note_ids[1])
        assert note1_span == (2, 6)
        assert note2_span == (9, 11)

    async def test_delete_affects_both_differently(self, client, test_database, test_person_data):
        """Delete should handle segmentation (continuous) and notes (non-continuous) differently."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_both(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)],
            notes=[(3, 7, "Note spanning segments")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 3, "end": 7}
        )
        assert response.status_code == 204

        seg_spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 3) in seg_spans
        assert (3, 6) in seg_spans

        note_span = await self._get_note_span(client, note_ids[0])
        assert note_span is None

    async def test_replace_preserves_segment_but_deletes_note_on_exact_match(
        self, client, test_database, test_person_data
    ):
        """Replace exact match: preserves segment (continuous) but deletes note (non-continuous)."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _, note_ids = await self._create_edition_with_both(
            client, test_database, person_id,
            content="0123456789",
            segments=[(0, 5), (5, 10)],
            notes=[(0, 5, "Note matching first segment")]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "replace", "start": 0, "end": 5, "text": "ABCDEFGH"}
        )
        assert response.status_code == 204

        seg_spans = await self._get_segmentation_spans(client, edition_id)
        assert (0, 8) in seg_spans
        assert (8, 13) in seg_spans

        note_span = await self._get_note_span(client, note_ids[0])
        assert note_span is None


@pytest.mark.asyncio(loop_scope="session")
class TestPatchContentWithPagination(TestEditionsEndpoints):
    """Integration tests for PATCH /content with pagination annotations."""

    async def _create_edition_with_pagination(self, client, test_database, person_id, content, pages):
        """Helper to create edition with custom pagination.

        Args:
            pages: List of (start, end, reference) tuples
        """
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": content,
            "metadata": {
                "type": "diplomatic",
                "bdrc": f"W{generate_id()[:8]}",
                "source": "Test Source",
            },
            "pagination": {
                "volumes": [{
                    "pages": [
                        {"reference": str(p[2]), "lines": [{"start": p[0], "end": p[1]}]}
                        for p in pages
                    ]
                }]
            },
        }
        response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert response.status_code == 201, f"Failed to create edition: {response.json()}"
        edition_id = response.json()["id"]

        return edition_id, text_id

    async def _get_pagination_spans(self, client, edition_id):
        """Helper to get pagination spans."""
        response = await client.get(f"/v2/editions/{edition_id}/pagination")
        assert response.status_code == 200
        data = response.json()
        if not data:
            return []
        volumes = data.get("volumes", [])
        if not volumes:
            return []
        # Get pages from the first volume (or combine all volumes if needed)
        pages = volumes[0].get("pages", [])
        return [(p["lines"][0]["start"], p["lines"][0]["end"]) for p in pages]

    async def test_insert_shifts_pagination(self, client, test_database, test_person_data):
        """Insert should shift pagination spans."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_pagination(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            pages=[(0, 8, 1), (8, 16, 2)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 4, "text": "XX"}
        )
        assert response.status_code == 204

        spans = await self._get_pagination_spans(client, edition_id)
        assert (0, 10) in spans
        assert (10, 18) in spans

    async def test_delete_shrinks_pagination(self, client, test_database, test_person_data):
        """Delete should shrink pagination spans."""
        person_id = await self._create_test_person(test_database, test_person_data)
        edition_id, _ = await self._create_edition_with_pagination(
            client, test_database, person_id,
            content="0123456789ABCDEF",
            pages=[(0, 8, 1), (8, 16, 2)]
        )

        response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "delete", "start": 2, "end": 6}
        )
        assert response.status_code == 204

        spans = await self._get_pagination_spans(client, edition_id)
        assert (0, 4) in spans
        assert (4, 12) in spans
