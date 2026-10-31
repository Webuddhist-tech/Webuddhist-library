from typing import TYPE_CHECKING, LiteralString

from database.content_state import touch_edition
from identifier import generate_id

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.annotation import SingleSpanAnnotation


# These fragments use explicit variables supplied by the domain queries.
SINGLE_SPAN_RETURN: LiteralString = """
    annotation.id AS id,
    edition.id AS edition_id,
    text.id AS text_id,
    {start: span.start, end: span.end} AS span,
    [(annotation)-[:HAS_METADATA]->(metadata:AnnotationMetadata) | metadata {.name}][0] AS metadata
"""

CREATE_SPAN_AND_METADATA: LiteralString = """
    WITH annotation
    CREATE (:Span {start: $span_start, end: $span_end})-[:SPAN_OF]->(annotation)
    CALL (*) {
        WHEN $metadata_id IS NOT NULL THEN {
            CREATE (annotation)-[:HAS_METADATA]->(:AnnotationMetadata {id: $metadata_id, name: $metadata_name})
        }
    }
    RETURN annotation.id AS id
"""

DELETE_SPAN_AND_METADATA: LiteralString = """
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(annotation)
    OPTIONAL MATCH (annotation)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
    DETACH DELETE span, metadata, annotation
    FINISH
"""


async def create_single_span_annotation(
    tx: AsyncManagedTransaction,
    query: LiteralString,
    edition_id: str,
    annotation: SingleSpanAnnotation,
    **properties: str,
) -> str:
    state = await touch_edition(tx, edition_id)
    state.validate_span(annotation.max_end)
    result = await tx.run(
        query,
        parameters={
            "edition_id": edition_id,
            "annotation_id": generate_id(),
            "span_start": annotation.span.start,
            "span_end": annotation.span.end,
            "metadata_id": generate_id() if annotation.metadata is not None else None,
            "metadata_name": annotation.metadata.name if annotation.metadata is not None else None,
            **properties,
        },
    )
    record = await result.single(strict=True)
    return str(record["id"])
