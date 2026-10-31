# pylint: disable=redefined-outer-name
"""Tests for POST /v2/applications endpoint."""
import pytest

@pytest.mark.asyncio(loop_scope="session")
class TestApplications:
    """Tests for applications API."""

    async def test_create_application(self, client, test_database):
        """Test POST creates application and returns 201."""
        payload = {"name": "MyApp"}
        response = await client.post("/v2/applications", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "myapp"
        assert data["name"] == "myapp"
        assert await test_database.application.exists("myapp")

    async def test_create_application_rejects_duplicate(self, client, test_database):
        """Test POST same name returns 409."""
        payload = {"name": "test_application"}
        response = await client.post("/v2/applications", json=payload)
        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()

    async def test_create_application_normalizes_to_lowercase(self, client, test_database):
        """Test input WeBuddhist is stored as webuddhist in both id and name."""
        payload = {"name": "  WeBuddhist  "}
        response = await client.post("/v2/applications", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "webuddhist"
        assert data["name"] == "webuddhist"

    async def test_create_application_empty_name_rejected(self, client, test_database):
        """Test POST with empty name string returns 422."""
        response = await client.post("/v2/applications", json={"name": ""})
        assert response.status_code == 422

    async def test_create_application_missing_name_rejected(self, client, test_database):
        """Test POST with missing name field returns 422."""
        response = await client.post("/v2/applications", json={})
        assert response.status_code == 422

    async def test_create_application_whitespace_only_rejected(self, client, test_database):
        """Test POST with whitespace-only name returns 422."""
        response = await client.post("/v2/applications", json={"name": "   "})
        assert response.status_code == 422

    async def test_create_application_malformed_json(self, client, test_database):
        """Test POST with malformed JSON returns 422."""
        response = await client.post(
            "/v2/applications",
            content="{bad json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
