# test_entity.py — unit tests for EntityStore + extract_entities.
#
# Each store test runs against a fresh temp SQLite file in tmp_path so tests
# are fully isolated. No mocking, no network. extract_entities is tested
# with a fake chat_fn, same pattern as transcript.py's extraction tests.

import json

import pytest

from entity import EntityStore, extract_entities


async def make_store(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    store = EntityStore(db_path)
    await store.init()
    return store


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    store = await make_store(tmp_path)
    await store.init()


@pytest.mark.asyncio
async def test_upsert_creates_new_entity(tmp_path):
    store = await make_store(tmp_path)
    entity = await store.upsert_entity("proj-1", "Alice", "person", "PM on the project", "meeting-1")
    assert entity.name == "Alice"
    assert entity.type == "person"
    assert entity.attributes == "PM on the project"
    assert json.loads(entity.sources) == ["meeting-1"]


@pytest.mark.asyncio
async def test_upsert_merges_sources_on_repeat_with_different_source(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    entity = await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-2")
    assert json.loads(entity.sources) == ["meeting-1", "meeting-2"]


@pytest.mark.asyncio
async def test_upsert_does_not_duplicate_same_source(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    entity = await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    assert json.loads(entity.sources) == ["meeting-1"]


@pytest.mark.asyncio
async def test_upsert_overwrites_attributes_when_new_text_present(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    entity = await store.upsert_entity("proj-1", "Alice", "person", "Now eng lead", "meeting-2")
    assert entity.attributes == "Now eng lead"


@pytest.mark.asyncio
async def test_upsert_keeps_old_attributes_when_new_is_empty(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    entity = await store.upsert_entity("proj-1", "Alice", "person", None, "meeting-2")
    assert entity.attributes == "PM"


@pytest.mark.asyncio
async def test_different_type_creates_separate_entity(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Acme", "organization", "vendor", "doc-1")
    await store.upsert_entity("proj-1", "Acme", "ticket", "ticket key coincidence", "doc-2")
    rows = await store.list_by_project("proj-1")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_get_by_name_returns_none_when_missing(tmp_path):
    store = await make_store(tmp_path)
    assert await store.get_by_name("proj-1", "Nobody", "person") is None


@pytest.mark.asyncio
async def test_get_by_name_returns_entity(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    entity = await store.get_by_name("proj-1", "Alice", "person")
    assert entity is not None
    assert entity.name == "Alice"


@pytest.mark.asyncio
async def test_list_by_project_orders_by_updated_at_desc(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    await store.upsert_entity("proj-1", "Bob", "person", "eng", "meeting-1")
    await store.upsert_entity("proj-1", "Alice", "person", "still PM", "meeting-2")
    rows = await store.list_by_project("proj-1")
    assert rows[0].name == "Alice"


@pytest.mark.asyncio
async def test_list_by_project_scoped_to_project(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    await store.upsert_entity("proj-2", "Bob", "person", "eng", "meeting-1")
    rows = await store.list_by_project("proj-1")
    assert len(rows) == 1
    assert rows[0].name == "Alice"


@pytest.mark.asyncio
async def test_find_matching_returns_multiple_hits(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    await store.upsert_entity("proj-1", "Bob", "person", "eng", "meeting-1")
    await store.upsert_entity("proj-1", "Carol", "person", "design", "meeting-1")
    hits = await store.find_matching("proj-1", "What did Alice and Bob decide?")
    names = {e.name for e in hits}
    assert names == {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_find_matching_returns_empty_list_on_no_hit(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    hits = await store.find_matching("proj-1", "no relevant names here")
    assert hits == []


@pytest.mark.asyncio
async def test_find_matching_caps_at_limit(tmp_path):
    store = await make_store(tmp_path)
    names = [f"Person{i}" for i in range(8)]
    for n in names:
        await store.upsert_entity("proj-1", n, "person", None, "doc-1")
    message = " ".join(names)
    hits = await store.find_matching("proj-1", message)
    assert len(hits) == 5


@pytest.mark.asyncio
async def test_delete_by_project_removes_all_rows(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    await store.upsert_entity("proj-1", "Bob", "person", "eng", "meeting-1")
    await store.delete_by_project("proj-1")
    assert await store.list_by_project("proj-1") == []


@pytest.mark.asyncio
async def test_delete_by_project_does_not_affect_other_projects(tmp_path):
    store = await make_store(tmp_path)
    await store.upsert_entity("proj-1", "Alice", "person", "PM", "meeting-1")
    await store.upsert_entity("proj-2", "Bob", "person", "eng", "meeting-1")
    await store.delete_by_project("proj-1")
    rows = await store.list_by_project("proj-2")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# extract_entities
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extract_entities_parses_valid_json():
    async def fake_chat(messages):
        return json.dumps({
            "entities": [
                {"name": "Alice", "type": "person", "attributes": "PM"},
                {"name": "KAN-8", "type": "ticket", "attributes": None},
            ]
        })

    result = await extract_entities("some transcript text", fake_chat)
    assert len(result) == 2
    assert result[0]["name"] == "Alice"
    assert result[1]["type"] == "ticket"


@pytest.mark.asyncio
async def test_extract_entities_normalises_missing_key():
    async def fake_chat(messages):
        return json.dumps({})

    result = await extract_entities("text with nothing extractable", fake_chat)
    assert result == []


@pytest.mark.asyncio
async def test_extract_entities_strips_markdown_fence():
    async def fake_chat(messages):
        return '```json\n{"entities": [{"name": "Bob", "type": "person", "attributes": null}]}\n```'

    result = await extract_entities("text", fake_chat)
    assert result == [{"name": "Bob", "type": "person", "attributes": None}]


@pytest.mark.asyncio
async def test_extract_entities_raises_on_invalid_json():
    async def fake_chat(messages):
        return "not json at all"

    with pytest.raises(ValueError):
        await extract_entities("text", fake_chat)
