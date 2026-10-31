import pytest
from httpx import ASGITransport, AsyncClient

from catalog_search import CatalogSearchService
from content_search import ContentSearchService
from main import create_app
from search_client import create_search_client

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_search_outage_preserves_canonical_crud_and_reports_degradation(test_database, caplog):
    db = test_database
    app = create_app(testing=True)
    app.state.db = db
    transport = create_search_client(endpoint="http://127.0.0.1:1", region="", auth_mode="none", request_timeout=1, max_retries=0)
    app.state.catalog_search = CatalogSearchService(client=transport, index_name="catalog")
    app.state.content_search = ContentSearchService(client=transport, index_name="content")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/v2/texts", json={"title": {"en": "Search outage"}, "language": "en", "category_id": "category"})
            assert created.status_code == 201, created.text
            assert f"Search update failed for text {created.json()['id']}" in caplog.text
            assert (await client.get(f"/v2/texts/{created.json()['id']}")).status_code == 200
            assert (await client.get("/v2/texts")).status_code == 200
            assert (await client.get("/v2/texts", params={"title": "outage"})).status_code == 503
    finally:
        await transport.close()
