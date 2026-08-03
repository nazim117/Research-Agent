# test_semantic_cache.py — unit tests for SemanticCacheStore + embed_cached.
#
# Each test runs against a fresh temp SQLite file in tmp_path so tests are
# fully isolated. No mocking of SQLite, no network — embed() itself is
# monkeypatched for embed_cached tests since it would otherwise hit the real
# embeddings service.

import pytest

import semantic_cache
from semantic_cache import SemanticCacheStore, embed_cached


async def make_store(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    store = SemanticCacheStore(db_path)
    await store.init()
    return store


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    """Calling init() twice on the same store does not raise."""
    store = await make_store(tmp_path)
    await store.init()


@pytest.mark.asyncio
async def test_get_returns_none_on_miss(tmp_path):
    store = await make_store(tmp_path)
    assert await store.get("proj-1", "hello") is None


@pytest.mark.asyncio
async def test_set_then_get_round_trips_vector(tmp_path):
    store = await make_store(tmp_path)
    await store.set("proj-1", "hello", [0.1, 0.2, 0.3])
    assert await store.get("proj-1", "hello") == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_get_strips_whitespace_before_hashing(tmp_path):
    """Normalization is leading/trailing whitespace only."""
    store = await make_store(tmp_path)
    await store.set("proj-1", "hello", [0.1, 0.2, 0.3])
    assert await store.get("proj-1", "  hello  ") == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_get_is_case_sensitive(tmp_path):
    """No casefolding — different case is a different cache key."""
    store = await make_store(tmp_path)
    await store.set("proj-1", "Hello", [0.1, 0.2, 0.3])
    assert await store.get("proj-1", "hello") is None


@pytest.mark.asyncio
async def test_scoped_by_project(tmp_path):
    store = await make_store(tmp_path)
    await store.set("proj-a", "hello", [0.1, 0.2, 0.3])
    assert await store.get("proj-b", "hello") is None
    assert await store.get("proj-a", "hello") == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_repeated_set_is_ignored_keeps_first_value(tmp_path):
    """set() on an existing key is a no-op (INSERT OR IGNORE) — first value wins."""
    store = await make_store(tmp_path)
    await store.set("proj-1", "hello", [0.1, 0.2, 0.3])
    await store.set("proj-1", "hello", [9.9, 9.9, 9.9])
    assert await store.get("proj-1", "hello") == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_get_increments_hit_count(tmp_path):
    store = await make_store(tmp_path)
    await store.set("proj-1", "hello", [0.1, 0.2, 0.3])
    await store.get("proj-1", "hello")
    await store.get("proj-1", "hello")

    stats = await store.get_stats("proj-1")
    assert stats.entry_count == 1
    assert stats.total_hits == 2


@pytest.mark.asyncio
async def test_get_stats_scoped_to_project(tmp_path):
    store = await make_store(tmp_path)
    await store.set("proj-a", "hello", [0.1])
    await store.set("proj-b", "world", [0.2])
    await store.get("proj-a", "hello")

    a_stats = await store.get_stats("proj-a")
    b_stats = await store.get_stats("proj-b")
    assert a_stats.entry_count == 1
    assert a_stats.total_hits == 1
    assert b_stats.entry_count == 1
    assert b_stats.total_hits == 0


@pytest.mark.asyncio
async def test_get_stats_empty_project(tmp_path):
    store = await make_store(tmp_path)
    stats = await store.get_stats("does-not-exist")
    assert stats.entry_count == 0
    assert stats.total_hits == 0


@pytest.mark.asyncio
async def test_delete_by_project_removes_only_that_project(tmp_path):
    store = await make_store(tmp_path)
    await store.set("proj-a", "hello", [0.1])
    await store.set("proj-b", "world", [0.2])

    await store.delete_by_project("proj-a")

    assert await store.get("proj-a", "hello") is None
    assert await store.get("proj-b", "world") == [0.2]


@pytest.mark.asyncio
async def test_embed_cached_miss_calls_embed_and_caches(tmp_path, monkeypatch):
    store = await make_store(tmp_path)
    calls = []

    async def fake_embed(text):
        calls.append(text)
        return [1.0, 2.0, 3.0]

    monkeypatch.setattr(semantic_cache, "embed", fake_embed)

    vector = await embed_cached(store, "proj-1", "hello")
    assert vector == [1.0, 2.0, 3.0]
    assert calls == ["hello"]


@pytest.mark.asyncio
async def test_embed_cached_hit_does_not_call_embed_again(tmp_path, monkeypatch):
    store = await make_store(tmp_path)
    calls = []

    async def fake_embed(text):
        calls.append(text)
        return [1.0, 2.0, 3.0]

    monkeypatch.setattr(semantic_cache, "embed", fake_embed)

    await embed_cached(store, "proj-1", "hello")
    await embed_cached(store, "proj-1", "hello")

    assert calls == ["hello"]  # only the first call actually embedded


pytestmark = pytest.mark.asyncio
