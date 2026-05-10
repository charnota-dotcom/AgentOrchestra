"""Tests for attachment CRUD on the EventStore."""

from __future__ import annotations

import pytest

from apps.service.store.events import EventStore
from apps.service.types import Agent, Attachment, AttachmentKind


async def _make_agent(store: EventStore, name: str = "A") -> Agent:
    a = Agent(name=name, provider="claude-cli", model="sonnet")
    return await store.insert_agent(a)


@pytest.mark.asyncio
async def test_insert_and_get_attachment(store: EventStore, tmp_path) -> None:
    agent = await _make_agent(store)
    att = Attachment(
        agent_id=agent.id,
        kind=AttachmentKind.IMAGE,
        original_name="dog.png",
        stored_path=str(tmp_path / "dog.png"),
        mime_type="image/png",
        bytes=42,
    )
    inserted = await store.insert_attachment(att)
    assert inserted.id == att.id

    fetched = await store.get_attachment(att.id)
    assert fetched is not None
    assert fetched.kind == AttachmentKind.IMAGE
    assert fetched.original_name == "dog.png"


@pytest.mark.asyncio
async def test_list_attachments_per_agent(store: EventStore, tmp_path) -> None:
    a1 = await _make_agent(store, "A1")
    a2 = await _make_agent(store, "A2")
    for i in range(3):
        await store.insert_attachment(
            Attachment(
                agent_id=a1.id,
                kind=AttachmentKind.SPREADSHEET,
                original_name=f"s{i}.csv",
                stored_path=str(tmp_path / f"s{i}.csv"),
                mime_type="text/csv",
                bytes=10,
                rendered_text=f"# sheet {i}",
            )
        )
    await store.insert_attachment(
        Attachment(
            agent_id=a2.id,
            kind=AttachmentKind.IMAGE,
            original_name="other.png",
            stored_path=str(tmp_path / "other.png"),
            mime_type="image/png",
            bytes=20,
        )
    )

    rows1 = await store.list_attachments(a1.id)
    rows2 = await store.list_attachments(a2.id)
    assert len(rows1) == 3
    assert len(rows2) == 1
    assert rows2[0].kind == AttachmentKind.IMAGE


@pytest.mark.asyncio
async def test_get_attachments_by_ids_preserves_order(
    store: EventStore, tmp_path
) -> None:
    agent = await _make_agent(store)
    ids = []
    for i in range(3):
        att = await store.insert_attachment(
            Attachment(
                agent_id=agent.id,
                kind=AttachmentKind.SPREADSHEET,
                original_name=f"s{i}.csv",
                stored_path=str(tmp_path / f"s{i}.csv"),
                mime_type="text/csv",
                bytes=10,
            )
        )
        ids.append(att.id)
    # Reverse order in the lookup; output must follow the requested order.
    out = await store.get_attachments_by_ids(list(reversed(ids)))
    assert [a.id for a in out] == list(reversed(ids))


@pytest.mark.asyncio
async def test_delete_attachment_returns_row_then_gone(
    store: EventStore, tmp_path
) -> None:
    agent = await _make_agent(store)
    att = await store.insert_attachment(
        Attachment(
            agent_id=agent.id,
            kind=AttachmentKind.IMAGE,
            original_name="x.png",
            stored_path=str(tmp_path / "x.png"),
            mime_type="image/png",
            bytes=5,
        )
    )
    deleted = await store.delete_attachment(att.id)
    assert deleted is not None
    assert deleted.id == att.id
    # Second delete returns None.
    assert await store.delete_attachment(att.id) is None
    assert await store.get_attachment(att.id) is None


@pytest.mark.asyncio
async def test_update_attachment_turn_index(store: EventStore, tmp_path) -> None:
    agent = await _make_agent(store)
    att = await store.insert_attachment(
        Attachment(
            agent_id=agent.id,
            kind=AttachmentKind.IMAGE,
            original_name="t.png",
            stored_path=str(tmp_path / "t.png"),
            mime_type="image/png",
            bytes=5,
        )
    )
    assert att.turn_index == -1
    await store.update_attachment_turn(att.id, 7)
    fetched = await store.get_attachment(att.id)
    assert fetched is not None
    assert fetched.turn_index == 7
