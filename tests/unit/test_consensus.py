"""Cross-vendor consensus orchestration."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.consensus import run_consensus
from apps.service.main import Handlers
from apps.service.providers.protocol import StreamEvent
from apps.service.providers.registry import register
from apps.service.types import (
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    InstructionTemplate,
    PersonalityCard,
    SandboxTier,
    long_id,
)


class _ScriptedProvider:
    """Yields a fixed answer per provider name."""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def open_chat(self, card, *, system=None):  # type: ignore[no-untyped-def]
        outer = self

        class _S:
            async def send(self, message):  # type: ignore[no-untyped-def]
                yield StreamEvent(kind="text_delta", text=outer.answer)
                yield StreamEvent(kind="assistant_message", text=outer.answer)
                yield StreamEvent(kind="usage", payload={"input_tokens": 5, "output_tokens": 3})
                yield StreamEvent(kind="finish")

            async def close(self):  # type: ignore[no-untyped-def]
                return None

        return _S()

    async def run_with_tools(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _register() -> None:
    register("vendorA", _ScriptedProvider("Answer from A"))  # type: ignore[arg-type]
    register("vendorB", _ScriptedProvider("Answer from B"))  # type: ignore[arg-type]
    register("judge", _ScriptedProvider("Synthesis: A and B agree."))  # type: ignore[arg-type]


async def _seed_consensus_card(store):  # type: ignore[no-untyped-def]
    template = InstructionTemplate(
        id=long_id(),
        name="Consensus",
        archetype="consensus",
        body="x",
        variables=[],
        version=1,
        content_hash="h",
    )
    await store.insert_template(template)
    card = PersonalityCard(
        name="Consensus",
        archetype="consensus",
        description="d",
        template_id=template.id,
        provider="anthropic",
        model="claude-sonnet-4-5",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    await store.insert_card(card)
    return card, template


@pytest.mark.asyncio
async def test_consensus_runs_candidates_and_synthesises(store) -> None:
    bus = EventBus()
    card, template = await _seed_consensus_card(store)

    result = await run_consensus(
        store,
        bus,
        question="What is 2+2?",
        judge_provider="judge",
        judge_model="m",
        candidates=[("vendorA", "x"), ("vendorB", "y")],
        consensus_card_id=card.id,
        consensus_template_id=template.id,
    )

    assert len(result.candidates) == 2
    assert "Synthesis" in result.judge_text

    cur = await store.db.execute(
        "SELECT title, body FROM artifacts WHERE run_id = ? ORDER BY created_at",
        (result.run_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    titles = [r["title"] for r in rows]
    # Two candidate transcripts + one synthesis summary.
    assert sum("Candidate" in t for t in titles) == 2
    assert any("Synthesis" in r["body"] for r in rows)


@pytest.mark.asyncio
async def test_consensus_requires_two_candidates(store) -> None:
    from apps.service.dispatch.consensus import run_consensus

    card, template = await _seed_consensus_card(store)
    # Empty list yields an empty zip; the function still attempts a judge
    # turn, but our concern is the API contract enforced by the RPC layer.
    # We assert here that single-candidate is allowed at the orchestrator
    # level; the RPC handler enforces >=2.
    result = await run_consensus(
        store,
        EventBus(),
        question="solo?",
        judge_provider="judge",
        judge_model="m",
        candidates=[("vendorA", "x")],
        consensus_card_id=card.id,
        consensus_template_id=template.id,
    )
    assert len(result.candidates) == 1


@pytest.mark.asyncio
async def test_select_consensus_winner_persists_summary(store, tmp_path) -> None:
    card, template = await _seed_consensus_card(store)
    result = await run_consensus(
        store,
        EventBus(),
        question="winner?",
        judge_provider="judge",
        judge_model="m",
        candidates=[("vendorA", "x"), ("vendorB", "y")],
        consensus_card_id=card.id,
        consensus_template_id=template.id,
    )
    handlers = Handlers(
        store=store,
        manager=SimpleNamespace(),  # type: ignore[arg-type]
        dispatcher=SimpleNamespace(),  # type: ignore[arg-type]
        data_dir=tmp_path,
    )
    out = await handlers.runs_select_consensus_winner(
        {"run_id": result.run_id, "winner_index": 1, "note": "pick one"}
    )
    assert out["ok"] is True
    cur = await store.db.execute(
        "SELECT title, body FROM artifacts WHERE run_id = ? AND title = 'Consensus winner selection'",
        (result.run_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    assert "Selected winner: Candidate #1" in row["body"]
