"""LocalSandbox + DockerSandbox argument shaping."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.service.sandbox.local import LocalSandbox, SandboxError


@pytest.mark.asyncio
async def test_local_sandbox_round_trip(tmp_path: Path) -> None:
    sb = LocalSandbox(worktree=tmp_path)
    await sb.write_file("a/b.txt", b"hello\n")
    assert (tmp_path / "a" / "b.txt").read_bytes() == b"hello\n"
    assert (await sb.read_file("a/b.txt")) == b"hello\n"


@pytest.mark.asyncio
async def test_local_sandbox_blocks_traversal(tmp_path: Path) -> None:
    sb = LocalSandbox(worktree=tmp_path)
    with pytest.raises(SandboxError):
        await sb.read_file("../etc/passwd")
    with pytest.raises(SandboxError):
        await sb.write_file("../escape.txt", b"x")


@pytest.mark.asyncio
async def test_docker_sandbox_requires_binary(tmp_path: Path, monkeypatch) -> None:
    from apps.service.sandbox.docker import DockerSandbox

    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SandboxError):
        await DockerSandbox.open_async(tmp_path)
