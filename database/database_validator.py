import logging
from collections.abc import Sequence
from typing import LiteralString

from neo4j import AsyncManagedTransaction

from exceptions import DataNotFoundError, DataValidationError, ForbiddenError, InvalidRequestError
from models.contribution import ContributionInputItem, PersonContributionInput
from models.text import TextInput

logger = logging.getLogger(__name__)

_MISSING_PERSON_IDS_QUERY: LiteralString = """
MATCH (p:Person) WHERE p.id IN $references
WITH collect(DISTINCT p.id) AS found
RETURN [x IN $references WHERE NOT x IN found] AS missing
"""

_MISSING_PERSON_BDRC_IDS_QUERY: LiteralString = """
MATCH (p:Person) WHERE p.bdrc IN $references
WITH collect(DISTINCT p.bdrc) AS found
RETURN [x IN $references WHERE NOT x IN found] AS missing
"""


class DatabaseValidator:
    def __init__(self) -> None:
        pass

    @staticmethod
    async def validate_contribution_references(
        tx: AsyncManagedTransaction, contributions: Sequence[ContributionInputItem]
    ) -> None:
        persons = [contrib for contrib in contributions if isinstance(contrib, PersonContributionInput)]
        ids = [person.id for person in persons if person.id]
        bdrc_ids = [person.bdrc_id for person in persons if person.bdrc_id]

        checks: tuple[tuple[LiteralString, str, list[str]], ...] = (
            (_MISSING_PERSON_IDS_QUERY, "persons", ids),
            (_MISSING_PERSON_BDRC_IDS_QUERY, "person BDRC IDs", bdrc_ids),
        )

        for query, label, references in checks:
            if not references:
                continue

            result = await tx.run(query, references=references)
            record = await result.single()
            missing = record["missing"] if record else []

            if missing:
                raise DataValidationError(f"Referenced {label} do not exist: {', '.join(missing)}")

    @staticmethod
    async def validate_text_creation(tx: AsyncManagedTransaction, text: TextInput) -> None:
        await DatabaseValidator.validate_text_title_unique(tx, dict(text.title.root))

        await DatabaseValidator.validate_contribution_references(tx, text.contributions)

    @staticmethod
    async def validate_text_exists(tx: AsyncManagedTransaction, text_id: str) -> None:
        query = """
        RETURN EXISTS { (e:Text {id: $text_id}) } AS exists
        """

        result = await tx.run(query, text_id=text_id)
        record = await result.single()

        if not record or not record["exists"]:
            raise DataValidationError(f"Text {text_id} does not exist. Cannot create edition for non-existent text.")

    @staticmethod
    async def validate_edition_exists(tx: AsyncManagedTransaction, edition_id: str) -> None:
        query = """
        RETURN EXISTS { (m:Edition {id: $edition_id}) } AS exists
        """

        result = await tx.run(query, edition_id=edition_id)
        record = await result.single()

        if not record or not record["exists"]:
            raise DataNotFoundError(f"Edition with ID '{edition_id}' not found")

    @staticmethod
    async def validate_language_code_exists(tx: AsyncManagedTransaction, language_code: str) -> None:
        """Validate that a given base language code exists.

        Uses direct pattern matching for efficiency, only collecting all codes on failure.
        Raises InvalidRequest with the available codes listed if not found.
        """
        query = """
        OPTIONAL MATCH (l:Language {code: $code})
        RETURN l IS NOT NULL AS exists,
               CASE WHEN l IS NULL
                 THEN COLLECT {
                   MATCH (lang:Language)
                   WITH lang ORDER BY lang.code
                   RETURN lang.code
                 }
                 ELSE null
               END AS codes
        """

        result = await tx.run(query, code=language_code)
        record = await result.single()

        if not record or not record["exists"]:
            codes = record["codes"] if record else []
            if not codes:
                raise InvalidRequestError(f"Language '{language_code}' is not present in Neo4j. No languages found.")
            raise InvalidRequestError(
                f"Language '{language_code}' is not present in Neo4j. Available languages: {', '.join(codes)}"
            )

    @staticmethod
    async def validate_language_codes_exist(tx: AsyncManagedTransaction, language_codes: list[str]) -> None:
        """Validate that all given base language codes exist. Raises InvalidRequest listing missing and available."""
        query = """
        UNWIND $codes_to_check AS code
        OPTIONAL MATCH (l:Language {code: code})
        WITH collect(DISTINCT CASE WHEN l IS NULL THEN code END) AS maybe_missing
        WITH [code IN maybe_missing WHERE code IS NOT NULL] AS missing
        RETURN missing,
               CASE WHEN size(missing) > 0
                 THEN COLLECT {
                   MATCH (lang:Language)
                   WITH lang ORDER BY lang.code
                   RETURN lang.code
                 }
                 ELSE []
               END AS codes
        """
        result = await tx.run(query, codes_to_check=[c.lower() for c in language_codes])
        record = await result.single()
        if not record:
            raise InvalidRequestError("No languages found in Neo4j database")
        missing = [c for c in (record["missing"] or []) if c]
        if missing:
            raise InvalidRequestError(
                f"Languages {', '.join(missing)} are not present in Neo4j. "
                f"Available languages: {', '.join(record['codes'])}"
            )

    @staticmethod
    async def validate_category_exists(
        tx: AsyncManagedTransaction, category_id: str, application: str | None = None
    ) -> None:
        """Validate that a category with the given ID exists.

        Raises DataValidationError if the category does not exist.
        """
        query = """
        MATCH (c:Category {id: $category_id})-[:BELONGS_TO]->(app:Application)
        RETURN app.id AS application
        """

        result = await tx.run(query, category_id=category_id)
        record = await result.single()

        if not record:
            raise DataValidationError(
                f"Category with ID '{category_id}' does not exist. Please provide a valid category_id."
            )
        if application is not None and record["application"] != application:
            raise ForbiddenError("Category belongs to another application")

    @staticmethod
    async def validate_tags_exist(
        tx: AsyncManagedTransaction, tag_ids: list[str], application: str | None = None
    ) -> None:
        """Validate that all given tag IDs exist. Raises DataValidationError listing missing IDs."""
        if not tag_ids:
            return

        query = """
        MATCH (t:Tag) WHERE t.id IN $tag_ids
        WITH collect(DISTINCT t) AS tags
        RETURN [x IN $tag_ids WHERE NOT x IN [t IN tags | t.id]] AS missing_tags,
               [t IN tags WHERE $application IS NOT NULL AND NOT
                   (t)-[:BELONGS_TO]->(:Application {id: $application}) | t.id] AS foreign_tags
        """

        result = await tx.run(query, tag_ids=tag_ids, application=application)
        record = await result.single()
        missing_tags = record["missing_tags"] if record else []

        if missing_tags:
            raise DataValidationError(f"Referenced tags do not exist: {', '.join(missing_tags)}")
        if record and record["foreign_tags"]:
            raise ForbiddenError("Tags belong to another application")

    @staticmethod
    async def validate_text_title_unique(tx: AsyncManagedTransaction, title: dict[str, str]) -> None:
        """Ensure no text exists with the same title text and language combination."""
        if not title:
            return

        query = """
        UNWIND $titles AS item
        MATCH (:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText {text: item.text})
            -[rel:HAS_LANGUAGE]->(lang:Language)
        WHERE toLower(coalesce(rel.bcp47, lang.code)) = toLower(item.lang)
        RETURN item.text AS text, item.lang AS lang
        LIMIT 1
        """

        titles_list = [{"text": text, "lang": lang} for lang, text in title.items()]
        result = await tx.run(query, titles=titles_list)
        record = await result.single()

        if record:
            raise DataValidationError(
                f"Text with title '{record['text']}' in language '{record['lang']}' already exists"
            )
