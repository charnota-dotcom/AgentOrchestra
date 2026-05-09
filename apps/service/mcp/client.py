"""Minimal stdio MCP client.

Speaks JSON-RPC 2.0 over a child process's stdin/stdout per the
Model Context Protocol spec.  Implements just what the orchestrator
needs to expose third-party tools to running agents:

- ``initialize`` — handshake; we advertise basic capabilities.
- ``tools/list`` — fetch the tool catalog.
- ``tools/call`` — invoke a tool, return JSON-encoded content blocks
  flattened to a string for the orchestrator's normalised
  ToolExecutor shape.

We deliberately do NOT support the full MCP surface (resources,
prompts, sampling) at this stage.  V4 ships the runtime wiring; V5
will broaden the surface as cards start asking for it.

The client is intentionally tolerant of malformed responses — many
community MCP servers ship with rough edges and we'd rather log + skip
than abort an agent run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPClient:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    proc: asyncio.subprocess.Process | None = None
    _next_id: int = 0
    _read_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _initialized: bool = False
    _tools_cache: list[MCPTool] | None = None

    async def open(self) -> None:
        if self.proc is not None:
            return
        merged_env = {**os.environ, **(self.env or {})}
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        await self._initialize()

    async def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=2.0)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        self.proc = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_tools(self, *, refresh: bool = False) -> list[MCPTool]:
        if self._tools_cache is not None and not refresh:
            return self._tools_cache
        result = await self._call("tools/list", {})
        tools = []
        for entry in result.get("tools") or []:
            try:
                tools.append(
                    MCPTool(
                        name=entry["name"],
                        description=entry.get("description", ""),
                        input_schema=entry.get("inputSchema") or {"type": "object"},
                    )
                )
            except KeyError:
                log.warning("MCP tool entry missing 'name': %r", entry)
        self._tools_cache = tools
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        result = await self._call(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        # MCP returns ``content: [{type, text|...}]`` with optional
        # ``isError``.  We collapse the content blocks to a single text
        # field for the ToolExecutor shape and pass through the error
        # flag.
        content_blocks = result.get("content") or []
        text_parts: list[str] = []
        for block in content_blocks:
            t = block.get("type")
            if t == "text":
                text_parts.append(block.get("text", ""))
            else:
                text_parts.append(json.dumps(block, default=str))
        return {
            "content": "\n".join(text_parts),
            "is_error": bool(result.get("isError", False)),
            "raw": result,
        }

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------

    async def _initialize(self) -> None:
        if self._initialized:
            return
        await self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "agentorchestra",
                    "version": "0.4.0",
                },
            },
        )
        # MCP requires a follow-up notification (no response).
        await self._notify("notifications/initialized", {})
        self._initialized = True

    async def _call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("MCP client not opened")
        self._next_id += 1
        rid = self._next_id
        req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        line = json.dumps(req).encode() + b"\n"
        async with self._write_lock:
            self.proc.stdin.write(line)
            await self.proc.stdin.drain()

        async with self._read_lock:
            while True:
                raw = await asyncio.wait_for(
                    self.proc.stdout.readline(),
                    timeout=timeout,
                )
                if not raw:
                    raise RuntimeError("MCP server closed stdout")
                try:
                    msg = json.loads(raw.decode())
                except json.JSONDecodeError:
                    log.warning("MCP non-JSON line: %r", raw[:200])
                    continue
                # Skip notifications coming from the server.
                if "id" not in msg:
                    continue
                if msg["id"] != rid:
                    log.warning("MCP id mismatch: %r vs %r", msg.get("id"), rid)
                    continue
                if "error" in msg:
                    raise RuntimeError(
                        f"MCP error: {msg['error']}",
                    )
                return msg.get("result") or {}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        async with self._write_lock:
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
            await self.proc.stdin.drain()


async def open_servers(
    servers: Iterable[dict[str, Any]],
) -> list[tuple[str, MCPClient, list[MCPTool]]]:
    """Convenience: open every server in ``servers`` and return their
    tool catalogs.  Servers that fail to start are skipped with a
    warning rather than aborting the whole batch.
    """
    out: list[tuple[str, MCPClient, list[MCPTool]]] = []
    for spec in servers:
        if spec.get("transport") != "stdio":
            log.info("MCP transport %s not yet supported; skipping", spec.get("transport"))
            continue
        client = MCPClient(
            command=spec["command"],
            args=spec.get("args") or [],
            env=spec.get("env") or {},
        )
        try:
            await client.open()
            tools = await client.list_tools()
        except Exception as exc:
            log.warning("MCP server %s failed to start: %s", spec.get("name"), exc)
            await client.close()
            continue
        out.append((spec["name"], client, tools))
    return out
