"""Round-3 audit tests — what's left after the Agent rip-out.

Originally housed Agent / Attachment cross-row safety tests; those
disappeared with the rip-out (see docs/DRONE_MODEL.md).  Kept the
flow-version-conflict + workspace-delete tests because they cover
behaviour not exercised elsewhere.
"""

from __future__ import annotations

import pytest

from apps.service.store.events import EventStore
from apps.service.types import Flow, FlowVersionConflict, Workspace


@pytest.mark.asyncio
async def test_update_flow_version_conflict(store: EventStore) -> None:
    flow = Flow(name="Test", description="x")
    await store.insert_flow(flow)
    # First update with the version we last saw — succeeds.
    flow.description = "y"
    await store.update_flow(flow, expected_version=flow.version)
    # Second update with the same (now stale) expected_version — conflict.
    flow.description = "z"
    with pytest.raises(FlowVersionConflict):
        await store.update_flow(flow, expected_version=flow.version)


@pytest.mark.asyncio
async def test_delete_workspace_returns_bool(store: EventStore) -> None:
    ws = Workspace(name="A", repo_path="/tmp/A")
    await store.insert_workspace(ws)
    assert await store.delete_workspace(ws.id) is True
    # Second delete — already gone — returns False without raising.
    assert await store.delete_workspace(ws.id) is False
