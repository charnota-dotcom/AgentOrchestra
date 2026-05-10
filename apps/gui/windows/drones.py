"""Drones tab — deployed instances of blueprints, ready to chat.

A *drone action* is what an operator deploys when they pick a blueprint
and start a conversation.  This tab is the operator's chat surface
against those actions.

Layout (left → right):
* Sidebar — list of actions, ordered by recency.  ``Deploy`` opens a
  modal that picks a blueprint + workspace.  Selecting an action loads
  its transcript.
* Centre — transcript + multi-line message input + Send.

Companion to ``apps/gui/windows/blueprints.py``.  See
``docs/DRONE_MODEL.md`` for the design.
"""

from __future__ import annotations

import asyncio
import html as _html
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _render_transcript_html(transcript: list[dict[str, Any]]) -> str:
    """Render an action's transcript as styled HTML for the viewer.

    Mirrors the lightweight markup the Agents tab uses — User /
    Assistant labels with role-coloured backgrounds.  Plain text only;
    the message body is HTML-escaped before insertion.
    """
    blocks: list[str] = []
    for m in transcript:
        role = m.get("role") or "user"
        content = _html.escape(m.get("content") or "")
        if role == "user":
            blocks.append(
                '<div style="background:#eef3fb;border-radius:6px;padding:10px 12px;'
                'margin-bottom:10px;"><b style="color:#1f6feb;">You</b><br>'
                f'<pre style="white-space:pre-wrap;font-family:inherit;margin:6px 0 0 0;">'
                f"{content}</pre></div>"
            )
        else:
            blocks.append(
                '<div style="background:#f7f8fa;border:1px solid #e6e7eb;border-radius:6px;'
                'padding:10px 12px;margin-bottom:10px;"><b style="color:#5b6068;">Drone</b><br>'
                f'<pre style="white-space:pre-wrap;font-family:inherit;margin:6px 0 0 0;">'
                f"{content}</pre></div>"
            )
    return (
        "".join(blocks)
        or "<i style='color:#7a7d85;'>(no messages yet — send one to get started)</i>"
    )


class DronesPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._actions: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar(), stretch=0)
        layout.addWidget(self._build_centre(), stretch=1)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload()))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("background:#fff;border-right:1px solid #e6e7eb;")
        wrap.setFixedWidth(280)
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Drones")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        header.addWidget(title)
        header.addStretch(1)
        deploy_btn = QtWidgets.QPushButton("Deploy")
        deploy_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #1f6feb;"
            "border-radius:4px;background:#1f6feb;color:#fff;font-size:12px;}"
            "QPushButton:hover{background:#1860d6;}"
        )
        deploy_btn.setToolTip(
            "Pick a blueprint + (optional) workspace and spawn a fresh drone "
            "action.  The action's blueprint snapshot is frozen at deploy "
            "time — later blueprint edits don't affect this action."
        )
        deploy_btn.clicked.connect(self._deploy_dialog)  # type: ignore[arg-type]
        header.addWidget(deploy_btn)
        v.addLayout(header)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget{border:none;background:transparent;}"
            "QListWidget::item{padding:8px 6px;border-radius:4px;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )
        self.list_widget.currentRowChanged.connect(self._on_select)  # type: ignore[arg-type]
        v.addWidget(self.list_widget, stretch=1)

        delete_btn = QtWidgets.QPushButton("Delete selected")
        delete_btn.setStyleSheet(
            "QPushButton{padding:4px 8px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;color:#5b6068;}"
            "QPushButton:hover{background:#fde8e7;border-color:#b3261e;color:#b3261e;}"
        )
        delete_btn.clicked.connect(self._delete_selected)  # type: ignore[arg-type]
        v.addWidget(delete_btn)
        return wrap

    # ------------------------------------------------------------------
    # Centre — transcript + input
    # ------------------------------------------------------------------

    def _build_centre(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        self.title = QtWidgets.QLabel("(no drone selected)")
        self.title.setStyleSheet("font-size:18px;font-weight:600;color:#0f1115;")
        v.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        self.subtitle.setWordWrap(True)
        v.addWidget(self.subtitle)

        # Workspace banner — green when bound, hidden when chat-only.
        self.workspace_label = QtWidgets.QLabel("")
        self.workspace_label.setStyleSheet(
            "color:#1f7a3f;font-size:11px;background:#e9f8ee;"
            "border:1px solid #c7e8d3;border-radius:4px;padding:4px 8px;"
        )
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setVisible(False)
        v.addWidget(self.workspace_label)

        self.transcript = QtWidgets.QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:12px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        v.addWidget(self.transcript, stretch=1)

        bottom = QtWidgets.QHBoxLayout()
        self.message_input = QtWidgets.QPlainTextEdit()
        self.message_input.setPlaceholderText("Type your message.  Ctrl+Enter to send.")
        self.message_input.setFixedHeight(110)
        self.message_input.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:8px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        bottom.addWidget(self.message_input, stretch=1)

        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setStyleSheet(
            "QPushButton{padding:10px 24px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;font-size:13px;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        self.send_btn.clicked.connect(self._send_message)  # type: ignore[arg-type]
        self.send_btn.setEnabled(False)
        bottom.addWidget(self.send_btn)
        v.addLayout(bottom)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.message_input)
        shortcut.activated.connect(self._send_message)  # type: ignore[arg-type]
        return wrap

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def _reload(self) -> None:
        try:
            self._actions = await self.client.call("drones.list", {})
        except Exception as e:
            self._actions = []
            self.subtitle.setText(f"Reload failed: {e}")
            self.subtitle.setStyleSheet("color:#b3261e;font-size:11px;")
            return
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for a in self._actions:
            snap = a.get("blueprint_snapshot") or {}
            name = snap.get("name") or "(unnamed blueprint)"
            role = snap.get("role") or "worker"
            provider = snap.get("provider") or ""
            model = snap.get("model") or ""
            n_turns = len(a.get("transcript") or [])
            label = f"{name}  ·  {role}\n{provider} / {model}  ·  {n_turns} msg"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, a["id"])
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self._current:
            for i, a in enumerate(self._actions):
                if a["id"] == self._current["id"]:
                    self.list_widget.setCurrentRow(i)
                    break

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._actions):
            self._current = None
            self.title.setText("(no drone selected)")
            self.subtitle.setText("")
            self.workspace_label.setVisible(False)
            self.transcript.setHtml(_render_transcript_html([]))
            self.send_btn.setEnabled(False)
            return
        action = self._actions[row]
        self._current = action
        snap = action.get("blueprint_snapshot") or {}
        self.title.setText(snap.get("name") or "(unnamed blueprint)")
        self.subtitle.setText(
            f"id {action['id']}  ·  blueprint {action.get('blueprint_id')}  ·  "
            f"role {snap.get('role', 'worker')}  ·  "
            f"{snap.get('provider', '')} / {snap.get('model', '')}"
        )
        if action.get("workspace_id"):
            asyncio.ensure_future(self._load_workspace_label(action["workspace_id"]))
        else:
            self.workspace_label.setVisible(False)
        self.transcript.setHtml(_render_transcript_html(action.get("transcript") or []))
        self.send_btn.setEnabled(True)

    async def _load_workspace_label(self, workspace_id: str) -> None:
        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception:
            self.workspace_label.setVisible(False)
            return
        ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if not ws:
            self.workspace_label.setVisible(False)
            return
        self.workspace_label.setText(
            f"📂 Bound to repo: {ws.get('name', '')} — {ws.get('repo_path', '')}"
        )
        self.workspace_label.setVisible(True)

    def _send_message(self) -> None:
        if not self._current:
            return
        text = self.message_input.toPlainText().strip()
        if not text:
            return
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        asyncio.ensure_future(self._send_async(self._current["id"], text))

    async def _send_async(self, action_id: str, message: str) -> None:
        try:
            out = await self.client.call(
                "drones.send", {"action_id": action_id, "message": message}
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Send failed", str(e))
            self.send_btn.setEnabled(True)
            return
        action = out.get("action") or {}
        # Update local cache + viewer.
        self._current = action
        for i, a in enumerate(self._actions):
            if a.get("id") == action.get("id"):
                self._actions[i] = action
                break
        self.transcript.setHtml(_render_transcript_html(action.get("transcript") or []))
        self.send_btn.setEnabled(True)
        # Re-pull list so the sidebar's "N msg" counter updates.
        await self._reload()

    def _delete_selected(self) -> None:
        if not self._current:
            return
        action_id = self._current["id"]
        if (
            QtWidgets.QMessageBox.question(
                self,
                "Delete drone",
                "Delete this drone action?  Its transcript will be lost.",
            )
            != QtWidgets.QMessageBox.StandardButton.Yes
        ):
            return
        asyncio.ensure_future(self._delete_async(action_id))

    async def _delete_async(self, action_id: str) -> None:
        try:
            await self.client.call("drones.delete", {"id": action_id})
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Delete failed", str(e))
            return
        self._current = None
        await self._reload()

    def _deploy_dialog(self) -> None:
        asyncio.ensure_future(self._deploy_dialog_async())

    async def _deploy_dialog_async(self) -> None:
        try:
            blueprints = await self.client.call("blueprints.list", {})
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Cannot open deploy dialog", str(e))
            return
        if not blueprints:
            QtWidgets.QMessageBox.information(
                self,
                "No blueprints yet",
                "Create a blueprint on the Blueprints tab first, then deploy from here.",
            )
            return
        dlg = _DeployDialog(blueprints, workspaces, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        params = dlg.params()
        try:
            action = await self.client.call("drones.deploy", params)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Deploy failed", str(e))
            return
        self._current = action
        await self._reload()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        asyncio.ensure_future(self._reload())


class _DeployDialog(QtWidgets.QDialog):
    """Pick a blueprint + (optional) workspace + (optional) one-off
    skills, return the params for ``drones.deploy``.
    """

    def __init__(
        self,
        blueprints: list[dict[str, Any]],
        workspaces: list[dict[str, Any]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Deploy drone")
        self.setModal(True)
        self.resize(520, 320)

        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self._blueprint = QtWidgets.QComboBox()
        for bp in blueprints:
            label = (
                f"{bp.get('name', '(unnamed)')}  ·  {bp.get('role', 'worker')}  ·  "
                f"{bp.get('provider', '')} / {bp.get('model', '')}"
            )
            self._blueprint.addItem(label, bp["id"])
        form.addRow("Blueprint", self._blueprint)

        self._workspace = QtWidgets.QComboBox()
        self._workspace.addItem("(no repo — chat only)", None)
        for ws in workspaces:
            self._workspace.addItem(
                f"{ws.get('name', '')} — {ws.get('repo_path', '')}", ws.get("id")
            )
        self._workspace.setToolTip(
            "Optional repo binding.  When bound, the CLI runs inside the repo "
            "and can read / search / edit files using its built-in tools."
        )
        form.addRow("Workspace", self._workspace)

        self._skills = QtWidgets.QLineEdit()
        self._skills.setPlaceholderText(
            "/oneoff-skill, /another  (optional, layered on top of blueprint defaults)"
        )
        form.addRow("Extra skills", self._skills)
        v.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Deploy")
        buttons.accepted.connect(self.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(self.reject)  # type: ignore[arg-type]
        v.addWidget(buttons)

    def params(self) -> dict[str, Any]:
        skills = [s.strip() for s in self._skills.text().replace("\n", ",").split(",") if s.strip()]
        return {
            "blueprint_id": self._blueprint.currentData(),
            "workspace_id": self._workspace.currentData(),
            "additional_skills": skills,
        }
