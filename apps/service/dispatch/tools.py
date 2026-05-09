"""Tool catalog used by worktree-bound Runs.

V1 ships three tools — read_file, write_file, list_files — all
sandboxed to the Run's worktree path.  Bash / shell execution is
deferred to V2 because it requires the Docker sandbox tier.

Every tool returns a dict the LLM can consume.  Errors are returned as
``{"error": "<message>"}`` rather than raising, so the agent can react
and retry rather than the Run aborting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from apps.service.types import is_path_inside

log = logging.getLogger(__name__)

# Maximum bytes a single read_file or write_file can transfer.  Tunable
# per card later; for V1 we keep agents from accidentally streaming
# multi-megabyte files into the context window.
_MAX_BYTES = 256 * 1024


@dataclass
class ToolDef:
    """JSON-schema-described tool.  Adapter-agnostic shape."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool invocation."""

    tool_use_id: str
    name: str
    content: dict[str, Any]
    is_error: bool = False


@dataclass
class ToolInvocation:
    """Recorded for the event log + final summary."""

    tool_use_id: str
    name: str
    params: dict[str, Any]
    result: ToolResult | None = None


class ToolExecutor(Protocol):
    """Anything callable from an agent loop."""

    def tools(self) -> list[ToolDef]: ...
    async def execute(
        self,
        tool_use_id: str,
        name: str,
        params: dict[str, Any],
    ) -> ToolResult: ...


# ---------------------------------------------------------------------------
# WorktreeToolset
# ---------------------------------------------------------------------------


@dataclass
class WorktreeToolset:
    """File-touching tools confined to a single worktree path."""

    worktree: Path
    written_files: set[str] = field(default_factory=set)
    invocations: list[ToolInvocation] = field(default_factory=list)

    def tools(self) -> list[ToolDef]:
        return [
            ToolDef(
                name="read_file",
                description=(
                    "Read a file from the workspace.  Path must be relative to "
                    "the workspace root.  Returns the file's text content."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace-relative path to the file.",
                        }
                    },
                    "required": ["path"],
                },
            ),
            ToolDef(
                name="write_file",
                description=(
                    "Create or overwrite a file in the workspace.  Path must be "
                    "relative to the workspace root.  The change will be "
                    "committed as a save point at the end of the agent's turn."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace-relative destination path.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full contents of the file (UTF-8).",
                        },
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolDef(
                name="list_files",
                description=(
                    "List files in the workspace.  Returns up to 500 entries, "
                    "skipping the .git directory and worktree internals."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace-relative subdirectory; default is the root.",
                            "default": ".",
                        }
                    },
                },
            ),
        ]

    async def execute(
        self,
        tool_use_id: str,
        name: str,
        params: dict[str, Any],
    ) -> ToolResult:
        invocation = ToolInvocation(
            tool_use_id=tool_use_id,
            name=name,
            params=params,
        )
        try:
            if name == "read_file":
                content = await self._read_file(params["path"])
            elif name == "write_file":
                content = await self._write_file(params["path"], params["content"])
            elif name == "list_files":
                content = await self._list_files(params.get("path", "."))
            else:
                content = {"error": f"unknown tool: {name}"}
        except Exception as exc:
            log.exception("tool %s failed", name)
            content = {"error": f"{type(exc).__name__}: {exc}"}

        result = ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=content,
            is_error="error" in content,
        )
        invocation.result = result
        self.invocations.append(invocation)
        return result

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _resolve(self, rel: str) -> Path | None:
        candidate = (self.worktree / rel).resolve(strict=False)
        if not is_path_inside(candidate, self.worktree):
            return None
        return candidate

    async def _read_file(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target:
            return {"error": f"path escape rejected: {path!r}"}
        if not target.exists() or not target.is_file():
            return {"error": f"not a file: {path!r}"}
        size = target.stat().st_size
        if size > _MAX_BYTES:
            return {
                "error": f"file too large ({size} bytes); limit is {_MAX_BYTES}",
            }
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"error": "file is not valid UTF-8"}
        return {"path": path, "size": size, "content": text}

    async def _write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target:
            return {"error": f"path escape rejected: {path!r}"}
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            return {"error": (f"content too large ({len(encoded)} bytes); limit is {_MAX_BYTES}")}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        rel = str(target.relative_to(self.worktree))
        self.written_files.add(rel)
        return {"path": rel, "bytes_written": len(encoded)}

    async def _list_files(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        if not target:
            return {"error": f"path escape rejected: {path!r}"}
        if not target.exists():
            return {"error": f"not found: {path!r}"}
        if not target.is_dir():
            return {"error": f"not a directory: {path!r}"}
        entries: list[dict[str, Any]] = []
        for p in target.rglob("*"):
            try:
                rel = p.relative_to(self.worktree).as_posix()
            except ValueError:
                continue
            # Skip git internals.
            if rel.startswith(".git/") or "/.git/" in rel:
                continue
            if rel.startswith(".agent-worktrees/"):
                continue
            entries.append(
                {
                    "path": rel,
                    "is_file": p.is_file(),
                    "size": p.stat().st_size if p.is_file() else None,
                }
            )
            if len(entries) >= 500:
                break
        return {"root": path, "count": len(entries), "entries": entries}

    # ------------------------------------------------------------------

    def reset_written(self) -> set[str]:
        """Return the set of files written since the last reset, then clear."""
        out = set(self.written_files)
        self.written_files.clear()
        return out


def serialize_invocations(invocations: list[ToolInvocation]) -> str:
    """Pretty-print a tool-call timeline for use as an Artifact body."""
    lines: list[str] = []
    for inv in invocations:
        params_brief = json.dumps(inv.params, default=str)[:300]
        lines.append(f"- {inv.name}({params_brief})")
        if inv.result and inv.result.is_error:
            err = inv.result.content.get("error", "")
            lines.append(f"    ! {err}")
    return "\n".join(lines)
