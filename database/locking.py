"""Take locks in their own statement, before reading mutable state in a later query."""

from typing import LiteralString

from neo4j import AsyncManagedTransaction, AsyncTransaction


async def lock_nodes(tx: AsyncManagedTransaction | AsyncTransaction, label: LiteralString, ids: list[str]) -> None:
    # Labels are trusted source-code constants. IDs are parameters and sorted for a
    # consistent order when an alignment or shared Work mutation touches several nodes.
    await (
        await tx.run(
            f"""
        MATCH (n:{label}) WHERE n.id IN $ids
        WITH n ORDER BY n.id
        WITH collect(n) AS nodes
        CALL apoc.lock.nodes(nodes)
        FINISH
    """,
            ids=ids,
        )
    ).consume()
