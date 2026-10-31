import logging
from typing import Annotated

from fastapi import Depends, Header, Request, Security
from fastapi.security import APIKeyHeader

from catalog_search import CatalogSearchService
from config import settings
from content_search import ContentSearchService
from database import Database
from exceptions import ForbiddenError, ServiceUnavailableError, UnauthorizedError
from storage import Storage

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"
APPLICATION_HEADER = "X-Application"

api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

OptionalAppHeader = Annotated[str | None, Header(alias="X-Application")]
RequiredAppHeader = Annotated[str, Header(alias="X-Application")]


def get_db(request: Request) -> Database:
    """Dependency that provides the database instance from app.state."""
    return request.app.state.db


def get_storage(request: Request) -> Storage:
    """Dependency that provides the storage instance from app.state."""
    return request.app.state.storage


def get_content_search(request: Request) -> ContentSearchService:
    """Dependency that provides the content search service from app.state."""
    service = getattr(request.app.state, "content_search", None)
    if service is None:
        raise ServiceUnavailableError("Content search is unavailable")
    return service


def get_catalog_search(request: Request) -> CatalogSearchService | None:
    """Dependency that provides the catalog search service from app.state."""
    return getattr(request.app.state, "catalog_search", None)


async def get_api_key(
    request: Request,
    x_api_key: str | None = Security(api_key_header),
) -> str:
    """
    Validate the API key from the request header.
    Returns the validated API key.
    Raises UnauthorizedError if the key is missing or invalid.
    """
    if request.app.state.testing or settings.environment == "dev":
        return "test-api-key"

    if not x_api_key:
        logger.warning("API request without API key from %s", request.client.host if request.client else "unknown")
        raise UnauthorizedError(f"Missing required header: {API_KEY_HEADER}")

    db = request.app.state.db
    result = await db.api_key.validate_key(x_api_key)
    if result is None:
        logger.warning("Invalid API key attempt from %s", request.client.host if request.client else "unknown")
        raise UnauthorizedError("Invalid API key")

    request.state.api_key_info = result

    bound_application_id = result.get("bound_application_id")
    if bound_application_id is not None:
        x_application = request.headers.get(APPLICATION_HEADER)
        if x_application != bound_application_id:
            logger.warning(
                "API key application mismatch: key bound to %s, request for %s",
                bound_application_id,
                x_application,
            )
            raise UnauthorizedError("API key not authorized for this application")

    return x_api_key


def get_admin_key(request: Request, api_key: Annotated[str, Depends(get_api_key)]) -> str:
    if getattr(request.state, "api_key_info", {}).get("bound_application_id") is not None:
        raise ForbiddenError("Application administration requires an unbound API key")
    return api_key
