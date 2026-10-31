# ruff: noqa: ANN001, ANN201, S101
import pytest


from models.base import LocalizedString
from models.contribution import PersonContributionInput
from models.enums import ContributorRole
from models.person import PersonInput
from models.text import TextInput


async def _create_person(
    test_database,
    *,
    name: dict[str, str],
    alt_names: list[dict[str, str]] | None = None,
) -> str:
    return await test_database.person.create(
        PersonInput(
            name=LocalizedString(name),
            alt_names=[LocalizedString(alt) for alt in alt_names] if alt_names else None,
        )
    )


async def _create_text(
    test_database,
    *,
    person_id: str,
    title: dict[str, str],
    language: str = "sa",
    alt_titles: list[dict[str, str]] | None = None,
) -> str:
    return await test_database.text.create(
        TextInput(
            category_id="category",
            title=LocalizedString(title),
            alt_titles=[LocalizedString(alt) for alt in alt_titles] if alt_titles else None,
            language=language,
            contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
        )
    )


async def publish_catalog(search, db):
    for kind, label in (("person", "Person"), ("text", "Text")):
        async with db.get_session() as session:
            records = await (await session.run(f"MATCH (n:{label}) RETURN n.id AS id")).data()
        for record in records:
            await search.index.publish(await search.prepare_document(kind, record["id"], db))
    await search.index.refresh_index()


@pytest.mark.asyncio(loop_scope="session")
class TestCatalogSearch:
    async def test_person_sanskrit_lenient_search_returns_canonical_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"sa-x-iast": "Śāntideva"})
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/persons", params={"name": "Shantideva"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == person_id
        assert response.json()["items"][0]["name"]["sa-x-iast"] == "Śāntideva"

    async def test_person_diacritic_free_search_returns_canonical_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"sa-x-iast": "Śāntideva"})
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/persons", params={"name": "Santideva"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == person_id
        assert response.json()["items"][0]["name"]["sa-x-iast"] == "Śāntideva"

    async def test_person_alternative_name_lenient_search_returns_canonical_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(
            test_database,
            name={"en": "Peace Deity"},
            alt_names=[{"sa-x-iast": "Śāntideva"}],
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/persons", params={"name": "Shantideva"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == person_id
        assert response.json()["items"][0]["alt_names"][0]["sa-x-iast"] == "Śāntideva"

    async def test_text_title_sanskrit_lenient_search_returns_canonical_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"en": "Search Author"})
        text_id = await _create_text(test_database, person_id=person_id, title={"sa-x-iast": "Nāgārjuna"})
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/texts", params={"title": "Nagarjuna"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == text_id
        assert response.json()["items"][0]["title"]["sa-x-iast"] == "Nāgārjuna"

    async def test_text_alternative_title_lenient_search_returns_canonical_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"en": "Search Author"})
        text_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"en": "Fundamental Verses"},
            language="en",
            alt_titles=[{"sa-x-iast": "Nāgārjuna"}],
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/texts", params={"title": "Nagarjuna"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == text_id
        assert response.json()["items"][0]["alt_titles"][0]["sa-x-iast"] == "Nāgārjuna"

    @pytest.mark.parametrize(
        "query",
        [
            "How to see yourself as you really are",
            "How to see yourself as you truly are",
            "How to see you as you really are",
        ],
    )
    async def test_text_english_title_tolerates_word_variations(
        self,
        catalog_client,
        catalog_search,
        test_database,
        query,
    ):
        person_id = await _create_person(test_database, name={"en": "Search Author"})
        text_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"en": "How to See Yourself as You Really Are"},
            language="en",
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/texts", params={"title": query})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == text_id

    async def test_person_tibetan_phonetic_search_returns_tibetan_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"bo": "ཞི་བ་ལྷ་"})
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/persons", params={"name": "zhi ba lha"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == person_id

    async def test_text_title_search_filters_before_pagination(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"en": "Search Author"})
        sanskrit_text_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"sa-x-iast": "Bodhicaryāvatāra"},
            language="sa",
        )
        english_text_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"en": "Bodhicaryavatara"},
            language="en",
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get(
            "/v2/texts",
            params={"title": "Bodhicaryavatara", "language": "sa", "limit": 1},
        )

        assert response.status_code == 200, response.json()
        body = response.json()
        assert [item["id"] for item in body["items"]] == [sanskrit_text_id]
        assert body["has_more"] is False

    async def test_person_search_excludes_syllable_neighbors_and_anagrams(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        target_id = await _create_person(test_database, name={"sa-x-iast": "Śāntideva"})
        neighbor_id = await _create_person(test_database, name={"sa-x-iast": "Śāntigarbha"})
        anagram_id = await _create_person(test_database, name={"sa-x-iast": "Devaśānti"})
        unrelated_id = await _create_person(test_database, name={"sa-x-iast": "Nāgārjuna"})
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/persons", params={"name": "Shantideva"})

        assert response.status_code == 200, response.json()
        returned_ids = [item["id"] for item in response.json()["items"]]
        assert returned_ids == [target_id]
        assert neighbor_id not in returned_ids
        assert anagram_id not in returned_ids
        assert unrelated_id not in returned_ids

    async def test_text_exact_title_ranks_above_superset_title(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"en": "Search Author"})
        exact_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"en": "Emptiness"},
            language="en",
        )
        superset_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"en": "Emptiness Meditation"},
            language="en",
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/texts", params={"title": "Emptiness"})

        assert response.status_code == 200, response.json()
        returned_ids = [item["id"] for item in response.json()["items"]]
        assert returned_ids[0] == exact_id
        assert set(returned_ids) == {exact_id, superset_id}

    async def test_text_author_filter_narrows_results(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        author_a = await _create_person(test_database, name={"en": "Author Alpha"})
        author_b = await _create_person(test_database, name={"en": "Author Beta"})
        text_a = await _create_text(
            test_database,
            person_id=author_a,
            title={"en": "Collected Works Alpha"},
            language="en",
        )
        text_b = await _create_text(
            test_database,
            person_id=author_b,
            title={"en": "Collected Works Beta"},
            language="en",
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get(
            "/v2/texts",
            params={"title": "Collected Works", "author_id": author_a},
        )

        assert response.status_code == 200, response.json()
        returned_ids = [item["id"] for item in response.json()["items"]]
        assert returned_ids == [text_a]
        assert text_b not in returned_ids

    async def test_text_sanskrit_devanagari_search_returns_record(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"en": "Search Author"})
        text_id = await _create_text(
            test_database,
            person_id=person_id,
            title={"sa-Deva": "नागार्जुन"},
            language="sa",
        )
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/texts", params={"title": "नागार्जुन"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"][0]["id"] == text_id

    async def test_deleting_person_removes_it_from_search(
        self,
        catalog_client,
        catalog_search,
    ):
        created = await catalog_client.post("/v2/persons", json={"name": {"sa-x-iast": "Śāntideva"}})
        assert created.status_code == 201, created.text
        person_id = created.json()["id"]
        await catalog_search.index.refresh_index()

        found = await catalog_client.get("/v2/persons", params={"name": "Shantideva"})
        assert [item["id"] for item in found.json()["items"]] == [person_id]

        deleted = await catalog_client.delete(f"/v2/persons/{person_id}")
        assert deleted.status_code == 204, deleted.text
        await catalog_search.index.refresh_index()

        after_delete = await catalog_client.get("/v2/persons", params={"name": "Shantideva"})
        assert after_delete.status_code == 200, after_delete.json()
        assert after_delete.json()["items"] == []

    async def test_search_with_no_matches_returns_empty_results(
        self,
        catalog_client,
        catalog_search,
        test_database,
    ):
        person_id = await _create_person(test_database, name={"sa-x-iast": "Śāntideva"})
        await publish_catalog(catalog_search, test_database)

        response = await catalog_client.get("/v2/persons", params={"name": "Zzzq Nonexistent"})

        assert response.status_code == 200, response.json()
        assert response.json()["items"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_language_filter_matches_graph_and_catalog(catalog_client, catalog_search, test_database):
    for index, language in enumerate(("en", "en-US", "en-GB", "bo")):
        await test_database.text.create(TextInput(title={"en": f"Language parity {index}", language: f"Language parity {index}"}, language=language, category_id="category"))
    await publish_catalog(catalog_search, test_database)
    for language, expected in (("EN", 3), ("en-us", 1), ("en-GB", 1), ("bo", 1)):
        graph = await catalog_client.get("/v2/texts", params={"language": language})
        search = await catalog_client.get("/v2/texts", params={"language": language, "title": "Language parity"})
        assert graph.status_code == search.status_code == 200, (graph.text, search.text)
        assert {r["id"] for r in graph.json()["items"]} == {r["id"] for r in search.json()["items"]}
        assert len(search.json()["items"]) == expected


@pytest.mark.asyncio(loop_scope="session")
async def test_catalog_paging_skips_entire_stale_page(catalog_search, test_database, monkeypatch):
    from models.requests import PersonFilter
    db = test_database
    valid = await _create_person(db, name={"en": "Current person"})
    record = await db.person.get(valid)
    observed = []

    async def pages(body):
        observed.append(1)
        yield [{"_source": {"person_id": f"deleted-{i}"}} for i in range(100)]
        observed.append(2)
        yield [{"_source": {"person_id": valid}}]

    monkeypatch.setattr(catalog_search.index, "pages", pages)
    items = await catalog_search.search_persons(db=db, query="person", filters=PersonFilter(), offset=0, limit=1)
    assert items == [record]
    assert observed == [1, 2]


async def create_through_api(client, path, data):
    response = await client.post(path, json=data, headers={"X-Application": "test_application"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def text_search_ids(client, search, **filters):
    await search.index.refresh_index()
    if "tag_ids" in filters:
        tags = filters.pop("tag_ids")
        filters["tag_id"] = ",".join(tags) if isinstance(tags, list) else tags
    response = await client.get("/v2/texts", params=filters)
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["items"]}


@pytest.mark.asyncio(loop_scope="session")
async def test_http_text_mutations_refresh_titles_contributors_and_remove_document(catalog_client, catalog_search):
    client, search = catalog_client, catalog_search
    first_author = await create_through_api(client, "/v2/persons", {"name": {"en": "First author"}})
    second_author = await create_through_api(client, "/v2/persons", {"name": {"en": "Second author"}})
    text = await create_through_api(
        client,
        "/v2/texts",
        {
            "title": {"en": "Quartz manuscript"},
            "alt_titles": [{"en": "Violet label"}],
            "language": "en",
            "category_id": "category",
            "contributions": [{"type": "person", "id": first_author, "role": "author"}],
        },
    )
    assert await text_search_ids(client, search, title="Quartz", author_id=first_author) == {text}
    assert await text_search_ids(client, search, title="Violet") == {text}
    response = await client.patch(
        f"/v2/texts/{text}",
        json={
            "title": {"en": "Cobalt manuscript"},
            "alt_titles": [{"en": "Amber label"}],
            "contributions": [{"type": "person", "id": second_author, "role": "author"}],
        },
    )
    assert response.status_code == 200, response.text
    assert await text_search_ids(client, search, title="Cobalt", author_id=second_author) == {text}
    assert await text_search_ids(client, search, title="Amber") == {text}
    for filters in ({"title": "Quartz"}, {"title": "Violet"}, {"title": "Cobalt", "author_id": first_author}):
        assert await text_search_ids(client, search, **filters) == set()
    assert (await client.delete(f"/v2/texts/{text}")).status_code == 204
    assert await text_search_ids(client, search, title="Cobalt") == set()
    indexed = await search.index.search({"query": {"term": {"text_id": text}}})
    assert indexed["hits"]["total"]["value"] == 0  # Hydration alone could hide a stale deleted document.


@pytest.mark.asyncio(loop_scope="session")
async def test_http_work_mutations_refresh_every_translation(catalog_client, catalog_search):
    client, search = catalog_client, catalog_search
    headers = {"X-Application": "test_application"}
    tag = await create_through_api(client, "/v2/tags", {"title": {"en": "Shared tag"}})
    other_tag = await create_through_api(client, "/v2/tags", {"title": {"en": "Replacement tag"}})
    category = await create_through_api(client, "/v2/categories", {"title": {"en": "New category"}})
    root = await create_through_api(
        client,
        "/v2/texts",
        {"title": {"en": "Shared manuscript original"}, "language": "en", "category_id": "category"},
    )
    texts = {root}
    for name, language in (("first", "bo"), ("second", "sa")):
        texts.add(
            await create_through_api(
                client,
                "/v2/texts",
                {
                    "title": {"en": f"Shared manuscript {name}", language: f"Shared manuscript {name}"},
                    "language": language,
                    "translation_of": root,
                },
            )
        )
    assert await text_search_ids(client, search, title="Shared manuscript", category_id="category") == texts
    response = await client.patch(
        f"/v2/texts/{root}",
        headers=headers,
        json={"category_id": category, "expected_category_id": "category", "tag_ids": [tag]},
    )
    assert response.status_code == 200, response.text
    assert await text_search_ids(client, search, title="Shared manuscript", category_id=category, tag_ids=tag) == texts
    assert await text_search_ids(client, search, title="Shared manuscript", category_id="category") == set()
    response = await client.patch(f"/v2/texts/{root}", headers=headers, json={"tag_ids": [other_tag]})
    assert response.status_code == 200, response.text
    assert await text_search_ids(client, search, title="Shared manuscript", tag_ids=other_tag) == texts
    assert await text_search_ids(client, search, title="Shared manuscript", tag_ids=tag) == set()
    translation = next(t for t in texts if t != root)
    assert (await client.post(f"/v2/texts/{translation}/tags/{tag}", headers=headers)).status_code == 204
    assert await text_search_ids(client, search, title="Shared manuscript", tag_ids=tag) == texts
    assert (await client.delete(f"/v2/texts/{translation}/tags/{tag}", headers=headers)).status_code == 204
    assert await text_search_ids(client, search, title="Shared manuscript", tag_ids=tag) == set()
    assert (await client.delete(f"/v2/tags/{other_tag}", headers=headers)).status_code == 204
    assert await text_search_ids(client, search, title="Shared manuscript", tag_ids=other_tag) == set()
    assert await text_search_ids(client, search, title="Shared manuscript", category_id=category) == texts


@pytest.mark.asyncio(loop_scope="session")
async def test_http_person_patch_replaces_search_names(catalog_client, catalog_search):
    client, search = catalog_client, catalog_search
    person = await create_through_api(client, "/v2/persons", {"name": {"en": "Quartz Scholar"}})
    await search.index.refresh_index()
    before = await client.get("/v2/persons", params={"name": "Quartz"})
    assert [p["id"] for p in before.json()["items"]] == [person]
    response = await client.patch(f"/v2/persons/{person}", json={"name": {"en": "Cobalt Scholar"}})
    assert response.status_code == 200, response.text
    await search.index.refresh_index()
    for name, expected in [("Quartz", []), ("Cobalt", [person])]:
        response = await client.get("/v2/persons", params={"name": name})
        assert response.status_code == 200, response.text
        assert [p["id"] for p in response.json()["items"]] == expected


@pytest.mark.asyncio(loop_scope="session")
async def test_catalog_title_filters_combine_without_broadening_results(catalog_client, catalog_search, test_database):
    from models.tag import TagInput
    from models.category import CategoryInput

    db, client, search = test_database, catalog_client, catalog_search
    a, b = [await db.tag.create(TagInput(title={"en": name}), "test_application") for name in ("Tag A", "Tag B")]
    category = await db.category.create(CategoryInput(title={"en": "Other category"}), "test_application")
    ids = []
    for i, (tags, cat, bdrc, wiki) in enumerate(
        [
            ([a, b], "category", "W-match", "Q-match"),
            ([a], "category", "W-other", "Q-other"),
            ([b], category, "W-third", "Q-third"),
        ]
    ):
        ids.append(
            await db.text.create(
                TextInput(
                    title={"en": f"Filter manuscript {i}"},
                    language="en",
                    category_id=cat,
                    tag_ids=tags,
                    bdrc=bdrc,
                    wiki=wiki,
                )
            )
        )
    await publish_catalog(search, db)
    assert await text_search_ids(client, search, title="Filter manuscript", tag_ids=[a, b], tag_id_match="all") == {
        ids[0]
    }
    assert await text_search_ids(client, search, title="Filter manuscript", tag_ids=[a, b], tag_id_match="any") == set(
        ids
    )
    assert await text_search_ids(
        client, search, title="Filter manuscript", tag_ids=[a, b], tag_id_match="any", category_id="category"
    ) == set(ids[:2])
    assert await text_search_ids(
        client, search, title="Filter manuscript", tag_ids=[a, b], tag_id_match="all", bdrc="W-match", wiki="Q-match"
    ) == {ids[0]}
    assert await text_search_ids(client, search, title="Filter manuscript", bdrc="W-match", wiki="Q-other") == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_catalog_offset_and_has_more_count_only_live_matches(
    catalog_client, catalog_search, test_database, monkeypatch
):
    ids = [await _create_person(test_database, name={"en": f"Live person {i}"}) for i in range(4)]
    closed = []

    async def pages(body):
        try:
            yield [{"_source": {"person_id": i}} for i in ("gone-first", ids[0], ids[1], "gone-middle")]
            yield [{"_source": {"person_id": i}} for i in (ids[2], "gone-last", ids[3])]
        finally:
            closed.append(True)

    monkeypatch.setattr(catalog_search.index, "pages", pages)
    for offset, expected, more in [(0, ids[:2], True), (1, ids[1:3], True), (2, ids[2:], False), (4, [], False)]:
        response = await catalog_client.get("/v2/persons", params={"name": "person", "limit": 2, "offset": offset})
        assert response.status_code == 200, response.text
        body = response.json()
        assert [r["id"] for r in body["items"]] == expected
        assert body["has_more"] is more
    assert closed == [True] * 4
