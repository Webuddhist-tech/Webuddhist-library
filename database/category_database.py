from typing import TYPE_CHECKING, LiteralString

from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from identifier import generate_id
from models.category import CategoryOutput

from .locking import lock_nodes
from .nomen_database import NomenDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from models.category import CategoryInput

    from .database import Database


class CategoryDatabase:
    _CATEGORY_RETURN: LiteralString = """
    {
        id: c.id,
        title: apoc.map.fromPairs([(c)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(l:Language) | [coalesce(r.bcp47, l.code), lt.text]]),
        description: CASE WHEN EXISTS {
            (c)-[:HAS_DESCRIPTION]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        } THEN apoc.map.fromPairs([(c)-[:HAS_DESCRIPTION]->(dn:Nomen)-[:HAS_LOCALIZATION]->(dlt:LocalizedText)
            -[r:HAS_LANGUAGE]->(dl:Language) | [coalesce(r.bcp47, dl.code), dlt.text]]) ELSE null END,
        parent_id: [(c)-[:HAS_PARENT]->(parent:Category) | parent.id][0],
        children: [(child:Category)-[:HAS_PARENT]->(c) | child.id]
    } AS category
    """

    GET_ROOTS_QUERY: LiteralString = f"""
    MATCH (c:Category)-[:BELONGS_TO]->(:Application {{id: $application}})
    WHERE NOT EXISTS {{ (c)-[:HAS_PARENT]->(:Category) }}
    RETURN {_CATEGORY_RETURN}
    """

    GET_CHILDREN_QUERY: LiteralString = f"""
    MATCH (c:Category)-[:HAS_PARENT]->(:Category {{id: $parent_id}})
    WHERE (c)-[:BELONGS_TO]->(:Application {{id: $application}})
    RETURN {_CATEGORY_RETURN}
    """

    CREATE_QUERY: LiteralString = """
        MATCH (n:Nomen {id: $nomen_id})
        MATCH (app:Application {id: $application})
        CREATE (c:Category {id: $category_id})
        CREATE (c)-[:HAS_TITLE]->(n)
        CREATE (c)-[:BELONGS_TO]->(app)
        WITH c, app
        OPTIONAL MATCH (parent:Category {id: $parent_id})-[:BELONGS_TO]->(app)
        OPTIONAL MATCH (desc_nomen:Nomen {id: $description_nomen_id})
        WITH c, parent, desc_nomen
        CALL (*) { WHEN parent IS NOT NULL THEN { CREATE (c)-[:HAS_PARENT]->(parent) } }
        CALL (*) { WHEN desc_nomen IS NOT NULL THEN { CREATE (c)-[:HAS_DESCRIPTION]->(desc_nomen) } }
        RETURN c.id AS category_id
    """

    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (c:Category {{id: $category_id}})-[:BELONGS_TO]->(:Application {{id: $application}})
    RETURN {_CATEGORY_RETURN}
    """

    FIND_EXISTING_QUERY: LiteralString = """
        MATCH (app:Application {id: $application})
        UNWIND $titles AS title
        MATCH (c:Category)-[:BELONGS_TO]->(:Application {id: $application})
        WHERE ($parent_id IS NULL AND NOT EXISTS { (c)-[:HAS_PARENT]->(:Category) })
           OR (c)-[:HAS_PARENT]->(:Category {id: $parent_id})
        MATCH (c)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(lang:Language)
        WHERE toLower(coalesce(r.bcp47, lang.code)) = toLower(title.language)
          AND toLower(lt.text) = toLower(title.text)
        RETURN title.language AS language, title.text AS title_text, c.id AS category_id
        LIMIT 1
    """

    PARENT_EXISTS_QUERY: LiteralString = """
        RETURN EXISTS {
            (:Category {id: $parent_id})-[:BELONGS_TO]->(:Application {id: $application})
        } AS exists
    """

    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (root:Category {id: $category_id})-[:BELONGS_TO]->(app:Application {id: $application})
    OPTIONAL MATCH (c:Category)-[:HAS_PARENT*0..]->(root)
    WHERE (c)-[:BELONGS_TO]->(app)
    OPTIONAL MATCH (w:Work)-[:HAS_CATEGORY]->(c)
    RETURN count(DISTINCT w) AS work_count
    """

    DELETE_QUERY: LiteralString = """
    MATCH (root:Category {id: $category_id})-[:BELONGS_TO]->(app:Application {id: $application})
    MATCH (c:Category)-[:HAS_PARENT*0..]->(root)
    WHERE (c)-[:BELONGS_TO]->(app)
    WITH collect(DISTINCT c) AS categories
    WITH categories, size(categories) AS deleted_count
    UNWIND categories AS c
    OPTIONAL MATCH (c)-[:HAS_TITLE]->(title_nomen:Nomen)
    OPTIONAL MATCH (title_nomen)-[:HAS_LOCALIZATION]->(title_lt:LocalizedText)
    OPTIONAL MATCH (c)-[:HAS_DESCRIPTION]->(desc_nomen:Nomen)
    OPTIONAL MATCH (desc_nomen)-[:HAS_LOCALIZATION]->(desc_lt:LocalizedText)
    DETACH DELETE c, title_nomen, title_lt, desc_nomen, desc_lt
    WITH DISTINCT deleted_count
    RETURN deleted_count
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get_by_id(self, category_id: str, application: str) -> CategoryOutput | None:
        async def read(tx: AsyncManagedTransaction) -> CategoryOutput | None:
            result = await tx.run(CategoryDatabase.GET_BY_ID_QUERY, category_id=category_id, application=application)
            record = await result.single()
            if record is None:
                return None
            return CategoryOutput.model_validate(record["category"])

        async with self.session as session:
            return await session.execute_read(read)

    async def get_all(self, application: str, parent_id: str | None = None) -> list[CategoryOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[CategoryOutput]:
            query = CategoryDatabase.GET_CHILDREN_QUERY if parent_id else CategoryDatabase.GET_ROOTS_QUERY
            result = await tx.run(query, application=application, parent_id=parent_id)
            return [CategoryOutput.model_validate(record["category"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def create(self, category: CategoryInput, application: str) -> str:
        async def create_transaction(tx: AsyncManagedTransaction) -> str:
            if category.parent_id is not None:
                await self._validate_parent_exists_tx(tx, application, category.parent_id)
            await self._validate_not_exists_tx(tx, application, category.title.root, category.parent_id)

            category_id = generate_id()
            nomen_id = await NomenDatabase.create_with_transaction(tx, category.title.root, None)
            description_nomen_id = None
            if category.description is not None:
                description_nomen_id = await NomenDatabase.create_with_transaction(tx, category.description.root, None)

            result = await tx.run(
                CategoryDatabase.CREATE_QUERY,
                category_id=category_id,
                application=application,
                nomen_id=nomen_id,
                parent_id=category.parent_id,
                description_nomen_id=description_nomen_id,
            )
            record = await result.single(strict=True)
            return record["category_id"]

        async with self.session as session:
            return str(await session.execute_write(create_transaction))

    async def _validate_parent_exists_tx(self, tx: AsyncManagedTransaction, application: str, parent_id: str) -> None:
        result = await tx.run(
            CategoryDatabase.PARENT_EXISTS_QUERY,
            application=application,
            parent_id=parent_id,
        )
        record = await result.single()
        if not record or not record["exists"]:
            raise DataNotFoundError(f"Parent category '{parent_id}' not found in application '{application}'")

    async def _validate_not_exists_tx(
        self, tx: AsyncManagedTransaction, application: str, title: dict[str, str], parent_id: str | None
    ) -> None:
        await lock_nodes(tx, "Application", [application])
        result = await tx.run(
            CategoryDatabase.FIND_EXISTING_QUERY,
            application=application,
            parent_id=parent_id,
            titles=[{"language": language, "text": title_text} for language, title_text in title.items()],
        )
        record = await result.single()
        if record:
            raise DataValidationError(
                f"Category with title '{record['title_text']}' in language '{record['language']}' "
                f"already exists for application '{application}'"
            )

    async def delete(self, category_id: str, application: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            check = await tx.run(CategoryDatabase.DELETE_CHECK_QUERY, category_id=category_id, application=application)
            check_record = await check.single()
            if check_record is None:
                raise DataNotFoundError(f"Category '{category_id}' not found in application '{application}'")
            if check_record["work_count"]:
                raise DataConflictError(
                    f"Category '{category_id}' cannot be deleted because it or its subcategories "
                    f"are referenced by {check_record['work_count']} work(s)"
                )

            result = await tx.run(CategoryDatabase.DELETE_QUERY, category_id=category_id, application=application)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Category '{category_id}' not found in application '{application}'")

        async with self.session as session:
            await session.execute_write(write)
