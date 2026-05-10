"""Tests for the coding-session helpers: clone, git_status, switch_branch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from apps.service.worktrees.manager import WorktreeError, WorktreeManager


def _git_init(repo: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@e.com",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("# x\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        env=env,
    )


@pytest.mark.asyncio
async def test_clone_workspace_rejects_dash_url(tmp_path: Path, store) -> None:
    mgr = WorktreeManager(store)
    with pytest.raises(WorktreeError, match="invalid git URL"):
        await mgr.clone_workspace(
            "--upload-pack=evil",
            dest_dir=tmp_path / "dest",
        )


@pytest.mark.asyncio
async def test_clone_workspace_rejects_control_chars(tmp_path: Path, store) -> None:
    mgr = WorktreeManager(store)
    with pytest.raises(WorktreeError, match="control characters"):
        await mgr.clone_workspace(
            "https://example.com/x.git\nrm -rf",
            dest_dir=tmp_path / "dest",
        )


@pytest.mark.asyncio
async def test_clone_workspace_rejects_existing_dest(tmp_path: Path, store) -> None:
    mgr = WorktreeManager(store)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(WorktreeError, match="already exists"):
        await mgr.clone_workspace("https://example.com/x.git", dest_dir=dest)


@pytest.mark.asyncio
async def test_clone_workspace_rejects_dash_branch(tmp_path: Path, store) -> None:
    mgr = WorktreeManager(store)
    with pytest.raises(WorktreeError, match="invalid branch"):
        await mgr.clone_workspace(
            "https://example.com/x.git",
            dest_dir=tmp_path / "dest",
            branch="--upload-pack=evil",
        )


@pytest.mark.asyncio
async def test_clone_workspace_clones_local_url(tmp_path: Path, store) -> None:
    """End-to-end: clone a local source repo into a managed dest and
    register it.  Skipped gracefully if `git` isn't on PATH (CI image)."""
    if not _which("git"):
        pytest.skip("git not on PATH")
    src = tmp_path / "src"
    src.mkdir()
    _git_init(src)
    mgr = WorktreeManager(store)
    dest = tmp_path / "managed_clones" / "src"
    ws = await mgr.clone_workspace(f"file://{src}", dest_dir=dest, name="cloned-src")
    assert ws.name == "cloned-src"
    assert Path(ws.repo_path).resolve() == dest.resolve()
    assert (dest / "README.md").is_file()


def _which(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None


@pytest.mark.asyncio
async def test_workspaces_git_status_reports_branch(tmp_path: Path, store) -> None:
    """workspaces.git_status reports the current branch + clean state."""
    if not _which("git"):
        pytest.skip("git not on PATH")
    src = tmp_path / "repo"
    src.mkdir()
    _git_init(src)

    # Use the Handlers helper directly — register the workspace and
    # then ask for status.  We construct a minimal handler stub via
    # the public RPC-style call.
    from apps.service.dispatch.bus import EventBus
    from apps.service.dispatch.dispatcher import RunDispatcher
    from apps.service.main import Handlers
    from apps.service.worktrees.manager import WorktreeManager as _WM

    bus = EventBus()
    mgr = _WM(store)
    disp = RunDispatcher(store, mgr, bus)
    h = Handlers(store, mgr, disp, data_dir=tmp_path / "data")

    ws = await mgr.register_workspace(src, name="repo")
    res = await h.workspaces_git_status({"workspace_id": ws.id})
    assert res["is_git"] is True
    assert res["branch"] == "main"
    assert res["modified"] == 0
    assert res["staged"] == 0
    assert res["untracked"] == 0
    assert res["last_commit_subject"].startswith("init")


@pytest.mark.asyncio
async def test_workspaces_switch_branch_creates_branch(tmp_path: Path, store) -> None:
    if not _which("git"):
        pytest.skip("git not on PATH")
    src = tmp_path / "repo"
    src.mkdir()
    _git_init(src)

    from apps.service.dispatch.bus import EventBus
    from apps.service.dispatch.dispatcher import RunDispatcher
    from apps.service.main import Handlers
    from apps.service.worktrees.manager import WorktreeManager as _WM

    bus = EventBus()
    mgr = _WM(store)
    disp = RunDispatcher(store, mgr, bus)
    h = Handlers(store, mgr, disp, data_dir=tmp_path / "data")
    ws = await mgr.register_workspace(src, name="repo")
    res = await h.workspaces_switch_branch(
        {"workspace_id": ws.id, "branch": "feature/x", "create": True}
    )
    assert res["branch"] == "feature/x"
    # And reading status again sees the new branch.
    status = await h.workspaces_git_status({"workspace_id": ws.id})
    assert status["branch"] == "feature/x"


@pytest.mark.asyncio
async def test_workspaces_switch_branch_rejects_dash(tmp_path: Path, store) -> None:
    from apps.service.dispatch.bus import EventBus
    from apps.service.dispatch.dispatcher import RunDispatcher
    from apps.service.main import Handlers
    from apps.service.worktrees.manager import WorktreeManager as _WM

    if not _which("git"):
        pytest.skip("git not on PATH")
    src = tmp_path / "repo"
    src.mkdir()
    _git_init(src)
    bus = EventBus()
    mgr = _WM(store)
    disp = RunDispatcher(store, mgr, bus)
    h = Handlers(store, mgr, disp, data_dir=tmp_path / "data")
    ws = await mgr.register_workspace(src, name="repo")
    with pytest.raises(ValueError, match="invalid branch"):
        await h.workspaces_switch_branch(
            {"workspace_id": ws.id, "branch": "--evil", "create": False}
        )


@pytest.mark.asyncio
async def test_build_repo_system_prompt_inlines_claude_md(tmp_path: Path, store) -> None:
    if not _which("git"):
        pytest.skip("git not on PATH")
    src = tmp_path / "repo"
    src.mkdir()
    _git_init(src)
    (src / "CLAUDE.md").write_text(
        "# Project conventions\n\n- always run pytest\n",
        encoding="utf-8",
    )
    # Re-add so the convention file is part of HEAD; not strictly
    # required for the prompt builder which reads from disk, but
    # keeps the repo state coherent.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.com",
    }
    subprocess.run(["git", "-C", str(src), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(src), "commit", "-q", "-m", "claude.md"],
        check=True,
        env=env,
    )

    from apps.service.dispatch.bus import EventBus
    from apps.service.dispatch.dispatcher import RunDispatcher
    from apps.service.main import Handlers
    from apps.service.worktrees.manager import WorktreeManager as _WM

    bus = EventBus()
    mgr = _WM(store)
    disp = RunDispatcher(store, mgr, bus)
    h = Handlers(store, mgr, disp, data_dir=tmp_path / "data")
    ws = await mgr.register_workspace(src, name="repo")

    # Re-fetch the typed Workspace so the helper receives the canonical row.
    ws_row = await store.get_workspace(ws.id)
    prompt = await h._build_repo_system_prompt(ws_row, base_system="be terse")
    assert "operating inside the project" in prompt
    assert "branch 'main'" in prompt
    assert "CLAUDE.md" in prompt
    assert "always run pytest" in prompt
    assert "be terse" in prompt
    # Order: header first, then convention block, then base_system.
    assert prompt.index("operating inside") < prompt.index("CLAUDE.md")
    assert prompt.index("CLAUDE.md") < prompt.index("be terse")
