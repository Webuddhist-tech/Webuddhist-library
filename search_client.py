import logging
from collections.abc import AsyncGenerator, Iterable

import botocore.session
from opensearchpy import AsyncHttpConnection, AsyncOpenSearch, AWSV4SignerAsyncAuth
from opensearchpy.exceptions import OpenSearchException, RequestError
from opensearchpy.helpers import async_bulk

from exceptions import InvalidRequestError

BULK_CHUNK_BYTES = 5 * 1024 * 1024
logger = logging.getLogger(__name__)


def create_search_client(
    *,
    endpoint: str,
    region: str,
    auth_mode: str = "basic",
    username: str = "",
    password: str = "",
    request_timeout: int = 120,
    max_retries: int = 3,
) -> AsyncOpenSearch:
    """Create a transport owned and closed by the calling lifespan/context."""
    if not endpoint:
        raise InvalidRequestError("OPENSEARCH_ENDPOINT is required")
    http_auth: tuple[str, str] | AWSV4SignerAsyncAuth | None = None
    if auth_mode == "basic":
        http_auth = (username, password)
    elif auth_mode == "aws":
        credentials = botocore.session.get_session().get_credentials()
        if credentials is None:
            raise InvalidRequestError("AWS credentials are required for OpenSearch SigV4 auth")
        http_auth = AWSV4SignerAsyncAuth(credentials, region, "es")
    elif auth_mode not in {"none", ""}:
        raise InvalidRequestError(f"Unsupported OpenSearch auth mode: {auth_mode}")

    return AsyncOpenSearch(
        hosts=[endpoint.rstrip("/")],
        use_ssl=endpoint.startswith("https://"),
        verify_certs=True,
        connection_class=AsyncHttpConnection,
        http_auth=http_auth,
        timeout=request_timeout,
        max_retries=max_retries,
        retry_on_timeout=True,
    )


class SearchIndex:
    """Index operations over a borrowed transport; this adapter never closes it."""

    def __init__(self, client: AsyncOpenSearch, index_name: str) -> None:
        self._client = client
        self.index_name = index_name

    async def index_exists(self) -> bool:
        return bool(await self._client.indices.exists(index=self.index_name))

    async def setup_index(self, body: dict) -> None:
        version = body["mappings"]["_meta"]["projection_version"]
        if await self.index_exists():
            mappings = await self._client.indices.get_mapping(index=self.index_name)
            if any(
                item["mappings"].get("_meta", {}).get("projection_version") != version for item in mappings.values()
            ):
                raise InvalidRequestError(
                    f"Search projection requires reindexing before use: {self.index_name}; "
                    "run scripts.reindex with the existing index names"
                )
            return
        body = body | {"aliases": {self.index_name: {}}}
        try:
            await self._client.indices.create(index=f"{self.index_name}-bootstrap-v{version}", body=body)
        except RequestError as exc:
            if exc.error != "resource_already_exists_exception" or not await self.index_exists():
                raise
            await self.setup_index(body)

    async def delete_index(self) -> None:
        if await self._client.indices.exists_alias(name=self.index_name):
            indexes = list(await self._client.indices.get_alias(name=self.index_name))
            await self._client.indices.delete(index=",".join(indexes))
        elif await self.index_exists():
            await self._client.indices.delete(index=self.index_name)

    async def publish(self, document: dict) -> None:
        if document.get("deleted"):
            await self._client.delete(index=self.index_name, id=document["id"], params={"ignore": [404]})
        else:
            await self._client.index(index=self.index_name, id=document["id"], body=document)

    async def bulk_index(self, documents: Iterable[dict], *, refresh: bool = True) -> int:
        # Stable IDs and index (replace) operations make retrying rejected items idempotent.
        actions = (
            {"_op_type": "index", "_index": self.index_name, "_id": document["id"], "_source": document}
            for document in documents
        )
        indexed, failed = await async_bulk(
            self._client,
            actions,
            max_chunk_bytes=BULK_CHUNK_BYTES,
            max_retries=3,
            raise_on_error=False,
            stats_only=True,
            params={"refresh": "false"},
        )
        if failed:
            raise InvalidRequestError(f"OpenSearch failed to index {failed} documents in {self.index_name}")
        if indexed and refresh:
            await self.refresh_index()
        return indexed

    async def refresh_index(self) -> None:
        await self._client.indices.refresh(index=self.index_name)

    async def delete_by_query(self, query: dict) -> None:
        await self._client.delete_by_query(
            index=self.index_name, body={"query": query}, params={"conflicts": "proceed", "refresh": "true"}
        )

    async def get_documents(self, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        response = await self._client.mget(body={"docs": [{"_index": index, "_id": doc_id} for index, doc_id in keys]})
        return {(doc["_index"], doc["_id"]): doc["_source"] for doc in response["docs"] if doc.get("found")}

    async def search(self, body: dict) -> dict:
        return await self._client.search(index=self.index_name, body=body)

    async def pages(self, body: dict) -> AsyncGenerator[list[dict]]:
        """A bounded caller consumes a stable search snapshot, then releases it."""
        scroll_id = None
        try:
            response = await self._client.search(index=self.index_name, body=body, params={"scroll": "1m"})
            while True:
                scroll_id = response.get("_scroll_id", scroll_id)
                hits = response.get("hits", {}).get("hits", [])
                if not hits:
                    return
                yield hits
                response = await self._client.scroll(body={"scroll_id": scroll_id, "scroll": "1m"})
        finally:
            if scroll_id is not None:
                try:
                    await self._client.clear_scroll(body={"scroll_id": [scroll_id]})
                except OpenSearchException:
                    logger.warning("Unable to clear search scroll; it will expire after one minute")

    async def analyze(self, body: dict) -> dict:
        return await self._client.indices.analyze(index=self.index_name, body=body)
