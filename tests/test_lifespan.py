from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from botocore.exceptions import BotoCoreError
from opensearchpy.exceptions import OpenSearchException
import pytest

import main
from exceptions import InvalidRequestError


@pytest.mark.parametrize("failure", ["database", "storage", "content", "catalog", "request", "close", None])
async def test_lifespan_cleans_up_every_acquired_resource(monkeypatch, failure):
    events = []

    def step(name):
        events.append(name)
        if name == failure:
            raise RuntimeError(name)

    class Database:
        def __init__(self, **kwargs):
            pass

        async def verify_connectivity(self):
            step("database")

        async def close(self):
            step("close_database")

    class Storage:
        def __init__(self, **kwargs):
            pass

        async def connect(self):
            step("storage")

        async def close(self):
            step("close_storage")

    class SearchClient:
        async def close(self):
            step("close")

    shared_client = SearchClient()

    class Search:
        def __init__(self, *, client, index_name):
            assert client is shared_client
            self.index_name = index_name

        async def setup_index(self):
            step(self.index_name)

    monkeypatch.setattr(main, "Database", Database)
    monkeypatch.setattr(main, "Storage", Storage)
    monkeypatch.setattr(main, "ContentSearchService", Search)
    monkeypatch.setattr(main, "CatalogSearchService", Search)
    factory = Mock(return_value=shared_client)
    monkeypatch.setattr(main, "create_search_client", factory)
    monkeypatch.setattr(main, "setup_telemetry", lambda app: events.append("telemetry"))
    monkeypatch.setattr(main, "shutdown_telemetry", lambda: events.append("close_telemetry"))
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            opensearch_endpoint="http://localhost:9200",
            neo4j_uri="bolt://localhost",
            neo4j_username="test",
            neo4j_password="test",
            neo4j_database="test",
            aws_s3_bucket="test",
            aws_region="test",
            opensearch_auth_mode="none",
            opensearch_username="",
            opensearch_password="",
            opensearch_index="content",
            opensearch_catalog_index="catalog",
        ),
    )
    app = main.create_app()

    async def run():
        async with main.lifespan(app):
            step("request")

    if failure:
        with pytest.raises(RuntimeError, match=failure):
            await run()
    else:
        await run()
    assert events[-2:] == ["close_database", "close_telemetry"]
    if failure != "database":
        assert events[-3:] == ["close_storage", "close_database", "close_telemetry"]
    if failure not in {"database", "storage"}:
        assert events[-4:] == ["close", "close_storage", "close_database", "close_telemetry"]
        factory.assert_called_once()


@pytest.mark.parametrize("stage,error", [
    ("setup", OpenSearchException("offline")),
    ("setup", InvalidRequestError("migration required")),
    ("client", InvalidRequestError("invalid auth mode")),
    ("client", BotoCoreError()),
])
async def test_search_initialization_failure_does_not_block_canonical_startup(monkeypatch, stage, error):
    db = SimpleNamespace(verify_connectivity=AsyncMock(), close=AsyncMock())
    storage = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
    client = SimpleNamespace(close=AsyncMock())
    search = SimpleNamespace(setup_index=AsyncMock(side_effect=error))
    monkeypatch.setattr(main, "settings", main.settings.model_copy(update={"neo4j_uri": "bolt://test", "opensearch_endpoint": "http://test"}))
    monkeypatch.setattr(main, "Database", Mock(return_value=db))
    monkeypatch.setattr(main, "Storage", Mock(return_value=storage))
    monkeypatch.setattr(main, "create_search_client", Mock(return_value=client, side_effect=error if stage == "client" else None))
    monkeypatch.setattr(main, "ContentSearchService", Mock(return_value=search))
    monkeypatch.setattr(main, "CatalogSearchService", Mock(return_value=search))
    monkeypatch.setattr(main, "setup_telemetry", Mock())
    monkeypatch.setattr(main, "shutdown_telemetry", Mock())
    app = main.create_app()
    async with main.lifespan(app):
        assert app.state.db is db and app.state.storage is storage
        assert not hasattr(app.state, "content_search")
    if stage == "client":
        client.close.assert_not_awaited()
    else:
        client.close.assert_awaited_once()
    storage.close.assert_awaited_once()
    db.close.assert_awaited_once()
