"""Install all APOC triggers against the configured Neo4j database.

Usage:
    python -m scripts.install_neo4j_triggers

Requires environment variables (via .env or shell):
    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE (default: neo4j)
"""

import asyncio
import logging
import sys

from neo4j import AsyncGraphDatabase

from config import settings
from database.neo4j_triggers import install_triggers

logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.neo4j_uri or not settings.neo4j_password:
        logger.error("NEO4J_URI and NEO4J_PASSWORD must be set (via .env or environment)")
        sys.exit(1)

    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))
    try:
        await driver.verify_connectivity()
        logger.info("Connected to %s", settings.neo4j_uri)

        await install_triggers(driver, settings.neo4j_database)
        logger.info("Neo4j triggers installed successfully.")
    finally:
        await driver.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    asyncio.run(main())
