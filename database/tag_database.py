from typing import TYPE_CHECKING, LiteralString

from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from identifier import generate_id
from models.tag import TagOutput

from .database_validator import DatabaseValidator
from .locking import lock_nodes
from .nomen_database import NomenDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from models.tag import TagInput

    from .database import Database


class TagDatabase:
    GET_ALL_QUERY: LiteralString = """
    MATCH (t:Tag)-[:BELONGS_TO]->(:Application {id: $application})
    RETURN {
        id: t.id,
        title: apoc.map.fromPairs([(t)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(l:Language) | [coalesce(r.bcp47, l.code), lt.text]]),
        description: CASE WHEN EXISTS {
            (t)-[:HAS_DESCRIPTION]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        } THEN apoc.map.fromPairs([(t)-[:HAS_DESCRIPTION]->(dn:Nomen)-[:HAS_LOCALIZATION]->(dlt:LocalizedText)
            -[r:HAS_LANGUAGE]->(dl:Language) | [coalesce(r.bcp47, dl.code), dlt.text]]) ELSE null END
    } AS tag
    """

    CREATE_QUERY: LiteralString = """
        MATCH (n:Nomen {id: $nomen_id})
        MATCH (app:Application {id: $application})
        CREATE (t:Tag {id: $tag_id})
        CREATE (t)-[:HAS_TITLE]->(n)
        CREATE (t)-[:BELONGS_TO]->(app)
        WITH t
        OPTIONAL MATCH (desc_nomen:Nomen {id: $description_nomen_id})
        WITH t, desc_nomen
        CALL (*) { WHEN desc_nomen IS NOT NULL THEN { CREATE (t)-[:HAS_DESCRIPTION]->(desc_nomen) } }
        RETURN t.id AS tag_id
    """

    FIND_EXISTING_QUERY: LiteralString = """
        MATCH (app:Application {id: $application})
        UNWIND $titles AS title
        MATCH (t:Tag)-[:BELONGS_TO]->(app:Application {id: $application})
        MATCH (t)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(lang:Language)
        WHERE toLower(coalesce(r.bcp47, lang.code)) = toLower(title.language)
          AND toLower(lt.text) = toLower(title.text)
        RETURN title.language AS language, title.text AS title_text, t.id AS tag_id
        LIMIT 1
    """

    DELETE_QUERY: LiteralString = """
        MATCH (t:Tag {id: $tag_id})-[:BELONGS_TO]->(app:Application {id: $application})
        WITH t, t.id AS deleted_tag_id
        OPTIONAL MATCH (t)-[:HAS_TITLE]->(title_nomen:Nomen)
        OPTIONAL MATCH (title_nomen)-[:HAS_LOCALIZATION]->(title_lt:LocalizedText)
        OPTIONAL MATCH (t)-[:HAS_DESCRIPTION]->(desc_nomen:Nomen)
        OPTIONAL MATCH (desc_nomen)-[:HAS_LOCALIZATION]->(desc_lt:LocalizedText)
        DETACH DELETE t, title_nomen, title_lt, desc_nomen, desc_lt
        RETURN deleted_tag_id
    """

    TAG_WORK_QUERY: LiteralString = """
        MATCH (w:Work {id: $work_id})
        MATCH (t:Tag {id: $tag_id})
        WHERE $application IS NULL OR (t)-[:BELONGS_TO]->(:Application {id: $application})
        MERGE (w)-[:HAS_TAG]->(t)
        RETURN w.id AS work_id
    """

    SET_WORK_TAGS_QUERY: LiteralString = """
        MATCH (w:Work {id: $work_id})
        OPTIONAL MATCH (w)-[old:HAS_TAG]->(tag:Tag)
        WHERE $replace AND ($application IS NULL OR
            (tag)-[:BELONGS_TO]->(:Application {id: $application}))
        DELETE old
        WITH DISTINCT w
        UNWIND $tag_ids AS tag_id
        MATCH (tag:Tag {id: tag_id})
        MERGE (w)-[:HAS_TAG]->(tag)
    """

    UNTAG_WORK_QUERY: LiteralString = """
        MATCH (w:Work {id: $work_id})
        MATCH (w)-[r:HAS_TAG]->(t:Tag {id: $tag_id})
        WHERE $application IS NULL OR (t)-[:BELONGS_TO]->(:Application {id: $application})
        DELETE r
        RETURN w.id AS work_id
    """

    TAG_SEGMENT_QUERY: LiteralString = """
        MATCH (s:Segment {id: $segment_id})
        MATCH (t:Tag {id: $tag_id})
        WHERE $application IS NULL OR (t)-[:BELONGS_TO]->(:Application {id: $application})
        MERGE (s)-[:HAS_TAG]->(t)
        RETURN s.id AS segment_id
    """

    UNTAG_SEGMENT_QUERY: LiteralString = """
        MATCH (s:Segment {id: $segment_id})-[r:HAS_TAG]->(t:Tag {id: $tag_id})
        WHERE $application IS NULL OR (t)-[:BELONGS_TO]->(:Application {id: $application})
        DELETE r
        RETURN s.id AS segment_id
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get_all(self, application: str) -> list[TagOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[TagOutput]:
            result = await tx.run(TagDatabase.GET_ALL_QUERY, application=application)
            return [TagOutput.model_validate(record["tag"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def create(self, tag: TagInput, application: str) -> str:
        async def create_transaction(tx: AsyncManagedTransaction) -> str:
            await self._validate_not_exists_tx(tx, application, tag.title.root)

            tag_id = generate_id()
            nomen_id = await NomenDatabase.create_with_transaction(tx, tag.title.root, None)
            description_nomen_id = None
            if tag.description is not None:
                description_nomen_id = await NomenDatabase.create_with_transaction(tx, tag.description.root, None)

            result = await tx.run(
                TagDatabase.CREATE_QUERY,
                tag_id=tag_id,
                application=application,
                nomen_id=nomen_id,
                description_nomen_id=description_nomen_id,
            )
            record = await result.single(strict=True)
            return record["tag_id"]

        async with self.session as session:
            return str(await session.execute_write(create_transaction))

    async def delete(self, tag_id: str, application: str) -> list[str]:
        async def write(tx: AsyncManagedTransaction) -> list[str]:
            async def work_ids() -> set[str]:
                result = await tx.run("MATCH (:Tag {id: $id})<-[:HAS_TAG]-(w:Work) RETURN w.id AS id", id=tag_id)
                return {record["id"] for record in await result.data()}

            works = await work_ids()
            # Match tag writers' Work -> Tag order, then reject newly attached Works.
            await lock_nodes(tx, "Work", sorted(works))
            await lock_nodes(tx, "Tag", [tag_id])
            if not await work_ids() <= works:
                raise DataConflictError("Tag attachments changed while acquiring locks; retry the operation")
            result = await tx.run(TagDatabase.DELETE_QUERY, tag_id=tag_id, application=application)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Tag with ID '{tag_id}' not found in application '{application}'")

            return sorted(works)

        async with self.session as session:
            return await session.execute_write(write)

    async def tag_work(self, work_id: str, tag_id: str, application: str | None = None) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await lock_nodes(tx, "Work", [work_id])
            await lock_nodes(tx, "Tag", [tag_id])
            result = await tx.run(TagDatabase.TAG_WORK_QUERY, work_id=work_id, tag_id=tag_id, application=application)
            if not await result.single():
                raise DataNotFoundError(f"Work '{work_id}' or Tag '{tag_id}' not found")

        async with self.session as session:
            await session.execute_write(write)

    async def untag_work(self, work_id: str, tag_id: str, application: str | None = None) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await lock_nodes(tx, "Work", [work_id])
            await lock_nodes(tx, "Tag", [tag_id])
            result = await tx.run(TagDatabase.UNTAG_WORK_QUERY, work_id=work_id, tag_id=tag_id, application=application)
            if not await result.single():
                raise DataNotFoundError(f"Tag '{tag_id}' is not attached to Work '{work_id}'")

        async with self.session as session:
            await session.execute_write(write)

    async def tag_segment(self, segment_id: str, tag_id: str, application: str | None = None) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            result = await tx.run(
                TagDatabase.TAG_SEGMENT_QUERY, segment_id=segment_id, tag_id=tag_id, application=application
            )
            if not await result.single():
                raise DataNotFoundError(f"Segment '{segment_id}' or Tag '{tag_id}' not found")

        async with self.session as session:
            await session.execute_write(write)

    async def untag_segment(self, segment_id: str, tag_id: str, application: str | None = None) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            result = await tx.run(
                TagDatabase.UNTAG_SEGMENT_QUERY, segment_id=segment_id, tag_id=tag_id, application=application
            )
            if not await result.single():
                raise DataNotFoundError(f"Tag '{tag_id}' is not attached to Segment '{segment_id}'")

        async with self.session as session:
            await session.execute_write(write)

    @staticmethod
    async def set_work_tags(
        tx: AsyncManagedTransaction,
        work_id: str,
        tag_ids: list[str],
        *,
        application: str | None = None,
        replace: bool = False,
    ) -> None:
        """Caller created the Work in this transaction or already holds its write lock."""
        await lock_nodes(tx, "Tag", tag_ids)
        await DatabaseValidator.validate_tags_exist(tx, tag_ids, application)
        await tx.run(
            TagDatabase.SET_WORK_TAGS_QUERY,
            work_id=work_id,
            tag_ids=tag_ids,
            application=application,
            replace=replace,
        )

    async def _validate_not_exists_tx(
        self, tx: AsyncManagedTransaction, application: str, title: dict[str, str]
    ) -> None:
        await lock_nodes(tx, "Application", [application])
        result = await tx.run(
            TagDatabase.FIND_EXISTING_QUERY,
            application=application,
            titles=[{"language": language, "text": title_text} for language, title_text in title.items()],
        )
        record = await result.single()
        if record:
            raise DataValidationError(
                f"Tag with title '{record['title_text']}' in language '{record['language']}' "
                f"already exists for application '{application}'"
            )
