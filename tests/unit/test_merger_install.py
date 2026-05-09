"""Mergiraf merge-driver installation."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.service.worktrees import merger


@pytest.mark.asyncio
async def test_install_no_op_when_unavailable(tmp_path: Path, monkeypatch) -> None:
    async def _fake_unavail() -> bool:
        return False

    monkeypatch.setattr(merger, "is_available", _fake_unavail)
    installed = await merger.install_as_merge_driver(tmp_path)
    assert installed is False
    assert not (tmp_path / ".gitattributes").exists()


@pytest.mark.asyncio
async def test_install_writes_gitattributes_when_available(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    async def _fake_avail() -> bool:
        return True

    monkeypatch.setattr(merger, "is_available", _fake_avail)
    installed = await merger.install_as_merge_driver(tmp_path)
    assert installed is True
    attrs = (tmp_path / ".gitattributes").read_text()
    assert "agentorchestra:mergiraf" in attrs
    assert "*.py merge=mergiraf" in attrs

    # Idempotent — second call doesn't duplicate.
    again = await merger.install_as_merge_driver(tmp_path)
    assert again is True
    assert (tmp_path / ".gitattributes").read_text().count("agentorchestra:mergiraf") == 1
