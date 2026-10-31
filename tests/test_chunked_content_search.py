from unittest.mock import AsyncMock

import pytest

from content_service import create_edition, edit_content
from content_search.service import CHUNK_CHARS, CHUNK_STEP, MAX_QUERY_CHARS
from models.content_operation import ContentOperation
from models.requests import EditionRequestModel
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def indexed(db, storage, search, content, spans=None):
    text = await db.text.create(TextInput(title={"en": "Chunked edition"}, language="en", category_id="category"))
    data = EditionRequestModel(metadata={"type": "critical"}, content=content, segmentation={"segments": [{"lines": [{"start": start, "end": end}]} for start, end in (spans or [(0, len(content))])]})
    edition = await create_edition(db, storage, text, data)
    await search.index_edition(edition, db, storage)
    await search.index.refresh_index()
    return edition


async def search_for(search, db, query, search_type="exact", limit=10):
    return await search.search(db=db, query=query, search_type=search_type, limit=limit)


async def test_literal_candidates_preserve_characters_offsets_and_long_matches(test_database, mock_storage, content_search):
    db, storage, search = test_database, mock_storage, content_search
    long = "boundary match " * 100
    content = "ABC abc བོད་ཡིག་ 中文 ?*\\literal " + long
    await indexed(db, storage, search, content)
    for query in ["BC", "bc", "བོད་", "文", "?*\\", long]:
        items = await search_for(search, db, query)
        assert items, query
        for item in items:
            assert content[item.match_span.start:item.match_span.end] == query
            assert item.context == content[item.context_span.start:item.context_span.end]
            assert item.segment_ids
    assert await search_for(search, db, "Abc") == []


async def test_exact_search_ranks_relevant_passages_before_earlier_offsets(test_database, mock_storage, content_search):
    content = ("needle " + "filler " * CHUNK_CHARS)[:CHUNK_CHARS] + " needle" * 20
    await indexed(test_database, mock_storage, content_search, content)
    items = await search_for(content_search, test_database, "needle", limit=3)
    assert len(items) == 3
    assert all(item.match_span.start >= CHUNK_CHARS for item in items)
    assert all(item.score > 0 for item in items)
    assert [item.score for item in items] == sorted((item.score for item in items), reverse=True)


async def test_stale_content_and_removed_segmentation_are_filtered(test_database, mock_storage, content_search):
    db, storage, search = test_database, mock_storage, content_search
    edition = await indexed(db, storage, search, "original content")
    await edit_content(db, storage, edition, ContentOperation.model_validate({"type": "replace", "start": 0, "end": 8, "text": "updated"}))
    assert await search_for(search, db, "original") == []
    await search.index_edition(edition, db, storage)
    await search.index.refresh_index()
    assert await search_for(search, db, "updated")
    await db.annotation.segmentation.delete_by_edition(edition)
    assert await search_for(search, db, "updated") == []


async def test_search_continues_after_stale_candidates_and_outside_segments(test_database, mock_storage, content_search):
    db, storage, search = test_database, mock_storage, content_search
    edition = await indexed(db, storage, search, "needle outside; needle inside", [(16, 29)])
    for i in range(30):
        await search.index.publish({"id": f"000{i:03}:0", "edition_id": f"000{i:03}", "text_id": "gone", "object_key": "gone", "offset": 0, "content": "needle"})
    await search.index.refresh_index()
    items = await search_for(search, db, "needle", limit=1)
    assert len(items) == 1 and items[0].edition_id == edition
    assert items[0].match_span.start == 16


async def test_large_document_similar_highlight_reaches_the_end(test_database, mock_storage, content_search):
    db, storage, search = test_database, mock_storage, content_search
    content = "filler Tibetan བོད་ 中文. " * 50000 + "unique final passage about clouds"
    await indexed(db, storage, search, content)
    items = await search_for(search, db, "unique final passage about clouds", "similar")
    assert len(items) == 1
    assert items[0].context_span.start > 1_000_000
    assert "unique final passage" in items[0].context


@pytest.mark.parametrize("start", [CHUNK_STEP - 1, CHUNK_STEP, CHUNK_CHARS - 1])
@pytest.mark.parametrize("length", [100, 501, MAX_QUERY_CHARS])
async def test_exact_boundary_matches_are_complete_and_unique(
    test_database, mock_storage, content_search, start, length
):
    query = "Q" * length
    content = "x" * start + query + "z" * CHUNK_CHARS
    await indexed(test_database, mock_storage, content_search, content)
    items = await search_for(content_search, test_database, query)
    assert len(items) == 1
    assert (items[0].match_span.start, items[0].match_span.end) == (start, start + length)
    assert items[0].context == content[items[0].context_span.start:items[0].context_span.end]


async def test_short_queries_do_not_fetch_adjacent_chunks(test_database, mock_storage, content_search, monkeypatch):
    await indexed(test_database, mock_storage, content_search, "x" * CHUNK_STEP + "needle")
    neighbors = AsyncMock(side_effect=AssertionError("Short searches should not fetch adjacent chunks"))
    monkeypatch.setattr(content_search.index, "get_documents", neighbors)
    items = await search_for(content_search, test_database, "needle")
    assert len(items) == 1
    assert items[0].match_span.start == CHUNK_STEP
    neighbors.assert_not_awaited()


async def test_long_queries_do_not_mix_content_versions(test_database, mock_storage, content_search, monkeypatch):
    query = "Q" * CHUNK_CHARS
    await indexed(test_database, mock_storage, content_search, "x" * (CHUNK_STEP - 1) + query)
    get_documents = content_search.index.get_documents

    async def stale_neighbors(keys):
        documents = await get_documents(keys)
        for document in documents.values():
            document["object_key"] = "an-old-content-object"
        return documents

    monkeypatch.setattr(content_search.index, "get_documents", stale_neighbors)
    assert await search_for(content_search, test_database, query) == []


async def test_similar_search_returns_separate_passages_without_overlap_duplicates(
    test_database, mock_storage, content_search
):
    phrase = "unique passage about clouds"
    content = "x " * (CHUNK_STEP // 2) + phrase + " y" * CHUNK_CHARS + " " + phrase
    await indexed(test_database, mock_storage, content_search, content)
    items = await search_for(content_search, test_database, phrase, "similar")
    assert len(items) == 2
    spans = sorted((item.context_span.start, item.context_span.end) for item in items)
    assert spans[0][1] <= spans[1][0]


async def test_reindex_removes_chunks_left_by_a_shortened_edition(test_database, mock_storage, content_search):
    db, storage, search = test_database, mock_storage, content_search
    content = "long content " * CHUNK_CHARS
    edition = await indexed(db, storage, search, content)
    await edit_content(db, storage, edition, ContentOperation.model_validate({
        "type": "delete", "start": 100, "end": len(content),
    }))
    await search.index_edition(edition, db, storage)
    await search.index.refresh_index()
    response = await search.index.search({"query": {"term": {"edition_id": edition}}, "size": 100})
    assert [hit["_source"]["offset"] for hit in response["hits"]["hits"]] == [0]
