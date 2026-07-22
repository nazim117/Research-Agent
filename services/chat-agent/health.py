# health.py — aggregate reachability check for the dashboard's Health Check
# wizard step and Settings' Integrations tab.
#
# Each dependency is probed independently (asyncio.gather) so a failure in
# one never masks or delays the others. Ollama, Qdrant, and the embeddings
# service are required; Docker and mcp-server are optional (see docstrings
# for why).

from __future__ import annotations

import asyncio
import subprocess

import httpx

_PROBE_TIMEOUT = 3.0


async def _check_ollama(base_url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            r = await client.get(f"{base_url}/api/tags")
            r.raise_for_status()
        return {"status": "ok", "detail": f"reachable at {base_url}", "required": True}
    except Exception as exc:
        return {"status": "error", "detail": f"unreachable at {base_url}: {exc}", "required": True}


async def _check_qdrant(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            r = await client.get(f"{url}/collections")
            r.raise_for_status()
        return {"status": "ok", "detail": f"reachable at {url}", "required": True}
    except Exception as exc:
        return {"status": "error", "detail": f"unreachable at {url}: {exc}", "required": True}


async def _check_embeddings(base_url: str) -> dict:
    # Required: RAG/memory retrieval always needs this, regardless of which
    # chat provider (Ollama or a cloud one) is active.
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            r = await client.get(f"{base_url}/health")
            r.raise_for_status()
        return {"status": "ok", "detail": f"reachable at {base_url}", "required": True}
    except Exception as exc:
        return {"status": "error", "detail": f"unreachable at {base_url}: {exc}", "required": True}


async def _check_mcp_server(mcp_client) -> dict:
    # Optional: mcp-server only brokers Jira/GitHub sync — chat, RAG, and
    # memory all work without it.
    try:
        await mcp_client.get_health()
        return {"status": "ok", "detail": "reachable", "required": False}
    except Exception as exc:
        return {"status": "error", "detail": f"unreachable: {exc}", "required": False}


def _check_docker_sync() -> dict:
    # Best-effort — no Docker Engine API client is used here (would add a new
    # dependency for a purely optional check); shells out to the CLI instead.
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return {"status": "ok", "detail": f"Docker {result.stdout.strip()}", "required": False}
        return {"status": "error", "detail": "Docker CLI returned an error", "required": False}
    except FileNotFoundError:
        return {"status": "error", "detail": "docker CLI not found on PATH", "required": False}
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "Docker did not respond in time", "required": False}
    except Exception as exc:
        return {"status": "error", "detail": f"Docker check failed: {exc}", "required": False}


async def _check_docker() -> dict:
    return await asyncio.to_thread(_check_docker_sync)


async def check_detailed_health(settings, mcp_client) -> dict:
    """Probe Ollama, Qdrant, the embeddings service, Docker, and mcp-server independently.

    Returns one entry per dependency in the shape the dashboard expects:
    {status: "ok"|"error", detail: str, required: bool}.
    """
    ollama, qdrant, embeddings, docker, mcp_server = await asyncio.gather(
        _check_ollama(settings.ollama_base_url),
        _check_qdrant(settings.qdrant_url),
        _check_embeddings(settings.embeddings_base_url),
        _check_docker(),
        _check_mcp_server(mcp_client),
    )
    return {
        "ollama": ollama,
        "qdrant": qdrant,
        "embeddings": embeddings,
        "docker": docker,
        "mcp_server": mcp_server,
    }
