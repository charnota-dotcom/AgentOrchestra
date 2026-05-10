"""Per-agent chat dialog opened by double-clicking a ConversationNode.

A small modal-ish QDialog (non-modal so the operator can drag the
canvas while it's open) showing one agent's transcript and a send box.
Continuing the conversation hits ``agents.send`` so the persistent
transcript on the service stays the source of truth — the canvas
ConversationNode auto-refreshes on next open.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class AgentChatDialog(QtWidgets.QDialog):
    sent = QtCore.Signal(dict)  # updated agent dict, emitted after each send

    def __init__(
        self,
        client: RpcClient,
        agent: dict[str, Any],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.agent = agent
        self.setWindowTitle(f"Chat with {agent.get('name', '?')}")
        self.resize(620, 540)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        header = QtWidgets.QLabel(
            f"<b>{agent.get('name', '?')}</b>  ·  "
            f"<span style='color:#5b6068'>{agent.get('model', '?')} "
            f"({agent.get('provider', '?')})</span>"
        )
        header.setStyleSheet("font-size:14px;color:#0f1115;")
        v.addWidget(header)

        if agent.get("parent_name"):
            preset = agent.get("parent_preset") or "follow-up"
            origin = QtWidgets.QLabel(
                f"Spawned via <b>{preset}</b> from <b>{agent['parent_name']}</b>"
            )
        else:
            origin = QtWidgets.QLabel("Top-level conversation")
        origin.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(origin)

        # References — read-only summary + "Edit" button.  Each
        # referenced agent's full transcript is inlined as context
        # on every send so this Claude / Gemini knows what those
        # other conversations contained.
        ref_row = QtWidgets.QHBoxLayout()
        self.refs_label = QtWidgets.QLabel(self._format_refs_label(agent))
        self.refs_label.setStyleSheet("color:#5b6068;font-size:11px;")
        self.refs_label.setWordWrap(True)
        ref_row.addWidget(self.refs_label, stretch=1)
        edit_refs_btn = QtWidgets.QPushButton("Edit references")
        edit_refs_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        edit_refs_btn.setToolTip(
            "Pick other conversations whose full transcripts should be "
            "inlined into this agent's prompt as read-only context.  "
            "Cross-provider (e.g. Gemini reading a Claude conversation) "
            "is supported — references are passed as plain text."
        )
        edit_refs_btn.clicked.connect(self._edit_references)  # type: ignore[arg-type]
        ref_row.addWidget(edit_refs_btn)
        v.addLayout(ref_row)

        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:12px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        v.addWidget(self.transcript, stretch=1)

        bottom = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QPlainTextEdit()
        self.input.setPlaceholderText("Continue the conversation.  Ctrl+Enter to send.")
        self.input.setFixedHeight(80)
        self.input.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:8px;font-size:13px;}"
        )
        bottom.addWidget(self.input, stretch=1)

        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setStyleSheet(
            "QPushButton{padding:10px 22px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        self.send_btn.clicked.connect(self._send)  # type: ignore[arg-type]
        bottom.addWidget(self.send_btn)
        v.addLayout(bottom)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.input)
        shortcut.activated.connect(self._send)  # type: ignore[arg-type]

        self._render_transcript()

    def _render_transcript(self) -> None:
        self.transcript.clear()
        for turn in self.agent.get("transcript", []):
            who = "You" if turn.get("role") == "user" else self.agent.get("name", "Agent")
            self.transcript.appendPlainText(f"{who}:\n{turn.get('content', '')}\n")

    @staticmethod
    def _format_refs_label(agent: dict[str, Any]) -> str:
        ref_ids = agent.get("reference_agent_ids") or []
        if not ref_ids:
            return "References: none"
        return f"References: {len(ref_ids)} other conversation(s) inlined as context"

    def _edit_references(self) -> None:
        asyncio.ensure_future(self._open_refs_dialog())

    async def _open_refs_dialog(self) -> None:
        try:
            agents = await self.client.call("agents.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't load agents", str(exc))
            return
        # Drop ourselves so an agent can't reference itself.
        candidates = [a for a in agents if a.get("id") != self.agent.get("id")]

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"References for {self.agent.get('name', '?')}")
        dlg.resize(520, 480)
        v = QtWidgets.QVBoxLayout(dlg)

        v.addWidget(
            QtWidgets.QLabel(
                "Tick the conversations whose full transcripts should be "
                "inlined as read-only context on every message you send.  "
                "Works cross-provider (e.g. Gemini reading a Claude chat) — "
                "we just pass the text."
            )
        )
        v.itemAt(0).widget().setWordWrap(True)  # type: ignore[union-attr]
        v.itemAt(0).widget().setStyleSheet("color:#5b6068;font-size:11px;")  # type: ignore[union-attr]

        listw = QtWidgets.QListWidget()
        listw.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:4px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #eef0f3;}"
        )
        current_refs = set(self.agent.get("reference_agent_ids") or [])
        for a in candidates:
            tlen = len(a.get("transcript") or [])
            label = (
                f"{a.get('name', '?')}  ·  {a.get('model', '?')}  "
                f"({a.get('provider', '?')})  ·  {tlen} turns"
            )
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if a.get("id") in current_refs
                else QtCore.Qt.CheckState.Unchecked
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, a.get("id"))
            listw.addItem(item)
        v.addWidget(listw, stretch=1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        v.addWidget(buttons)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        chosen: list[str] = []
        for i in range(listw.count()):
            it = listw.item(i)
            if it is not None and it.checkState() == QtCore.Qt.CheckState.Checked:
                chosen.append(str(it.data(QtCore.Qt.ItemDataRole.UserRole)))
        try:
            updated = await self.client.call(
                "agents.set_references",
                {"agent_id": self.agent["id"], "reference_agent_ids": chosen},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't save references", str(exc))
            return
        self.agent = updated
        self.refs_label.setText(self._format_refs_label(self.agent))

    def _send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.send_btn.setEnabled(False)
        # Optimistic: show the user's message immediately.
        self.transcript.appendPlainText(f"You:\n{text}\n")
        asyncio.ensure_future(self._send_async(text))

    async def _send_async(self, message: str) -> None:
        try:
            res = await self.client.call(
                "agents.send",
                {"agent_id": self.agent["id"], "message": message},
            )
        except Exception as exc:
            self.transcript.appendPlainText(f"Error:\n{exc}\n")
            self.send_btn.setEnabled(True)
            return
        self.agent = res.get("agent", self.agent)
        self._render_transcript()
        self.send_btn.setEnabled(True)
        self.sent.emit(self.agent)
