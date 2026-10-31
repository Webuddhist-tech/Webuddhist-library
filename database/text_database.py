from typing import TYPE_CHECKING, Any, LiteralString

from neo4j.exceptions import ConstraintError

from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from identifier import generate_id
from models.requests import TextFilter
from models.text import TextBase, TextInput, TextOutput, TextPatch

from .contribution_database import ContributionDatabase, contributions_return
from .database_validator import DatabaseValidator
from .nomen_database import NomenDatabase
from .tag_database import TagDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession, Record

    from .database import Database

TEXT_LABEL: LiteralString = "Text"


class TextDatabase:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    _TEXT_RETURN: LiteralString = (
        """
    {
        id: e.id,
        bdrc: e.bdrc,
        wiki: e.wiki,
        commentary_of: [(e)-[:COMMENTARY_OF]->(c_target:Text) | c_target.id][0],
        translation_of: [(e)-[:TRANSLATION_OF]->(t_target:Text) | t_target.id][0],
        commentaries: [(e)<-[:COMMENTARY_OF]-(c_child:Text) | c_child.id],
        translations: [(e)<-[:TRANSLATION_OF]-(t_child:Text) | t_child.id],
        contributions: """
        + contributions_return("e")
        + """,
        date: e.date,
        title: apoc.map.fromPairs([(e)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->
            (lt:LocalizedText)-[r:HAS_LANGUAGE]->(lang:Language)
            WHERE NOT EXISTS { (n)-[:ALTERNATIVE_OF]->(:Nomen) } |
            [coalesce(r.bcp47, lang.code), lt.text]]),
        alt_titles: [(e)-[:HAS_TITLE]->(:Nomen)<-[:ALTERNATIVE_OF]-(an:Nomen) |
            apoc.map.fromPairs([(an)-[:HAS_LOCALIZATION]->(lt:LocalizedText)-[r:HAS_LANGUAGE]->(lang:Language) |
                [coalesce(r.bcp47, lang.code), lt.text]])],
        language: [(e)-[r:HAS_LANGUAGE]->(lang:Language) | coalesce(r.bcp47, lang.code)][0],
        category_id: [(e)-[:TEXT_OF]->(work:Work)-[:HAS_CATEGORY]->(cat:Category) | cat.id][0],
        license: coalesce([(e)-[:HAS_LICENSE]->(license:LicenseType) | license.name][0], "public"),
        editions: [(e)<-[:EDITION_OF]-(m:Edition) | m.id],
        tag_ids: [(e)-[:TEXT_OF]->(w:Work)-[:HAS_TAG]->(t:Tag)
            WHERE ($application IS NULL OR (t)-[:BELONGS_TO]->(:Application {id: $application})) | t.id]
    } AS text
    """
    )

    GET_QUERY: LiteralString = f"""
    MATCH (e:Text {{id: $id}})-[:TEXT_OF]->(w:Work)
    RETURN {_TEXT_RETURN}, w.id AS work_id
    """

    GET_BY_IDS_QUERY: LiteralString = f"""
    UNWIND range(0, size($ids) - 1) AS idx
    MATCH (e:Text {{id: $ids[idx]}})
    WITH e, idx
    ORDER BY idx
    RETURN {_TEXT_RETURN}
    """

    GET_ALL_QUERY: LiteralString = f"""
    CALL () {{
        WHEN $bdrc IS NOT NULL THEN {{ MATCH (e:Text {{bdrc: $bdrc}}) RETURN e }}
        WHEN $wiki IS NOT NULL THEN {{ MATCH (e:Text {{wiki: $wiki}}) RETURN e }}
        WHEN $author_id IS NOT NULL THEN {{
            MATCH (e:Text)-[:HAS_CONTRIBUTION]->(:Contribution)-[:BY]->(:Person {{id: $author_id}})
            RETURN DISTINCT e
        }}
        WHEN $category_id IS NOT NULL THEN {{
            MATCH (e:Text)-[:TEXT_OF]->(:Work)-[:HAS_CATEGORY]->(:Category {{id: $category_id}}) RETURN e
        }}
        WHEN size($tag_ids) > 0 THEN {{
            MATCH (e:Text)-[:TEXT_OF]->(w:Work)
            WHERE CASE $tag_id_match
                WHEN 'all' THEN ALL(required_tag_id IN $tag_ids WHERE (w)-[:HAS_TAG]->(:Tag {{id: required_tag_id}}))
                ELSE ANY(required_tag_id IN $tag_ids WHERE (w)-[:HAS_TAG]->(:Tag {{id: required_tag_id}}))
            END
            RETURN e
        }}
        WHEN $language IS NOT NULL THEN {{
            MATCH (e:Text)-[:HAS_LANGUAGE]->(:Language {{code: $language_base}}) RETURN e
        }}
        ELSE {{ MATCH (e:Text) RETURN e }}
    }}
    WITH e
    WHERE ($language IS NULL OR EXISTS {{
          (e)-[r:HAS_LANGUAGE]->(lang:Language {{code: $language_base}})
          WHERE NOT $language CONTAINS '-' OR toLower(coalesce(r.bcp47, lang.code)) = $language
      }})
      AND ($category_id IS NULL OR (e)-[:TEXT_OF]->(:Work)-[:HAS_CATEGORY]->(:Category {{id: $category_id}}))
      AND ($author_id IS NULL OR EXISTS {{
          (e)-[:HAS_CONTRIBUTION]->(:Contribution)-[:BY]->(:Person {{id: $author_id}})
      }})
      AND (
        size($tag_ids) = 0
        OR CASE $tag_id_match
            WHEN 'all' THEN ALL(
                required_tag_id IN $tag_ids
                WHERE (e)-[:TEXT_OF]->(:Work)-[:HAS_TAG]->(:Tag {{id: required_tag_id}})
            )
            ELSE ANY(
                required_tag_id IN $tag_ids
                WHERE (e)-[:TEXT_OF]->(:Work)-[:HAS_TAG]->(:Tag {{id: required_tag_id}})
            )
        END
      )
      AND ($wiki IS NULL OR e.wiki = $wiki)
    ORDER BY e.id SKIP $offset LIMIT $limit
    RETURN {_TEXT_RETURN}
    """

    UPDATE_LICENSE_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})
    OPTIONAL MATCH (e)-[r:HAS_LICENSE]->()
    DELETE r
    WITH e
    MATCH (license:LicenseType {name: $license})
    MERGE (e)-[:HAS_LICENSE]->(license)
    RETURN e.id as text_id
    """

    UPDATE_LANGUAGE_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})
    OPTIONAL MATCH (e)-[r:HAS_LANGUAGE]->()
    DELETE r
    WITH e
    MATCH (lang:Language {code: $language_code})
    MERGE (e)-[:HAS_LANGUAGE {bcp47: $bcp47_tag}]->(lang)
    RETURN e.id as text_id
    """

    UPDATE_CATEGORY_QUERY: LiteralString = """
    MATCH (w:Work {id: $work_id}), (cat:Category {id: $category_id})
    OPTIONAL MATCH (w)-[r:HAS_CATEGORY]->()
    DELETE r
    WITH w, cat
    MERGE (w)-[:HAS_CATEGORY]->(cat)
    RETURN w.id as work_id
    """

    UPDATE_PROPERTIES_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})
    SET e += $properties
    RETURN e.id as text_id
    """

    DELETE_TITLE_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})-[:HAS_TITLE]->(n:Nomen)
    OPTIONAL MATCH (n)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    OPTIONAL MATCH (n)<-[:ALTERNATIVE_OF]-(alt:Nomen)-[:HAS_LOCALIZATION]->(alt_lt:LocalizedText)
    DETACH DELETE n, lt, alt, alt_lt
    FINISH
    """

    LINK_TITLE_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})
    MATCH (n:Nomen {id: $nomen_id})
    CREATE (e)-[:HAS_TITLE]->(n)
    FINISH
    """

    _CREATE_TEXT_LINKS: LiteralString = """
    MATCH (n:Nomen {id: $title_nomen_id}), (l:Language {code: $language_code})
    MATCH (license:LicenseType {name: $license})
    MERGE (e)-[:HAS_LANGUAGE {bcp47: $bcp47_tag}]->(l)
    MERGE (e)-[:HAS_TITLE]->(n)
    MERGE (e)-[:HAS_LICENSE]->(license)
    RETURN e.id AS text_id, w.id AS work_id
    """

    CREATE_STANDALONE_QUERY: LiteralString = f"""
    CREATE (w:Work {{id: $work_id}})
    CREATE (e:Text {{id: $text_id, bdrc: $bdrc, wiki: $wiki, date: $date}})
    MERGE (e)-[:TEXT_OF {{original: $original}}]->(w)
    {_CREATE_TEXT_LINKS}
    """

    CREATE_TRANSLATION_QUERY: LiteralString = f"""
    MATCH (target:Text {{id: $target_id}})-[:TEXT_OF]->(w:Work)
    CREATE (e:Text {{id: $text_id, bdrc: $bdrc, wiki: $wiki, date: $date}})
    MERGE (e)-[:TEXT_OF {{original: false}}]->(w)
    MERGE (e)-[:TRANSLATION_OF]->(target)
    {_CREATE_TEXT_LINKS}
    """

    CREATE_COMMENTARY_QUERY: LiteralString = f"""
    MATCH (target:Text {{id: $target_id}})
    CREATE (w:Work {{id: $work_id}})
    CREATE (e:Text {{id: $text_id, bdrc: $bdrc, wiki: $wiki, date: $date}})
    MERGE (e)-[:COMMENTARY_OF]->(target)
    MERGE (e)-[:TEXT_OF {{original: true}}]->(w)
    {_CREATE_TEXT_LINKS}
    """

    GET_WORK_ID_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})-[:TEXT_OF]->(w:Work)
    RETURN w.id AS work_id
    """

    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})-[:TEXT_OF]->(w:Work)
    RETURN w.id AS work_id,
           count { (e)<-[:EDITION_OF]-(:Edition) } AS edition_count,
           count { (e)<-[:TRANSLATION_OF]-(:Text) } AS translation_count,
           count { (e)<-[:COMMENTARY_OF]-(:Text) } AS commentary_count
    """

    DELETE_QUERY: LiteralString = """
    MATCH (e:Text {id: $text_id})-[:TEXT_OF]->(w:Work)
    WITH e, w, count { (:Text)-[:TEXT_OF]->(w) } AS work_text_count
    CALL (e) {
        OPTIONAL MATCH (e)-[:HAS_TITLE]->(n:Nomen)
        OPTIONAL MATCH (n)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
        OPTIONAL MATCH (n)<-[:ALTERNATIVE_OF]-(alt:Nomen)-[:HAS_LOCALIZATION]->(alt_lt:LocalizedText)
        DETACH DELETE n, lt, alt, alt_lt
        RETURN count(*) AS title_delete_count
    }
    CALL (e) {
        OPTIONAL MATCH (e)-[:HAS_CONTRIBUTION]->(c:Contribution)
        DETACH DELETE c
        RETURN count(*) AS contribution_delete_count
    }
    WITH e, w, work_text_count
    DETACH DELETE e
    WITH w, work_text_count
    CALL (*) {
        WHEN work_text_count = 1 THEN { DETACH DELETE w }
    }
    RETURN work_text_count = 1 AS work_deleted
    """

    async def get(self, text_id: str, application: str | None = None) -> TextOutput:
        async with self.session as session:
            return await session.execute_read(self.get_with_transaction, text_id, application)

    @staticmethod
    async def get_with_transaction(
        tx: AsyncManagedTransaction, text_id: str, application: str | None = None
    ) -> TextOutput:
        result = await tx.run(TextDatabase.GET_QUERY, id=text_id, application=application)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Text with ID '{text_id}' not found")
        return TextOutput.model_validate(record["text"])

    async def get_by_ids(self, text_ids: list[str], application: str | None = None) -> list[TextOutput]:
        if not text_ids:
            return []

        async def read(tx: AsyncManagedTransaction) -> list[TextOutput]:
            result = await tx.run(TextDatabase.GET_BY_IDS_QUERY, ids=text_ids, application=application)
            return [TextOutput.model_validate(record["text"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def get_work_id(self, text_id: str) -> str:
        async def read(tx: AsyncManagedTransaction) -> str:
            result = await tx.run(TextDatabase.GET_WORK_ID_QUERY, text_id=text_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Text with ID '{text_id}' not found or has no associated Work")
            return record["work_id"]

        async with self.session as session:
            return await session.execute_read(read)

    async def get_all(
        self, offset: int, limit: int, filters: TextFilter | None = None, application: str | None = None
    ) -> list[TextOutput]:
        filters = filters or TextFilter()

        async def read(tx: AsyncManagedTransaction) -> list[TextOutput]:
            if filters.language:
                await DatabaseValidator.validate_language_code_exists(tx, filters.language.split("-")[0])
            result = await tx.run(
                TextDatabase.GET_ALL_QUERY,
                offset=offset,
                limit=limit,
                language=filters.language,
                language_base=filters.language.split("-")[0] if filters.language else None,
                category_id=filters.category_id,
                author_id=filters.author_id,
                tag_ids=filters.tag_ids,
                tag_id_match=filters.tag_id_match,
                bdrc=filters.bdrc,
                wiki=filters.wiki,
                application=application,
            )
            return [TextOutput.model_validate(record["text"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def create(self, text: TextInput, application: str | None = None) -> str:
        try:
            async with self.session as session:
                return await session.execute_write(TextDatabase.create_with_transaction, text, application=application)
        except ConstraintError as e:
            raise DataConflictError(str(e)) from e

    async def delete(self, text_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            result = await tx.run(TextDatabase.DELETE_CHECK_QUERY, text_id=text_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Text with ID '{text_id}' not found")

            blockers = []
            if record["edition_count"]:
                blockers.append(f"{record['edition_count']} edition(s)")
            if record["translation_count"]:
                blockers.append(f"{record['translation_count']} translation(s)")
            if record["commentary_count"]:
                blockers.append(f"{record['commentary_count']} commentary/commentaries")
            if blockers:
                raise DataConflictError(f"Text '{text_id}' cannot be deleted because it has {', '.join(blockers)}")

            await tx.run(TextDatabase.DELETE_QUERY, text_id=text_id)

        async with self.session as session:
            await session.execute_write(write)

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction, text: TextInput, text_id: str | None = None, application: str | None = None
    ) -> str:
        text_id = text_id or generate_id()
        await TextDatabase._validate_related_text(tx, text)

        work_id = generate_id()
        await DatabaseValidator.validate_text_creation(tx, text)
        base_lang_code = text.language.split("-")[0].lower()
        await DatabaseValidator.validate_language_code_exists(tx, base_lang_code)

        alt_titles = [dict(t.root) for t in text.alt_titles] if text.alt_titles else []
        title_nomen_id = await NomenDatabase.create_with_transaction(tx, dict(text.title.root), alt_titles)

        params: dict[str, Any] = {
            "text_id": text_id,
            "bdrc": text.bdrc,
            "wiki": text.wiki,
            "date": text.date,
            "language_code": base_lang_code,
            "bcp47_tag": text.language,
            "title_nomen_id": title_nomen_id,
            "target_id": text.translation_of or text.commentary_of,
            "license": text.license.value,
        }

        if text.commentary_of:
            result = await tx.run(TextDatabase.CREATE_COMMENTARY_QUERY, work_id=work_id, **params)
        elif text.translation_of:
            result = await tx.run(TextDatabase.CREATE_TRANSLATION_QUERY, **params)
        else:
            result = await tx.run(TextDatabase.CREATE_STANDALONE_QUERY, work_id=work_id, original=True, **params)

        record = await result.single(strict=True)
        created_text_id = str(record["text_id"])
        work_id = str(record["work_id"])

        if text.category_id is not None:
            await TextDatabase._set_category(tx, work_id, None, text.category_id, application, None)

        for contribution in text.contributions:
            await ContributionDatabase.create_with_transaction(tx, TEXT_LABEL, created_text_id, contribution)

        if text.tag_ids:
            await TagDatabase.set_work_tags(tx, work_id, text.tag_ids, application=application)

        return created_text_id

    @staticmethod
    async def _get_for_update(tx: AsyncManagedTransaction, text_id: str) -> Record | None:
        await (
            await tx.run(
                """
            MATCH (e:Text {id: $id})-[:TEXT_OF]->(w:Work)
            CALL apoc.lock.nodes([w, e])
            FINISH
        """,
                id=text_id,
            )
        ).consume()
        return await (await tx.run(TextDatabase.GET_QUERY, id=text_id, application=None)).single()

    @staticmethod
    async def _validate_related_text(tx: AsyncManagedTransaction, text: TextInput) -> None:
        target_id = text.translation_of or text.commentary_of
        if not target_id:
            return
        record = await TextDatabase._get_for_update(tx, target_id)
        if not record:
            raise DataNotFoundError(f"Target text '{target_id}' not found")
        target_language = record.data()["text"]["language"]
        if text.translation_of and target_language == text.language:
            raise DataValidationError("Translation must have a different language than the target text")

    async def update(self, text_id: str, patch: TextPatch, application: str | None = None) -> TextOutput:
        async def update_transaction(tx: AsyncManagedTransaction) -> TextOutput:
            record = await TextDatabase._get_for_update(tx, text_id)
            if record is None:
                raise DataNotFoundError(f"Text with ID '{text_id}' not found")
            existing = {key: value for key, value in record["text"].items() if key in TextBase.model_fields}
            updated = TextBase.model_validate(
                existing | patch.model_dump(include=set(TextBase.model_fields), exclude_unset=True)
            )

            properties = patch.model_dump(include={"bdrc", "wiki", "date"}, exclude_unset=True)
            if properties:
                await tx.run(TextDatabase.UPDATE_PROPERTIES_QUERY, text_id=text_id, properties=properties)

            if patch.model_fields_set & {"title", "alt_titles"}:
                await tx.run(TextDatabase.DELETE_TITLE_QUERY, text_id=text_id)
                title_nomen_id = await NomenDatabase.create_with_transaction(
                    tx, updated.title.root, [alt.root for alt in updated.alt_titles or []]
                )
                await tx.run(TextDatabase.LINK_TITLE_QUERY, text_id=text_id, nomen_id=title_nomen_id)

            if patch.license is not None:
                await tx.run(TextDatabase.UPDATE_LICENSE_QUERY, text_id=text_id, license=patch.license.value)

            if patch.language is not None:
                base_lang_code = patch.language.split("-")[0].lower()
                await tx.run(
                    TextDatabase.UPDATE_LANGUAGE_QUERY,
                    text_id=text_id,
                    language_code=base_lang_code,
                    bcp47_tag=patch.language,
                )

            if patch.category_id is not None:
                await self._set_category(
                    tx,
                    record["work_id"],
                    record["text"]["category_id"],
                    patch.category_id,
                    application,
                    patch.expected_category_id,
                )

            if patch.contributions is not None:
                await DatabaseValidator.validate_contribution_references(tx, patch.contributions)
                await ContributionDatabase.delete_all_with_transaction(tx, TEXT_LABEL, text_id)
                for contribution in patch.contributions:
                    await ContributionDatabase.create_with_transaction(tx, TEXT_LABEL, text_id, contribution)

            if patch.tag_ids is not None:
                await TagDatabase.set_work_tags(
                    tx, record["work_id"], patch.tag_ids, application=application, replace=True
                )
            return await self.get_with_transaction(tx, text_id, application)

        async with self.session as session:
            try:
                return await session.execute_write(update_transaction)
            except ConstraintError as e:
                raise DataConflictError(str(e)) from e

    @staticmethod
    async def _set_category(
        tx: AsyncManagedTransaction,
        work_id: str,
        current: str | None,
        category_id: str,
        application: str | None,
        expected: str | None,
    ) -> None:
        if current == category_id:
            return
        if current is not None:
            await DatabaseValidator.validate_category_exists(tx, current, application)
            if expected != current:
                raise DataConflictError("Category changed or expected_category_id was not supplied")
        await DatabaseValidator.validate_category_exists(tx, category_id, application)
        result = await tx.run(TextDatabase.UPDATE_CATEGORY_QUERY, work_id=work_id, category_id=category_id)
        if await result.single() is None:
            raise DataNotFoundError("Work or category no longer exists")
