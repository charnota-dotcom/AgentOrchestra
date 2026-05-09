"""SQLite event store smoke tests."""

from __future__ import annotations

import pytest

from apps.service.types import (
    Artifact,
    ArtifactKind,
    Event,
    EventKind,
    EventSource,
    Run,
    RunState,
    Workspace,
    long_id,
)


@pytest.mark.asyncio
async def test_open_creates_schema(store) -> None:
    cur = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    )
    row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_event_seq_assigns_monotonically(store) -> None:
    e1 = await store.append_event(
        Event(source=EventSource.SYSTEM, kind=EventKind.SERVICE_STARTED, text="a")
    )
    e2 = await store.append_event(
        Event(source=EventSource.SYSTEM, kind=EventKind.SERVICE_STARTED, text="b")
    )
    assert e2.seq == e1.seq + 1


@pytest.mark.asyncio
async def test_fts_search(store) -> None:
    ws = await store.insert_workspace(Workspace(name="w", repo_path="/tmp/never-used"))
    art = Artifact(
        id=long_id(), run_id="r1", kind=ArtifactKind.SUMMARY,
        title="Anthropic SDK overview", body="Python client for Claude.",
    )
    await store.insert_artifact(art)
    hits = await store.search("anthropic")
    assert any("anthropic" in h["title"].lower() for h in hits)


@pytest.mark.asyncio
async def test_run_round_trip(store) -> None:
    ws = await store.insert_workspace(Workspace(name="w", repo_path="/tmp/never-used"))
    # We need a card and instruction — use minimal stubs by inserting raw rows.
    await store.db.execute(
        "INSERT INTO templates VALUES (?,?,?,?,?,?,?,?)",
        ("t", "T", "demo", "body", "[]", 1, "h", "2026-01-01T00:00:00+00:00"),
    )
    await store.db.execute(
        """INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("c", "C", "demo", "d", "t", "anthropic", "claude-sonnet-4-5",
         "{}", "{}", "devcontainer", "[]", 60, 50, 0, 1,
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    await store.db.execute(
        "INSERT INTO instructions VALUES (?,?,?,?,?,?,?)",
        ("i", "t", 1, "c", "rendered", "{}", "2026-01-01T00:00:00+00:00"),
    )
    await store.db.commit()

    run = Run(workspace_id=ws.id, card_id="c", instruction_id="i")
    await store.insert_run(run)
    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.state is RunState.QUEUED

    await store.update_run_state(run.id, RunState.PLANNING)
    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.state is RunState.PLANNING
