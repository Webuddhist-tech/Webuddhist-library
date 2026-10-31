import logging
from typing import TYPE_CHECKING, LiteralString

from exceptions import DataNotFoundError, DataValidationError
from models.edition import EditionOutput
from models.enums import EditionType

from .annotation.bibliographic_database import BibliographicDatabase
from .annotation.mark_database import MarkDatabase
from .annotation.note_database import NoteDatabase
from .annotation.pagination_database import PaginationDatabase
from .annotation.segmentation_database import SegmentationDatabase
from .annotation.table_of_contents_database import TableOfContentsDatabase
from .database_validator import DatabaseValidator
from .locking import lock_nodes
from .nomen_database import NomenDatabase
from .recording_database import RecordingDatabase
from .text_database import TextDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from models.annotation import PaginationInput, SegmentationInput
    from models.edition import EditionInput
    from models.text import TextInput

    from .database import Database

logger = logging.getLogger(__name__)


class EditionDatabase:
    DELETE_QUERY: LiteralString = """
    MATCH (m:Edition {id: $edition_id})
    OPTIONAL MATCH (m)-[:HAS_INCIPIT_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    OPTIONAL MATCH (n)<-[:ALTERNATIVE_OF]-(alt:Nomen)-[:HAS_LOCALIZATION]->(alt_lt:LocalizedText)
    OPTIONAL MATCH (attribute:Attribute)-[:ATTRIBUTE_OF]->(m)
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(attribute)
    OPTIONAL MATCH (attribute)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
    DETACH DELETE m, n, lt, alt, alt_lt, span, metadata, attribute
    FINISH
    """

    _EDITION_RETURN: LiteralString = """
    RETURN {
        id: m.id, bdrc: m.bdrc, wiki: m.wiki, colophon: m.colophon,
        source: [(m)-[:HAS_SOURCE]->(s:Source) | s.name][0],
        type: [(m)-[:HAS_TYPE]->(mt:EditionType) | mt.name][0],
        incipit_title: CASE WHEN EXISTS {
            (m)-[:HAS_INCIPIT_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        } THEN apoc.map.fromPairs([(m)-[:HAS_INCIPIT_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->
            (lt:LocalizedText)-[r:HAS_LANGUAGE]->(l:Language) |
            [coalesce(r.bcp47, l.code), lt.text]]) ELSE null END,
        alt_incipit_titles: CASE WHEN EXISTS {
            (m)-[:HAS_INCIPIT_TITLE]->(:Nomen)<-[:ALTERNATIVE_OF]-(:Nomen)
        } THEN [(m)-[:HAS_INCIPIT_TITLE]->(:Nomen)<-[:ALTERNATIVE_OF]-(an:Nomen) |
            apoc.map.fromPairs([(an)-[:HAS_LOCALIZATION]->(lt:LocalizedText)-[r:HAS_LANGUAGE]->(l:Language) |
                [coalesce(r.bcp47, l.code), lt.text]])] ELSE null END,
        text_id: e.id
    } as edition
    """

    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (m:Edition {{id: $edition_id}})-[:EDITION_OF]->(e:Text)
    {_EDITION_RETURN}
    """

    GET_BY_TEXT_ID_QUERY: LiteralString = f"""
    MATCH (m:Edition)-[:EDITION_OF]->(e:Text {{id: $text_id}})
    WHERE $edition_type IS NULL OR EXISTS {{ (m)-[:HAS_TYPE]->(:EditionType {{name: $edition_type}}) }}
    {_EDITION_RETURN}
    """

    GET_RELATED_QUERY: LiteralString = f"""
    CALL () {{
        MATCH (:Edition {{id: $edition_id}})
              -[:HAS_SEGMENTATION]->(:Segmentation)
              <-[:SEGMENT_OF]-(:Segment)
              -[:ALIGNED_TO]-(:Segment)
              -[:SEGMENT_OF]->(:Segmentation)
              <-[:HAS_SEGMENTATION]-(m:Edition)
        WHERE m.id <> $edition_id
        RETURN m
        UNION
        MATCH (:Edition {{id: $edition_id}})-[:EDITION_OF]->(:Text)
              -[:TRANSLATION_OF|:COMMENTARY_OF]-(:Text)<-[:EDITION_OF]-(m:Edition)
        WHERE m.id <> $edition_id
        RETURN m
    }}
    MATCH (m)-[:EDITION_OF]->(e:Text)
    WITH DISTINCT m, e
    {_EDITION_RETURN}
    """

    CREATE_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})
    OPTIONAL MATCH (it:Nomen {id: $incipit_nomen_id})
    MERGE (mt:EditionType {name: $type})
    CREATE (m:Edition {
        id: $edition_id, bdrc: $bdrc, wiki: $wiki, colophon: $colophon, content_length: $content_length,
        content_key: $content_key, revision: 0
    })
    WITH m, e, mt, it
    CREATE (m)-[:EDITION_OF]->(e), (m)-[:HAS_TYPE]->(mt)
    CALL (*) { WHEN it IS NOT NULL THEN { CREATE (m)-[:HAS_INCIPIT_TITLE]->(it) } }
    CALL (*) { WHEN $source IS NOT NULL THEN { MERGE (s:Source {name: $source}) CREATE (m)-[:HAS_SOURCE]->(s) } }
    RETURN m.id AS edition_id
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get(self, edition_id: str) -> EditionOutput:
        async def read(tx: AsyncManagedTransaction) -> EditionOutput:
            result = await tx.run(
                EditionDatabase.GET_BY_ID_QUERY,
                edition_id=edition_id,
            )
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Edition '{edition_id}' not found")
            return EditionOutput.model_validate(record["edition"])

        async with self.session as session:
            return await session.execute_read(read)

    async def get_all(self, text_id: str, edition_type: EditionType | None = None) -> list[EditionOutput]:
        async with self.session as session:
            return await session.execute_read(EditionDatabase.get_all_with_transaction, text_id, edition_type)

    @staticmethod
    async def get_all_with_transaction(
        tx: AsyncManagedTransaction, text_id: str, edition_type: EditionType | None = None
    ) -> list[EditionOutput]:
        result = await tx.run(
            EditionDatabase.GET_BY_TEXT_ID_QUERY,
            text_id=text_id,
            edition_type=edition_type.value if edition_type else None,
        )
        records = await result.data()
        return [EditionOutput.model_validate(record["edition"]) for record in records]

    async def get_related(self, edition_id: str) -> list[EditionOutput]:
        """Find all editions related through alignment or text relationships."""

        async def read(tx: AsyncManagedTransaction) -> list[EditionOutput]:
            result = await tx.run(EditionDatabase.GET_RELATED_QUERY, edition_id=edition_id)
            return [EditionOutput.model_validate(record["edition"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def create(
        self,
        edition: EditionInput,
        edition_id: str,
        text_id: str,
        content_length: int,
        text: TextInput | None = None,
        pagination: PaginationInput | None = None,
        segmentation: SegmentationInput | None = None,
        content_key: str | None = None,
    ) -> str:
        async def transaction_function(tx: AsyncManagedTransaction) -> str:
            if text:
                await TextDatabase.create_with_transaction(tx, text, text_id)

            created_edition_id = await self.create_with_transaction(
                tx, edition, text_id, edition_id, content_length, content_key
            )

            if segmentation is not None:
                await SegmentationDatabase.add_with_transaction(tx, created_edition_id, segmentation)

            if pagination is not None:
                await PaginationDatabase.add_with_transaction(tx, created_edition_id, pagination)

            return created_edition_id

        async with self.session as session:
            return await session.execute_write(transaction_function)

    async def delete(self, edition_id: str) -> None:
        async with self.session as session:
            await session.execute_write(self.delete_with_transaction, edition_id)

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, edition_id: str) -> None:
        await SegmentationDatabase.delete_by_edition_with_transaction(tx, edition_id)
        for query in (
            PaginationDatabase.DELETE_ALL_QUERY,
            TableOfContentsDatabase.DELETE_ALL_QUERY,
            BibliographicDatabase.DELETE_ALL_QUERY,
            NoteDatabase.DELETE_ALL_QUERY,
            MarkDatabase.DELETE_ALL_QUERY,
            RecordingDatabase.DELETE_ALL_QUERY,
            EditionDatabase.DELETE_QUERY,
        ):
            await tx.run(query, edition_id=edition_id)

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction,
        edition: EditionInput,
        text_id: str,
        edition_id: str,
        content_length: int,
        content_key: str | None = None,
    ) -> str:
        await lock_nodes(tx, "Text", [text_id])
        await EditionDatabase._validate_create(tx, edition, text_id)

        incipit_nomen_id = None
        if edition.incipit_title:
            alt_incipit_data = [alt.root for alt in edition.alt_incipit_titles] if edition.alt_incipit_titles else None
            incipit_nomen_id = await NomenDatabase.create_with_transaction(
                tx, edition.incipit_title.root, alt_incipit_data
            )

        result = await tx.run(
            EditionDatabase.CREATE_QUERY,
            edition_id=edition_id,
            text_id=text_id,
            bdrc=edition.bdrc,
            wiki=edition.wiki,
            type=edition.type.value if edition.type else None,
            colophon=edition.colophon,
            incipit_nomen_id=incipit_nomen_id,
            source=edition.source,
            content_length=content_length,
            content_key=content_key or f"base_texts/{text_id}/{edition_id}.txt",
        )

        record = await result.single(strict=True)
        return str(record["edition_id"])

    @staticmethod
    async def _validate_create(
        tx: AsyncManagedTransaction,
        edition: EditionInput,
        text_id: str,
    ) -> None:
        await DatabaseValidator.validate_text_exists(tx, text_id)

        if edition.type == EditionType.CRITICAL:
            await EditionDatabase._validate_no_critical_exists(tx, text_id)

    @staticmethod
    async def _validate_no_critical_exists(tx: AsyncManagedTransaction, text_id: str) -> None:
        """Ensure only one critical edition exists for an text."""
        editions = await EditionDatabase.get_all_with_transaction(tx, text_id, EditionType.CRITICAL)
        if editions:
            raise DataValidationError("Critical edition already present for this text")
