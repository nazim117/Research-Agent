"""Generic outbound HTTP tool — lets the agent call any external API without
a dedicated tool per service."""

from __future__ import annotations

import json

import httpx

from instrumentation import tool

_MAX_BYTES = 100 * 1024

_client = httpx.AsyncClient(timeout=30.0)


async def _read_capped(response: httpx.Response) -> bytes:
    """Read up to _MAX_BYTES from a streamed response, matching Go's
    io.LimitReader(resp.Body, maxBytes) semantics."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        remaining = _MAX_BYTES - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if total >= _MAX_BYTES:
            break
    return b"".join(chunks)


def register(mcp) -> None:
    @tool(mcp)
    async def http_request(
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | dict | None = None,
    ) -> dict:
        """Make an HTTP request to any external API and return the status
        code, response headers, and body. The body is automatically parsed as
        JSON if possible. Use this to call REST APIs, webhooks, or any HTTP
        service during the execute step."""
        method = method.upper()

        content: bytes | None = None
        if body is not None:
            content = body.encode() if isinstance(body, str) else json.dumps(body).encode()

        req_headers = {str(k): str(v) for k, v in (headers or {}).items()}
        if content is not None and not any(k.lower() == "content-type" for k in req_headers):
            req_headers["Content-Type"] = "application/json"

        try:
            async with _client.stream(method, url, headers=req_headers, content=content) as resp:
                raw = await _read_capped(resp)
                status = resp.status_code
                resp_headers = dict(resp.headers)
        except httpx.HTTPError as exc:
            raise ValueError(f"request failed: {exc}") from exc

        try:
            parsed_body = json.loads(raw)
        except json.JSONDecodeError:
            parsed_body = raw.decode(errors="replace")

        return {
            "status": status,
            "headers": resp_headers,
            "body": parsed_body,
            "truncated": len(raw) == _MAX_BYTES,
        }
