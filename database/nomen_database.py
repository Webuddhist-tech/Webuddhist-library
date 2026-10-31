from typing import TYPE_CHECKING, LiteralString

from identifier import generate_id

from .database_validator import DatabaseValidator

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


class NomenDatabase:
    CREATE_MANY_QUERY: LiteralString = """
    UNWIND $nomens AS data
    CREATE (n:Nomen {id: data.id})
    CALL (n, data) {
        UNWIND data.localizations AS item
        MATCH (l:Language {code: item.base})
        CREATE (n)-[:HAS_LOCALIZATION]->(:LocalizedText {text: item.text})
            -[:HAS_LANGUAGE {bcp47: item.language}]->(l)
    }
    RETURN n.id AS id
    """

    LINK_ALTERNATIVES_QUERY: LiteralString = """
    MATCH (primary:Nomen {id: $primary_id})
    UNWIND $alternative_ids AS alternative_id
    MATCH (alternative:Nomen {id: alternative_id})
    CREATE (alternative)-[:ALTERNATIVE_OF]->(primary)
    FINISH
    """

    @staticmethod
    async def create_many_with_transaction(tx: AsyncManagedTransaction, nomens: dict[str, dict[str, str]]) -> None:
        await DatabaseValidator.validate_language_codes_exist(
            tx, sorted({language.split("-")[0].lower() for texts in nomens.values() for language in texts})
        )
        rows = [
            {
                "id": nomen_id,
                "localizations": [
                    {"base": language.split("-")[0].lower(), "language": language, "text": text}
                    for language, text in texts.items()
                ],
            }
            for nomen_id, texts in nomens.items()
        ]
        result = await tx.run(NomenDatabase.CREATE_MANY_QUERY, nomens=rows)
        if {record["id"] for record in await result.data()} != set(nomens):
            raise RuntimeError("Incomplete nomen creation")

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction, primary_text: dict[str, str], alternative_texts: list[dict[str, str]] | None = None
    ) -> str:
        primary_id = generate_id()
        alternatives = {generate_id(): texts for texts in alternative_texts or []}
        await NomenDatabase.create_many_with_transaction(tx, {primary_id: primary_text, **alternatives})
        if alternatives:
            result = await tx.run(
                NomenDatabase.LINK_ALTERNATIVES_QUERY,
                primary_id=primary_id,
                alternative_ids=list(alternatives),
            )
            await result.consume()
        return primary_id
