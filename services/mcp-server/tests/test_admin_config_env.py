import httpx
import pytest

import env_vars
from app import build_app
from server import build_mcp


@pytest.fixture
async def client():
    app = build_app(build_mcp())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(env_vars, "find_env_file", lambda: str(path))
    return path


async def test_get_without_origin_header_is_allowed(client):
    resp = await client.get("/config/env")
    assert resp.status_code == 200
    keys = {row["key"] for row in resp.json()}
    assert keys == {"BRAVE_SEARCH_API_KEY", "SEARXNG_BASE_URL"}


async def test_get_with_default_dashboard_origin_is_allowed(client):
    resp = await client.get("/config/env", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200


async def test_get_with_disallowed_origin_is_rejected(client):
    resp = await client.get("/config/env", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


async def test_get_with_configured_dashboard_origin_allowlist(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_ORIGIN", "http://localhost:5173,https://staging.example")
    resp = await client.get("/config/env", headers={"Origin": "https://staging.example"})
    assert resp.status_code == 200


async def test_put_persists_allowlisted_key(client, _env_file):
    resp = await client.put("/config/env", json={"key": "SEARXNG_BASE_URL", "value": "http://x:8080"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    from dotenv import dotenv_values

    assert dotenv_values(str(_env_file))["SEARXNG_BASE_URL"] == "http://x:8080"

    resp = await client.get("/config/env")
    row = next(r for r in resp.json() if r["key"] == "SEARXNG_BASE_URL")
    assert row == {"key": "SEARXNG_BASE_URL", "secret": False, "configured": True, "hint": "http://x:8080"}


async def test_put_rejects_unknown_key(client):
    resp = await client.put("/config/env", json={"key": "SOME_RANDOM_VAR", "value": "x"})
    assert resp.status_code == 400


async def test_put_secret_never_echoed_back_in_full(client):
    await client.put("/config/env", json={"key": "BRAVE_SEARCH_API_KEY", "value": "sk-verysecretvalue"})
    resp = await client.get("/config/env")
    assert "sk-verysecretvalue" not in resp.text
    row = next(r for r in resp.json() if r["key"] == "BRAVE_SEARCH_API_KEY")
    assert row["hint"] == "…alue"


async def test_put_with_disallowed_origin_is_rejected(client):
    resp = await client.put(
        "/config/env",
        json={"key": "SEARXNG_BASE_URL", "value": "http://x"},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403
