# pylint: disable=redefined-outer-name
"""
Tests for API key authentication.

Tests cover:
- Master keys (no application binding) accept any X-Application header
- App-bound keys validate X-Application header matches
- App-bound keys reject mismatched X-Application header
- Missing API key returns 401
- Invalid API key returns 401
- API key CRUD operations (create, validate, revoke, rotate, list)

Requires environment variables:
- NEO4J_TEST_URI: Neo4j test instance URI
- NEO4J_TEST_PASSWORD: Password for test instance
"""


import pytest
from identifier import generate_id


@pytest.mark.asyncio(loop_scope="session")
class TestApiKeyDatabase:
    """Tests for ApiKeyDatabase CRUD operations."""

    async def test_create_master_key(self, test_database):
        """Test creating a master key (no application binding)."""
        key_id = generate_id()
        name = "Test Master Key"
        email = "test@example.com"

        created_id, raw_key = await test_database.api_key.create(key_id, name, email)

        assert created_id == key_id
        assert raw_key is not None
        assert len(raw_key) > 20

    async def test_create_app_bound_key(self, test_database):
        """Test creating a key bound to an application."""
        key_id = generate_id()
        name = "Test App Key"
        email = "app@example.com"
        application_id = "test_application"

        created_id, raw_key = await test_database.api_key.create(key_id, name, email, application_id)

        assert created_id == key_id
        assert raw_key is not None

    async def test_create_app_bound_key_nonexistent_app(self, test_database):
        """Test creating a key bound to nonexistent application fails."""
        key_id = generate_id()
        name = "Test Key"
        email = "test@example.com"

        with pytest.raises(ValueError, match="not found"):
            await test_database.api_key.create(key_id, name, email, "nonexistent_app")

    async def test_validate_master_key(self, test_database):
        """Test validating a master key returns id and no bound application."""
        key_id = generate_id()
        name = "Test Master Key"
        email = "test@example.com"
        created_id, raw_key = await test_database.api_key.create(key_id, name, email)

        result = await test_database.api_key.validate_key(raw_key)

        assert result is not None
        assert result["id"] == created_id
        assert result["bound_application_id"] is None

    async def test_validate_app_bound_key(self, test_database):
        """Test validating an app-bound key returns bound application id."""
        key_id = generate_id()
        name = "Test App Key"
        email = "app@example.com"
        application_id = "test_application"
        created_id, raw_key = await test_database.api_key.create(key_id, name, email, application_id)

        result = await test_database.api_key.validate_key(raw_key)

        assert result is not None
        assert result["id"] == created_id
        assert result["bound_application_id"] == application_id

    async def test_validate_invalid_key(self, test_database):
        """Test validating an invalid key returns None."""
        result = await test_database.api_key.validate_key("invalid_key_12345")

        assert result is None

    async def test_revoke_key(self, test_database):
        """Test revoking a key makes it invalid."""
        key_id = generate_id()
        name = "Test Key"
        email = "test@example.com"
        created_id, raw_key = await test_database.api_key.create(key_id, name, email)

        assert await test_database.api_key.validate_key(raw_key) is not None

        success = await test_database.api_key.revoke(created_id)
        assert success is True

        assert await test_database.api_key.validate_key(raw_key) is None

    async def test_revoke_nonexistent_key(self, test_database):
        """Test revoking a nonexistent key returns False."""
        success = await test_database.api_key.revoke("nonexistent_key_id")
        assert success is False

    async def test_rotate_key(self, test_database):
        """Test rotating a key generates new value and invalidates old."""
        key_id = generate_id()
        name = "Test Key"
        email = "test@example.com"
        created_id, old_key = await test_database.api_key.create(key_id, name, email)

        new_key = await test_database.api_key.rotate_key(created_id)

        assert new_key is not None
        assert new_key != old_key
        assert await test_database.api_key.validate_key(old_key) is None
        assert await test_database.api_key.validate_key(new_key) is not None

    async def test_rotate_nonexistent_key(self, test_database):
        """Test rotating a nonexistent key returns None."""
        result = await test_database.api_key.rotate_key("nonexistent_key_id")
        assert result is None

    async def test_list_all_keys(self, test_database):
        """Test listing all keys."""
        key_id1 = generate_id()
        key_id2 = generate_id()
        await test_database.api_key.create(key_id1, "Key 1", "key1@example.com")
        await test_database.api_key.create(key_id2, "Key 2", "key2@example.com", "test_application")

        keys = await test_database.api_key.list_all()

        assert len(keys) >= 2
        key_ids = [k["id"] for k in keys]
        assert key_id1 in key_ids
        assert key_id2 in key_ids

        bound_key = next(k for k in keys if k["id"] == key_id2)
        assert bound_key["bound_application_id"] == "test_application"


@pytest.mark.asyncio(loop_scope="session")
class TestApiKeyAuthMiddleware:
    """Tests for API key authentication middleware (integration tests).
    
    Note: These tests use auth_client fixture which has testing=False,
    so real API key validation is enforced.
    """

    async def test_missing_api_key_returns_401(self, auth_client, test_database):
        """Test request without API key returns 401."""
        response = await auth_client.get("/v2/categories/", headers={"X-Application": "test_application"})

        assert response.status_code == 401
        data = response.json()
        assert "X-API-Key" in data["error"]

    async def test_invalid_api_key_returns_401(self, auth_client, test_database):
        """Test request with invalid API key returns 401."""
        response = await auth_client.get(
            "/v2/categories/",
            headers={
                "X-API-Key": "invalid_key_12345",
                "X-Application": "test_application",
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert "Invalid API key" in data["error"]


@pytest.mark.asyncio(loop_scope="session")
class TestApiKeyAuthMiddlewareWithKeys:
    """Tests for API key authentication that require creating keys first."""

    async def test_master_key_accepts_any_application(self, auth_client, test_database):
        """Test master key works with any X-Application header."""
        key_id = generate_id()
        _, raw_key = await test_database.api_key.create(key_id, "Master Key", "master@example.com")

        await test_database.application.create("another_application", "Another application")
        for application in ("test_application", "another_application"):
            response = await auth_client.get(
                "/v2/categories/",
                headers={"X-API-Key": raw_key, "X-Application": application},
            )
            assert response.status_code == 200, response.text

    async def test_app_bound_key_accepts_matching_application(self, auth_client, test_database):
        """Test app-bound key works when X-Application matches."""
        key_id = generate_id()
        _, raw_key = await test_database.api_key.create(key_id, "App Key", "app@example.com", "test_application")

        response = await auth_client.get(
            "/v2/categories/",
            headers={
                "X-API-Key": raw_key,
                "X-Application": "test_application",
            },
        )

        assert response.status_code == 200

    async def test_app_bound_key_rejects_mismatched_application(self, auth_client, test_database):
        """Test app-bound key rejects requests with mismatched X-Application header."""
        key_id = generate_id()
        _, raw_key = await test_database.api_key.create(key_id, "App Key", "app@example.com", "test_application")

        response = await auth_client.get(
            "/v2/categories/",
            headers={
                "X-API-Key": raw_key,
                "X-Application": "different_application",
            },
        )

        assert response.status_code == 401
        assert response.json()["error"] == "API key not authorized for this application"


@pytest.mark.asyncio(loop_scope='session')
async def test_bound_key_requires_application_header(auth_client, test_database):
    _, key = await test_database.api_key.create(generate_id(), 'Bound', 'bound@example.com', 'test_application')
    response = await auth_client.get('/v2/texts', headers={'X-API-Key': key})
    assert response.status_code == 401
    assert response.json()['error'] == 'API key not authorized for this application'


@pytest.mark.asyncio(loop_scope='session')
async def test_unbound_key_can_administer_applications(auth_client, test_database):
    _, key = await test_database.api_key.create(generate_id(), 'Admin', 'admin@example.com')
    headers = {'X-API-Key': key}
    created = await auth_client.post('/v2/applications', headers=headers, json={'name': '  New App  '})
    assert created.status_code == 201, created.text
    assert created.json() == {'id': 'new app', 'name': 'new app'}
    assert await test_database.application.exists('new app')
    deleted = await auth_client.delete('/v2/applications/new%20app', headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert not await test_database.application.exists('new app')
