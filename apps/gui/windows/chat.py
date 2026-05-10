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
import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


_ATTACHMENT_FILTER = (
    "Supported files (*.png *.jpg *.jpeg *.gif *.webp *.xlsx *.xls *.csv);;"
    "Images (*.png *.jpg *.jpeg *.gif *.webp);;"
    "Spreadsheets (*.xlsx *.xls *.csv);;"
    "All files (*)"
)


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
        # Accept dropped local files anywhere on the page so the
        # operator can drag from the OS file browser straight onto
        # the chat input.
        self.setAcceptDrops(True)
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

        # Workspace picker — when set, the CLI subprocess is spawned
        # with cwd = repo_path so the model can use its built-in file
        # tools against the project.  Changing the workspace = new
        # chat (same constraint as the model picker).
        ws_row = QtWidgets.QHBoxLayout()
        ws_label = QtWidgets.QLabel("Repo:")
        ws_label.setStyleSheet("color:#5b6068;font-size:12px;")
        ws_row.addWidget(ws_label)
        self.workspace_combo = QtWidgets.QComboBox()
        self.workspace_combo.addItem("(no repo — chat only)", "")
        self.workspace_combo.setToolTip(
            "When a repo is selected, the agent runs inside that "
            "directory and can read / search / edit files using the "
            "CLI's built-in tools."
        )
        self.workspace_combo.currentIndexChanged.connect(  # type: ignore[arg-type]
            lambda _i: self._new_chat()
        )
        ws_row.addWidget(self.workspace_combo, stretch=1)
        add_repo_btn = QtWidgets.QPushButton("Add repo…")
        add_repo_btn.setStyleSheet(
            "QPushButton{padding:5px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        add_repo_btn.setToolTip(
            "Pick an existing local git repo on disk and register it as "
            "a workspace this agent can use."
        )
        add_repo_btn.clicked.connect(self._add_repo)  # type: ignore[arg-type]
        ws_row.addWidget(add_repo_btn)
        clone_btn = QtWidgets.QPushButton("Clone from git…")
        clone_btn.setStyleSheet(
            "QPushButton{padding:5px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        clone_btn.setToolTip(
            "Clone a remote git URL into AgentOrchestra's data directory "
            "and register it as a workspace.  One-click coding session "
            "from a repo URL."
        )
        clone_btn.clicked.connect(self._clone_repo)  # type: ignore[arg-type]
        ws_row.addWidget(clone_btn)
        layout.addLayout(ws_row)
        # Populate after construction so we don't block the UI.
        asyncio.ensure_future(self._reload_workspaces())

        # Transcript above, input below.
        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:12px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        layout.addWidget(self.transcript, stretch=1)

        # Pending attachments — files the user picked but hasn't sent.
        # Wrapped in a horizontal QScrollArea so spamming the paperclip
        # 50 times can't push the dialog wider than the screen and
        # clip the send button.
        self._pending_attachments: list[dict[str, Any]] = []
        self.attachments_row = QtWidgets.QHBoxLayout()
        self.attachments_row.setSpacing(6)
        self.attachments_row.setContentsMargins(0, 0, 0, 0)
        self.attachments_row.addStretch(1)
        chip_inner = QtWidgets.QWidget()
        chip_inner.setLayout(self.attachments_row)
        self._attachments_wrap = QtWidgets.QScrollArea()
        self._attachments_wrap.setWidgetResizable(True)
        self._attachments_wrap.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._attachments_wrap.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._attachments_wrap.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._attachments_wrap.setFixedHeight(36)
        self._attachments_wrap.setWidget(chip_inner)
        self._attachments_wrap.setVisible(False)
        layout.addWidget(self._attachments_wrap)

        # Bottom: paperclip + message input + send button.
        bottom = QtWidgets.QHBoxLayout()
        self.attach_btn = QtWidgets.QPushButton("📎")
        self.attach_btn.setShortcut(QtGui.QKeySequence("Ctrl+Shift+A"))
        self.attach_btn.setToolTip(
            "Attach an image (.png/.jpg/.gif/.webp) or a spreadsheet "
            "(.xlsx/.xls/.csv) to the next message.  Spreadsheets are "
            "rendered as a markdown table; images pass through to the CLI.\n\n"
            "Shortcut: Ctrl+Shift+A.  You can also drag a file onto the "
            "input box."
        )
        self.attach_btn.setStyleSheet(
            "QPushButton{padding:10px 12px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:14px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.attach_btn.clicked.connect(self._attach_file)  # type: ignore[arg-type]
        bottom.addWidget(self.attach_btn)

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

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    _SUPPORTED_EXTS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".xlsx",
        ".xls",
        ".csv",
    }

    def _attach_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Attach a file to this conversation",
            "",
            _ATTACHMENT_FILTER,
        )
        if path:
            self._queue_local_attachment(Path(path))

    def _queue_local_attachment(self, p: Path) -> None:
        """Queue a local file for upload at next send.

        Shared by the paperclip dialog and the drag-drop handler.
        Skips unsupported extensions with a transcript warning.
        """
        if p.suffix.lower() not in self._SUPPORTED_EXTS:
            self._append(
                "Warning",
                f"unsupported file type {p.suffix!r}; supported: {sorted(self._SUPPORTED_EXTS)}",
            )
            return
        try:
            size = p.stat().st_size
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't read file", str(exc))
            return
        kind = (
            "image"
            if p.suffix.lower()
            in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
            }
            else "spreadsheet"
        )
        self._pending_attachments.append(
            {"local_path": str(p), "original_name": p.name, "kind": kind, "bytes": size}
        )
        self._render_pending_attachments()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(u.toLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        accepted = 0
        for u in urls:
            local = u.toLocalFile()
            if local:
                self._queue_local_attachment(Path(local))
                accepted += 1
        if accepted:
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _render_pending_attachments(self) -> None:
        while self.attachments_row.count() > 1:
            item = self.attachments_row.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        for att in self._pending_attachments:
            chip = self._build_chip(att)
            self.attachments_row.insertWidget(self.attachments_row.count() - 1, chip)
        self._attachments_wrap.setVisible(bool(self._pending_attachments))

    def _build_chip(self, att: dict[str, Any]) -> QtWidgets.QWidget:
        chip = QtWidgets.QFrame()
        chip.setStyleSheet(
            "QFrame{background:#eef4ff;border:1px solid #c8d6f0;border-radius:10px;}"
            "QLabel{color:#1f3a6a;font-size:11px;}"
            "QPushButton{border:none;color:#5b6068;background:transparent;font-size:14px;}"
            "QPushButton:hover{color:#c0392b;}"
        )
        h = QtWidgets.QHBoxLayout(chip)
        h.setContentsMargins(8, 2, 4, 2)
        h.setSpacing(4)
        icon = "🖼" if att.get("kind") == "image" else "📊"
        kb = max(1, int(att.get("bytes", 0)) // 1024)
        h.addWidget(QtWidgets.QLabel(f"{icon} {att.get('original_name', '?')} · {kb} KB"))
        rm = QtWidgets.QPushButton("✕")
        rm.setFixedSize(18, 18)
        rm.clicked.connect(lambda _=False, a=att: self._remove_attachment(a))  # type: ignore[arg-type]
        h.addWidget(rm)
        return chip

    def _remove_attachment(self, att: dict[str, Any]) -> None:
        self._pending_attachments = [a for a in self._pending_attachments if a is not att]
        self._render_pending_attachments()
        # If the attachment was already uploaded server-side, delete it.
        # Surface failures as a toast so the operator notices an
        # orphan staying behind on their disk; the previous code
        # silently swallowed everything.
        if att.get("id") and self._agent_id:
            asyncio.ensure_future(self._delete_remote_attachment(att, self._agent_id))

    async def _delete_remote_attachment(self, att: dict[str, Any], agent_id: str) -> None:
        try:
            await self.client.call(
                "attachments.delete",
                {"id": att["id"], "agent_id": agent_id},
            )
        except Exception as exc:
            self._append("Warning", f"could not delete {att.get('original_name', '?')}: {exc}")

    async def _upload_pending_for_agent(self, agent_id: str) -> list[str]:
        """Upload every cached local file and return the resulting
        attachment ids in order.  Files that fail to upload are
        skipped with a chat-side warning.

        Read + base64-encode happen in a thread so a multi-MB file
        doesn't freeze the qasync event loop.
        """
        ids: list[str] = []
        for att in list(self._pending_attachments):
            if att.get("id"):
                ids.append(att["id"])
                continue
            local = att.get("local_path", "")
            try:
                data = await asyncio.to_thread(Path(local).read_bytes)
            except OSError as exc:
                self._append("Warning", f"could not read {local}: {exc}")
                continue
            content_b64 = await asyncio.to_thread(
                lambda d=data: base64.b64encode(d).decode("ascii")
            )
            try:
                res = await self.client.call(
                    "attachments.upload",
                    {
                        "agent_id": agent_id,
                        "original_name": att.get("original_name", "upload"),
                        "content_b64": content_b64,
                    },
                )
            except Exception as exc:
                self._append("Warning", f"upload failed for {att.get('original_name')}: {exc}")
                continue
            att["id"] = res.get("id")
            if res.get("warning"):
                self._append("Warning", f"{att.get('original_name')}: {res['warning']}")
            if att["id"]:
                ids.append(att["id"])
        return ids

    def _send(self) -> None:
        text = self.message_input.toPlainText().strip()
        # Attachment-only send: synthesise a short user message so the
        # backend has something to record and the auto-name has a
        # filename to derive from, rather than silently doing nothing.
        if not text and not self._pending_attachments:
            return
        if not text:
            names = ", ".join(a.get("original_name", "?") for a in self._pending_attachments)
            text = f"Please review the attached file{'s' if len(self._pending_attachments) > 1 else ''}: {names}"
        self._history.append({"role": "user", "content": text})
        if self._pending_attachments:
            names = ", ".join(a.get("original_name", "?") for a in self._pending_attachments)
            self._append("You", f"{text}\n[attached: {names}]")
        else:
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
            ws_id = self.workspace_combo.currentData() or None
            try:
                created = await self.client.call(
                    "agents.create",
                    {
                        "name": _auto_name_from(message, label, self._pending_attachments),
                        "provider": provider,
                        "model": model,
                        "system": system,
                        "workspace_id": ws_id,
                    },
                )
                self._agent_id = created["id"]
            except Exception as exc:
                self._append("Error", f"could not create agent: {exc}")
                self.send_btn.setEnabled(True)
                return

        # Upload any pending attachments now that we have an agent.
        attachment_ids = await self._upload_pending_for_agent(self._agent_id)

        try:
            res = await self.client.call(
                "agents.send",
                {
                    "agent_id": self._agent_id,
                    "message": message,
                    "attachment_ids": attachment_ids,
                },
            )
            reply = res.get("reply", "")
        except Exception as exc:
            self._append("Error", str(exc))
            self.send_btn.setEnabled(True)
            return
        # Clear the chip row — these are bound to the just-sent turn.
        self._pending_attachments = []
        self._render_pending_attachments()
        self._history.append({"role": "assistant", "content": reply})
        self._append(_label_for(provider, model), reply or "(empty reply)")
        self.send_btn.setEnabled(True)

    def _new_chat(self) -> None:
        # Clearing the in-memory mirror is enough — the previous
        # session's Agent stays in the Conversations palette so it
        # can still be resumed from the canvas or the Agents tab.
        # The next send mints a fresh Agent.
        # Pending attachments must be wiped too, otherwise:
        #  - chips with no `id` (cached local paths) would silently
        #    rebind to the next agent's first send, leaking files
        #    across agents.
        #  - chips with an `id` (already uploaded) belong to the
        #    *previous* agent's directory and would get rejected by
        #    the cross-agent auth check on send anyway.
        prev_agent_id = self._agent_id
        for att in list(self._pending_attachments):
            att_id = att.get("id")
            if att_id and prev_agent_id:
                asyncio.ensure_future(
                    self._delete_remote_attachment(
                        {"id": att_id, "original_name": att.get("original_name", "?")},
                        prev_agent_id,
                    )
                )
        self._pending_attachments = []
        self._render_pending_attachments()
        self._agent_id = None
        self._history.clear()
        self.transcript.clear()
        self.message_input.clear()

    # ------------------------------------------------------------------
    # Workspaces (project repos)
    # ------------------------------------------------------------------

    async def _reload_workspaces(self, *, select_id: str | None = None) -> None:
        try:
            rows = await self.client.call("workspaces.list", {})
        except Exception:
            return
        prev = select_id or (self.workspace_combo.currentData() or "")
        self.workspace_combo.blockSignals(True)
        self.workspace_combo.clear()
        self.workspace_combo.addItem("(no repo — chat only)", "")
        for w in rows:
            label = f"{w.get('name', '?')} — {w.get('repo_path', '?')}"
            self.workspace_combo.addItem(label, w.get("id", ""))
        # Restore prior selection if it still exists.
        idx = 0
        for i in range(self.workspace_combo.count()):
            if self.workspace_combo.itemData(i) == prev:
                idx = i
                break
        self.workspace_combo.setCurrentIndex(idx)
        self.workspace_combo.blockSignals(False)

    def _add_repo(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Pick the project repo to give the agent access to",
            str(Path.home()),
        )
        if not path:
            return
        asyncio.ensure_future(self._register_repo(Path(path)))

    async def _register_repo(self, path: Path) -> None:
        try:
            ws = await self.client.call(
                "workspaces.register",
                {"path": str(path), "name": path.name},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't register repo", str(exc))
            return
        await self._reload_workspaces(select_id=ws.get("id"))

    def _clone_repo(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Clone from git")
        dlg.resize(520, 220)
        form = QtWidgets.QFormLayout(dlg)

        url_input = QtWidgets.QLineEdit()
        url_input.setPlaceholderText("https://github.com/owner/repo.git")
        form.addRow("Git URL:", url_input)

        branch_input = QtWidgets.QLineEdit()
        branch_input.setPlaceholderText("(default — leave blank for repo's default branch)")
        form.addRow("Branch:", branch_input)

        depth_input = QtWidgets.QLineEdit()
        depth_input.setPlaceholderText("(optional — e.g. 1 for shallow)")
        form.addRow("Depth:", depth_input)

        info = QtWidgets.QLabel(
            "Clones into AgentOrchestra's data directory.  This may "
            "take a minute or two for large repos.  HTTPS URLs use your "
            "git credential helper; SSH URLs use your SSH key."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#5b6068;font-size:11px;")
        form.addRow(info)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Clone")
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        form.addRow(buttons)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        url = url_input.text().strip()
        if not url:
            return
        branch = branch_input.text().strip() or None
        depth_str = depth_input.text().strip()
        depth: int | None = None
        if depth_str:
            try:
                depth = int(depth_str)
            except ValueError:
                QtWidgets.QMessageBox.warning(self, "Invalid depth", "Depth must be an integer.")
                return
        asyncio.ensure_future(self._do_clone(url, branch, depth))

    async def _do_clone(self, url: str, branch: str | None, depth: int | None) -> None:
        # Show a non-modal busy chip in the transcript.  Cloning a real
        # repo is slow; the operator should see something happening.
        self._append("System", f"Cloning {url} … (this may take a minute)")
        try:
            ws = await self.client.call(
                "workspaces.clone",
                {"url": url, "branch": branch, "depth": depth},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Clone failed", str(exc))
            self._append("Error", f"clone failed: {exc}")
            return
        self._append(
            "System",
            f"Cloned: {ws.get('name', '?')} at {ws.get('repo_path', '?')}",
        )
        await self._reload_workspaces(select_id=ws.get("id"))

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


def _auto_name_from(
    message: str,
    model_label: str,
    attachments: list[dict[str, Any]] | None = None,
) -> str:
    """Produce a short browsable name from the user's first message.

    Truncates to ~50 chars and strips newlines so the Conversations
    palette stays scannable.  When the message is essentially empty
    but a file is attached, name the agent after the file so the
    palette still reads as something specific (not the model label).
    """
    one_line = " ".join(message.split())
    # Strip the synthetic "Please review the attached file: x.png"
    # prefix we mint for attachment-only sends so the agent name is
    # the filename rather than that boilerplate.
    if attachments and one_line.lower().startswith("please review the attached"):
        one_line = ""
    if not one_line:
        if attachments:
            first = attachments[0].get("original_name", "")
            if first:
                trimmed = first if len(first) <= 50 else first[:47] + "…"
                return f"📎 {trimmed}"
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
