"""Tests for blueprints.* + drones.* RPC handlers and the
``_check_drone_authority`` matrix.

Two layers:

1. Pure-function tests for ``_check_drone_authority`` — pin every cell
   of the role/op/scope matrix in docs/DRONE_MODEL.md.
2. Handler tests — instantiate a real ``Handlers`` against the test
   ``EventStore`` fixture and exercise the RPC surface end-to-end at
   the handler boundary.  Stubs out ``manager``/``dispatcher`` since
   the new methods don't touch them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.service.main import Handlers, _check_drone_authority
from apps.service.store.events import EventStore
from apps.service.types import DroneRole, Workspace

# ---------------------------------------------------------------------------
# Authority matrix (pure-function)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["append_reference", "append_skill", "append_attachment"])
def test_authority_auditor_denied_even_on_self(op: str) -> None:
    with pytest.raises(PermissionError):
        _check_drone_authority(DroneRole.AUDITOR, op, is_self=True)
    with pytest.raises(PermissionError):
        _check_drone_authority(DroneRole.AUDITOR, op, is_self=False)


@pytest.mark.parametrize("role", [DroneRole.WORKER, DroneRole.SUPERVISOR, DroneRole.COURIER])
@pytest.mark.parametrize("op", ["append_reference", "append_skill", "append_attachment"])
def test_authority_self_mutation_allowed_for_non_auditor(role: DroneRole, op: str) -> None:
    # Should not raise.
    _check_drone_authority(role, op, is_self=True)


@pytest.mark.parametrize("op", ["append_reference", "append_skill", "append_attachment"])
def test_authority_worker_denied_cross_action(op: str) -> None:
    with pytest.raises(PermissionError):
        _check_drone_authority(DroneRole.WORKER, op, is_self=False)


def test_authority_courier_allowed_cross_reference_only() -> None:
    _check_drone_authority(DroneRole.COURIER, "append_reference", is_self=False)
    with pytest.raises(PermissionError):
        _check_drone_authority(DroneRole.COURIER, "append_skill", is_self=False)
    with pytest.raises(PermissionError):
        _check_drone_authority(DroneRole.COURIER, "append_attachment", is_self=False)


@pytest.mark.parametrize("op", ["append_reference", "append_skill", "append_attachment"])
def test_authority_supervisor_allowed_any_cross(op: str) -> None:
    _check_drone_authority(DroneRole.SUPERVISOR, op, is_self=False)


def test_authority_unknown_op_raises() -> None:
    with pytest.raises(ValueError):
        _check_drone_authority(DroneRole.SUPERVISOR, "delete_action", is_self=True)


# ---------------------------------------------------------------------------
# Handler fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def handlers(store: EventStore, tmp_path) -> Handlers:
    """Real ``Handlers`` wired to the test store.

    The blueprint / drone RPCs don't touch ``manager`` or
    ``dispatcher``, so we hand them a ``SimpleNamespace`` stub.
    ``data_dir`` gets a per-test tmp so attachments code (untouched
    here, but inited in __init__) doesn't pollute ~/.local.
    """
    return Handlers(
        store=store,
        manager=SimpleNamespace(),  # type: ignore[arg-type]
        dispatcher=SimpleNamespace(),  # type: ignore[arg-type]
        data_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# blueprints.* RPCs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blueprints_create_then_list_then_get(handlers: Handlers) -> None:
    out = await handlers.blueprints_create(
        {
            "name": "Reviewer",
            "provider": "claude-cli",
            "model": "claude-sonnet-4-6",
            "role": "supervisor",
            "skills": ["/research-deep"],
        }
    )
    assert out["name"] == "Reviewer"
    assert out["role"] == "supervisor"
    assert out["skills"] == ["/research-deep"]
    assert out["version"] == 1

    listed = await handlers.blueprints_list({})
    assert any(r["id"] == out["id"] for r in listed)

    fetched = await handlers.blueprints_get({"id": out["id"]})
    assert fetched["id"] == out["id"]


@pytest.mark.asyncio
async def test_blueprints_create_rejects_unknown_role(handlers: Handlers) -> None:
    with pytest.raises(ValueError):
        await handlers.blueprints_create(
            {
                "name": "X",
                "provider": "claude-cli",
                "model": "x",
                "role": "overlord",
            }
        )


@pytest.mark.asyncio
async def test_blueprints_update_bumps_version_and_persists(handlers: Handlers) -> None:
    out = await handlers.blueprints_create({"name": "A", "provider": "claude-cli", "model": "x"})
    updated = await handlers.blueprints_update(
        {
            "id": out["id"],
            "system_persona": "v2",
            "skills": ["/a"],
        }
    )
    assert updated["version"] == 2
    assert updated["system_persona"] == "v2"
    assert updated["skills"] == ["/a"]


@pytest.mark.asyncio
async def test_blueprints_update_optimistic_conflict_surfaces_as_value_error(
    handlers: Handlers,
) -> None:
    out = await handlers.blueprints_create({"name": "A", "provider": "claude-cli", "model": "x"})
    # First writer commits with expected_version=1 -> v2.
    await handlers.blueprints_update(
        {"id": out["id"], "system_persona": "first", "expected_version": 1}
    )
    # Second writer tries with the now-stale v1 expectation.
    with pytest.raises(ValueError):
        await handlers.blueprints_update(
            {"id": out["id"], "system_persona": "second", "expected_version": 1}
        )


@pytest.mark.asyncio
async def test_blueprints_delete_refuses_when_actions_linked(handlers: Handlers) -> None:
    bp = await handlers.blueprints_create({"name": "A", "provider": "claude-cli", "model": "x"})
    await handlers.drones_deploy({"blueprint_id": bp["id"]})
    out = await handlers.blueprints_delete({"id": bp["id"]})
    assert out == {"deleted": False, "linked_actions": 1}


# ---------------------------------------------------------------------------
# drones.* RPCs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drones_deploy_snapshots_blueprint_at_deploy_time(handlers: Handlers) -> None:
    bp = await handlers.blueprints_create(
        {
            "name": "A",
            "provider": "claude-cli",
            "model": "claude-sonnet-4-6",
            "system_persona": "original",
            "skills": ["/x"],
        }
    )
    action = await handlers.drones_deploy({"blueprint_id": bp["id"]})
    # Edit the blueprint AFTER deploy.
    await handlers.blueprints_update(
        {"id": bp["id"], "system_persona": "MUTATED", "skills": ["/y"]}
    )
    fetched = await handlers.drones_get({"id": action["id"]})
    # Snapshot is frozen.
    assert fetched["blueprint_snapshot"]["system_persona"] == "original"
    assert fetched["blueprint_snapshot"]["skills"] == ["/x"]


@pytest.mark.asyncio
async def test_drones_deploy_with_workspace(handlers: Handlers, store: EventStore) -> None:
    ws = Workspace(name="proj", repo_path="/tmp/proj")
    await store.insert_workspace(ws)
    bp = await handlers.blueprints_create({"name": "A", "provider": "claude-cli", "model": "x"})
    action = await handlers.drones_deploy(
        {
            "blueprint_id": bp["id"],
            "workspace_id": ws.id,
            "additional_skills": ["/oneoff"],
        }
    )
    assert action["workspace_id"] == ws.id
    assert action["additional_skills"] == ["/oneoff"]


@pytest.mark.asyncio
async def test_drones_list_filter_by_blueprint(handlers: Handlers) -> None:
    bp1 = await handlers.blueprints_create({"name": "1", "provider": "claude-cli", "model": "x"})
    bp2 = await handlers.blueprints_create({"name": "2", "provider": "claude-cli", "model": "x"})
    for _ in range(2):
        await handlers.drones_deploy({"blueprint_id": bp1["id"]})
    await handlers.drones_deploy({"blueprint_id": bp2["id"]})
    only_1 = await handlers.drones_list({"blueprint_id": bp1["id"]})
    only_2 = await handlers.drones_list({"blueprint_id": bp2["id"]})
    everything = await handlers.drones_list({})
    assert len(only_1) == 2
    assert len(only_2) == 1
    assert len(everything) == 3


@pytest.mark.asyncio
async def test_drones_append_reference_supervisor_can_target_peer(handlers: Handlers) -> None:
    sup_bp = await handlers.blueprints_create(
        {"name": "sup", "provider": "claude-cli", "model": "x", "role": "supervisor"}
    )
    wkr_bp = await handlers.blueprints_create(
        {"name": "wkr", "provider": "claude-cli", "model": "x", "role": "worker"}
    )
    sup = await handlers.drones_deploy({"blueprint_id": sup_bp["id"]})
    wkr = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})
    other = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})

    out = await handlers.drones_append_reference(
        {"actor_id": sup["id"], "target_id": wkr["id"], "reference_action_id": other["id"]}
    )
    assert other["id"] in out["additional_reference_action_ids"]


@pytest.mark.asyncio
async def test_drones_append_reference_worker_denied_cross(handlers: Handlers) -> None:
    wkr_bp = await handlers.blueprints_create(
        {"name": "wkr", "provider": "claude-cli", "model": "x", "role": "worker"}
    )
    actor = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})
    target = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})
    other = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})
    with pytest.raises(ValueError, match="worker drones cannot"):
        await handlers.drones_append_reference(
            {"actor_id": actor["id"], "target_id": target["id"], "reference_action_id": other["id"]}
        )


@pytest.mark.asyncio
async def test_drones_append_skill_courier_denied_cross(handlers: Handlers) -> None:
    cour_bp = await handlers.blueprints_create(
        {"name": "c", "provider": "claude-cli", "model": "x", "role": "courier"}
    )
    wkr_bp = await handlers.blueprints_create(
        {"name": "w", "provider": "claude-cli", "model": "x", "role": "worker"}
    )
    actor = await handlers.drones_deploy({"blueprint_id": cour_bp["id"]})
    target = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})
    with pytest.raises(ValueError, match="courier drones can only"):
        await handlers.drones_append_skill(
            {"actor_id": actor["id"], "target_id": target["id"], "skill": "/x"}
        )


@pytest.mark.asyncio
async def test_drones_append_skill_self_allowed_for_worker(handlers: Handlers) -> None:
    wkr_bp = await handlers.blueprints_create(
        {"name": "w", "provider": "claude-cli", "model": "x", "role": "worker"}
    )
    actor = await handlers.drones_deploy({"blueprint_id": wkr_bp["id"]})
    out = await handlers.drones_append_skill(
        {"actor_id": actor["id"], "target_id": actor["id"], "skill": "/oneoff"}
    )
    assert "/oneoff" in out["additional_skills"]


@pytest.mark.asyncio
async def test_drones_append_skill_auditor_denied_self(handlers: Handlers) -> None:
    aud_bp = await handlers.blueprints_create(
        {"name": "a", "provider": "claude-cli", "model": "x", "role": "auditor"}
    )
    actor = await handlers.drones_deploy({"blueprint_id": aud_bp["id"]})
    with pytest.raises(ValueError, match="auditor drones are read-only"):
        await handlers.drones_append_skill(
            {"actor_id": actor["id"], "target_id": actor["id"], "skill": "/x"}
        )


@pytest.mark.asyncio
async def test_drones_delete(handlers: Handlers) -> None:
    bp = await handlers.blueprints_create({"name": "A", "provider": "claude-cli", "model": "x"})
    action = await handlers.drones_deploy({"blueprint_id": bp["id"]})
    out = await handlers.drones_delete({"id": action["id"]})
    assert out == {"deleted": True}
    # Idempotent on missing id — second call returns deleted=False.
    out2 = await handlers.drones_delete({"id": action["id"]})
    assert out2 == {"deleted": False}
