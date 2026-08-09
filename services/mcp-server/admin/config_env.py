"""GET/PUT /config/env — env var admin API for the dashboard's Settings >
Advanced tab. Gated by an Origin allowlist since it can reveal whether
credentials are configured (and a masked hint), not just a boolean."""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

import env_vars


def _allowed_dashboard_origins() -> list[str]:
    raw = os.environ.get("DASHBOARD_ORIGIN", "http://localhost:5173")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _check_origin(request: Request) -> bool:
    """corsMiddleware answers CORS preflights for the whole API with a
    wildcard (needed because the Chrome extension's origin ID changes every
    reload), so it can't be used to gate this route — this is a narrower,
    explicit allowlist check instead. Requests with no Origin header
    (server-to-server calls, curl, tests) are allowed through; only a
    present-but-unrecognized Origin is rejected."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin in _allowed_dashboard_origins()


def register(mcp) -> None:
    @mcp.custom_route("/config/env", methods=["GET", "PUT"])
    async def config_env(request: Request):
        if not _check_origin(request):
            return JSONResponse({"error": "origin not allowed"}, status_code=403)

        if request.method == "GET":
            return JSONResponse(env_vars.get_env_vars())

        body = await request.json()
        key, value = body.get("key"), body.get("value")
        try:
            env_vars.set_env_var(key, value, env_vars.find_env_file())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})
