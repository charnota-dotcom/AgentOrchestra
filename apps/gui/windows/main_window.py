"""Main window.

A QMainWindow with a left-side rail of section buttons and a stacked
widget for the main content.  Each section is a self-contained widget
in `apps/gui/windows/`.

We avoid importing PySide6 at module load so the unit-test path doesn't
need Qt.  The class is constructed lazily.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets


from apps.gui.annotator import setup_annotator
from apps.gui.canvas.page import CanvasPage
from apps.gui.windows.blueprints import BlueprintsPage
from apps.gui.windows.composer import ComposerPage
from apps.gui.windows.analytics import AnalyticsPage
from apps.gui.windows.drones import DronesPage
from apps.gui.windows.first_run import FirstRunWizard, first_run_pending
from apps.gui.windows.history import HistoryPage
from apps.gui.windows.home import HomePage
from apps.gui.windows.limits import LimitsPage
from apps.gui.windows.live import LivePage
from apps.gui.windows.review import ReviewPage
from apps.gui.windows.settings import SettingsPage
from apps.gui.windows.skills import SkillsPage

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        from apps.gui.ipc.sse_client import SseClient
        self.sse = SseClient(base_url=client.base_url, token=client.token)

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
        self.live = LivePage(self.client)
        self.review = ReviewPage(self.client)
        self.history = HistoryPage(self.client)
        self.settings = SettingsPage(self.client)
        self.canvas = CanvasPage(self.client)
        self.analytics = AnalyticsPage(self.client)
        self.blueprints = BlueprintsPage(self.client)
        self.skills = SkillsPage(self.client)
        self.drones = DronesPage(self.client, sse=self.sse, provider_mode="manual")
        self.agents = DronesPage(self.client, sse=self.sse, provider_mode="autonomous")
        self.limits = LimitsPage(self.client)

        self.stack.addWidget(self.home)  # 0
        self.stack.addWidget(self.composer)  # 1
        self.stack.addWidget(self.live)  # 2
        self.stack.addWidget(self.review)  # 3
        self.stack.addWidget(self.history)  # 4
        self.stack.addWidget(self.settings)  # 5
        self.stack.addWidget(self.canvas)  # 6
        self.stack.addWidget(self.analytics)  # 7
        self.stack.addWidget(self.limits)  # 8
        self.stack.addWidget(self.blueprints)  # 9
        self.stack.addWidget(self.skills)  # 10
        self.stack.addWidget(self.drones)  # 11
        self.stack.addWidget(self.agents)  # 12

        self.setCentralWidget(central)

        # Cross-page navigation.
        self.composer.dispatched.connect(self._on_dispatched)
        self.live.review_requested.connect(self._on_review_requested)
        self.review.closed.connect(lambda: self._switch_to(0))

        self._wire_navigation()
        self.stack.setCurrentIndex(0)

        # Optional annotation overlay (pyside6_annotator).  No-ops if
        # the package isn't installed; never raises.
        self._annotator = setup_annotator(self)

        self._first_run_wizard_scheduled = False

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        # Defer the modal wizard until after the main window has had a
        # chance to paint at least once.  A 0 ms timer fires before
        # initial paint events flush, so the wizard would overlay an
        # un-painted (transparent / black) main-window rectangle.
        if not self._first_run_wizard_scheduled and first_run_pending():
            self._first_run_wizard_scheduled = True
            QtCore.QTimer.singleShot(80, self._show_first_run_wizard)

    def _show_first_run_wizard(self) -> None:
        wizard = FirstRunWizard(self.client, parent=self)
        wizard.exec()

    def _on_dispatched(self, run_id: str, card_name: str) -> None:
        self.live.attach_run(run_id, card_name=card_name)
        self._switch_to(2)

    def _on_review_requested(self, run_id: str) -> None:
        self.review.attach_run(run_id)
        self._switch_to(3)

    def _build_rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QFrame()
        # Bug: Fixed width of 220px prevented narrow window resizing.
        rail.setMinimumWidth(160)
        rail.setMaximumWidth(220)
        rail.setStyleSheet("background:#1f2024;color:#e8e8ea;")

        layout = QtWidgets.QVBoxLayout(rail)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(4)

        title = QtWidgets.QLabel("AgentOrchestra")
        title.setStyleSheet("font-weight:600;font-size:15px;color:#fff;padding:6px 8px;")
        layout.addWidget(title)
        layout.addSpacing(12)

        self._nav_buttons: list[QtWidgets.QPushButton] = []
        for label in (
            "Home",
            "Drones",
            "Agents",
            "Blueprints",
            "Skills",
            "Compose",
            "Canvas",
            "Analytics",
            "History",
            "Limits",
            "Settings",
        ):
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

        manual_btn = QtWidgets.QPushButton("User Manual 🚀")
        manual_btn.setStyleSheet(
            "QPushButton{text-align:left;padding:8px 10px;border:none;"
            "background:transparent;color:#f0c97a;border-radius:4px;font-size:12px;}"
            "QPushButton:hover{background:#2a2c31;}"
        )

        def _open_manual() -> None:
            path = Path(__file__).parent.parent.parent.parent / "docs" / "USER_MANUAL.md"
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.absolute())))

        manual_btn.clicked.connect(_open_manual)
        layout.addWidget(manual_btn)

        status = QtWidgets.QLabel("Service: localhost:8765")
        status.setStyleSheet("color:#7a7d85;font-size:11px;padding:6px 8px;")
        layout.addWidget(status)
        return rail

    # Map rail button index -> stack widget index.
    # Home → 0, Drones → 11, Agents → 12, Blueprints → 9, Skills → 10,
    # Compose → 1, Canvas → 6, Analytics → 7, History → 4, Limits → 8, Settings → 5
    _NAV_TO_STACK = {
        0: 0,
        1: 11,
        2: 12,
        3: 9,
        4: 10,
        5: 1,
        6: 6,
        7: 7,
        8: 4,
        9: 8,
        10: 5,
    }

    def _wire_navigation(self) -> None:
        for i, btn in enumerate(self._nav_buttons):
            btn.clicked.connect(
                lambda _checked=False, idx=i: self._switch_to(self._NAV_TO_STACK[idx])
            )
        self._nav_buttons[0].setChecked(True)

    def _switch_to(self, stack_idx: int) -> None:
        # Update rail-button checked state to match the new stack page.
        rail_idx = next((b for b, s in self._NAV_TO_STACK.items() if s == stack_idx), None)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == rail_idx)
        self.stack.setCurrentIndex(stack_idx)
