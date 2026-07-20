"""HTTP-level tests for the Settings/Wizard support routes added to main.py:
GET /health/detailed, GET /models, POST /models/pull, GET /config,
GET /integrations/status, GET /config/env, PUT /config/env/{key}.

Follows test_actions_api.py's pattern: AsyncClient + ASGITransport against
the real app, with singletons monkeypatched so no real network is used.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from mcp_client import MCPError


@pytest.mark.asyncio
async def test_health_detailed_relays_aggregate_result():
    fake_result = {
        "ollama": {"status": "ok", "detail": "reachable", "required": True},
        "qdrant": {"status": "ok", "detail": "reachable", "required": True},
        "docker": {"status": "error", "detail": "not found", "required": False},
        "mcp_server": {"status": "ok", "detail": "reachable", "required": False},
    }
    with patch("main.check_detailed_health", new_callable=AsyncMock, return_value=fake_result):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health/detailed")

    assert resp.status_code == 200
    assert resp.json() == fake_result


@pytest.mark.asyncio
async def test_config_never_includes_secrets():
    with patch("main.settings") as s:
        s.llm_provider = "openai_compatible"
        s.ollama_chat_model = "llama3"
        s.ollama_embed_model = "nomic-embed-text"
        s.ollama_base_url = "http://localhost:11434"
        s.openai_model = "deepseek-chat"
        s.openai_provider_label = "DeepSeek"
        s.openai_base_url = "https://api.deepseek.com/v1"
        s.openai_api_key = "sk-this-must-never-appear-in-the-response"

        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openai_compatible"
    assert body["ollama"]["chat_model"] == "llama3"
    assert body["openai"]["model"] == "deepseek-chat"
    assert body["openai"]["configured"] is True
    # The secret itself must never be serialized.
    assert "sk-this-must-never-appear-in-the-response" not in resp.text
    assert "api_key" not in resp.text


@pytest.mark.asyncio
async def test_models_lists_installed_models():
    fake_tags_response = MagicMock()
    fake_tags_response.raise_for_status.return_value = None
    fake_tags_response.json.return_value = {"models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}]}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return fake_tags_response

    with patch("ollama_models.httpx.AsyncClient", return_value=_FakeClient()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/models")

    assert resp.status_code == 200
    assert resp.json() == {"installed": ["llama3:latest", "mistral:latest"]}


@pytest.mark.asyncio
async def test_integrations_status_proxies_mcp_server():
    fake_status = {"jira": {"configured": True, "base_url": "https://example.atlassian.net"}, "github": {"configured": False}}
    with patch("main._mcp") as mcp:
        mcp.get_integrations_status = AsyncMock(return_value=fake_status)
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/integrations/status")

    assert resp.status_code == 200
    assert resp.json() == fake_status


@pytest.mark.asyncio
async def test_integrations_status_maps_mcp_error_to_http_status():
    with patch("main._mcp") as mcp:
        mcp.get_integrations_status = AsyncMock(side_effect=MCPError("mcp-server unreachable", status_code=502))
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/integrations/status")

    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_get_config_env_merges_local_and_remote_vars():
    local_vars = [{"key": "LLM_PROVIDER", "secret": False, "configured": True, "hint": "ollama"}]
    remote_vars = [{"key": "GITHUB_TOKEN", "secret": True, "configured": True, "hint": "…1234"}]
    with (
        patch("main.env_config.list_env_vars", return_value=local_vars),
        patch("main._mcp") as mcp,
    ):
        mcp.get_env_vars = AsyncMock(return_value=remote_vars)
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/config/env")

    assert resp.status_code == 200
    assert resp.json() == {"vars": local_vars + remote_vars, "mcp_error": None}


@pytest.mark.asyncio
async def test_get_config_env_degrades_when_mcp_server_unreachable():
    local_vars = [{"key": "LLM_PROVIDER", "secret": False, "configured": True, "hint": "ollama"}]
    with (
        patch("main.env_config.list_env_vars", return_value=local_vars),
        patch("main._mcp") as mcp,
    ):
        mcp.get_env_vars = AsyncMock(side_effect=MCPError("mcp-server unreachable", status_code=502))
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/config/env")

    assert resp.status_code == 200
    body = resp.json()
    assert body["vars"] == local_vars
    assert body["mcp_error"] == "mcp-server unreachable"


@pytest.mark.asyncio
async def test_get_config_env_rejects_disallowed_origin():
    with patch("main.env_config.list_env_vars", return_value=[]), patch("main._mcp") as mcp:
        mcp.get_env_vars = AsyncMock(return_value=[])
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/config/env", headers={"Origin": "http://evil.example"})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_config_env_allows_default_dashboard_origin():
    with patch("main.env_config.list_env_vars", return_value=[]), patch("main._mcp") as mcp:
        mcp.get_env_vars = AsyncMock(return_value=[])
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/config/env", headers={"Origin": "http://localhost:5173"})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_config_env_owned_key_writes_locally_never_proxies():
    with (
        patch("main.env_config.set_env_var") as set_var,
        patch("main._mcp") as mcp,
    ):
        mcp.set_env_var = AsyncMock()
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put("/config/env/OPENAI_API_KEY", json={"value": "sk-new-key"})

    assert resp.status_code == 200
    set_var.assert_called_once_with("OPENAI_API_KEY", "sk-new-key")
    mcp.set_env_var.assert_not_called()
    # The value must never be echoed back in the response.
    assert "sk-new-key" not in resp.text


@pytest.mark.asyncio
async def test_put_config_env_unowned_key_proxies_to_mcp_server():
    with (
        patch("main.env_config.set_env_var") as set_var,
        patch("main._mcp") as mcp,
    ):
        mcp.set_env_var = AsyncMock()
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put("/config/env/GITHUB_TOKEN", json={"value": "ghp_new"})

    assert resp.status_code == 200
    mcp.set_env_var.assert_called_once_with("GITHUB_TOKEN", "ghp_new")
    set_var.assert_not_called()


@pytest.mark.asyncio
async def test_put_config_env_rejects_disallowed_origin():
    with patch("main.env_config.set_env_var") as set_var, patch("main._mcp") as mcp:
        mcp.set_env_var = AsyncMock()
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/config/env/OPENAI_API_KEY",
                json={"value": "sk-x"},
                headers={"Origin": "http://evil.example"},
            )

    assert resp.status_code == 403
    set_var.assert_not_called()
    mcp.set_env_var.assert_not_called()
