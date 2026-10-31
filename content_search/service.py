from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import aclosing
from itertools import batched
from typing import TYPE_CHECKING

from database.content_state import read_at_revision
from exceptions import DataNotFoundError, DataValidationError
from models.content_search import ContentSearchResult, ContentSearchSpan
from search_client import SearchIndex

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction
    from opensearchpy import AsyncOpenSearch

    from database import Database
    from storage import Storage

CONTEXT_CHARS = 200
CHUNK_CHARS = 4000
CHUNK_OVERLAP = 500
CHUNK_STEP = CHUNK_CHARS - CHUNK_OVERLAP
# Longer repeated literals can exceed OpenSearch's wildcard automaton limit.
LITERAL_PREFIX_CHARS = 64
MAX_QUERY_CHARS = 10_000
CANDIDATE_BATCH = 25
MAX_DOCUMENTS = 1000
MAX_OCCURRENCES = 10000


class ContentSearchService:
    def __init__(self, *, client: AsyncOpenSearch, index_name: str) -> None:
        self.index = SearchIndex(client, index_name)

    async def setup_index(self) -> None:
        await self.index.setup_index(_index_body())

    async def prepare_documents(self, edition_id: str, db: Database, storage: Storage) -> list[dict]:
        async def eligibility(tx: AsyncManagedTransaction) -> bool:
            result = await tx.run(
                """
                RETURN EXISTS { (:Edition {id: $id})-[:HAS_SEGMENTATION]->(:Segmentation)
                                <-[:SEGMENT_OF]-(:Segment)<-[:SPAN_OF]-(:Span) } AS eligible
            """,
                id=edition_id,
            )
            return (await result.single(strict=True))["eligible"]

        try:
            async with db.get_session() as session:
                state, eligible = await session.execute_read(read_at_revision, edition_id, eligibility)
            if not eligible:
                return []
        except DataNotFoundError:
            return []
        content = await storage.read_text(state.object_key)
        if len(content) != state.length:
            raise DataValidationError(f"Stored content length differs for edition {edition_id}")
        return [
            {
                "id": f"{edition_id}:{offset}",
                "edition_id": edition_id,
                "text_id": state.text_id,
                "object_key": state.object_key,
                "offset": offset,
                "content": content[offset : offset + CHUNK_CHARS],
            }
            for offset in range(0, len(content), CHUNK_STEP)
        ]

    async def index_edition(self, edition_id: str, db: Database, storage: Storage) -> None:
        documents = await self.prepare_documents(edition_id, db, storage)
        await self.index.delete_by_query({"term": {"edition_id": edition_id}})
        await self.index.bulk_index(documents, refresh=False)

    async def _extend_hits(self, hits: list[dict], query_length: int) -> None:
        """Fetch only the following chunks needed to verify a long literal match."""
        extra = (query_length - CHUNK_OVERLAP + CHUNK_STEP - 1) // CHUNK_STEP
        if extra <= 0:
            return
        neighbors = {
            (hit["_index"], f"{hit['_source']['edition_id']}:{hit['_source']['offset'] + step * CHUNK_STEP}")
            for hit in hits
            for step in range(1, extra + 1)
        }
        documents = await self.index.get_documents(neighbors)
        for hit in hits:
            source = hit["_source"]
            for step in range(1, extra + 1):
                key = hit["_index"], f"{source['edition_id']}:{source['offset'] + step * CHUNK_STEP}"
                part = documents.get(key)
                if part is None or part["object_key"] != source["object_key"]:
                    break
                source["content"] += part["content"][CHUNK_OVERLAP:]

    async def search(
        self,
        *,
        db: Database,
        query: str,
        search_type: str,
        limit: int,
        text_id: str | None = None,
        edition_id: str | None = None,
    ) -> list[ContentSearchResult]:
        results: list[ContentSearchResult] = []
        if not 1 <= len(query) <= MAX_QUERY_CHARS:
            raise DataValidationError(f"Search query must contain 1-{MAX_QUERY_CHARS} characters")
        documents = occurrences = 0
        body = _search_body(query, search_type, text_id, edition_id)
        async with aclosing(self.index.pages(body)) as pages:
            async for hits in pages:
                documents += len(hits)
                if search_type == "exact":
                    await self._extend_hits(hits, len(query))
                for batch in batched(
                    (c for hit in hits for c in _candidates(hit, query, search_type)), 100, strict=False
                ):
                    candidates = batch[: MAX_OCCURRENCES - occurrences]
                    occurrences += len(candidates)
                    for item in await _join_segments(db, candidates):
                        if search_type == "similar" and any(
                            previous.edition_id == item.edition_id
                            and previous.context_span.start < item.context_span.end
                            and item.context_span.start < previous.context_span.end
                            for previous in results
                        ):
                            continue
                        results.append(item)
                        if len(results) == limit:
                            return results
                    if occurrences >= MAX_OCCURRENCES:
                        return results
                if len(hits) < CANDIDATE_BATCH or documents >= MAX_DOCUMENTS:
                    return results
        return results


def _index_body() -> dict:
    return {
        "settings": {
            "number_of_shards": 1,
            "analysis": {
                "analyzer": {
                    "content_search_default": {"type": "custom", "tokenizer": "icu_tokenizer", "filter": ["lowercase"]},
                }
            },
        },
        "mappings": {
            "_meta": {"projection_version": 3},
            "dynamic": "strict",
            "properties": {
                "id": {"type": "keyword"},
                "edition_id": {"type": "keyword"},
                "text_id": {"type": "keyword"},
                "object_key": {"type": "keyword"},
                "offset": {"type": "long"},
                "content": {
                    "type": "wildcard",
                    "fields": {
                        "analyzed": {"type": "text", "analyzer": "content_search_default", "index_options": "offsets"},
                    },
                },
            },
        },
    }


def _search_body(query: str, search_type: str, text_id: str | None, edition_id: str | None) -> dict:
    filters: list[dict] = []
    if text_id is not None:
        filters.append({"term": {"text_id": text_id}})
    if edition_id is not None:
        filters.append({"term": {"edition_id": edition_id}})
    clauses: dict = {"filter": filters}
    if search_type == "exact":
        literal = (
            query[: min(CHUNK_OVERLAP, LITERAL_PREFIX_CHARS)]
            .replace("\\", "\\\\")
            .replace("*", "\\*")
            .replace("?", "\\?")
        )
        filters.append({"wildcard": {"content": {"value": f"*{literal}*"}}})
        clauses["should"] = [{"match_phrase": {"content.analyzed": {"query": query}}}]
        clauses["minimum_should_match"] = 0  # Scoring must not exclude literal-only matches.
    else:
        clauses["must"] = [{"match_phrase": {"content.analyzed": {"query": query, "slop": 12}}}]
    body: dict = {
        "size": CANDIDATE_BATCH,
        "query": {"bool": clauses},
        "sort": [{"_score": "desc"}, {"edition_id": "asc"}, {"offset": "asc"}],
    }
    if search_type != "exact":
        body["highlight"] = {
            "pre_tags": [""],
            "post_tags": [""],
            "fields": {
                "content.analyzed": {"number_of_fragments": 1, "fragment_size": CONTEXT_CHARS, "order": "score"},
            },
        }
    return body


def _candidates(hit: dict, query: str, search_type: str) -> Iterator[dict]:
    source = hit["_source"]
    content = source["content"]
    if search_type == "exact":
        start = content.find(query)
        # Each start belongs to one chunk, so overlapping chunks cannot duplicate an exact hit.
        while 0 <= start < CHUNK_STEP:
            end = start + len(query)
            padding = max(0, CONTEXT_CHARS - len(query))
            left, right = max(0, start - padding // 2), min(len(content), end + padding - padding // 2)
            yield _candidate(hit, left, right, start, end)
            start = content.find(query, start + 1)
    else:
        for fragment in hit.get("highlight", {}).get("content.analyzed", []):
            start = content.find(fragment)
            if start >= 0:
                padding = max(0, CONTEXT_CHARS - len(fragment))
                left = max(0, start - padding // 2)
                right = min(len(content), start + len(fragment) + padding - padding // 2)
                yield _candidate(hit, left, right, None, None)
                return
        # An absent/unlocatable highlight is not evidence of a match at offset zero.


def _candidate(hit: dict, left: int, right: int, start: int | None, end: int | None) -> dict:
    source = hit["_source"]
    offset = source["offset"]
    return {
        "key": source["object_key"],
        "start": offset + (start if start is not None else left),
        "end": offset + (end if end is not None else right),
        "result": ContentSearchResult(
            text_id=source["text_id"],
            edition_id=source["edition_id"],
            score=float(hit.get("_score") or 0),
            context=source["content"][left:right],
            context_span=ContentSearchSpan(start=offset + left, end=offset + right),
            match_span=ContentSearchSpan(start=offset + start, end=offset + end)
            if start is not None and end is not None
            else None,
        ),
    }


async def _join_segments(db: Database, candidates: Sequence[dict]) -> list[ContentSearchResult]:
    if not candidates:
        return []
    rows = [
        {"i": i, "edition": c["result"].edition_id, "key": c["key"], "start": c["start"], "end": c["end"]}
        for i, c in enumerate(candidates)
    ]

    async def read(tx: AsyncManagedTransaction) -> list[ContentSearchResult]:
        result = await tx.run(
            """
            UNWIND $rows AS row
            MATCH (e:Edition {id: row.edition, content_key: row.key})
            WITH collect({i: row.i, edition: e.id, revision: e.revision, start: row.start, end: row.end}) AS snapshots
            UNWIND snapshots AS snapshot
            MATCH (:Edition {id: snapshot.edition})-[:HAS_SEGMENTATION]->(:Segmentation)
                  <-[:SEGMENT_OF]-(s:Segment)<-[:SPAN_OF]-(span:Span)
            WHERE (span.start < snapshot.end AND span.end > snapshot.start) OR
                  (span.start = span.end AND span.start >= snapshot.start AND span.start < snapshot.end)
            WITH snapshot, s.id AS id, min(span.start) AS start ORDER BY snapshot.i, start, id
            RETURN snapshot.i AS i, snapshot.edition AS edition, snapshot.revision AS revision, collect(id) AS segments
            ORDER BY i
        """,
            rows=rows,
        )
        matches = await result.data()
        if not matches:
            return []
        result = await tx.run(
            "MATCH (e:Edition) WHERE e.id IN $ids RETURN e.id AS id, e.revision AS revision",
            ids=list({match["edition"] for match in matches}),
        )
        revisions = {r["id"]: r["revision"] for r in await result.data()}
        return [
            candidates[match["i"]]["result"].model_copy(update={"segment_ids": match["segments"]})
            for match in matches
            if revisions.get(match["edition"]) == match["revision"]
        ]

    async with db.get_session() as session:
        return await session.execute_read(read)
