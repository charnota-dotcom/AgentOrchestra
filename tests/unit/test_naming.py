"""Branch-name regex and ID generators."""

from __future__ import annotations

import pytest

from apps.service.types import Branch, short_id


def test_short_id_uses_only_crockford_chars() -> None:
    allowed = set("0123456789abcdefghjkmnpqrstvwxyz")
    for _ in range(200):
        s = short_id(8)
        assert len(s) == 8
        assert set(s).issubset(allowed)


def test_short_id_is_unique_enough() -> None:
    seen = {short_id() for _ in range(2000)}
    # 2000 from ~40 bits of entropy should not collide
    assert len(seen) == 2000


def test_branch_name_must_start_with_agent_prefix() -> None:
    with pytest.raises(ValueError, match="agent/"):
        Branch(
            run_id="r1",
            workspace_id="w1",
            base_ref="0" * 40,
            base_branch_name="main",
            agent_branch_name="not-prefixed/x",
            worktree_path="/tmp/x",
        )


def test_branch_name_rejects_illegal_chars() -> None:
    with pytest.raises(ValueError):
        Branch(
            run_id="r1",
            workspace_id="w1",
            base_ref="0" * 40,
            base_branch_name="main",
            agent_branch_name="agent/with spaces/x",
            worktree_path="/tmp/x",
        )


def test_branch_name_accepts_archetype_slug() -> None:
    b = Branch(
        run_id="r1",
        workspace_id="w1",
        base_ref="0" * 40,
        base_branch_name="main",
        agent_branch_name="agent/qa-on-fix/01hf7e2k",
        worktree_path="/tmp/x",
    )
    assert b.agent_branch_name == "agent/qa-on-fix/01hf7e2k"
