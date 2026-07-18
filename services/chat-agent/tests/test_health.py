"""Unit tests for health.py — aggregate dependency reachability check.

Each dependency probe is mocked independently (no real network) to verify
that one dependency being down never masks or blocks the others' status.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from health import check_detailed_health


class _FakeAsyncClient:
    """Fake httpx.AsyncClient supporting only the `async with ... .get(url)` shape
    that health.py's probes use.
    """

    def __init__(self, get_impl):
        self._get_impl = get_impl

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        return self._get_impl(url)


def _resp_ok() -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    return r


def _make_get_impl(down_urls=()):
    def get_impl(url):
        if any(bad in url for bad in down_urls):
            raise ConnectionError(f"simulated down: {url}")
        return _resp_ok()
    return get_impl


def _settings():
    return SimpleNamespace(
        ollama_base_url="http://ollama-test:1",
        qdrant_url="http://qdrant-test:2",
    )


def _mcp(healthy: bool = True):
    mcp = AsyncMock()
    if healthy:
        mcp.get_health = AsyncMock(return_value={"status": "healthy"})
    else:
        mcp.get_health = AsyncMock(side_effect=Exception("mcp-server unreachable"))
    return mcp


@pytest.mark.asyncio
async def test_all_healthy():
    with (
        patch("health.httpx.AsyncClient", side_effect=lambda **kw: _FakeAsyncClient(_make_get_impl())),
        patch("health.subprocess.run", return_value=MagicMock(returncode=0, stdout="24.0.0\n")),
    ):
        result = await check_detailed_health(_settings(), _mcp(healthy=True))

    assert result["ollama"] == {"status": "ok", "detail": "reachable at http://ollama-test:1", "required": True}
    assert result["qdrant"]["status"] == "ok"
    assert result["qdrant"]["required"] is True
    assert result["docker"]["status"] == "ok"
    assert result["docker"]["required"] is False
    assert result["mcp_server"]["status"] == "ok"
    assert result["mcp_server"]["required"] is False


@pytest.mark.asyncio
async def test_ollama_down_does_not_affect_others():
    with (
        patch(
            "health.httpx.AsyncClient",
            side_effect=lambda **kw: _FakeAsyncClient(_make_get_impl(down_urls=["ollama-test"])),
        ),
        patch("health.subprocess.run", return_value=MagicMock(returncode=0, stdout="24.0.0\n")),
    ):
        result = await check_detailed_health(_settings(), _mcp(healthy=True))

    assert result["ollama"]["status"] == "error"
    assert result["ollama"]["required"] is True
    assert result["qdrant"]["status"] == "ok"
    assert result["mcp_server"]["status"] == "ok"


@pytest.mark.asyncio
async def test_docker_cli_missing_is_optional_failure():
    with (
        patch("health.httpx.AsyncClient", side_effect=lambda **kw: _FakeAsyncClient(_make_get_impl())),
        patch("health.subprocess.run", side_effect=FileNotFoundError()),
    ):
        result = await check_detailed_health(_settings(), _mcp(healthy=True))

    assert result["docker"]["status"] == "error"
    assert result["docker"]["required"] is False
    assert "not found" in result["docker"]["detail"]
    # Docker being unavailable must not affect required dependencies.
    assert result["ollama"]["status"] == "ok"
    assert result["qdrant"]["status"] == "ok"


@pytest.mark.asyncio
async def test_mcp_server_down_is_optional_failure():
    with (
        patch("health.httpx.AsyncClient", side_effect=lambda **kw: _FakeAsyncClient(_make_get_impl())),
        patch("health.subprocess.run", return_value=MagicMock(returncode=0, stdout="24.0.0\n")),
    ):
        result = await check_detailed_health(_settings(), _mcp(healthy=False))

    assert result["mcp_server"]["status"] == "error"
    assert result["mcp_server"]["required"] is False
    assert result["ollama"]["status"] == "ok"
    assert result["qdrant"]["status"] == "ok"
