"""WorktreeToolset behavior, including path-traversal guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.service.dispatch.tools import WorktreeToolset


@pytest.fixture
def toolset(tmp_path: Path) -> WorktreeToolset:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "README.md").write_text("# hi\n")
    (wt / "src").mkdir()
    (wt / "src" / "a.py").write_text("print('a')\n")
    return WorktreeToolset(worktree=wt)


@pytest.mark.asyncio
async def test_read_file_returns_content(toolset: WorktreeToolset) -> None:
    result = await toolset.execute("u1", "read_file", {"path": "README.md"})
    assert not result.is_error
    assert result.content["content"] == "# hi\n"


@pytest.mark.asyncio
async def test_read_file_outside_worktree_blocked(toolset: WorktreeToolset) -> None:
    result = await toolset.execute("u1", "read_file", {"path": "../escape.txt"})
    assert result.is_error
    assert "path escape" in result.content["error"]


@pytest.mark.asyncio
async def test_write_file_records_path(toolset: WorktreeToolset) -> None:
    result = await toolset.execute(
        "u1", "write_file", {"path": "src/b.py", "content": "print('b')\n"}
    )
    assert not result.is_error
    assert "src/b.py" in toolset.written_files
    assert (toolset.worktree / "src" / "b.py").read_text() == "print('b')\n"


@pytest.mark.asyncio
async def test_write_file_path_traversal_blocked(toolset: WorktreeToolset) -> None:
    result = await toolset.execute("u1", "write_file", {"path": "../escaped.txt", "content": "x"})
    assert result.is_error


@pytest.mark.asyncio
async def test_write_file_size_capped(toolset: WorktreeToolset) -> None:
    big = "x" * (300 * 1024)
    result = await toolset.execute(
        "u1",
        "write_file",
        {"path": "big.txt", "content": big},
    )
    assert result.is_error
    assert "too large" in result.content["error"]


@pytest.mark.asyncio
async def test_list_files_skips_git_internals(toolset: WorktreeToolset) -> None:
    (toolset.worktree / ".git").mkdir()
    (toolset.worktree / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    result = await toolset.execute("u1", "list_files", {"path": "."})
    assert not result.is_error
    paths = {e["path"] for e in result.content["entries"]}
    assert "README.md" in paths
    assert not any(p.startswith(".git/") for p in paths)


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(toolset: WorktreeToolset) -> None:
    result = await toolset.execute("u1", "exec_shell", {})
    assert result.is_error


@pytest.mark.asyncio
async def test_reset_written_clears_set(toolset: WorktreeToolset) -> None:
    await toolset.execute("u1", "write_file", {"path": "x.txt", "content": "y"})
    assert "x.txt" in toolset.written_files
    cleared = toolset.reset_written()
    assert cleared == {"x.txt"}
    assert toolset.written_files == set()
