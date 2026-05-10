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
