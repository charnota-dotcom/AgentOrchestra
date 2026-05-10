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

from apps.gui.service_supervisor import ensure_service_running
from apps.service.secrets.keyring_store import hook_token

log = logging.getLogger(__name__)


def _import_qt() -> tuple:
    """Import PySide6 lazily so non-GUI code paths don't pull it in."""
    try:
        import qasync  # type: ignore[import-not-found]
        from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

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
        ensure_service_running(args.service_url)

    _qt_core, _qt_gui, qt_widgets, qasync = _import_qt()
    from apps.gui.ipc.client import RpcClient  # local import (qasync after Qt)
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
            loop.run_until_complete(client.aclose())
        except Exception:
            log.exception("RpcClient aclose failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
