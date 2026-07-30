# tests/test_mcp_integration.py — real mcp-server HTTP integration tests.
#
# These tests call MCPClient.call() against a live mcp-server (port 8083).
# Skipped when the mcp-server is unreachable (conftest.mcp_up).
#
# What is covered:
#   - The real chat-agent ↔ mcp-server HTTP boundary
#   - MCPClient.call() happy path (memory_set / memory_get)

import pytest

from mcp_client import MCPClient

pytestmark = pytest.mark.integration


@pytest.fixture
def mcp(mcp_up):
    """MCPClient pointed at the live mcp-server."""
    from config import settings
    return MCPClient(base_url=settings.mcp_base_url, timeout=10.0)


# ─── memory tools (no creds needed) ──────────────────────────────────────────

async def test_memory_set_and_get(mcp):
    """memory_set then memory_get returns the stored value."""
    key = "integration_test_key"
    value = "integration_test_value"

    # Store a value.
    set_result = await mcp.call("memory_set", {"key": key, "value": value}, project_id="test")
    # mcp-server returns {} or a confirmation dict — just assert no error raised.
    assert isinstance(set_result, dict)

    # Retrieve the value.
    get_result = await mcp.call("memory_get", {"key": key}, project_id="test")
    assert isinstance(get_result, dict)
    # The result should contain the value we stored.
    assert get_result.get("value") == value or value in str(get_result)


async def test_memory_set_overwrites(mcp):
    """Calling memory_set twice with the same key stores the latest value."""
    key = "integration_test_overwrite"

    await mcp.call("memory_set", {"key": key, "value": "first"}, project_id="test")
    await mcp.call("memory_set", {"key": key, "value": "second"}, project_id="test")

    result = await mcp.call("memory_get", {"key": key}, project_id="test")
    assert "second" in str(result)


# ─── health check ─────────────────────────────────────────────────────────────

async def test_mcp_server_health(mcp_up):
    """GET /health returns 200 and {"status":"healthy"}."""
    import httpx

    from config import settings

    async with httpx.AsyncClient() as client:
        r = await client.get(f"{settings.mcp_base_url}/health", timeout=5.0)

    assert r.status_code == 200
    assert r.json().get("status") == "healthy"
