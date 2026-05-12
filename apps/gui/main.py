"""GUI process entrypoint.

Starts the QApplication, hooks Qt's event loop into asyncio via qasync,
boots the RPC client, and shows the main window.

This is the V1 shell — it focuses on layout and navigation rather than
deep functionality.  Each tab is a placeholder widget that can be
replaced individually as the corresponding subsystem matures.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from apps.gui.service_supervisor import spawn_if_needed, wait_for_service
from apps.service.secrets.keyring_store import hook_token

log = logging.getLogger(__name__)


def _import_qt() -> tuple[Any, Any, Any, Any]:
    """Import PySide6 lazily so non-GUI code paths don't pull it in."""
    try:
        import os
        import PySide6
        import qasync
        from PySide6 import QtCore, QtGui, QtWidgets

        # Ensure Qt can find its plugins (Standardized workspace fix)
        plugin_path = os.path.join(os.path.dirname(PySide6.__file__), "plugins")
        QtCore.QCoreApplication.addLibraryPath(plugin_path)

        return QtCore, QtGui, QtWidgets, qasync
    except ImportError as exc:
        raise SystemExit(
            "GUI deps missing; install with `pip install agentorchestra[gui]`"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentorchestra-gui")
    parser.add_argument("--service-url", default="http://127.0.0.1:8765")
    parser.add_argument("--token", default=None, help="RPC token; falls back to keyring lookup")
    parser.add_argument(
        "--no-spawn-service",
        action="store_true",
        help="Don't auto-spawn the service if it's not already running",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.no_spawn_service:
        spawn_if_needed()
        if not wait_for_service(timeout=30.0):
            log.error("service failed to become ready within 30s")

    _qt_core, _qt_gui, qt_widgets, qasync = _import_qt()
    from apps.gui.ipc.client import RpcClient  # local import (qasync after Qt)
    from apps.gui.service_supervisor import reap
    from apps.gui.windows.main_window import MainWindow

    token = args.token or hook_token()
    app = qt_widgets.QApplication(sys.argv)
    # QSettings keys derive from these.  The pyside6_annotator library
    # uses ("pyside6_annotator", "annotator") internally; we pick a
    # different pair so its keys never collide with ours and stale
    # values from a previous host app aren't read back into ours.
    app.setOrganizationName("AgentOrchestra")
    app.setApplicationName("AgentOrchestra")
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    client = RpcClient(base_url=args.service_url, token=token)
    window = MainWindow(client=client)
    window.show()

    # Close the underlying httpx.AsyncClient cleanly on quit so we don't
    # leak the connection pool / TLS sockets after the GUI exits.
    # aboutToQuit fires while the loop is still running, so we can't
    # drive run_until_complete from there.  Instead, after run_forever
    # returns (QApp has quit, loop is stopped but not yet closed),
    # await aclose synchronously before __exit__ closes the loop.
    with loop:
        loop.run_forever()
        try:
            reap()
            loop.run_until_complete(client.aclose())
        except Exception:
            log.exception("Cleanup failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
