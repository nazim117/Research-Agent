# load all runtime configuration from environment variables.
#
# pydantic-settings reads each field from the matching env var (case-insensitive).
# If a field has no default and the env var is missing, the import FAILS immediately
# with a clear error message.  That "fail fast" behaviour is intentional: we would
# rather crash at startup than discover a missing key on the first real request.
#
# How to set values:
#   - export LLM_PROVIDER=ollama   (shell)
#   - add LLM_PROVIDER=ollama to a .env file next to this service
#   - pass -e LLM_PROVIDER=ollama to docker run / docker compose

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at services/chat-agent/config.py.
# __file__ is the absolute path to this file, so .parent gives services/chat-agent/
# and .parent.parent.parent gives the repo root where the shared .env lives.
# We check both locations so the service works whether you launch uvicorn from
# the service directory, the repo root, or inside Docker (env vars only, no file).
_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parent.parent


class Settings(BaseSettings):
    # ── LLM provider ──────────────────────────────────────────────────────────
    # Which backend handles chat completions.  Set once at deploy time by the admin.
    # Options: "ollama" (local, default) | "openai_compatible" (any OpenAI-compatible API)
    # End users cannot override this — use the env var or .env file.
    llm_provider: str = "ollama"

    # Ollama chat model — separate from the embedding model.
    # Must be pulled first: `ollama pull llama3`
    ollama_chat_model: str = "llama3"

    # OpenAI-compatible backend (GitHub Models, DeepSeek, OpenAI, etc.)
    # All three must be set when LLM_PROVIDER=openai_compatible.
    # OPENAI_PROVIDER_LABEL is just a human-readable name used in error messages.
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    openai_provider_label: str = "openai-compatible"

    # Path to the SQLite database file.  In Docker this is usually a mounted volume
    # (e.g. /data/chat.db) so the data survives container restarts.
    sqlite_path: str = "chat.db"

    # Port the uvicorn server listens on.
    port: int = 8084

    # Ollama (local embedding model)
    # Ollama runs on the host machine (or in its own container).
    # Default port is 11434; override with OLLAMA_BASE_URL if running elsewhere.
    ollama_base_url: str = "http://localhost:11434"

    # The embedding model to use.  nomic-embed-text produces 768-dimensional vectors
    # and runs efficiently on CPU.  Must already be pulled: `ollama pull nomic-embed-text`
    ollama_embed_model: str = "nomic-embed-text"

    # Qdrant (vector database)
    # Qdrant runs as a Docker service (see docker-compose.yml).
    # Inside Docker Compose, use the service name: http://qdrant:6333
    # When running uvicorn locally, use: http://localhost:6333
    qdrant_url: str = "http://localhost:6333"

    # Name of the Qdrant collection that stores conversation message vectors.
    qdrant_collection: str = "conversations"

    # Name of the Qdrant collection that stores document chunk vectors.
    # Kept separate from conversations so document search and memory search
    # never cross-contaminate each other's results.
    qdrant_docs_collection: str = "documents"

    # How many semantically similar past messages to retrieve per request.
    # These are injected into the prompt as extra context for the LLM.
    memory_search_k: int = 5

    # MCP server — internal tool gateway.
    # The mcp-server (services/mcp-server, port 8083) proxies all PM vendor
    # API calls.  It holds JIRA_* and GITHUB_TOKEN; the chat-agent never reads
    # those credentials directly.  Inside Docker Compose the service name
    # "mcp-server" resolves to the container; override with MCP_BASE_URL.
    mcp_base_url: str = "http://localhost:8083"
    mcp_timeout_s: float = 30.0

    # Jira Cloud / GitHub credentials.
    # NOTE: these fields are NO LONGER read by the chat-agent.  They are kept
    # here only so that any .env file that sets JIRA_* or GITHUB_TOKEN does not
    # cause a pydantic-settings "extra field" error.  The values are ignored
    # at runtime — set them on the mcp-server service instead.
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    github_token: str = ""

    # Check the repo-root .env first, then a local .env next to this file.
    # pydantic-settings reads them left-to-right; later files override earlier ones.
    # Missing files are silently skipped — env vars set in the shell always win.
    model_config = SettingsConfigDict(
        env_file=[str(_REPO_ROOT / ".env"), str(_THIS_DIR / ".env")],
        extra="ignore",
    )


# Module-level singleton.  Import this object everywhere:
#   from config import settings
#   print(settings.llm_provider)
settings = Settings()
