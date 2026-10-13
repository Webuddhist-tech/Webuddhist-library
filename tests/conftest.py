# pylint: disable=redefined-outer-name
import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import suppress
from pathlib import Path
from typing import LiteralString, cast

import httpx
from neo4j import AsyncGraphDatabase, GraphDatabase
import pytest
import pytest_asyncio
from testcontainers.core.container import DockerContainer
from testcontainers.neo4j import Neo4jContainer

from catalog_search import CatalogSearchService
from content_search import ContentSearchService
from database.database import Database
from database.neo4j_triggers import install_triggers

# Suppress verbose Neo4j driver logging
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.io").setLevel(logging.WARNING)
logging.getLogger("neo4j.pool").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


OPENSEARCH_IMAGE = "opensearchproject/opensearch:3.5.0"
OPENSEARCH_JAVA_OPTS = "-Xms512m -Xmx512m"
OPENSEARCH_STARTUP_TIMEOUT_SECONDS = 240
OPENSEARCH_TIBETAN_PLUGIN_ENV = "OPENSEARCH_TIBETAN_PLUGIN_ZIP"
OPENSEARCH_BDRC_PLUGIN_ENV = "OPENSEARCH_BDRC_PLUGIN_ZIP"
NEO4J_TRIGGER_REFRESH_SECONDS = 2


def _opensearch_command(*, install_catalog_plugins: bool) -> str:
    # The bundled distribution ships ~20 heavyweight plugins (ml-commons,
    # security-analytics, k-NN, neural-search, ...) that the test suite never
    # uses but that dominate startup time. Removing them before boot cuts
    # readiness from ~60s to ~10s.
    install_commands = [
        "rm -rf /usr/share/opensearch/plugins/*",
        "/usr/share/opensearch/bin/opensearch-plugin install --batch analysis-icu",
    ]
    if install_catalog_plugins:
        install_commands.extend(
            [
                "/usr/share/opensearch/bin/opensearch-plugin install --batch "
                "file:///tmp/opensearch-plugins/analysis-tibetan.zip",
                "/usr/share/opensearch/bin/opensearch-plugin install --batch "
                "file:///tmp/opensearch-plugins/analysis-bdrc.zip",
            ]
        )

    command = " && ".join(
        [
            f"export OPENSEARCH_JAVA_OPTS='{OPENSEARCH_JAVA_OPTS}'",
            *install_commands,
            "./opensearch-docker-entrypoint.sh opensearch",
        ]
    )
    return f"""bash -c "{command}" """


def load_constraints_file() -> list[str]:
    constraints_path = Path(__file__).parent.parent / "database" / "neo4j_constraints.cypher"
    if not constraints_path.exists():
        return []
    with open(constraints_path) as f:
        content = f.read()
    return [stmt.strip() for stmt in content.split(";") if stmt.strip()]


def setup_test_schema(session) -> None:
    """Setup common test schema data needed by all test suites."""

    # DELETE must be in separate transaction - constraint checks happen before commit
    session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n").consume())

    def do_seed(tx):
        tx.run("""
            CREATE (:Language {code: 'bo', name: 'Tibetan'})
            CREATE (:Language {code: 'en', name: 'English'})
            CREATE (:Language {code: 'sa', name: 'Sanskrit'})
            CREATE (:Language {code: 'zh', name: 'Chinese'})
            CREATE (:Language {code: 'tib', name: 'Spoken Tibetan'})
            CREATE (:RoleType {name: 'translator'})
            CREATE (:RoleType {name: 'author'})
            CREATE (:RoleType {name: 'reviser'})
            CREATE (:RoleType {name: 'narrator'})
            CREATE (:LicenseType {name: 'public'})
            CREATE (:LicenseType {name: 'cc0'})
            CREATE (:LicenseType {name: 'cc-by'})
            CREATE (:LicenseType {name: 'cc-by-sa'})
            CREATE (:LicenseType {name: 'cc-by-nd'})
            CREATE (:LicenseType {name: 'cc-by-nc'})
            CREATE (:LicenseType {name: 'cc-by-nc-sa'})
            CREATE (:LicenseType {name: 'cc-by-nc-nd'})
            CREATE (:LicenseType {name: 'copyrighted'})
            CREATE (:LicenseType {name: 'unknown'})
            CREATE (:NoteType {name: 'durchen'})
            CREATE (:BibliographyType {name: 'colophon'})
            CREATE (:BibliographyType {name: 'incipit'})
            CREATE (:BibliographyType {name: 'alt_incipit'})
            CREATE (:BibliographyType {name: 'alt_title'})
            CREATE (:BibliographyType {name: 'person'})
            CREATE (:BibliographyType {name: 'title'})
            CREATE (:BibliographyType {name: 'author'})
        """).consume()
        tx.run("""
            CREATE (app:Application {id: 'test_application', name: 'Test Application'})
            CREATE (cat:Category {id: 'category'})-[:BELONGS_TO]->(app)
            CREATE (nomen:Nomen {id: 'category_nomen'})
            CREATE (cat)-[:HAS_TITLE]->(nomen)
            CREATE (lt_en:LocalizedText {text: 'Test Category'})
            CREATE (lt_bo:LocalizedText {text: 'ཚིག་སྒྲུབ་གསར་པ།'})
            WITH nomen, lt_en, lt_bo
            MATCH (lang_en:Language {code: 'en'})
            MATCH (lang_bo:Language {code: 'bo'})
            CREATE (nomen)-[:HAS_LOCALIZATION]->(lt_en)-[:HAS_LANGUAGE]->(lang_en)
            CREATE (nomen)-[:HAS_LOCALIZATION]->(lt_bo)-[:HAS_LANGUAGE]->(lang_bo)
        """).consume()

    session.execute_write(do_seed)


@pytest.fixture(scope="session")
def _neo4j_container():
    """Session-scoped Neo4j container.

    A local Neo4j instance is spun up via testcontainers and torn down
    automatically when the test session ends.  Requires Docker to be running.
    """
    colima_socket = os.path.expanduser("~/.colima/default/docker.sock")
    if not os.environ.get("DOCKER_HOST"):
        if os.path.exists(colima_socket):
            os.environ["DOCKER_HOST"] = f"unix://{colima_socket}"
        elif not os.path.exists("/var/run/docker.sock"):
            pytest.fail(
                "No Docker daemon found. Tests require a running Docker-compatible runtime.\n"
                "Start your Docker daemon:\n"
                "  - Colima: colima start\n"
                "  - Docker Desktop: Open the Docker Desktop application\n"
                "See README.md for details."
            )

    if os.environ.get("DOCKER_HOST") and not os.environ.get("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE"):
        os.environ["TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE"] = "/var/run/docker.sock"

    container = (
        Neo4jContainer("neo4j:2026.07.1")
        .with_env("NEO4J_PLUGINS", '["apoc"]')
        .with_env("NEO4J_apoc_trigger_enabled", "true")
        .with_env("NEO4J_apoc_trigger_refresh", "1000")
        .with_env("NEO4J_dbms_security_procedures_unrestricted", "apoc.*")
        .with_env("NEO4J_dbms_security_procedures_allowlist", "apoc.*")
    )
    container.start()

    test_uri = container.get_connection_url()
    test_password = container.password

    os.environ["NEO4J_URI"] = test_uri
    os.environ["NEO4J_USERNAME"] = "neo4j"
    os.environ["NEO4J_PASSWORD"] = test_password

    # Enable CYPHER25 (GQL) as the default language — must run on the system database
    _driver = GraphDatabase.driver(test_uri, auth=("neo4j", test_password))
    with _driver.session(database="system") as sys_session:
        sys_session.run("ALTER DATABASE neo4j SET DEFAULT LANGUAGE CYPHER 25").consume()

    # Setup constraints once per session using sync driver
    constraint_statements = load_constraints_file()
    with _driver.session() as session:
        for statement in constraint_statements:
            try:
                session.run(cast(LiteralString, statement)).consume()
            except Exception:
                pass  # Constraint already exists

    async def do_install_triggers() -> None:
        async_driver = AsyncGraphDatabase.driver(test_uri, auth=("neo4j", test_password))
        try:
            await install_triggers(async_driver)
        finally:
            await async_driver.close()

    asyncio.run(do_install_triggers())
    time.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)
    _driver.close()

    yield {"uri": test_uri, "password": test_password}

    container.stop()


@pytest.fixture(scope="module")
def _opensearch_endpoint() -> Generator[str]:
    """Module-scoped OpenSearch container for content search tests."""
    tibetan_plugin_zip = os.environ.get(OPENSEARCH_TIBETAN_PLUGIN_ENV)
    bdrc_plugin_zip = os.environ.get(OPENSEARCH_BDRC_PLUGIN_ENV)
    install_catalog_plugins = bool(tibetan_plugin_zip or bdrc_plugin_zip)
    if install_catalog_plugins:
        if not tibetan_plugin_zip or not Path(tibetan_plugin_zip).exists():
            pytest.fail(f"{OPENSEARCH_TIBETAN_PLUGIN_ENV} must point to a built analysis-tibetan plugin ZIP")
        if not bdrc_plugin_zip or not Path(bdrc_plugin_zip).exists():
            pytest.fail(f"{OPENSEARCH_BDRC_PLUGIN_ENV} must point to a built analysis-bdrc plugin ZIP")
        assert tibetan_plugin_zip is not None
        assert bdrc_plugin_zip is not None

    container = (
        DockerContainer(OPENSEARCH_IMAGE, command=_opensearch_command(install_catalog_plugins=install_catalog_plugins))
        .with_env("discovery.type", "single-node")
        .with_env("DISABLE_SECURITY_PLUGIN", "true")
        .with_env("OPENSEARCH_JAVA_OPTS", OPENSEARCH_JAVA_OPTS)
        .with_exposed_ports(9200)
    )
    if install_catalog_plugins:
        tibetan_plugin_zip = cast(str, tibetan_plugin_zip)
        bdrc_plugin_zip = cast(str, bdrc_plugin_zip)
        container = container.with_volume_mapping(
            str(Path(tibetan_plugin_zip).resolve()),
            "/tmp/opensearch-plugins/analysis-tibetan.zip",
            mode="ro",
        ).with_volume_mapping(
            str(Path(bdrc_plugin_zip).resolve()),
            "/tmp/opensearch-plugins/analysis-bdrc.zip",
            mode="ro",
        )
    container.start()
    host = container.get_container_host_ip()
    if host in {"localhost", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    endpoint = f"http://{host}:{container.get_exposed_port(9200)}"

    last_error: httpx.HTTPError | None = None
    for _ in range(OPENSEARCH_STARTUP_TIMEOUT_SECONDS):
        try:
            response = httpx.get(endpoint, timeout=2, trust_env=False)
            if response.status_code < 500:
                break
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(1)
    else:
        logs = ""
        with suppress(Exception):
            assert container._container is not None
            logs = container._container.logs(tail=120).decode(errors="replace")
        with suppress(Exception):
            container.stop()
        raise RuntimeError(f"OpenSearch container did not become ready: {last_error}\n{logs}")

    try:
        yield endpoint
    finally:
        with suppress(Exception):
            container.stop()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _neo4j_database(_neo4j_container):
    """Session-scoped async Database backed by a disposable Neo4j container."""
    db = Database(neo4j_uri=_neo4j_container["uri"], neo4j_auth=("neo4j", _neo4j_container["password"]))
    await db.verify_connectivity()
    yield db
    await db.close()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def test_database(_neo4j_database):
    """Per-test fixture: clean DB, seed data, yield shared Database."""
    async with _neo4j_database.get_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
        await session.run("""
            CREATE (lang_bo:Language {code: 'bo', name: 'Tibetan'})
            CREATE (lang_en:Language {code: 'en', name: 'English'})
            CREATE (:Language {code: 'sa', name: 'Sanskrit'})
            CREATE (:Language {code: 'zh', name: 'Chinese'})
            CREATE (:Language {code: 'tib', name: 'Spoken Tibetan'})
            CREATE (:TextType {name: 'root'})
            CREATE (:TextType {name: 'commentary'})
            CREATE (:TextType {name: 'translation'})
            CREATE (:RoleType {name: 'translator'})
            CREATE (:RoleType {name: 'author'})
            CREATE (:RoleType {name: 'reviser'})
            CREATE (:RoleType {name: 'narrator'})
            CREATE (:LicenseType {name: 'public'})
            CREATE (:LicenseType {name: 'cc0'})
            CREATE (:LicenseType {name: 'cc-by'})
            CREATE (:LicenseType {name: 'cc-by-sa'})
            CREATE (:LicenseType {name: 'cc-by-nd'})
            CREATE (:LicenseType {name: 'cc-by-nc'})
            CREATE (:LicenseType {name: 'cc-by-nc-sa'})
            CREATE (:LicenseType {name: 'cc-by-nc-nd'})
            CREATE (:LicenseType {name: 'copyrighted'})
            CREATE (:LicenseType {name: 'unknown'})
            CREATE (:NoteType {name: 'durchen'})
            CREATE (:BibliographyType {name: 'colophon'})
            CREATE (:BibliographyType {name: 'incipit'})
            CREATE (:BibliographyType {name: 'alt_incipit'})
            CREATE (:BibliographyType {name: 'alt_title'})
            CREATE (:BibliographyType {name: 'person'})
            CREATE (:BibliographyType {name: 'title'})
            CREATE (:BibliographyType {name: 'author'})
            CREATE (app:Application {id: 'test_application', name: 'Test Application'})
            CREATE (cat:Category {id: 'category'})-[:BELONGS_TO]->(app)
            CREATE (nomen:Nomen {id: 'category_nomen'})
            CREATE (cat)-[:HAS_TITLE]->(nomen)
            CREATE (lt_en:LocalizedText {text: 'Test Category'})
            CREATE (lt_bo:LocalizedText {text: 'ཚིག་སྒྲུབ་གསར་པ།'})
            CREATE (nomen)-[:HAS_LOCALIZATION]->(lt_en)-[:HAS_LANGUAGE]->(lang_en)
            CREATE (nomen)-[:HAS_LOCALIZATION]->(lt_bo)-[:HAS_LANGUAGE]->(lang_bo)
        """)

    return _neo4j_database


@pytest.fixture(scope="function")
def mock_storage():
    """Mock S3 storage instance for tests."""
    return MockS3Storage()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def content_search(_opensearch_endpoint: str) -> AsyncGenerator[ContentSearchService]:
    """Real OpenSearch content search service for tests."""
    service = ContentSearchService(
        endpoint=_opensearch_endpoint,
        index_name=f"openpecha-content-search-test-{uuid.uuid4().hex}",
        region="ap-southeast-1",
        auth_mode="none",
        request_timeout=5,
        max_retries=0,
    )
    try:
        await service.connect()
        yield service
    finally:
        with suppress(Exception):
            await service.delete_index()
        await service.close()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def catalog_search(request) -> AsyncGenerator[CatalogSearchService]:
    """Real OpenSearch catalog search service for tests that provide BDRC plugin ZIPs."""
    if not os.environ.get(OPENSEARCH_TIBETAN_PLUGIN_ENV) or not os.environ.get(OPENSEARCH_BDRC_PLUGIN_ENV):
        pytest.skip(
            f"Catalog search tests require {OPENSEARCH_TIBETAN_PLUGIN_ENV} and {OPENSEARCH_BDRC_PLUGIN_ENV}"
        )

    _opensearch_endpoint = request.getfixturevalue("_opensearch_endpoint")
    service = CatalogSearchService(
        endpoint=_opensearch_endpoint,
        index_name=f"openpecha-catalog-search-test-{uuid.uuid4().hex}",
        region="ap-southeast-1",
        auth_mode="none",
        request_timeout=5,
        max_retries=0,
    )
    try:
        await service.connect()
        await _assert_catalog_analyzers(_opensearch_endpoint, service)
        yield service
    finally:
        with suppress(Exception):
            await service.delete_index()
        await service.close()


async def _assert_catalog_analyzers(endpoint: str, service: CatalogSearchService) -> None:
    async with httpx.AsyncClient(base_url=endpoint, timeout=5, trust_env=False) as client:
        plugins = await client.get("/_cat/plugins")
        plugins.raise_for_status()
        plugin_text = plugins.text
        assert "analysis-tibetan" in plugin_text
        assert "analysis-bdrc" in plugin_text

    await service.analyze({"analyzer": "catalog_tibetan", "text": "ཞི་བ་ལྷ་"})
    await service.analyze({"analyzer": "catalog_sanskrit_roman", "text": "Śāntideva"})



class MockS3Storage:
    """In-memory S3 storage mock for tests."""

    def __init__(self):
        self._storage: dict[str, str | bytes] = {}

    async def store_base_text(self, text_id: str, edition_id: str, base_text: str) -> str:
        key = f"base_texts/{text_id}/{edition_id}.txt"
        self._storage[key] = base_text
        return f"https://mock-s3.example.com/{key}"

    async def retrieve_base_text(self, text_id: str, edition_id: str) -> str:
        key = f"base_texts/{text_id}/{edition_id}.txt"
        if key not in self._storage:
            from exceptions import DataNotFoundError

            raise DataNotFoundError(f"File not found: {key}")
        value = self._storage[key]
        if not isinstance(value, str):
            raise TypeError(f"Expected text content for key: {key}")
        return value

    async def generate_base_text_url(self, text_id: str, edition_id: str, expires_in: int = 3600) -> str:
        key = f"base_texts/{text_id}/{edition_id}.txt"
        return f"https://mock-s3.example.com/{key}?signed=1&expires_in={expires_in}"

    async def delete_base_text(self, text_id: str, edition_id: str) -> None:
        key = f"base_texts/{text_id}/{edition_id}.txt"
        if key in self._storage:
            del self._storage[key]

    async def apply_insert(self, text_id: str, edition_id: str, position: int, text: str) -> str:
        current = await self.retrieve_base_text(text_id, edition_id)
        updated = current[:position] + text + current[position:]
        return await self.store_base_text(text_id, edition_id, updated)

    async def apply_delete(self, text_id: str, edition_id: str, start: int, end: int) -> str:
        current = await self.retrieve_base_text(text_id, edition_id)
        updated = current[:start] + current[end:]
        return await self.store_base_text(text_id, edition_id, updated)

    async def apply_replace(self, text_id: str, edition_id: str, start: int, end: int, text: str) -> str:
        current = await self.retrieve_base_text(text_id, edition_id)
        updated = current[:start] + text + current[end:]
        return await self.store_base_text(text_id, edition_id, updated)

    async def rollback_base_text(self, text_id: str, edition_id: str) -> None:
        pass  # No-op for mock

    @staticmethod
    def _recording_path(edition_id: str, recording_id: str, extension: str) -> str:
        return f"recordings/{edition_id}/{recording_id}.{extension}"

    async def store_recording(
        self, edition_id: str, recording_id: str, extension: str, audio: bytes, content_type: str
    ) -> str:
        key = self._recording_path(edition_id, recording_id, extension)
        self._storage[key] = audio
        return f"https://mock-s3.example.com/{key}"

    async def delete_recording(self, edition_id: str, recording_id: str, extension: str) -> None:
        self._storage.pop(self._recording_path(edition_id, recording_id, extension), None)

    async def generate_recording_url(
        self, edition_id: str, recording_id: str, extension: str, expires_in: int = 3600
    ) -> str:
        key = self._recording_path(edition_id, recording_id, extension)
        return f"https://mock-s3.example.com/{key}?signed=1&expires_in={expires_in}"


class NoOpContentSearch:
    async def index_edition(self, *args, **kwargs) -> None:
        pass

    async def delete_edition(self, *args, **kwargs) -> None:
        pass

    async def search(self, *args, **kwargs) -> list:
        return []


class NoOpCatalogSearch:
    available = False


@pytest.fixture(scope="function")
def noop_content_search():
    return NoOpContentSearch()


@pytest.fixture(scope="function")
def noop_catalog_search():
    return NoOpCatalogSearch()


@pytest_asyncio.fixture(loop_scope="session")
async def client(test_database, mock_storage, noop_content_search, noop_catalog_search):
    """Create async HTTP client with app.state configured for testing."""
    import httpx

    from main import create_app

    fastapi_app = create_app(testing=True)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = noop_content_search
    fastapi_app.state.catalog_search = noop_catalog_search
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="session")
async def search_client(test_database, mock_storage, content_search, noop_catalog_search):
    """Create async HTTP client backed by real OpenSearch for search tests."""
    import httpx

    from main import create_app

    fastapi_app = create_app(testing=True)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = content_search
    fastapi_app.state.catalog_search = noop_catalog_search
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="session")
async def catalog_client(test_database, mock_storage, noop_content_search, catalog_search):
    """Create async HTTP client backed by real OpenSearch catalog search."""
    import httpx

    from main import create_app

    fastapi_app = create_app(testing=True)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = noop_content_search
    fastapi_app.state.catalog_search = catalog_search
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="session")
async def auth_client(test_database, mock_storage, noop_content_search, noop_catalog_search):
    """Create async HTTP client with real API key authentication (testing=False)."""
    import httpx

    from main import create_app
    from config import settings

    fastapi_app = create_app(testing=False)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = noop_content_search
    fastapi_app.state.catalog_search = noop_catalog_search
    
    settings.environment = "test"
    
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        yield ac
