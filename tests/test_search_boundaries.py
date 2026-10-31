from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import content_search.service as service_module
from content_search import ContentSearchService
from content_search.service import _candidates
from main import create_app


def hit(text, index=0):
    return {
        "_score": 1,
        "_source": {
            "content": text,
            "edition_id": f"edition-{index}",
            "text_id": "text",
            "object_key": "key",
            "offset": 0,
        },
    }


@pytest.mark.parametrize("budget", ["documents", "occurrences"])
async def test_search_stops_at_documented_candidate_budget(monkeypatch, budget):
    search = ContentSearchService(client=SimpleNamespace(), index_name="test")
    seen, requested, closed = [], [], []

    async def pages(body):
        assert body["size"] == 25
        try:
            for page in range(41):
                requested.append(page)
                yield [hit("x" * 3500 if budget == "occurrences" else "x", page * 25 + i) for i in range(25)]
        finally:
            closed.append(True)

    async def reject_stale(db, candidates):
        seen.extend(candidates)
        return []

    monkeypatch.setattr(search.index, "pages", pages)
    monkeypatch.setattr(service_module, "_join_segments", reject_stale)
    assert await search.search(db=None, query="x", search_type="exact", limit=1) == []
    assert len(seen) == (1000 if budget == "documents" else 10000)
    assert len(requested) == (40 if budget == "documents" else 1)
    assert closed == [True]


@pytest.mark.parametrize("highlight", [None, [], ["not present in source"]])
def test_similar_search_does_not_invent_an_offset_without_a_locatable_highlight(highlight):
    candidate = hit("real source text")
    if highlight is not None:
        candidate["highlight"] = {"content.analyzed": highlight}
    assert list(_candidates(candidate, "source", "similar")) == []
    candidate["highlight"] = {"content.analyzed": ["source"]}
    result = list(_candidates(candidate, "source", "similar"))
    assert len(result) == 1
    assert result[0]["result"].context == "real source text"
    assert result[0]["result"].match_span is None


def test_long_literal_candidate_must_match_beyond_the_indexed_prefix():
    query = "q" * 80 + "right suffix"
    assert list(_candidates(hit("q" * 80 + "wrong suffix"), query, "exact")) == []
    assert [(c["start"], c["end"]) for c in _candidates(hit("prefix " + query), query, "exact")] == [
        (7, 7 + len(query))
    ]


def test_literal_offsets_count_codepoints_without_normalizing_text():
    content = "🙂e\u0301 x é 🙂e\u0301"
    assert [(c["start"], c["end"]) for c in _candidates(hit(content), "🙂e\u0301", "exact")] == [(0, 3), (8, 11)]
    assert [(c["start"], c["end"]) for c in _candidates(hit(content), "é", "exact")] == [(6, 7)]
    assert [(c["start"], c["end"]) for c in _candidates(hit("aaaa"), "aaa", "exact")] == [(0, 3), (1, 4)]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"query": ""},
        {"query": "x" * 10001},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 101},
        {"query": "x", "search_type": "invalid"},
    ],
)
async def test_content_search_rejects_invalid_requests_before_querying(params):
    app = create_app(testing=True)
    app.state.db = None
    app.state.content_search = SimpleNamespace(search=AsyncMock(return_value=[]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v2/content-search", params=params)
    assert response.status_code == 422, response.text
    app.state.content_search.search.assert_not_awaited()


@pytest.mark.parametrize("length,limit", [(1, 1), (10000, 100)])
async def test_content_search_accepts_boundary_values(length, limit):
    app = create_app(testing=True)
    app.state.db = object()
    app.state.content_search = SimpleNamespace(search=AsyncMock(return_value=[]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v2/content-search", params={"query": "x" * length, "limit": limit})
    assert response.status_code == 200, response.text
    app.state.content_search.search.assert_awaited_once_with(
        db=app.state.db, query="x" * length, search_type="exact", limit=limit, text_id=None, edition_id=None
    )


@pytest.mark.parametrize("query", ["", "x" * 10001])
async def test_service_rejects_query_outside_limits_before_search(query):
    from exceptions import DataValidationError

    search = ContentSearchService(client=SimpleNamespace(), index_name="test")
    with pytest.raises(DataValidationError, match="Search query must contain 1-10000"):
        await search.search(db=None, query=query, search_type="exact", limit=1)
