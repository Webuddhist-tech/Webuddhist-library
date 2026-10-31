# pylint: disable=redefined-outer-name
"""Integration tests for v2/texts endpoints using real Neo4j test instance.

Tests endpoints:
- GET /v2/texts/ (get all texts with filtering and pagination)
- GET /v2/texts/{text_id} (get single text)
- POST /v2/texts/ (create text)
- GET /v2/texts/{text_id}/editions/ (get editions of a text)

Requires environment variables:
- NEO4J_TEST_URI: Neo4j test instance URI
- NEO4J_TEST_PASSWORD: Password for test instance
"""


import pytest
from models.category import CategoryInput
from models.text import TextInput
from models.person import PersonInput


def _items(data):
    return data["items"] if isinstance(data, dict) and "items" in data else data


@pytest.fixture
async def test_person_data():
    """Sample person data for testing"""
    return {
        "name": {"en": "Test Author", "bo": "སློབ་དཔོན།"},
        "alt_names": [{"en": "Alternative Name", "bo": "མིང་གཞན།"}],
        "bdrc": "P123456",
        "wiki": "Q123456",
    }

@pytest.fixture
async def test_text_data():
    """Sample text data for testing"""
    return {
        "title": {"en": "Test text", "bo": "བརྟག་དཔྱད་ཚིག་སྒྲུབ།"},
        "alt_titles": [{"en": "Alternative Title", "bo": "མཚན་བྱང་གཞན།"}],
        "language": "en",
        "contributions": [],  # Will be populated with actual person IDs
        "date": "2024-01-01",
        "bdrc": "W123456",
        "wiki": "Q789012",
    }


@pytest.mark.asyncio(loop_scope="session")
class TestGetAllTextsV2:
    """Tests for GET /v2/texts/ endpoint (get all texts)"""

    async def test_get_all_metadata_empty_database(self, client, test_database):
        """Test getting all texts from empty database"""
        response = await client.get("/v2/texts/")

        assert response.status_code == 200
        body = response.json()
        data = _items(body)
        assert body["offset"] == 0
        assert body["limit"] == 20
        assert body["has_more"] is False
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_all_metadata_default_pagination(self, client, test_database, test_person_data):
        """Test default pagination (limit=20, offset=0)"""
        # Create test person first
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        # Use pre-created category from conftest
        category_id = 'category'

        # Create test texts
        text_ids = []
        for i in range(25):
            expr_data = {
                "title": {"en": f"Test text {i+1}", "bo": f"བརྟག་དཔྱད་ཚིག་སྒྲུབ་{i+1}།"},
                "language": "en",
                "category_id": category_id,
                "contributions": [{"type": "person", "id": person_id, "role": "author"}],
                "bdrc": f"W123456{i+1}",
                "wiki": f"Q789012{i+1}",
                "date": f"2024-01-01{i+1}",
            }
            text = TextInput.model_validate(expr_data)
            text_id = await test_database.text.create(text)
            text_ids.append(text_id)

        response = await client.get("/v2/texts/")

        assert response.status_code == 200
        body = response.json()
        data = _items(body)
        assert body["offset"] == 0
        assert body["limit"] == 20
        assert body["has_more"] is True
        assert isinstance(data, list)
        assert len(data) == 20
        # Verify all returned IDs are from our created texts
        returned_ids = {item["id"] for item in data}
        assert returned_ids.issubset(set(text_ids))

    async def test_get_all_metadata_custom_pagination(self, client, test_database, test_person_data):
        """Test custom pagination parameters"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest
        # Create multiple texts
        text_ids = []
        for i in range(5):
            expr_data = {
                "title": {"en": f"text {i + 1}", "bo": f"ཚིག་སྒྲུབ་{i + 1}།"},
                "language": "en",
                "category_id": category_id,
                "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            }
            text = TextInput.model_validate(expr_data)
            expr_id = await test_database.text.create(text)
            text_ids.append(expr_id)

        # Test limit=2, offset=1
        response = await client.get("/v2/texts?limit=2&offset=1")

        assert response.status_code == 200
        body = response.json()
        data = _items(body)
        assert body["offset"] == 1
        assert body["limit"] == 2
        assert body["has_more"] is True
        assert len(data) == 2
        # Verify returned IDs are from our created texts (order is by id, not creation)
        returned_ids = {item["id"] for item in data}
        assert returned_ids.issubset(set(text_ids))

    async def test_get_all_metadata_filter_by_category(self, client, test_database, test_person_data):
        """Test filtering by category_id"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        # Use pre-created category from conftest as category 1
        category_id_1 = 'category'
        # Create a second category for testing filter
        category_2 = CategoryInput.model_validate({'title': {'en': 'Category 2', 'bo': 'དེབ་སྤྱི་༢།'}})
        category_id_2 = await test_database.category.create(category_2, 'test_application')

        # Create text in category 1
        expr_data_1 = {
            "title": {"en": "text in Category 1", "bo": "རྩ་བའི་ཚིག་སྒྲུབ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id_1,
        }
        text_1 = TextInput.model_validate(expr_data_1)
        expr_id_1 = await test_database.text.create(text_1)

        # Create text in category 2
        expr_data_2 = {
            "title": {"en": "text in Category 2", "bo": "སྒྱུར་བའི་ཚིག་སྒྲུབ།"},
            "language": "bo",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id_2,
        }
        text_2 = TextInput.model_validate(expr_data_2)
        expr_id_2 = await test_database.text.create(text_2)

        # Filter by category_id_1
        response = await client.get(f"/v2/texts?category_id={category_id_1}")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == expr_id_1
        assert data[0]["category_id"] == category_id_1

        # Filter by category_id_2
        response = await client.get(f"/v2/texts?category_id={category_id_2}")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == expr_id_2
        assert data[0]["category_id"] == category_id_2

    async def test_get_all_metadata_filter_by_language(self, client, test_database, test_person_data):
        """Test filtering by language"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)


        category_id = 'category'  # Use pre-created category from conftest
        # Create English text
        en_data = {
            "title": {"en": "English text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        en_text = TextInput.model_validate(en_data)
        en_id = await test_database.text.create(en_text)

        # Create Tibetan text
        bo_data = {
            "title": {"bo": "བོད་ཡིག་ཚིག་སྒྲུབ།"},
            "category_id": category_id,
            "language": "bo",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        bo_text = TextInput.model_validate(bo_data)
        bo_id = await test_database.text.create(bo_text)

        # Filter by English
        response = await client.get("/v2/texts?language=en")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == en_id
        assert data[0]["language"] == "en"

        # Filter by Tibetan
        response = await client.get("/v2/texts?language=bo")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == bo_id
        assert data[0]["language"] == "bo"

    async def test_get_all_metadata_filter_by_title_requires_catalog_search(
        self, client, test_database, test_person_data
    ):
        """Title search is backed by catalog OpenSearch, not Neo4j substring filtering."""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)
        await test_database.text.create(
            TextInput.model_validate(
                {
                    "title": {"en": "Buddha dharma"},
                    "language": "en",
                    "category_id": "category",
                    "contributions": [{"type": "person", "id": person_id, "role": "author"}],
                }
            )
        )

        response = await client.get("/v2/texts?title=Buddha")

        assert response.status_code == 503
        assert response.json()["error"] == "Catalog search is required for text title search"

    async def test_get_all_metadata_filter_by_title_rejects_short_search(self, client):
        """Title search requires at least 2 characters to avoid broad substring scans."""
        response = await client.get("/v2/texts?title=a")

        assert response.status_code == 422

    async def test_get_all_metadata_filter_by_author(self, client, test_database, test_person_data):
        """Test filtering by author_id"""
        # Create two test persons
        person1 = PersonInput.model_validate(test_person_data)
        person1_id = await test_database.person.create(person1)

        person2_data = {
            "name": {"en": "Second Author", "bo": "སློབ་དཔོན་གཉིས་པ།"},
            "bdrc": "P654321",
            "wiki": "Q654321",
        }
        person2 = PersonInput.model_validate(person2_data)
        person2_id = await test_database.person.create(person2)

        category_id = 'category'

        # Create text by person1
        expr1_data = {
            "title": {"en": "text by Author 1", "bo": "རྩོམ་པ་པོ་དང་པོའི་ཚིག་སྒྲུབ།"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person1_id, "role": "author"}],
        }
        text1 = TextInput.model_validate(expr1_data)
        expr1_id = await test_database.text.create(text1)

        # Create text by person2
        expr2_data = {
            "title": {"en": "text by Author 2", "bo": "རྩོམ་པ་པོ་གཉིས་པའི་ཚིག་སྒྲུབ།"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person2_id, "role": "author"}],
        }
        text2 = TextInput.model_validate(expr2_data)
        expr2_id = await test_database.text.create(text2)

        # Create text by both authors
        expr3_data = {
            "title": {"en": "text by Both Authors", "bo": "རྩོམ་པ་པོ་གཉིས་ཀའི་ཚིག་སྒྲུབ།"},
            "language": "bo",
            "category_id": category_id,
            "contributions": [
                {"type": "person", "id": person1_id, "role": "author"},
                {"type": "person", "id": person2_id, "role": "translator"}
            ],
        }
        text3 = TextInput.model_validate(expr3_data)
        expr3_id = await test_database.text.create(text3)

        # Filter by person1_id
        response = await client.get(f"/v2/texts?author_id={person1_id}")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 2
        returned_ids = {item["id"] for item in data}
        assert returned_ids == {expr1_id, expr3_id}

        # Filter by person2_id
        response = await client.get(f"/v2/texts?author_id={person2_id}")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 2
        returned_ids = {item["id"] for item in data}
        assert returned_ids == {expr2_id, expr3_id}

        # Filter by non-existent author
        response = await client.get("/v2/texts?author_id=nonexistent_author_id")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 0



    async def test_get_all_metadata_filter_by_author_and_language(self, client, test_database, test_person_data):
        """Test combining author_id and language filters"""
        # Create two test persons
        person1 = PersonInput.model_validate(test_person_data)
        person1_id = await test_database.person.create(person1)

        person2_data = {
            "name": {"en": "Second Author", "bo": "སློབ་དཔོན་གཉིས་པ།"},
            "bdrc": "P654321",
            "wiki": "Q654321",
        }
        person2 = PersonInput.model_validate(person2_data)
        person2_id = await test_database.person.create(person2)

        category_id = 'category'

        # Create English text by person1
        expr1_data = {
            "title": {"en": "English text by Author 1"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person1_id, "role": "author"}],
        }
        text1 = TextInput.model_validate(expr1_data)
        expr1_id = await test_database.text.create(text1)

        # Create Tibetan text by person1
        expr2_data = {
            "title": {"bo": "རྩོམ་པ་པོ་དང་པོའི་བོད་ཡིག"},
            "language": "bo",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person1_id, "role": "author"}],
        }
        text2 = TextInput.model_validate(expr2_data)
        expr2_id = await test_database.text.create(text2)

        # Create English text by person2
        expr3_data = {
            "title": {"en": "English text by Author 2"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person2_id, "role": "author"}],
        }
        text3 = TextInput.model_validate(expr3_data)
        expr3_id = await test_database.text.create(text3)

        # Filter by author_id=person1_id AND language=en
        response = await client.get(f"/v2/texts?author_id={person1_id}&language=en")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == expr1_id
        assert data[0]["language"] == "en"

        # Filter by author_id=person1_id AND language=bo
        response = await client.get(f"/v2/texts?author_id={person1_id}&language=bo")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == expr2_id
        assert data[0]["language"] == "bo"

        # Filter by author_id=person2_id AND language=bo (should return nothing)
        response = await client.get(f"/v2/texts?author_id={person2_id}&language=bo")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 0

    async def test_get_all_metadata_invalid_limit(self, client, test_database):
        """Test invalid limit parameters"""

        # Test limit too low - returns 422 for validation errors
        response = await client.get("/v2/texts?limit=0")
        assert response.status_code == 422

        # Test non-integer limit - returns 422 for validation errors
        response = await client.get("/v2/texts?limit=abc")
        assert response.status_code == 422

        # Test limit too high
        response = await client.get("/v2/texts?limit=101")
        assert response.status_code == 422

    async def test_get_all_metadata_invalid_offset(self, client, test_database):
        """Test invalid offset parameters"""

        # Test negative offset - returns 422 for validation errors
        response = await client.get("/v2/texts?offset=-1")
        assert response.status_code == 422

        # Test non-integer offset - returns 422 for validation errors
        response = await client.get("/v2/texts?offset=abc")
        assert response.status_code == 422

    async def test_get_all_metadata_edge_pagination(self, client, test_database, test_person_data):
        """Test edge cases for pagination"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create one text
        expr_data = {
            "title": {"en": "Single text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        await test_database.text.create(text)

        # Test limit=1 (minimum)
        response = await client.get("/v2/texts?limit=1")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1

        # Test limit=100 (maximum)
        response = await client.get("/v2/texts?limit=100")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1

        # Test large offset (beyond available data)
        response = await client.get("/v2/texts?offset=1000")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 0


@pytest.mark.asyncio(loop_scope="session")
class TestGetSingleTextV2:
    """Tests for GET /v2/texts/{text_id} endpoint (get single text)"""

    async def test_get_single_metadata_by_text_id_success(self, client, test_database, test_person_data, test_text_data):
        """Test successfully retrieving a single text"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)
        category_id = 'category'  # Use pre-created category from conftest
        # Create test text
        test_text_data["contributions"] = [{"type": "person", "id": person_id, "role": "author"}]
        test_text_data["category_id"] = category_id
        text = TextInput.model_validate(test_text_data)
        text_id = await test_database.text.create(text)

        response = await client.get(f"/v2/texts/{text_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == text_id
        assert data["title"]["en"] == "Test text"
        assert data["title"]["bo"] == "བརྟག་དཔྱད་ཚིག་སྒྲུབ།"
        assert data["language"] == "en"
        assert data["date"] == "2024-01-01"
        assert data["bdrc"] == "W123456"
        assert data["wiki"] == "Q789012"
        assert len(data["contributions"]) == 1
        assert data["contributions"][0]["role"] == "author"
        assert data["commentary_of"] is None
        assert data["translation_of"] is None
        assert data["category_id"] == category_id

    async def test_get_texts_filter_by_bdrc(self, client, test_database, test_person_data, test_text_data):
        """Test filtering texts by BDRC ID using query parameter"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)
        category_id = 'category'
        # Create test text
        test_text_data["contributions"] = [{"type": "person", "id": person_id, "role": "author"}]
        test_text_data["category_id"] = category_id
        text = TextInput.model_validate(test_text_data)
        text_id = await test_database.text.create(text)

        response = await client.get(f"/v2/texts?bdrc={test_text_data['bdrc']}")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["bdrc"] == test_text_data['bdrc']
        assert data[0]["title"]["en"] == "Test text"
        assert data[0]["id"] == text_id


    async def test_get_single_translation_metadata_success(self, client, test_database, test_person_data):
        """Test successfully retrieving a translation text"""

        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)
        category_id = 'category'  # Use pre-created category from conftest
        # Create target ROOT text
        root_data = {
            "title": {"en": "Target Root text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        root_text = TextInput.model_validate(root_data)
        target_id = await test_database.text.create(root_text)

        # Create TRANSLATION text
        translation_data = {
            "title": {"bo": "སྒྱུར་བའི་ཚིག་སྒྲུབ།", "en": "Translation text"},
            "language": "bo",
            "translation_of": target_id,
            "contributions": [{"type": "person", "id": person_id, "role": "translator"}],
        }
        translation_text = TextInput.model_validate(translation_data)
        translation_id = await test_database.text.create(translation_text)

        response = await client.get(f"/v2/texts/{translation_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["translation_of"] == target_id
        assert data["commentary_of"] is None
        assert data["language"] == "bo"
        assert data["contributions"][0]["role"] == "translator"

    async def test_get_single_metadata_invalid_id(self, client, test_database):
        """Test retrieving invalid text id"""

        response = await client.get("/v2/texts/invalid_id")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["error"].lower()


@pytest.mark.asyncio(loop_scope="session")
class TestPostTextV2:
    """Tests for POST /v2/texts/ endpoint (create text)"""

    async def test_create_root_text_success(self, client, test_database, test_person_data):
        """Test successfully creating a ROOT text"""
        # Create test person first
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create ROOT text (no type field, no translation_of/commentary_of)
        text_data = {
            "title": {"en": "New Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id,
            "license": "cc0"
        }

        response = await client.post("/v2/texts", json=text_data)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        # Verify the text was created by retrieving it
        created_id = data["id"]
        verify_response = await client.get(f"/v2/texts/{created_id}")
        assert verify_response.status_code == 200
        verify_data = verify_response.json()
        assert verify_data["title"]["en"] == "New Root text"
        assert verify_data["translation_of"] is None
        assert verify_data["commentary_of"] is None

    async def test_create_text_without_contributions_success(self, client):
        """Test creating a text when attribution is unknown"""
        text_data = {
            "title": {"en": "Anonymous text"},
            "language": "en",
            "category_id": "category",
        }

        response = await client.post("/v2/texts", json=text_data)

        assert response.status_code == 201
        created_id = response.json()["id"]

        verify_response = await client.get(f"/v2/texts/{created_id}")
        assert verify_response.status_code == 200
        assert verify_response.json()["contributions"] == []

    async def test_create_text_with_duplicate_title_rejected(self, client, test_database, test_person_data):
        """Test creating a text with a title already used by another text is rejected."""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        text_data = {
            "title": {"en-US": "Duplicate BCP47 Title"},
            "language": "en-US",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": "category",
            "license": "cc0",
        }

        response_1 = await client.post("/v2/texts", json=text_data)
        assert response_1.status_code == 201

        duplicate_text_data = {
            **text_data,
            "bdrc": "T_DUPLICATE_TITLE",
        }

        response_2 = await client.post("/v2/texts", json=duplicate_text_data)

        assert response_2.status_code == 422
        assert "error" in response_2.json()
        assert "already exists" in response_2.json()["error"].lower()

    async def test_create_text_missing_json(self, client):
        """Test POST with no JSON data"""

        response = await client.post("/v2/texts", headers={"Content-Type": "application/json"})

        assert response.status_code == 422  # Returns 400 for missing JSON body
        data = response.json()
        assert "detail" in data

    async def test_create_text_invalid_json(self, client):
        """Test POST with invalid JSON"""

        response = await client.post("/v2/texts", content="invalid json", headers={"Content-Type": "application/json"})

        assert response.status_code == 422  # Returns 400 for invalid JSON
        data = response.json()
        assert "detail" in data

    async def test_create_text_missing_required_fields(self, client):
        """Test POST with missing required fields"""

        # Missing title field
        text_data = {"language": "en", "contributions": []}

        response = await client.post("/v2/texts", json=text_data)

        assert response.status_code == 422  # Proper validation error status
        data = response.json()
        assert "detail" in data

    async def test_create_root_text_with_both_relations_fails(self, client):
        """Test that text with both commentary_of and translation_of fails validation"""
        text_data = {
            "title": {"en": "Test"},
            "language": "en",
            "translation_of": "some_target_id",
            "commentary_of": "another_target_id",
            "contributions": [],
            "license": "cc0",
        }

        response = await client.post("/v2/texts", json=text_data)

        assert response.status_code == 422

    async def test_create_translation_with_valid_root_target_success(self, client, test_database, test_person_data):
        """Test successfully creating a TRANSLATION with a valid root target"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create ROOT text
        root_data = {
            "title": {"en": "Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id,
        }
        root_text = TextInput.model_validate(root_data)
        root_id = await test_database.text.create(root_text)

        # Create TRANSLATION text
        translation_data = {
            "title": {"en": "Translation text", "bo": "སྒྱུར་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "bo",
            "translation_of": root_id,
            "contributions": [{"type": "person", "id": person_id, "role": "translator"}],
        }
        response = await client.post("/v2/texts", json=translation_data)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/texts/{data['id']}")).json()
        assert stored["translation_of"] == root_id
        assert stored["category_id"] == category_id
        assert stored["title"] == translation_data["title"]
        root = (await client.get(f"/v2/texts/{root_id}")).json()
        assert root["translations"] == [data["id"]]

    async def test_create_commentary_with_valid_root_target_success(self, client, test_database, test_person_data):
        """Test successfully creating a COMMENTARY with a valid root target"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create ROOT text
        root_data = {
            "title": {"en": "Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id,
        }
        root_text = TextInput.model_validate(root_data)
        root_id = await test_database.text.create(root_text)

        # Create COMMENTARY text
        commentary_data = {
            "title": {"en": "Commentary text", "bo": "འགྲེལ་པ།"},
            "language": "bo",
            "commentary_of": root_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id
        }
        response = await client.post("/v2/texts", json=commentary_data)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/texts/{data['id']}")).json()
        assert stored["commentary_of"] == root_id
        assert stored["category_id"] == category_id
        assert stored["title"] == commentary_data["title"]
        root = (await client.get(f"/v2/texts/{root_id}")).json()
        assert root["commentaries"] == [data["id"]]

    async def test_create_translation_with_invalid_root_target(self, client, test_database, test_person_data):
        """Test creating a TRANSLATION with an invalid root target"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create TRANSLATION text
        translation_data = {
            "title": {"en": "Translation text", "bo": "སྒྱུར་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "bo",
            "translation_of": "invalid_target",
            "contributions": [{"type": "person", "id": person_id, "role": "translator"}],
        }
        response = await client.post("/v2/texts", json=translation_data)

        assert response.status_code == 404

    async def test_create_commentary_with_invalid_root_target(self, client, test_database, test_person_data):
        """Test creating a COMMENTARY with an invalid root target"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create COMMENTARY text
        commentary_data = {
            "title": {"en": "Commentary text", "bo": "འགྲེལ་པ།"},
            "language": "bo",
            "commentary_of": "invalid_target",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id
        }
        response = await client.post("/v2/texts", json=commentary_data)

        assert response.status_code == 404
    
    async def test_create_text_without_category_id(self, client, test_database, test_person_data):
        """Test creating a text without a category ID"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        root_data = {
            "title": {"en": "Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}]
        }

        response = await client.post("/v2/texts", json=root_data)

        assert response.status_code == 422

    async def test_create_text_with_invalid_person_role(self, client, test_database, test_person_data):
        """Test creating a text with an invalid person role"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create ROOT text
        root_data = {
            "title": {"en": "Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "invalid_role"}],
            "category_id": category_id
        }
        response = await client.post("/v2/texts", json=root_data)

        assert response.status_code == 422
    
    async def test_create_text_with_person_contribution_both_id_and_bdrc_id(
        self, client, test_database, test_person_data
    ):
        """Test creating a text with a person contribution containing both id and bdrc_id."""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        root_data = {
            "title": {"en": "Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "bdrc_id": "P123456", "role": "author"}],
            "category_id": category_id
        }

        response = await client.post("/v2/texts", json=root_data)

        assert response.status_code == 422

    async def test_create_text_with_ai_contribution_rejects_bdrc_id(self, client):
        """AI contributions use id only and cannot include bdrc_id."""
        root_data = {
            "title": {"en": "AI translated text"},
            "language": "en",
            "contributions": [{"type": "ai", "id": "gpt-4", "bdrc_id": "P123456", "role": "translator"}],
            "category_id": "category",
        }

        response = await client.post("/v2/texts", json=root_data)

        assert response.status_code == 422

    async def test_create_text_with_existing_bdrc_id(self, client, test_database, test_person_data):
        """Test creating a text with an existing BDRC ID"""
        # Create test person first
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'  # Use pre-created category from conftest

        # Create ROOT text
        text_data = {
            "bdrc": "T1234567",
            "title": {"en": "New Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གསར་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id,
            "license": "cc0"
        }
        response_1 = await client.post("/v2/texts", json=text_data)

        assert response_1.status_code == 201

        duplicate_text_data = {
            "bdrc": "T1234567",
            "title": {"en": "Duplicate Root text", "bo": "རྩ་བའི་ཚིག་སྒྲུབ་གཞན་པ།"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": category_id,
            "license": "cc0"
        }

        response_2 = await client.post("/v2/texts", json=duplicate_text_data)

        assert response_2.status_code == 409
        assert "error" in response_2.json()
        assert "already exists" in response_2.json()["error"].lower()

    async def test_create_text_with_nonexistent_category_id(self, client, test_database, test_person_data):
        """Test creating a text with a non-existent category_id returns an error"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        text_data = {
            "title": {"en": "Orphan Text"},
            "language": "en",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "category_id": "nonexistent_category_id",
        }

        response = await client.post("/v2/texts", json=text_data)

        assert response.status_code == 422
        data = response.json()
        assert "error" in data


@pytest.mark.asyncio(loop_scope="session")
class TestPatchTextV2:
    """Tests for PATCH /v2/texts/{text_id} endpoint (update text)"""

    async def test_patch_text_update_bdrc_only(self, client, test_database, test_person_data):
        """Test updating only the bdrc field"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Original Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "bdrc": "W111111",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"bdrc": "W222222"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == text_id
        assert data["bdrc"] == "W222222"
        assert data["title"]["en"] == "Original Title"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "W222222"
        assert get_data["title"]["en"] == "Original Title"

    async def test_patch_text_adds_contributions_when_created_without_any(
        self, client, test_database, test_person_data
    ):
        """Test attributing a text that was created without contributions"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        text = TextInput.model_validate(
            {"title": {"en": "Unattributed Title"}, "language": "en", "category_id": "category"}
        )
        text_id = await test_database.text.create(text)

        response = await client.patch(
            f"/v2/texts/{text_id}",
            json={"contributions": [{"type": "person", "id": person_id, "role": "author"}]},
        )

        assert response.status_code == 200
        assert response.json()["contributions"] == [
            {"type": "person", "id": person_id, "bdrc_id": "P123456", "role": "author", "name": person.name.root}
        ]

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert len(get_response.json()["contributions"]) == 1

    async def test_patch_text_replaces_existing_contributions(self, client, test_database, test_person_data):
        """Test that contributions are replaced as a whole set, not appended to"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        text = TextInput.model_validate(
            {
                "title": {"en": "Replaced Contributions"},
                "language": "en",
                "category_id": "category",
                "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            }
        )
        text_id = await test_database.text.create(text)

        response = await client.patch(
            f"/v2/texts/{text_id}",
            json={"contributions": [{"type": "ai", "id": "gpt-4", "role": "translator"}]},
        )

        assert response.status_code == 200
        assert response.json()["contributions"] == [{"type": "ai", "id": "gpt-4", "role": "translator"}]

    async def test_patch_text_clears_contributions(self, client, test_database, test_person_data):
        """Test removing every contribution with an empty list"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        text = TextInput.model_validate(
            {
                "title": {"en": "Cleared Contributions"},
                "language": "en",
                "category_id": "category",
                "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            }
        )
        text_id = await test_database.text.create(text)

        response = await client.patch(f"/v2/texts/{text_id}", json={"contributions": []})

        assert response.status_code == 200
        assert response.json()["contributions"] == []

    async def test_patch_text_with_nonexistent_person_keeps_contributions(
        self, client, test_database, test_person_data
    ):
        """Test that a rejected contribution patch leaves the existing contributions in place"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        text = TextInput.model_validate(
            {
                "title": {"en": "Kept Contributions"},
                "language": "en",
                "category_id": "category",
                "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            }
        )
        text_id = await test_database.text.create(text)

        response = await client.patch(
            f"/v2/texts/{text_id}",
            json={"contributions": [{"type": "person", "id": "P00000000", "role": "author"}]},
        )

        assert response.status_code == 422
        assert "do not exist" in response.json()["error"].lower()

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.json()["contributions"][0]["id"] == person_id

    async def test_patch_text_update_wiki_only(self, client, test_database, test_person_data):
        """Test updating only the wiki field"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Wiki Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "wiki": "Q111111",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"wiki": "Q222222"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["wiki"] == "Q222222"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["wiki"] == "Q222222"

    async def test_patch_text_update_date_only(self, client, test_database, test_person_data):
        """Test updating only the date field"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Date Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "date": "2024-01-01",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"date": "2025-06-15"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2025-06-15"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["date"] == "2025-06-15"

    async def test_patch_text_update_title_only(self, client, test_database, test_person_data):
        """Test updating only the title field"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Original Title", "bo": "བོད་མཚན་བྱང་།"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": {"en": "Updated Title", "bo": "གསར་བསྒྱུར་མཚན་བྱང་།"}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]["en"] == "Updated Title"
        assert data["title"]["bo"] == "གསར་བསྒྱུར་མཚན་བྱང་།"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Updated Title"
        assert get_data["title"]["bo"] == "གསར་བསྒྱུར་མཚན་བྱང་།"

    async def test_patch_text_update_alt_titles_only(self, client, test_database, test_person_data):
        """Test updating only the alt_titles field"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Primary Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "alt_titles": [{"en": "Old Alt Title"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"alt_titles": [{"en": "New Alt Title 1"}, {"en": "New Alt Title 2"}]}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]["en"] == "Primary Title"
        assert len(data["alt_titles"]) == 2
        alt_titles_en = [alt.get("en") for alt in data["alt_titles"] if "en" in alt]
        assert "New Alt Title 1" in alt_titles_en
        assert "New Alt Title 2" in alt_titles_en

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert len(get_data["alt_titles"]) == 2

    async def test_patch_text_update_license_only(self, client, test_database, test_person_data):
        """Test updating only the license field"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "License Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "license": "public",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"license": "cc0"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["license"] == "cc0"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["license"] == "cc0"

    async def test_patch_text_update_multiple_fields(self, client, test_database, test_person_data):
        """Test updating multiple fields at once"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Original"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "bdrc": "W333333",
            "wiki": "Q333333",
            "license": "public",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {
            "title": {"en": "Updated"},
            "bdrc": "W444444",
            "wiki": "Q444444",
            "license": "cc-by",
        }
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]["en"] == "Updated"
        assert data["bdrc"] == "W444444"
        assert data["wiki"] == "Q444444"
        assert data["license"] == "cc-by"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Updated"
        assert get_data["bdrc"] == "W444444"
        assert get_data["wiki"] == "Q444444"
        assert get_data["license"] == "cc-by"

    async def test_patch_text_not_found(self, client, test_database):
        """Test patching a non-existent text returns 404"""
        patch_data = {"bdrc": "W555555"}
        response = await client.patch(
            "/v2/texts/nonexistent_id",
            json=patch_data,
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert "not found" in data["error"].lower()

    async def test_patch_text_empty_payload_rejected(self, client, test_database, test_person_data):
        """Test that empty payload is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_patch_text_null_field_rejected(self, client, test_database, test_person_data):
        """Test that explicit null fields are rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "bdrc": "W_NULL_PATCH",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        response = await client.patch(
            f"/v2/texts/{text_id}",
            json={"bdrc": None},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert "Null values are not supported in PATCH" in str(data["detail"])

    async def test_patch_text_unknown_field_rejected(self, client, test_database, test_person_data):
        """Test that unknown fields are rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"unknown_field": "value"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_patch_text_preserves_unpatched_fields(self, client, test_database, test_person_data):
        """Test that fields not in patch are preserved"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Original Title", "bo": "བོད་མཚན་བྱང་།"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "alt_titles": [{"en": "Alt Title"}],
            "bdrc": "W101010",
            "wiki": "Q101010",
            "date": "2024-01-01",
            "license": "public",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"bdrc": "W202020"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["bdrc"] == "W202020"
        assert data["wiki"] == "Q101010"
        assert data["title"]["en"] == "Original Title"
        assert data["title"]["bo"] == "བོད་མཚན་བྱང་།"
        assert data["date"] == "2024-01-01"
        assert data["license"] == "public"
        assert len(data["alt_titles"]) == 1

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "W202020"
        assert get_data["wiki"] == "Q101010"
        assert get_data["title"]["en"] == "Original Title"
        assert get_data["date"] == "2024-01-01"
        assert get_data["license"] == "public"

    async def test_patch_text_with_tibetan_title(self, client, test_database, test_person_data):
        """Test patching with Tibetan title (must include text's language)"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "English Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": {"en": "Updated English", "bo": "བོད་སྐད་མཚན་བྱང་།"}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]["en"] == "Updated English"
        assert data["title"]["bo"] == "བོད་སྐད་མཚན་བྱང་།"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Updated English"
        assert get_data["title"]["bo"] == "བོད་སྐད་མཚན་བྱང་།"

    async def test_patch_text_title_missing_text_language_rejected(self, client, test_database, test_person_data):
        """Test that patching title without text's language is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "English Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": {"bo": "བོད་སྐད་མཚན་བྱང་།"}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        # Handle both FastAPI field validation (list) and Pydantic model validation (string)
        detail = data["detail"]
        if isinstance(detail, list):
            # FastAPI field validation errors
            assert any("language" in str(error.get("msg", "")).lower() for error in detail)
        else:
            # Pydantic model validation error
            assert isinstance(detail, str)
            assert "language" in detail.lower()

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "English Title"

    async def test_patch_text_clear_alt_titles(self, client, test_database, test_person_data):
        """Test clearing alt_titles by providing empty list"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Primary Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "alt_titles": [{"en": "Alt Title 1"}, {"en": "Alt Title 2"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"alt_titles": []}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["alt_titles"] is None or data["alt_titles"] == []

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["alt_titles"] is None or get_data["alt_titles"] == []

    async def test_patch_text_update_language_with_bcp47(self, client, test_database, test_person_data):
        """Test updating language with BCP47 code"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "English Title", "bo": "བོད་སྐད་མཚན་བྱང་།"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"language": "bo-Latn"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "bo-Latn"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["language"] == "bo-Latn"

    async def test_patch_text_invalid_json(self, client, test_database, test_person_data):
        """Test that invalid JSON is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        response = await client.patch(
            f"/v2/texts/{text_id}",
            content="invalid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_patch_text_invalid_title_structure(self, client, test_database, test_person_data):
        """Test that invalid title structure is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": "not a dict"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Test Text"

    async def test_patch_text_empty_title_rejected(self, client, test_database, test_person_data):
        """Test that empty title dict is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": {}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Test Text"

    async def test_patch_text_invalid_license_rejected(self, client, test_database, test_person_data):
        """Test that invalid license value is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"license": "invalid_license"}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["license"] == "public"

    async def test_patch_text_alt_title_same_as_primary_deduped(self, client, test_database, test_person_data):
        """Test that alt_title identical to title is deduplicated"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Primary Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {
            "title": {"en": "New Primary"},
            "alt_titles": [{"en": "New Primary"}, {"en": "Different Alt"}],
        }
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        alt_titles_en = [alt.get("en") for alt in data.get("alt_titles", []) if "en" in alt]
        assert "New Primary" not in alt_titles_en
        assert "Different Alt" in alt_titles_en

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "New Primary"
        get_alt_titles_en = [alt.get("en") for alt in get_data.get("alt_titles", []) if "en" in alt]
        assert "New Primary" not in get_alt_titles_en
        assert "Different Alt" in get_alt_titles_en

    async def test_patch_text_with_invalid_language_code(self, client, test_database, test_person_data):
        """Test patching with invalid language code returns error"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": {"xx": "Invalid Language"}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Test Text"

    async def test_patch_text_title_preserves_other_languages(self, client, test_database, test_person_data):
        """Test that updating a title in one language preserves other language versions"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Original English Title", "bo": "བོད་ཡིག་མཚན་བྱང་།"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        verify_response = await client.get(f"/v2/texts/{text_id}")
        assert verify_response.status_code == 200
        verify_data = verify_response.json()
        assert verify_data["title"]["en"] == "Original English Title"
        assert verify_data["title"]["bo"] == "བོད་ཡིག་མཚན་བྱང་།"

        patch_data = {"title": {"en": "Updated English Title", "bo": "བོད་ཡིག་མཚན་བྱང་།"}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]["en"] == "Updated English Title"
        assert data["title"]["bo"] == "བོད་ཡིག་མཚན་བྱང་།"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "Updated English Title"
        assert get_data["title"]["bo"] == "བོད་ཡིག་མཚན་བྱང་།"

    async def test_patch_text_title_adds_new_language(self, client, test_database, test_person_data):
        """Test that patching title with a new language adds it"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "English Title"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        patch_data = {"title": {"en": "English Title", "bo": "བོད་ཡིག་མཚན་བྱང་།"}}
        response = await client.patch(
            f"/v2/texts/{text_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]["en"] == "English Title"
        assert data["title"]["bo"] == "བོད་ཡིག་མཚན་བྱང་།"

        get_response = await client.get(f"/v2/texts/{text_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["title"]["en"] == "English Title"
        assert get_data["title"]["bo"] == "བོད་ཡིག་མཚན་བྱང་།"

    async def test_patch_text_license_all_valid_values(self, client, test_database, test_person_data):
        """Test that all valid license values work"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "License Test"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "license": "public",
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        valid_licenses = ["cc0", "cc-by", "cc-by-sa", "copyrighted", "unknown"]
        for license_value in valid_licenses:
            patch_data = {"license": license_value}
            response = await client.patch(
                f"/v2/texts/{text_id}",
                json=patch_data,
            )

            assert response.status_code == 200, f"Failed for license: {license_value}"
            data = response.json()
            assert data["license"] == license_value

            get_response = await client.get(f"/v2/texts/{text_id}")
            assert get_response.status_code == 200
            get_data = get_response.json()
            assert get_data["license"] == license_value

    async def test_patch_text_missing_body_returns_422(self, client, test_database, test_person_data):
        """Test that a missing request body returns 422."""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = 'category'
        expr_data = {
            "title": {"en": "Test Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        text_id = await test_database.text.create(text)

        response = await client.patch(
            f"/v2/texts/{text_id}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_patch_text_duplicate_bdrc_rejected(self, client, test_database, test_person_data):
        """Test that patching a text with a BDRC ID already used by another text is rejected"""
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        category_id = "category"
        text1_data = {
            "title": {"en": "First BDRC Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "bdrc": "W_DUP_PATCH_1",
        }
        text1 = TextInput.model_validate(text1_data)
        await test_database.text.create(text1)

        text2_data = {
            "title": {"en": "Second BDRC Text"},
            "language": "en",
            "category_id": category_id,
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            "bdrc": "W_DUP_PATCH_2",
        }
        text2 = TextInput.model_validate(text2_data)
        text2_id = await test_database.text.create(text2)

        response = await client.patch(
            f"/v2/texts/{text2_id}",
            json={"bdrc": "W_DUP_PATCH_1"},
        )

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()


@pytest.mark.asyncio(loop_scope="session")
class TestGetEditionsV2:
    """Tests for GET /v2/texts/{text_id}/editions endpoint (get editions of a text)"""

    async def _create_test_person(self, db, test_person_data):
        """Helper to create a test person"""
        person = PersonInput.model_validate(test_person_data)
        return await db.person.create(person)

    async def _create_test_text(self, db, person_id, title=None):
        """Helper to create a test text"""
        if title is None:
            title = {"en": "Test text", "bo": "བརྟག་དཔྱད།"}
        expr_data = {
            "title": title,
            "language": "en",
            "category_id": "category",
            "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        }
        text = TextInput.model_validate(expr_data)
        return await db.text.create(text)

    async def test_get_editions_empty_list(self, client, test_database, test_person_data):
        """Test getting editions for an text with no editions"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_editions_single_diplomatic(self, client, test_database, test_person_data):
        """Test getting a single diplomatic edition"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Test content for diplomatic edition",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W12345",
                "source": "Test Source",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 35}]}]
                }]
            },
        }
        post_response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert post_response.status_code == 201
        edition_id = post_response.json()["id"]

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == edition_id
        assert data[0]["type"] == "diplomatic"
        assert data[0]["bdrc"] == "W12345"
        assert data[0]["text_id"] == text_id

    async def test_get_editions_single_critical(self, client, test_database, test_person_data):
        """Test getting a single critical edition"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Test content for critical edition",
            "metadata": {
                "type": "critical",
                "source": "Critical Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 33}]}]
            },
        }
        post_response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert post_response.status_code == 201
        edition_id = post_response.json()["id"]

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == edition_id
        assert data[0]["type"] == "critical"

    async def test_get_editions_multiple_editions(self, client, test_database, test_person_data):
        """Test getting multiple editions for an text"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        diplomatic_data = {
            "content": "Diplomatic content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W11111",
                "source": "Source A",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response_1 = await client.post(f"/v2/texts/{text_id}/editions", json=diplomatic_data)
        assert post_response_1.status_code == 201
        diplomatic_id = post_response_1.json()["id"]

        diplomatic_data_2 = {
            "content": "Another diplomatic content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W22222",
                "source": "Source B",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 26}]}]
                }]
            },
        }
        post_response_2 = await client.post(f"/v2/texts/{text_id}/editions", json=diplomatic_data_2)
        assert post_response_2.status_code == 201
        diplomatic_id_2 = post_response_2.json()["id"]

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        returned_ids = {item["id"] for item in data}
        assert diplomatic_id in returned_ids
        assert diplomatic_id_2 in returned_ids

    async def test_get_editions_filter_by_diplomatic_type(self, client, test_database, test_person_data):
        """Test filtering editions by diplomatic type"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        diplomatic_data = {
            "content": "Diplomatic content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W11111",
                "source": "Source A",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response_1 = await client.post(f"/v2/texts/{text_id}/editions", json=diplomatic_data)
        assert post_response_1.status_code == 201
        diplomatic_id = post_response_1.json()["id"]

        critical_data = {
            "content": "Critical content",
            "metadata": {
                "type": "critical",
                "source": "Critical Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 16}]}]
            },
        }
        post_response_2 = await client.post(f"/v2/texts/{text_id}/editions", json=critical_data)
        assert post_response_2.status_code == 201

        response = await client.get(f"/v2/texts/{text_id}/editions?edition_type=diplomatic")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == diplomatic_id
        assert data[0]["type"] == "diplomatic"

    async def test_get_editions_filter_by_critical_type(self, client, test_database, test_person_data):
        """Test filtering editions by critical type"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        diplomatic_data = {
            "content": "Diplomatic content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W11111",
                "source": "Source A",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response_1 = await client.post(f"/v2/texts/{text_id}/editions", json=diplomatic_data)
        assert post_response_1.status_code == 201

        critical_data = {
            "content": "Critical content",
            "metadata": {
                "type": "critical",
                "source": "Critical Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 16}]}]
            },
        }
        post_response_2 = await client.post(f"/v2/texts/{text_id}/editions", json=critical_data)
        assert post_response_2.status_code == 201
        critical_id = post_response_2.json()["id"]

        response = await client.get(f"/v2/texts/{text_id}/editions?edition_type=critical")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == critical_id
        assert data[0]["type"] == "critical"

    async def test_get_editions_filter_returns_empty_when_no_match(self, client, test_database, test_person_data):
        """Test filtering returns empty list when no editions match the type"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        diplomatic_data = {
            "content": "Diplomatic content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W11111",
                "source": "Source A",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response = await client.post(f"/v2/texts/{text_id}/editions", json=diplomatic_data)
        assert post_response.status_code == 201

        response = await client.get(f"/v2/texts/{text_id}/editions?edition_type=critical")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0

    async def test_get_editions_invalid_text_id(self, client, test_database):
        """Test getting editions for a non-existent text returns empty list"""
        response = await client.get("/v2/texts/non-existent-id/editions")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_editions_invalid_edition_type(self, client, test_database, test_person_data):
        """Test filtering with invalid edition type returns 422"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        response = await client.get(f"/v2/texts/{text_id}/editions?edition_type=invalid_type")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_get_editions_returns_all_metadata_fields(self, client, test_database, test_person_data):
        """Test that returned editions include all expected metadata fields"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Test content with all fields",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W99999",
                "wiki": "Q88888",
                "source": "Complete Source",
                "colophon": "Test colophon text",
                "incipit_title": {"en": "Opening words", "bo": "དབུ་ཚིག"},
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 28}]}]
                }]
            },
        }
        post_response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert post_response.status_code == 201
        edition_id = post_response.json()["id"]

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        edition = data[0]
        assert edition["id"] == edition_id
        assert edition["text_id"] == text_id
        assert edition["type"] == "diplomatic"
        assert edition["bdrc"] == "W99999"
        assert edition["wiki"] == "Q88888"
        assert edition["colophon"] == "Test colophon text"
        assert edition["incipit_title"]["en"] == "Opening words"
        assert edition["incipit_title"]["bo"] == "དབུ་ཚིག"

    async def test_get_editions_no_filter_returns_all_types(self, client, test_database, test_person_data):
        """Test that no filter returns all edition types"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        diplomatic_data = {
            "content": "Diplomatic content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W11111",
                "source": "Source A",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response_1 = await client.post(f"/v2/texts/{text_id}/editions", json=diplomatic_data)
        assert post_response_1.status_code == 201

        critical_data = {
            "content": "Critical content",
            "metadata": {
                "type": "critical",
                "source": "Critical Source",
            },
            "segmentation": {
                "segments": [{"lines": [{"start": 0, "end": 16}]}]
            },
        }
        post_response_2 = await client.post(f"/v2/texts/{text_id}/editions", json=critical_data)
        assert post_response_2.status_code == 201

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        types = {item["type"] for item in data}
        assert "diplomatic" in types
        assert "critical" in types

    async def test_get_editions_different_texts_isolated(self, client, test_database, test_person_data):
        """Test that editions from different texts are isolated"""
        person_id = await self._create_test_person(test_database, test_person_data)

        text_id_1 = await self._create_test_text(
            test_database, person_id, title={"en": "text 1"}
        )
        text_id_2 = await self._create_test_text(
            test_database, person_id, title={"en": "text 2"}
        )

        edition_data_1 = {
            "content": "Content for text 1",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W11111",
                "source": "Source 1",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response_1 = await client.post(f"/v2/texts/{text_id_1}/editions", json=edition_data_1)
        assert post_response_1.status_code == 201
        edition_id_1 = post_response_1.json()["id"]

        edition_data_2 = {
            "content": "Content for text 2",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W22222",
                "source": "Source 2",
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 18}]}]
                }]
            },
        }
        post_response_2 = await client.post(f"/v2/texts/{text_id_2}/editions", json=edition_data_2)
        assert post_response_2.status_code == 201
        edition_id_2 = post_response_2.json()["id"]

        response_1 = await client.get(f"/v2/texts/{text_id_1}/editions")
        assert response_1.status_code == 200
        data_1 = response_1.json()
        assert len(data_1) == 1
        assert data_1[0]["id"] == edition_id_1
        assert data_1[0]["text_id"] == text_id_1

        response_2 = await client.get(f"/v2/texts/{text_id_2}/editions")
        assert response_2.status_code == 200
        data_2 = response_2.json()
        assert len(data_2) == 1
        assert data_2[0]["id"] == edition_id_2
        assert data_2[0]["text_id"] == text_id_2

    async def test_get_editions_with_tibetan_content(self, client, test_database, test_person_data):
        """Test getting editions with Tibetan incipit titles"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "བོད་སྐད་ཀྱི་ཡིག་ཆ།",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W77777",
                "source": "Tibetan Source",
                "incipit_title": {"bo": "དབུ་ཚིག་བོད་སྐད།", "en": "Tibetan Opening"},
            },
            "pagination": {
                "volumes": [{
                    "pages": [{"reference": "1a", "lines": [{"start": 0, "end": 17}]}]
                }]
            },
        }
        post_response = await client.post(f"/v2/texts/{text_id}/editions", json=edition_data)
        assert post_response.status_code == 201

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["incipit_title"]["bo"] == "དབུ་ཚིག་བོད་སྐད།"
        assert data[0]["incipit_title"]["en"] == "Tibetan Opening"

    async def test_get_editions_round_trip(self, client, test_database, test_person_data):
        """Test creating an edition and retrieving it via get_editions"""
        person_id = await self._create_test_person(test_database, test_person_data)
        text_id = await self._create_test_text(test_database, person_id)

        edition_data = {
            "content": "Round trip test content",
            "metadata": {
                "type": "diplomatic",
                "bdrc": "W55555",
                "wiki": "Q66666",
                "source": "Round Trip Source",
                "colophon": "Round trip colophon",
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

        response = await client.get(f"/v2/texts/{text_id}/editions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        edition = data[0]
        assert edition["id"] == edition_id
        assert edition["text_id"] == text_id
        assert edition["type"] == "diplomatic"
        assert edition["bdrc"] == "W55555"
        assert edition["wiki"] == "Q66666"
        assert edition["colophon"] == "Round trip colophon"
