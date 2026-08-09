import httpx
import pytest
import respx

import tools.http_request as http_request_module
from tests.conftest import ToolError, call_tool_json


@pytest.fixture
def mcp(single_tool_mcp):
    return single_tool_mcp(http_request_module)


@respx.mock
async def test_get_returns_status_headers_and_json_body(mcp):
    respx.get("https://api.example.com/things").mock(
        return_value=httpx.Response(200, json={"ok": True}, headers={"X-Custom": "yes"})
    )
    out = await call_tool_json(mcp, "http_request", {"url": "https://api.example.com/things"})
    assert out["status"] == 200
    assert out["body"] == {"ok": True}
    assert out["headers"]["x-custom"] == "yes"
    assert out["truncated"] is False


@respx.mock
async def test_non_json_body_returned_as_string(mcp):
    respx.get("https://api.example.com/text").mock(return_value=httpx.Response(200, text="plain text"))
    out = await call_tool_json(mcp, "http_request", {"url": "https://api.example.com/text"})
    assert out["body"] == "plain text"


@respx.mock
async def test_dict_body_is_json_serialized_with_default_content_type(mcp):
    route = respx.post("https://api.example.com/create").mock(return_value=httpx.Response(201, json={"id": 1}))
    out = await call_tool_json(
        mcp,
        "http_request",
        {"url": "https://api.example.com/create", "method": "post", "body": {"name": "x"}},
    )
    assert out["status"] == 201
    sent = route.calls.last.request
    assert sent.headers["content-type"] == "application/json"
    assert sent.content == b'{"name": "x"}'


@respx.mock
async def test_string_body_passed_through_raw(mcp):
    route = respx.post("https://api.example.com/raw").mock(return_value=httpx.Response(200))
    await call_tool_json(
        mcp,
        "http_request",
        {"url": "https://api.example.com/raw", "method": "POST", "body": "raw-text-body"},
    )
    assert route.calls.last.request.content == b"raw-text-body"


@respx.mock
async def test_caller_supplied_content_type_is_not_overridden(mcp):
    route = respx.post("https://api.example.com/xml").mock(return_value=httpx.Response(200))
    await call_tool_json(
        mcp,
        "http_request",
        {
            "url": "https://api.example.com/xml",
            "method": "POST",
            "body": "<a/>",
            "headers": {"Content-Type": "application/xml"},
        },
    )
    assert route.calls.last.request.headers["content-type"] == "application/xml"


@respx.mock
async def test_caller_headers_are_forwarded(mcp):
    route = respx.get("https://api.example.com/auth").mock(return_value=httpx.Response(200))
    await call_tool_json(
        mcp,
        "http_request",
        {"url": "https://api.example.com/auth", "headers": {"Authorization": "Bearer tok"}},
    )
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_response_is_truncated_at_100kb(mcp):
    big = "x" * (150 * 1024)
    respx.get("https://api.example.com/big").mock(return_value=httpx.Response(200, text=big))
    out = await call_tool_json(mcp, "http_request", {"url": "https://api.example.com/big"})
    assert out["truncated"] is True
    assert len(out["body"]) == 100 * 1024


@respx.mock
async def test_request_failure_raises_tool_error(mcp):
    respx.get("https://api.example.com/down").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ToolError, match="request failed"):
        await mcp.call_tool("http_request", {"url": "https://api.example.com/down"})
