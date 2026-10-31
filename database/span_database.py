from typing import TYPE_CHECKING, LiteralString

from .database_validator import DatabaseValidator

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from .database import Database


def _adjust_continuous_for_insert(start: int, end: int, insert_pos: int, insert_len: int) -> tuple[int, int]:
    """Adjust continuous span (Segmentation/Pagination) for INSERT. Expands at end boundary and position 0."""
    if insert_pos == 0 and start == 0:
        return (start, end + insert_len)
    if insert_pos <= start:
        return (start + insert_len, end + insert_len)
    if insert_pos <= end:
        return (start, end + insert_len)
    return (start, end)


def _adjust_annotation_for_insert(start: int, end: int, insert_pos: int, insert_len: int) -> tuple[int, int]:
    """Adjust annotation span for INSERT. Shifts at boundaries, only expands when strictly inside."""
    if insert_pos <= start:
        return (start + insert_len, end + insert_len)
    if insert_pos < end:
        return (start, end + insert_len)
    return (start, end)


def _adjust_span_for_delete(start: int, end: int, del_start: int, del_end: int) -> tuple[int, int] | None:
    """Adjust span for DELETE. Returns None if fully encompassed."""
    del_len = del_end - del_start

    if del_end <= start:
        return (start - del_len, end - del_len)
    if del_start >= end:
        return (start, end)
    if del_start <= start and del_end >= end:
        return None
    if del_start <= start < del_end < end:
        return (del_start, end - del_len)
    if start < del_start < end <= del_end:
        return (start, del_start)
    if start < del_start and del_end < end:
        return (start, end - del_len)
    return (start, end)


def _adjust_continuous_for_replace(
    start: int,
    end: int,
    replace_start: int,
    replace_end: int,
    new_len: int,
    *,
    is_first_encompassed: bool,
) -> tuple[int, int] | None:
    """Adjust continuous span (Segmentation/Pagination) for REPLACE. Keeps first encompassed segment."""
    delta = new_len - (replace_end - replace_start)

    if replace_start >= end:
        return (start, end)
    if replace_end <= start:
        return (start + delta, end + delta)
    if start == replace_start and end == replace_end:
        return (start, start + new_len)
    if replace_start <= start and replace_end >= end:
        if is_first_encompassed:
            return (replace_start, replace_start + new_len)
        return None
    if start < replace_start and replace_end < end:
        return (start, end + delta)
    if replace_start <= start < replace_end < end:
        new_start = replace_start if start == replace_start else replace_start + new_len
        return (new_start, end + delta)
    if start < replace_start < end <= replace_end:
        return (start, replace_start + new_len)
    return (start, end)


def _map_replace_boundary(position: int, replace_start: int, replace_end: int, new_len: int) -> int:
    """Map one line boundary through a replacement."""
    if position <= replace_start:
        return position
    if position >= replace_end:
        return position + new_len - (replace_end - replace_start)
    return replace_start + new_len


def _adjust_continuous_lines_for_replace(
    lines: list[tuple[str, int, int]],
    replace_start: int,
    replace_end: int,
    new_len: int,
    *,
    is_first_encompassed: bool,
) -> list[tuple[str, int, int]] | None:
    """Adjust the outer entity once, then map all of its line boundaries consistently."""
    nonempty_indexes = [index for index, (_, start, end) in enumerate(lines) if start < end]
    if not nonempty_indexes:
        return [
            (
                span_id,
                mapped := _map_replace_boundary(start, replace_start, replace_end, new_len),
                mapped,
            )
            for span_id, start, _ in lines
        ]

    outer = _adjust_continuous_for_replace(
        lines[0][1],
        lines[-1][2],
        replace_start,
        replace_end,
        new_len,
        is_first_encompassed=is_first_encompassed,
    )
    if outer is None:
        return None

    outer_start, outer_end = outer
    adjusted_lines: list[tuple[str, int, int]] = []
    first_nonempty, last_nonempty = nonempty_indexes[0], nonempty_indexes[-1]
    for index, (span_id, start, end) in enumerate(lines):
        new_start = min(max(_map_replace_boundary(start, replace_start, replace_end, new_len), outer_start), outer_end)
        new_end = min(max(_map_replace_boundary(end, replace_start, replace_end, new_len), outer_start), outer_end)
        if index == first_nonempty:
            new_start = outer_start
        if index == last_nonempty:
            new_end = outer_end
        if new_start != new_end or start == end:
            adjusted_lines.append((span_id, new_start, new_end))

    return sorted(adjusted_lines, key=lambda line: (line[1], line[2], line[0]))


def _adjust_annotation_for_replace(
    start: int,
    end: int,
    replace_start: int,
    replace_end: int,
    new_len: int,
) -> tuple[int, int] | None:
    """Adjust annotation span for REPLACE. Deletes on exact match or encompass."""
    delta = new_len - (replace_end - replace_start)

    if replace_start >= end:
        return (start, end)
    if replace_end <= start:
        return (start + delta, end + delta)
    if replace_start <= start and replace_end >= end:
        return None
    if start < replace_start and replace_end < end:
        return (start, end + delta)
    if replace_start <= start < replace_end < end:
        return (replace_start + new_len, end + delta)

    return (start, replace_start + new_len)  # start < replace_start < end <= replace_end


class SpanDatabase:
    FIND_CONTINUOUS_SPANS_QUERY: LiteralString = """
    CALL {
        MATCH (m:Edition {id: $edition_id})
            -[:HAS_SEGMENTATION]->(collection:Segmentation)
            <-[:SEGMENT_OF]-(entity:Segment)
            <-[:SPAN_OF]-(span:Span)
        RETURN collection.id AS collection_id, entity.id AS entity_id,
               elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
        UNION ALL
        MATCH (m:Edition {id: $edition_id})
            <-[:PAGINATION_OF]-(collection:Pagination)
            <-[:VOLUME_OF]-(:Volume)
            <-[:PAGE_OF]-(entity:Page)
            <-[:SPAN_OF]-(span:Span)
        RETURN collection.id AS collection_id, entity.id AS entity_id,
               elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
    }
    RETURN collection_id, entity_id, span_id, span_start, span_end
    ORDER BY collection_id, span_start, entity_id, span_end, span_id
    """

    FIND_ANNOTATION_SPANS_QUERY: LiteralString = """
    CALL {
        MATCH (m:Edition {id: $edition_id})
            <-[:NOTE_OF|MARK_OF|BIBLIOGRAPHY_OF|ATTRIBUTE_OF]-(entity)
            <-[:SPAN_OF]-(span:Span)
        RETURN elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
        UNION ALL
        MATCH (m:Edition {id: $edition_id})
            <-[:TOC_OF]-(:TableOfContents)
            <-[:SECTION_OF]-(entity:TableOfContentsSection)
            <-[:SPAN_OF]-(span:Span)
        RETURN elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
        UNION ALL
        MATCH (m:Edition {id: $edition_id})
            <-[:RECORDING_OF]-(:Recording)
            <-[:CUE_OF]-(entity:Cue)
            <-[:SPAN_OF]-(span:Span)
        RETURN elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
    }
    RETURN span_id, span_start, span_end
    ORDER BY span_start
    """

    BATCH_UPDATE_SPANS_QUERY: LiteralString = """
    UNWIND $updates AS u
    MATCH (span:Span)
    WHERE elementId(span) = u.span_id
    SET span.start = u.new_start, span.end = u.new_end
    FINISH
    """

    BATCH_DELETE_SPANS_QUERY: LiteralString = """
    UNWIND $span_ids AS span_id
    MATCH (span:Span)-[:SPAN_OF]->(
        entity:Segment|Page|BibliographicMetadata|Note|Mark|Attribute|TableOfContentsSection|Cue
    )
    WHERE elementId(span) = span_id
    DETACH DELETE span
    WITH DISTINCT entity
    WHERE NOT (:Span)-[:SPAN_OF]->(entity)
    OPTIONAL MATCH (entity)-[:HAS_TITLE|HAS_SUMMARY]->(nomen:Nomen)
    OPTIONAL MATCH (nomen)-[:HAS_LOCALIZATION]->(localized:LocalizedText)
    DETACH DELETE localized, nomen, entity
    FINISH
    """

    SHIFT_CONTENT_LENGTH_QUERY: LiteralString = """
    MATCH (m:Edition {id: $edition_id})
    SET m.content_length = m.content_length + $delta
    FINISH
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    async def _shift_content_length(tx: AsyncManagedTransaction, edition_id: str, delta: int) -> None:
        if delta:
            await tx.run(SpanDatabase.SHIFT_CONTENT_LENGTH_QUERY, edition_id=edition_id, delta=delta)

    @staticmethod
    async def _flush_batch(
        tx: AsyncManagedTransaction,
        updates: list[dict[str, str | int]],
        deletes: list[str],
    ) -> None:
        """Execute batched span updates and deletions."""
        if updates:
            await tx.run(SpanDatabase.BATCH_UPDATE_SPANS_QUERY, updates=updates)
        if deletes:
            await tx.run(SpanDatabase.BATCH_DELETE_SPANS_QUERY, span_ids=deletes)

    async def adjust_spans_for_insert(self, edition_id: str, position: int, length: int) -> None:
        """Adjust all spans for an INSERT operation."""

        async def write(tx: AsyncManagedTransaction) -> None:
            await DatabaseValidator.validate_edition_spans(tx, edition_id, position)
            updates: list[dict[str, str | int]] = []

            result = await tx.run(self.FIND_CONTINUOUS_SPANS_QUERY, edition_id=edition_id)
            for record in await result.data():
                adjusted = _adjust_continuous_for_insert(record["span_start"], record["span_end"], position, length)
                if adjusted != (record["span_start"], record["span_end"]):
                    updates.append({"span_id": record["span_id"], "new_start": adjusted[0], "new_end": adjusted[1]})

            result = await tx.run(self.FIND_ANNOTATION_SPANS_QUERY, edition_id=edition_id)
            for record in await result.data():
                adjusted = _adjust_annotation_for_insert(record["span_start"], record["span_end"], position, length)
                if adjusted != (record["span_start"], record["span_end"]):
                    updates.append({"span_id": record["span_id"], "new_start": adjusted[0], "new_end": adjusted[1]})

            await self._flush_batch(tx, updates, [])
            await self._shift_content_length(tx, edition_id, length)

        async with self._db.get_session() as session:
            await session.execute_write(write)

    async def adjust_spans_for_delete(self, edition_id: str, start: int, end: int) -> None:
        """Adjust all spans for a DELETE operation."""

        async def write(tx: AsyncManagedTransaction) -> None:
            await DatabaseValidator.validate_edition_spans(tx, edition_id, end)
            updates: list[dict[str, str | int]] = []
            deletes: list[str] = []

            result = await tx.run(self.FIND_CONTINUOUS_SPANS_QUERY, edition_id=edition_id)
            for record in await result.data():
                adjusted = _adjust_span_for_delete(record["span_start"], record["span_end"], start, end)
                if adjusted is None:
                    deletes.append(record["span_id"])
                elif adjusted != (record["span_start"], record["span_end"]):
                    updates.append({"span_id": record["span_id"], "new_start": adjusted[0], "new_end": adjusted[1]})

            result = await tx.run(self.FIND_ANNOTATION_SPANS_QUERY, edition_id=edition_id)
            for record in await result.data():
                adjusted = _adjust_span_for_delete(record["span_start"], record["span_end"], start, end)
                if adjusted is None:
                    deletes.append(record["span_id"])
                elif adjusted != (record["span_start"], record["span_end"]):
                    updates.append({"span_id": record["span_id"], "new_start": adjusted[0], "new_end": adjusted[1]})

            await self._flush_batch(tx, updates, deletes)
            await self._shift_content_length(tx, edition_id, start - end)

        async with self._db.get_session() as session:
            await session.execute_write(write)

    async def adjust_spans_for_replace(self, edition_id: str, start: int, end: int, new_len: int) -> None:
        """Adjust all spans for a REPLACE operation."""

        async def write(tx: AsyncManagedTransaction) -> None:
            await DatabaseValidator.validate_edition_spans(tx, edition_id, end)
            updates: list[dict[str, str | int]] = []
            deletes: list[str] = []

            result = await tx.run(self.FIND_CONTINUOUS_SPANS_QUERY, edition_id=edition_id)
            entities: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
            for record in await result.data():
                key = (record["collection_id"], record["entity_id"])
                entities.setdefault(key, []).append((record["span_id"], record["span_start"], record["span_end"]))

            encompassed_collections: set[str] = set()
            ordered_entities = sorted(
                entities.items(),
                key=lambda item: (item[0][0], item[1][0][1], item[1][-1][2], item[0][1]),
            )
            for (collection_id, _), lines in ordered_entities:
                is_encompassed = any(line_start < line_end for _, line_start, line_end in lines)
                is_encompassed = is_encompassed and start <= lines[0][1] and end >= lines[-1][2]
                is_first = is_encompassed and collection_id not in encompassed_collections
                if is_encompassed:
                    encompassed_collections.add(collection_id)

                adjusted_lines = _adjust_continuous_lines_for_replace(
                    lines, start, end, new_len, is_first_encompassed=is_first
                )
                if adjusted_lines is None:
                    deletes.extend(span_id for span_id, _, _ in lines)
                    continue

                adjusted_by_id = {span_id: (new_start, new_end) for span_id, new_start, new_end in adjusted_lines}
                for span_id, old_start, old_end in lines:
                    adjusted = adjusted_by_id.get(span_id)
                    if adjusted is None:
                        deletes.append(span_id)
                    elif adjusted != (old_start, old_end):
                        updates.append({"span_id": span_id, "new_start": adjusted[0], "new_end": adjusted[1]})

            result = await tx.run(self.FIND_ANNOTATION_SPANS_QUERY, edition_id=edition_id)
            for record in await result.data():
                adjusted = _adjust_annotation_for_replace(record["span_start"], record["span_end"], start, end, new_len)
                if adjusted is None:
                    deletes.append(record["span_id"])
                elif adjusted != (record["span_start"], record["span_end"]):
                    updates.append({"span_id": record["span_id"], "new_start": adjusted[0], "new_end": adjusted[1]})

            await self._flush_batch(tx, updates, deletes)
            await self._shift_content_length(tx, edition_id, new_len - (end - start))

        async with self._db.get_session() as session:
            await session.execute_write(write)
