"""Sandbox abstraction.

A Sandbox brokers filesystem reads, writes, and (eventually) command
execution between the agent's tool calls and the worktree on disk.
The default ``LocalSandbox`` does direct filesystem I/O — that's the
V1 devcontainer-style tier.  ``DockerSandbox`` brokers I/O through a
running Docker container that has the worktree bind-mounted, giving
process- and network-level isolation.

The protocol is deliberately small: V1's WorktreeToolset only needs
read_file, write_file, list_files.  ``run_command`` is reserved for
when a future archetype needs shell execution.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class Sandbox(Protocol):
    """Brokers I/O between the agent and the worktree."""

    @property
    def worktree(self) -> Path: ...

    async def read_file(self, rel: str) -> bytes: ...
    async def write_file(self, rel: str, content: bytes) -> None: ...
    async def list_paths(self) -> Iterable[Path]: ...
    async def close(self) -> None: ...
