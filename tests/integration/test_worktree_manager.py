"""WorktreeManager — end-to-end against a real git repo."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.service.types import (
    BlastRadiusPolicy,
    BranchState,
    CostPolicy,
    PersonalityCard,
    SandboxTier,
    short_id,
)
from apps.service.worktrees.manager import WorktreeManager

pytestmark = pytest.mark.integration


def _commit(repo: Path, files: dict[str, str], msg: str) -> None:
    import subprocess

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@e.com",
    }
    for path, body in files.items():
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body)
        subprocess.run(["git", "-C", str(repo), "add", path], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True, env=env)


def _make_card() -> PersonalityCard:
    return PersonalityCard(
        name="Demo",
        archetype="demo",
        description="d",
        template_id="t-demo",
        provider="anthropic",
        model="claude-sonnet-4-5",
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )


@pytest.mark.asyncio
async def test_register_workspace_rejects_non_git(store, tmp_path) -> None:
    mgr = WorktreeManager(store)
    notgit = tmp_path / "notgit"
    notgit.mkdir()
    with pytest.raises(Exception):
        await mgr.register_workspace(notgit)


@pytest.mark.asyncio
async def test_create_and_commit_and_cleanup(store, isolated_repo) -> None:
    mgr = WorktreeManager(store)
    # Stub out a card+template+instruction so the FK constraints in
    # the schema are happy.
    await store.db.execute(
        "INSERT INTO templates VALUES (?,?,?,?,?,?,?,?)",
        ("t-demo", "Demo", "demo", "body", "[]", 1, "h", "2026-01-01T00:00:00+00:00"),
    )
    await store.db.commit()

    ws = await mgr.register_workspace(isolated_repo, name="demo")
    card = _make_card()
    await store.insert_card(card)

    # Insert a stub instruction + run for FK.
    await store.db.execute(
        "INSERT INTO instructions VALUES (?,?,?,?,?,?,?)",
        ("i", "t-demo", 1, card.id, "rendered", "{}", "2026-01-01T00:00:00+00:00"),
    )
    await store.db.execute(
        """INSERT INTO runs (id, workspace_id, card_id, instruction_id,
            branch_id, state, state_changed_at, created_at,
            completed_at, cost_usd, cost_tokens, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "r1",
            ws.id,
            card.id,
            "i",
            None,
            "queued",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            None,
            0.0,
            0,
            None,
        ),
    )
    await store.db.commit()

    branch = await mgr.create("r1", ws, card)
    assert branch.state is BranchState.ACTIVE
    assert Path(branch.worktree_path).exists()
    assert branch.agent_branch_name.startswith("agent/demo/")

    # Write a file in the worktree and commit it through the manager.
    (Path(branch.worktree_path) / "agent_note.md").write_text("hello\n")
    sha = await mgr.commit(branch.id, ["agent_note.md"], "Add agent_note")
    assert len(sha) == 40

    # Request review and merge cleanly.
    review = await mgr.request_review(branch.id)
    assert "agent_note.md" in review["changed_files"]

    result = await mgr.approve_and_merge(branch.id, mode="clean")
    assert result["merged"] is True

    after = await store.get_branch(branch.id)
    assert after is not None
    assert after.state is BranchState.CLEANED
    assert not Path(branch.worktree_path).exists()


@pytest.mark.asyncio
async def test_panic_reset_clears_worktrees(store, isolated_repo) -> None:
    mgr = WorktreeManager(store)
    await store.db.execute(
        "INSERT INTO templates VALUES (?,?,?,?,?,?,?,?)",
        ("t-demo", "Demo", "demo", "body", "[]", 1, "h", "2026-01-01T00:00:00+00:00"),
    )
    await store.db.commit()
    ws = await mgr.register_workspace(isolated_repo)
    card = _make_card()
    await store.insert_card(card)

    for n in range(3):
        run_id = short_id(8)
        await store.db.execute(
            "INSERT INTO instructions VALUES (?,?,?,?,?,?,?)",
            (f"i{n}", "t-demo", 1, card.id, "r", "{}", "2026-01-01T00:00:00+00:00"),
        )
        await store.db.execute(
            """INSERT INTO runs (id, workspace_id, card_id, instruction_id,
                branch_id, state, state_changed_at, created_at,
                completed_at, cost_usd, cost_tokens, error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                ws.id,
                card.id,
                f"i{n}",
                None,
                "queued",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                None,
                0.0,
                0,
                None,
            ),
        )
        await store.db.commit()
        await mgr.create(run_id, ws, card)

    result = await mgr.panic_reset(ws.id)
    assert result["reset"] == 3
    # Main branch is still intact.
    assert (isolated_repo / "README.md").exists()
