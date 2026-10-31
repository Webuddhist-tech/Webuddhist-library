"""Build replacement projections during a write pause and switch both aliases together."""

import argparse
import asyncio
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, LiteralString
from uuid import uuid4

from catalog_search import CatalogSearchService
from catalog_search.service import _index_body as catalog_mapping
from config import settings
from content_search import ContentSearchService
from content_search.service import _index_body as content_mapping
from database import Database
from search_client import create_search_client
from storage import Storage

if TYPE_CHECKING:
    from opensearchpy import AsyncOpenSearch

logger = logging.getLogger(__name__)


async def rebuild(
    db: Database, storage: Storage, client: AsyncOpenSearch, catalog_alias: str, content_alias: str
) -> dict:
    """Manually rebuild from canonical records with API/import writers stopped."""
    old = {}
    concrete = set()
    for alias in (catalog_alias, content_alias):
        if await client.indices.exists_alias(name=alias):
            old[alias] = list(await client.indices.get_alias(name=alias))
        elif await client.indices.exists(index=alias):
            concrete.add(alias)
            old[alias] = []
        else:
            old[alias] = []
    suffix = uuid4().hex
    names = {alias: f"{alias}-{suffix}" for alias in old}
    await client.indices.create(index=names[catalog_alias], body=catalog_mapping())
    await client.indices.create(index=names[content_alias], body=content_mapping())
    catalog = CatalogSearchService(client=client, index_name=names[catalog_alias])
    content = ContentSearchService(client=client, index_name=names[content_alias])
    counts = await _populate(db, storage, catalog, content, catalog_alias, content_alias)
    for alias, index in names.items():
        await client.indices.refresh(index=index)
        actual = (await client.count(index=index))["count"]
        if actual != counts[alias]:
            raise RuntimeError(f"Replacement {index} has {actual} documents; expected {counts[alias]}")
    actions = [{"remove": {"index": index, "alias": alias}} for alias, indexes in old.items() for index in indexes]
    # A concrete index occupies its name. Replace it with the same-named alias only
    # after both replacement indexes have been populated and checked.
    actions += [{"remove_index": {"index": index}} for index in sorted(concrete)]
    actions += [{"add": {"index": index, "alias": alias}} for alias, index in names.items()]
    if concrete:
        logger.warning("Replacing concrete indexes with aliases of the same names: %s", sorted(concrete))
    await client.indices.update_aliases(body={"actions": actions})
    return {"previous": old, "current": names, "documents": counts, "replaced_concrete": sorted(concrete)}


async def _populate(
    db: Database,
    storage: Storage,
    catalog: CatalogSearchService,
    content: ContentSearchService,
    catalog_alias: str,
    content_alias: str,
) -> dict[str, int]:
    counts = {catalog_alias: 0, content_alias: 0}
    labels: dict[str, LiteralString] = {"person": "Person", "text": "Text", "edition": "Edition"}
    for kind, label in labels.items():
        service = content if kind == "edition" else catalog
        alias = content_alias if kind == "edition" else catalog_alias
        after = ""
        while True:
            async with db.get_session() as session:
                result = await session.run(
                    f"MATCH (n:{label}) WHERE n.id > $after RETURN n.id AS id ORDER BY id LIMIT 100",
                    after=after,
                )
                ids = [record["id"] for record in await result.data()]
            if not ids:
                break
            documents = []
            for entity_id in ids:
                if kind == "edition":
                    counts[alias] += await content.index.bulk_index(
                        await content.prepare_documents(entity_id, db, storage), refresh=False
                    )
                else:
                    document = await catalog.prepare_document(kind, entity_id, db)
                    if not document.get("deleted"):
                        documents.append(document)
            counts[alias] += await service.index.bulk_index(documents, refresh=False)
            after = ids[-1]
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--writes-paused",
        action="store_true",
        required=True,
        help="Confirm API/import writers are stopped",
    )
    parser.parse_args()
    async with AsyncExitStack() as stack:
        db = await stack.enter_async_context(
            Database(settings.neo4j_uri, (settings.neo4j_username, settings.neo4j_password), settings.neo4j_database)
        )
        storage = Storage(settings.aws_s3_bucket, settings.aws_region)
        stack.push_async_callback(storage.close)
        await storage.connect()
        client = create_search_client(
            endpoint=settings.opensearch_endpoint,
            region=settings.aws_region,
            auth_mode=settings.opensearch_auth_mode,
            username=settings.opensearch_username,
            password=settings.opensearch_password,
        )
        stack.push_async_callback(client.close)
        logger.info(
            "Cutover: %s",
            await rebuild(
                db,
                storage,
                client,
                settings.opensearch_catalog_index,
                settings.opensearch_index,
            ),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
