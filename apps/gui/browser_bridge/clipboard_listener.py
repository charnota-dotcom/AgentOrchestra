"""OS-level clipboard watcher with source-URL extraction.

Emits a ``ClipboardEvent`` whenever the operator copies text.  The
event carries:

* ``text`` — the plain-text contents
* ``source_url`` — the URL of the page the operator copied from, parsed
  from the ``SourceURL:`` header that Chrome / Edge / Firefox embed
  inside the ``CF_HTML`` (Windows) / ``text/html`` (Mac/Linux) clipboard
  format whenever you Ctrl+C inside a web page.  ``None`` for non-
  browser copies (Notepad, terminal, ...).
* ``source_title`` — page title where available (macOS extra)
* ``captured_at`` — when we observed the change

Implementation uses Qt's ``QClipboard.dataChanged`` signal, which is
push-based across all three OSes.  Qt's clipboard implementation
already polls / hooks the OS-native API under the hood; we layer the
SourceURL extraction on top.

Hard-import rule (per the sub-package README): no
``apps.service.*`` or ``apps.gui.ipc.*`` imports — pure stdlib +
PySide6.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from PySide6 import QtCore, QtWidgets


@dataclass
class ClipboardEvent:
    """One observed clipboard-content change."""

    text: str
    source_url: str | None = None
    source_title: str | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


_SOURCE_URL_RE = re.compile(
    r"^SourceURL:\s*(?P<url>\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_source_url(html_fragment: str | None) -> str | None:
    """Pull the ``SourceURL:`` header out of a Chrome-style HTML
    clipboard fragment.  Returns ``None`` if not present.

    Chrome / Edge / Firefox embed a 4-line header like:

        Version:1.0
        StartHTML:000000123
        EndHTML:000004567
        SourceURL:https://claude.ai/chat/abc-uuid

    ...before the actual HTML.  We just need the URL line.
    """
    if not html_fragment:
        return None
    match = _SOURCE_URL_RE.search(html_fragment)
    if not match:
        return None
    url = match.group("url").strip()
    return url or None


class ClipboardListener(QtCore.QObject):
    """Wraps Qt's clipboard with a typed callback API.

    Construct with ``ClipboardListener(callback)`` — callback is
    invoked with a fresh ``ClipboardEvent`` each time the operator
    copies new text.  Call ``start()`` to begin listening,
    ``stop()`` to stop.

    Multi-instance safe: each listener subscribes / unsubscribes
    independently.  The underlying Qt clipboard is global so all
    listeners fire on every change, which is what the router expects.
    """

    # Internal Qt signal so the callback can be wired up via Qt's
    # thread-affinity rules (the clipboard event arrives on the GUI
    # thread, where the callback will also run).
    _changed = QtCore.Signal(object)

    def __init__(
        self,
        on_event: Callable[[ClipboardEvent], None],
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_event = on_event
        self._wired = False
        self._changed.connect(self._dispatch)  # type: ignore[arg-type]

    def start(self) -> None:
        """Begin observing the system clipboard.  Idempotent."""
        if self._wired:
            return
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        clip = app.clipboard()
        if clip is None:
            return
        clip.dataChanged.connect(self._on_clipboard_changed)  # type: ignore[arg-type]
        self._wired = True

    def stop(self) -> None:
        """Stop observing.  Idempotent — safe to call repeatedly."""
        if not self._wired:
            return
        app = QtWidgets.QApplication.instance()
        if app is None:
            self._wired = False
            return
        clip = app.clipboard()
        if clip is None:
            self._wired = False
            return
        try:
            clip.dataChanged.disconnect(self._on_clipboard_changed)
        except (RuntimeError, TypeError):
            # Already disconnected — fine.
            pass
        self._wired = False

    def _on_clipboard_changed(self) -> None:
        """Qt clipboard signal handler — runs on the GUI thread."""
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        clip = app.clipboard()
        if clip is None:
            return
        mime = clip.mimeData()
        if mime is None:
            return
        text = mime.text() if mime.hasText() else ""
        if not text:
            # Empty copy / image-only copy → ignore.
            return
        html = mime.html() if mime.hasHtml() else None
        source_url = _parse_source_url(html)
        event = ClipboardEvent(text=text, source_url=source_url)
        self._changed.emit(event)

    def _dispatch(self, event: object) -> None:
        if not isinstance(event, ClipboardEvent):
            return
        try:
            self._on_event(event)
        except Exception:
            # A callback exception must not break the listener — the
            # operator can still paste manually.  Quietly continue.
            pass
