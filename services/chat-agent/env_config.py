# env_config.py — read/write access to this service's own env vars, for the
# Settings UI's Advanced tab.
#
# Secrets (OPENAI_API_KEY) are never returned in full — only "configured"
# plus a last-4-characters hint, matching the write-only pattern used by
# mcp-server's equivalent endpoint (services/mcp-server/internal/tools/registry.go).
# Writes persist to the shared repo-root .env file (config.py's Settings reads
# the same file) via python-dotenv; the running process picks up the new
# value immediately for display purposes, but Settings itself is a
# module-level singleton read once at import, so a restart is still required
# for the change to take effect in the app's actual behaviour.

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from dotenv import set_key

_REPO_ROOT = Path(__file__).parent.parent.parent
DEFAULT_ENV_PATH = _REPO_ROOT / ".env"


class EnvVarSpec(TypedDict):
    key: str
    secret: bool


# Fixed allowlist of vars this service owns. Any key outside this list is
# rejected by set_env_var — this must never be usable to set arbitrary env vars.
OWNED_VARS: list[EnvVarSpec] = [
    {"key": "LLM_PROVIDER", "secret": False},
    {"key": "OLLAMA_CHAT_MODEL", "secret": False},
    {"key": "OLLAMA_EMBED_MODEL", "secret": False},
    {"key": "OLLAMA_BASE_URL", "secret": False},
    {"key": "OPENAI_BASE_URL", "secret": False},
    {"key": "OPENAI_MODEL", "secret": False},
    {"key": "OPENAI_PROVIDER_LABEL", "secret": False},
    {"key": "OPENAI_API_KEY", "secret": True},
]

OWNED_KEYS = {spec["key"] for spec in OWNED_VARS}


def _mask_hint(value: str, secret: bool) -> str:
    if not secret:
        return value
    if len(value) <= 4:
        return "…" + value
    return "…" + value[-4:]


def list_env_vars() -> list[dict]:
    """Current state of every env var this service owns. Secret values are
    never returned in full — only configured/hint.
    """
    out = []
    for spec in OWNED_VARS:
        value = os.environ.get(spec["key"], "")
        row = {"key": spec["key"], "secret": spec["secret"], "configured": bool(value)}
        if value:
            row["hint"] = _mask_hint(value, spec["secret"])
        out.append(row)
    return out


def set_env_var(key: str, value: str, env_path: Path | str = DEFAULT_ENV_PATH) -> None:
    """Persist a new value for one allowlisted env var to the .env file, and
    update the current process's environment so list_env_vars() reflects it
    immediately. Raises ValueError for an unrecognized key.
    """
    if key not in OWNED_KEYS:
        raise ValueError(f"{key!r} is not a recognized env var")

    set_key(str(env_path), key, value)
    os.environ[key] = value
