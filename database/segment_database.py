from typing import TYPE_CHECKING, LiteralString

from exceptions import DataNotFoundError
from models.annotation import (
    SegmentWithContextOutput,
)
from models.requests import RelatedSegmentsFilter

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from .database import Database


class SegmentDatabase:
    GET_QUERY: LiteralString = """
    MATCH (seg:Segment {id: $segment_id})-[:SEGMENT_OF]->(segmentation:Segmentation)
        <-[:HAS_SEGMENTATION]-(edition:Edition)-[:EDITION_OF]->(text:Text)
    MATCH (span:Span)-[:SPAN_OF]->(seg)
    WHERE span.start < span.end
    WITH seg, segmentation, edition, text, span ORDER BY span.start
    WITH seg, segmentation, edition, text,
        collect({start: span.start, end: span.end}) AS lines,
        [(seg)-[:HAS_TAG]->(t:Tag)
            WHERE ($application IS NULL
                OR (t)-[:BELONGS_TO]->(:Application {id: $application}))
            | t.id] AS tag_ids
    RETURN seg.id AS id, segmentation.id AS segmentation_id,
        edition.id AS edition_id, text.id AS text_id,
        seg.type AS type,
        seg.reference AS reference,
        lines,
        CASE WHEN size(tag_ids) = 0 THEN null ELSE tag_ids END AS tag_ids
    """

    FIND_START_SEGMENTS_QUERY: LiteralString = """
    MATCH (:Edition {id: $edition_id})
        -[:HAS_SEGMENTATION]->(:Segmentation)
        <-[:SEGMENT_OF]-(seg:Segment)
        <-[:SPAN_OF]-(span:Span)
    WHERE span.start < span.end
      AND ANY(sp IN $spans WHERE span.start < sp[1] AND span.end > sp[0])
    RETURN DISTINCT seg.id AS segment_id
    ORDER BY segment_id
    """

    QUERY_DISCOVER: LiteralString = """
    UNWIND $segment_ids AS segment_id
    MATCH (:Segment {id: segment_id})-[:ALIGNED_TO]-(remote_seg:Segment)
        -[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(remote_edition:Edition)
    RETURN DISTINCT remote_seg.id AS segment_id, remote_edition.id AS edition_id
    ORDER BY edition_id, segment_id
    """

    RESOLVE_SEGMENT_PAGE_QUERY: LiteralString = """
    UNWIND $segment_ids AS segment_id
    MATCH (seg:Segment {id: segment_id})-[:SEGMENT_OF]->(sgn:Segmentation)
        <-[:HAS_SEGMENTATION]-(edition:Edition)-[:EDITION_OF]->(text:Text)
    WHERE ($text_id IS NULL OR text.id = $text_id)
      AND ($filter_edition_id IS NULL OR edition.id = $filter_edition_id)
      AND ($language IS NULL OR (text)-[:HAS_LANGUAGE]->(:Language {code: $language}))
    CALL (seg) {
        MATCH (span:Span)-[:SPAN_OF]->(seg)
        WHERE span.start < span.end
        WITH span ORDER BY span.start
        RETURN collect({start: span.start, end: span.end}) AS lines,
               min(span.start) AS min_start
    }
    WITH text, edition, sgn, seg, lines, min_start
    WHERE size(lines) > 0
    WITH text, edition, sgn, seg, lines, min_start,
        [(seg)-[:HAS_TAG]->(t:Tag)
            WHERE ($application IS NULL
                OR (t)-[:BELONGS_TO]->(:Application {id: $application}))
            | t.id] AS tag_ids
    WITH text, edition, sgn, seg, lines, min_start,
         CASE WHEN size(tag_ids) = 0 THEN null ELSE tag_ids END AS tag_ids
    ORDER BY text.id, edition.id, sgn.id, min_start, seg.id
    SKIP $offset
    LIMIT $limit
    RETURN seg.id AS id, sgn.id AS segmentation_id,
           edition.id AS edition_id, text.id AS text_id,
           seg.type AS type,
           seg.reference AS reference, lines, tag_ids
    """

    FIND_BY_SPAN_QUERY: LiteralString = """
    MATCH (:Edition {id: $edition_id})
        -[:HAS_SEGMENTATION]->(:Segmentation)
        <-[:SEGMENT_OF]-(seg:Segment)
        <-[:SPAN_OF]-(span:Span)
    WHERE span.start < $span_end AND span.end > $span_start
    RETURN DISTINCT seg.id as segment_id
    ORDER BY segment_id
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def _session(self) -> AsyncSession:
        return self._db.get_session()

    async def get(self, segment_id: str, application: str | None = None) -> SegmentWithContextOutput:
        async def _read(tx: AsyncManagedTransaction) -> SegmentWithContextOutput:
            result = await tx.run(self.GET_QUERY, segment_id=segment_id, application=application)
            records = await result.data()
            if not records:
                raise DataNotFoundError(f"Segment '{segment_id}' not found")
            return SegmentWithContextOutput.model_validate(records[0])

        async with self._session as session:
            return await session.execute_read(_read)

    async def get_related(
        self,
        edition_id: str,
        spans: list[tuple[int, int]],
        application: str | None = None,
        max_depth: int = 5,
        offset: int = 0,
        limit: int = 20,
        filters: RelatedSegmentsFilter | None = None,
    ) -> list[SegmentWithContextOutput]:
        """Traverse direct segment alignments from an edition+spans and return paged segments."""
        filters = filters or RelatedSegmentsFilter()

        async def _read(tx: AsyncManagedTransaction) -> list[SegmentWithContextOutput]:
            start_records = await (
                await tx.run(
                    self.FIND_START_SEGMENTS_QUERY,
                    edition_id=edition_id,
                    spans=_merge_spans([list(s) for s in spans]),
                )
            ).data()
            start_segment_ids = {record["segment_id"] for record in start_records}
            if not start_segment_ids:
                return []

            related_segment_ids: set[str] = set()
            visited_editions = {edition_id}
            frontier = sorted(start_segment_ids)

            for _ in range(max_depth):
                records = await (await tx.run(self.QUERY_DISCOVER, segment_ids=frontier)).data()
                new_segment_editions = {
                    record["segment_id"]: record["edition_id"]
                    for record in records
                    if record["edition_id"] not in visited_editions
                }
                if not new_segment_editions:
                    break

                # Updating after collection preserves one-to-many alignments within this level.
                visited_editions.update(new_segment_editions.values())
                frontier = sorted(new_segment_editions)
                related_segment_ids.update(frontier)

            if not related_segment_ids:
                return []

            return await self._resolve_segment_page(
                tx,
                segment_ids=sorted(related_segment_ids),
                application=application,
                offset=offset,
                limit=limit,
                filters=filters,
            )

        async with self._session as session:
            return await session.execute_read(_read)

    async def _resolve_segment_page(
        self,
        tx: AsyncManagedTransaction,
        segment_ids: list[str],
        application: str | None,
        offset: int,
        limit: int,
        filters: RelatedSegmentsFilter,
    ) -> list[SegmentWithContextOutput]:
        records = await (
            await tx.run(
                self.RESOLVE_SEGMENT_PAGE_QUERY,
                segment_ids=segment_ids,
                application=application,
                offset=offset,
                limit=limit,
                text_id=filters.text_id,
                filter_edition_id=filters.edition_id,
                language=filters.language,
            )
        ).data()
        return [SegmentWithContextOutput.model_validate(record) for record in records if record["lines"]]

    async def find_by_span(self, edition_id: str, start: int, end: int) -> list[str]:
        async def _read(tx: AsyncManagedTransaction) -> list[str]:
            result = await tx.run(self.FIND_BY_SPAN_QUERY, edition_id=edition_id, span_start=start, span_end=end)
            return [r["segment_id"] for r in await result.data()]

        async with self._session as session:
            return await session.execute_read(_read)


def _merge_spans(spans: list[list[int]]) -> list[list[int]]:
    if not spans:
        return []

    spans.sort()

    merged = spans[:1]
    for curr in spans[1:]:
        prev = merged[-1]
        if curr[0] <= prev[1]:
            prev[1] = max(prev[1], curr[1])
        else:
            merged.append(curr)
    return merged
