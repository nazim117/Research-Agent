# ollama_models.py — list locally installed Ollama models, and proxy a
# model pull's progress straight through from Ollama's own NDJSON API.
#
# Used by GET /models and POST /models/pull, which back the dashboard's
# Setup Wizard Models step and Settings' LLM Models/Embeddings tabs.

from __future__ import annotations

from typing import AsyncIterator

import httpx


async def list_models(settings) -> dict:
    """Return {"installed": [model names]} from Ollama's local model list."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            r.raise_for_status()
    except httpx.RequestError as exc:
        raise ConnectionError(f"Could not reach Ollama at {settings.ollama_base_url}: {exc}") from exc
    return {"installed": [m["name"] for m in r.json().get("models", [])]}


async def stream_pull(settings, model_name: str) -> AsyncIterator[bytes]:
    """Proxy Ollama's POST /api/pull, yielding each NDJSON progress line
    unmodified as it arrives, e.g.:
        {"status":"pulling manifest"}
        {"status":"downloading digestname","completed":1234,"total":5678}
        {"status":"success"}

    Ollama reports pull failures (e.g. unknown model name) as a normal-status
    stream line like {"error": "..."} rather than an HTTP error — the caller
    (dashboard's pullModel) checks for that field on each parsed line.
    """
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{settings.ollama_base_url}/api/pull",
            json={"name": model_name},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n").encode("utf-8")
