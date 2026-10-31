from typing import TYPE_CHECKING, LiteralString

from neo4j.exceptions import ResultNotSingleError

from database.content_state import read_value, touch_annotation, touch_edition
from exceptions import DataConflictError, DataNotFoundError
from identifier import generate_id
from models.annotation import PaginationInput, PaginationOutput

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database.database import Database


class PaginationDatabase:
    _GET_QUERY_BODY: LiteralString = """
    MATCH (pagination)<-[:VOLUME_OF]-(volume:Volume)<-[:PAGE_OF]-(page:Page)<-[:SPAN_OF]-(span:Span)
    WITH pagination, edition, text, volume, page, span ORDER BY span.start, span.end
    WITH pagination, edition, text, volume, page, collect(span {.start, .end}) AS lines
    ORDER BY lines[0].start
    WITH pagination, edition, text, volume, collect(page {.reference, lines: lines}) AS pages
    ORDER BY volume.index
    WITH pagination, edition, text, collect(volume {.index, pages: pages,
        metadata: [(volume)-[:HAS_METADATA]->(m:AnnotationMetadata) | m {.name}][0]}) AS volumes
    RETURN pagination.id AS id, edition.id AS edition_id, text.id AS text_id, volumes,
           [(pagination)-[:HAS_METADATA]->(m:AnnotationMetadata) | m {.name}][0] AS metadata
    """

    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (pagination:Pagination {{id: $pagination_id}})
        -[:PAGINATION_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)
    {_GET_QUERY_BODY}
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (pagination:Pagination)-[:PAGINATION_OF]->(edition:Edition {{id: $edition_id}})
        -[:EDITION_OF]->(text:Text)
    {_GET_QUERY_BODY}
    """

    CREATE_QUERY: LiteralString = """
    MATCH (edition:Edition {id: $edition_id})
    WHERE NOT EXISTS { (:Pagination)-[:PAGINATION_OF]->(edition) }
    CREATE (pagination:Pagination {id: $pagination_id})-[:PAGINATION_OF]->(edition)
    WITH pagination
    FOREACH (metadata IN $metadata |
        CREATE (pagination)-[:HAS_METADATA]->(:AnnotationMetadata {id: metadata.id, name: metadata.name})
    )
    WITH pagination
    UNWIND $volumes AS volume_data
    CREATE (volume:Volume {id: volume_data.id, index: volume_data.index})-[:VOLUME_OF]->(pagination)
    WITH pagination, volume, volume_data
    FOREACH (metadata IN volume_data.metadata |
        CREATE (volume)-[:HAS_METADATA]->(:AnnotationMetadata {id: metadata.id, name: metadata.name})
    )
    WITH pagination, volume, volume_data
    UNWIND volume_data.pages AS page_data
    CREATE (page:Page {id: page_data.id, reference: page_data.reference})-[:PAGE_OF]->(volume)
    WITH pagination, page, page_data
    UNWIND page_data.lines AS line
    CREATE (:Span {start: line.start, end: line.end})-[:SPAN_OF]->(page)
    RETURN DISTINCT pagination.id AS id
    """

    _DELETE_BODY: LiteralString = """
    OPTIONAL MATCH (pagination)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
    OPTIONAL MATCH (volume:Volume)-[:VOLUME_OF]->(pagination)
    OPTIONAL MATCH (volume)-[:HAS_METADATA]->(volume_metadata:AnnotationMetadata)
    OPTIONAL MATCH (page:Page)-[:PAGE_OF]->(volume)
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(page)
    DETACH DELETE span, page, volume_metadata, volume, metadata, pagination
    FINISH
    """

    DELETE_QUERY: LiteralString = f"""
    MATCH (pagination:Pagination {{id: $pagination_id}})
    {_DELETE_BODY}
    """

    DELETE_ALL_QUERY: LiteralString = f"""
    MATCH (pagination:Pagination)-[:PAGINATION_OF]->(:Edition {{id: $edition_id}})
    {_DELETE_BODY}
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, pagination_id: str) -> PaginationOutput:
        async def read(tx: AsyncManagedTransaction) -> PaginationOutput:
            result = await tx.run(PaginationDatabase.GET_BY_ID_QUERY, pagination_id=pagination_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Pagination with ID '{pagination_id}' not found")
            return PaginationOutput.model_validate(record)

        async with self._db.get_session() as session:
            return await session.execute_read(read)

    async def get_all(self, edition_id: str) -> PaginationOutput | None:
        async def read(tx: AsyncManagedTransaction) -> PaginationOutput | None:
            result = await tx.run(PaginationDatabase.GET_BY_EDITION_ID_QUERY, edition_id=edition_id)
            record = await result.single()
            return PaginationOutput.model_validate(record) if record else None

        async with self._db.get_session() as session:
            return await session.execute_read(read_value, edition_id, read)

    async def add(self, edition_id: str, pagination: PaginationInput) -> str:
        async with self._db.get_session() as session:
            return await session.execute_write(self.add_with_transaction, edition_id, pagination)

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        pagination: PaginationInput,
    ) -> str:
        state = await touch_edition(tx, edition_id)
        state.validate_span(pagination.max_end)

        volumes_data = [
            {
                "id": generate_id(),
                "index": volume.index,
                "pages": [page.model_dump() | {"id": generate_id()} for page in volume.pages],
                "metadata": [{"id": generate_id(), "name": volume.metadata.name}]
                if volume.metadata is not None
                else [],
            }
            for volume in pagination.volumes
        ]
        result = await tx.run(
            PaginationDatabase.CREATE_QUERY,
            edition_id=edition_id,
            pagination_id=generate_id(),
            volumes=volumes_data,
            metadata=[{"id": generate_id(), "name": pagination.metadata.name}]
            if pagination.metadata is not None
            else [],
        )
        try:
            record = await result.single(strict=True)
        except ResultNotSingleError as exc:
            raise DataConflictError(f"Edition '{edition_id}' already has a pagination") from exc
        return str(record["id"])

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, pagination_id: str) -> None:
        await touch_annotation(tx, "Pagination", pagination_id)
        await tx.run(PaginationDatabase.DELETE_QUERY, pagination_id=pagination_id)

    async def delete(self, pagination_id: str) -> None:
        async with self._db.get_session() as session:
            await session.execute_write(self.delete_with_transaction, pagination_id)
