"""DockerSandbox — bind-mount the worktree into a long-lived container.

Implementation strategy:

1. On ``open_async`` we ``docker run -d`` a container with:
   - cap-drop ALL, no-new-privileges
   - read-only / except for the worktree mount
   - no network by default (cards declare opt-in)
   - the worktree bind-mounted at /workspace
   - a sleep-infinity command so the container stays alive
2. read_file / write_file dispatch through ``docker exec`` with
   stdin/stdout pipes — slower than local FS but safer for arbitrary
   agent output.
3. ``close`` stops + removes the container.

If the ``docker`` binary is missing this class raises a clear
SandboxError on open; callers (the dispatcher) fall back to
LocalSandbox with a logged warning so V3 cards remain usable when
Docker isn't present.

Note: tested manually against real Docker; in this commit we ship the
implementation + a fake-CLI test that exercises argument shaping.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from apps.service.sandbox.local import SandboxError

log = logging.getLogger(__name__)

DEFAULT_IMAGE = "alpine:3.20"
DEFAULT_RUN_FLAGS = (
    "--rm",
    "-d",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--network=none",
    "--read-only",
    "--tmpfs=/tmp:rw,noexec,nosuid",
)
WORKDIR = "/workspace"


@dataclass
class DockerSandbox:
    worktree: Path
    image: str = DEFAULT_IMAGE
    container_id: str = ""
    extra_run_flags: tuple[str, ...] = ()
    network_enabled: bool = False
    docker_binary: str = field(default_factory=lambda: shutil.which("docker") or "")

    @classmethod
    async def open_async(
        cls,
        worktree: Path,
        *,
        image: str = DEFAULT_IMAGE,
        network_enabled: bool = False,
        extra_run_flags: Iterable[str] = (),
    ) -> DockerSandbox:
        sb = cls(
            worktree=worktree,
            image=image,
            extra_run_flags=tuple(extra_run_flags),
            network_enabled=network_enabled,
        )
        if not sb.docker_binary:
            raise SandboxError("docker binary not on PATH")
        flags = list(DEFAULT_RUN_FLAGS)
        if network_enabled:
            flags = [f for f in flags if f != "--network=none"]
        flags.extend(sb.extra_run_flags)
        flags.extend(
            [
                "-v",
                f"{worktree.resolve()}:{WORKDIR}",
                "-w",
                WORKDIR,
                image,
                "sh",
                "-c",
                "sleep infinity",
            ]
        )
        proc = await asyncio.create_subprocess_exec(
            sb.docker_binary,
            "run",
            *flags,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(
                f"docker run failed [{proc.returncode}]: {err.decode(errors='replace').strip()}"
            )
        sb.container_id = out.decode().strip()
        return sb

    async def close(self) -> None:
        if not self.container_id:
            return
        proc = await asyncio.create_subprocess_exec(
            self.docker_binary,
            "stop",
            self.container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        self.container_id = ""

    def _ensure(self) -> None:
        if not self.container_id:
            raise SandboxError("DockerSandbox not opened")

    async def _exec(
        self,
        *args: str,
        stdin_data: bytes | None = None,
    ) -> tuple[int, bytes, bytes]:
        self._ensure()
        proc = await asyncio.create_subprocess_exec(
            self.docker_binary,
            "exec",
            "-i",
            self.container_id,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(stdin_data)
        return proc.returncode or 0, out, err

    async def read_file(self, rel: str) -> bytes:
        # Note: path traversal is enforced at the WorktreeToolset layer;
        # we trust rel here since the bind mount confines it anyway.
        code, out, err = await self._exec("cat", f"{WORKDIR}/{rel}")
        if code != 0:
            raise SandboxError(f"read_file failed: {err.decode(errors='replace').strip()}")
        return out

    async def write_file(self, rel: str, content: bytes) -> None:
        # Pass the destination as a positional arg ($1) so a malicious
        # `rel` cannot break out of the quoted string and inject commands.
        target = f"{WORKDIR}/{rel}"
        code, _, err = await self._exec(
            "sh",
            "-c",
            'mkdir -p "$(dirname "$1")" && cat > "$1"',
            "sh",
            target,
            stdin_data=content,
        )
        if code != 0:
            raise SandboxError(f"write_file failed: {err.decode(errors='replace').strip()}")

    async def list_paths(self) -> Iterable[Path]:
        code, out, _err = await self._exec(
            "sh",
            "-c",
            'find "$1" -type f -printf "%P\\n" 2>/dev/null',
            "sh",
            WORKDIR,
        )
        if code != 0:
            return []
        rels = [ln for ln in out.decode(errors="replace").splitlines() if ln]
        return [self.worktree / rel for rel in rels]
