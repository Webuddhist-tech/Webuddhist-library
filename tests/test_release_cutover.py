from time import perf_counter

import pytest

from content_service import create_edition
from models.requests import EditionRequestModel
from models.text import TextInput
from scripts.reindex import rebuild

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_alias_cutover_and_rollback_retain_old_indexes(test_database, mock_storage, content_search, catalog_search):
    db, storage = test_database, mock_storage
    text = await db.text.create(TextInput(title={"en": "Rehearsal"}, language="en", category_id="category"))
    await create_edition(db, storage, text, EditionRequestModel(metadata={"type": "critical"}, content="rehearsal text", segmentation={"segments": [{"lines": [{"start": 0, "end": 14}]}]}))
    client = content_search.index._client
    catalog_alias, content_alias = catalog_search.index.index_name, content_search.index.index_name
    begin = perf_counter()
    first = await rebuild(db, storage, client, catalog_alias, content_alias)
    second = await rebuild(db, storage, client, catalog_alias, content_alias)
    assert second["previous"] == {alias: [index] for alias, index in first["current"].items()}
    for index in first["current"].values():
        assert await client.indices.exists(index=index)
    assert await content_search.search(db=db, query="rehearsal", search_type="exact", limit=10)
    # No writes occurred between builds, so the old snapshot remains a valid rollback target.
    actions = [{"remove": {"index": index, "alias": alias}} for alias, index in second["current"].items()]
    actions += [{"add": {"index": index, "alias": alias}} for alias, index in first["current"].items()]
    await client.indices.update_aliases(body={"actions": actions})
    assert await content_search.search(db=db, query="rehearsal", search_type="exact", limit=10)
    print(f"Two replacement builds, validation, atomic switches and rollback: {perf_counter()-begin:.3f}s (tiny local fixture)")
    # Fixtures delete the active aliases; remove only this test's retained inactive indexes.
    inactive = set(second["current"].values()) | {i for indexes in first["previous"].values() for i in indexes}
    for index in inactive:
        await client.indices.delete(index=index)
