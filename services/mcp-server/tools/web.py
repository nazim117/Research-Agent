"""Web search and page fetching.

web_search dispatches to the best available backend, in order:
  1. SearXNG          — when SEARXNG_BASE_URL is set (bundled via
     docker-compose.yml; no API key, no per-query cost)
  2. Brave Search API — when BRAVE_SEARCH_API_KEY is set (free tier, signup
     required)
  3. DuckDuckGo HTML  — scraped from lite.duckduckgo.com, last-resort
     fallback requiring no key
"""

from __future__ import annotations

import html
import json
import os
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
import trafilatura

from instrumentation import tool
from tools._net import assert_safe_url

# Raw network read cap — generous, since HTML pages carry a lot of
# boilerplate (nav/scripts/styles) before the actual content; the *output*
# handed to the model is capped separately at _MAX_OUTPUT_BYTES, after HTML
# extraction has already shrunk it.
_MAX_RAW_FETCH_BYTES = 500 * 1024
_MAX_OUTPUT_BYTES = 100 * 1024
_MAX_DDG_BYTES = 512 * 1024

_client = httpx.AsyncClient(timeout=15.0)

# DDG lite uses single-quoted class attributes; href precedes the class.
# Matches: href="//duckduckgo.com/l/?uddg=...&rut=..." class='result-link'>Title</a>
_RE_DDG_LINK = re.compile(
    r"href=\"(//duckduckgo\.com/l/\?uddg=[^\"]+)\"[^>]*class='result-link'>([^<]+)</a>"
)
_RE_DDG_SNIPPET = re.compile(r"class='result-snippet'[^>]*>([\s\S]*?)</td>")
_RE_HTML_TAGS = re.compile(r"<[^>]*>")


def _clean_text(s: str) -> str:
    s = _RE_HTML_TAGS.sub(" ", s)
    return " ".join(s.split())


def _looks_like_html(content_type: str, body: str) -> bool:
    if "html" in content_type.lower():
        return True
    # Some servers omit/mislabel Content-Type; sniff the body itself so
    # e.g. a mislabeled HTML page still gets extracted rather than dumped
    # to the model as raw markup.
    stripped = body.lstrip()[:200].lower()
    return stripped.startswith(("<!doctype html", "<html"))


# Below this, treat extraction as having found nothing useful. trafilatura
# doesn't always return "" for a JavaScript-rendered shell page — it can
# still pull out a stray "Loading..." or nav fragment — so an empty-string
# check alone isn't enough to catch the case this is meant to catch.
_MIN_EXTRACTED_CHARS = 80


def _extract_readable_text(html_content: str) -> str:
    """Reduce an HTML page to its main readable text (article body — nav,
    ads, scripts, and other boilerplate stripped), the same approach
    chat-agent's extractors.py uses for document ingestion. Returns "" if
    trafilatura can't find a main content section, or finds only a scrap
    below _MIN_EXTRACTED_CHARS (e.g. a JavaScript-rendered page whose
    initial HTML is just a loading shell) — callers should treat that as
    "nothing useful here," not silently fall back to the raw markup, which
    just wastes the caller's context budget."""
    try:
        extracted = (trafilatura.extract(html_content) or "").strip()
    except Exception:
        # trafilatura/lxml can raise on genuinely malformed markup; this is
        # best-effort extraction from arbitrary, untrusted web content, so
        # degrade to "nothing extracted" rather than fail the whole fetch.
        return ""
    return extracted if len(extracted) >= _MIN_EXTRACTED_CHARS else ""


def _decode_ddg_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
    except ValueError:
        return ""
    target = parse_qs(parsed.query).get("uddg", [""])[0]
    if target:
        try:
            return unquote(target)
        except ValueError:
            return target
    if href.startswith("http"):
        return href
    return ""


async def _searxng_search(query: str, limit: int, base_url: str) -> dict:
    api_url = f"{base_url.rstrip('/')}/search?q={quote(query)}&format=json&categories=general"
    try:
        resp = await _client.get(api_url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise ValueError(f"searxng: request failed: {exc}") from exc
    if resp.status_code != 200:
        raise ValueError(f"searxng: status {resp.status_code}: {resp.text}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"searxng: decode response: {exc}") from exc

    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])[:limit]
    ]
    return {"query": query, "count": len(results), "results": results}


async def _brave_search(query: str, limit: int, api_key: str) -> dict:
    api_url = (
        f"https://api.search.brave.com/res/v1/web/search"
        f"?q={quote(query)}&count={limit}&search_lang=en&result_filter=web"
    )
    try:
        resp = await _client.get(
            api_url,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"brave: request failed: {exc}") from exc
    if resp.status_code != 200:
        raise ValueError(f"brave: status {resp.status_code}: {resp.text}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"brave: decode response: {exc}") from exc

    web_results = data.get("web", {}).get("results", [])[:limit]
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
        for r in web_results
    ]
    return {"query": query, "count": len(results), "results": results}


async def _ddg_html_search(query: str, limit: int) -> dict:
    api_url = f"https://lite.duckduckgo.com/lite/?q={quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with _client.stream("GET", api_url, headers=headers) as resp:
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                remaining = _MAX_DDG_BYTES - total
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
            content = b"".join(chunks).decode(errors="replace")
    except httpx.HTTPError as exc:
        raise ValueError(f"ddg: request failed: {exc}") from exc

    link_matches = _RE_DDG_LINK.findall(content)
    snippet_matches = _RE_DDG_SNIPPET.findall(content)

    results = []
    for i, (href, title_raw) in enumerate(link_matches):
        if len(results) >= limit:
            break
        actual_url = _decode_ddg_url(html.unescape(href))
        if not actual_url:
            continue
        title = _clean_text(html.unescape(title_raw))
        if not title:
            continue
        snippet = _clean_text(html.unescape(snippet_matches[i])) if i < len(snippet_matches) else ""
        results.append({"title": title, "url": actual_url, "snippet": snippet})

    return {"query": query, "count": len(results), "results": results}


def register(mcp) -> None:
    @tool(mcp)
    async def web_search(query: str, limit: int = 5) -> dict:
        """Search the web for current information and return titles, URLs,
        and snippets. Uses SearXNG if SEARXNG_BASE_URL is set, else Brave
        Search API if BRAVE_SEARCH_API_KEY is set, otherwise falls back to
        DuckDuckGo. Use during execute steps when the agent needs real-time
        or recent information."""
        limit = max(1, min(limit, 10))

        base_url = os.environ.get("SEARXNG_BASE_URL")
        if base_url:
            return await _searxng_search(query, limit, base_url)
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if api_key:
            return await _brave_search(query, limit, api_key)
        return await _ddg_html_search(query, limit)

    @tool(mcp)
    async def web_fetch(url: str) -> dict:
        """Fetch the content of a URL and return it as text. HTML pages are
        automatically reduced to their main readable text — scripts, nav,
        ads, and other boilerplate stripped — so most pages come back as
        clean article text rather than raw markup. Use to read a specific
        page or API endpoint found via web_search. Responses are capped at
        100 KB."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"invalid URL {url!r}")
        assert_safe_url(url)

        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
        try:
            async with _client.stream("GET", url, headers=headers) as resp:
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    remaining = _MAX_RAW_FETCH_BYTES - total
                    if remaining <= 0:
                        break
                    chunks.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                body = b"".join(chunks).decode(errors="replace")
                status = resp.status_code
                content_type = resp.headers.get("content-type", "")
        except httpx.HTTPError as exc:
            raise ValueError(f"fetch failed: {exc}") from exc

        if _looks_like_html(content_type, body):
            extracted = _extract_readable_text(body)
            body = extracted or (
                "(No readable article text could be extracted from this page — "
                "it may be JavaScript-rendered or have no main content section. "
                "Try a different URL, such as a raw markdown/API/JSON endpoint "
                "for this content if one exists.)"
            )

        body_bytes = body.encode()
        truncated = len(body_bytes) > _MAX_OUTPUT_BYTES
        if truncated:
            body = body_bytes[:_MAX_OUTPUT_BYTES].decode(errors="replace")

        return {
            "url": url,
            "status": status,
            "body": body,
            "truncated": truncated,
        }
