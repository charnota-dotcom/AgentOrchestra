"""Main window.

A QMainWindow with a left-side rail of section buttons and a stacked
widget for the main content.  Each section is a self-contained widget
in `apps/gui/windows/`.

We avoid importing PySide6 at module load so the unit-test path doesn't
need Qt.  The class is constructed lazily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.windows.composer import ComposerPage
from apps.gui.windows.history import HistoryPage
from apps.gui.windows.home import HomePage
from apps.gui.windows.settings import SettingsPage

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, client: "RpcClient") -> None:
        super().__init__()
        self.client = client
        self.setWindowTitle("AgentOrchestra")
        self.resize(1280, 820)

        central = QtWidgets.QWidget(self)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rail = self._build_rail()
        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(rail)
        root.addWidget(self.stack, stretch=1)

        # Pages
        self.home = HomePage(self.client)
        self.composer = ComposerPage(self.client)
        self.history = HistoryPage(self.client)
        self.settings = SettingsPage(self.client)

        self.stack.addWidget(self.home)
        self.stack.addWidget(self.composer)
        self.stack.addWidget(self.history)
        self.stack.addWidget(self.settings)

        self.setCentralWidget(central)

        self._wire_navigation()
        self.stack.setCurrentIndex(0)

    def _build_rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QFrame()
        rail.setFixedWidth(220)
        rail.setStyleSheet("background:#1f2024;color:#e8e8ea;")

        layout = QtWidgets.QVBoxLayout(rail)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(4)

        title = QtWidgets.QLabel("AgentOrchestra")
        title.setStyleSheet("font-weight:600;font-size:15px;color:#fff;padding:6px 8px;")
        layout.addWidget(title)
        layout.addSpacing(12)

        self._nav_buttons: list[QtWidgets.QPushButton] = []
        for label in ("Home", "Compose", "History", "Settings"):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton{text-align:left;padding:8px 10px;border:none;"
                "background:transparent;color:#e8e8ea;border-radius:4px;}"
                "QPushButton:checked{background:#3b3d44;color:#fff;}"
                "QPushButton:hover{background:#2a2c31;}"
            )
            layout.addWidget(btn)
            self._nav_buttons.append(btn)

        layout.addStretch(1)

        status = QtWidgets.QLabel("Service: localhost:8765")
        status.setStyleSheet("color:#7a7d85;font-size:11px;padding:6px 8px;")
        layout.addWidget(status)
        return rail

    def _wire_navigation(self) -> None:
        for i, btn in enumerate(self._nav_buttons):
            btn.clicked.connect(lambda _checked=False, idx=i: self._switch_to(idx))
        self._nav_buttons[0].setChecked(True)

    def _switch_to(self, idx: int) -> None:
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)
