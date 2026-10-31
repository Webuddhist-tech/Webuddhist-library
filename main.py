import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j.exceptions import ClientError as Neo4jClientError
from opensearchpy.exceptions import OpenSearchException
from pydantic import ValidationError

from catalog_search import CatalogSearchService
from config import settings
from content_search import ContentSearchService
from database import Database
from exceptions import InvalidRequestError, OpenPechaError
from observability import setup_telemetry, shutdown_telemetry
from routers.alignments import router as alignments_router
from routers.annotation.bibliographic import router as bibliographic_router
from routers.annotation.durchens import router as durchens_router
from routers.annotation.paginations import router as paginations_router
from routers.annotation.table_of_contents import router as table_of_contents_router
from routers.annotation.yigchungs import router as yigchungs_router
from routers.applications import router as applications_router
from routers.categories import router as categories_router
from routers.content_search import router as content_search_router
from routers.editions import router as editions_router
from routers.languages import router as languages_router
from routers.persons import router as persons_router
from routers.recordings import router as recordings_router
from routers.segments import router as segments_router
from routers.tags import router as tags_router
from routers.texts import router as texts_router
from search_client import create_search_client
from storage import Storage

logger = logging.getLogger(__name__)


def _map_neo4j_client_error(exc: Neo4jClientError) -> tuple[int, str]:
    """Map Neo4j client errors to concise API responses."""
    code = getattr(exc, "code", "")
    message = str(exc)

    if code == "Neo.ClientError.Transaction.TransactionHookFailed" or "Error executing triggers" in message:
        detail = message
        marker = "java.lang.RuntimeException:"
        if marker in message:
            detail = message.split(marker, 1)[1].strip().split(", enforce_", 1)[0].strip(" }")
        else:
            fallback = re.search(r"Error executing triggers \{([^=]+)=([^}]+?)\s*(?:,\s*[A-Za-z0-9_]+=|\})", message)
            if fallback:
                detail = f"{fallback.group(1).strip()}: {fallback.group(2).strip()}"
            else:
                detail = "Database validation failed due to trigger constraints."
        return 422, detail

    if "Constraint" in code:
        return 409, "Database constraint violation."

    return 400, message


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for startup/shutdown events."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    logger.info("Starting OpenPecha API")

    async with AsyncExitStack() as stack:
        stack.callback(shutdown_telemetry)
        setup_telemetry(app)

        if not app.state.testing and settings.neo4j_uri:
            db = Database(
                neo4j_uri=settings.neo4j_uri,
                neo4j_auth=(settings.neo4j_username, settings.neo4j_password),
                neo4j_database=settings.neo4j_database,
            )
            stack.push_async_callback(db.close)
            await db.verify_connectivity()
            app.state.db = db

            storage = Storage(bucket_name=settings.aws_s3_bucket, region=settings.aws_region)
            stack.push_async_callback(storage.close)
            await storage.connect()
            app.state.storage = storage

            if settings.opensearch_endpoint:
                try:
                    search_client = create_search_client(
                        endpoint=settings.opensearch_endpoint,
                        region=settings.aws_region,
                        auth_mode=settings.opensearch_auth_mode,
                        username=settings.opensearch_username,
                        password=settings.opensearch_password,
                        request_timeout=10,
                        max_retries=0,
                    )
                    stack.push_async_callback(search_client.close)
                    content_search = ContentSearchService(client=search_client, index_name=settings.opensearch_index)
                    catalog_search = CatalogSearchService(
                        client=search_client, index_name=settings.opensearch_catalog_index
                    )
                    await content_search.setup_index()
                    await catalog_search.setup_index()
                except InvalidRequestError, BotoCoreError, ClientError, OpenSearchException, ValueError:
                    logger.exception("Search disabled until restart; canonical CRUD remains available")
                else:
                    app.state.content_search = content_search
                    app.state.catalog_search = catalog_search
            logger.info("Canonical stores initialized")
        else:
            logger.info("Skipping database initialization (testing mode or no NEO4J_URI)")
        yield

    logger.info("Shutting down OpenPecha API")


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(
        title="OpenPecha API v2",
        version="2.13.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.state.testing = testing

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(_request: Request, exc: ValidationError) -> JSONResponse:
        """Handle Pydantic validation errors."""
        errs = exc.errors()
        first_msg = (errs[0].get("msg") if errs else None) or str(exc) or "Invalid input"
        return JSONResponse(status_code=422, content={"detail": first_msg})

    @app.exception_handler(OpenPechaError)
    async def openpecha_exception_handler(_request: Request, exc: OpenPechaError) -> JSONResponse:
        """Handle OpenPecha custom exceptions."""
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(NotImplementedError)
    async def not_implemented_handler(_request: Request, exc: NotImplementedError) -> JSONResponse:
        """Handle not implemented errors."""
        return JSONResponse(status_code=501, content={"error": str(exc)})

    @app.exception_handler(Neo4jClientError)
    async def neo4j_client_error_handler(_request: Request, exc: Neo4jClientError) -> JSONResponse:
        """Handle Neo4j client errors with concise messages."""
        status_code, message = _map_neo4j_client_error(exc)
        logger.warning("Neo4j client error (%s): %s", getattr(exc, "code", "unknown"), message)
        return JSONResponse(status_code=status_code, content={"error": message})

    @app.exception_handler(OpenSearchException)
    async def search_exception_handler(_request: Request, _exc: OpenSearchException) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "Search is temporarily unavailable"})

    @app.exception_handler(Exception)
    async def general_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        """Handle all other exceptions."""
        logger.exception("Exception occurred: %s - %s", type(exc).__name__, str(exc))
        return JSONResponse(status_code=500, content={"error": str(exc)})

    @app.middleware("http")
    async def request_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Log all requests and add no-cache headers to all responses."""
        response = await call_next(request)
        logger.info(
            "Request: %s %s | Status: %d",
            request.method,
            request.url.path,
            response.status_code,
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/__/health")
    async def health_check() -> dict:
        """Health check endpoint."""
        return {"status": "healthy"}

    app.include_router(texts_router)
    app.include_router(editions_router)
    app.include_router(persons_router)
    app.include_router(categories_router)
    app.include_router(tags_router)
    app.include_router(languages_router)
    app.include_router(alignments_router)
    app.include_router(paginations_router)
    app.include_router(table_of_contents_router)
    app.include_router(bibliographic_router)
    app.include_router(durchens_router)
    app.include_router(yigchungs_router)
    app.include_router(applications_router)
    app.include_router(content_search_router)
    app.include_router(segments_router)
    app.include_router(recordings_router)

    return app


app = create_app()
