"""Palette panel — drag source for new canvas nodes.

Three sections: control nodes (Trigger / Branch / Merge / Human /
Output), agent cards loaded from the service, and live drone actions
(deployed instances of blueprints).  Each row is draggable; the canvas
page reads the MIME data on drop and creates the matching node.

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
    # Emitted right after a "Deploy" succeeds, so the canvas page can
    # drop a DroneActionNode at the view's centre and auto-open the
    # chat dialog.  Without this, the new drone was only added to the
    # palette and the operator stared at an empty canvas wondering
    # where it went.
    drone_deployed = QtCore.Signal(dict)  # the enriched drone-action dict

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

        # Drones — deployed actions from the Drones tab.  Drag onto
        # the canvas to anchor an action as a node; double-click the
        # node to open a chat dialog scoped to that action.
        drones_header = QtWidgets.QHBoxLayout()
        drones_header.setContentsMargins(0, 0, 0, 0)
        drones_header.addWidget(self._section_header("Drones"), stretch=1)
        deploy_btn = QtWidgets.QPushButton("Deploy")
        deploy_btn.setStyleSheet(
            "QPushButton{padding:2px 8px;border:1px solid #1f6feb;"
            "border-radius:4px;background:#1f6feb;color:#fff;font-size:11px;}"
            "QPushButton:hover{background:#1860d6;}"
        )
        deploy_btn.setToolTip(
            "Deploy a new drone action from a blueprint without leaving the canvas."
        )
        deploy_btn.clicked.connect(self._deploy_dialog)  # type: ignore[arg-type]
        drones_header.addWidget(deploy_btn)
        layout.addLayout(drones_header)

        self.drones_list = _DragList()
        self.drones_list.setStyleSheet(self._list_stylesheet())
        layout.addWidget(self.drones_list, stretch=1)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload_all()))

    async def _reload_all(self) -> None:
        await asyncio.gather(self.reload_cards(), self.reload_drones())

    # Deploy a drone action from a blueprint, without leaving the
    # canvas.  Mirrors the Drones tab's Deploy dialog so the canvas
    # surface behaves identically.  On success the palette refreshes
    # and the new action is draggable + auto-dropped on the canvas.
    def _deploy_dialog(self) -> None:
        asyncio.ensure_future(self._deploy_dialog_async())

    async def _deploy_dialog_async(self) -> None:
        try:
            blueprints = await self.client.call("blueprints.list", {})
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Cannot open deploy dialog", str(exc))
            return
        if not blueprints:
            QtWidgets.QMessageBox.information(
                self,
                "No blueprints yet",
                "Create a blueprint on the Blueprints tab first, then deploy from here.",
            )
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Deploy drone")
        dlg.resize(520, 360)
        outer = QtWidgets.QVBoxLayout(dlg)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)
        header = QtWidgets.QLabel(
            "<b>Deploy drone</b><br/>"
            "<span style='color:#5b6068;font-size:11px;'>Pick a blueprint + "
            "(optional) workspace.  The action's blueprint snapshot is frozen "
            "at deploy time — later blueprint edits don't affect this action."
            "</span>"
        )
        header.setTextFormat(QtCore.Qt.TextFormat.RichText)
        outer.addWidget(header)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        bp_combo = QtWidgets.QComboBox()
        for bp in blueprints:
            label = (
                f"{bp.get('name', '(unnamed)')}  ·  {bp.get('role', 'worker')}  ·  "
                f"{bp.get('provider', '')} / {bp.get('model', '')}"
            )
            bp_combo.addItem(label, bp["id"])
        form.addRow("Blueprint:", bp_combo)

        ws_combo = QtWidgets.QComboBox()
        ws_combo.addItem("(no repo — chat only)", "")
        for w in workspaces:
            ws_combo.addItem(f"{w.get('name', '?')} — {w.get('repo_path', '?')}", w.get("id", ""))
        form.addRow("Repo:", ws_combo)

        skills_input = QtWidgets.QLineEdit()
        skills_input.setPlaceholderText(
            "/oneoff-skill, /another  (optional, layered on blueprint defaults)"
        )
        form.addRow("Extra skills:", skills_input)

        outer.addLayout(form)

        first_msg = QtWidgets.QPlainTextEdit()
        first_msg.setPlaceholderText(
            "Optional first message — sent right after deploy.  Without one, "
            "the drone is created and you double-click it on the canvas to chat."
        )
        first_msg.setMinimumHeight(90)
        outer.addWidget(QtWidgets.QLabel("First message:"))
        outer.addWidget(first_msg)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Deploy")
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        outer.addWidget(buttons)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        skills = [s.strip() for s in skills_input.text().replace("\n", ",").split(",") if s.strip()]
        await self._do_deploy(
            blueprint_id=bp_combo.currentData(),
            workspace_id=ws_combo.currentData() or None,
            additional_skills=skills,
            first_message=first_msg.toPlainText().strip(),
        )

    async def _do_deploy(
        self,
        *,
        blueprint_id: str,
        workspace_id: str | None,
        additional_skills: list[str],
        first_message: str,
    ) -> None:
        try:
            action = await self.client.call(
                "drones.deploy",
                {
                    "blueprint_id": blueprint_id,
                    "workspace_id": workspace_id,
                    "additional_skills": additional_skills,
                },
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Deploy failed", str(exc))
            return
        if first_message:
            try:
                send_res = await self.client.call(
                    "drones.send",
                    {"action_id": action["id"], "message": first_message},
                )
                # Keep the freshest action shape (transcript now has
                # the first turn) for the canvas drop below.
                action = send_res.get("action", action)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "First message failed", str(exc))
        await self.reload_drones()
        # Surface the new drone on the canvas — the empty-canvas-after-
        # Deploy UX was the #1 source of "where did my drone go?"
        # confusion before the rip-out.  Page wires this signal to drop
        # a DroneActionNode at the view centre + auto-open the chat.
        self.drone_deployed.emit(action)

    async def reload_drones(self) -> None:
        try:
            actions = await self.client.call("drones.list", {})
        except Exception:
            actions = []
        self.drones_list.clear()
        for a in actions:
            snap = a.get("blueprint_snapshot") or {}
            name = snap.get("name") or "(unnamed)"
            sub = (
                f"{snap.get('provider', '?')} / {snap.get('model', '?')}  ·  "
                f"{len(a.get('transcript') or [])} turns"
            )
            if a.get("workspace_id"):
                sub += "  ·  📂 repo"
            item = QtWidgets.QListWidgetItem(f"{name}\n{sub}")
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                {"kind": "drone_action", "action": a},
            )
            self.drones_list.addItem(item)

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
