import asyncio

import pytest
import pytest_asyncio
from pydantic import ValidationError

import database.tag_database as tag_database
from exceptions import DataConflictError
from identifier import generate_id
from models.annotation import SegmentationInput
from models.category import CategoryInput
from models.edition import EditionInput
from models.tag import TagInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def work(test_database):
    db = test_database
    await db.application.create("other", "Other")
    a = await db.tag.create(TagInput(title={"en": "A"}), "test_application")
    b = await db.tag.create(TagInput(title={"en": "B"}), "other")
    category = await db.category.create(CategoryInput(title={"en": "Another category"}), "test_application")
    foreign_category = await db.category.create(CategoryInput(title={"en": "Foreign category"}), "other")
    text = await db.text.create(
        TextInput(title={"bo": "Source"}, language="bo", category_id="category", tag_ids=[a, b])
    )
    _, key = await db.api_key.create(generate_id(), "Bound", "test@example.com", "test_application")
    return {
        "text": text,
        "a": a,
        "b": b,
        "category": category,
        "foreign_category": foreign_category,
        "headers": {"X-API-Key": key, "X-Application": "test_application"},
    }


@pytest.mark.parametrize("category", [None, "category", "different"])
async def test_translation_rejects_any_category(category):
    with pytest.raises(ValidationError, match="must not be supplied"):
        TextInput(title={"en": "Translation"}, language="en", translation_of="source", category_id=category)


@pytest.mark.parametrize("relation", [{}, {"commentary_of": "source"}])
async def test_new_work_requires_category(relation):
    with pytest.raises(ValidationError, match="category_id is required"):
        TextInput(title={"en": "Text"}, language="en", **relation)


async def test_tagged_translation_inherits_category(auth_client, test_database, work):
    response = await auth_client.post(
        "/v2/texts",
        headers=work["headers"],
        json={
            "title": {"en": "Translation"},
            "language": "en",
            "translation_of": work["text"],
            "tag_ids": [work["a"]],
        },
    )
    assert response.status_code == 201, response.text
    translated = await test_database.text.get(response.json()["id"])
    assert translated.category_id == "category"
    assert set(translated.tag_ids) == {work["a"], work["b"]}
    assert await test_database.text.get_work_id(translated.id) == await test_database.text.get_work_id(work["text"])


async def test_bound_key_cannot_create_text_with_foreign_category(auth_client, test_database, work):
    before = {text.id for text in await test_database.text.get_all(offset=0, limit=100, filters=None)}
    response = await auth_client.post(
        "/v2/texts",
        headers=work["headers"],
        json={
            "title": {"en": "Unauthorized"},
            "language": "en",
            "category_id": work["foreign_category"],
        },
    )
    assert response.status_code == 403, response.text
    assert {text.id for text in await test_database.text.get_all(offset=0, limit=100, filters=None)} == before


async def test_tag_replacement_preserves_other_application(auth_client, test_database, work):
    response = await auth_client.patch(f"/v2/texts/{work['text']}", headers=work["headers"], json={"tag_ids": []})
    assert response.status_code == 200, response.text
    assert (await test_database.text.get(work["text"])).tag_ids == [work["b"]]
    response = await auth_client.patch(
        f"/v2/texts/{work['text']}", headers=work["headers"], json={"tag_ids": [work["b"]]}
    )
    assert response.status_code == 403, response.text
    assert (await test_database.text.get(work["text"])).tag_ids == [work["b"]]


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_foreign_tag_cannot_be_attached_or_removed(auth_client, test_database, work, method):
    edition_id = generate_id()
    await test_database.edition.create(
        EditionInput(type="critical"),
        edition_id,
        work["text"],
        content_length=10,
        segmentation=SegmentationInput(segments=[{"lines": [{"start": 0, "end": 10}]}]),
    )
    segment = (await test_database.annotation.segmentation.get_all_segments_by_edition(edition_id))[0]
    await test_database.tag.tag_segment(segment.id, work["b"])
    for resource, resource_id in (("texts", work["text"]), ("segments", segment.id)):
        response = await auth_client.request(
            method, f"/v2/{resource}/{resource_id}/tags/{work['b']}", headers=work["headers"]
        )
        assert response.status_code == 404, response.text
    assert work["b"] in (await test_database.text.get(work["text"])).tag_ids
    assert work["b"] in (await test_database.segment.get(segment.id)).tag_ids


async def test_category_change_checks_owner_and_expected_value(auth_client, test_database, work):
    url = f"/v2/texts/{work['text']}"
    for body, status in (
        ({"category_id": "category"}, 200),
        ({"category_id": work["category"]}, 409),
        ({"category_id": work["foreign_category"], "expected_category_id": "category"}, 403),
        ({"category_id": work["category"], "expected_category_id": "category"}, 200),
        ({"category_id": "category", "expected_category_id": "category"}, 409),
    ):
        response = await auth_client.patch(url, headers=work["headers"], json=body)
        assert response.status_code == status, response.text
    assert (await test_database.text.get(work["text"])).category_id == work["category"]


async def test_cannot_replace_another_applications_existing_category(auth_client, test_database, work):
    foreign = await test_database.text.create(
        TextInput(title={"en": "Foreign work"}, language="en", category_id=work["foreign_category"])
    )
    response = await auth_client.patch(
        f"/v2/texts/{foreign}",
        headers=work["headers"],
        json={
            "category_id": "category",
            "expected_category_id": work["foreign_category"],
        },
    )
    assert response.status_code == 403, response.text
    assert (await test_database.text.get(foreign)).category_id == work["foreign_category"]


async def test_category_edits_through_translations_share_one_lock(auth_client, test_database, work):
    translation = await test_database.text.create(
        TextInput(title={"en": "Translation"}, language="en", translation_of=work["text"])
    )
    other_category = await test_database.category.create(
        CategoryInput(title={"en": "Third category"}), "test_application"
    )
    responses = await asyncio.gather(
        *(
            auth_client.patch(
                f"/v2/texts/{text_id}",
                headers=work["headers"],
                json={
                    "category_id": category_id,
                    "expected_category_id": "category",
                },
            )
            for text_id, category_id in ((work["text"], work["category"]), (translation, other_category))
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert (await test_database.text.get(translation)).category_id == (
        await test_database.text.get(work["text"])
    ).category_id


async def test_bound_keys_cannot_administer_applications(auth_client, test_database, work):
    response = await auth_client.post("/v2/applications", headers=work["headers"], json={"name": "new-app"})
    assert response.status_code == 403, response.text
    await test_database.application.create("empty-app", "Empty")
    response = await auth_client.delete("/v2/applications/empty-app", headers=work["headers"])
    assert response.status_code == 403, response.text
    assert await test_database.application.exists("empty-app")


async def test_taxonomy_preserves_full_language_tags(test_database):
    title = {"en": "Generic", "en-US": "American"}
    tag_id = await test_database.tag.create(TagInput(title=title, description=title), "test_application")
    tag = next(t for t in await test_database.tag.get_all("test_application") if t.id == tag_id)
    category_id = await test_database.category.create(CategoryInput(title=title, description=title), "test_application")
    category = await test_database.category.get_by_id(category_id, "test_application")
    for record in (tag, category):
        assert record.title.root == title
        assert record.description.root == title


async def test_deleting_shared_work_tag_updates_every_translation(test_database, work):
    db = test_database
    translation = await db.text.create(TextInput(title={"en": "Projection translation"}, language="en", translation_of=work["text"]))

    await db.tag.delete(work["a"], "test_application")
    for text_id in (work["text"], translation):
        assert work["a"] not in (await db.text.get(text_id)).tag_ids


async def test_tag_deletion_rejects_attachment_added_while_acquiring_locks(test_database, work, monkeypatch):
    db = test_database
    other = await db.text.create(TextInput(title={"en": "Concurrent tag"}, language="en", category_id="category"))
    other_work = await db.text.get_work_id(other)
    original_work = await db.text.get_work_id(work["text"])
    lock_nodes = tag_database.lock_nodes
    attached = False

    async def attach_before_tag_lock(tx, label, ids):
        nonlocal attached
        if label == "Tag" and not attached:
            attached = True
            await db.tag.tag_work(other_work, work["a"], "test_application")
        await lock_nodes(tx, label, ids)

    with monkeypatch.context() as patch:
        patch.setattr(tag_database, "lock_nodes", attach_before_tag_lock)
        with pytest.raises(DataConflictError, match="Tag attachments changed"):
            await db.tag.delete(work["a"], "test_application")

    for text_id in (work["text"], other):
        assert work["a"] in (await db.text.get(text_id)).tag_ids
    assert set(await db.tag.delete(work["a"], "test_application")) == {original_work, other_work}


@pytest.mark.parametrize('scope', ['test_application', None])
async def test_unbound_key_scope_controls_shared_work_tag_replacement(auth_client, test_database, work, scope):
    _, key = await test_database.api_key.create(generate_id(), 'Admin', 'admin@example.com')
    headers = {'X-API-Key': key}
    if scope is not None:
        headers['X-Application'] = scope
    response = await auth_client.patch(f"/v2/texts/{work['text']}", headers=headers, json={'tag_ids': []})
    assert response.status_code == 200, response.text
    assert (await test_database.text.get(work['text'])).tag_ids == ([work['b']] if scope else [])
    foreign = await test_database.text.create(TextInput(title={'en': 'Foreign category admin'}, language='en', category_id=work['foreign_category']))
    response = await auth_client.patch(f'/v2/texts/{foreign}', headers=headers, json={'category_id': 'category', 'expected_category_id': work['foreign_category']})
    assert response.status_code == (403 if scope else 200), response.text
    assert (await test_database.text.get(foreign)).category_id == (work['foreign_category'] if scope else 'category')
