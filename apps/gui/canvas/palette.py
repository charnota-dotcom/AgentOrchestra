"""Palette panel — drag source for new canvas nodes.

Two sections: control nodes (Trigger / Branch / Merge / Human /
Output), then agent cards loaded from the service.  Each row is
draggable; the canvas page reads the MIME data on drop and creates
the matching node.

V1 uses Qt's standard drag-and-drop with a custom MIME type so the
canvas can distinguish a palette drag from a normal selection drag.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


PALETTE_MIME = "application/x-agentorchestra-palette"


_CONTROL_NODES = [
    ("trigger", "Trigger", "Manual start"),
    ("branch", "Branch", "Route on regex"),
    ("merge", "Merge", "Join branches"),
    ("human", "Human", "Approve / Reject"),
    ("output", "Output", "Final sink"),
]


class _DragList(QtWidgets.QListWidget):
    """A QListWidget that emits a custom-MIME drag on item press."""

    def startDrag(
        self,
        _supported_actions: QtCore.Qt.DropAction,
    ) -> None:
        item = self.currentItem()
        if item is None:
            return
        payload = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not payload:
            return
        mime = QtCore.QMimeData()
        mime.setData(PALETTE_MIME, json.dumps(payload).encode("utf-8"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.DropAction.CopyAction)


class PalettePanel(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("background:#fff;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Palette")
        title.setStyleSheet("font-size:13px;font-weight:600;color:#0f1115;")
        layout.addWidget(title)

        layout.addWidget(self._section_header("Control"))
        self.control_list = _DragList()
        self.control_list.setStyleSheet(self._list_stylesheet())
        for kind, name, desc in _CONTROL_NODES:
            item = QtWidgets.QListWidgetItem(f"{name}\n{desc}")
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                {"kind": "control", "control_kind": kind},
            )
            self.control_list.addItem(item)
        layout.addWidget(self.control_list)

        layout.addWidget(self._section_header("Agent cards"))
        self.cards_list = _DragList()
        self.cards_list.setStyleSheet(self._list_stylesheet())
        layout.addWidget(self.cards_list, stretch=1)

        # Persistent named conversations (Agents tab).  Drag onto the
        # canvas to anchor a conversation as a node; double-click the
        # node to open a chat dialog scoped to that one agent.
        layout.addWidget(self._section_header("Conversations"))
        self.agents_list = _DragList()
        self.agents_list.setStyleSheet(self._list_stylesheet())
        layout.addWidget(self.agents_list, stretch=1)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload_all()))

    async def _reload_all(self) -> None:
        await asyncio.gather(self.reload_cards(), self.reload_agents())

    async def reload_agents(self) -> None:
        try:
            agents = await self.client.call("agents.list", {})
        except Exception:
            agents = []
        self.agents_list.clear()
        for a in agents:
            label_lines = [a.get("name", "?")]
            sub = f"{a.get('model', '?')} · {len(a.get('transcript') or [])} turns"
            if a.get("parent_name"):
                sub += f"  ↩ {a.get('parent_preset') or 'follow-up'} of {a['parent_name']}"
            label_lines.append(sub)
            item = QtWidgets.QListWidgetItem("\n".join(label_lines))
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                {"kind": "conversation", "agent": a},
            )
            self.agents_list.addItem(item)

    @staticmethod
    def _section_header(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(
            "font-size:11px;font-weight:600;color:#5b6068;"
            "text-transform:uppercase;letter-spacing:0.05em;padding-top:4px;"
        )
        return lbl

    @staticmethod
    def _list_stylesheet() -> str:
        return (
            "QListWidget{background:#f6f8fa;border:1px solid #e6e7eb;border-radius:4px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #e6e7eb;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )

    async def reload_cards(self) -> None:
        try:
            cards = await self.client.call("cards.list", {})
        except Exception:
            cards = []
        self.cards_list.clear()
        for card in cards:
            item = QtWidgets.QListWidgetItem(
                f"{card.get('name', '?')}\n{card.get('provider', '?')} · {card.get('model', '?')}"
            )
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                {"kind": "agent", "card": card},
            )
            self.cards_list.addItem(item)
