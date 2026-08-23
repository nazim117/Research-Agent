from __future__ import annotations

import json
from types import ModuleType

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError


@pytest.fixture
def single_tool_mcp():
    """Build a FastMCP instance with just one tools/* module registered, for
    fast unit tests that don't need a full client/server session."""

    def _build(module: ModuleType) -> FastMCP:
        mcp = FastMCP("test")
        module.register(mcp)
        return mcp

    return _build


async def call_tool_json(mcp: FastMCP, name: str, arguments: dict) -> dict:
    """Call a tool via FastMCP's in-process call_tool() and parse the JSON
    text result. Raises mcp.server.fastmcp.exceptions.ToolError on failure —
    the same exception the real protocol layer converts into isError=true."""
    content = await mcp.call_tool(name, arguments)
    blocks = content if isinstance(content, list) else content[0]
    return json.loads(blocks[0].text)


@pytest.fixture(autouse=True)
def _mock_dns_resolution(monkeypatch):
    """tools/_net.py's SSRF guard resolves hostnames via a real DNS lookup
    (socket.getaddrinfo) before web_fetch/http_request make a request. Tests
    use hostnames like api.example.com that would otherwise need a live
    network call — pin resolution to a fixed public IP by default so the
    whole suite stays network-free. Literal IPs (e.g. SSRF tests using
    "127.0.0.1" directly) pass through unchanged, matching real getaddrinfo
    behavior (a literal IP resolves to itself, no lookup needed) — otherwise
    SSRF tests asserting a literal unsafe IP is blocked would be defeated by
    this fixture rewriting it to a public one. Other SSRF-specific tests
    override this per-test to prove a *hostname* resolving to a private IP
    is also blocked."""
    import ipaddress

    import tools._net as net_module

    def _fake_resolve(hostname: str) -> set[str]:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return {"93.184.216.34"}
        return {hostname}

    monkeypatch.setattr(net_module, "_resolve", _fake_resolve)


@pytest.fixture(autouse=True)
def _reset_memory_store():
    """tools/memory.py holds a module-level dict shared across the process,
    matching the Go service's in-process singleton. Reset it between tests
    so test order doesn't matter."""
    import tools.memory as memory_module

    memory_module._store.clear()
    yield
    memory_module._store.clear()


__all__ = ["ToolError", "call_tool_json"]
