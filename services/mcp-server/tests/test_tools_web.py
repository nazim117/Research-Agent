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


# ---------------------------------------------------------------------------
# HTML extraction — web_fetch reduces HTML pages to readable text instead of
# handing back raw markup (nav/scripts/ads stripped), same technique as
# chat-agent's extractors.py. Fixes the agentic loop repeatedly burning
# rounds on JS-rendered pages whose raw HTML is just a loading shell.
# ---------------------------------------------------------------------------

HTML_ARTICLE = """<!DOCTYPE html>
<html><head><title>Qdrant 1.19 Release</title></head>
<body>
<nav>Home | Docs | Blog</nav>
<article>
<h1>Qdrant 1.19.0 Release Notes</h1>
<p>This release introduces TurboQuant compression, a new quantization method
that reduces vector storage size by up to 50 percent while maintaining high
recall across most workloads and datasets tested internally.</p>
<p>We also improved query throughput by roughly 30 percent, and fixed a
memory leak in the HNSW index builder that could occur during long-running
indexing jobs on large collections.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>"""

HTML_JS_SHELL = """<!DOCTYPE html>
<html><head><title>Qdrant Blog</title></head>
<body><div id="app">Loading...</div><script src="/app.js"></script></body></html>"""


@respx.mock
async def test_web_fetch_extracts_readable_text_from_html(mcp):
    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=HTML_ARTICLE, headers={"content-type": "text/html; charset=utf-8"})
    )
    out = await call_tool_json(mcp, "web_fetch", {"url": "https://example.com/blog"})
    assert "TurboQuant compression" in out["body"]
    assert "<nav>" not in out["body"]
    assert "<html>" not in out["body"]


@respx.mock
async def test_web_fetch_html_with_no_extractable_content_returns_helpful_message(mcp):
    respx.get("https://example.com/spa").mock(
        return_value=httpx.Response(200, text=HTML_JS_SHELL, headers={"content-type": "text/html; charset=utf-8"})
    )
    out = await call_tool_json(mcp, "web_fetch", {"url": "https://example.com/spa"})
    assert "Loading..." not in out["body"]
    assert "No readable article text" in out["body"]


@respx.mock
async def test_web_fetch_leaves_non_html_content_untouched(mcp):
    body_text = '{"tag_name": "v1.19.0", "body": "release body text"}'
    respx.get("https://api.example.com/release").mock(
        return_value=httpx.Response(200, text=body_text, headers={"content-type": "application/json"})
    )
    out = await call_tool_json(mcp, "web_fetch", {"url": "https://api.example.com/release"})
    assert out["body"] == body_text


@respx.mock
async def test_web_fetch_sniffs_html_when_content_type_missing(mcp):
    respx.get("https://example.com/mislabeled").mock(
        return_value=httpx.Response(200, text=HTML_ARTICLE, headers={"content-type": "application/octet-stream"})
    )
    out = await call_tool_json(mcp, "web_fetch", {"url": "https://example.com/mislabeled"})
    assert "TurboQuant compression" in out["body"]


# ---------------------------------------------------------------------------
# SSRF guard (tools/_net.py) — web_fetch must never reach internal infra.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
])
async def test_web_fetch_rejects_literal_unsafe_ips(mcp, url):
    with respx.mock:
        route = respx.get(url).mock(return_value=httpx.Response(200, text="should never be seen"))
        with pytest.raises(ToolError, match="non-public address"):
            await mcp.call_tool("web_fetch", {"url": url})
        assert not route.called


async def test_web_fetch_rejects_hostname_resolving_to_private_ip(mcp, monkeypatch):
    import tools._net as net_module

    monkeypatch.setattr(net_module, "_resolve", lambda hostname: {"10.1.2.3"})
    with respx.mock:
        route = respx.get("https://internal.corp.example/").mock(return_value=httpx.Response(200))
        with pytest.raises(ToolError, match="non-public address"):
            await mcp.call_tool("web_fetch", {"url": "https://internal.corp.example/"})
        assert not route.called


async def test_web_fetch_rejects_non_http_scheme(mcp):
    # netloc must be non-empty to get past web_fetch's own pre-check
    # (`not parsed.scheme or not parsed.netloc`) and reach assert_safe_url.
    with pytest.raises(ToolError, match="unsupported scheme"):
        await mcp.call_tool("web_fetch", {"url": "ftp://example.com/file"})
