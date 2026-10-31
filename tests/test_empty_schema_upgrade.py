import asyncio

import pytest
import pytest_asyncio
from neo4j.exceptions import ClientError

from database.migrations import migrate
from database.neo4j_triggers import install_triggers
from models.enums import LicenseType
from tests.conftest import load_constraints_file, NEO4J_TRIGGER_REFRESH_SECONDS
from tests.schema_contract import EXPECTED_CONSTRAINTS, EXPECTED_INDEXES, EXPECTED_TRIGGERS

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def schema_inventory(db):
    async with db.get_session() as session:
        constraints = {r["name"] for r in await (await session.run("SHOW CONSTRAINTS")).data()}
        indexes = {r["name"] for r in await (await session.run("SHOW INDEXES")).data()}
    async with db._driver.session(database="system") as session:
        triggers = await (await session.run("CALL apoc.trigger.show($database)", database=db._database)).data()
    return constraints, indexes, {r["name"]: r for r in triggers}


@pytest_asyncio.fixture(loop_scope="session")
async def empty_schema_database(_neo4j_database):
    """Remove the installed fixture schema, and restore it even when the test fails."""
    db = _neo4j_database
    try:
        async with db.get_session() as session:
            await (await session.run("MATCH (n) DETACH DELETE n")).consume()
        constraints, _, triggers = await schema_inventory(db)
        async with db._driver.session(database="system") as session:
            for name in triggers:
                await (
                    await session.run("CALL apoc.trigger.drop($database, $name)", database=db._database, name=name)
                ).consume()
            await (
                await session.run("ALTER DATABASE $database SET DEFAULT LANGUAGE CYPHER 5", database=db._database)
            ).consume()
        async with db.get_session() as session:
            for name in constraints:
                await (await session.run(f"DROP CONSTRAINT `{name}`")).consume()
            indexes = await (await session.run("SHOW INDEXES")).data()
            for row in indexes:
                if row["type"] != "LOOKUP":
                    await (await session.run(f"DROP INDEX `{row['name']}`")).consume()
        await asyncio.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)
        constraints, indexes, triggers = await schema_inventory(db)
        assert constraints == set()
        assert not EXPECTED_INDEXES & indexes
        assert triggers == {}
        assert db._database != "neo4j"  # Exercise the configured database, not a hard-coded default.
        yield db
    finally:
        async with db._driver.session(database="system") as session:
            await (
                await session.run("ALTER DATABASE $database SET DEFAULT LANGUAGE CYPHER 25", database=db._database)
            ).consume()
        async with db.get_session() as session:
            await (await session.run("MATCH (n) DETACH DELETE n")).consume()
            for statement in load_constraints_file():
                await (await session.run(statement)).consume()
        await install_triggers(db._driver, db._database)
        await asyncio.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)


async def assert_installed_schema(db):
    constraints, indexes, triggers = await schema_inventory(db)
    assert constraints == EXPECTED_CONSTRAINTS
    assert EXPECTED_INDEXES <= indexes
    assert set(triggers) == EXPECTED_TRIGGERS
    assert all(t["selector"]["phase"] == "before" and not t["paused"] for t in triggers.values())
    await asyncio.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="already exists"):
            await (await session.run("CREATE (:Language {code:'duplicate'}), (:Language {code:'duplicate'})")).consume()
        with pytest.raises(ClientError, match="enforce_localizedtext_has_language"):
            await (await session.run('CREATE (:LocalizedText {text: "orphan"})')).consume()
        assert (await (await session.run("MATCH (n:LocalizedText) RETURN count(n) AS count")).single())["count"] == 0


async def test_migration_bootstraps_empty_database_and_query_language(empty_schema_database):
    db = empty_schema_database
    await migrate(db._driver, db._database)
    await assert_installed_schema(db)
    async with db.get_session() as session:
        names = await (await session.run("MATCH (l:LicenseType) RETURN l.name AS name")).data()
        assert {r["name"] for r in names} == set(LicenseType)
        assert (await (await session.run("WHEN true THEN { RETURN 1 AS result }")).single())["result"] == 1
    before = await schema_inventory(db)
    await migrate(db._driver, db._database)
    assert await schema_inventory(db) == before


async def test_partial_legacy_schema_resumes_and_replaces_old_triggers(empty_schema_database, monkeypatch):
    import database.migrations as migrations

    db = empty_schema_database
    async with db.get_session() as session:
        await (
            await session.run("CREATE CONSTRAINT language_code_unique FOR (n:Language) REQUIRE n.code IS UNIQUE")
        ).consume()
        await (await session.run("CREATE (:Language {code: 'en', name: 'English'})")).consume()
    async with db._driver.session(database="system") as session:
        for name in ("enforce_span_start_lt_end", "enforce_span_start_lte_end"):
            await (
                await session.run(
                    "CALL apoc.trigger.install($database, $name, 'RETURN null', {phase:'before'})",
                    database=db._database,
                    name=name,
                )
            ).consume()

    async def interrupted(*args):
        raise RuntimeError("interrupted installation")

    with monkeypatch.context() as patch:
        patch.setattr(migrations, "install_triggers", interrupted)
        with pytest.raises(RuntimeError, match="interrupted installation"):
            await migrate(db._driver, db._database)
    assert "enforce_span_start_lt_end" in (await schema_inventory(db))[2]
    await migrate(db._driver, db._database)
    await assert_installed_schema(db)
    assert (await schema_inventory(db))[2]["enforce_span_start_lte_end"]["query"] != "RETURN null"
    async with db.get_session() as session:
        assert await (await session.run("MATCH (n:Language) RETURN n.code AS code, n.name AS name")).data() == [
            {"code": "en", "name": "English"}
        ]
