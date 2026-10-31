"""Documented graph behaviors with explicit membership and preservation checks."""

import pytest

from identifier import generate_id
from models.alignment import EditionAlignmentInput
from models.annotation import SegmentationInput
from models.category import CategoryInput
from models.edition import EditionInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")
HEADERS = {"X-Application": "test_application"}


@pytest.mark.parametrize("depth", [1, 3])
async def test_delete_unused_category_tree_cleans_localizations_only(client, test_database, depth):
    db = test_database
    parent = None
    deleted = []
    for level in range(depth):
        parent = await db.category.create(
            CategoryInput(
                title={"en": f"Deleted title {level}"},
                description={"en": f"Deleted description {level}"},
                parent_id=parent,
            ),
            "test_application",
        )
        deleted.append(parent)
    sibling = await db.category.create(CategoryInput(title={"en": "Preserved sibling"}), "test_application")
    await db.application.create("foreign", "Foreign")
    foreign = await db.category.create(CategoryInput(title={"en": "Foreign category"}), "foreign")
    text = await db.text.create(TextInput(title={"en": "Unrelated work"}, language="en", category_id="category"))
    async with db.get_session() as session:
        owned = await (
            await session.run(
                "MATCH (c:Category)-[:HAS_TITLE|HAS_DESCRIPTION]->(n:Nomen)-[:HAS_LOCALIZATION]->(l:LocalizedText) WHERE c.id IN $ids RETURN collect(DISTINCT elementId(n)) + collect(DISTINCT elementId(l)) AS ids",
                ids=deleted,
            )
        ).single()
    assert len(owned["ids"]) == 4 * depth
    response = await client.delete(f"/v2/categories/{deleted[0]}", headers=HEADERS)
    assert response.status_code == 204, response.text
    for category in deleted:
        assert (await client.get(f"/v2/categories/{category}", headers=HEADERS)).status_code == 404
    assert await db.category.get_by_id(sibling, "test_application") is not None
    assert await db.category.get_by_id(foreign, "foreign") is not None
    assert (await db.text.get(text)).category_id == "category"
    async with db.get_session() as session:
        assert (
            await (
                await session.run("MATCH (n) WHERE elementId(n) IN $ids RETURN count(n) AS count", ids=owned["ids"])
            ).single()
        )["count"] == 0


async def graph_edition(db, index, lines=None, language="en"):
    text = await db.text.create(
        TextInput(title={language: f"Traversal {index}"}, language=language, category_id="category")
    )
    edition = generate_id()
    await db.edition.create(
        EditionInput(type="critical"),
        edition,
        text,
        10,
        segmentation=SegmentationInput(
            segments=[
                {"reference": str(i), "lines": [{"start": start, "end": end}]}
                for i, (start, end) in enumerate(lines or [(0, 10)])
            ]
        ),
    )
    return edition, await db.annotation.segmentation.get_all_segments_by_edition(edition)


async def align(db, source, target, source_ref="0", target_ref="0"):
    await db.alignment.replace(
        source,
        target,
        EditionAlignmentInput(
            alignments=[{"source_segment_reference": source_ref, "target_segment_reference": target_ref}]
        ),
    )


async def test_alignment_chain_stops_at_five_hops_and_filters_after_traversal(client, test_database):
    db = test_database
    graph = [await graph_edition(db, i, language="bo" if i in (1, 3) else "en") for i in range(7)]
    for (source, _), (target, _) in zip(graph, graph[1:]):
        await align(db, source, target)
    origin = graph[0][1][0].id
    response = await client.get(f"/v2/segments/{origin}/related", params={"limit": 100})
    assert response.status_code == 200, response.text
    expected = {graph[i][1][0].id for i in range(1, 6)}
    assert {s["id"] for s in response.json()["items"]} == expected
    filtered = await client.get(f"/v2/segments/{origin}/related", params={"language": "en", "limit": 100})
    assert {s["id"] for s in filtered.json()["items"]} == {graph[i][1][0].id for i in (2, 4, 5)}
    # A middle node can traverse both directions, including edges stored toward it.
    middle = await client.get(f"/v2/segments/{graph[3][1][0].id}/related", params={"limit": 100})
    assert {s["id"] for s in middle.json()["items"]} == {
        segments[0].id for i, (_, segments) in enumerate(graph) if i != 3
    }


async def test_alignment_diamond_cycle_deduplicates_and_pages_stably(client, test_database):
    db = test_database
    graph = [await graph_edition(db, i) for i in range(4)]
    for a, b in ((0, 1), (0, 2), (1, 3), (2, 3), (3, 0)):
        await align(db, graph[a][0], graph[b][0])
    url = f"/v2/segments/{graph[0][1][0].id}/related"
    complete = (await client.get(url, params={"limit": 100})).json()
    ids = [s["id"] for s in complete["items"]]
    assert set(ids) == {graph[i][1][0].id for i in (1, 2, 3)}
    assert len(ids) == 3
    first = (await client.get(url, params={"limit": 2})).json()
    second = (await client.get(url, params={"limit": 2, "offset": 2})).json()
    assert [s["id"] for s in first["items"] + second["items"]] == ids
    assert first["has_more"] is True and second["has_more"] is False
    assert (await client.get(url, params={"limit": 2, "offset": 3})).json()["items"] == []


async def test_zero_width_lookup_and_alignment_projection(test_database):
    db = test_database
    edition, segments = await graph_edition(db, "markers", [(0, 0), (0, 5), (5, 5), (5, 10), (10, 10)])
    ids = [s.id for s in segments]
    for start, end, expected in [
        (0, 0, {0, 1}),
        (5, 5, {2, 3}),
        (10, 10, {4}),
        (0, 5, {0, 1}),
        (5, 10, {2, 3}),
        (0, 10, {0, 1, 2, 3}),
    ]:
        assert set(await db.segment.find_by_span(edition, start, end)) == {ids[i] for i in expected}, (start, end)
    target, targets = await graph_edition(db, "target", [(0, 0), (0, 10)])
    await db.alignment.replace(
        edition,
        target,
        EditionAlignmentInput(
            alignments=[
                {"source_segment_reference": "2", "target_segment_reference": "0"},
                {"source_segment_reference": "0", "target_segment_reference": "1"},
            ]
        ),
    )
    pairs = await db.alignment.get(edition, target, offset=0, limit=20)
    assert len(pairs) == 2
    marker_pair = next(pair for pair in pairs if pair.source_segment.id == ids[2])
    assert marker_pair.target_segment.id == targets[0].id
    assert marker_pair.target_segment.lines[0].start == marker_pair.target_segment.lines[0].end == 0
    related = await db.segment.get_related(edition, [(5, 5)])
    assert [segment.id for segment in related] == [targets[0].id]
