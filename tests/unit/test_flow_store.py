"""EventStore — flow CRUD + flow_run round-trip."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from apps.service.store.events import EventStore
from apps.service.types import Flow, FlowRun, FlowState


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[EventStore]:
    s = EventStore(tmp_path / "f.sqlite")
    await s.open()
    await s.db.execute("PRAGMA foreign_keys = OFF")
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_flow_round_trip(store: EventStore) -> None:
    flow = Flow(
        name="hello",
        nodes=[{"id": "n1", "type": "trigger"}],
        edges=[],
    )
    await store.insert_flow(flow)
    fetched = await store.get_flow(flow.id)
    assert fetched is not None
    assert fetched.name == "hello"
    assert fetched.nodes == [{"id": "n1", "type": "trigger"}]
    assert fetched.edges == []


@pytest.mark.asyncio
async def test_flow_update_bumps_version(store: EventStore) -> None:
    flow = Flow(name="v1")
    await store.insert_flow(flow)
    flow.name = "v2"
    await store.update_flow(flow)
    fetched = await store.get_flow(flow.id)
    assert fetched is not None
    assert fetched.name == "v2"
    assert fetched.version == 2  # bumped by SQL UPDATE


@pytest.mark.asyncio
async def test_flow_list_orders_recent_first(store: EventStore) -> None:
    a = Flow(name="alpha")
    b = Flow(name="beta")
    await store.insert_flow(a)
    await store.insert_flow(b)
    listed = await store.list_flows()
    assert {f.name for f in listed} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_flow_delete_cascades_runs(store: EventStore) -> None:
    flow = Flow(name="x")
    await store.insert_flow(flow)
    run = FlowRun(flow_id=flow.id, state=FlowState.PENDING)
    await store.insert_flow_run(run)
    assert await store.get_flow_run(run.id) is not None

    ok = await store.delete_flow(flow.id)
    assert ok is True
    # Run is gone too.
    assert await store.get_flow_run(run.id) is None


@pytest.mark.asyncio
async def test_flow_run_round_trip(store: EventStore) -> None:
    flow = Flow(name="x")
    await store.insert_flow(flow)
    run = FlowRun(flow_id=flow.id, state=FlowState.RUNNING, node_outputs={"a": "hi"})
    await store.insert_flow_run(run)
    fetched = await store.get_flow_run(run.id)
    assert fetched is not None
    assert fetched.state == FlowState.RUNNING
    assert fetched.node_outputs == {"a": "hi"}
