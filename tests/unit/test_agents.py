"""Agents store + follow-up presets."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from apps.service.agents import FOLLOWUP_PRESETS, followup_instruction
from apps.service.store.events import EventStore
from apps.service.types import Agent


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[EventStore]:
    s = EventStore(tmp_path / "a.sqlite")
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_agent_round_trip(store: EventStore) -> None:
    a = Agent(name="Smith", provider="claude-cli", model="claude-sonnet-4-6")
    await store.insert_agent(a)
    fetched = await store.get_agent(a.id)
    assert fetched is not None
    assert fetched.name == "Smith"
    assert fetched.transcript == []


@pytest.mark.asyncio
async def test_agent_update_persists_transcript(store: EventStore) -> None:
    a = Agent(name="Smith", provider="claude-cli", model="x")
    await store.insert_agent(a)
    a.transcript.append({"role": "user", "content": "hi"})
    a.transcript.append({"role": "assistant", "content": "hello"})
    await store.update_agent(a)
    fetched = await store.get_agent(a.id)
    assert fetched is not None
    assert len(fetched.transcript) == 2
    assert fetched.transcript[0]["content"] == "hi"


@pytest.mark.asyncio
async def test_agent_list_orders_recent_first(store: EventStore) -> None:
    older = Agent(name="A", provider="claude-cli", model="x")
    newer = Agent(name="B", provider="claude-cli", model="x")
    await store.insert_agent(older)
    await store.insert_agent(newer)
    # Touch B so its updated_at advances.
    newer.transcript.append({"role": "user", "content": "ping"})
    await store.update_agent(newer)
    listed = await store.list_agents()
    assert listed[0].name == "B"


@pytest.mark.asyncio
async def test_agent_delete_detaches_children(store: EventStore) -> None:
    parent = Agent(name="P", provider="claude-cli", model="x")
    await store.insert_agent(parent)
    child = Agent(
        name="C",
        provider="claude-cli",
        model="x",
        parent_id=parent.id,
        parent_name=parent.name,
    )
    await store.insert_agent(child)
    assert await store.delete_agent(parent.id) is True
    refreshed = await store.get_agent(child.id)
    assert refreshed is not None
    # Child survives but its parent_id is null.
    assert refreshed.parent_id is None


def test_followup_presets_have_required_keys() -> None:
    expected_keys = {"summarise", "annotate", "deep_dive", "critique", "verify", "custom"}
    assert expected_keys <= set(FOLLOWUP_PRESETS.keys())


def test_followup_instruction_returns_preset_body() -> None:
    body = followup_instruction("summarise")
    assert "summarise" in body.lower()


def test_followup_instruction_uses_custom_text_when_custom() -> None:
    text = followup_instruction("custom", "do the thing")
    assert text == "do the thing"


def test_followup_instruction_returns_empty_for_empty_custom() -> None:
    assert followup_instruction("custom", "") == ""
