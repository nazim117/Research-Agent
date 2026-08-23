"""Entrypoint. TRANSPORT=stdio runs the server as a child process for
external MCP clients (Claude Code/Desktop config) with no HTTP layer and no
tracing — the parent process manages the child's lifetime. Any other value
(or unset) runs a persistent Streamable HTTP service on PORT (default 8083),
which is what chat-agent and Docker Compose talk to.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

logging.basicConfig(stream=sys.stderr, format="[mcp] %(message)s", level=logging.INFO)
log = logging.getLogger("mcp-server")


def main() -> None:
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file)
        log.info("loaded env from %s", env_file)

    # MCP_ENV_FILE (set in docker-compose.yml to a path in a named volume)
    # is where PUT /config/env persists dashboard-saved values — see
    # env_vars.find_env_file(). Load it *after* the block above, with
    # override=True, so a value the user previously saved wins over
    # whatever docker-compose's environment:/env_file: blocks set this
    # boot; otherwise a saved value would persist to disk but never
    # actually take effect after a restart.
    mcp_env_file = os.environ.get("MCP_ENV_FILE")
    if mcp_env_file and os.path.exists(mcp_env_file):
        load_dotenv(mcp_env_file, override=True)
        log.info("loaded persisted env overrides from %s", mcp_env_file)

    from server import build_mcp

    mcp = build_mcp()

    if os.environ.get("TRANSPORT") == "stdio":
        mcp.run(transport="stdio")
        return

    from observability.tracing import init_tracer

    shutdown = init_tracer()
    try:
        import uvicorn

        from app import build_app

        port = int(os.environ.get("PORT", "8083"))
        log.info("MCP server listening on :%d", port)
        uvicorn.run(build_app(mcp), host="0.0.0.0", port=port, log_config=None)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
