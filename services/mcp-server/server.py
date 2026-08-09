"""Builds the FastMCP server and auto-registers every tool and admin module.

Convention: any module directly inside tools/ or admin/ that defines a
module-level register(mcp) -> None function is imported and called
automatically. No central list, no switch statement — dropping a new file
in tools/ (e.g. tools/jira.py) with its own register() is the entire
integration step for a new agent-facing tool. Modules starting with "_" are
skipped, so private helpers can live alongside registered modules.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from mcp.server.fastmcp import FastMCP

import admin
import tools


def _register_all(package: ModuleType, mcp: FastMCP) -> None:
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{module_info.name}")
        register = getattr(module, "register", None)
        if register is None:
            raise RuntimeError(
                f"{package.__name__}.{module_info.name} has no register(mcp) function — "
                f"every module in {package.__name__}/ must define one"
            )
        register(mcp)


def build_mcp() -> FastMCP:
    mcp = FastMCP("research-agent-mcp-server")
    _register_all(tools, mcp)
    _register_all(admin, mcp)
    return mcp


__all__ = ["build_mcp"]
