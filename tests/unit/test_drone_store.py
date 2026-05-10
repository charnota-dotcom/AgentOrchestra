"""Tests for DroneBlueprint + DroneAction CRUD on the EventStore.

See docs/DRONE_MODEL.md for the design.  These tests pin the schema
contract: round-trip of every field, role hydration, JSON columns
parsed back into list[str] / dict, optimistic-concurrency on
update_blueprint, refusal of blueprint delete when actions linked.
"""

from __future__ import annotations

import pytest

from apps.service.store.events import EventStore
from apps.service.types import (
    BlueprintVersionConflict,
    DroneAction,
    DroneBlueprint,
    DroneRole,
    Workspace,
)


def _bp(**kw) -> DroneBlueprint:
    """Builder for a minimal valid blueprint with sensible defaults."""
    return DroneBlueprint(
        name=kw.pop("name", "Test blueprint"),
        provider=kw.pop("provider", "claude-cli"),
        model=kw.pop("model", "claude-sonnet-4-6"),
        **kw,
    )


@pytest.mark.asyncio
async def test_insert_and_get_blueprint_round_trip(store: EventStore) -> None:
    bp = _bp(
        description="reviewer",
        role=DroneRole.SUPERVISOR,
        system_persona="You are a code reviewer.",
        skills=["/research-deep", "/cite-sources"],
        reference_blueprint_ids=["bp-design"],
    )
    inserted = await store.insert_drone_blueprint(bp)
    assert inserted.id == bp.id

    fetched = await store.get_drone_blueprint(bp.id)
    assert fetched is not None
    assert fetched.name == "Test blueprint"
    assert fetched.role == DroneRole.SUPERVISOR
    assert fetched.skills == ["/research-deep", "/cite-sources"]
    assert fetched.reference_blueprint_ids == ["bp-design"]
    assert fetched.system_persona == "You are a code reviewer."
    assert fetched.version == 1


@pytest.mark.asyncio
async def test_list_blueprints_orders_by_updated_at_desc(store: EventStore) -> None:
    a = await store.insert_drone_blueprint(_bp(name="A"))
    await store.insert_drone_blueprint(_bp(name="B"))
    # Touch A so it becomes most recent.
    a.system_persona = "edited"
    await store.update_drone_blueprint(a)

    rows = await store.list_drone_blueprints()
    names = [r.name for r in rows]
    assert names[0] == "A"
    assert names[1] == "B"


@pytest.mark.asyncio
async def test_update_blueprint_bumps_version_and_persists(store: EventStore) -> None:
    bp = await store.insert_drone_blueprint(_bp())
    bp.system_persona = "v2"
    bp.skills = ["/a"]
    updated = await store.update_drone_blueprint(bp)
    assert updated.version == 2
    again = await store.get_drone_blueprint(bp.id)
    assert again is not None
    assert again.version == 2
    assert again.system_persona == "v2"
    assert again.skills == ["/a"]


@pytest.mark.asyncio
async def test_update_blueprint_optimistic_conflict(store: EventStore) -> None:
    bp = await store.insert_drone_blueprint(_bp())
    # Two writers fetch v1.  Writer one commits.
    bp.system_persona = "writer one"
    await store.update_drone_blueprint(bp, expected_version=1)
    # Writer two tries to commit with the now-stale v1 expectation.
    bp.system_persona = "writer two"
    with pytest.raises(BlueprintVersionConflict):
        await store.update_drone_blueprint(bp, expected_version=1)


@pytest.mark.asyncio
async def test_delete_blueprint_refuses_when_actions_linked(store: EventStore) -> None:
    bp = await store.insert_drone_blueprint(_bp())
    action = DroneAction(
        blueprint_id=bp.id,
        blueprint_snapshot=bp.model_dump(mode="json"),
    )
    await store.insert_drone_action(action)

    deleted = await store.delete_drone_blueprint(bp.id)
    assert deleted is False
    # Blueprint still there.
    assert await store.get_drone_blueprint(bp.id) is not None

    # Now delete the action, retry — should succeed.
    await store.delete_drone_action(action.id)
    deleted_again = await store.delete_drone_blueprint(bp.id)
    assert deleted_again is True
    assert await store.get_drone_blueprint(bp.id) is None


@pytest.mark.asyncio
async def test_action_round_trip_with_workspace(store: EventStore) -> None:
    bp = await store.insert_drone_blueprint(_bp(role=DroneRole.WORKER))
    ws = Workspace(name="proj", repo_path="/tmp/proj")
    await store.insert_workspace(ws)

    action = DroneAction(
        blueprint_id=bp.id,
        blueprint_snapshot=bp.model_dump(mode="json"),
        workspace_id=ws.id,
        additional_skills=["/oneoff"],
        additional_reference_action_ids=["other-action"],
        transcript=[{"role": "user", "content": "hi"}],
    )
    await store.insert_drone_action(action)

    fetched = await store.get_drone_action(action.id)
    assert fetched is not None
    assert fetched.workspace_id == ws.id
    assert fetched.additional_skills == ["/oneoff"]
    assert fetched.additional_reference_action_ids == ["other-action"]
    assert fetched.transcript == [{"role": "user", "content": "hi"}]
    # effective_skills includes blueprint defaults + action additions.
    bp_with_skills = _bp(skills=["/from-bp"])
    bp2 = await store.insert_drone_blueprint(bp_with_skills)
    a2 = DroneAction(
        blueprint_id=bp2.id,
        blueprint_snapshot=bp2.model_dump(mode="json"),
        additional_skills=["/from-action"],
    )
    await store.insert_drone_action(a2)
    f2 = await store.get_drone_action(a2.id)
    assert f2 is not None
    assert f2.effective_skills == ["/from-bp", "/from-action"]


@pytest.mark.asyncio
async def test_action_role_inherits_from_snapshot(store: EventStore) -> None:
    bp = await store.insert_drone_blueprint(_bp(role=DroneRole.AUDITOR))
    action = DroneAction(
        blueprint_id=bp.id,
        blueprint_snapshot=bp.model_dump(mode="json"),
    )
    await store.insert_drone_action(action)
    fetched = await store.get_drone_action(action.id)
    assert fetched is not None
    assert fetched.effective_role == DroneRole.AUDITOR


@pytest.mark.asyncio
async def test_action_role_falls_back_to_worker_on_garbage_snapshot(
    store: EventStore,
) -> None:
    """Defence in depth: a malformed / future-format snapshot must not
    silently grant SUPERVISOR.  Default to WORKER (most-restricted).
    """
    bp = await store.insert_drone_blueprint(_bp())
    action = DroneAction(
        blueprint_id=bp.id,
        blueprint_snapshot={"role": "totally-invalid-role"},
    )
    await store.insert_drone_action(action)
    fetched = await store.get_drone_action(action.id)
    assert fetched is not None
    assert fetched.effective_role == DroneRole.WORKER


@pytest.mark.asyncio
async def test_list_actions_filter_by_blueprint(store: EventStore) -> None:
    bp1 = await store.insert_drone_blueprint(_bp(name="bp1"))
    bp2 = await store.insert_drone_blueprint(_bp(name="bp2"))
    for _ in range(3):
        await store.insert_drone_action(
            DroneAction(blueprint_id=bp1.id, blueprint_snapshot=bp1.model_dump(mode="json"))
        )
    await store.insert_drone_action(
        DroneAction(blueprint_id=bp2.id, blueprint_snapshot=bp2.model_dump(mode="json"))
    )

    rows1 = await store.list_drone_actions(blueprint_id=bp1.id)
    rows2 = await store.list_drone_actions(blueprint_id=bp2.id)
    rows_all = await store.list_drone_actions()
    assert len(rows1) == 3
    assert len(rows2) == 1
    assert len(rows_all) == 4


@pytest.mark.asyncio
async def test_count_actions_for_blueprint(store: EventStore) -> None:
    bp = await store.insert_drone_blueprint(_bp())
    assert await store.count_actions_for_blueprint(bp.id) == 0
    for _ in range(5):
        await store.insert_drone_action(
            DroneAction(blueprint_id=bp.id, blueprint_snapshot=bp.model_dump(mode="json"))
        )
    assert await store.count_actions_for_blueprint(bp.id) == 5


@pytest.mark.asyncio
async def test_drone_role_enum_has_four_canonical_values() -> None:
    """Belt-and-braces: roles are closed-set in v1.  Adding one is a
    deliberate operator request, not a typo.  This test fails on
    accidental enum drift.
    """
    assert {r.value for r in DroneRole} == {"worker", "supervisor", "courier", "auditor"}
