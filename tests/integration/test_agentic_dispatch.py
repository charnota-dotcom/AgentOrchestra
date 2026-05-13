"""End-to-end agentic dispatch with a fake provider.

Verifies the worktree-bound path: branch creation, per-turn commits
from tool calls, diff captured as a DIFF artifact, RunState lands in
REVIEWING, and approval cleanly merges.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.dispatcher import RunDispatcher
from apps.service.providers.protocol import StreamEvent
from apps.service.providers.registry import register
from apps.service.types import (
    ArtifactKind,
    BlastRadiusPolicy,
    BranchState,
    CardMode,
    CostPolicy,
    Instruction,
    InstructionTemplate,
    PersonalityCard,
    RunState,
    SandboxTier,
    long_id,
)
from apps.service.worktrees.manager import WorktreeManager

pytestmark = pytest.mark.integration


class _FakeAgenticProvider:
    """Yields a plan, then one tool_call (write_file), then ends."""

    name = "fake-agentic"

    class _ChatSession:
        async def send(self, message):  # type: ignore[no-untyped-def]
            yield StreamEvent(kind="text_delta", text="1. Read README\n2. Add agent.txt\n")
            yield StreamEvent(kind="assistant_message", text="plan")
            yield StreamEvent(kind="usage", payload={"input_tokens": 5, "output_tokens": 3})
            yield StreamEvent(kind="finish")

        async def close(self):  # type: ignore[no-untyped-def]
            return None

    async def open_chat(self, card, *, system=None):  # type: ignore[no-untyped-def]
        return self._ChatSession()

    async def run_with_tools(  # type: ignore[no-untyped-def]
        self,
        card,
        *,
        system,
        user_message,
        executor,
        max_turns=16,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="usage", payload={"input_tokens": 100, "output_tokens": 50})
        # Turn 1: write a file.
        yield StreamEvent(
            kind="tool_call",
            text="write_file",
            payload={
                "tool_use_id": "u1",
                "name": "write_file",
                "params": {"path": "agent.txt", "content": "hello\n"},
            },
        )
        result = await executor.execute(
            "u1",
            "write_file",
            {"path": "agent.txt", "content": "hello\n"},
        )
        yield StreamEvent(
            kind="tool_result",
            payload={
                "tool_use_id": result.tool_use_id,
                "name": result.name,
                "is_error": result.is_error,
                "content": result.content,
            },
        )
        yield StreamEvent(kind="turn_end", payload={"turn": 1, "tool_calls": 1})

        # Turn 2: assistant says it's done.
        yield StreamEvent(kind="assistant_message", text="All done.")
        yield StreamEvent(kind="turn_end", payload={"turn": 2})
        yield StreamEvent(kind="finish", payload={"input_tokens": 100, "output_tokens": 50})

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _register() -> None:
    register("fake-agentic", _FakeAgenticProvider())  # type: ignore[arg-type]


async def _seed(store, archetype: str = "code-edit"):  # type: ignore[no-untyped-def]
    template = InstructionTemplate(
        id=long_id(),
        name="t",
        archetype=archetype,
        body="hi",
        variables=[],
        version=1,
        content_hash="h",
    )
    await store.insert_template(template)
    card = PersonalityCard(
        name="Plan Act",
        archetype=archetype,
        description="d",
        template_id=template.id,
        provider="fake-agentic",
        model="claude-sonnet-4-5",
        mode=CardMode.AGENTIC,
        requires_plan=True,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        max_turns=4,
        max_commits_per_run=10,
    )
    await store.insert_card(card)
    instruction = Instruction(
        id=long_id(),
        template_id=template.id,
        template_version=1,
        card_id=card.id,
        rendered_text="please write hello",
        variables={},
    )
    await store.insert_instruction(instruction)
    return card, instruction


@pytest.mark.asyncio
async def test_agentic_dispatch_creates_branch_commits_and_diff(
    store,
    isolated_repo: Path,
) -> None:
    bus = EventBus()
    store.on_append = bus.publish
    manager = WorktreeManager(store)
    dispatcher = RunDispatcher(store, manager, bus)

    workspace = await manager.register_workspace(isolated_repo, name="ws")
    card, instruction = await _seed(store)

    run = await dispatcher.dispatch(
        workspace_id=workspace.id,
        card_id=card.id,
        instruction_id=instruction.id,
        rendered_text="please write hello",
    )
    for _ in range(50):
        fetched = await store.get_run(run.id)
        if fetched and fetched.state is RunState.AWAITING_APPROVAL:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("run did not reach AWAITING_APPROVAL")

    ok = await dispatcher.approve_plan(run.id)
    assert ok

    await dispatcher._tasks[run.id]

    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.state is RunState.REVIEWING
    assert fetched.branch_id

    # Branch was created and committed into.
    branch = await store.get_branch(fetched.branch_id)
    assert branch is not None
    assert branch.state is BranchState.AWAITING_REVIEW
    assert branch.last_commit_sha

    # Diff artifact was produced and contains the new file.
    cur = await store.db.execute("SELECT kind, body FROM artifacts WHERE run_id = ?", (run.id,))
    rows = [dict(r) for r in await cur.fetchall()]
    assert any(r["kind"] == ArtifactKind.DIFF.value for r in rows)
    diff_row = next(r for r in rows if r["kind"] == ArtifactKind.DIFF.value)
    assert "agent.txt" in diff_row["body"]


@pytest.mark.asyncio
async def test_agentic_approve_merges_into_base(
    store,
    isolated_repo: Path,
) -> None:
    bus = EventBus()
    store.on_append = bus.publish
    manager = WorktreeManager(store)
    dispatcher = RunDispatcher(store, manager, bus)

    workspace = await manager.register_workspace(isolated_repo)
    card, instruction = await _seed(store)

    run = await dispatcher.dispatch(
        workspace_id=workspace.id,
        card_id=card.id,
        instruction_id=instruction.id,
        rendered_text="please write hello",
    )
    for _ in range(50):
        fetched = await store.get_run(run.id)
        if fetched and fetched.state is RunState.AWAITING_APPROVAL:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("run did not reach AWAITING_APPROVAL")

    ok = await dispatcher.approve_plan(run.id)
    assert ok

    await dispatcher._tasks[run.id]

    await dispatcher.approve(run.id, note="lgtm")

    final = await store.get_run(run.id)
    assert final is not None
    assert final.state is RunState.MERGED

    # The file is now on the base branch.
    assert (isolated_repo / "agent.txt").exists()


@pytest.mark.asyncio
async def test_agentic_dispatch_requires_workspace(store) -> None:
    bus = EventBus()
    manager = WorktreeManager(store)
    dispatcher = RunDispatcher(store, manager, bus)
    card, instruction = await _seed(store)

    with pytest.raises(Exception, match="workspace"):
        await dispatcher.dispatch(
            workspace_id=None,
            card_id=card.id,
            instruction_id=instruction.id,
            rendered_text="x",
        )
