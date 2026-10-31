"""Audit the configured database; use --apply during a write pause to apply upgrades."""

import argparse
import asyncio
import logging
from contextlib import AsyncExitStack

from config import settings
from database import Database
from database.content_migration import migrate_content
from database.migrations import audit_schema, migrate
from storage import Storage

logger = logging.getLogger(__name__)


async def run(*, apply: bool, content: bool = False) -> None:
    async with AsyncExitStack() as stack:
        db = await stack.enter_async_context(
            Database(settings.neo4j_uri, (settings.neo4j_username, settings.neo4j_password), settings.neo4j_database)
        )
        driver = db._driver  # noqa: SLF001 - schema DDL needs the migration-owned driver
        if apply:
            await migrate(driver, settings.neo4j_database)
            logger.info("Schema upgrade completed")
        else:
            violations = await audit_schema(driver, settings.neo4j_database)
            for name, ids in violations.items():
                logger.error("%s (%d): %s", name, len(ids), ids[:20])
            if violations:
                raise SystemExit(1)
            logger.info("Schema audit passed")
        if content:
            storage = Storage(settings.aws_s3_bucket, settings.aws_region)
            stack.push_async_callback(storage.close)
            await storage.connect()
            count = await migrate_content(db, storage, apply=apply)
            logger.info("Verified %d edition content objects", count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply migrations with application writers paused")
    parser.add_argument("--content", action="store_true", help="Also verify/backfill immutable content using S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(apply=args.apply, content=args.content))
