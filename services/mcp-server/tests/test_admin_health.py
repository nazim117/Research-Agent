import httpx
import pytest
import respx

from app import build_app
from server import build_mcp


@pytest.fixture(autouse=True)
def _no_backend_configured(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)


@pytest.fixture
async def client():
    app = build_app(build_mcp())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_defaults_to_duckduckgo_when_unconfigured(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "healthy",
        "web_search": {"backend": "duckduckgo", "configured": False},
    }


async def test_health_reports_brave_when_key_configured(client, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "some-key")
    resp = await client.get("/health")
    assert resp.json()["web_search"] == {"backend": "brave", "configured": True}


@respx.mock
async def test_health_reports_searxng_reachable(client, monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng:8080")
    respx.get("http://searxng:8080/").mock(return_value=httpx.Response(200))
    resp = await client.get("/health")
    assert resp.json()["web_search"] == {
        "backend": "searxng",
        "configured": True,
        "reachable": True,
    }


@respx.mock
async def test_health_reports_searxng_unreachable(client, monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng:8080")
    respx.get("http://searxng:8080/").mock(side_effect=httpx.ConnectError("refused"))
    resp = await client.get("/health")
    assert resp.json()["web_search"] == {
        "backend": "searxng",
        "configured": True,
        "reachable": False,
    }
