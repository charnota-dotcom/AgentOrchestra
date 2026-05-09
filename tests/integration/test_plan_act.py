"""Plan-act split with HITL gate for agentic runs."""

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
    BlastRadiusPolicy,
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


class _PlanThenAct:
    """Plan phase yields text only; agent phase writes a file."""

    name = "plan-act-fake"

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
        yield StreamEvent(kind="usage", payload={"input_tokens": 10, "output_tokens": 5})
        yield StreamEvent(
            kind="tool_call",
            payload={
                "tool_use_id": "u1",
                "name": "write_file",
                "params": {"path": "agent.txt", "content": "ok\n"},
            },
        )
        result = await executor.execute(
            "u1",
            "write_file",
            {"path": "agent.txt", "content": "ok\n"},
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
        yield StreamEvent(kind="finish")

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _register() -> None:
    register("plan-act-fake", _PlanThenAct())  # type: ignore[arg-type]


async def _seed(store):  # type: ignore[no-untyped-def]
    template = InstructionTemplate(
        id=long_id(),
        name="t",
        archetype="code-edit",
        body="hi",
        variables=[],
        version=1,
        content_hash="h",
    )
    await store.insert_template(template)
    card = PersonalityCard(
        name="Plan Act",
        archetype="code-edit",
        description="d",
        template_id=template.id,
        provider="plan-act-fake",
        model="m",
        mode=CardMode.AGENTIC,
        requires_plan=True,
        max_turns=2,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    await store.insert_card(card)
    instruction = Instruction(
        id=long_id(),
        template_id=template.id,
        template_version=1,
        card_id=card.id,
        rendered_text="please do the thing",
        variables={},
    )
    await store.insert_instruction(instruction)
    return card, instruction


@pytest.mark.asyncio
async def test_plan_act_pauses_until_approval(store, isolated_repo: Path) -> None:
    bus = EventBus()
    store.on_append = bus.publish
    manager = WorktreeManager(store)
    dispatcher = RunDispatcher(store, manager, bus)
    workspace = await manager.register_workspace(isolated_repo)
    card, ins = await _seed(store)

    run = await dispatcher.dispatch(
        workspace_id=workspace.id,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text=ins.rendered_text,
    )

    # Wait until the run reaches AWAITING_APPROVAL and a PLAN artifact exists.
    for _ in range(50):
        fetched = await store.get_run(run.id)
        if fetched and fetched.state is RunState.AWAITING_APPROVAL:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("run did not reach AWAITING_APPROVAL")

    cur = await store.db.execute(
        "SELECT body FROM artifacts WHERE run_id = ? AND kind = 'plan'", (run.id,)
    )
    rows = await cur.fetchall()
    assert rows
    assert "Read README" in rows[0]["body"]

    # Approve.
    ok = await dispatcher.approve_plan(run.id)
    assert ok

    # Run should now finish.
    await dispatcher._tasks[run.id]
    final = await store.get_run(run.id)
    assert final is not None
    assert final.state is RunState.REVIEWING
