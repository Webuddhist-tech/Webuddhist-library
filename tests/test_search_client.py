import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opensearchpy.serializer import JSONSerializer
from opensearchpy.exceptions import RequestError

import search_client
from catalog_search import CatalogSearchService
from content_search import ContentSearchService
from exceptions import InvalidRequestError
from search_client import SearchIndex


def transport_for_bulk(responder=None):
    requests = []

    async def bulk(*, body, **kwargs):
        lines = [json.loads(line) for line in body.splitlines()]
        actions = lines[::2]
        documents = lines[1::2]
        requests.append((actions, documents, kwargs | {"body_bytes": len(body.encode("utf-8"))}))
        items = []
        for action, document in zip(actions, documents, strict=True):
            operation, metadata = next(iter(action.items()))
            status = responder(metadata, len(requests)) if responder else 201
            result = {**metadata, "status": status}
            if status >= 400:
                result["error"] = {"type": "test_error", "reason": "rejected"}
            items.append({operation: result})
        return {"items": items, "errors": any(next(iter(item.values()))["status"] >= 400 for item in items)}

    client = SimpleNamespace(
        transport=SimpleNamespace(serializer=JSONSerializer()),
        bulk=bulk,
        indices=SimpleNamespace(refresh=AsyncMock()),
    )
    return client, requests


async def test_bulk_streams_byte_bounded_requests_and_refreshes_once(monkeypatch):
    monkeypatch.setattr(search_client, "BULK_CHUNK_BYTES", 250)
    client, requests = transport_for_bulk()
    index = SearchIndex(client, "test")
    count = await index.bulk_index({"id": str(i), "content": "x" * 60} for i in range(8))
    assert count == 8
    assert len(requests) > 1
    assert all(kwargs["body_bytes"] <= 250 for _, _, kwargs in requests)
    assert [document["id"] for _, documents, _ in requests for document in documents] == [str(i) for i in range(8)]
    assert all(kwargs["params"]["refresh"] == "false" for _, _, kwargs in requests)
    client.indices.refresh.assert_awaited_once_with(index="test")


async def test_bulk_retries_only_rejected_items_with_same_ids(monkeypatch):
    monkeypatch.setattr("opensearchpy._async.helpers.actions.asyncio.sleep", AsyncMock())
    client, requests = transport_for_bulk(
        lambda metadata, attempt: 429 if metadata["_id"] == "retry" and attempt == 1 else 201
    )
    index = SearchIndex(client, "test")
    assert await index.bulk_index(iter([{"id": "ok"}, {"id": "retry"}]), refresh=False) == 2
    assert [[action["index"]["_id"] for action in actions] for actions, _, _ in requests] == [
        ["ok", "retry"],
        ["retry"],
    ]
    client.indices.refresh.assert_not_awaited()


async def test_bulk_reports_document_failure_without_refresh():
    client, requests = transport_for_bulk(lambda metadata, attempt: 400 if metadata["_id"] == "bad" else 201)
    index = SearchIndex(client, "test")
    with pytest.raises(InvalidRequestError, match="failed to index 1 documents"):
        await index.bulk_index([{"id": "ok"}, {"id": "bad"}])
    assert len(requests) == 1
    client.indices.refresh.assert_not_awaited()


async def test_bulk_empty_generator_does_not_refresh():
    client, requests = transport_for_bulk()
    assert await SearchIndex(client, "test").bulk_index(iter(())) == 0
    assert requests == []
    client.indices.refresh.assert_not_awaited()


async def test_services_use_same_transport_with_distinct_indexes():
    client = SimpleNamespace(indices=SimpleNamespace(
        exists=AsyncMock(return_value=True),
        get_mapping=AsyncMock(side_effect=[
            {"index": {"mappings": {"_meta": {"projection_version": version}}}} for version in (2, 3)
        ]),
    ))
    catalog = CatalogSearchService(client=client, index_name="catalog-search")
    content = ContentSearchService(client=client, index_name="content-search")
    await catalog.setup_index()
    await content.setup_index()
    assert [call.kwargs["index"] for call in client.indices.get_mapping.await_args_list] == ["catalog-search", "content-search"]


async def test_bulk_exhausts_bounded_retries(monkeypatch):
    monkeypatch.setattr("opensearchpy._async.helpers.actions.asyncio.sleep", AsyncMock())
    client, requests = transport_for_bulk(lambda metadata, attempt: 429)
    with pytest.raises(InvalidRequestError, match="failed to index 1 documents"):
        await SearchIndex(client, "test").bulk_index([{"id": "throttled"}])
    assert len(requests) == 4
    client.indices.refresh.assert_not_awaited()


@pytest.mark.parametrize("ending", ["exhausted", "early", "error", "cancelled"])
async def test_scroll_context_is_released_for_every_exit(ending):
    import asyncio
    from contextlib import aclosing
    from opensearchpy.exceptions import OpenSearchException

    client = SimpleNamespace(
        search=AsyncMock(return_value={"_scroll_id": "first", "hits": {"hits": [{"_id": "a"}]}}),
        scroll=AsyncMock(return_value={"_scroll_id": "second", "hits": {"hits": []}}),
        clear_scroll=AsyncMock(),
    )
    if ending == "error":
        client.scroll.side_effect = OpenSearchException("offline")
    if ending == "cancelled":
        client.scroll.side_effect = asyncio.CancelledError()

    async def consume():
        async with aclosing(SearchIndex(client, "index").pages({"query": {"match_all": {}}})) as pages:
            assert await anext(pages) == [{"_id": "a"}]
            if ending == "early":
                return
            async for _ in pages:
                pytest.fail("Unexpected second page")

    if ending in ("error", "cancelled"):
        with pytest.raises(OpenSearchException if ending == "error" else asyncio.CancelledError):
            await consume()
    else:
        await consume()
    client.clear_scroll.assert_awaited_once_with(body={"scroll_id": ["second" if ending == "exhausted" else "first"]})
    if ending == "early":
        client.scroll.assert_not_awaited()


async def test_scroll_cleanup_failure_preserves_original_error(caplog):
    from opensearchpy.exceptions import OpenSearchException

    client = SimpleNamespace(
        search=AsyncMock(return_value={"_scroll_id": "one", "hits": {"hits": [{}]}}),
        scroll=AsyncMock(side_effect=ValueError("original")),
        clear_scroll=AsyncMock(side_effect=OpenSearchException("cleanup")),
    )
    with pytest.raises(ValueError, match="original"):
        async for _ in SearchIndex(client, "index").pages({}):
            pass
    assert "Unable to clear search scroll" in caplog.text


@pytest.mark.parametrize("mapping", [{}, {"projection_version": 1}])
async def test_existing_index_with_incompatible_projection_requires_rebuild_without_renaming(mapping):
    client = SimpleNamespace(
        indices=SimpleNamespace(
            exists=AsyncMock(return_value=True),
            get_mapping=AsyncMock(return_value={"old": {"mappings": {"_meta": mapping}}}),
            create=AsyncMock(),
            delete=AsyncMock(),
        )
    )
    with pytest.raises(InvalidRequestError, match="content-search; run scripts.reindex with the existing index names"):
        await SearchIndex(client, "content-search").setup_index({"mappings": {"_meta": {"projection_version": 3}}})
    client.indices.create.assert_not_awaited()
    client.indices.delete.assert_not_awaited()


@pytest.mark.parametrize("name,version", [("content-search", 3), ("catalog-search", 2)])
@pytest.mark.parametrize("concrete", [True, False])
async def test_existing_compatible_index_or_alias_is_reused_without_replacement(name, version, concrete):
    client = SimpleNamespace(
        indices=SimpleNamespace(
            exists=AsyncMock(return_value=True),
            get_mapping=AsyncMock(return_value={
                name if concrete else f"{name}-replacement": {"mappings": {"_meta": {"projection_version": version}}}
            }),
            create=AsyncMock(), delete=AsyncMock(), update_aliases=AsyncMock(),
        )
    )
    await SearchIndex(client, name).setup_index({"mappings": {"_meta": {"projection_version": version}}})
    client.indices.get_mapping.assert_awaited_once_with(index=name)
    client.indices.create.assert_not_awaited()
    client.indices.delete.assert_not_awaited()
    client.indices.update_aliases.assert_not_awaited()


async def test_first_start_creates_the_configured_search_name():
    body = {"mappings": {"_meta": {"projection_version": 3}}}
    client = SimpleNamespace(indices=SimpleNamespace(exists=AsyncMock(return_value=False), create=AsyncMock()))
    await SearchIndex(client, "content-search").setup_index(body)
    client.indices.create.assert_awaited_once_with(
        index="content-search-bootstrap-v3", body={**body, "aliases": {"content-search": {}}}
    )
    assert "aliases" not in body


@pytest.mark.parametrize("version", [1, 3])
async def test_concurrent_index_creation_checks_the_winning_index_mapping(version):
    client = SimpleNamespace(indices=SimpleNamespace(
        exists=AsyncMock(side_effect=[False, True, True]),
        create=AsyncMock(side_effect=RequestError(400, "resource_already_exists_exception")),
        get_mapping=AsyncMock(return_value={"existing": {"mappings": {"_meta": {"projection_version": version}}}}),
    ))
    index = SearchIndex(client, "content-search")
    body = {"mappings": {"_meta": {"projection_version": 3}}}
    if version == 3:
        await index.setup_index(body)
    else:
        with pytest.raises(InvalidRequestError, match="projection requires reindexing"):
            await index.setup_index(body)
    client.indices.get_mapping.assert_awaited_once_with(index="content-search")


@pytest.mark.parametrize("error", ["illegal_argument_exception", "resource_already_exists_exception"])
async def test_index_setup_does_not_hide_creation_failure_without_a_usable_index(error):
    failure = RequestError(400, error)
    client = SimpleNamespace(indices=SimpleNamespace(
        exists=AsyncMock(return_value=False), create=AsyncMock(side_effect=failure), get_mapping=AsyncMock()
    ))
    with pytest.raises(RequestError) as raised:
        await SearchIndex(client, "content-search").setup_index({"mappings": {"_meta": {"projection_version": 3}}})
    assert raised.value is failure
    client.indices.get_mapping.assert_not_awaited()


@pytest.mark.parametrize("mode", ["basic", "aws", "none"])
async def test_search_auth_configuration(monkeypatch, mode):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from botocore.credentials import Credentials

    credentials = Credentials("test-key", "test-secret", "test-token")
    monkeypatch.setattr(
        search_client.botocore.session, "get_session", lambda: SimpleNamespace(get_credentials=lambda: credentials)
    )
    received = []

    async def index_exists(request):
        received.append(request.headers)
        return web.Response()

    app = web.Application()
    app.router.add_head("/content-search", index_exists)
    async with TestServer(app) as server:
        async with search_client.create_search_client(
            endpoint=str(server.make_url("/")),
            region="ap-southeast-1",
            auth_mode=mode,
            username="reader",
            password="password",
            max_retries=0,
        ) as client:
            assert await client.indices.exists(index="content-search")

    headers = received[0]
    if mode == "aws":
        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=test-key/")
        assert "/ap-southeast-1/es/aws4_request" in headers["Authorization"]
        assert headers["X-Amz-Security-Token"] == "test-token"
    elif mode == "basic":
        from aiohttp import BasicAuth

        assert headers["Authorization"] == BasicAuth("reader", "password").encode()
    else:
        assert "Authorization" not in headers


@pytest.mark.parametrize(
    "endpoint,mode,match",
    [
        ("", "none", "ENDPOINT is required"),
        ("http://test", "unsupported", "Unsupported"),
        ("http://test", "aws", "AWS credentials"),
    ],
)
def test_search_auth_configuration_errors(monkeypatch, endpoint, mode, match):
    monkeypatch.setattr(
        search_client.botocore.session, "get_session", lambda: SimpleNamespace(get_credentials=lambda: None)
    )
    with pytest.raises(InvalidRequestError, match=match):
        search_client.create_search_client(endpoint=endpoint, region="test", auth_mode=mode)
