"""Browser-bridge sub-package.

Public exports — call sites should import from this module, not from
the implementation files directly.
"""

from __future__ import annotations

from apps.gui.browser_bridge.clipboard_listener import (
    ClipboardEvent,
    ClipboardListener,
)
from apps.gui.browser_bridge.clipboard_router import RouteDecision, route
from apps.gui.browser_bridge.dialog import BrowserBridgeDialog
from apps.gui.browser_bridge.handoff import HandoffFormat, render_handoff
from apps.gui.browser_bridge.url_launcher import open_url, set_clipboard

__all__ = [
    "BrowserBridgeDialog",
    "ClipboardEvent",
    "ClipboardListener",
    "HandoffFormat",
    "RouteDecision",
    "open_url",
    "render_handoff",
    "route",
    "set_clipboard",
]
