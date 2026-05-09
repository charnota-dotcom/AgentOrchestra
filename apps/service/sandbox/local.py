"""LocalSandbox — direct filesystem access (V1 devcontainer-style tier).

Path-traversal blocking happens here, so the WorktreeToolset can use
the sandbox uniformly across tiers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from apps.service.types import is_path_inside


class SandboxError(Exception):
    pass


@dataclass
class LocalSandbox:
    worktree: Path

    def _resolve(self, rel: str) -> Path:
        target = (self.worktree / rel).resolve(strict=False)
        if not is_path_inside(target, self.worktree):
            raise SandboxError(f"path escape rejected: {rel!r}")
        return target

    async def read_file(self, rel: str) -> bytes:
        target = self._resolve(rel)
        if not target.exists() or not target.is_file():
            raise SandboxError(f"not a file: {rel!r}")
        return target.read_bytes()

    async def write_file(self, rel: str, content: bytes) -> None:
        target = self._resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def list_paths(self) -> Iterable[Path]:
        return list(self.worktree.rglob("*"))

    async def close(self) -> None:
        return None
