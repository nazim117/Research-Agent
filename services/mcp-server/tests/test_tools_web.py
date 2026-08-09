import httpx
import pytest
import respx

import tools.web as web_module
from tests.conftest import ToolError, call_tool_json

DDG_HTML = """
<html><body><table>
<tr><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Fasyncio.html&amp;rut=1" class='result-link'>asyncio docs</a>
</td></tr>
<tr><td class='result-snippet'>Asynchronous I/O library.</td></tr>
<tr><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2Fasync-io%2F&amp;rut=2" class='result-link'>Real Python asyncio</a>
</td></tr>
<tr><td class='result-snippet'>A <b>hands-on</b> walkthrough.</td></tr>
</table></body></html>
"""


@pytest.fixture
def mcp(single_tool_mcp):
    return single_tool_mcp(web_module)


@pytest.fixture(autouse=True)
def _no_search_backend_configured(monkeypatch):
    """Tests opt into a backend explicitly — don't let a developer's real
    .env (BRAVE_SEARCH_API_KEY, SEARXNG_BASE_URL) leak into these tests."""
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)


@respx.mock
async def test_web_search_uses_searxng_when_configured(mcp, monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng:8080")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "unused-since-searxng-wins")
    route = respx.get("http://searxng:8080/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "T1", "url": "https://x.example/1", "content": "snippet 1"}]},
        )
    )
    out = await call_tool_json(mcp, "web_search", {"query": "python asyncio"})
    assert out == {
        "query": "python asyncio",
        "count": 1,
        "results": [{"title": "T1", "url": "https://x.example/1", "snippet": "snippet 1"}],
    }
    sent = route.calls.last.request
    assert sent.url.params["format"] == "json"
    assert sent.url.params["q"] == "python asyncio"


@respx.mock
async def test_web_search_searxng_non_200_raises(mcp, monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng:8080")
    respx.get("http://searxng:8080/search").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ToolError, match="searxng: status 500"):
        await mcp.call_tool("web_search", {"query": "x"})


@respx.mock
async def test_web_search_uses_brave_when_no_searxng(mcp, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-key")
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={"web": {"results": [{"title": "B1", "url": "https://b.example/1", "description": "d1"}]}},
        )
    )
    out = await call_tool_json(mcp, "web_search", {"query": "python asyncio"})
    assert out["results"] == [{"title": "B1", "url": "https://b.example/1", "snippet": "d1"}]
    assert route.calls.last.request.headers["x-subscription-token"] == "secret-key"


@respx.mock
async def test_web_search_brave_non_200_raises(mcp, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-key")
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    with pytest.raises(ToolError, match="brave: status 401"):
        await mcp.call_tool("web_search", {"query": "x"})


@respx.mock
async def test_web_search_falls_back_to_duckduckgo(mcp):
    respx.get("https://lite.duckduckgo.com/lite/").mock(return_value=httpx.Response(200, text=DDG_HTML))
    out = await call_tool_json(mcp, "web_search", {"query": "python asyncio"})
    assert out["count"] == 2
    assert out["results"][0] == {
        "title": "asyncio docs",
        "url": "https://docs.python.org/3/asyncio.html",
        "snippet": "Asynchronous I/O library.",
    }
    assert out["results"][1]["snippet"] == "A hands-on walkthrough."


@respx.mock
async def test_web_search_ddg_respects_limit(mcp):
    respx.get("https://lite.duckduckgo.com/lite/").mock(return_value=httpx.Response(200, text=DDG_HTML))
    out = await call_tool_json(mcp, "web_search", {"query": "x", "limit": 1})
    assert out["count"] == 1


@pytest.mark.parametrize("limit,expected", [(0, 1), (-5, 1), (100, 10), (3, 3)])
async def test_web_search_limit_is_clamped(mcp, monkeypatch, limit, expected):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng:8080")
    many_results = {"results": [{"title": f"T{i}", "url": f"u{i}", "content": "c"} for i in range(10)]}
    with respx.mock:
        respx.get("http://searxng:8080/search").mock(return_value=httpx.Response(200, json=many_results))
        out = await call_tool_json(mcp, "web_search", {"query": "x", "limit": limit})
    assert out["count"] == expected


@respx.mock
async def test_web_fetch_returns_capped_body(mcp):
    respx.get("https://example.com/page").mock(return_value=httpx.Response(200, text="hello world"))
    out = await call_tool_json(mcp, "web_fetch", {"url": "https://example.com/page"})
    assert out == {"url": "https://example.com/page", "status": 200, "body": "hello world", "truncated": False}


@respx.mock
async def test_web_fetch_truncates_at_100kb(mcp):
    big = "y" * (150 * 1024)
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, text=big))
    out = await call_tool_json(mcp, "web_fetch", {"url": "https://example.com/big"})
    assert out["truncated"] is True
    assert len(out["body"]) == 100 * 1024


async def test_web_fetch_rejects_invalid_url(mcp):
    with pytest.raises(ToolError, match="invalid URL"):
        await mcp.call_tool("web_fetch", {"url": "not-a-url"})
