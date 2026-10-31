"""Large tables of contents retain localization and hierarchy without per-section queries."""
import pytest
from neo4j import AsyncManagedTransaction

from identifier import generate_id
from models.annotation import TableOfContentsInput
from models.edition import EditionInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_large_toc_retains_all_sections_and_localizations(test_database, monkeypatch):
    db = test_database
    text = await db.text.create(TextInput(title={"en": "Large TOC"}, language="en", category_id="category"))
    edition = generate_id()
    size = 1001
    await db.edition.create(EditionInput(type="critical"), edition, text, size)
    children = [{"title": {"en": str(i), "bo": f"དཔེ་ཆ་ {i}"}, "summary": {"en": f"Summary {i}"}, "span": {"start": i, "end": i + 1}} for i in range(size)]
    payload = TableOfContentsInput(sections=[{"title": {"en": "Root"}, "span": {"start": 0, "end": size}, "subsections": children}])
    run = AsyncManagedTransaction.run
    queries = 0

    async def measured(self, *args, **kwargs):
        nonlocal queries
        queries += 1
        return await run(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(AsyncManagedTransaction, "run", measured)
        toc = await db.annotation.table_of_contents.add(edition, payload)
    read = await db.annotation.table_of_contents.get(toc)
    assert len(read.sections[0].subsections) == size
    for actual, expected in zip(read.sections[0].subsections, payload.sections[0].subsections, strict=True):
        assert actual.title == expected.title
        assert actual.summary == expected.summary
        assert actual.span == expected.span
    # A regression to one round trip per title would require thousands of queries.
    assert queries < 40, queries
