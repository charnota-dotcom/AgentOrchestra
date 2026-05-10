"""Helper-level tests for ``apps.gui.windows.drones`` and ``drones.send``.

The repo has no pytest-qt, so we only test the pure functions and the
RPC layer here.  Widget-level smoke is manual.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def handlers(store, tmp_path):
    """Local copy of the fixture from ``test_drone_rpcs.py`` — wires the
    real ``Handlers`` against the test ``EventStore``.  Duplicated
    rather than promoted to ``conftest`` so each PR's tests stay
    self-contained.
    """
    from apps.service.main import Handlers

    return Handlers(
        store=store,
        manager=SimpleNamespace(),
        dispatcher=SimpleNamespace(),
        data_dir=tmp_path,
    )


def test_render_transcript_html_empty() -> None:
    pytest.importorskip("PySide6")
    from apps.gui.windows.drones import _render_transcript_html

    out = _render_transcript_html([])
    assert "no messages yet" in out


def test_render_transcript_html_user_and_assistant_blocks() -> None:
    pytest.importorskip("PySide6")
    from apps.gui.windows.drones import _render_transcript_html

    out = _render_transcript_html(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
    )
    # Both blocks appear, roles labelled.
    assert ">You<" in out
    assert ">Drone<" in out
    assert "hello" in out
    assert "world" in out


def test_render_transcript_html_escapes_html_in_content() -> None:
    pytest.importorskip("PySide6")
    from apps.gui.windows.drones import _render_transcript_html

    out = _render_transcript_html([{"role": "user", "content": "<script>alert(1)</script>"}])
    # The dangerous tag must NOT survive verbatim — escaped so the
    # QTextEdit renders it as text rather than executing markup.
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


# ---------------------------------------------------------------------------
# drones.send RPC — integration with the test EventStore.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drones_send_rejects_action_without_provider_in_snapshot(
    handlers,
) -> None:
    """A hand-crafted action with an empty / partial blueprint snapshot
    must refuse rather than crash the provider lookup.
    """
    from apps.service.types import DroneAction

    bad = DroneAction(blueprint_id="bp-fake", blueprint_snapshot={"name": "broken"})
    await handlers.store.insert_drone_action(bad)
    with pytest.raises(ValueError, match="no provider/model in snapshot"):
        await handlers.drones_send({"action_id": bad.id, "message": "hi"})


@pytest.mark.asyncio
async def test_drones_send_rejects_unknown_action(handlers) -> None:
    with pytest.raises(ValueError, match="unknown action"):
        await handlers.drones_send({"action_id": "no-such-id", "message": "hi"})
