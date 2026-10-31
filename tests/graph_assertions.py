"""Exact graph comparisons for rollback tests in the isolated test database."""


async def snapshot_graph(db):
    """Capture identities and contents, so counts cannot hide leaks or mutations."""
    async def read(tx):
        nodes = await (await tx.run("""
            MATCH (n)
            RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
            ORDER BY id
        """)).data()
        for node in nodes:
            node["labels"].sort()
        relationships = await (await tx.run("""
            MATCH (a)-[r]->(b)
            RETURN elementId(r) AS id, elementId(a) AS start, elementId(b) AS end,
                   type(r) AS type, properties(r) AS properties
            ORDER BY id
        """)).data()
        return {"nodes": nodes, "relationships": relationships}

    async with db.get_session() as session:
        return await session.execute_read(read)
