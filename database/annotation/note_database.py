from typing import TYPE_CHECKING, LiteralString

from database.database_validator import DatabaseValidator
from exceptions import DataNotFoundError

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database.database import Database
from identifier import generate_id
from models.annotation import NoteInput, NoteOutput


class NoteDatabase:
    GET_BY_ID_QUERY: LiteralString = """
    MATCH (span:Span)-[:SPAN_OF]->(n:Note {id: $note_id})
        -[:NOTE_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)
    WHERE span.start < span.end
    RETURN n.id AS id,
           edition.id AS edition_id,
           text.id AS text_id,
           n.text AS text,
           {start: span.start, end: span.end} AS span
    ORDER BY span.start
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = """
    MATCH (edition:Edition {id: $edition_id})<-[:NOTE_OF]-(n:Note)
        -[:HAS_TYPE]->(:NoteType {name: $note_type}),
        (edition)-[:EDITION_OF]->(text:Text)
    MATCH (span:Span)-[:SPAN_OF]->(n)
    WHERE span.start < span.end
    RETURN n.id AS id,
           edition.id AS edition_id,
           text.id AS text_id,
           n.text AS text,
           {start: span.start, end: span.end} AS span
    ORDER BY span.start
    """

    CREATE_QUERY: LiteralString = """
    MATCH (m:Edition {id: $edition_id}), (nt:NoteType {name: $note_type})
    CREATE (span:Span {start: $span_start, end: $span_end})
        -[:SPAN_OF]->(n:Note {id: $note_id, text: $text})
        -[:NOTE_OF]->(m),
        (n)-[:HAS_TYPE]->(nt)
    RETURN n.id AS note_id
    """

    DELETE_QUERY: LiteralString = """
    MATCH (n:Note {id: $note_id})
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(n)
    DETACH DELETE span, n
    FINISH
    """

    DELETE_ALL_QUERY: LiteralString = """
    MATCH (n:Note)-[:NOTE_OF]->(:Edition {id: $edition_id})
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(n)
    DETACH DELETE span, n
    FINISH
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, note_id: str) -> NoteOutput:
        async def read(tx: AsyncManagedTransaction) -> NoteOutput:
            result = await tx.run(NoteDatabase.GET_BY_ID_QUERY, note_id=note_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Note with ID '{note_id}' not found")
            return NoteOutput.model_validate(record)

        async with self._db.get_session() as session:
            return await session.execute_read(read)

    async def get_all(self, edition_id: str, note_type: str = "durchen") -> list[NoteOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[NoteOutput]:
            result = await tx.run(
                NoteDatabase.GET_BY_EDITION_ID_QUERY,
                edition_id=edition_id,
                note_type=note_type,
            )
            return [NoteOutput.model_validate(record) for record in await result.data()]

        async with self._db.get_session() as session:
            return await session.execute_read(read)

    async def add_durchen(self, edition_id: str, note: NoteInput) -> str:
        return (await self.add_durchens(edition_id, [note]))[0]

    async def add_durchens(self, edition_id: str, notes: list[NoteInput]) -> list[str]:
        async def add_all(tx: AsyncManagedTransaction) -> list[str]:
            return [await NoteDatabase.add_with_transaction(tx, edition_id, note, "durchen") for note in notes]

        async with self._db.get_session() as session:
            return await session.execute_write(add_all)

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        note: NoteInput,
        note_type: str,
    ) -> str:
        await DatabaseValidator.validate_edition_spans(tx, edition_id, note.max_end)

        generated_id = generate_id()

        result = await tx.run(
            NoteDatabase.CREATE_QUERY,
            edition_id=edition_id,
            note_id=generated_id,
            text=note.text,
            span_start=note.span.start,
            span_end=note.span.end,
            note_type=note_type,
        )
        record = await result.single(strict=True)
        return str(record["note_id"])

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, note_id: str) -> None:
        await tx.run(NoteDatabase.DELETE_QUERY, note_id=note_id)

    async def delete(self, note_id: str) -> None:
        async with self._db.get_session() as session:
            await session.execute_write(lambda tx: NoteDatabase.delete_with_transaction(tx, note_id))

    @staticmethod
    async def delete_all_with_transaction(
        tx: AsyncManagedTransaction, edition_id: str, _note_type: str = "durchen"
    ) -> None:
        await tx.run(NoteDatabase.DELETE_ALL_QUERY, edition_id=edition_id)
