"""Claude Code hook pack installer.

Edits ``~/.claude/settings.json`` (creating it if missing) so Claude
Code invokes the bundled hook script for the events we care about.
The installer is idempotent: re-running adds the entry only if it
isn't already present, and uninstalling cleanly strips just our
entries without disturbing the user's other hooks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.service.secrets.keyring_store import hook_token

log = logging.getLogger(__name__)


HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop", "SubagentStop")
TAG = "agentorchestra"


@dataclass(frozen=True)
class HookPlan:
    settings_path: Path
    script_path: Path
    service_url: str
    token: str


def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def default_script_path() -> Path:
    # Resolve the bundled hook in packs/hooks.
    here = Path(__file__).resolve()
    return here.parents[3] / "packs" / "hooks" / "agentorchestra-hook.sh"


def status(settings_path: Path | None = None) -> dict[str, Any]:
    """Inspect settings.json without modifying it.  Returns whether each
    event currently has an agentorchestra entry.
    """
    path = settings_path or default_settings_path()
    if not path.exists():
        return {"installed": False, "events": {ev: False for ev in HOOK_EVENTS}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {
            "installed": False,
            "events": {ev: False for ev in HOOK_EVENTS},
            "error": "settings.json is not valid JSON",
        }
    hooks = data.get("hooks") or {}
    out: dict[str, bool] = {}
    any_installed = False
    for ev in HOOK_EVENTS:
        entries = hooks.get(ev) or []
        present = any(_is_ours(e) for e in entries)
        out[ev] = present
        if present:
            any_installed = True
    return {"installed": any_installed, "events": out}


def install(
    *,
    service_url: str,
    settings_path: Path | None = None,
    script_path: Path | None = None,
) -> HookPlan:
    """Install the hook entry for every Claude Code event we listen to.

    Idempotent.  Returns the resolved HookPlan including the script
    location and the token the script will pass back.
    """
    sp = settings_path or default_settings_path()
    script = script_path or default_script_path()
    if not script.exists():
        raise FileNotFoundError(f"hook script missing at {script}")
    sp.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if sp.exists():
        try:
            data = json.loads(sp.read_text())
        except json.JSONDecodeError:
            log.warning("settings.json is invalid JSON; keeping a backup")
            sp.with_suffix(".json.bak").write_text(sp.read_text())
            data = {}

    hooks = data.setdefault("hooks", {})
    token = hook_token()
    entry = {
        "type": "command",
        "command": str(script),
        "tag": TAG,
        "env": {
            "AGENTORCHESTRA_URL": service_url,
            "AGENTORCHESTRA_TOKEN": token,
        },
    }
    for ev in HOOK_EVENTS:
        existing = hooks.get(ev) or []
        existing = [e for e in existing if not _is_ours(e)]
        existing.append(dict(entry))
        hooks[ev] = existing

    sp.write_text(json.dumps(data, indent=2) + "\n")
    return HookPlan(settings_path=sp, script_path=script, service_url=service_url, token=token)


def uninstall(settings_path: Path | None = None) -> int:
    """Strip every agentorchestra entry from settings.json.  Returns the
    number of entries removed.
    """
    sp = settings_path or default_settings_path()
    if not sp.exists():
        return 0
    try:
        data = json.loads(sp.read_text())
    except json.JSONDecodeError:
        return 0
    hooks = data.get("hooks") or {}
    removed = 0
    for ev in HOOK_EVENTS:
        before = hooks.get(ev) or []
        after = [e for e in before if not _is_ours(e)]
        removed += len(before) - len(after)
        if after:
            hooks[ev] = after
        elif ev in hooks:
            del hooks[ev]
    if hooks:
        data["hooks"] = hooks
    elif "hooks" in data:
        del data["hooks"]
    sp.write_text(json.dumps(data, indent=2) + "\n")
    return removed


def _is_ours(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return entry.get("tag") == TAG
