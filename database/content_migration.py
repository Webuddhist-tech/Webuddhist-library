"""Verify legacy content and backfill pointers during an explicit maintenance pause."""

from typing import TYPE_CHECKING

from exceptions import DataValidationError

if TYPE_CHECKING:
    from database import Database
    from storage import Storage


async def migrate_content(db: Database, storage: Storage, *, apply: bool = False) -> int:
    """Backfill only missing fields; existing objects can remain at their legacy keys."""
    count = 0
    after = ""
    while True:
        async with db.get_session() as session:
            result = await session.run(
                """
                MATCH (e:Edition)-[:EDITION_OF]->(t:Text) WHERE e.id > $after
                RETURN e.id AS id, t.id AS text_id, e.content_key AS key, e.content_length AS length,
                       e.revision AS revision
                ORDER BY e.id LIMIT 100
            """,
                after=after,
            )
            editions = await result.data()
        if not editions:
            break
        for edition in editions:
            key = edition["key"] or f"base_texts/{edition['text_id']}/{edition['id']}.txt"
            content = await storage.read_text(key)
            length = len(content)
            if not length or edition["length"] not in (None, length):
                raise DataValidationError(f"Edition {edition['id']} has empty content or a length mismatch")
            async with db.get_session() as session:
                invalid = await (
                    await session.run(
                        """
                    MATCH (e:Edition {id: $id})
                    CALL (e) {
                        MATCH (e)-[:HAS_SEGMENTATION]->()<-[:SEGMENT_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
                        UNION MATCH (e)<-[:PAGINATION_OF]-()<-[:VOLUME_OF]-()<-[:PAGE_OF]-()<-[:SPAN_OF]-(s:Span)
                        RETURN s
                        UNION MATCH (e)<-[:NOTE_OF|MARK_OF|BIBLIOGRAPHY_OF|ATTRIBUTE_OF]-()<-[:SPAN_OF]-(s:Span)
                        RETURN s
                        UNION MATCH (e)<-[:TOC_OF]-()<-[:SECTION_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
                        UNION MATCH (e)<-[:RECORDING_OF]-()<-[:CUE_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
                    }
                    WITH s
                    WHERE s.start IS NULL OR s.end IS NULL OR s.start < 0 OR s.end < s.start OR s.end > $length
                    RETURN count(s) AS invalid
                """,
                        id=edition["id"],
                        length=length,
                    )
                ).single(strict=True)
                if invalid["invalid"]:
                    raise DataValidationError(f"Edition {edition['id']} has annotations outside its actual content")
                if apply and any(edition[field] is None for field in ("key", "revision", "length")):
                    await (
                        await session.run(
                            """
                            MATCH (e:Edition {id: $id})
                            SET e.content_key = coalesce(e.content_key, $key),
                                e.content_length = coalesce(e.content_length, $length),
                                e.revision = coalesce(e.revision, 0)
                            """,
                            id=edition["id"],
                            key=key,
                            length=length,
                        )
                    ).consume()
            count += 1
        after = editions[-1]["id"]
    return count
