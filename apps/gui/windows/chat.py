"""Chat — the lay-person agent UI.

A single chat box, a model picker, an optional thinking-depth dropdown,
and an optional /skills field.  No card, no template, no run state
machine — just send a message, get a reply.

This page exists alongside Compose / Canvas / History so operators
who want the "branch-per-agent + state machine + cost caps" power
can still get it, while operators who just want to send a quick
message to ``sonnet 4.6`` with hard thinking can skip every gate.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


# Model presets — every option goes through a local CLI (Claude Code
# or Gemini CLI) so chat reuses your Max-plan / Gemini-CLI auth and
# never bills against an API key.  The dropdown reads naturally; each
# entry maps to (provider, model).  Add more here without changing
# any other code.
_MODEL_PRESETS: list[tuple[str, str, str]] = [
    ("Claude Sonnet 4.6  (Claude Code)", "claude-cli", "claude-sonnet-4-6"),
    ("Claude Opus 4.7  (Claude Code)", "claude-cli", "claude-opus-4-7"),
    ("Claude Haiku 4.5  (Claude Code)", "claude-cli", "claude-haiku-4-5"),
    ("Gemini 2.5 Pro  (Gemini CLI)", "gemini-cli", "gemini-2.5-pro"),
    ("Gemini 2.5 Flash  (Gemini CLI)", "gemini-cli", "gemini-2.5-flash"),
]


_THINKING_PRESETS: list[tuple[str, str]] = [
    ("Off", ""),
    ("Normal", "Think briefly before answering."),
    ("Hard", "Think carefully and step by step before answering. Show your reasoning."),
    (
        "Very hard",
        "Think exhaustively step by step before answering. "
        "Consider edge cases, alternative interpretations, and potential pitfalls. "
        "Show your reasoning explicitly.",
    ),
]


class ChatPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Chat")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel(
            "Pick a model, write a message, hit send.  Routes through "
            "the local Claude Code (or Gemini) CLI you're already "
            "logged into — no API key, no billing.  Use /skill-name "
            'anywhere in your message; pick "Hard" thinking for '
            "tougher questions."
        )
        subtitle.setStyleSheet("color:#5b6068;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Top row: model + thinking + skills
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(12)

        model_label = QtWidgets.QLabel("Model:")
        model_label.setStyleSheet("color:#5b6068;font-size:12px;")
        top.addWidget(model_label)
        self.model_combo = QtWidgets.QComboBox()
        for label, _provider, _model in _MODEL_PRESETS:
            self.model_combo.addItem(label)
        top.addWidget(self.model_combo, stretch=1)

        thinking_label = QtWidgets.QLabel("Thinking:")
        thinking_label.setStyleSheet("color:#5b6068;font-size:12px;")
        top.addWidget(thinking_label)
        self.thinking_combo = QtWidgets.QComboBox()
        for label, _ in _THINKING_PRESETS:
            self.thinking_combo.addItem(label)
        self.thinking_combo.setCurrentIndex(1)  # Normal
        top.addWidget(self.thinking_combo)

        layout.addLayout(top)

        skills_row = QtWidgets.QHBoxLayout()
        skills_label = QtWidgets.QLabel("Skills:")
        skills_label.setStyleSheet("color:#5b6068;font-size:12px;")
        skills_row.addWidget(skills_label)
        self.skills_input = QtWidgets.QLineEdit()
        self.skills_input.setPlaceholderText(
            "Optional, e.g. /research-deep /cite-sources  (free-form, passed in the prompt)"
        )
        skills_row.addWidget(self.skills_input, stretch=1)
        layout.addLayout(skills_row)

        # Transcript above, input below.
        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:12px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        layout.addWidget(self.transcript, stretch=1)

        # Bottom: message input + send button.
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
        self.send_btn.clicked.connect(self._send)  # type: ignore[arg-type]
        bottom.addWidget(self.send_btn)
        layout.addLayout(bottom)

        # Ctrl+Enter to send from the input box.
        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.message_input)
        shortcut.activated.connect(self._send)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def _send(self) -> None:
        text = self.message_input.toPlainText().strip()
        if not text:
            return
        skills = self.skills_input.text().strip()
        if skills:
            text = f"{skills}\n\n{text}"
        self._append("You", text)
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        asyncio.ensure_future(self._send_async(text))

    async def _send_async(self, message: str) -> None:
        idx = self.model_combo.currentIndex()
        _label, provider, model = _MODEL_PRESETS[idx]
        thinking_idx = self.thinking_combo.currentIndex()
        _t_label, system_thinking = _THINKING_PRESETS[thinking_idx]

        try:
            res = await self.client.call(
                "chat.send",
                {
                    "provider": provider,
                    "model": model,
                    "message": message,
                    "system": system_thinking,
                },
            )
            reply = res.get("reply", "")
        except Exception as exc:
            self._append("Error", str(exc))
            self.send_btn.setEnabled(True)
            return
        self._append(_label_for(provider, model), reply or "(empty reply)")
        self.send_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    def _append(self, who: str, text: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if self.transcript.toPlainText():
            cursor.insertText("\n\n")
        cursor.insertText(f"{who}:\n{text}")
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()


def _label_for(provider: str, model: str) -> str:
    return f"{model} ({provider})"
