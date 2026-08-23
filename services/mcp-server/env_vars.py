"""Env-var admin API backing the dashboard's Settings > Advanced tab.

This is *not* an MCP tool — it's a plain admin operation exposed only via
the /config/env HTTP route (see admin/config_env.py). Only the allowlisted
keys below can ever be read or written through it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, set_key


@dataclass(frozen=True)
class _EnvVarSpec:
    key: str
    secret: bool


# Fixed set of vars this service will read or write. Any key outside this
# list is rejected — this endpoint must never set arbitrary env vars.
ENV_VAR_ALLOWLIST = [
    _EnvVarSpec(key="BRAVE_SEARCH_API_KEY", secret=True),
    _EnvVarSpec(key="SEARXNG_BASE_URL", secret=False),
]


def _mask_hint(value: str, secret: bool) -> str:
    if not secret:
        return value
    return "…" + (value[-4:] if len(value) > 4 else value)


def find_env_file() -> str:
    """Return the path this service reads/writes allowlisted env vars from.

    MCP_ENV_FILE takes precedence when set — this is the dedicated writable
    location docker-compose.yml points at a named volume, since env_file: in
    Compose only injects values into the container's process environment at
    startup, it never gives the container access to the host's .env file to
    write back to (find_dotenv() would find nothing inside the container).
    The file (and its parent dir) is created if missing, since this path is
    meant to always be writable.

    Falls back to walking up from cwd for a .env file (mirroring Go's
    findEnvFile) when MCP_ENV_FILE is unset — unchanged behavior for native,
    non-Docker use, where the repo-root .env is directly on disk.
    """
    configured = os.environ.get("MCP_ENV_FILE")
    if configured:
        path = Path(configured)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        return str(path)
    return find_dotenv(usecwd=True)


def get_env_vars() -> list[dict]:
    """Report the current state of every env var this service owns. Secret
    values are never returned in full — only a last-4-characters hint."""
    out = []
    for spec in ENV_VAR_ALLOWLIST:
        value = os.environ.get(spec.key, "")
        row: dict = {"key": spec.key, "secret": spec.secret, "configured": bool(value)}
        if value:
            row["hint"] = _mask_hint(value, spec.secret)
        out.append(row)
    return out


def set_env_var(key: str, value: str, env_path: str) -> None:
    """Persist a new value for one allowlisted env var to the .env file at
    env_path, and update the current process's environment so get_env_vars()
    reflects it immediately."""
    if key not in {spec.key for spec in ENV_VAR_ALLOWLIST}:
        raise ValueError(f"{key!r} is not a recognized env var")
    if not env_path:
        raise ValueError("no .env file found")

    # Validate the file is at least readable/parseable before writing to it.
    dotenv_values(env_path)
    set_key(env_path, key, value)
    os.environ[key] = value


__all__ = ["ENV_VAR_ALLOWLIST", "find_env_file", "get_env_vars", "set_env_var"]
