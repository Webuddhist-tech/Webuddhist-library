# pylint: disable=redefined-outer-name
"""Integration tests for DELETE endpoints and graph cleanup behavior."""

import pytest

from identifier import generate_id


APPLICATION_HEADER = {"X-Application": "test_application"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{generate_id()}"


async def _create_person(client, *, name: str | None = None) -> str:
    response = await client.post("/v2/persons", json={"name": {"en": name or _unique("Person")}})
    assert response.status_code == 201, response.json()
    return response.json()["id"]


async def _create_text(
    client,
    person_id: str,
    *,
    title: str | None = None,
    language: str = "en",
    category_id: str = "category",
    translation_of: str | None = None,
    commentary_of: str | None = None,
    tag_ids: list[str] | None = None,
) -> str:
    body = {
        "title": {language: title or _unique("Text")},
        "language": language,
        "category_id": category_id,
        "contributions": [{"type": "person", "id": person_id, "role": "author"}],
        "tag_ids": tag_ids or [],
    }
    if translation_of is not None:
        body["translation_of"] = translation_of
    if commentary_of is not None:
        body["commentary_of"] = commentary_of

    response = await client.post("/v2/texts", json=body)
    assert response.status_code == 201, response.json()
    return response.json()["id"]


async def _create_diplomatic_edition(client, text_id: str, *, source: str = "Delete Test Source") -> str:
    content = "0123456789"
    response = await client.post(
        f"/v2/texts/{text_id}/editions",
        json={
            "content": content,
            "metadata": {
                "type": "diplomatic",
                "bdrc": _unique("W"),
                "source": source,
            },
            "pagination": {
                "volumes": [
                    {
                        "pages": [
                            {
                                "reference": "1a",
                                "lines": [{"start": 0, "end": len(content)}],
                            }
                        ]
                    }
                ]
            },
        },
    )
    assert response.status_code == 201, response.json()
    return response.json()["id"]


async def _create_segmentation(client, edition_id: str, spans: list[tuple[int, int]]) -> list[str]:
    references = [f"{index + 1}" for index in range(len(spans))]
    response = await client.post(
        f"/v2/editions/{edition_id}/segmentation",
        json={
            "segments": [
                {
                    "reference": references[index],
                    "lines": [{"start": start, "end": end}],
                }
                for index, (start, end) in enumerate(spans)
            ]
        },
    )
    assert response.status_code == 201, response.json()
    response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments")
    assert response.status_code == 200, response.json()
    return [segment["reference"] for segment in response.json()["items"]]


async def _scalar(test_database, query: str, **params):
    async with test_database.get_session() as session:
        result = await session.run(query, **params)
        record = await result.single()
        return None if record is None else record[0]


@pytest.mark.asyncio(loop_scope="session")
class TestReferenceDeletes:
    async def test_delete_unused_language(self, client, test_database):
        code = f"lang{generate_id()[:6].lower()}"
        response = await client.post("/v2/languages", json={"code": code, "name": _unique("Language")})
        assert response.status_code == 201

        response = await client.delete(f"/v2/languages/{code}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Language {code: $code}) }", code=code) is False

    async def test_delete_language_not_found(self, client):
        response = await client.delete("/v2/languages/not_a_language")

        assert response.status_code == 404

    async def test_delete_language_conflicts_when_referenced(self, client, test_database):
        code = f"lang{generate_id()[:6].lower()}"
        assert (await client.post("/v2/languages", json={"code": code, "name": _unique("Language")})).status_code == 201
        person_id = await _create_person(client)
        await _create_text(client, person_id, language=code)

        response = await client.delete(f"/v2/languages/{code}")

        assert response.status_code == 409
        assert "HAS_LANGUAGE" in response.json()["error"]
        assert await _scalar(test_database, "RETURN EXISTS { (:Language {code: $code}) }", code=code) is True

    async def test_delete_unused_application(self, client, test_database):
        name = f"app{generate_id()[:6].lower()}"
        response = await client.post("/v2/applications", json={"name": name})
        assert response.status_code == 201

        response = await client.delete(f"/v2/applications/{name}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Application {id: $name}) }", name=name) is False

    async def test_delete_application_not_found(self, client):
        response = await client.delete("/v2/applications/not_an_application")

        assert response.status_code == 404

    async def test_delete_application_conflicts_when_used(self, client, test_database):
        name = f"app{generate_id()[:6].lower()}"
        assert (await client.post("/v2/applications", json={"name": name})).status_code == 201
        headers = {"X-Application": name}
        response = await client.post("/v2/categories", json={"title": {"en": _unique("Category")}}, headers=headers)
        assert response.status_code == 201

        response = await client.delete(f"/v2/applications/{name}")

        assert response.status_code == 409
        assert "category" in response.json()["error"].lower()
        assert await _scalar(test_database, "RETURN EXISTS { (:Application {id: $name}) }", name=name) is True

    async def test_delete_application_conflicts_with_tag_or_api_key(self, client, test_database):
        tag_app = f"app{generate_id()[:6].lower()}"
        assert (await client.post("/v2/applications", json={"name": tag_app})).status_code == 201
        tag_response = await client.post(
            "/v2/tags",
            json={"title": {"en": _unique("Tag")}},
            headers={"X-Application": tag_app},
        )
        assert tag_response.status_code == 201

        response = await client.delete(f"/v2/applications/{tag_app}")

        assert response.status_code == 409
        assert "tag" in response.json()["error"].lower()

        key_app = f"app{generate_id()[:6].lower()}"
        assert (await client.post("/v2/applications", json={"name": key_app})).status_code == 201
        await test_database.api_key.create(generate_id(), "Delete endpoint test key", "delete-test@example.com", key_app)

        response = await client.delete(f"/v2/applications/{key_app}")

        assert response.status_code == 409
        assert "api key" in response.json()["error"].lower()


@pytest.mark.asyncio(loop_scope="session")
class TestPersonAndCategoryDeletes:
    async def test_delete_unreferenced_person_removes_name_graph(self, client, test_database):
        person_name = _unique("Disposable Person")
        person_id = await _create_person(client, name=person_name)

        response = await client.delete(f"/v2/persons/{person_id}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Person {id: $person_id}) }", person_id=person_id) is False
        assert (
            await _scalar(
                test_database,
                "MATCH (:LocalizedText {text: $person_name}) RETURN count(*)",
                person_name=person_name,
            )
            == 0
        )

    async def test_delete_person_not_found(self, client):
        response = await client.delete("/v2/persons/not_a_person")

        assert response.status_code == 404

    async def test_delete_person_conflicts_when_contributed_to_text(self, client, test_database):
        person_id = await _create_person(client)
        await _create_text(client, person_id)

        response = await client.delete(f"/v2/persons/{person_id}")

        assert response.status_code == 409
        assert "contribution" in response.json()["error"].lower()
        assert await _scalar(test_database, "RETURN EXISTS { (:Person {id: $person_id}) }", person_id=person_id) is True

    async def test_delete_category_conflicts_when_work_depends_on_descendant(self, client, test_database):
        person_id = await _create_person(client)
        parent_response = await client.post(
            "/v2/categories",
            json={"title": {"en": _unique("Parent")}, "description": {"en": "Parent description"}},
            headers=APPLICATION_HEADER,
        )
        assert parent_response.status_code == 201
        parent_id = parent_response.json()["id"]
        child_response = await client.post(
            "/v2/categories",
            json={"title": {"en": _unique("Child")}, "parent_id": parent_id},
            headers=APPLICATION_HEADER,
        )
        assert child_response.status_code == 201
        child_id = child_response.json()["id"]
        grandchild_response = await client.post(
            "/v2/categories",
            json={"title": {"en": _unique("Grandchild")}, "parent_id": child_id},
            headers=APPLICATION_HEADER,
        )
        assert grandchild_response.status_code == 201
        grandchild_id = grandchild_response.json()["id"]
        text_id = await _create_text(client, person_id, category_id=grandchild_id)
        work_id = await test_database.text.get_work_id(text_id)

        response = await client.delete(f"/v2/categories/{parent_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 409
        assert "work" in response.json()["error"].lower()
        assert await _scalar(test_database, "RETURN EXISTS { (:Category {id: $id}) }", id=parent_id) is True
        assert await _scalar(test_database, "RETURN EXISTS { (:Category {id: $id}) }", id=child_id) is True
        assert await _scalar(test_database, "RETURN EXISTS { (:Category {id: $id}) }", id=grandchild_id) is True
        assert (
            await _scalar(
                test_database,
                "MATCH (:Work {id: $work_id})-[r:HAS_CATEGORY]->() RETURN count(r)",
                work_id=work_id,
            )
            == 1
        )

    async def test_delete_category_wrong_application_returns_404_and_preserves_category(self, client, test_database):
        wrong_app = f"deletewrongapp{generate_id()[:6].lower()}"
        response = await client.post("/v2/applications", json={"name": wrong_app})
        assert response.status_code == 201
        category_response = await client.post(
            "/v2/categories",
            json={"title": {"en": _unique("Wrong App Category")}},
            headers=APPLICATION_HEADER,
        )
        assert category_response.status_code == 201
        category_id = category_response.json()["id"]

        response = await client.delete(f"/v2/categories/{category_id}", headers={"X-Application": wrong_app})

        assert response.status_code == 404
        assert await _scalar(test_database, "RETURN EXISTS { (:Category {id: $id}) }", id=category_id) is True


@pytest.mark.asyncio(loop_scope="session")
class TestTextDeletes:
    async def test_delete_text_removes_owned_work(self, client, test_database):
        person_id = await _create_person(client)
        text_id = await _create_text(client, person_id)
        work_id = await test_database.text.get_work_id(text_id)

        response = await client.delete(f"/v2/texts/{text_id}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Text {id: $text_id}) }", text_id=text_id) is False
        assert await _scalar(test_database, "RETURN EXISTS { (:Work {id: $work_id}) }", work_id=work_id) is False
        assert (
            await _scalar(
                test_database,
                "MATCH (:Contribution)-[:BY]->(:Person {id: $person_id}) RETURN count(*)",
                person_id=person_id,
            )
            == 0
        )

    async def test_delete_text_not_found(self, client):
        response = await client.delete("/v2/texts/not_a_text")

        assert response.status_code == 404

    async def test_delete_text_conflicts_when_it_has_editions(self, client, test_database):
        person_id = await _create_person(client)
        text_id = await _create_text(client, person_id)
        await _create_diplomatic_edition(client, text_id)

        response = await client.delete(f"/v2/texts/{text_id}")

        assert response.status_code == 409
        assert "edition" in response.json()["error"].lower()
        assert await _scalar(test_database, "RETURN EXISTS { (:Text {id: $text_id}) }", text_id=text_id) is True

    async def test_delete_text_conflicts_when_other_text_points_to_it(self, client, test_database):
        person_id = await _create_person(client)
        root_id = await _create_text(client, person_id, language="bo", title=_unique("Root"))
        await _create_text(client, person_id, title=_unique("Translation"), translation_of=root_id)

        response = await client.delete(f"/v2/texts/{root_id}")

        assert response.status_code == 409
        assert "translation" in response.json()["error"].lower()
        assert await _scalar(test_database, "RETURN EXISTS { (:Text {id: $text_id}) }", text_id=root_id) is True

    async def test_delete_text_conflicts_when_commentary_points_to_it(self, client, test_database):
        person_id = await _create_person(client)
        root_id = await _create_text(client, person_id, language="bo", title=_unique("Root"))
        await _create_text(client, person_id, language="bo", title=_unique("Commentary"), commentary_of=root_id)

        response = await client.delete(f"/v2/texts/{root_id}")

        assert response.status_code == 409
        assert "commentary" in response.json()["error"].lower()
        assert await _scalar(test_database, "RETURN EXISTS { (:Text {id: $text_id}) }", text_id=root_id) is True

    async def test_delete_translation_preserves_shared_work_and_tags(self, client, test_database):
        person_id = await _create_person(client)
        tag_response = await client.post("/v2/tags", json={"title": {"en": _unique("Tag")}}, headers=APPLICATION_HEADER)
        assert tag_response.status_code == 201
        tag_id = tag_response.json()["id"]
        root_id = await _create_text(client, person_id, language="bo", title=_unique("Root"), tag_ids=[tag_id])
        translation_id = await _create_text(client, person_id, title=_unique("Translation"), translation_of=root_id)
        work_id = await test_database.text.get_work_id(root_id)

        response = await client.delete(f"/v2/texts/{translation_id}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Work {id: $work_id}) }", work_id=work_id) is True
        assert (
            await _scalar(
                test_database,
                "MATCH (:Work {id: $work_id})-[:HAS_TAG]->(:Tag {id: $tag_id}) RETURN count(*)",
                work_id=work_id,
                tag_id=tag_id,
            )
            == 1
        )


@pytest.mark.asyncio(loop_scope="session")
class TestExistingDeleteCascades:
    async def test_delete_tag_removes_work_and_segment_tag_relationships(self, client, test_database):
        person_id = await _create_person(client)
        tag_response = await client.post("/v2/tags", json={"title": {"en": _unique("Tag")}}, headers=APPLICATION_HEADER)
        assert tag_response.status_code == 201
        tag_id = tag_response.json()["id"]
        text_id = await _create_text(client, person_id)
        await client.post(f"/v2/texts/{text_id}/tags/{tag_id}")
        segment_id = _unique("seg")
        async with test_database.get_session() as session:
            await session.run(
                """
                CREATE (sgn:Segmentation {id: $segmentation_id})
                CREATE (:Segment {id: $segment_id})-[:SEGMENT_OF]->(sgn)
                """,
                segmentation_id=f"{segment_id}_segmentation",
                segment_id=segment_id,
            )
        assert (await client.post(f"/v2/segments/{segment_id}/tags/{tag_id}")).status_code == 204

        response = await client.delete(f"/v2/tags/{tag_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Tag {id: $tag_id}) }", tag_id=tag_id) is False
        assert (
            await _scalar(
                test_database,
                "MATCH ()-[r:HAS_TAG]->(:Tag {id: $tag_id}) RETURN count(r)",
                tag_id=tag_id,
            )
            == 0
        )

    async def test_delete_tag_wrong_application_returns_404_and_preserves_tag(self, client, test_database):
        wrong_app = f"tagwrongapp{generate_id()[:6].lower()}"
        response = await client.post("/v2/applications", json={"name": wrong_app})
        assert response.status_code == 201
        tag_response = await client.post("/v2/tags", json={"title": {"en": _unique("Tag")}}, headers=APPLICATION_HEADER)
        assert tag_response.status_code == 201
        tag_id = tag_response.json()["id"]

        response = await client.delete(f"/v2/tags/{tag_id}", headers={"X-Application": wrong_app})

        assert response.status_code == 404
        assert await _scalar(test_database, "RETURN EXISTS { (:Tag {id: $tag_id}) }", tag_id=tag_id) is True

    async def test_delete_edition_cascades_annotations_and_preserves_source(self, client, test_database):
        person_id = await _create_person(client)
        text_id = await _create_text(client, person_id)
        source_name = _unique("Source")
        edition_id = await _create_diplomatic_edition(client, text_id, source=source_name)
        bib_response = await client.post(
            f"/v2/editions/{edition_id}/bibliographic",
            json={"span": {"start": 0, "end": 5}, "type": "colophon"},
        )
        assert bib_response.status_code == 201
        bib_id = bib_response.json()["id"]
        note_response = await client.post(
            f"/v2/editions/{edition_id}/durchens",
            json=[{"span": {"start": 5, "end": 10}, "text": "note"}],
        )
        assert note_response.status_code == 201
        note_id = note_response.json()[0]["id"]

        response = await client.delete(f"/v2/editions/{edition_id}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Edition {id: $edition_id}) }", edition_id=edition_id) is False
        assert (
            await _scalar(
                test_database,
                "RETURN EXISTS { (:BibliographicMetadata {id: $bib_id}) }",
                bib_id=bib_id,
            )
            is False
        )
        assert await _scalar(test_database, "RETURN EXISTS { (:Note {id: $note_id}) }", note_id=note_id) is False
        assert await _scalar(test_database, "RETURN EXISTS { (:Source {name: $source_name}) }", source_name=source_name) is True
        assert await _scalar(test_database, "RETURN EXISTS { (:Text {id: $text_id}) }", text_id=text_id) is True

    async def test_delete_edition_cascades_alignment_when_edition_is_target(self, client, test_database):
        person_id = await _create_person(client)
        source_text_id = await _create_text(client, person_id, title=_unique("Source Text"))
        target_text_id = await _create_text(client, person_id, title=_unique("Target Text"))
        source_edition_id = await _create_diplomatic_edition(client, source_text_id)
        target_edition_id = await _create_diplomatic_edition(client, target_text_id)
        source_segment_refs = await _create_segmentation(client, source_edition_id, [(0, 5)])
        target_segment_refs = await _create_segmentation(client, target_edition_id, [(0, 5)])
        alignment_response = await client.put(
            f"/v2/editions/{source_edition_id}/alignments/{target_edition_id}",
            json={"alignments": [{
                "source_segment_reference": source_segment_refs[0],
                "target_segment_reference": target_segment_refs[0],
            }]},
        )
        assert alignment_response.status_code == 204

        response = await client.delete(f"/v2/editions/{target_edition_id}")

        assert response.status_code == 204
        assert await _scalar(test_database, "RETURN EXISTS { (:Edition {id: $id}) }", id=target_edition_id) is False
        assert await _scalar(test_database, "RETURN EXISTS { (:Edition {id: $id}) }", id=source_edition_id) is True
        assert (
            await _scalar(
                test_database,
                """
                RETURN count {
                    (:Edition {id: $source_edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)
                    <-[:SEGMENT_OF]-(:Segment)-[:ALIGNED_TO]->(:Segment)
                }
                """,
                source_edition_id=source_edition_id,
            )
            == 0
        )
