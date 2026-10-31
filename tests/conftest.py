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
from search_client import create_search_client

# Suppress verbose Neo4j driver logging
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.io").setLevel(logging.WARNING)
logging.getLogger("neo4j.pool").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


OPENSEARCH_IMAGE = "opensearchproject/opensearch:3.5.0"
OPENSEARCH_JAVA_OPTS = "-Xms256m -Xmx256m"
OPENSEARCH_STARTUP_TIMEOUT_SECONDS = 240
OPENSEARCH_TIBETAN_PLUGIN_ENV = "OPENSEARCH_TIBETAN_PLUGIN_ZIP"
OPENSEARCH_BDRC_PLUGIN_ENV = "OPENSEARCH_BDRC_PLUGIN_ZIP"
NEO4J_TRIGGER_REFRESH_SECONDS = 2
NEO4J_TEST_DATABASE = "openpecha-test"


def _opensearch_command() -> str:
    # Test only the analyzers used by this application; omit unrelated heavyweight plugins.
    commands = [
        "rm -rf /usr/share/opensearch/plugins/*",
        "/usr/share/opensearch/bin/opensearch-plugin install --batch analysis-icu",
        "/usr/share/opensearch/bin/opensearch-plugin install --batch file:///tmp/opensearch-plugins/analysis-tibetan.zip",
        "/usr/share/opensearch/bin/opensearch-plugin install --batch file:///tmp/opensearch-plugins/analysis-bdrc.zip",
        "./opensearch-docker-entrypoint.sh opensearch",
    ]
    return 'bash -c "' + ' && '.join(commands) + '"'


def load_constraints_file() -> list[str]:
    constraints_path = Path(__file__).parent.parent / "database" / "neo4j_constraints.cypher"
    if not constraints_path.exists():
        return []
    with open(constraints_path) as f:
        content = f.read()
    return [stmt.strip() for stmt in content.split(";") if stmt.strip()]




@pytest.fixture(scope="session")
def _docker_runtime():
    """Session-scoped Neo4j container.

    A local Neo4j instance is spun up via testcontainers and torn down
    automatically when the test session ends.  Requires Docker to be running.
    """
    with pytest.MonkeyPatch.context() as patch:
        colima_socket = os.path.expanduser("~/.colima/default/docker.sock")
        if not os.environ.get("DOCKER_HOST"):
            if os.path.exists(colima_socket):
                patch.setenv("DOCKER_HOST", f"unix://{colima_socket}")
            elif not os.path.exists("/var/run/docker.sock"):
                pytest.fail(
                    "No Docker daemon found. Tests require a running Docker-compatible runtime.\n"
                    "Start your Docker daemon:\n"
                    "  - Colima: colima start\n"
                    "  - Docker Desktop: Open the Docker Desktop application\n"
                    "See README.md for details."
                )

        if os.environ.get("DOCKER_HOST") and not os.environ.get("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE"):
            patch.setenv("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
        yield



@pytest.fixture(scope="session")
def _neo4j_container(_docker_runtime):
    """One disposable Neo4j database per suite, with bounded memory."""
    container = (
        Neo4jContainer("neo4j:2026.07.1")
        .with_env("NEO4J_server_memory_heap_initial__size", "256m")
        .with_env("NEO4J_server_memory_heap_max__size", "512m")
        .with_env("NEO4J_server_memory_pagecache_size", "64m")
        .with_env("NEO4J_initial_dbms_default__database", NEO4J_TEST_DATABASE)
        .with_env("NEO4J_PLUGINS", '["apoc"]')
        .with_env("NEO4J_apoc_trigger_enabled", "true")
        .with_env("NEO4J_apoc_trigger_refresh", "1000")
        .with_env("NEO4J_dbms_security_procedures_unrestricted", "apoc.*")
        .with_env("NEO4J_dbms_security_procedures_allowlist", "apoc.*")
    )
    try:
        container.start()
        test_uri = container.get_connection_url()
        test_password = container.password
        with pytest.MonkeyPatch.context() as patch:
            for key, value in {
                "NEO4J_URI": test_uri, "NEO4J_USERNAME": "neo4j",
                "NEO4J_PASSWORD": test_password, "NEO4J_DATABASE": NEO4J_TEST_DATABASE,
            }.items():
                patch.setenv(key, value)
            with GraphDatabase.driver(test_uri, auth=("neo4j", test_password)) as driver:
                with driver.session(database="system") as session:
                    session.run("ALTER DATABASE $database SET DEFAULT LANGUAGE CYPHER 25", database=NEO4J_TEST_DATABASE).consume()
                with driver.session(database=NEO4J_TEST_DATABASE) as session:
                    for statement in load_constraints_file():
                        session.run(cast(LiteralString, statement)).consume()

            async def do_install_triggers():
                async with AsyncGraphDatabase.driver(test_uri, auth=("neo4j", test_password)) as driver:
                    await install_triggers(driver, NEO4J_TEST_DATABASE)

            asyncio.run(do_install_triggers())
            time.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)
            yield {"uri": test_uri, "password": test_password, "database": NEO4J_TEST_DATABASE}
    finally:
        with suppress(Exception):
            container.stop()


@pytest.fixture(scope="session")
def _opensearch_endpoint(_docker_runtime) -> Generator[str]:
    """One OpenSearch container shared by all search tests in the session."""
    plugin_dir = Path(__file__).resolve().parent.parent / "opensearch-plugins"
    plugins = {
        "analysis-tibetan": Path(os.environ.get(OPENSEARCH_TIBETAN_PLUGIN_ENV, plugin_dir / "analysis-tibetan.zip")),
        "analysis-bdrc": Path(os.environ.get(OPENSEARCH_BDRC_PLUGIN_ENV, plugin_dir / "analysis-bdrc.zip")),
    }
    for name, path in plugins.items():
        if not path.is_file():
            pytest.fail(f"Required OpenSearch plugin {name} is missing: {path}")
    container = (
        DockerContainer(OPENSEARCH_IMAGE, command=_opensearch_command())
        .with_env("discovery.type", "single-node")
        .with_env("DISABLE_SECURITY_PLUGIN", "true")
        .with_env("OPENSEARCH_JAVA_OPTS", OPENSEARCH_JAVA_OPTS)
        .with_exposed_ports(9200)
    )
    for name, path in plugins.items():
        container = container.with_volume_mapping(str(path.resolve()), f"/tmp/opensearch-plugins/{name}.zip", mode="ro")
    try:
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
            raise RuntimeError(f"OpenSearch container did not become ready: {last_error}\n{logs}")

        yield endpoint
    finally:
        with suppress(Exception):
            container.stop()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _neo4j_database(_neo4j_container):
    """Session-scoped async Database backed by a disposable Neo4j container."""
    db = Database(neo4j_uri=_neo4j_container["uri"], neo4j_auth=("neo4j", _neo4j_container["password"]), neo4j_database=_neo4j_container["database"])
    try:
        await db.verify_connectivity()
        yield db
    finally:
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
    client = create_search_client(
        endpoint=_opensearch_endpoint,
        region="ap-southeast-1",
        auth_mode="none",
        request_timeout=30,
        max_retries=0,
    )
    service = ContentSearchService(client=client, index_name=f"openpecha-content-search-test-{uuid.uuid4().hex}")
    try:
        await service.setup_index()
        yield service
    finally:
        with suppress(Exception):
            await service.index.delete_index()
        await client.close()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def catalog_search(request) -> AsyncGenerator[CatalogSearchService]:
    """Real OpenSearch catalog search service for tests that provide BDRC plugin ZIPs."""
    _opensearch_endpoint = request.getfixturevalue("_opensearch_endpoint")
    client = create_search_client(
        endpoint=_opensearch_endpoint,
        region="ap-southeast-1",
        auth_mode="none",
        request_timeout=30,
        max_retries=0,
    )
    service = CatalogSearchService(client=client, index_name=f"openpecha-catalog-search-test-{uuid.uuid4().hex}")
    try:
        await service.setup_index()
        await _assert_catalog_analyzers(_opensearch_endpoint, service)
        yield service
    finally:
        with suppress(Exception):
            await service.index.delete_index()
        await client.close()


async def _assert_catalog_analyzers(endpoint: str, service: CatalogSearchService) -> None:
    async with httpx.AsyncClient(base_url=endpoint, timeout=5, trust_env=False) as client:
        plugins = await client.get("/_cat/plugins")
        plugins.raise_for_status()
        plugin_text = plugins.text
        assert "analysis-tibetan" in plugin_text
        assert "analysis-bdrc" in plugin_text

    await service.index.analyze({"analyzer": "catalog_tibetan", "text": "ཞི་བ་ལྷ་"})
    await service.index.analyze({"analyzer": "catalog_sanskrit_roman", "text": "Śāntideva"})



class MockS3Storage:
    """In-memory S3 storage mock for tests."""

    def __init__(self):
        self._storage: dict[str, str | bytes] = {}

    async def put_immutable(self, key, body, content_type):
        if not isinstance(body, bytes):
            body = body.read()
        if key in self._storage:
            from exceptions import DataConflictError
            raise DataConflictError("Storage key already exists")
        self._storage[key] = body

    async def read_text(self, key):
        if key not in self._storage:
            from exceptions import DataNotFoundError
            raise DataNotFoundError(f"Content object '{key}' not found")
        value = self._storage[key]
        return value.decode() if isinstance(value, bytes) else value

    async def generate_url(self, key, expires_in=3600):
        return f"https://mock-s3.example.com/{key}?signed=1&expires_in={expires_in}"

    async def store_base_text(self, text_id: str, edition_id: str, base_text: str) -> str:
        key = f"base_texts/{text_id}/{edition_id}.txt"
        self._storage[key] = base_text
        return f"https://mock-s3.example.com/{key}"



class NoOpContentSearch:
    async def index_edition(self, *args):
        pass

    async def search(self, *args, **kwargs) -> list:
        return []


@pytest.fixture(scope="function")
def noop_content_search():
    return NoOpContentSearch()


@pytest_asyncio.fixture(loop_scope="session")
async def client(test_database, mock_storage, noop_content_search):
    """Create async HTTP client with app.state configured for testing."""
    import httpx

    from main import create_app

    fastapi_app = create_app(testing=True)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = noop_content_search
    fastapi_app.state.catalog_search = None
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="session")
async def search_client(test_database, mock_storage, content_search):
    """Create async HTTP client backed by real OpenSearch for search tests."""
    import httpx

    from main import create_app

    fastapi_app = create_app(testing=True)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = content_search
    fastapi_app.state.catalog_search = None
    transport = httpx.ASGITransport(app=fastapi_app)

    async def refresh_before_search(request):
        if request.url.path == "/v2/content-search":
            await content_search.index.refresh_index()

    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        ac.event_hooks["request"].append(refresh_before_search)
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
async def auth_client(test_database, mock_storage, noop_content_search, monkeypatch):
    """Create async HTTP client with real API key authentication (testing=False)."""
    import httpx

    from main import create_app
    from config import settings

    fastapi_app = create_app(testing=False)
    fastapi_app.state.db = test_database
    fastapi_app.state.storage = mock_storage
    fastapi_app.state.content_search = noop_content_search
    fastapi_app.state.catalog_search = None

    monkeypatch.setattr(settings, "environment", "test")

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:
        yield ac
