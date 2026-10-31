"""Repeatable schema upgrades. Run one at a time with writers paused; audit never mutates data."""

import logging
from enum import StrEnum
from pathlib import Path
from typing import LiteralString, cast

from neo4j import AsyncDriver

from database.neo4j_triggers import audit_triggers, install_triggers
from models.enums import AttributeType, BibliographyType, ContributorRole, EditionType, LicenseType, MarkType, NoteType

logger = logging.getLogger(__name__)

AUDITS: dict[str, LiteralString] = {
    "duplicate_ids": """
        MATCH (n) WHERE n.id IS NOT NULL
        UNWIND labels(n) AS label
        WITH label, n.id AS id, count(*) AS count WHERE count > 1
        RETURN label + ':' + id AS violating_id
    """,
    "duplicate_lookup_names": """
        MATCH (n:AI|Language|Source|RoleType|EditionType|LicenseType|BibliographyType|NoteType|MarkType|AttributeType)
        UNWIND labels(n) AS label
        WITH label, coalesce(n.code, n.name, n.id) AS key, count(*) AS count
        WHERE key IS NOT NULL AND count > 1
        RETURN label + ':' + key AS violating_id
    """,
    "noncontiguous_segmentation": """
        MATCH (segmentation:Segmentation)<-[:SEGMENT_OF]-(segment:Segment)
        OPTIONAL MATCH (line:Span)-[:SPAN_OF]->(segment)
        WITH segmentation, segment, line ORDER BY line.start, line.end
        WITH segmentation, segment, collect(line) AS lines
        ORDER BY lines[0].start, lines[-1].end, segment.id
        WITH segmentation, collect({start: lines[0].start, end: lines[-1].end,
            invalid: size(lines) = 0 OR any(i IN range(1, size(lines) - 1)
                WHERE lines[i - 1].end <> lines[i].start)}) AS segments
        WHERE any(segment IN segments WHERE segment.invalid)
           OR any(i IN range(1, size(segments) - 1) WHERE segments[i - 1].end <> segments[i].start)
        RETURN coalesce(segmentation.id, elementId(segmentation)) AS violating_id
    """,
    "empty_pagination": """
        MATCH (n:Pagination|Volume|Page)
        WHERE (n:Pagination AND NOT (:Volume)-[:VOLUME_OF]->(n))
           OR (n:Volume AND NOT (:Page)-[:PAGE_OF]->(n))
           OR (n:Page AND NOT EXISTS { (s:Span)-[:SPAN_OF]->(n) WHERE s.start < s.end })
        RETURN coalesce(n.id, elementId(n)) AS violating_id
    """,
    "legacy_category_relationship": """
        MATCH (c:Category)-[:CHILD_OF]->() RETURN coalesce(c.id, elementId(c)) AS violating_id
    """,
}


async def audit_schema(driver: AsyncDriver, database: str) -> dict[str, list[str]]:
    violations = await audit_triggers(driver, database)
    async with driver.session(database=database) as session:
        for name, query in AUDITS.items():
            result = await session.run(query)
            ids = [record["violating_id"] async for record in result]
            if ids:
                violations[name] = ids
    return violations


async def migrate(driver: AsyncDriver, database: str) -> None:
    """Audit and apply every idempotent step on each explicit invocation."""
    async with driver.session(database="system") as system:
        await (await system.run("ALTER DATABASE $database SET DEFAULT LANGUAGE CYPHER 25", database=database)).consume()

    violations = await audit_schema(driver, database)
    if violations:
        for name, ids in violations.items():
            logger.error("%s: %s", name, ids[:20])
        raise RuntimeError("Schema audit failed; repair reported records before applying migrations")
    async with driver.session(database=database) as session:
        constraints = Path(__file__).with_name("neo4j_constraints.cypher").read_text()
        for statement in constraints.split(";"):
            if statement.strip():
                await (await session.run(cast(LiteralString, statement))).consume()
        lookups: dict[LiteralString, type[StrEnum]] = {
            "RoleType": ContributorRole,
            "EditionType": EditionType,
            "LicenseType": LicenseType,
            "NoteType": NoteType,
            "MarkType": MarkType,
            "BibliographyType": BibliographyType,
            "AttributeType": AttributeType,
        }
        for label, enum in lookups.items():
            await (
                await session.run(f"UNWIND $names AS name MERGE (:{label} {{name: name}})", names=list(enum))
            ).consume()
    await install_triggers(driver, database)
