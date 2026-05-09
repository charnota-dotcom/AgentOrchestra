"""RunDispatcher with a fake provider."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.dispatcher import DispatchError, RunDispatcher
from apps.service.providers.protocol import StreamEvent
from apps.service.providers.registry import register
from apps.service.types import (
    BlastRadiusPolicy,
    CostPolicy,
    Instruction,
    InstructionTemplate,
    PersonalityCard,
    RunState,
    SandboxTier,
    long_id,
)


class _FakeChat:
    name = "fake"

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="text_delta", text="Hello, ")
        yield StreamEvent(kind="text_delta", text="world.")
        yield StreamEvent(
            kind="assistant_message", text="Hello, world.",
            payload={"finished_at": "2026-01-01T00:00:00+00:00"},
        )
        yield StreamEvent(
            kind="usage",
            payload={"input_tokens": 12, "output_tokens": 5},
        )
        yield StreamEvent(kind="finish")


class _FakeProvider:
    name = "fake"

    async def open_chat(self, card, *, system=None) -> _FakeChat:  # type: ignore[no-untyped-def]
        return _FakeChat()

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _register_fake_provider() -> None:
    register("fake", _FakeProvider())  # type: ignore[arg-type]


async def _seed_card(store) -> PersonalityCard:  # type: ignore[no-untyped-def]
    template = InstructionTemplate(
        id=long_id(), name="t", archetype="demo",
        body="hi", variables=[], version=1, content_hash="h",
    )
    await store.insert_template(template)
    card = PersonalityCard(
        name="Demo", archetype="demo", description="d",
        template_id=template.id,
        provider="fake", model="claude-sonnet-4-5",
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    await store.insert_card(card)
    return card


@pytest.mark.asyncio
async def test_dispatch_completes_into_reviewing_state(store, tmp_path) -> None:
    from apps.service.worktrees.manager import WorktreeManager
    bus = EventBus()
    store.on_append = bus.publish

    card = await _seed_card(store)
    ins = Instruction(
        id=long_id(), template_id=card.template_id, template_version=1,
        card_id=card.id, rendered_text="please respond",
        variables={},
    )
    await store.insert_instruction(ins)

    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)
    run = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text="please respond",
    )
    # Wait for the background task to finish.
    task = dispatcher._tasks.get(run.id)
    assert task is not None
    await task

    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.state is RunState.REVIEWING

    # An artifact should exist with the assistant text.
    cur = await store.db.execute(
        "SELECT title, body FROM artifacts WHERE run_id = ?", (run.id,)
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert "Hello, world." in rows[0]["body"]


@pytest.mark.asyncio
async def test_approve_only_from_reviewing(store) -> None:
    from apps.service.worktrees.manager import WorktreeManager
    bus = EventBus()
    card = await _seed_card(store)
    ins = Instruction(
        id=long_id(), template_id=card.template_id, template_version=1,
        card_id=card.id, rendered_text="x", variables={},
    )
    await store.insert_instruction(ins)
    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)

    run = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text="x",
    )
    # Approve before dispatch finishes -> run is still in EXECUTING/PLANNING.
    with pytest.raises(DispatchError, match="REVIEWING"):
        await dispatcher.approve(run.id)
    # Wait for completion, then approve should succeed.
    await dispatcher._tasks[run.id]
    await dispatcher.approve(run.id, note="lgtm")
    final = await store.get_run(run.id)
    assert final is not None
    assert final.state is RunState.MERGED
