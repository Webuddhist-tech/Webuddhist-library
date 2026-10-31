"""Edition revisions and consistent reads shared by content and annotation writers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import LiteralString

from neo4j import AsyncManagedTransaction

from database.locking import lock_nodes
from exceptions import DataConflictError, DataNotFoundError, DataValidationError


@dataclass(frozen=True)
class ContentState:
    edition_id: str
    text_id: str
    object_key: str
    length: int
    revision: int

    def validate_span(self, end: int) -> None:
        if end > self.length:
            raise DataValidationError(
                f"Offsets extend to {end} but edition '{self.edition_id}' content is {self.length} characters; "
                "offsets must be Unicode code point positions in the edition content"
            )


async def read_state(tx: AsyncManagedTransaction, edition_id: str) -> ContentState:
    result = await tx.run(
        """
        MATCH (e:Edition {id: $id})-[:EDITION_OF]->(text:Text)
        RETURN e.id AS edition_id, text.id AS text_id, e.content_key AS object_key,
               e.content_length AS length, e.revision AS revision
    """,
        id=edition_id,
    )
    record = await result.single()
    if record is None:
        raise DataNotFoundError(f"Edition with ID '{edition_id}' not found")
    if any(record[key] is None for key in ("object_key", "length", "revision")):
        raise DataValidationError(f"Edition '{edition_id}' requires the content-state migration")
    return ContentState(**record)


async def advance_revision(tx: AsyncManagedTransaction, edition_id: str) -> None:
    """Neo4j locks before reading the revision and holds that lock until commit."""
    result = await tx.run(
        """
        MATCH (e:Edition {id: $id}) SET e.revision = e.revision + 1
        FINISH
    """,
        id=edition_id,
    )
    await result.consume()


async def read_at_revision[T](
    tx: AsyncManagedTransaction, edition_id: str, read: Callable[[AsyncManagedTransaction], Awaitable[T]]
) -> tuple[ContentState, T]:
    """No long-lived read lock: reject a graph view that overlaps a committed mutation."""
    for _ in range(3):
        state = await read_state(tx, edition_id)
        value = await read(tx)
        if state == await read_state(tx, edition_id):
            return state, value
    raise DataConflictError("Edition changed while reading; reload and retry")


async def touch_edition(tx: AsyncManagedTransaction, edition_id: str) -> ContentState:
    """Serialize an annotation mutation and invalidate edits prepared before it."""
    await advance_revision(tx, edition_id)
    return await read_state(tx, edition_id)


async def touch_annotation(tx: AsyncManagedTransaction, label: LiteralString, annotation_id: str) -> None:
    result = await tx.run(f"MATCH (a:{label} {{id: $id}})-->(e:Edition) RETURN e.id AS id", id=annotation_id)
    record = await result.single()
    if record is not None:
        await touch_edition(tx, record["id"])


async def lock_connected_editions(tx: AsyncManagedTransaction, edition_id: str) -> list[str]:
    """Segment removal changes alignment views on both ends; lock those editions too."""

    async def connected() -> set[str]:
        result = await tx.run(
            """
            MATCH (:Edition {id: $id})-[:HAS_SEGMENTATION]->(:Segmentation)<-[:SEGMENT_OF]-(:Segment)
                  -[:ALIGNED_TO]-(:Segment)-[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(e:Edition)
            RETURN DISTINCT e.id AS id
        """,
            id=edition_id,
        )
        return {edition_id, *(r["id"] for r in await result.data())}

    ids = await connected()
    await lock_nodes(tx, "Edition", sorted(ids))
    if not await connected() <= ids:
        raise DataConflictError("Alignment changed while acquiring edition locks; retry the operation")
    return sorted(ids - {edition_id})


async def read_value[T](
    tx: AsyncManagedTransaction, edition_id: str, read: Callable[[AsyncManagedTransaction], Awaitable[T]]
) -> T:
    return (await read_at_revision(tx, edition_id, read))[1]
