# request_context.py — per-request correlation id stored in a contextvar.
#
# A contextvar survives across async await boundaries within the same request
# but is isolated between concurrent requests — exactly what we need so that
# different requests don't overwrite each other's id.
#
# Usage:
#   set_request_id("abc-123")  # called once per request by the middleware
#   get_request_id()           # called anywhere in the same request to read it

import uuid
from contextvars import ContextVar

# Default is empty string so log records before the middleware runs don't crash.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the request-id bound to the current async context."""
    return _request_id_var.get()


def set_request_id(value: str) -> None:
    """Bind a request-id to the current async context.

    Called by RequestIdMiddleware once per incoming request.  Any code running
    inside the same async task (route handler, background step) will see this id.
    """
    _request_id_var.set(value)


def new_request_id() -> str:
    """Generate a fresh UUID-4 request id string."""
    return str(uuid.uuid4())
