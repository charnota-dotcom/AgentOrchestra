"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from apps.service.store.events import EventStore


_LOCAL_TMP = Path(__file__).resolve().parent.parent / ".tmp"
_LOCAL_TMP.mkdir(parents=True, exist_ok=True)
for _env_key in ("TMP", "TEMP", "TMPDIR"):
    os.environ.setdefault(_env_key, str(_LOCAL_TMP))
tempfile.tempdir = str(_LOCAL_TMP)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[EventStore]:
    db_path = tmp_path / "test.sqlite"
    s = EventStore(db_path)
    await s.open()
    # Tests insert minimal stubs that don't always satisfy every
    # referential link (a single test wants an Artifact without a real
    # Run, etc.); production runs with FK enforcement on, but in the
    # test fixture we disable it so we don't have to seed full FK
    # closures for every micro-test.
    await s.db.execute("PRAGMA foreign_keys = OFF")
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def isolated_repo(tmp_path: Path) -> Iterator[Path]:
    """Create a fresh git repo with one initial commit."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@e.com",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("# test repo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        env=env,
    )
    yield repo
