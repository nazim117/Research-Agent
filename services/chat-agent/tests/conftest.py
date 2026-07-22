# tests/conftest.py — shared fixtures for integration and e2e test tiers.
#
# Fixtures defined here are available to every test file without explicit import.
# Two tiers live here:
#
#   integration — each test hits ONE real external dependency
#                 (Qdrant, Ollama, or mcp-server). Tests are skipped when the
#                 dependency is unreachable so plain `pytest` stays green.
#
# Usage:
#   pytest -m integration   # run only integration tests (need real deps)
#   pytest -m "not integration and not e2e"  # unit tier only (CI default)

from __future__ import annotations

import os
import sys
import uuid

import httpx
import pytest
import pytest_asyncio

# ─── sys.path shim ────────────────────────────────────────────────────────────
# Allow `import main`, `import config`, etc. when pytest is invoked from the
# repo root or from services/chat-agent.  This supersedes the per-file shims
# that test_briefing.py / test_transcript.py / test_standup.py used to include.

_SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)


# ─── Reachability helpers ─────────────────────────────────────────────────────
# Each helper performs a synchronous HTTP probe via httpx.  We use synchronous
# here on purpose: pytest fixtures that call pytest.skip() work best without
# being inside an async context (pytest-asyncio handles the async tests
# themselves, but the skip decision should be instant and synchronous).

def _is_reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            return r.status_code < 500
    except Exception:
        return False


# ─── Dependency-gating fixtures ──────────────────────────────────────────────
# Each fixture calls pytest.skip() when its service is down.  Any test that
# depends on one of these fixtures is therefore silently skipped rather than
# failing when the infrastructure is absent.

@pytest.fixture(scope="session")
def qdrant_up():
    """Skip if Qdrant is not reachable at settings.qdrant_url."""
    from config import settings
    if not _is_reachable(f"{settings.qdrant_url}/collections"):
        pytest.skip(f"Qdrant not reachable at {settings.qdrant_url}")


@pytest.fixture(scope="session")
def ollama_up():
    """Skip if Ollama is not reachable at settings.ollama_base_url."""
    from config import settings
    if not _is_reachable(f"{settings.ollama_base_url}/api/tags"):
        pytest.skip(f"Ollama not reachable at {settings.ollama_base_url}")


@pytest.fixture(scope="session")
def embeddings_up():
    """Skip if the embeddings service (TEI) is not reachable at
    settings.embeddings_base_url. Embeddings no longer go through Ollama —
    see services/chat-agent/embeddings.py.
    """
    from config import settings
    if not _is_reachable(f"{settings.embeddings_base_url}/health"):
        pytest.skip(f"Embeddings service not reachable at {settings.embeddings_base_url}")


@pytest.fixture(scope="session")
def ollama_chat_model_up(ollama_up):
    """Skip if settings.ollama_chat_model is not pulled in the local Ollama instance.

    This prevents LLM integration tests from failing with a 404 when the
    configured model (default: llama3) hasn't been pulled yet.  Run
    `ollama pull <model>` to make these tests pass.
    """
    import httpx
    from config import settings

    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{settings.ollama_base_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
        # Ollama tags include version suffix (e.g. "llama3:latest"); check prefix.
        model_base = settings.ollama_chat_model.split(":")[0]
        available = any(m.split(":")[0] == model_base for m in models)
        if not available:
            pytest.skip(
                f"Ollama chat model '{settings.ollama_chat_model}' not pulled. "
                f"Available: {models or ['(none)']}.  Run: ollama pull {settings.ollama_chat_model}"
            )
    except Exception as exc:
        pytest.skip(f"Could not check Ollama model list: {exc}")


@pytest.fixture(scope="session")
def mcp_up():
    """Skip if mcp-server is not reachable at settings.mcp_base_url."""
    from config import settings
    if not _is_reachable(f"{settings.mcp_base_url}/health"):
        pytest.skip(f"mcp-server not reachable at {settings.mcp_base_url}")


# ─── Qdrant helper fixtures ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def real_vstore(qdrant_up):
    """A VectorStore connected to the live Qdrant instance.

    Calls ensure_collection so both production collection names exist before
    any test runs.  The VectorStore itself does not hold persistent state, so
    no teardown is needed on this fixture.
    """
    from config import settings
    from vectors import VectorStore

    vs = VectorStore(url=settings.qdrant_url)
    await vs.ensure_collection(settings.qdrant_collection, dim=768)
    await vs.ensure_collection(settings.qdrant_docs_collection, dim=768)
    return vs


@pytest_asyncio.fixture
async def unique_project(real_vstore):
    """Yield a fresh UUID project_id and clean up its vectors after the test.

    This fixture is the core of our integration-test isolation strategy:
    every test writes into a UUID-keyed partition that no other test can see,
    and the teardown removes all those vectors so the Qdrant collections are
    left exactly as they were.
    """
    from config import settings

    pid = str(uuid.uuid4())
    yield pid

    # Teardown: remove all test data from both collections.
    for coll in (settings.qdrant_collection, settings.qdrant_docs_collection):
        try:
            await real_vstore.delete_by_project(coll, pid)
        except Exception:
            pass  # best-effort cleanup; don't mask test failures
