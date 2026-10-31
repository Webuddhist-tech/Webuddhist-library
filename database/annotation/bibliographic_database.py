from typing import TYPE_CHECKING, LiteralString

from database.content_state import read_value, touch_annotation
from exceptions import DataNotFoundError

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database.database import Database
from models.annotation import BibliographicMetadataInput, BibliographicMetadataOutput

from .single_span import (
    CREATE_SPAN_AND_METADATA,
    DELETE_SPAN_AND_METADATA,
    SINGLE_SPAN_RETURN,
    create_single_span_annotation,
)


class BibliographicDatabase:
    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (span:Span)-[:SPAN_OF]->(annotation:BibliographicMetadata {{id: $bibliographic_id}})
        -[:BIBLIOGRAPHY_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)

    MATCH (annotation)-[:HAS_TYPE]->(annotation_type:BibliographyType)
    RETURN annotation_type.name AS type, {SINGLE_SPAN_RETURN}
    ORDER BY span.start, span.end
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})<-[:BIBLIOGRAPHY_OF]-(annotation:BibliographicMetadata)
        -[:HAS_TYPE]->(annotation_type:BibliographyType),
        (edition)-[:EDITION_OF]->(text:Text)
    MATCH (span:Span)-[:SPAN_OF]->(annotation)

    RETURN annotation_type.name AS type, {SINGLE_SPAN_RETURN}
    ORDER BY span.start, span.end
    """

    CREATE_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})
    MATCH (annotation_type:BibliographyType {{name: $type}})
    CREATE (annotation:BibliographicMetadata {{id: $annotation_id}})-[:BIBLIOGRAPHY_OF]->(edition),
        (annotation)-[:HAS_TYPE]->(annotation_type)
    {CREATE_SPAN_AND_METADATA}
    """

    DELETE_QUERY: LiteralString = f"""
    MATCH (annotation:BibliographicMetadata {{id: $bibliographic_id}})
    {DELETE_SPAN_AND_METADATA}
    """

    DELETE_ALL_QUERY: LiteralString = f"""
    MATCH (annotation:BibliographicMetadata)-[:BIBLIOGRAPHY_OF]->(:Edition {{id: $edition_id}})
    {DELETE_SPAN_AND_METADATA}
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, bibliographic_id: str) -> BibliographicMetadataOutput:
        async def read(tx: AsyncManagedTransaction) -> BibliographicMetadataOutput:
            result = await tx.run(
                BibliographicDatabase.GET_BY_ID_QUERY,
                bibliographic_id=bibliographic_id,
            )
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Bibliographic metadata with ID '{bibliographic_id}' not found")
            return BibliographicMetadataOutput.model_validate(record)

        async with self._db.get_session() as session:
            return await session.execute_read(read)

    async def get_all(self, edition_id: str) -> list[BibliographicMetadataOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[BibliographicMetadataOutput]:
            result = await tx.run(BibliographicDatabase.GET_BY_EDITION_ID_QUERY, edition_id=edition_id)
            return [BibliographicMetadataOutput.model_validate(record) for record in await result.data()]

        async with self._db.get_session() as session:
            return await session.execute_read(read_value, edition_id, read)

    async def add(
        self,
        edition_id: str,
        item: BibliographicMetadataInput,
    ) -> str:
        async with self._db.get_session() as session:
            return await session.execute_write(BibliographicDatabase.add_with_transaction, edition_id, item)

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        item: BibliographicMetadataInput,
    ) -> str:
        return await create_single_span_annotation(
            tx, BibliographicDatabase.CREATE_QUERY, edition_id, item, type=item.type.value
        )

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, bibliographic_id: str) -> None:
        await touch_annotation(tx, "BibliographicMetadata", bibliographic_id)
        await tx.run(BibliographicDatabase.DELETE_QUERY, bibliographic_id=bibliographic_id)

    async def delete(self, bibliographic_id: str) -> None:
        async with self._db.get_session() as session:
            await session.execute_write(BibliographicDatabase.delete_with_transaction, bibliographic_id)
