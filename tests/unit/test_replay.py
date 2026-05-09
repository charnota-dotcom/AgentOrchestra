"""Replay/fork past runs."""

from __future__ import annotations

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
    SandboxTier,
    long_id,
)
from apps.service.worktrees.manager import WorktreeManager


class _Echo:
    name = "echo"

    class _S:
        async def send(self, message):  # type: ignore[no-untyped-def]
            yield StreamEvent(kind="text_delta", text=f"echo: {message[:20]}")
            yield StreamEvent(kind="assistant_message", text=f"echo: {message[:20]}")
            yield StreamEvent(kind="usage", payload={"input_tokens": 1, "output_tokens": 1})
            yield StreamEvent(kind="finish")

        async def close(self):  # type: ignore[no-untyped-def]
            return None

    async def open_chat(self, card, *, system=None):  # type: ignore[no-untyped-def]
        return self._S()

    async def run_with_tools(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _register() -> None:
    register("echo", _Echo())  # type: ignore[arg-type]


async def _seed(store):  # type: ignore[no-untyped-def]
    template = InstructionTemplate(
        id=long_id(),
        name="t",
        archetype="demo",
        body="hi",
        variables=[],
        version=1,
        content_hash="h",
    )
    await store.insert_template(template)
    card = PersonalityCard(
        name="Demo",
        archetype="demo",
        description="d",
        template_id=template.id,
        provider="echo",
        model="m1",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    await store.insert_card(card)
    ins = Instruction(
        id=long_id(),
        template_id=template.id,
        template_version=1,
        card_id=card.id,
        rendered_text="please respond",
        variables={},
    )
    await store.insert_instruction(ins)
    return card, ins


@pytest.mark.asyncio
async def test_replay_reuses_instruction_and_card(store) -> None:
    bus = EventBus()
    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)
    card, ins = await _seed(store)

    original = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text=ins.rendered_text,
    )
    await dispatcher._tasks[original.id]

    replay = await dispatcher.replay(original.id)
    await dispatcher._tasks[replay.id]
    assert replay.id != original.id
    final = await store.get_run(replay.id)
    assert final is not None
    assert final.state.value == "reviewing"
    # Both runs reference the same card_id (no override).
    assert final.card_id == original.card_id


@pytest.mark.asyncio
async def test_replay_with_model_override_clones_card(store) -> None:
    bus = EventBus()
    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)
    card, ins = await _seed(store)

    original = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text=ins.rendered_text,
    )
    await dispatcher._tasks[original.id]

    replay = await dispatcher.replay(original.id, model_override="m2")
    await dispatcher._tasks[replay.id]
    final = await store.get_run(replay.id)
    new_card = await store.get_card(final.card_id)
    assert new_card is not None
    assert new_card.model == "m2"
    assert new_card.id != card.id  # cloned, not mutated
