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
from typing import TYPE_CHECKING

from apps.service.secrets.keyring_store import hook_token

if TYPE_CHECKING:  # pragma: no cover
    pass


log = logging.getLogger(__name__)


def _import_qt() -> tuple:
    """Import PySide6 lazily so non-GUI code paths don't pull it in."""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]
        import qasync  # type: ignore[import-not-found]
        return QtCore, QtGui, QtWidgets, qasync
    except ImportError as exc:
        raise SystemExit(
            "GUI deps missing; install with `pip install agentorchestra[gui]`"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentorchestra-gui")
    parser.add_argument("--service-url", default="http://127.0.0.1:8765")
    parser.add_argument("--token", default=None,
                        help="RPC token; falls back to keyring lookup")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    QtCore, QtGui, QtWidgets, qasync = _import_qt()
    from apps.gui.ipc.client import RpcClient  # local import (qasync after Qt)
    from apps.gui.windows.main_window import MainWindow

    token = args.token or hook_token()
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("AgentOrchestra")
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    client = RpcClient(base_url=args.service_url, token=token)
    window = MainWindow(client=client)
    window.show()

    with loop:
        return loop.run_forever()


if __name__ == "__main__":
    sys.exit(main())
