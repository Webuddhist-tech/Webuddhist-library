import asyncio

import pytest

from exceptions import DataNotFoundError
from identifier import generate_id
from models.annotation import BibliographicMetadataInput, MarkInput, NoteInput
from models.edition import EditionInput
from models.text import TextInput, TextOutput

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def create_record(client, kind, **extra):
    data = (
        {"name": {"en": "Person"}, "alt_names": [{"en": "Old alias"}]}
        if kind == "persons"
        else {
            "title": {"en": "Text", "bo": "བོད།"},
            "alt_titles": [{"en": "Old title"}],
            "language": "en",
            "category_id": "category",
        }
    )
    response = await client.post(f"/v2/{kind}", json=data | extra)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.parametrize(
    "kind,primary,alternatives", [("persons", "name", "alt_names"), ("texts", "title", "alt_titles")]
)
@pytest.mark.parametrize("alternatives_value", [[], [{"en": "New"}]])
async def test_patch_primary_can_clear_all_alternatives(
    client, test_database, kind, primary, alternatives, alternatives_value
):
    record_id = await create_record(client, kind)
    response = await client.patch(
        f"/v2/{kind}/{record_id}", json={primary: {"en": "New"}, alternatives: alternatives_value}
    )
    assert response.status_code == 200, response.text
    result = (await client.get(f"/v2/{kind}/{record_id}")).json()
    assert result[primary] == {"en": "New"}
    assert not result[alternatives]


@pytest.mark.parametrize("kind,repository_name", [("persons", "person"), ("texts", "text")])
async def test_concurrent_scalar_patches_preserve_omitted_fields(
    client, test_database, kind, repository_name
):
    record_id = await create_record(client, kind)
    repository = getattr(test_database, repository_name)
    responses = await asyncio.gather(
        client.patch(f"/v2/{kind}/{record_id}", json={"bdrc": "P-concurrent" if kind == "persons" else "W-concurrent"}),
        client.patch(f"/v2/{kind}/{record_id}", json={"wiki": "Q-concurrent"}),
    )
    assert [response.status_code for response in responses] == [200, 200], [r.text for r in responses]
    result = await repository.get(record_id)
    assert result.bdrc == ("P-concurrent" if kind == "persons" else "W-concurrent")
    assert result.wiki == "Q-concurrent"


async def test_concurrent_title_and_language_updates_validate_current_state(client, test_database):
    text_id = await create_record(client, "texts")
    responses = await asyncio.gather(
        client.patch(f"/v2/texts/{text_id}", json={"language": "bo"}),
        client.patch(f"/v2/texts/{text_id}", json={"title": {"en": "English only"}}),
    )
    assert sorted(r.status_code for r in responses) == [200, 422], [r.text for r in responses]
    response = await client.get(f"/v2/texts/{text_id}")
    assert response.status_code == 200
    TextOutput.model_validate(response.json())
    result = response.json()
    if responses[0].status_code == 200:
        assert result["language"] == "bo"
        assert result["title"] == {"en": "Text", "bo": "བོད།"}
    else:
        assert result["language"] == "en"
        assert result["title"] == {"en": "English only"}


@pytest.mark.parametrize("kind", ["note", "mark", "bibliographic"])
@pytest.mark.parametrize("metadata", [None, {}, {"name": "Editorial review"}])
async def test_single_span_metadata_roundtrip_and_delete(test_database, kind, metadata):
    db = test_database
    text_id = await db.text.create(TextInput(title={"en": "Annotation text"}, language="en", category_id="category"))
    edition_id = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition_id, text_id, content_length=10)
    annotation = getattr(db.annotation, kind)
    fields = {"span": {"start": 2, "end": 5}, "metadata": metadata}
    if kind == "note":
        annotation_id = await annotation.add_durchen(edition_id, NoteInput(**fields, text="A note"))
    elif kind == "mark":
        annotation_id = await annotation.add_yigchung(edition_id, MarkInput(**fields))
    else:
        annotation_id = await annotation.add(edition_id, BibliographicMetadataInput(**fields, type="title"))
    single = await annotation.get(annotation_id)
    listed = await annotation.get_all(edition_id)
    assert listed == [single]
    assert (single.metadata.model_dump(exclude_none=True) if single.metadata is not None else None) == metadata
    await annotation.delete(annotation_id)
    with pytest.raises(DataNotFoundError):
        await annotation.get(annotation_id)
    async with db.get_session() as session:
        result = await session.run("MATCH (m:AnnotationMetadata) RETURN count(m) AS remaining")
        assert (await result.single())["remaining"] == 0


@pytest.mark.parametrize("deletion", ["edition", "content"])
async def test_bulk_annotation_deletion_removes_metadata(test_database, deletion):
    db = test_database
    text_id = await db.text.create(TextInput(title={"en": "Annotation text"}, language="en", category_id="category"))
    edition_id = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition_id, text_id, content_length=10)
    fields = {"span": {"start": 2, "end": 5}, "metadata": {"name": "Review"}}
    await db.annotation.note.add_durchen(edition_id, NoteInput(**fields, text="A note"))
    await db.annotation.mark.add_yigchung(edition_id, MarkInput(**fields))
    await db.annotation.bibliographic.add(edition_id, BibliographicMetadataInput(**fields, type="title"))
    async with db.get_session() as session:
        before = await (await session.run("MATCH (m:AnnotationMetadata) RETURN count(m) AS count")).single()
        assert before["count"] == 3
    if deletion == "edition":
        await db.edition.delete(edition_id)
    else:
        async with db.get_session() as session:
            await session.execute_write(db.span.adjust_with_transaction, edition_id, 2, 5, 0)
    async with db.get_session() as session:
        result = await session.run("MATCH (m:AnnotationMetadata) RETURN count(m) AS remaining")
        assert (await result.single())["remaining"] == 0
