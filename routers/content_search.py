import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from content_search.service import MAX_QUERY_CHARS, ContentSearchService
from database import Database
from dependencies import get_api_key, get_content_search, get_db
from models.content_search import ContentSearchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/content-search", tags=["Content Search"])


@router.get(
    "",
    summary="Search edition content",
    description="Search base text content and return text, edition, and segment locations.",
)
async def search_content(
    query: Annotated[str, Query(description="Search query", min_length=1, max_length=MAX_QUERY_CHARS)],
    db: Annotated[Database, Depends(get_db)],
    _api_key: Annotated[str, Depends(get_api_key)],
    content_search: Annotated[ContentSearchService, Depends(get_content_search)],
    search_type: Annotated[Literal["exact", "similar"], Query(description="Type of content search")] = "exact",
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of results")] = 10,
    text_id: Annotated[str | None, Query(description="Filter results to one text")] = None,
    edition_id: Annotated[str | None, Query(description="Filter results to one edition")] = None,
) -> list[ContentSearchResult]:
    logger.info("Searching edition content with search_type=%s", search_type)
    return await content_search.search(
        db=db,
        query=query,
        search_type=search_type,
        limit=limit,
        text_id=text_id,
        edition_id=edition_id,
    )
