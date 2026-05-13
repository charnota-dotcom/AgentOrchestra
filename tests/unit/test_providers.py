"""Provider registry + fallback chain semantics."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.dispatcher import RunDispatcher
from apps.service.providers.protocol import StreamEvent
from apps.service.providers.registry import (
    get_provider,
    known_providers,
    register,
)
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


def test_default_registry_lists_subscription_only() -> None:
    # The default install registers ONLY subscription / local providers
    # so accidental dispatch never bills against a metered API account.
    names = known_providers()
    assert "claude-cli" in names
    assert "gemini-cli" in names
    assert "codex-cli" in names
    assert "ollama" in names
    # API-keyed adapters are imported but deliberately not registered.
    assert "anthropic" not in names
    assert "google" not in names


def test_get_unknown_provider_raises() -> None:
    with pytest.raises(Exception):
        get_provider("nonexistent")


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


class _FailingProvider:
    name = "failing"

    async def open_chat(self, card, *, system=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("rate limited")

    async def run_with_tools(  # type: ignore[no-untyped-def]
        self,
        card,
        *,
        system,
        user_message,
        executor,
        max_turns=16,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="error", text="not used in this test")

    async def healthcheck(self) -> bool:
        return False


class _SecondaryProvider:
    name = "secondary"

    class _Session:
        async def send(self, message):  # type: ignore[no-untyped-def]
            yield StreamEvent(kind="text_delta", text="from-secondary")
            yield StreamEvent(kind="assistant_message", text="from-secondary")
            yield StreamEvent(kind="usage", payload={"input_tokens": 1, "output_tokens": 2})
            yield StreamEvent(kind="finish")

        async def close(self) -> None:
            return None

    async def open_chat(self, card, *, system=None):  # type: ignore[no-untyped-def]
        return self._Session()

    async def run_with_tools(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _register_fakes() -> None:
    register("failing", _FailingProvider())  # type: ignore[arg-type]
    register("secondary", _SecondaryProvider())  # type: ignore[arg-type]


async def _seed(store, *, primary: str, fallbacks: list) -> tuple:  # type: ignore[no-untyped-def]
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
        provider=primary,
        model="any",  # type: ignore[arg-type]
        mode=CardMode.CHAT,
        fallbacks=fallbacks,
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
        rendered_text="hi",
        variables={},
    )
    await store.insert_instruction(instruction)
    return card, instruction


@pytest.mark.asyncio
async def test_fallback_engages_when_primary_open_fails(store) -> None:
    bus = EventBus()
    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)
    card, ins = await _seed(
        store,
        primary="failing",
        fallbacks=[{"provider": "secondary", "model": "any"}],
    )
    run = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text="hi",
    )
    await dispatcher._tasks[run.id]
    cur = await store.db.execute("SELECT body FROM artifacts WHERE run_id = ?", (run.id,))
    rows = await cur.fetchall()
    assert any("from-secondary" in r["body"] for r in rows)


@pytest.mark.asyncio
async def test_no_fallbacks_fails_run(store) -> None:
    bus = EventBus()
    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)
    card, ins = await _seed(store, primary="failing", fallbacks=[])
    run = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text="hi",
    )
    await dispatcher._tasks[run.id]
    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.state.value == "aborted"


@pytest.mark.asyncio
async def test_dispatch_unknown_provider_in_fallback_skipped(store) -> None:
    bus = EventBus()
    dispatcher = RunDispatcher(store, WorktreeManager(store), bus)
    card, ins = await _seed(
        store,
        primary="failing",
        fallbacks=[
            {"provider": "definitely-missing", "model": "x"},
            {"provider": "secondary", "model": "any"},
        ],
    )
    run = await dispatcher.dispatch(
        workspace_id=None,
        card_id=card.id,
        instruction_id=ins.id,
        rendered_text="hi",
    )
    await dispatcher._tasks[run.id]
    final = await store.get_run(run.id)
    assert final is not None
    # The missing provider was skipped; secondary succeeded.
    assert final.state.value == "reviewing"
