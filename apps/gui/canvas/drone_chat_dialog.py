"""Per-drone chat dialog opened by double-clicking a DroneActionNode.

Minimal QDialog (non-modal) showing one drone action's transcript and
a send box.  Continues the conversation via ``drones.send`` so the
persistent transcript on the service stays the source of truth — the
canvas DroneActionNode auto-refreshes on next open via ``sent``.

Intentionally smaller than the legacy ``AgentChatDialog``: no
attachments, no spawn-followup, no per-turn references.  Those are
follow-up features for the Drones tab, not the canvas mini-dialog.
"""

from __future__ import annotations

import asyncio
import html as _html
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _render_html(transcript: list[dict[str, Any]]) -> str:
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
        or "<i style='color:#7a7d85;'>(no messages yet — type one below to start)</i>"
    )


class DroneActionChatDialog(QtWidgets.QDialog):
    sent = QtCore.Signal(dict)  # updated action dict, emitted after each send

    def __init__(
        self,
        client: RpcClient,
        action: dict[str, Any],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.action = action
        snap = action.get("blueprint_snapshot") or {}
        self.setWindowTitle(f"Drone — {snap.get('name', '?')}")
        self.resize(640, 540)
        # Non-modal so the operator can keep dragging on the canvas.
        self.setModal(False)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        title = QtWidgets.QLabel(snap.get("name") or "(unnamed drone)")
        title.setStyleSheet("font-size:16px;font-weight:600;color:#0f1115;")
        v.addWidget(title)

        sub = QtWidgets.QLabel(
            f"{snap.get('role', 'worker')}  ·  {snap.get('provider', '?')} / "
            f"{snap.get('model', '?')}"
        )
        sub.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(sub)

        self.transcript = QtWidgets.QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:12px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        self.transcript.setHtml(_render_html(action.get("transcript") or []))
        v.addWidget(self.transcript, stretch=1)

        bottom = QtWidgets.QHBoxLayout()
        self.message_input = QtWidgets.QPlainTextEdit()
        self.message_input.setPlaceholderText("Type your message.  Ctrl+Enter to send.")
        self.message_input.setFixedHeight(100)
        self.message_input.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:8px;font-size:13px;}"
        )
        bottom.addWidget(self.message_input, stretch=1)
        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setStyleSheet(
            "QPushButton{padding:10px 24px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;font-size:13px;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        self.send_btn.clicked.connect(self._send)  # type: ignore[arg-type]
        bottom.addWidget(self.send_btn)
        v.addLayout(bottom)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.message_input)
        shortcut.activated.connect(self._send)  # type: ignore[arg-type]

    def _send(self) -> None:
        text = self.message_input.toPlainText().strip()
        if not text:
            return
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        asyncio.ensure_future(self._send_async(text))

    async def _send_async(self, text: str) -> None:
        try:
            out = await self.client.call(
                "drones.send", {"action_id": self.action["id"], "message": text}
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Send failed", str(exc))
            self.send_btn.setEnabled(True)
            return
        self.action = out.get("action") or self.action
        self.transcript.setHtml(_render_html(self.action.get("transcript") or []))
        self.send_btn.setEnabled(True)
        # Bubble up so the canvas can refresh the node.
        self.sent.emit(self.action)
