"""Home dashboard.

Shows running agents (top) and recent runs (bottom).  Currently a
read-only view powered by a single ``runs.list`` RPC call.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class HomePage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        header = QtWidgets.QLabel("Home")
        header.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        layout.addWidget(header)

        subtitle = QtWidgets.QLabel(
            "Active agents appear at the top.  Past runs are searchable in History."
        )
        subtitle.setStyleSheet("color:#5b6068;")
        layout.addWidget(subtitle)

        self.active_table = self._build_table(["Agent", "State", "Branch", "Cost", "Started"])
        layout.addWidget(self._section("Active", self.active_table))

        self.recent_table = self._build_table(["Run ID", "Card", "State", "Cost", "Created"])
        layout.addWidget(self._section("Recent", self.recent_table), stretch=1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._reload)  # type: ignore[arg-type]
        layout.addWidget(self.refresh_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        QtCore.QTimer.singleShot(0, self._reload)

    @staticmethod
    def _build_table(headers: list[str]) -> QtWidgets.QTableWidget:
        t = QtWidgets.QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setStretchLastSection(True)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        return t

    @staticmethod
    def _section(title: str, body: QtWidgets.QWidget) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(16, 12, 16, 16)
        v.setSpacing(10)
        h = QtWidgets.QLabel(title)
        h.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        v.addWidget(h)
        v.addWidget(body)
        return wrap

    def _reload(self) -> None:
        asyncio.ensure_future(self._reload_async())

    async def _reload_async(self) -> None:
        try:
            runs = await self.client.call("runs.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "RPC error", str(exc))
            return
        active = [
            r
            for r in runs
            if r["state"] in ("queued", "planning", "executing", "awaiting_approval")
        ]
        self._populate(
            self.active_table, active, ("id", "state", "branch_id", "cost_usd", "created_at")
        )
        self._populate(
            self.recent_table, runs[:50], ("id", "card_id", "state", "cost_usd", "created_at")
        )

    @staticmethod
    def _populate(table: QtWidgets.QTableWidget, rows: list[dict], cols: tuple[str, ...]) -> None:
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(cols):
                val = row.get(key, "")
                if isinstance(val, float):
                    val = f"${val:.2f}"
                item = QtWidgets.QTableWidgetItem(str(val) if val is not None else "")
                table.setItem(r, c, item)
