# pylint: disable=redefined-outer-name
"""
Integration tests for v2/tags endpoints and tag-related functionality.

Tests endpoints:
- GET /v2/tags/ (get all tags)
- POST /v2/tags/ (create tag)
- DELETE /v2/tags/<tag_id> (delete tag)
- POST /v2/texts/<text_id>/tags/<tag_id> (tag a text)
- DELETE /v2/texts/<text_id>/tags/<tag_id> (untag a text)
- POST /v2/segments/<segment_id>/tags/<tag_id> (tag a segment)
- DELETE /v2/segments/<segment_id>/tags/<tag_id> (untag a segment)
- Inline tagging via POST /v2/texts (create text with tag_ids)
- Tag filtering via GET /v2/texts?tag_id=...
"""

import logging

import pytest
from identifier import generate_id
from models.tag import TagInput

logger = logging.getLogger(__name__)

APPLICATION_HEADER = {"X-Application": "test_application"}


@pytest.fixture
async def test_tag_data():
    """Sample tag data for testing"""
    return {
        "title": {"en": "Philosophy", "bo": "གྲུབ་མཐའ"},
    }


@pytest.fixture
async def test_tag_data_minimal():
    """Minimal tag data for testing"""
    return {"title": {"en": "Meditation"}}


@pytest.fixture
async def test_tag_data_with_description():
    """Tag data with description"""
    return {
        "title": {"en": "Ethics", "bo": "ཚུལ་ཁྲིམས"},
        "description": {"en": "Texts related to ethical conduct", "bo": "ཚུལ་ཁྲིམས་ཀྱི་གཞུང་།"},
    }


async def _create_tag(client, tag_data):
    """Helper to create a tag and return its ID."""
    response = await client.post("/v2/tags/", json=tag_data, headers=APPLICATION_HEADER)
    assert response.status_code == 201
    return response.json()["id"]


async def _create_text(client, tag_ids=None):
    """Helper to create an text and return its ID."""
    text_data = {
        "title": {"bo": f"ཚོད་ལྟའི་གཞུང་། {generate_id()}"},
        "language": "bo",
        "category_id": "category",
        "license": "public",
        "contributions": [{"type": "person", "id": "test_person", "role": "author"}],
    }
    if tag_ids is not None:
        text_data["tag_ids"] = tag_ids
    response = await client.post("/v2/texts/", json=text_data)
    assert response.status_code == 201
    return response.json()["id"]


def _items(data):
    return data["items"] if isinstance(data, dict) and "items" in data else data


async def _seed_person(test_database):
    """Seed a test person for text creation."""
    async with test_database.get_session() as session:
        await session.run("""
            MERGE (p:Person {id: 'test_person'})
            MERGE (n:Nomen {id: 'test_person_nomen'})
            MERGE (p)-[:HAS_NAME]->(n)
            MERGE (lt:LocalizedText {text: 'Test Author'})
            WITH n, lt
            MATCH (lang:Language {code: 'en'})
            MERGE (n)-[:HAS_LOCALIZATION]->(lt)-[:HAS_LANGUAGE]->(lang)
        """)


@pytest.mark.asyncio(loop_scope="session")
class TestGetAllTags:
    """Tests for GET /v2/tags/ endpoint"""

    async def test_get_all_tags_empty(self, client, test_database):
        """Test getting tags when none exist returns empty list"""
        response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_all_tags_returns_created_tags(self, client, test_database):
        """Test getting tags returns previously created tags"""
        tag_id = await _create_tag(client, {"title": {"en": "Test Tag"}})

        response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        tag_ids = [t["id"] for t in data]
        assert tag_id in tag_ids

    async def test_get_all_tags_missing_application_header(self, client, test_database):
        """Test getting tags without X-Application header fails"""
        response = await client.get("/v2/tags/")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_get_all_tags_invalid_application(self, client, test_database):
        """Test getting tags with invalid application returns 404"""
        response = await client.get("/v2/tags/", headers={"X-Application": "nonexistent_app"})

        assert response.status_code == 404
        data = response.json()
        assert "error" in data


@pytest.mark.asyncio(loop_scope="session")
class TestCreateTag:
    """Tests for POST /v2/tags/ endpoint"""

    async def test_create_tag_success(self, client, test_database, test_tag_data):
        """Test successfully creating a tag and verifying it appears in GET"""
        response = await client.post("/v2/tags/", json=test_tag_data, headers=APPLICATION_HEADER)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["id"] is not None

        get_response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)
        tags = get_response.json()
        created_tag = next((t for t in tags if t["id"] == data["id"]), None)
        assert created_tag is not None
        assert created_tag["title"]["en"] == "Philosophy"
        assert created_tag["title"]["bo"] == "གྲུབ་མཐའ"

    async def test_create_tag_minimal(self, client, test_database, test_tag_data_minimal):
        """Test creating tag with minimal data and verifying title roundtrip"""
        response = await client.post("/v2/tags/", json=test_tag_data_minimal, headers=APPLICATION_HEADER)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        get_response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)
        tags = get_response.json()
        created_tag = next((t for t in tags if t["id"] == data["id"]), None)
        assert created_tag is not None
        assert created_tag["title"]["en"] == "Meditation"
        assert created_tag.get("description") is None

    async def test_create_tag_with_description(self, client, test_database, test_tag_data_with_description):
        """Test creating tag with description"""
        tag_id = await _create_tag(client, test_tag_data_with_description)

        response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)
        tags = response.json()
        created_tag = next((t for t in tags if t["id"] == tag_id), None)

        assert created_tag is not None
        assert created_tag["description"] is not None
        assert created_tag["description"]["en"] == "Texts related to ethical conduct"
        assert created_tag["description"]["bo"] == "ཚུལ་ཁྲིམས་ཀྱི་གཞུང་།"

    async def test_create_tag_duplicate_rejected(self, client, test_database):
        """Test creating tag with duplicate title in same application fails"""
        tag_data = {"title": {"en": "Unique Tag Title For Dup Test"}}

        response1 = await client.post("/v2/tags/", json=tag_data, headers=APPLICATION_HEADER)
        assert response1.status_code == 201

        response2 = await client.post("/v2/tags/", json=tag_data, headers=APPLICATION_HEADER)
        assert response2.status_code == 422
        data = response2.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()

    async def test_create_tag_missing_title(self, client, test_database):
        """Test creating tag without title fails"""
        response = await client.post("/v2/tags/", json={}, headers=APPLICATION_HEADER)

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_tag_empty_title(self, client, test_database):
        """Test creating tag with empty title fails"""
        response = await client.post("/v2/tags/", json={"title": {}}, headers=APPLICATION_HEADER)

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_tag_missing_application_header(self, client, test_database):
        """Test creating tag without X-Application header fails"""
        tag_data = {"title": {"en": "No App Header"}}

        response = await client.post("/v2/tags/", json=tag_data)

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data


@pytest.mark.asyncio(loop_scope="session")
class TestDeleteTag:
    """Tests for DELETE /v2/tags/<tag_id> endpoint"""

    async def test_delete_tag_success(self, client, test_database):
        """Test successfully deleting a tag"""
        tag_id = await _create_tag(client, {"title": {"en": "Tag To Delete"}})

        response = await client.delete(f"/v2/tags/{tag_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 204

        get_response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)
        tags = get_response.json()
        tag_ids = [t["id"] for t in tags]
        assert tag_id not in tag_ids

    async def test_delete_tag_nonexistent(self, client, test_database):
        """Test deleting non-existent tag returns 404"""
        response = await client.delete("/v2/tags/nonexistent_tag_id", headers=APPLICATION_HEADER)

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_delete_tag_missing_application_header(self, client, test_database):
        """Test deleting tag without X-Application header fails"""
        tag_id = await _create_tag(client, {"title": {"en": "Tag To Delete No Header"}})

        response = await client.delete(f"/v2/tags/{tag_id}")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data


@pytest.mark.asyncio(loop_scope="session")
class TestTagWork:
    """Tests for tagging/untagging works via text endpoints"""

    async def test_tag_work(self, client, test_database):
        """Test adding a tag to a work via text endpoint"""
        await _seed_person(test_database)
        tag_id = await _create_tag(client, {"title": {"en": "Work Tag"}})
        text_id = await _create_text(client)

        response = await client.post(f"/v2/texts/{text_id}/tags/{tag_id}")

        assert response.status_code == 204

        get_response = await client.get(f"/v2/texts/{text_id}")
        text = get_response.json()
        assert tag_id in text["tag_ids"]

    async def test_untag_work(self, client, test_database):
        """Test removing a tag from a work via text endpoint"""
        await _seed_person(test_database)
        tag_id = await _create_tag(client, {"title": {"en": "Work Untag"}})
        text_id = await _create_text(client)

        await client.post(f"/v2/texts/{text_id}/tags/{tag_id}")

        response = await client.delete(f"/v2/texts/{text_id}/tags/{tag_id}")

        assert response.status_code == 204

        get_response = await client.get(f"/v2/texts/{text_id}")
        text = get_response.json()
        assert tag_id not in text["tag_ids"]

    async def test_tag_work_multiple_tags(self, client, test_database):
        """Test adding multiple tags to a work"""
        await _seed_person(test_database)
        tag_id_1 = await _create_tag(client, {"title": {"en": "Multi Tag 1"}})
        tag_id_2 = await _create_tag(client, {"title": {"en": "Multi Tag 2"}})
        text_id = await _create_text(client)

        await client.post(f"/v2/texts/{text_id}/tags/{tag_id_1}")
        await client.post(f"/v2/texts/{text_id}/tags/{tag_id_2}")

        get_response = await client.get(f"/v2/texts/{text_id}")
        text = get_response.json()
        assert tag_id_1 in text["tag_ids"]
        assert tag_id_2 in text["tag_ids"]

    async def test_tag_nonexistent_text(self, client, test_database):
        """Test tagging a non-existent text returns 404"""
        tag_id = await _create_tag(client, {"title": {"en": "Orphan Tag"}})

        response = await client.post(f"/v2/texts/nonexistent_expr/tags/{tag_id}")

        assert response.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
class TestInlineTagging:
    """Tests for inline tag_ids on text create and update"""

    async def test_create_text_with_tag_ids(self, client, test_database):
        """Test creating an text with inline tag_ids"""
        await _seed_person(test_database)
        tag_id = await _create_tag(client, {"title": {"en": "Inline Create Tag"}})

        text_id = await _create_text(client, tag_ids=[tag_id])

        get_response = await client.get(f"/v2/texts/{text_id}")
        text = get_response.json()
        assert tag_id in text["tag_ids"]

    async def test_create_text_without_tag_ids(self, client, test_database):
        """Test creating an text without tag_ids yields empty list"""
        await _seed_person(test_database)

        text_id = await _create_text(client)

        get_response = await client.get(f"/v2/texts/{text_id}")
        text = get_response.json()
        assert text["tag_ids"] == []

    async def test_update_text_tag_ids(self, client, test_database):
        """Test updating text tag_ids via PATCH"""
        await _seed_person(test_database)
        tag_id_1 = await _create_tag(client, {"title": {"en": "Patch Tag 1"}})
        tag_id_2 = await _create_tag(client, {"title": {"en": "Patch Tag 2"}})

        text_id = await _create_text(client, tag_ids=[tag_id_1])

        patch_data = {"tag_ids": [tag_id_2]}
        response = await client.patch(f"/v2/texts/{text_id}", json=patch_data)

        assert response.status_code == 200
        data = response.json()
        assert tag_id_2 in data["tag_ids"]
        assert tag_id_1 not in data["tag_ids"]


@pytest.mark.asyncio(loop_scope="session")
class TestTagSegment:
    """Tests for tagging/untagging segments"""

    async def test_tag_segment(self, client, test_database):
        """Test adding a tag to a segment"""
        tag_id = await _create_tag(client, {"title": {"en": "Segment Tag"}})

        async with test_database.get_session() as session:
            await session.run("""
                CREATE (sgn:Segmentation {id: 'test_sgn_tag'})
                CREATE (seg:Segment {id: 'test_seg_tag'})
                CREATE (seg)-[:SEGMENT_OF]->(sgn)
            """)

        await test_database.tag.tag_segment("test_seg_tag", tag_id)

        async with test_database.get_session() as session:
            cursor = await session.run("""
                MATCH (s:Segment {id: 'test_seg_tag'})-[:HAS_TAG]->(t:Tag {id: $tag_id})
                RETURN t.id AS tag_id
            """, tag_id=tag_id)
            result = await cursor.single()
            assert result is not None
            assert result["tag_id"] == tag_id

    async def test_untag_segment(self, client, test_database):
        """Test removing a tag from a segment"""
        tag_id = await _create_tag(client, {"title": {"en": "Segment Untag"}})

        async with test_database.get_session() as session:
            await session.run("""
                CREATE (sgn:Segmentation {id: 'test_sgn_untag'})
                CREATE (seg:Segment {id: 'test_seg_untag'})
                CREATE (seg)-[:SEGMENT_OF]->(sgn)
            """)

        await test_database.tag.tag_segment("test_seg_untag", tag_id)
        await test_database.tag.untag_segment("test_seg_untag", tag_id)

        async with test_database.get_session() as session:
            cursor = await session.run("""
                MATCH (s:Segment {id: 'test_seg_untag'})-[:HAS_TAG]->(t:Tag {id: $tag_id})
                RETURN t.id AS tag_id
            """, tag_id=tag_id)
            result = await cursor.single()
            assert result is None

    async def test_tag_segment_via_api(self, client, test_database):
        """Test tagging a segment via API endpoint"""
        tag_id = await _create_tag(client, {"title": {"en": "API Segment Tag"}})

        async with test_database.get_session() as session:
            await session.run("""
                CREATE (sgn:Segmentation {id: 'test_sgn_api'})
                CREATE (seg:Segment {id: 'test_seg_api'})
                CREATE (seg)-[:SEGMENT_OF]->(sgn)
            """)

        response = await client.post(f"/v2/segments/test_seg_api/tags/{tag_id}")
        assert response.status_code == 204

        async with test_database.get_session() as session:
            cursor = await session.run("""
                MATCH (s:Segment {id: 'test_seg_api'})-[:HAS_TAG]->(t:Tag {id: $tag_id})
                RETURN t.id AS tag_id
            """, tag_id=tag_id)
            result = await cursor.single()
            assert result is not None
            assert result["tag_id"] == tag_id

    async def test_untag_segment_via_api(self, client, test_database):
        """Test untagging a segment via API endpoint"""
        tag_id = await _create_tag(client, {"title": {"en": "API Segment Untag"}})

        async with test_database.get_session() as session:
            await session.run("""
                CREATE (sgn:Segmentation {id: 'test_sgn_api_untag'})
                CREATE (seg:Segment {id: 'test_seg_api_untag'})
                CREATE (seg)-[:SEGMENT_OF]->(sgn)
            """)

        await client.post(f"/v2/segments/test_seg_api_untag/tags/{tag_id}")

        response = await client.delete(f"/v2/segments/test_seg_api_untag/tags/{tag_id}")
        assert response.status_code == 204

        async with test_database.get_session() as session:
            cursor = await session.run("""
                MATCH (s:Segment {id: 'test_seg_api_untag'})-[:HAS_TAG]->(t:Tag {id: $tag_id})
                RETURN t.id AS tag_id
            """, tag_id=tag_id)
            result = await cursor.single()
            assert result is None


@pytest.mark.asyncio(loop_scope="session")
class TestTagFiltering:
    """Tests for filtering texts by tag_id"""

    async def test_filter_texts_by_tag_id(self, client, test_database):
        """Test filtering texts by tag_id returns only tagged texts"""
        await _seed_person(test_database)
        tag_id = await _create_tag(client, {"title": {"en": "Filter Tag"}})

        text_id_tagged = await _create_text(client, tag_ids=[tag_id])
        text_id_untagged = await _create_text(client)

        response = await client.get("/v2/texts/", params={"tag_id": tag_id})

        assert response.status_code == 200
        data = response.json()
        result_ids = [expr["id"] for expr in _items(data)]
        assert text_id_tagged in result_ids
        assert text_id_untagged not in result_ids

    @pytest.mark.parametrize("match_mode", ["all", "any"])
    async def test_filter_texts_by_tag_match_mode(self, client, test_database, match_mode):
        await _seed_person(test_database)
        tradition_id = await _create_tag(client, {"title": {"en": f"{match_mode} Tradition"}})
        chant_id = await _create_tag(client, {"title": {"en": f"{match_mode} Chant"}})

        both_id = await _create_text(client, tag_ids=[tradition_id, chant_id])
        tradition_only_id = await _create_text(client, tag_ids=[tradition_id])
        chant_only_id = await _create_text(client, tag_ids=[chant_id])
        untagged_id = await _create_text(client)

        params = {"tag_id": f"{tradition_id},{chant_id}"}
        if match_mode != "all":
            params["tag_id_match"] = match_mode
        response = await client.get("/v2/texts/", params=params)

        assert response.status_code == 200
        result_ids = {expr["id"] for expr in _items(response.json())}
        expected_ids = {both_id} if match_mode == "all" else {both_id, tradition_only_id, chant_only_id}
        assert result_ids == expected_ids
        assert untagged_id not in result_ids

    async def test_filter_texts_by_nonexistent_tag_returns_empty(self, client, test_database):
        """Test filtering by non-existent tag_id returns empty list"""
        response = await client.get("/v2/texts/", params={"tag_id": "nonexistent_tag"})

        assert response.status_code == 200
        data = response.json()
        assert data["has_more"] is False
        assert data["offset"] == 0
        assert data["limit"] == 20
        assert data["items"] == []

    async def test_filter_texts_rejects_empty_tag_id(self, client):
        response = await client.get("/v2/texts/", params={"tag_id": "tag-a,,tag-b"})

        assert response.status_code == 422

    async def test_filter_texts_rejects_invalid_match_mode(self, client):
        response = await client.get("/v2/texts/", params={"tag_id": "tag-a,tag-b", "tag_id_match": "some"})

        assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
class TestTagLocalization:
    """Tests for tag title/description localization"""

    async def test_tag_multi_language_title(self, client, test_database):
        """Test that tag titles are properly localized"""
        tag_data = {
            "title": {
                "en": "English Tag Title",
                "bo": "བོད་ཡིག་ཁ་བྱང་།",
                "zh": "中文标签",
            }
        }

        tag_id = await _create_tag(client, tag_data)

        response = await client.get("/v2/tags/", headers=APPLICATION_HEADER)
        tags = response.json()
        created_tag = next((t for t in tags if t["id"] == tag_id), None)

        assert created_tag is not None
        assert created_tag["title"]["en"] == "English Tag Title"
        assert created_tag["title"]["bo"] == "བོད་ཡིག་ཁ་བྱང་།"
        assert created_tag["title"]["zh"] == "中文标签"



@pytest.mark.asyncio(loop_scope="session")
class TestTagSharing:
    """Tests for tag sharing between works"""

    async def test_same_tag_on_multiple_works(self, client, test_database):
        """Test that two works can share the same tag node"""
        await _seed_person(test_database)
        tag_id = await _create_tag(client, {"title": {"en": "Shared Tag"}})

        text_id_1 = await _create_text(client, tag_ids=[tag_id])
        text_id_2 = await _create_text(client, tag_ids=[tag_id])

        get_1 = await client.get(f"/v2/texts/{text_id_1}")
        get_2 = await client.get(f"/v2/texts/{text_id_2}")

        expr_1 = get_1.json()
        expr_2 = get_2.json()

        assert tag_id in expr_1["tag_ids"]
        assert tag_id in expr_2["tag_ids"]


async def _seed_app_b(test_database):
    """Seed a second application and its own category for isolation tests."""
    async with test_database.get_session() as session:
        await session.run("""
            MERGE (app:Application {id: 'app_b', name: 'Application B'})
            WITH app
            MERGE (cat:Category {id: 'category_b'})-[:BELONGS_TO]->(app)
            MERGE (nomen:Nomen {id: 'category_b_nomen'})
            MERGE (cat)-[:HAS_TITLE]->(nomen)
            MERGE (lt:LocalizedText {text: 'App B Category'})
            WITH nomen, lt
            MATCH (lang:Language {code: 'en'})
            MERGE (nomen)-[:HAS_LOCALIZATION]->(lt)-[:HAS_LANGUAGE]->(lang)
        """)


APP_B_HEADER = {"X-Application": "app_b"}


@pytest.mark.asyncio(loop_scope="session")
class TestTagApplicationIsolation:
    """Tests that tags are isolated per application."""

    async def test_listing_tags_only_returns_own_application(self, client, test_database):
        """Tags created for App A should not appear when listing tags for App B."""
        await _seed_app_b(test_database)

        tag_id_a = await _create_tag(client, {"title": {"en": "App A Only Tag"}})

        response_b = await client.get("/v2/tags/", headers=APP_B_HEADER)
        assert response_b.status_code == 200
        tags_b = response_b.json()
        tag_ids_b = [t["id"] for t in tags_b]
        assert tag_id_a not in tag_ids_b

    async def test_listing_tags_returns_own_tags(self, client, test_database):
        """Tags created for App B should appear when listing tags for App B, not App A."""
        await _seed_app_b(test_database)

        tag_b_data = {"title": {"en": "App B Exclusive Tag"}}
        response_create = await client.post("/v2/tags/", json=tag_b_data, headers=APP_B_HEADER)
        assert response_create.status_code == 201
        tag_id_b = response_create.json()["id"]

        response_b = await client.get("/v2/tags/", headers=APP_B_HEADER)
        tags_b = response_b.json()
        tag_ids_b = [t["id"] for t in tags_b]
        assert tag_id_b in tag_ids_b

        response_a = await client.get("/v2/tags/", headers=APPLICATION_HEADER)
        tags_a = response_a.json()
        tag_ids_a = [t["id"] for t in tags_a]
        assert tag_id_b not in tag_ids_a

    async def test_same_tag_title_allowed_across_applications(self, client, test_database):
        """Two applications can each have a tag with the same title."""
        await _seed_app_b(test_database)

        tag_data = {"title": {"en": "Cross App Same Title"}}
        tag_id_a = await _create_tag(client, tag_data)

        response_b = await client.post("/v2/tags/", json=tag_data, headers=APP_B_HEADER)
        assert response_b.status_code == 201
        tag_id_b = response_b.json()["id"]

        assert tag_id_a != tag_id_b

    async def test_text_tag_ids_filtered_by_application(self, client, test_database):
        """When getting an text, tag_ids should only include tags belonging to the caller's application."""
        await _seed_app_b(test_database)
        await _seed_person(test_database)

        tag_id_a = await _create_tag(client, {"title": {"en": "App A Expr Tag"}})

        tag_b_data = {"title": {"en": "App B Expr Tag"}}
        response_b_create = await client.post("/v2/tags/", json=tag_b_data, headers=APP_B_HEADER)
        tag_id_b = response_b_create.json()["id"]

        text_id = await _create_text(client, tag_ids=[tag_id_a])

        work_id = await test_database.text.get_work_id(text_id)
        await test_database.tag.tag_work(work_id, tag_id_b)

        response_a = await client.get(
            f"/v2/texts/{text_id}",
            headers=APPLICATION_HEADER,
        )
        expr_a = response_a.json()
        assert tag_id_a in expr_a["tag_ids"]
        assert tag_id_b not in expr_a["tag_ids"]

        response_b = await client.get(
            f"/v2/texts/{text_id}",
            headers=APP_B_HEADER,
        )
        expr_b = response_b.json()
        assert tag_id_b in expr_b["tag_ids"]
        assert tag_id_a not in expr_b["tag_ids"]

    async def test_delete_tag_does_not_affect_other_application(self, client, test_database):
        """Deleting a tag in App A should not affect App B's tags."""
        await _seed_app_b(test_database)

        tag_id_a = await _create_tag(client, {"title": {"en": "Delete Isolation Tag"}})

        tag_b_data = {"title": {"en": "App B Surviving Tag"}}
        response_b_create = await client.post("/v2/tags/", json=tag_b_data, headers=APP_B_HEADER)
        tag_id_b = response_b_create.json()["id"]

        await client.delete(f"/v2/tags/{tag_id_a}", headers=APPLICATION_HEADER)

        response_b = await client.get("/v2/tags/", headers=APP_B_HEADER)
        tags_b = response_b.json()
        tag_ids_b = [t["id"] for t in tags_b]
        assert tag_id_b in tag_ids_b
