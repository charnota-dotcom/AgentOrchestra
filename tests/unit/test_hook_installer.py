"""Claude hook pack installer."""

from __future__ import annotations

import json
from pathlib import Path

from apps.service.ingestion import hook_installer


def test_status_handles_missing_file(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    s = hook_installer.status(settings_path=settings)
    assert s["installed"] is False
    assert all(v is False for v in s["events"].values())


def test_install_creates_file_and_writes_entries(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    plan = hook_installer.install(
        service_url="http://127.0.0.1:8765",
        settings_path=settings,
        script_path=script,
    )
    assert plan.settings_path == settings
    assert settings.exists()
    data = json.loads(settings.read_text())
    for ev in hook_installer.HOOK_EVENTS:
        assert any(e.get("tag") == hook_installer.TAG for e in data["hooks"][ev])


def test_install_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    hook_installer.install(
        service_url="http://x",
        settings_path=settings,
        script_path=script,
    )
    hook_installer.install(
        service_url="http://x",
        settings_path=settings,
        script_path=script,
    )
    data = json.loads(settings.read_text())
    for ev in hook_installer.HOOK_EVENTS:
        ours = [e for e in data["hooks"][ev] if e.get("tag") == hook_installer.TAG]
        assert len(ours) == 1


def test_uninstall_removes_only_our_entries(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"type": "command", "command": "/usr/bin/true"}],
                }
            }
        )
    )
    hook_installer.install(
        service_url="http://x",
        settings_path=settings,
        script_path=script,
    )
    hook_installer.uninstall(settings_path=settings)
    data = json.loads(settings.read_text())
    # User's foreign hook is preserved.
    assert data["hooks"]["SessionStart"] == [{"type": "command", "command": "/usr/bin/true"}]


def test_install_missing_script_raises(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    try:
        hook_installer.install(
            service_url="http://x",
            settings_path=settings,
            script_path=tmp_path / "nope.sh",
        )
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")
