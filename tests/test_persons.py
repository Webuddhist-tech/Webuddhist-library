# pylint: disable=redefined-outer-name
"""
Integration tests for v2/persons endpoints using real Neo4j test instance.

Tests endpoints:
- GET /v2/persons/ (get all persons)
- GET /v2/persons/{id} (get single person)
- POST /v2/persons/ (create person)

Requires environment variables:
- NEO4J_TEST_URI: Neo4j test instance URI
- NEO4J_TEST_PASSWORD: Password for test instance
"""

import logging

import pytest
from models.person import PersonInput

logger = logging.getLogger(__name__)


def _items(data):
    return data["items"] if isinstance(data, dict) and "items" in data else data


@pytest.fixture
async def test_person_data():
    """Sample person data for testing"""
    return {
        "name": {"en": "Test Author", "bo": "རྩོམ་པ་པོ།"},
        "alt_names": [{"en": "Alternative Name", "bo": "མིང་གཞན།"}],
        "bdrc": "P123456",
    }


@pytest.fixture
async def test_person_data_minimal():
    """Minimal person data for testing"""
    return {"name": {"en": "Minimal Person"}}


@pytest.mark.asyncio(loop_scope="session")
class TestGetAllPersonsV2:
    """Tests for GET /v2/persons/ endpoint (get all persons)"""

    async def test_get_all_persons_empty_database(self, client, test_database):
        """Test getting all persons from empty database"""
        response = await client.get("/v2/persons/")
        assert response.status_code == 200
        body = response.json()
        data = _items(body)
        assert body["offset"] == 0
        assert body["limit"] == 20
        assert body["has_more"] is False
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_all_persons_with_data(self, client, test_database, test_person_data):
        """Test getting all persons when database has data"""
        # Create test person directly via DB
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        response = await client.get("/v2/persons/")

        assert response.status_code == 200
        data = _items(response.json())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == person_id
        assert data[0]["name"]["en"] == "Test Author"
        assert data[0]["bdrc"] == "P123456"

    async def test_get_all_persons_multiple_persons(self, client, test_database):
        """Test getting all persons with multiple persons in database"""
        # Create multiple test persons
        person_ids = []
        for i in range(3):
            person_data = {
                "name": {"en": f"Person {i+1}", "bo": f"གང་ཟག་{i+1}།"},
                "bdrc": f"P{i+1:06d}",
            }
            person = PersonInput.model_validate(person_data)
            person_id = await test_database.person.create(person)
            person_ids.append(person_id)

        response = await client.get("/v2/persons/")

        assert response.status_code == 200
        data = _items(response.json())
        assert isinstance(data, list)
        assert len(data) == 3

        # Verify all persons are returned
        returned_ids = [p["id"] for p in data]
        for person_id in person_ids:
            assert person_id in returned_ids

    async def test_get_all_persons_with_alternative_names(self, client, test_database):
        """Test getting persons with alternative names"""
        # Create person with alternative names
        person_data = {
            "name": {"en": "Primary Name", "bo": "གཙོ་བོའི་མིང་།"},
            "alt_names": [
                {"en": "Alt Name 1", "bo": "གཞན་མིང་༡།"},
                {"en": "Alt Name 2"},
            ],
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.get("/v2/persons/")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == person_id
        assert data[0]["name"]["en"] == "Primary Name"
        assert len(data[0]["alt_names"]) == 2
        # Check that both alt names are present (order may vary)
        alt_name_texts = [alt["en"] for alt in data[0]["alt_names"] if "en" in alt]
        assert "Alt Name 1" in alt_name_texts
        assert "Alt Name 2" in alt_name_texts

    async def test_get_all_persons_with_default_pagination(self, client, test_database):
        """Test default pagination (limit=20, offset=0)"""
        # Create 25 test persons
        for i in range(25):
            person_data = {"name": {"en": f"Person {i+1}"}}
            person = PersonInput.model_validate(person_data)
            await test_database.person.create(person)

        # Request without pagination params (should use defaults)
        response = await client.get("/v2/persons/")

        assert response.status_code == 200
        body = response.json()
        data = _items(body)
        assert body["offset"] == 0
        assert body["limit"] == 20
        assert body["has_more"] is True
        # Default limit is 20, so should get 20 persons
        assert len(data) == 20

    async def test_get_all_persons_with_custom_limit(self, client, test_database):
        """Test pagination with custom limit"""
        # Create 15 test persons
        for i in range(15):
            person_data = {"name": {"en": f"Person {i+1}"}}
            person = PersonInput.model_validate(person_data)
            await test_database.person.create(person)

        # Request with limit=5
        response = await client.get("/v2/persons/?limit=5")

        assert response.status_code == 200
        body = response.json()
        data = _items(body)
        assert body["limit"] == 5
        assert body["has_more"] is True
        assert len(data) == 5

    async def test_get_all_persons_with_custom_offset(self, client, test_database):
        """Test pagination with custom offset"""
        # Create 10 test persons with identifiable names
        created_ids = []
        for i in range(10):
            person_data = {"name": {"en": f"Person {i+1:02d}"}}
            person = PersonInput.model_validate(person_data)
            person_id = await test_database.person.create(person)
            created_ids.append(person_id)

        # Request first page (offset=0, limit=5)
        response1 = await client.get("/v2/persons/?limit=5&offset=0")
        assert response1.status_code == 200
        data1 = _items(response1.json())
        assert len(data1) == 5

        # Request second page (offset=5, limit=5)
        response2 = await client.get("/v2/persons/?limit=5&offset=5")
        assert response2.status_code == 200
        data2 = _items(response2.json())
        assert len(data2) == 5

        # Verify no overlap between pages
        ids1 = [p["id"] for p in data1]
        ids2 = [p["id"] for p in data2]
        assert len(set(ids1) & set(ids2)) == 0  # No common IDs

    async def test_get_all_persons_limit_edge_cases(self, client, test_database):
        """Test pagination limit edge cases (min=1, max=100)"""
        # Create some test persons
        for i in range(5):
            person_data = {"name": {"en": f"Person {i+1}"}}
            person = PersonInput.model_validate(person_data)
            await test_database.person.create(person)

        # Test limit=1
        response = await client.get("/v2/persons/?limit=1")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1

        # Test limit=100 (max allowed)
        response = await client.get("/v2/persons/?limit=100")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 5  # Only 5 persons exist

    async def test_get_all_persons_invalid_limit_too_low(self, client, test_database):
        """Test pagination with limit less than 1"""
        response = await client.get("/v2/persons/?limit=0")
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        detail = data["detail"]
        assert isinstance(detail, list)
        assert any("greater than or equal to 1" in str(error.get("msg", "")) for error in detail)

    async def test_get_all_persons_invalid_limit_too_high(self, client, test_database):
        """Test pagination with limit greater than 100"""
        response = await client.get("/v2/persons/?limit=101")
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        detail = data["detail"]
        assert isinstance(detail, list)
        assert any("less than or equal to 100" in str(error.get("msg", "")) for error in detail)

    async def test_get_all_persons_invalid_offset_negative(self, client, test_database):
        """Test pagination with negative offset"""
        response = await client.get("/v2/persons/?offset=-1")
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        detail = data["detail"]
        assert isinstance(detail, list)
        assert any("greater than or equal to 0" in str(error.get("msg", "")) for error in detail)

    async def test_get_all_persons_offset_beyond_results(self, client, test_database):
        """Test pagination with offset beyond available results"""
        # Create 5 test persons
        for i in range(5):
            person_data = {"name": {"en": f"Person {i+1}"}}
            person = PersonInput.model_validate(person_data)
            await test_database.person.create(person)

        # Request with offset=100 (beyond all results)
        response = await client.get("/v2/persons/?offset=100")
        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 0  # Should return empty list

    async def test_get_all_persons_pagination_consistency(self, client, test_database):
        """Test that paginated results cover all data without duplication"""
        # Create 30 test persons
        all_created_ids = []
        for i in range(30):
            person_data = {"name": {"en": f"Person {i+1:02d}"}}
            person = PersonInput.model_validate(person_data)
            person_id = await test_database.person.create(person)
            all_created_ids.append(person_id)

        # Fetch all persons using pagination (3 pages of 10)
        all_fetched_ids = []
        for page in range(3):
            response = await client.get(f"/v2/persons/?limit=10&offset={page*10}")
            assert response.status_code == 200
            data = _items(response.json())
            fetched_ids = [p["id"] for p in data]
            all_fetched_ids.extend(fetched_ids)

        # Verify we got all persons exactly once
        assert len(all_fetched_ids) == 30
        assert len(set(all_fetched_ids)) == 30  # No duplicates
        assert set(all_fetched_ids) == set(all_created_ids)  # Same set of IDs

    async def test_get_all_persons_invalid_limit_non_integer(self, client, test_database):
        """Non-integer limit should return 422 validation error."""
        response = await client.get("/v2/persons/?limit=abc")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_get_all_persons_invalid_offset_non_integer(self, client, test_database):
        """Non-integer offset should return 422 validation error."""
        response = await client.get("/v2/persons/?offset=abc")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_get_all_persons_empty_database_with_pagination_params(
        self, client, test_database
    ):
        """Empty DB + explicit limit/offset should still return an empty list with 200."""
        response = await client.get("/v2/persons/?limit=10&offset=5")

        assert response.status_code == 200
        data = _items(response.json())
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_all_persons_filter_by_name_requires_catalog_search(self, client, test_database):
        """Name search is backed by catalog OpenSearch, not Neo4j substring filtering."""
        await test_database.person.create(PersonInput.model_validate({"name": {"en": "John Smith"}}))

        response = await client.get("/v2/persons/?name=John")

        assert response.status_code == 503
        assert response.json()["error"] == "Catalog search is required for person name search"

    async def test_get_all_persons_filter_by_name_rejects_short_search(self, client):
        """Name search requires at least 2 characters to avoid broad substring scans."""
        response = await client.get("/v2/persons/?name=a")

        assert response.status_code == 422

    async def test_get_all_persons_filter_by_bdrc(self, client, test_database):
        """Test filtering persons by BDRC ID"""
        person1 = PersonInput.model_validate({"name": {"en": "Person 1"}, "bdrc": "P111111"})
        person2 = PersonInput.model_validate({"name": {"en": "Person 2"}, "bdrc": "P222222"})
        person1_id = await test_database.person.create(person1)
        await test_database.person.create(person2)

        response = await client.get("/v2/persons/?bdrc=P111111")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == person1_id
        assert data[0]["bdrc"] == "P111111"

    async def test_get_all_persons_filter_by_wiki(self, client, test_database):
        """Test filtering persons by Wiki ID"""
        person1 = PersonInput.model_validate({"name": {"en": "Person 1"}, "wiki": "Q111111"})
        person2 = PersonInput.model_validate({"name": {"en": "Person 2"}, "wiki": "Q222222"})
        person1_id = await test_database.person.create(person1)
        await test_database.person.create(person2)

        response = await client.get("/v2/persons/?wiki=Q111111")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == person1_id
        assert data[0]["wiki"] == "Q111111"

    async def test_get_all_persons_filter_combined(self, client, test_database):
        """Test filtering persons with multiple filters"""
        person1 = PersonInput.model_validate({
            "name": {"en": "Target Person"},
            "bdrc": "P333333",
            "wiki": "Q333333",
        })
        person2 = PersonInput.model_validate({
            "name": {"en": "Other Person"},
            "bdrc": "P444444",
            "wiki": "Q444444",
        })
        person1_id = await test_database.person.create(person1)
        await test_database.person.create(person2)

        response = await client.get("/v2/persons/?bdrc=P333333&wiki=Q333333")

        assert response.status_code == 200
        data = _items(response.json())
        assert len(data) == 1
        assert data[0]["id"] == person1_id


@pytest.mark.asyncio(loop_scope="session")
class TestGetSinglePersonV2:
    """Tests for GET /v2/persons/{id} endpoint (get single person)"""

    async def test_get_single_person_success(self, client, test_database, test_person_data):
        """Test successfully retrieving a single person"""
        # Create test person
        person = PersonInput.model_validate(test_person_data)
        person_id = await test_database.person.create(person)

        response = await client.get(f"/v2/persons/{person_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["name"]["en"] == "Test Author"
        assert data["name"]["bo"] == "རྩོམ་པ་པོ།"
        assert data["bdrc"] == "P123456"
        assert len(data["alt_names"]) == 1
    
    async def test_get_single_person_not_found_returns_error_message(
        self, client, test_database
    ):
        """404 for non-existent person should include a JSON error message."""
        response = await client.get("/v2/persons/nonexistent_id")

        assert response.status_code == 404
        data = response.json()
        assert isinstance(data, dict)
        assert "error" in data
        assert "not" in data["error"].lower()
        assert "found" in data["error"].lower()

    async def test_get_single_person_minimal_data(
        self, client, test_database, test_person_data_minimal
    ):
        """Test retrieving person with minimal data (only required fields)"""
        # Create minimal person
        person = PersonInput.model_validate(test_person_data_minimal)
        person_id = await test_database.person.create(person)

        response = await client.get(f"/v2/persons/{person_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["name"]["en"] == "Minimal Person"
        assert data.get("alt_names") is None or data["alt_names"] == []
        assert data.get("bdrc") is None

    async def test_get_single_person_with_wiki_field(self, client, test_database):
        """Test retrieving person returns wiki field"""
        person_data = {
            "name": {"en": "Wiki Test Person"},
            "wiki": "Q123456",
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.get(f"/v2/persons/{person_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["wiki"] == "Q123456"

    async def test_get_single_person_with_both_external_ids(self, client, test_database):
        """Test retrieving person returns both bdrc and wiki fields"""
        person_data = {
            "name": {"en": "Dual ID Test Person"},
            "bdrc": "P654321",
            "wiki": "Q654321",
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.get(f"/v2/persons/{person_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["bdrc"] == "P654321"
        assert data["wiki"] == "Q654321"

    async def test_get_single_person_empty_id(self, client, test_database):
        """Test retrieving person with empty ID"""
        response = await client.get("/v2/persons/")

        # This should hit the GET all persons endpoint, not single person
        assert response.status_code == 200
        data = _items(response.json())
        assert isinstance(data, list)


@pytest.mark.asyncio(loop_scope="session")
class TestPostPersonV2:
    """Tests for POST /v2/persons/ endpoint (create person)"""

    async def test_create_person_success_full_data(self, client, test_database):
        """Test successfully creating a person with full data and verify round-trip"""
        person_data = {
            "name": {"en": "New Person", "bo": "གང་ཟག་གསར་པ།"},
            "alt_names": [
                {"en": "Alternative Name", "bo": "གཞན་མིང་།"},
                {"en": "Another Alt Name"},
            ],
            "bdrc": "P999999",
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["id"] is not None

        get_response = await client.get(f"/v2/persons/{data['id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["id"] == data["id"]
        assert get_data["name"]["en"] == "New Person"
        assert get_data["name"]["bo"] == "གང་ཟག་གསར་པ།"
        assert get_data["bdrc"] == "P999999"
        assert get_data.get("wiki") is None
        assert len(get_data["alt_names"]) == 2
        alt_en = [a.get("en") for a in get_data["alt_names"] if "en" in a]
        assert "Alternative Name" in alt_en
        assert "Another Alt Name" in alt_en

    async def test_create_person_rejects_unknown_fields(self, client, test_database):
        """Payload with unknown fields should trigger a validation error (or be explicitly allowed)."""
        person_data = {
            "name": {"en": "Extra Field Person"},
            "unknown_field": "should_not_be_here",
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        # If PersonModelInput uses extra="forbid", this should be 422
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
    
    async def test_create_person_name_null_is_invalid(self, client, test_database):
        """Explicit null for name should be rejected as invalid."""
        person_data = {"name": None}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_name_values_must_be_strings(self, client, test_database):
        """Name localization values must be strings, not numbers or other types."""
        person_data = {"name": {"en": 12345}}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_alt_names_item_invalid(self, client, test_database):
        """Alt_names list containing a non-dict element should be rejected."""
        person_data = {
            "name": {"en": "Alt Names Item Invalid"},
            "alt_names": [
                "this is not a dict",
                {"en": "Valid Alt Name"},
            ],
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_bdrc_must_be_string(self, client, test_database):
        """Non-string BDRC identifiers should be rejected (no silent coercion)."""
        person_data = {
            "name": {"en": "BDRC Type Test"},
            "bdrc": 123456,  # wrong type
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_empty_json_object_is_invalid(self, client, test_database):
        """Empty JSON object with application/json should fail validation (missing name)."""
        response = await client.post(
            "/v2/persons/",
            json={},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_empty_body_with_json_content_type(self, client, test_database):
        response = await client.post(
            "/v2/persons/",
            content="",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_with_existing_bdrc_id_rejected(self, client, test_database):
        """Creating two persons with the same BDRC ID should fail if BDRC is unique."""
        person1 = {
            "name": {"en": "First Person"},
            "bdrc": "P111111",
        }
        person2 = {
            "name": {"en": "Second Person"},
            "bdrc": "P111111",  # same BDRC
        }

        # First one succeeds
        response1 = await client.post(
            "/v2/persons/",
            json=person1,
        )
        assert response1.status_code == 201

        # Second one should be rejected
        response2 = await client.post(
            "/v2/persons/",
            json=person2,
        )
        # Mirror /v2/texts duplicate behavior (likely 400)
        assert response2.status_code == 409
        data = response2.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()

    async def test_create_person_success_minimal_data(self, client, test_database):
        """Test creating person with minimal data and verify optional fields are absent"""
        person_data = {"name": {"en": "Minimal Person"}}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        get_response = await client.get(f"/v2/persons/{data['id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["id"] == data["id"]
        assert get_data["name"]["en"] == "Minimal Person"
        assert get_data.get("bdrc") is None
        assert get_data.get("wiki") is None
        assert get_data.get("alt_names") is None or get_data["alt_names"] == []

    async def test_create_person_tibetan_only_name(self, client, test_database):
        """Test creating person with Tibetan-only name"""
        person_data = {"name": {"bo": "བོད་པའི་མིང་།"}}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/persons/{data['id']}")).json()
        assert stored["name"] == person_data["name"]
        assert {tuple(sorted(name.items())) for name in stored.get("alt_names") or []} == {
            tuple(sorted(name.items())) for name in person_data.get("alt_names", [])
        }

    async def test_create_person_missing_json(self, client, test_database):
        """Test POST with no JSON data"""
        response = await client.post("/v2/persons/")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_person_invalid_json(self, client, test_database):
        """Test POST with invalid JSON"""
        response = await client.post(
            "/v2/persons/",
            content="invalid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422

    async def test_create_person_missing_required_fields(self, client, test_database):
        """Test POST with missing required fields"""
        # Missing name field
        person_data = {"bdrc": "P123456"}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422  # Pydantic validation error
        data = response.json()
        assert "detail" in data

    async def test_create_person_empty_name(self, client, test_database):
        """Test POST with empty name object"""
        person_data = {"name": {}}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422  # Should fail validation for empty name
        data = response.json()
        assert "detail" in data

    async def test_create_person_invalid_name_structure(self, client, test_database):
        """Test POST with invalid name structure"""
        person_data = {"name": "string instead of dict"}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 422  # Pydantic validation error
        data = response.json()
        assert "detail" in data

    async def test_create_person_with_bdrc_id(self, client, test_database):
        """Test creating person with BDRC identifier and verify roundtrip"""
        person_data = {"name": {"en": "BDRC Person"}, "bdrc": "P1234567890"}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        get_response = await client.get(f"/v2/persons/{data['id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "P1234567890"
        assert get_data["name"]["en"] == "BDRC Person"
        assert get_data.get("wiki") is None

    async def test_create_person_multilingual_names(self, client, test_database):
        """Test creating person with multiple language names and alt_names"""
        person_data = {
            "name": {
                "en": "Multilingual Person",
                "bo": "སྐད་རིགས་མང་པོའི་གང་ཟག",
                "zh": "多语言人物",
            },
            "alt_names": [
                {"en": "English Alt", "bo": "བོད་ཡིག་གཞན།"},
                {"zh": "中文别名"},
            ],
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/persons/{data['id']}")).json()
        assert stored["name"] == person_data["name"]
        assert {tuple(sorted(name.items())) for name in stored.get("alt_names") or []} == {
            tuple(sorted(name.items())) for name in person_data.get("alt_names", [])
        }

    async def test_create_person_with_wiki_id(self, client, test_database):
        """Test creating person with Wiki identifier and verify roundtrip"""
        person_data = {"name": {"en": "Wiki Person"}, "wiki": "Q12345"}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        get_response = await client.get(f"/v2/persons/{data['id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["wiki"] == "Q12345"
        assert get_data["name"]["en"] == "Wiki Person"
        assert get_data.get("bdrc") is None

    async def test_create_person_with_existing_wiki_id_rejected(self, client, test_database):
        """Creating two persons with the same Wiki ID should fail if Wiki is unique."""
        person1 = {"name": {"en": "First Wiki Person"}, "wiki": "Q999999"}
        person2 = {"name": {"en": "Second Wiki Person"}, "wiki": "Q999999"}

        response1 = await client.post(
            "/v2/persons/",
            json=person1,
        )
        assert response1.status_code == 201

        response2 = await client.post(
            "/v2/persons/",
            json=person2,
        )
        assert response2.status_code == 409
        data = response2.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()

    async def test_create_person_with_both_bdrc_and_wiki(self, client, test_database):
        """Test creating person with both BDRC and Wiki identifiers and verify roundtrip"""
        person_data = {
            "name": {"en": "Dual ID Person"},
            "bdrc": "P777777",
            "wiki": "Q777777",
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        get_response = await client.get(f"/v2/persons/{data['id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "P777777"
        assert get_data["wiki"] == "Q777777"
        assert get_data["name"]["en"] == "Dual ID Person"

    async def test_create_person_with_empty_alt_names_list(self, client, test_database):
        """Empty alt_names list should be valid"""
        person_data = {"name": {"en": "Empty Alt Names Person"}, "alt_names": []}

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/persons/{data['id']}")).json()
        assert stored["name"] == person_data["name"]
        assert {tuple(sorted(name.items())) for name in stored.get("alt_names") or []} == {
            tuple(sorted(name.items())) for name in person_data.get("alt_names", [])
        }

    async def test_create_person_alt_name_same_as_primary_deduped(self, client, test_database):
        """Alt name identical to primary name should be deduplicated"""
        person_data = {
            "name": {"en": "Primary Name"},
            "alt_names": [
                {"en": "Primary Name"},
                {"en": "Different Alt Name"},
            ],
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        create_data = response.json()
        person_id = create_data["id"]

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        alt_names_en = [alt.get("en") for alt in get_data.get("alt_names", []) if "en" in alt]
        assert "Primary Name" not in alt_names_en
        assert "Different Alt Name" in alt_names_en

    async def test_create_person_with_many_alt_names(self, client, test_database):
        """Test creating person with many alternative names"""
        person_data = {
            "name": {"en": "Many Alt Names Person"},
            "alt_names": [{"en": f"Alt Name {i}"} for i in range(10)],
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        create_data = response.json()
        person_id = create_data["id"]

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert len(get_data.get("alt_names", [])) == 10

        assert {tuple(sorted(name.items())) for name in get_data["alt_names"]} == {
            tuple(sorted(name.items())) for name in person_data["alt_names"]
        }

    async def test_create_person_name_with_special_characters(self, client, test_database):
        """Test creating person with special characters in name"""
        person_data = {
            "name": {"en": "Person with 'quotes' and \"double quotes\""},
            "alt_names": [{"en": "Name with\nnewline"}],
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/persons/{data['id']}")).json()
        assert stored["name"] == person_data["name"]
        assert {tuple(sorted(name.items())) for name in stored.get("alt_names") or []} == {
            tuple(sorted(name.items())) for name in person_data.get("alt_names", [])
        }

    async def test_create_person_with_bcp47_language_tag(self, client, test_database):
        """Test creating person with BCP47 language tags like en-US"""
        person_data = {
            "name": {"en-US": "American Person", "bo-Latn": "bod skad"},
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/persons/{data['id']}")).json()
        assert stored["name"] == person_data["name"]
        assert {tuple(sorted(name.items())) for name in stored.get("alt_names") or []} == {
            tuple(sorted(name.items())) for name in person_data.get("alt_names", [])
        }

    async def test_create_person_with_invalid_language_code(self, client, test_database):
        """Test creating person with invalid language code returns error"""
        person_data = {
            "name": {"xx": "Invalid Language Person"},
        }

        response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "not present" in data["error"].lower() or "language" in data["error"].lower()


@pytest.mark.asyncio(loop_scope="session")
class TestPersonsIntegration:
    """Integration tests combining multiple operations"""

    async def test_create_and_retrieve_person(self, client, test_database):
        """Test creating a person and then retrieving it"""
        # Create person via API
        person_data = {
            "name": {"en": "Integration Test Person", "bo": "འདུས་སྦྱོར་བརྟག་དཔྱད།"},
            "alt_names": [{"en": "Alt Integration Name"}],
            "bdrc": "P888888",
        }

        create_response = await client.post(
            "/v2/persons/",
            json=person_data,
        )

        assert create_response.status_code == 201
        create_data = create_response.json()
        person_id = create_data["id"]

        # Retrieve the created person
        get_response = await client.get(f"/v2/persons/{person_id}")

        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["id"] == person_id
        assert get_data["name"]["en"] == "Integration Test Person"
        assert get_data["name"]["bo"] == "འདུས་སྦྱོར་བརྟག་དཔྱད།"
        assert get_data["bdrc"] == "P888888"
        assert len(get_data["alt_names"]) == 1

    async def test_create_multiple_and_get_all(self, client, test_database):
        """Test creating multiple persons and retrieving all"""
        # Create multiple persons via API
        created_ids = []
        for i in range(3):
            person_data = {
                "name": {"en": f"Batch Person {i+1}"},
                "bdrc": f"P{i+1:06d}",
            }

            response = await client.post(
                "/v2/persons/",
                json=person_data,
            )

            assert response.status_code == 201
            data = response.json()
            created_ids.append(data["id"])

        # Get all persons
        get_all_response = await client.get("/v2/persons/")

        assert get_all_response.status_code == 200
        all_data = _items(get_all_response.json())
        assert len(all_data) == 3

        # Verify all created persons are returned
        returned_ids = [p["id"] for p in all_data]
        for created_id in created_ids:
            assert created_id in returned_ids


@pytest.mark.asyncio(loop_scope="session")
class TestPatchPersonV2:
    """Tests for PATCH /v2/persons/{id} endpoint (update person)"""

    async def test_patch_person_update_bdrc_only(self, client, test_database):
        """Test updating only the bdrc field"""
        person_data = {"name": {"en": "Original Person"}, "bdrc": "P111111"}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"bdrc": "P222222"}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["bdrc"] == "P222222"
        assert data["name"]["en"] == "Original Person"

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "P222222"
        assert get_data["name"]["en"] == "Original Person"

    async def test_patch_person_update_wiki_only(self, client, test_database):
        """Test updating only the wiki field"""
        person_data = {"name": {"en": "Wiki Person"}, "wiki": "Q111111"}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"wiki": "Q222222"}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["wiki"] == "Q222222"
        assert data["name"]["en"] == "Wiki Person"

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["wiki"] == "Q222222"
        assert get_data["name"]["en"] == "Wiki Person"

    async def test_patch_person_update_name_only(self, client, test_database):
        """Test updating only the name field"""
        person_data = {"name": {"en": "Original Name", "bo": "བོད་མིང་།"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"name": {"en": "Updated Name", "bo": "གསར་བསྒྱུར་མིང་།"}}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["name"]["en"] == "Updated Name"
        assert data["name"]["bo"] == "གསར་བསྒྱུར་མིང་།"

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Updated Name"
        assert get_data["name"]["bo"] == "གསར་བསྒྱུར་མིང་།"

    async def test_patch_person_update_alt_names_only(self, client, test_database):
        """Test updating only the alt_names field"""
        person_data = {
            "name": {"en": "Primary Name"},
            "alt_names": [{"en": "Old Alt Name"}],
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"alt_names": [{"en": "New Alt Name 1"}, {"en": "New Alt Name 2"}]}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == person_id
        assert data["name"]["en"] == "Primary Name"
        assert len(data["alt_names"]) == 2
        alt_names_en = [alt.get("en") for alt in data["alt_names"] if "en" in alt]
        assert "New Alt Name 1" in alt_names_en
        assert "New Alt Name 2" in alt_names_en

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Primary Name"
        assert len(get_data["alt_names"]) == 2
        get_alt_names_en = [alt.get("en") for alt in get_data["alt_names"] if "en" in alt]
        assert "New Alt Name 1" in get_alt_names_en
        assert "New Alt Name 2" in get_alt_names_en

    async def test_patch_person_update_multiple_fields(self, client, test_database):
        """Test updating multiple fields at once"""
        person_data = {
            "name": {"en": "Original"},
            "bdrc": "P333333",
            "wiki": "Q333333",
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {
            "name": {"en": "Updated"},
            "bdrc": "P444444",
            "wiki": "Q444444",
        }
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"]["en"] == "Updated"
        assert data["bdrc"] == "P444444"
        assert data["wiki"] == "Q444444"

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Updated"
        assert get_data["bdrc"] == "P444444"
        assert get_data["wiki"] == "Q444444"

    async def test_patch_person_not_found(self, client, test_database):
        """Test patching a non-existent person returns 404"""
        patch_data = {"bdrc": "P555555"}
        response = await client.patch(
            "/v2/persons/nonexistent_id",
            json=patch_data,
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_patch_person_empty_payload_rejected(self, client, test_database):
        """Test that empty payload is rejected"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.patch(
            f"/v2/persons/{person_id}",
            json={},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_patch_person_null_field_rejected(self, client, test_database):
        """Test that explicit null fields are rejected"""
        person_data = {"name": {"en": "Test Person"}, "bdrc": "P123456"}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.patch(
            f"/v2/persons/{person_id}",
            json={"bdrc": None},
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert "Null values are not supported in PATCH" in str(data["detail"])

    async def test_patch_person_unknown_field_rejected(self, client, test_database):
        """Test that unknown fields are rejected"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"unknown_field": "value"}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_patch_person_duplicate_bdrc_rejected(self, client, test_database):
        """Test that duplicate BDRC ID is rejected"""
        person1_data = {"name": {"en": "Person 1"}, "bdrc": "P666666"}
        person1 = PersonInput.model_validate(person1_data)
        await test_database.person.create(person1)

        person2_data = {"name": {"en": "Person 2"}, "bdrc": "P777777"}
        person2 = PersonInput.model_validate(person2_data)
        person2_id = await test_database.person.create(person2)

        patch_data = {"bdrc": "P666666"}
        response = await client.patch(
            f"/v2/persons/{person2_id}",
            json=patch_data,
        )

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()

        get_response = await client.get(f"/v2/persons/{person2_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "P777777"

    async def test_patch_person_duplicate_wiki_rejected(self, client, test_database):
        """Test that duplicate Wiki ID is rejected"""
        person1_data = {"name": {"en": "Person 1"}, "wiki": "Q888888"}
        person1 = PersonInput.model_validate(person1_data)
        await test_database.person.create(person1)

        person2_data = {"name": {"en": "Person 2"}, "wiki": "Q999999"}
        person2 = PersonInput.model_validate(person2_data)
        person2_id = await test_database.person.create(person2)

        patch_data = {"wiki": "Q888888"}
        response = await client.patch(
            f"/v2/persons/{person2_id}",
            json=patch_data,
        )

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()

        get_response = await client.get(f"/v2/persons/{person2_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["wiki"] == "Q999999"

    async def test_patch_person_invalid_name_structure(self, client, test_database):
        """Test that invalid name structure is rejected"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"name": "not a dict"}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Test Person"

    async def test_patch_person_empty_name_rejected(self, client, test_database):
        """Test that empty name dict is rejected"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"name": {}}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Test Person"

    async def test_patch_person_invalid_alt_names_structure(self, client, test_database):
        """Test that invalid alt_names structure is rejected"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"alt_names": "not a list"}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Test Person"

    async def test_patch_person_preserves_unpatched_fields(self, client, test_database):
        """Test that fields not in patch are preserved"""
        person_data = {
            "name": {"en": "Original Name", "bo": "བོད་མིང་།"},
            "alt_names": [{"en": "Alt Name"}],
            "bdrc": "P101010",
            "wiki": "Q101010",
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"bdrc": "P202020"}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["bdrc"] == "P202020"
        assert data["wiki"] == "Q101010"
        assert data["name"]["en"] == "Original Name"
        assert data["name"]["bo"] == "བོད་མིང་།"
        assert len(data["alt_names"]) == 1

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["bdrc"] == "P202020"
        assert get_data["wiki"] == "Q101010"
        assert get_data["name"]["en"] == "Original Name"
        assert get_data["name"]["bo"] == "བོད་མིང་།"
        assert len(get_data["alt_names"]) == 1

    async def test_patch_person_with_tibetan_name(self, client, test_database):
        """Test patching with Tibetan name"""
        person_data = {"name": {"en": "English Name"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"name": {"bo": "བོད་སྐད་མིང་།"}}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"]["bo"] == "བོད་སྐད་མིང་།"

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["bo"] == "བོད་སྐད་མིང་།"

    async def test_patch_person_with_bcp47_language_tag(self, client, test_database):
        """Test patching with BCP47 language tags"""
        person_data = {"name": {"en": "English Name"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"name": {"en-US": "American Name", "bo-Latn": "bod skad"}}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert "id" in data

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert "id" in get_data

        assert data["id"] == get_data["id"] == person_id
        assert data["name"] == get_data["name"] == patch_data["name"]

    async def test_patch_person_clear_alt_names(self, client, test_database):
        """Test clearing alt_names by providing empty list"""
        person_data = {
            "name": {"en": "Primary Name"},
            "alt_names": [{"en": "Alt Name 1"}, {"en": "Alt Name 2"}],
        }
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"alt_names": []}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["alt_names"] is None or data["alt_names"] == []

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["alt_names"] is None or get_data["alt_names"] == []

    async def test_patch_person_invalid_json(self, client, test_database):
        """Test that invalid JSON is rejected"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.patch(
            f"/v2/persons/{person_id}",
            content="invalid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422

    async def test_patch_person_no_content_type(self, client, test_database):
        """Test that missing content type is handled"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        response = await client.patch(f"/v2/persons/{person_id}")

        assert response.status_code == 422

    async def test_patch_person_alt_name_same_as_primary_deduped(self, client, test_database):
        """Test that alt_name identical to name is deduplicated"""
        person_data = {"name": {"en": "Primary Name"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {
            "name": {"en": "New Primary"},
            "alt_names": [{"en": "New Primary"}, {"en": "Different Alt"}],
        }
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 200
        data = response.json()
        alt_names_en = [alt.get("en") for alt in data.get("alt_names", []) if "en" in alt]
        assert "New Primary" not in alt_names_en
        assert "Different Alt" in alt_names_en

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "New Primary"
        get_alt_names_en = [alt.get("en") for alt in get_data.get("alt_names", []) if "en" in alt]
        assert "New Primary" not in get_alt_names_en
        assert "Different Alt" in get_alt_names_en

    async def test_patch_person_with_invalid_language_code(self, client, test_database):
        """Test patching with invalid language code returns error"""
        person_data = {"name": {"en": "Test Person"}}
        person = PersonInput.model_validate(person_data)
        person_id = await test_database.person.create(person)

        patch_data = {"name": {"xx": "Invalid Language"}}
        response = await client.patch(
            f"/v2/persons/{person_id}",
            json=patch_data,
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data

        get_response = await client.get(f"/v2/persons/{person_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["name"]["en"] == "Test Person"
