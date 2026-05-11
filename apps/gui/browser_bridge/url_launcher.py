"""Cross-platform URL open + clipboard set.

Two operations bundled into one module:

* ``open_url(url)`` — open ``url`` in the operator's default browser.
  Wraps ``webbrowser.open``; auto-prepends ``https://`` when the URL
  has no scheme.  No-op for empty / whitespace-only input so callers
  don't need to guard.

* ``set_clipboard(text)`` — put ``text`` on the system clipboard.
  Uses ``QApplication.clipboard()`` so the call is consistent with
  the rest of the GUI and survives if the operator uses an exotic
  clipboard manager.

Pure stdlib + PySide6.  No ``apps.service`` imports, no
``apps.gui.ipc`` imports — keeps the sub-package testable in
isolation per the hard-import rule in the package README.
"""

from __future__ import annotations

import webbrowser

from PySide6 import QtWidgets


def open_url(url: str | None) -> bool:
    """Open ``url`` in the default browser.  Returns True on success,
    False if input was empty.  Never raises — a failed open is a
    soft failure (the operator can fall back to copying the URL
    manually).
    """
    if not url:
        return False
    cleaned = url.strip()
    if not cleaned:
        return False
    # webbrowser.open is happy with URLs that lack a scheme on most
    # platforms but the behaviour varies; normalise to https for
    # predictability.
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    try:
        webbrowser.open(cleaned, new=2)  # new=2 → new tab if possible
    except Exception:
        return False
    return True


def set_clipboard(text: str | None) -> bool:
    """Put ``text`` on the system clipboard.  Returns True on success.

    Empty / None input is a no-op (returns False).  Uses the running
    QApplication's clipboard — callers must be inside an active Qt
    event loop, which the GUI always is.
    """
    if not text:
        return False
    app = QtWidgets.QApplication.instance()
    if app is None:
        # Defensive: shouldn't fire in the live app but keeps the
        # helper usable from non-Qt callers (tests, scripts).
        return False
    clipboard = app.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(text)
    return True
