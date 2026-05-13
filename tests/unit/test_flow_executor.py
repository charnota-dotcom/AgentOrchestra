"""FlowExecutor — validation, topo sort, branch routing.

These tests don't spin up a real LLM provider; they monkeypatch the
provider registry to return a stub session that yields a single
``text_delta`` and a ``finish``.  Real provider integration is covered
by the existing per-provider tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import json

import pytest
import pytest_asyncio

from apps.service.flows.executor import FlowExecutor, FlowValidationError
from apps.service.providers import registry as provider_registry
from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.store.events import EventStore
from apps.service.types import (
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    Flow,
    FlowState,
    PersonalityCard,
    SandboxTier,
)


class _StubSession(ChatSession):
    name = "stub"

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(kind="text_delta", text=self._reply)
        yield StreamEvent(kind="finish")

    async def close(self) -> None:
        return None


class _StubProvider:
    name = "stub"

    def __init__(self, replies: dict[str, str]) -> None:
        self._replies = replies

    async def open_chat(self, card: PersonalityCard, *, system: str | None = None) -> ChatSession:
        return _StubSession(self._replies.get(card.id, f"reply for {card.name}"))

    async def healthcheck(self) -> bool:
        return True


def _card(card_id: str, name: str = "Test") -> PersonalityCard:
    return PersonalityCard(
        id=card_id,
        name=name,
        archetype="demo",
        description="d",
        template_id="t",
        provider="stub",
        model="x",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[EventStore]:
    s = EventStore(tmp_path / "test.sqlite")
    await s.open()
    # Match the conftest fixture's behaviour — disable FK enforcement
    # so micro-tests can insert cards / artifacts without first
    # seeding their full referential closure.
    await s.db.execute("PRAGMA foreign_keys = OFF")
    yield s
    await s.close()


def test_validate_rejects_cycle() -> None:
    flow = Flow(
        name="cyclic",
        nodes=[
            {"id": "a", "type": "agent", "card_id": "c"},
            {"id": "b", "type": "agent", "card_id": "c"},
        ],
        edges=[
            {"from_node": "a", "from_port": "out", "to_node": "b", "to_port": "in"},
            {"from_node": "b", "from_port": "out", "to_node": "a", "to_port": "in"},
        ],
    )
    ex = FlowExecutor(store=None)  # type: ignore[arg-type]
    with pytest.raises(FlowValidationError, match="cycle"):
        ex._validate(flow)


def test_validate_rejects_dangling_edge() -> None:
    flow = Flow(
        name="dangling",
        nodes=[{"id": "a", "type": "trigger"}],
        edges=[{"from_node": "a", "from_port": "out", "to_node": "ghost", "to_port": "in"}],
    )
    ex = FlowExecutor(store=None)  # type: ignore[arg-type]
    with pytest.raises(FlowValidationError, match="unknown node"):
        ex._validate(flow)


def test_branch_evaluates_regex() -> None:
    node = {"id": "b", "type": "branch", "params": {"pattern": "found:"}}
    text, take_true = FlowExecutor._run_branch(node, ["found: yes"])
    assert take_true is True
    assert text == "found: yes"

    text2, take_true2 = FlowExecutor._run_branch(node, ["nothing"])
    assert take_true2 is False
    assert text2 == "nothing"


def test_merge_concatenates() -> None:
    out = FlowExecutor._run_merge(["alpha", "beta"])
    assert "alpha" in out and "beta" in out
    assert out.count("---") == 1


@pytest.mark.asyncio
async def test_run_simple_chain(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trigger → AgentA → Output, end-to-end."""
    card = _card("card-1", "AgentA")
    await store.insert_card(card)

    monkeypatch.setattr(
        provider_registry,
        "get_provider",
        lambda name: _StubProvider({"card-1": "agent reply!"}),
    )

    flow = Flow(
        name="chain",
        nodes=[
            {"id": "t", "type": "trigger"},
            {"id": "a", "type": "agent", "card_id": "card-1"},
            {"id": "o", "type": "output"},
        ],
        edges=[
            {"from_node": "t", "from_port": "start", "to_node": "a", "to_port": "in"},
            {"from_node": "a", "from_port": "out", "to_node": "o", "to_port": "in"},
        ],
    )
    await store.insert_flow(flow)

    ex = FlowExecutor(store)
    run = await ex.dispatch(flow)
    # Wait for the supervisor task to finish.
    task = ex._active.get(run.id)
    assert task is not None
    await task

    refreshed = await store.get_flow_run(run.id)
    assert refreshed is not None
    assert refreshed.state == FlowState.FINISHED
    assert refreshed.node_outputs.get("a") == "agent reply!"
    assert refreshed.node_outputs.get("o") == "agent reply!"


@pytest.mark.asyncio
async def test_branch_skips_not_taken_path(
    store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = _card("card-1", "AgentA")
    await store.insert_card(card)
    card2 = _card("card-2", "AgentB")
    await store.insert_card(card2)

    monkeypatch.setattr(
        provider_registry,
        "get_provider",
        lambda name: _StubProvider({"card-1": "found: yes", "card-2": "should not run"}),
    )

    flow = Flow(
        name="branchy",
        nodes=[
            # Entry agent with no upstream — has to carry its own goal.
            {
                "id": "a",
                "type": "agent",
                "card_id": "card-1",
                "params": {"goal": "kick off"},
            },
            {"id": "br", "type": "branch", "params": {"pattern": "found:"}},
            {"id": "true_path", "type": "output"},
            {"id": "false_path", "type": "agent", "card_id": "card-2"},
        ],
        edges=[
            {"from_node": "a", "from_port": "out", "to_node": "br", "to_port": "in"},
            {"from_node": "br", "from_port": "true", "to_node": "true_path", "to_port": "in"},
            {"from_node": "br", "from_port": "false", "to_node": "false_path", "to_port": "in"},
        ],
    )
    await store.insert_flow(flow)

    ex = FlowExecutor(store)
    # Hand-roll a run since there's no Trigger; execution still
    # progresses because nodes with no inputs are wave-1 and `a` has
    # zero in-degree.
    run = await ex.dispatch(flow)
    await ex._active[run.id]

    refreshed = await store.get_flow_run(run.id)
    assert refreshed is not None
    assert refreshed.state == FlowState.FINISHED
    # True path took the output.
    assert refreshed.node_outputs.get("true_path") == "found: yes"
    # False path was skipped — never executed agent-2.
    assert "false_path" not in refreshed.node_outputs


@pytest.mark.asyncio
async def test_consensus_node_fanout_and_judge(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    c1 = _card("card-c1", "Candidate1")
    c2 = _card("card-c2", "Candidate2")
    cj = _card("card-judge", "Judge")
    await store.insert_card(c1)
    await store.insert_card(c2)
    await store.insert_card(cj)

    monkeypatch.setattr(
        provider_registry,
        "get_provider",
        lambda name: _StubProvider(
            {
                "card-c1": "answer A",
                "card-c2": "answer B",
                "card-judge": "winner: 1\nfinal: answer A",
            }
        ),
    )

    flow = Flow(
        name="consensus",
        nodes=[
            {"id": "t", "type": "trigger"},
            {
                "id": "cn",
                "type": "consensus",
                "card_id": "card-judge",
                "params": {"candidate_card_ids": ["card-c1", "card-c2"]},
            },
            {"id": "o", "type": "output"},
        ],
        edges=[
            {"from_node": "t", "from_port": "start", "to_node": "cn", "to_port": "in"},
            {"from_node": "cn", "from_port": "out", "to_node": "o", "to_port": "in"},
        ],
    )
    await store.insert_flow(flow)

    ex = FlowExecutor(store)
    run = await ex.dispatch(flow)
    task = ex._active.get(run.id)
    assert task is not None
    await task

    refreshed = await store.get_flow_run(run.id)
    assert refreshed is not None
    assert refreshed.state == FlowState.FINISHED
    cn_out = refreshed.node_outputs.get("cn")
    assert cn_out is not None
    payload = json.loads(cn_out)
    assert len(payload["candidates"]) == 2
    assert "winner" in payload["judge"]["output"]


@pytest.mark.asyncio
async def test_staging_area_manual_release(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card("card-1", "Reaper")
    await store.insert_card(card)

    monkeypatch.setattr(
        provider_registry,
        "get_provider",
        lambda name: _StubProvider({"card-1": "reaper reply"}),
    )

    flow = Flow(
        name="staging",
        nodes=[
            {"id": "t", "type": "trigger"},
            {
                "id": "s",
                "type": "staging_area",
                "params": {"mode": "manual_release", "timeout_seconds": 5},
            },
            {"id": "o", "type": "output"},
        ],
        edges=[
            {"from_node": "t", "from_port": "start", "to_node": "s", "to_port": "in"},
            {"from_node": "s", "from_port": "out", "to_node": "o", "to_port": "in"},
        ],
    )
    await store.insert_flow(flow)

    ex = FlowExecutor(store)
    run = await ex.dispatch(flow)
    task = ex._active.get(run.id)
    assert task is not None

    for _ in range(20):
        if await ex.approve_human(run.id, "s", True):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("staging node did not start waiting in time")
    await task

    refreshed = await store.get_flow_run(run.id)
    assert refreshed is not None
    assert refreshed.state == FlowState.FINISHED
    assert "s" in refreshed.node_outputs
    assert "start" in refreshed.node_outputs["s"]


@pytest.mark.asyncio
async def test_reaper_alias_runs_with_canonical_type(store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card("card-1", "ReaperAlias")
    await store.insert_card(card)

    monkeypatch.setattr(
        provider_registry,
        "get_provider",
        lambda name: _StubProvider({"card-1": "alias reply"}),
    )

    flow = Flow(
        name="alias-chain",
        nodes=[
            {"id": "t", "type": "trigger"},
            {"id": "r", "type": "reaper", "card_id": "card-1"},
            {"id": "o", "type": "output"},
        ],
        edges=[
            {"from_node": "t", "from_port": "start", "to_node": "r", "to_port": "in"},
            {"from_node": "r", "from_port": "out", "to_node": "o", "to_port": "in"},
        ],
    )
    await store.insert_flow(flow)

    ex = FlowExecutor(store)
    run = await ex.dispatch(flow)
    task = ex._active.get(run.id)
    assert task is not None
    await task

    refreshed = await store.get_flow_run(run.id)
    assert refreshed is not None
    assert refreshed.state == FlowState.FINISHED
    assert refreshed.node_outputs.get("r") == "alias reply"
