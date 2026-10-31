"""Optional updates after an HTTP mutation. Failures are logged; manual reindexing repairs misses."""

import logging
from typing import Literal

from fastapi import Request

logger = logging.getLogger(__name__)


async def update_search(request: Request, kind: Literal["person", "text", "work", "edition"], entity_id: str) -> None:
    state = request.app.state
    try:
        if kind == "edition":
            service = getattr(state, "content_search", None)
            if service is not None:
                await service.index_edition(entity_id, state.db, state.storage)
            return
        service = getattr(state, "catalog_search", None)
        if service is None:
            return
        ids = [entity_id]
        if kind in {"text", "work"}:
            async with state.db.get_session() as session:
                result = await session.run(
                    """
                    MATCH (t:Text)-[:TEXT_OF]->(w:Work)
                    WHERE w.id = $work_id OR EXISTS { (:Text {id: $text_id})-[:TEXT_OF]->(w) }
                    RETURN t.id AS id
                    """,
                    work_id=entity_id if kind == "work" else None,
                    text_id=entity_id if kind == "text" else None,
                )
                ids = [record["id"] for record in await result.data()]
            if kind == "text" and entity_id not in ids:
                ids.append(entity_id)  # A deleted text still needs removal from search.
            kind = "text"
        for document_id in ids:
            await service.index.publish(await service.prepare_document(kind, document_id, state.db))
    except Exception:
        logger.exception("Search update failed for %s %s; reindex to repair", kind, entity_id)
