"""In-process key/value memory store, scoped to the server process lifetime.

Survives across steps within a single agent run but resets on restart — no
persistence, no TTL. A plain dict is safe here without a lock: FastMCP tool
calls run on the asyncio event loop, and no `await` occurs between reading
and writing `_store`, so each handler's critical section is atomic.
"""

from __future__ import annotations

from instrumentation import tool

_store: dict[str, str] = {}


def register(mcp) -> None:
    @tool(mcp)
    async def memory_set(key: str, value: str) -> dict:
        """Store a value in the agent's memory under a named key. Use this to
        persist context between steps — e.g. the user's goal, intermediate
        results, or a plan. Overwrites any existing value for that key."""
        _store[key] = value
        return {"ok": True, "key": key}

    @tool(mcp)
    async def memory_get(key: str) -> dict:
        """Retrieve a previously stored value from the agent's memory by key."""
        if key not in _store:
            raise ValueError(f"key not found: {key!r}")
        return {"key": key, "value": _store[key]}

    @tool(mcp)
    async def memory_list() -> dict:
        """List all keys and values currently stored in the agent's memory.
        Useful at the start of a step to see what context is already
        available."""
        return {"count": len(_store), "entries": dict(_store)}
