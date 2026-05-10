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
# never bills against an API key.
#
# Each entry is (display label, provider, model, system_prompt).  The
# system prompt is what swaps the assistant's persona without leaving
# the same provider — e.g. "Claude Sonnet 4.6 (General Chat)" goes
# through claude-cli but with a friendlier general-purpose prompt
# than the default coding-assistant behaviour you'd get with
# "Claude Sonnet 4.6 (Claude Code)".
_MODEL_PRESETS: list[tuple[str, str, str, str]] = [
    # Coding-default rows — what you'd get from `claude` on its own.
    ("Claude Sonnet 4.6  (Claude Code)", "claude-cli", "claude-sonnet-4-6", ""),
    ("Claude Opus 4.7  (Claude Code)", "claude-cli", "claude-opus-4-7", ""),
    ("Claude Haiku 4.5  (Claude Code)", "claude-cli", "claude-haiku-4-5", ""),
    ("Gemini 2.5 Pro  (Gemini CLI)", "gemini-cli", "gemini-2.5-pro", ""),
    ("Gemini 2.5 Flash  (Gemini CLI)", "gemini-cli", "gemini-2.5-flash", ""),
    # General-chat rows — same models, friendlier prompt.  Useful for
    # writing, research, brainstorming, planning, everyday questions.
    (
        "Claude Sonnet 4.6  (General Chat)",
        "claude-cli",
        "claude-sonnet-4-6",
        "You are a friendly general-purpose assistant.  Help with "
        "writing, research, brainstorming, planning, and everyday "
        "questions.  Do not assume the user is asking about code; "
        "if they are, treat code as one option among many.  Be "
        "concise unless asked for depth.",
    ),
    (
        "Claude Opus 4.7  (General Chat)",
        "claude-cli",
        "claude-opus-4-7",
        "You are a friendly general-purpose assistant.  Help with "
        "writing, research, brainstorming, planning, and everyday "
        "questions.  Do not assume the user is asking about code; "
        "if they are, treat code as one option among many.  Be "
        "concise unless asked for depth.",
    ),
    (
        "Gemini 2.5 Pro  (General Chat)",
        "gemini-cli",
        "gemini-2.5-pro",
        "You are a friendly general-purpose assistant.  Help with "
        "writing, research, brainstorming, planning, and everyday "
        "questions.  Do not assume the user is asking about code; "
        "if they are, treat code as one option among many.  Be "
        "concise unless asked for depth.",
    ),
    # File / artifact mode — model emits the file's contents directly
    # so the operator can hit "Save reply" and write it to disk.
    (
        "Claude Sonnet 4.6  (File / artifact)",
        "claude-cli",
        "claude-sonnet-4-6",
        "Produce a self-contained artifact the user can save to "
        "disk.  Format your reply as the file's literal contents "
        "— no surrounding chatter, no Markdown fencing unless the "
        "file format itself uses Markdown.  If the user didn't "
        "specify a format, pick the most appropriate one (plain "
        "text, JSON, CSV, Markdown, etc.) and start your reply "
        "with a single header line `# filename.ext` so the file "
        "can be saved with a sensible name.",
    ),
    (
        "Gemini 2.5 Pro  (File / artifact)",
        "gemini-cli",
        "gemini-2.5-pro",
        "Produce a self-contained artifact the user can save to "
        "disk.  Format your reply as the file's literal contents "
        "— no surrounding chatter, no Markdown fencing unless the "
        "file format itself uses Markdown.  If the user didn't "
        "specify a format, pick the most appropriate one (plain "
        "text, JSON, CSV, Markdown, etc.) and start your reply "
        "with a single header line `# filename.ext` so the file "
        "can be saved with a sensible name.",
    ),
    # Image-prompt mode — useful for piping into a separate image
    # generator (Midjourney, DALL-E, Stable Diffusion).  Native image
    # generation needs a paid API; out of scope for the no-fees default.
    (
        "Claude Sonnet 4.6  (Image prompt)",
        "claude-cli",
        "claude-sonnet-4-6",
        "The user wants an image.  Don't try to render one — "
        "instead produce a precise, vivid prompt suitable for a "
        "text-to-image generator (Midjourney / DALL-E / Stable "
        "Diffusion / Imagen).  Include subject, composition, "
        "style, lighting, mood, and any reference points.  Keep "
        "the prompt under 200 words.  Output only the prompt; no "
        "preamble.",
    ),
    (
        "Gemini 2.5 Pro  (Image prompt)",
        "gemini-cli",
        "gemini-2.5-pro",
        "The user wants an image.  Don't try to render one — "
        "instead produce a precise, vivid prompt suitable for a "
        "text-to-image generator (Midjourney / DALL-E / Stable "
        "Diffusion / Imagen).  Include subject, composition, "
        "style, lighting, mood, and any reference points.  Keep "
        "the prompt under 200 words.  Output only the prompt; no "
        "preamble.",
    ),
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
        # Each Chat session is automatically persisted as an Agent so
        # it shows up in the canvas Conversations palette and can be
        # dragged onto the canvas.  ``self._agent_id`` tracks the
        # current session: None means "no message sent yet for this
        # session, agents.create on the first send".  "New chat" /
        # changing the model preset clears it back to None.
        self._agent_id: str | None = None
        # In-memory mirror of the agent's transcript so the GUI shows
        # the user's message immediately on send (optimistic) and
        # then reconciles with the service reply.
        self._history: list[dict[str, str]] = []
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
        for entry in _MODEL_PRESETS:
            self.model_combo.addItem(entry[0])
        # Switching model mid-thread would require a model swap on
        # the existing Agent, which our backend doesn't support.
        # Treat a model change as "start a new chat" so the operator
        # gets a fresh, correctly-modelled Agent.
        self.model_combo.currentIndexChanged.connect(  # type: ignore[arg-type]
            lambda _i: self._new_chat()
        )
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

        controls = QtWidgets.QHBoxLayout()
        controls.addStretch(1)
        save_btn = QtWidgets.QPushButton("Save last reply…")
        save_btn.setToolTip(
            "Save the most recent assistant reply to a file.  Useful "
            'when you ran the "File / artifact" model preset.'
        )
        save_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        save_btn.clicked.connect(self._save_last_reply)  # type: ignore[arg-type]
        controls.addWidget(save_btn)

        new_chat_btn = QtWidgets.QPushButton("New chat (clear history)")
        new_chat_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        new_chat_btn.clicked.connect(self._new_chat)  # type: ignore[arg-type]
        controls.addWidget(new_chat_btn)
        layout.addLayout(controls)

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
        self._history.append({"role": "user", "content": text})
        self._append("You", text)
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        asyncio.ensure_future(self._send_async(text))

    async def _send_async(self, message: str) -> None:
        idx = self.model_combo.currentIndex()
        label, provider, model, mode_system = _MODEL_PRESETS[idx]
        thinking_idx = self.thinking_combo.currentIndex()
        _t_label, system_thinking = _THINKING_PRESETS[thinking_idx]

        # System prompt is stitched together from three sources, in
        # priority order: the model preset's mode prompt (Coding /
        # General / File / Image), the thinking-depth directive,
        # then the user's free-form skills field.  Joined with blank
        # lines so each is its own paragraph for the model.
        skills = self.skills_input.text().strip()
        system_parts = [p for p in (mode_system, system_thinking, _skills_to_system(skills)) if p]
        system = "\n\n".join(system_parts)

        # First message of the session: mint a persistent Agent so
        # this conversation shows up in the canvas Conversations
        # palette and can be dragged onto the canvas.  Subsequent
        # messages route through agents.send so the transcript stays
        # in one place.  Auto-name = first ~40 chars of the user's
        # opening message so the palette is browsable.
        if self._agent_id is None:
            try:
                created = await self.client.call(
                    "agents.create",
                    {
                        "name": _auto_name_from(message, label),
                        "provider": provider,
                        "model": model,
                        "system": system,
                    },
                )
                self._agent_id = created["id"]
            except Exception as exc:
                self._append("Error", f"could not create agent: {exc}")
                self.send_btn.setEnabled(True)
                return
            try:
                res = await self.client.call(
                    "agents.send",
                    {"agent_id": self._agent_id, "message": message},
                )
                reply = res.get("reply", "")
            except Exception as exc:
                self._append("Error", str(exc))
                self.send_btn.setEnabled(True)
                return
            self._history.append({"role": "assistant", "content": reply})
            self._append(_label_for(provider, model), reply or "(empty reply)")
            self.send_btn.setEnabled(True)
            return

        # Subsequent messages: just extend the existing agent's
        # transcript.  agents.send already folds the full prior
        # transcript into the prompt server-side.
        try:
            res = await self.client.call(
                "agents.send",
                {"agent_id": self._agent_id, "message": message},
            )
            reply = res.get("reply", "")
        except Exception as exc:
            self._append("Error", str(exc))
            self.send_btn.setEnabled(True)
            return
        self._history.append({"role": "assistant", "content": reply})
        self._append(_label_for(provider, model), reply or "(empty reply)")
        self.send_btn.setEnabled(True)

    def _new_chat(self) -> None:
        # Clearing the in-memory mirror is enough — the previous
        # session's Agent stays in the Conversations palette so it
        # can still be resumed from the canvas or the Agents tab.
        # The next send mints a fresh Agent.
        self._agent_id = None
        self._history.clear()
        self.transcript.clear()
        self.message_input.clear()

    def _save_last_reply(self) -> None:
        """Save the most recent assistant turn to a file.

        Looks for a leading ``# filename.ext`` header line (which the
        File / artifact model preset emits) to seed the suggested
        filename.  Strips the header from the saved contents so the
        file isn't littered with our own marker.
        """
        last = next(
            (m for m in reversed(self._history) if m.get("role") == "assistant"),
            None,
        )
        if last is None:
            QtWidgets.QMessageBox.information(
                self,
                "Nothing to save",
                "No assistant reply yet.  Send a message first.",
            )
            return
        text = last.get("content", "")
        suggested_name = "reply.txt"
        body = text
        # File / artifact mode emits "# filename.ext" as the first
        # line.  Pick that up if present.
        first_line, sep, rest = text.partition("\n")
        if first_line.startswith("# ") and "." in first_line[2:50]:
            suggested_name = first_line[2:].strip()
            body = rest if sep else ""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save reply as file",
            suggested_name,
        )
        if not path:
            return
        try:
            from pathlib import Path

            Path(path).write_text(body, encoding="utf-8")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._append("System", f"Saved reply to {path}")

    def _render_for_send(self) -> str:
        """Fold the in-memory turn buffer into a single prompt.

        Single-turn: just the user's text.  Multi-turn: prior turns
        prefixed with role labels so the model can reconstruct the
        conversation, ending with the latest user message at the end.
        """
        if len(self._history) <= 1:
            return self._history[-1]["content"] if self._history else ""
        parts: list[str] = []
        for m in self._history[:-1]:
            role = "User" if m["role"] == "user" else "Assistant"
            parts.append(f"{role}: {m['content']}")
        parts.append(f"User: {self._history[-1]['content']}")
        return "\n\n".join(parts)

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


def _auto_name_from(message: str, model_label: str) -> str:
    """Produce a short browsable name from the user's first message.

    Truncates to ~50 chars and strips newlines so the Conversations
    palette stays scannable.  Falls back to the model label when the
    first message is essentially empty.
    """
    one_line = " ".join(message.split())
    if not one_line:
        return model_label
    if len(one_line) <= 50:
        return one_line
    return one_line[:47] + "…"


def _skills_to_system(skills: str) -> str:
    """Turn the free-form ``/foo /bar baz`` skills field into a system
    directive the model will read as instructions.  We don't try to
    actually invoke Claude Code's first-class Skills feature here —
    that only fires in interactive mode and our Chat tab is headless.
    But we *do* tell the model "treat these as activation directives"
    so the reply respects them in spirit.
    """
    if not skills.strip():
        return ""
    return (
        "Skill directives (treat each `/name` token as an activation "
        "instruction; respond as if those skills are active for this "
        f"conversation): {skills.strip()}"
    )
