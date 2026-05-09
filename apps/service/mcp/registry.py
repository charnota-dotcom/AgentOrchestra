"""MCP server registry.

A trust-on-first-use catalog of Model Context Protocol servers the
user wants the orchestrator to expose to agents.  Each server has:

- a stable id, display name, command + args, env, transport (stdio /
  http / sse), trust level (untrusted / trusted), and a SHA-256 of the
  exact command+args at trust time so we can warn if the binary is
  later replaced.

V3 ships the registry + the trust workflow.  Wiring the registered
servers into agent dispatch (i.e. exposing them as tools to running
agents) is a V4 concern: it requires per-card opt-in and a sandbox
tier upgrade for non-bundled MCPs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from apps.service.types import long_id, utc_now


class MCPTransport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPTrust(StrEnum):
    UNTRUSTED = "untrusted"
    TRUSTED = "trusted"
    BLOCKED = "blocked"


@dataclass
class MCPServer:
    id: str
    name: str
    transport: MCPTransport
    command: str = ""  # for stdio
    args: list[str] | None = None  # for stdio
    url: str = ""  # for http / sse
    env: dict[str, str] | None = None
    trust: MCPTrust = MCPTrust.UNTRUSTED
    fingerprint: str = ""  # sha256 captured at trust time
    added_at: datetime = field(default_factory=utc_now)


def _fingerprint(server: MCPServer) -> str:
    payload = json.dumps(
        {
            "transport": server.transport.value,
            "command": server.command,
            "args": server.args or [],
            "url": server.url,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


_REGISTRY_PATH_ENV = "AGENTORCHESTRA_MCP_REGISTRY"


def default_registry_path() -> Path:
    return Path.home() / ".local" / "share" / "agentorchestra" / "mcp_registry.json"


def _load(path: Path) -> list[MCPServer]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    out: list[MCPServer] = []
    for item in raw:
        out.append(
            MCPServer(
                id=item["id"],
                name=item["name"],
                transport=MCPTransport(item["transport"]),
                command=item.get("command", ""),
                args=item.get("args") or [],
                url=item.get("url", ""),
                env=item.get("env") or {},
                trust=MCPTrust(item.get("trust", "untrusted")),
                fingerprint=item.get("fingerprint", ""),
                added_at=datetime.fromisoformat(item["added_at"]),
            )
        )
    return out


def _dump(path: Path, servers: list[MCPServer]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serial = [
        {
            "id": s.id,
            "name": s.name,
            "transport": s.transport.value,
            "command": s.command,
            "args": s.args or [],
            "url": s.url,
            "env": s.env or {},
            "trust": s.trust.value,
            "fingerprint": s.fingerprint,
            "added_at": s.added_at.isoformat(),
        }
        for s in servers
    ]
    path.write_text(json.dumps(serial, indent=2) + "\n")


def list_servers(path: Path | None = None) -> list[MCPServer]:
    return _load(path or default_registry_path())


def add_server(
    *,
    name: str,
    transport: MCPTransport | str,
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env: dict[str, str] | None = None,
    path: Path | None = None,
) -> MCPServer:
    p = path or default_registry_path()
    servers = _load(p)
    server = MCPServer(
        id=long_id(),
        name=name,
        transport=MCPTransport(transport) if isinstance(transport, str) else transport,
        command=command,
        args=args or [],
        url=url,
        env=env or {},
    )
    server.fingerprint = _fingerprint(server)
    servers.append(server)
    _dump(p, servers)
    return server


def trust_server(server_id: str, *, path: Path | None = None) -> MCPServer | None:
    p = path or default_registry_path()
    servers = _load(p)
    for s in servers:
        if s.id == server_id:
            s.trust = MCPTrust.TRUSTED
            s.fingerprint = _fingerprint(s)
            _dump(p, servers)
            return s
    return None


def block_server(server_id: str, *, path: Path | None = None) -> MCPServer | None:
    p = path or default_registry_path()
    servers = _load(p)
    for s in servers:
        if s.id == server_id:
            s.trust = MCPTrust.BLOCKED
            _dump(p, servers)
            return s
    return None


def remove_server(server_id: str, *, path: Path | None = None) -> bool:
    p = path or default_registry_path()
    servers = _load(p)
    after = [s for s in servers if s.id != server_id]
    if len(after) == len(servers):
        return False
    _dump(p, after)
    return True


def to_dict(server: MCPServer) -> dict[str, Any]:
    return {
        "id": server.id,
        "name": server.name,
        "transport": server.transport.value,
        "command": server.command,
        "args": server.args or [],
        "url": server.url,
        "env": server.env or {},
        "trust": server.trust.value,
        "fingerprint": server.fingerprint,
        "added_at": server.added_at.isoformat(),
    }
