"""Annotator overlay wiring.

Optional integration with the ``pyside6_annotator`` library.  When the
package is installed, ``setup_annotator`` attaches an
``AnnotationManager`` and a ``FloatingAnnotationBar`` to the main
window so the operator can drop annotations on any widget and review
them later.  When the package is not installed, the function returns
``None`` and the GUI behaves as before.

Key contract notes pulled from
``TECHNICAL_INTEGRATION_REQUIREMENTS.md``:

* The data dir must exist before construction; ``_save_annotations``
  swallows I/O errors silently, so missing it loses data without a
  signal.
* The bar must be constructed with ``parent=None`` to survive
  QStackedWidget page swaps (the main window's content is exactly
  such a stack).
* We always pass an explicit ``action_log_path`` so action-log
  processing works on macOS / Linux too — the library only auto-
  derives a path on Windows + OneDrive.
* ``destroyed`` on the main window must close the bar so the
  application-level event filter is removed cleanly.
* QSettings org/app must differ from the library's own
  ``("pyside6_annotator", "annotator")`` to avoid stale-key collisions
  — handled in ``apps/gui/main.py`` via ``setApplicationName`` /
  ``setOrganizationName``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.gui.windows.main_window import MainWindow

log = logging.getLogger(__name__)


# Display name and slug used when computing the per-app action-log
# auto-path.  Keep the slug in sync with the value computed by the
# annotator's regex (``re.sub(r"[^\w\-]", "_", app_name.lower())``)
# so a future ``_pending_actions.json`` written by an external tool
# resolves to the same file.
APP_NAME = "AgentOrchestra"
APP_SLUG = "agentorchestra"


def _data_dir() -> Path:
    # Match the location the rest of the GUI uses for per-user state
    # (see ``apps.gui.windows.first_run.SENTINEL_PATH``).
    base = Path.home() / ".local" / "share" / "agentorchestra" / "annotations"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _action_log_path() -> Path:
    # Cross-platform explicit path.  The library would otherwise try
    # to write to ``~/OneDrive/Desktop/Annotation logs/<slug>.json``
    # on Windows and disable action logging entirely elsewhere.
    base = Path.home() / ".local" / "share" / "agentorchestra" / "action_logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{APP_SLUG}.json"


# Stack indices exposed by ``MainWindow``.  Keep this in sync with
# ``MainWindow.__init__`` if the page order ever changes.
_SCREEN_TO_STACK_INDEX: dict[str, int] = {
    "HomePage": 0,
    "ComposerPage": 1,
    "LivePage": 2,
    "ReviewPage": 3,
    "HistoryPage": 4,
    "SettingsPage": 5,
    "CanvasPage": 6,
    "ChatPage": 7,
    "AgentsPage": 8,
    "LimitsPage": 9,
}


def _make_navigate_to(window: MainWindow):
    def navigate_to(screen_name: str) -> bool:
        idx = _SCREEN_TO_STACK_INDEX.get(screen_name)
        if idx is None:
            return False
        # ``_switch_to`` updates the rail-button checked state and
        # changes the stack page atomically — exactly what the
        # annotator's Jump retry expects.
        window._switch_to(idx)
        return True

    return navigate_to


def _app_version() -> str:
    # Best-effort: read the installed metadata.  Fall back to a
    # placeholder rather than crashing — the version only affects
    # action-log entries.
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("agentorchestra")
        except PackageNotFoundError:
            return "0.0.0+dev"
    except Exception:  # pragma: no cover - defensive
        return "0.0.0+dev"


def setup_annotator(window: MainWindow) -> tuple[Any, Any] | None:
    """Attach the annotator overlay to ``window`` if the lib is present.

    Returns the ``(manager, bar)`` pair on success, ``None`` if the
    package is not installed or construction fails.  Failure is
    logged at WARNING level and never raised — the orchestrator GUI
    must keep working without the annotator.
    """
    try:
        from pyside6_annotator import (  # type: ignore[import-not-found]
            AnnotationManager,
            FloatingAnnotationBar,
        )
    except ImportError:
        log.info(
            "pyside6_annotator not installed; annotation overlay disabled "
            "(install with `pip install agentorchestra[gui]` to enable)"
        )
        return None

    try:
        data_dir = _data_dir()
        action_log_path = _action_log_path()
        manager = AnnotationManager(
            window=window,
            data_dir=data_dir,
            app_name=APP_NAME,
            app_version=_app_version(),
            action_log_path=action_log_path,
            navigate_to=_make_navigate_to(window),
        )
        bar = FloatingAnnotationBar(
            manager,
            data_dir=data_dir,
            host=window,
            parent=None,  # MUST be None — see integration requirements §5.
            settings_key=APP_SLUG,
        )
        window.destroyed.connect(bar.close)  # type: ignore[arg-type]
        log.info("annotation overlay attached (data_dir=%s)", data_dir)
        return manager, bar
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("failed to attach annotation overlay: %s", exc)
        return None
