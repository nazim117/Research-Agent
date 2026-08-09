"""Filesystem tools, sandboxed to a workspace directory.

All paths are relative to FILE_WORK_DIR (default ./agent-workspace) and
cannot escape it — _safe_path rejects traversal via Path.is_relative_to(),
which is more robust than a manual string-prefix check.
"""

from __future__ import annotations

import os
from pathlib import Path

from instrumentation import tool


def _work_dir() -> Path:
    return Path(os.environ.get("FILE_WORK_DIR", "./agent-workspace")).resolve()


def _safe_path(path: str) -> Path:
    root = _work_dir()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"path {path!r} escapes the workspace")
    return candidate


def register(mcp) -> None:
    @tool(mcp)
    async def file_read(path: str) -> dict:
        """Read the contents of a file from the agent's workspace. Paths are
        relative to the workspace root and cannot escape it."""
        abs_path = _safe_path(path)
        try:
            content = abs_path.read_text()
        except OSError as exc:
            raise ValueError(f"read file failed: {exc}") from exc
        return {"path": path, "content": content, "bytes": len(content.encode())}

    @tool(mcp)
    async def file_write(path: str, content: str) -> dict:
        """Write content to a file in the agent's workspace, creating it (and
        any parent directories) if it doesn't exist. Overwrites existing
        files."""
        abs_path = _safe_path(path)
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content)
        except OSError as exc:
            raise ValueError(f"write file failed: {exc}") from exc
        return {"ok": True, "path": path, "bytes": len(content.encode())}

    @tool(mcp)
    async def file_list(path: str = ".") -> dict:
        """List the files and subdirectories in a directory within the
        agent's workspace."""
        abs_path = _safe_path(path)
        try:
            entries = sorted(abs_path.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            raise ValueError(f"list directory failed: {exc}") from exc

        out = []
        for entry in entries:
            is_dir = entry.is_dir()
            row: dict = {"name": entry.name, "is_dir": is_dir}
            if not is_dir:
                row["bytes"] = entry.stat().st_size
            out.append(row)

        return {"path": path, "count": len(out), "entries": out}
