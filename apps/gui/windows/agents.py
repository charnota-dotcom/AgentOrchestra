"""Agents tab — named, persistent conversations with follow-up linkage.

Layout (left → right):

* Sidebar — list of agents, ordered by recency.  "+" creates a new
  one; clicking an agent loads its transcript.
* Centre — transcript of the selected agent + multi-line message
  input.  Send extends the conversation; the model sees the full
  history.
* Right — "Spawn follow-up" panel.  Pick a preset (Summarise /
  Annotate / Deep dive / Critique / Verify / Custom), give the new
  agent a name, click Spawn.  The new agent receives the parent's
  transcript as context plus a preset-driven instruction.

Routes through the same subscription-only providers as Chat:
``claude-cli`` and ``gemini-cli``.  No API keys.
"""

from __future__ import annotations

import asyncio
import base64
import html as _html
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.presets import MODE_CODING, MODEL_PRESETS, ModelPreset

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


_ATTACHMENT_FILTER = (
    "Supported files (*.png *.jpg *.jpeg *.gif *.webp *.xlsx *.xls *.csv);;"
    "Images (*.png *.jpg *.jpeg *.gif *.webp);;"
    "Spreadsheets (*.xlsx *.xls *.csv);;"
    "All files (*)"
)
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


# Coding-mode subset of the shared MODEL_PRESETS — the Agents tab's
# "+ New agent" dialog is for spawning a worker without the chat-style
# mode + thinking + skills pickers.  Operators who want those should
# use the canvas New-Conversation dialog or the Chat tab.  Showing the
# full 12-row matrix here would be confusing without those companion
# fields.  Annotated with the element type so mypy / Pyright can still
# check `chosen.model` lookups downstream.
_AGENTS_TAB_PRESETS: tuple[ModelPreset, ...] = tuple(
    p for p in MODEL_PRESETS if p.mode == MODE_CODING
)
# Loud import-time check — if the Coding mode constant ever drifts or
# gets renamed and this list ends up empty, the dialog would silently
# open with zero rows and IndexError on accept.  ``assert`` is stripped
# under ``python -O`` / ``PYTHONOPTIMIZE=1``, so we use an explicit
# ``raise`` to keep the defence under optimised launches.
if not _AGENTS_TAB_PRESETS:
    raise RuntimeError(
        "apps.gui.presets has no Coding-mode entries; the Agents tab dialog depends on at least one"
    )


class AgentsPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._agents: list[dict[str, Any]] = []
        self._current_agent: dict[str, Any] | None = None
        self._presets: list[dict[str, str]] = []
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar(), stretch=0)
        layout.addWidget(self._build_centre(), stretch=1)
        layout.addWidget(self._build_followup(), stretch=0)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload_all()))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("background:#fff;border-right:1px solid #e6e7eb;")
        wrap.setFixedWidth(240)
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Agents")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        header.addWidget(title)
        header.addStretch(1)
        new_btn = QtWidgets.QPushButton("+ New")
        new_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#f6f8fa;font-size:12px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        new_btn.clicked.connect(self._new_agent_dialog)  # type: ignore[arg-type]
        header.addWidget(new_btn)
        v.addLayout(header)

        self.agent_list = QtWidgets.QListWidget()
        self.agent_list.setStyleSheet(
            "QListWidget{border:none;background:transparent;}"
            "QListWidget::item{padding:8px 6px;border-radius:4px;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )
        self.agent_list.currentRowChanged.connect(self._on_agent_selected)  # type: ignore[arg-type]
        v.addWidget(self.agent_list, stretch=1)

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
        # Drag-and-drop accepts onto the whole centre pane so an
        # operator can drop a file from File Explorer.
        wrap.setAcceptDrops(True)
        wrap.dragEnterEvent = self._drag_enter_event  # type: ignore[method-assign]
        wrap.dropEvent = self._drop_event  # type: ignore[method-assign]
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        self.title = QtWidgets.QLabel("(no agent selected)")
        self.title.setStyleSheet("font-size:18px;font-weight:600;color:#0f1115;")
        v.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(self.subtitle)

        # Workspace banner — mirrors the canvas chat dialog.  Green
        # when bound, grey when chat-only.  Change repo button opens
        # the same picker the canvas dialog uses.
        ws_row = QtWidgets.QHBoxLayout()
        self.workspace_label = QtWidgets.QLabel("📂 No repo bound — chat-only conversation")
        self.workspace_label.setStyleSheet(
            "color:#1f7a3f;font-size:11px;background:#e9f8ee;"
            "border:1px solid #c7e8d3;border-radius:4px;padding:4px 8px;"
        )
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setVisible(False)
        ws_row.addWidget(self.workspace_label, stretch=1)
        self.change_repo_btn = QtWidgets.QPushButton("Change repo")
        self.change_repo_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.change_repo_btn.setToolTip(
            "Bind / unbind this conversation to a project repo.  When "
            "bound, the CLI runs inside that directory and can read, "
            "search, and edit files using its built-in tools."
        )
        self.change_repo_btn.clicked.connect(self._change_workspace)  # type: ignore[arg-type]
        self.change_repo_btn.setVisible(False)
        ws_row.addWidget(self.change_repo_btn)
        v.addLayout(ws_row)

        # Live git-status banner — shown only when workspace is a git
        # repo.  Refreshed on agent select + after each send.
        gs_row = QtWidgets.QHBoxLayout()
        self.git_status_label = QtWidgets.QLabel("")
        self.git_status_label.setStyleSheet(
            "color:#5b6068;font-size:11px;background:#f7f8fa;"
            "border:1px solid #e6e7eb;border-radius:4px;padding:3px 8px;"
        )
        self.git_status_label.setWordWrap(True)
        self.git_status_label.setVisible(False)
        gs_row.addWidget(self.git_status_label, stretch=1)
        self.switch_branch_btn = QtWidgets.QPushButton("Switch branch")
        self.switch_branch_btn.setStyleSheet(
            "QPushButton{padding:3px 8px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.switch_branch_btn.setToolTip(
            "Switch this workspace to a different git branch.  "
            "Optionally creates the branch if it doesn't exist."
        )
        self.switch_branch_btn.clicked.connect(self._switch_branch)  # type: ignore[arg-type]
        self.switch_branch_btn.setVisible(False)
        gs_row.addWidget(self.switch_branch_btn)
        v.addLayout(gs_row)

        # References row — count + Edit button.
        refs_row = QtWidgets.QHBoxLayout()
        self.refs_label = QtWidgets.QLabel("References: none")
        self.refs_label.setStyleSheet("color:#5b6068;font-size:11px;")
        self.refs_label.setWordWrap(True)
        refs_row.addWidget(self.refs_label, stretch=1)
        self.edit_refs_btn = QtWidgets.QPushButton("Edit references")
        self.edit_refs_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.edit_refs_btn.setToolTip(
            "Pick other conversations whose full transcripts should be "
            "inlined into this agent's prompt as read-only context."
        )
        self.edit_refs_btn.clicked.connect(self._edit_references)  # type: ignore[arg-type]
        self.edit_refs_btn.setVisible(False)
        refs_row.addWidget(self.edit_refs_btn)
        v.addLayout(refs_row)

        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:12px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        v.addWidget(self.transcript, stretch=1)

        # Pending attachments — chip-row scroll area, hidden when empty.
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
        v.addWidget(self._attachments_wrap)

        bottom = QtWidgets.QHBoxLayout()
        self.attach_btn = QtWidgets.QPushButton("📎")
        self.attach_btn.setShortcut(QtGui.QKeySequence("Ctrl+Shift+A"))
        self.attach_btn.setToolTip(
            "Attach an image (.png/.jpg/.gif/.webp) or a spreadsheet "
            "(.xlsx/.xls/.csv) to the next message.  Spreadsheets are "
            "rendered as a markdown table; images pass through to the CLI.\n\n"
            "Shortcut: Ctrl+Shift+A.  You can also drag a file onto the pane."
        )
        self.attach_btn.setStyleSheet(
            "QPushButton{padding:10px 12px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:14px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.attach_btn.clicked.connect(self._attach_file)  # type: ignore[arg-type]
        self.attach_btn.setEnabled(False)
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
        self.send_btn.clicked.connect(self._send_message)  # type: ignore[arg-type]
        self.send_btn.setEnabled(False)
        bottom.addWidget(self.send_btn)
        v.addLayout(bottom)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.message_input)
        shortcut.activated.connect(self._send_message)  # type: ignore[arg-type]
        return wrap

    # ------------------------------------------------------------------
    # Right — spawn follow-up
    # ------------------------------------------------------------------

    def _build_followup(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("background:#fff;border-left:1px solid #e6e7eb;")
        wrap.setFixedWidth(280)
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        v.addWidget(self._heading("Spawn follow-up"))
        v.addWidget(
            self._small(
                "Create a new agent that builds on the selected "
                "agent's conversation.  Pick a preset action or "
                "write your own."
            )
        )

        v.addWidget(self._small("Preset"))
        self.preset_combo = QtWidgets.QComboBox()
        v.addWidget(self.preset_combo)

        v.addWidget(self._small("Custom instruction (used when preset = Custom)"))
        self.custom_input = QtWidgets.QPlainTextEdit()
        self.custom_input.setMinimumHeight(80)
        v.addWidget(self.custom_input)

        v.addWidget(self._small("Name for the new agent"))
        self.followup_name = QtWidgets.QLineEdit()
        self.followup_name.setPlaceholderText("e.g. Smith Critique")
        v.addWidget(self.followup_name)

        v.addStretch(1)
        self.spawn_btn = QtWidgets.QPushButton("Spawn follow-up")
        self.spawn_btn.setStyleSheet(
            "QPushButton{padding:8px 14px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        self.spawn_btn.clicked.connect(self._spawn_followup)  # type: ignore[arg-type]
        self.spawn_btn.setEnabled(False)
        v.addWidget(self.spawn_btn)
        return wrap

    @staticmethod
    def _heading(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("font-size:13px;font-weight:600;color:#0f1115;")
        return lbl

    @staticmethod
    def _small(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#5b6068;font-size:11px;")
        lbl.setWordWrap(True)
        return lbl

    # ------------------------------------------------------------------
    # Data flow
    # ------------------------------------------------------------------

    async def _reload_all(self) -> None:
        await asyncio.gather(self._reload_agents(), self._load_presets())

    async def _reload_agents(self) -> None:
        try:
            self._agents = await self.client.call("agents.list", {})
        except Exception:
            self._agents = []
        self.agent_list.clear()
        for a in self._agents:
            label = a.get("name", "?")
            parent_name = a.get("parent_name")
            if parent_name:
                label = f"{label}  ↩ {parent_name}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, a.get("id"))
            self.agent_list.addItem(item)

    async def _load_presets(self) -> None:
        try:
            presets = await self.client.call("agents.followup_presets", {})
        except Exception:
            presets = []
        self._presets = presets
        self.preset_combo.clear()
        for p in presets:
            self.preset_combo.addItem(p.get("label", "?"))

    def _on_agent_selected(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._agents):
            self._current_agent = None
            self.send_btn.setEnabled(False)
            self.spawn_btn.setEnabled(False)
            self.attach_btn.setEnabled(False)
            self.change_repo_btn.setVisible(False)
            self.workspace_label.setVisible(False)
            self.git_status_label.setVisible(False)
            self.switch_branch_btn.setVisible(False)
            self.edit_refs_btn.setVisible(False)
            self.refs_label.setText("References: none")
            return
        self._current_agent = self._agents[idx]
        self.send_btn.setEnabled(True)
        self.spawn_btn.setEnabled(True)
        self.attach_btn.setEnabled(True)
        self.change_repo_btn.setVisible(True)
        self.edit_refs_btn.setVisible(True)
        # Wipe any pending attachments left over from the previous
        # selection — they belonged to that agent, not this one.
        self._pending_attachments = []
        self._render_pending_attachments()
        self._render_agent(self._current_agent)
        # Refresh git status for the newly-selected agent's workspace.
        asyncio.ensure_future(self._refresh_git_status())

    def _render_agent(self, agent: dict[str, Any]) -> None:
        self.title.setText(agent.get("name", "?"))
        sub = (
            f"{agent.get('model', '?')} · {agent.get('provider', '?')}"
            f" · {len(agent.get('transcript') or [])} turns"
        )
        if agent.get("parent_name"):
            sub += f"  ·  follow-up of {agent['parent_name']}"
        self.subtitle.setText(sub)
        # Workspace banner — same shape as the canvas chat dialog.
        self.workspace_label.setText(self._format_workspace_label(agent))
        self.workspace_label.setVisible(True)
        # References label.
        ref_ids = agent.get("reference_agent_ids") or []
        self.refs_label.setText(
            f"References: {len(ref_ids)} other conversation(s) inlined as context"
            if ref_ids
            else "References: none"
        )
        self.transcript.clear()
        for turn in agent.get("transcript", []):
            who = "You" if turn.get("role") == "user" else agent.get("name", "Agent")
            self.transcript.appendPlainText(f"{who}:\n{turn.get('content', '')}\n")

    @staticmethod
    def _format_workspace_label(agent: dict[str, Any]) -> str:
        """Plain-rich-text banner showing the bound repo, html-escaped
        so an attacker-controlled workspace name can't render as HTML.
        Identical contract to the canvas chat dialog's helper.
        """
        ws_id = agent.get("workspace_id")
        ws_name = _html.escape(str(agent.get("workspace_name") or ""))
        ws_path = _html.escape(str(agent.get("workspace_path") or ""))
        if not ws_id:
            return "📂 No repo bound — chat-only conversation"
        if ws_name and ws_path:
            return f"📂 Working in: <b>{ws_name}</b> ({ws_path})"
        if ws_path:
            return f"📂 Working in: {ws_path}"
        return "📂 Repo bound (id only — refresh to load details)"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _new_agent_dialog(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("New agent")
        form = QtWidgets.QFormLayout(dlg)
        name = QtWidgets.QLineEdit()
        name.setPlaceholderText("e.g. Agent Smith")
        form.addRow("Name:", name)
        model = QtWidgets.QComboBox()
        for preset in _AGENTS_TAB_PRESETS:
            model.addItem(preset.display())
        form.addRow("Model:", model)
        system = QtWidgets.QPlainTextEdit()
        system.setPlaceholderText("Optional system prompt — set the agent's persona / role.")
        system.setMinimumHeight(80)
        form.addRow("System:", system)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        form.addRow(buttons)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        idx = model.currentIndex()
        if idx < 0 or idx >= len(_AGENTS_TAB_PRESETS):
            # Defensive: combo was rebuilt or model emptied between
            # show + accept.  Refuse rather than IndexError into the
            # user's face.
            return
        chosen = _AGENTS_TAB_PRESETS[idx]
        provider, model_name = chosen.provider, chosen.model
        asyncio.ensure_future(
            self._do_create(
                name.text().strip() or "Unnamed agent",
                provider,
                model_name,
                system.toPlainText().strip(),
            )
        )

    async def _do_create(self, name: str, provider: str, model: str, system: str) -> None:
        try:
            agent = await self.client.call(
                "agents.create",
                {"name": name, "provider": provider, "model": model, "system": system},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Create failed", str(exc))
            return
        await self._reload_agents()
        # Select the new one (it's at the top of the recency-sorted list).
        for i, a in enumerate(self._agents):
            if a.get("id") == agent.get("id"):
                self.agent_list.setCurrentRow(i)
                break

    def _send_message(self) -> None:
        if self._current_agent is None:
            return
        text = self.message_input.toPlainText().strip()
        # Allow attachment-only sends — synthesise a short message so
        # the backend has something to record (mirrors Chat tab behaviour).
        if not text and not self._pending_attachments:
            return
        if not text:
            names = ", ".join(a.get("original_name", "?") for a in self._pending_attachments)
            text = (
                f"Please review the attached file"
                f"{'s' if len(self._pending_attachments) > 1 else ''}: {names}"
            )
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        # Optimistic append so the user sees their message immediately.
        att_summary = ""
        if self._pending_attachments:
            names = ", ".join(a.get("original_name", "?") for a in self._pending_attachments)
            att_summary = f"\n[attached: {names}]"
        self.transcript.appendPlainText(f"You:\n{text}{att_summary}\n")
        asyncio.ensure_future(self._do_send(self._current_agent["id"], text))

    async def _do_send(self, agent_id: str, message: str) -> None:
        # Upload any pending attachments before the send so we can
        # pass attachment_ids through agents.send.
        attachment_ids = await self._upload_pending_for_agent(agent_id)
        try:
            res = await self.client.call(
                "agents.send",
                {
                    "agent_id": agent_id,
                    "message": message,
                    "attachment_ids": attachment_ids,
                },
            )
        except Exception as exc:
            self.transcript.appendPlainText(f"Error:\n{exc}\n")
            self.send_btn.setEnabled(True)
            return
        # Pending attachments are now bound to the just-sent turn.
        self._pending_attachments = []
        self._render_pending_attachments()
        # Refresh both the list (so updated_at reorders the agent to
        # the top) and the open transcript.
        agent = res.get("agent", {})
        self._current_agent = agent
        self._render_agent(agent)
        await self._reload_agents()
        # Re-select the same agent after reload.
        for i, a in enumerate(self._agents):
            if a.get("id") == agent.get("id"):
                self.agent_list.setCurrentRow(i)
                break
        self.send_btn.setEnabled(True)
        # Repo-aware path: agent may have run git ops; refresh banner.
        await self._refresh_git_status()

    def _spawn_followup(self) -> None:
        if self._current_agent is None:
            return
        idx = self.preset_combo.currentIndex()
        if idx < 0 or idx >= len(self._presets):
            return
        preset = self._presets[idx]["key"]
        custom = self.custom_input.toPlainText().strip()
        if preset == "custom" and not custom:
            QtWidgets.QMessageBox.warning(
                self,
                "Spawn failed",
                "Pick a preset or enter a custom instruction.",
            )
            return
        name = self.followup_name.text().strip()
        asyncio.ensure_future(self._do_spawn(self._current_agent["id"], name, preset, custom))

    async def _do_spawn(self, parent_id: str, name: str, preset: str, custom: str) -> None:
        try:
            res = await self.client.call(
                "agents.spawn_followup",
                {
                    "parent_id": parent_id,
                    "name": name,
                    "preset": preset,
                    "custom": custom,
                },
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Spawn failed", str(exc))
            return
        new_agent = res.get("agent", {})
        await self._reload_agents()
        for i, a in enumerate(self._agents):
            if a.get("id") == new_agent.get("id"):
                self.agent_list.setCurrentRow(i)
                break
        self.followup_name.clear()
        self.custom_input.clear()
        # Auto-trigger the first turn so the user sees the follow-up
        # response without an extra click — the seeded transcript
        # already contains the instruction so we send an empty
        # nudge.
        asyncio.ensure_future(self._do_send(new_agent["id"], "Begin."))

    def _delete_selected(self) -> None:
        if self._current_agent is None:
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Delete agent",
            f"Delete '{self._current_agent.get('name', '?')}' and its transcript?",
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        asyncio.ensure_future(self._do_delete(self._current_agent["id"]))

    async def _do_delete(self, agent_id: str) -> None:
        try:
            await self.client.call("agents.delete", {"id": agent_id})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self._current_agent = None
        await self._reload_agents()
        self.title.setText("(no agent selected)")
        self.subtitle.setText("")
        self.transcript.clear()
        self.send_btn.setEnabled(False)
        self.spawn_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Workspace binding (mirrors apps/gui/canvas/chat_dialog.py)
    # ------------------------------------------------------------------

    def _change_workspace(self) -> None:
        if self._current_agent is None:
            return
        asyncio.ensure_future(self._open_workspace_dialog())

    async def _open_workspace_dialog(self) -> None:
        if self._current_agent is None:
            return
        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't load workspaces", str(exc))
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Repo for {self._current_agent.get('name', '?')}")
        dlg.resize(520, 320)
        v = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel(
            "When a repo is selected, the CLI runs with cwd set to that "
            "directory.  The model can use its built-in Read / Bash / "
            "Edit / Grep tools against the project."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(info)

        listw = QtWidgets.QListWidget()
        none_item = QtWidgets.QListWidgetItem("(no repo — chat-only)")
        none_item.setData(QtCore.Qt.ItemDataRole.UserRole, "")
        listw.addItem(none_item)
        for w in workspaces:
            item = QtWidgets.QListWidgetItem(f"{w.get('name', '?')}  ·  {w.get('repo_path', '?')}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, w.get("id", ""))
            listw.addItem(item)
        current_id = self._current_agent.get("workspace_id") or ""
        for i in range(listw.count()):
            it = listw.item(i)
            if it is not None and it.data(QtCore.Qt.ItemDataRole.UserRole) == current_id:
                listw.setCurrentRow(i)
                break
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
        sel = listw.currentItem()
        if sel is None:
            return
        chosen = str(sel.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        try:
            updated = await self.client.call(
                "agents.set_workspace",
                {"agent_id": self._current_agent["id"], "workspace_id": chosen or None},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't bind repo", str(exc))
            return
        # The RPC enriches with workspace_name + workspace_path so the
        # banner just re-renders cleanly.
        self._current_agent = updated
        self.workspace_label.setText(self._format_workspace_label(updated))
        await self._refresh_git_status()

    # ------------------------------------------------------------------
    # Git status banner + branch switch
    # ------------------------------------------------------------------

    async def _refresh_git_status(self) -> None:
        if self._current_agent is None:
            self.git_status_label.setVisible(False)
            self.switch_branch_btn.setVisible(False)
            return
        ws_id = self._current_agent.get("workspace_id") or ""
        if not ws_id:
            self.git_status_label.setVisible(False)
            self.switch_branch_btn.setVisible(False)
            return
        try:
            res = await self.client.call("workspaces.git_status", {"workspace_id": ws_id})
        except Exception as exc:
            self.git_status_label.setText(f"⚠ git status failed: {_html.escape(str(exc))}")
            self.git_status_label.setVisible(True)
            self.switch_branch_btn.setVisible(False)
            return
        if not res.get("is_git"):
            self.git_status_label.setText(
                "📁 not a git repo — file tools available, no branch tracking"
            )
            self.git_status_label.setVisible(True)
            self.switch_branch_btn.setVisible(False)
            return
        # Escape every server-derived string.
        branch = _html.escape(str(res.get("branch", "?")))
        ahead = res.get("ahead", 0)
        behind = res.get("behind", 0)
        modified = res.get("modified", 0)
        staged = res.get("staged", 0)
        untracked = res.get("untracked", 0)
        last_sha = _html.escape(str(res.get("last_commit_sha", "")))
        last_subj_raw = str(res.get("last_commit_subject", ""))
        dirty_bits: list[str] = []
        if modified:
            dirty_bits.append(f"{modified} modified")
        if staged:
            dirty_bits.append(f"{staged} staged")
        if untracked:
            dirty_bits.append(f"{untracked} untracked")
        clean_marker = " · clean" if not dirty_bits else " · " + ", ".join(dirty_bits)
        ahead_marker = ""
        if ahead:
            ahead_marker += f" ↑{ahead}"
        if behind:
            ahead_marker += f" ↓{behind}"
        text = f"git: <b>{branch}</b>{ahead_marker}{clean_marker}"
        if last_sha:
            short_subj_raw = last_subj_raw if len(last_subj_raw) <= 60 else last_subj_raw[:57] + "…"
            text += f"  ·  last: {last_sha} {_html.escape(short_subj_raw)}"
        self.git_status_label.setText(text)
        self.git_status_label.setVisible(True)
        self.switch_branch_btn.setVisible(True)

    def _switch_branch(self) -> None:
        if self._current_agent is None:
            return
        ws_id = self._current_agent.get("workspace_id") or ""
        if not ws_id:
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Switch branch",
            "Branch to switch to (prefix with `+` to create):",
        )
        if not ok or not text.strip():
            return
        branch = text.strip()
        create = False
        if branch.startswith("+"):
            create = True
            branch = branch[1:].strip()
        if not branch:
            return
        asyncio.ensure_future(self._do_switch(ws_id, branch, create))

    async def _do_switch(self, ws_id: str, branch: str, create: bool) -> None:
        try:
            await self.client.call(
                "workspaces.switch_branch",
                {"workspace_id": ws_id, "branch": branch, "create": create},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't switch branch", str(exc))
            return
        self.transcript.appendPlainText(
            f"System: switched to branch '{branch}'" + (" (created)" if create else "") + "\n"
        )
        await self._refresh_git_status()

    # ------------------------------------------------------------------
    # References (cross-chat context inlining)
    # ------------------------------------------------------------------

    def _edit_references(self) -> None:
        if self._current_agent is None:
            return
        asyncio.ensure_future(self._open_refs_dialog())

    async def _open_refs_dialog(self) -> None:
        if self._current_agent is None:
            return
        try:
            agents = await self.client.call("agents.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't load agents", str(exc))
            return
        candidates = [a for a in agents if a.get("id") != self._current_agent.get("id")]

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"References for {self._current_agent.get('name', '?')}")
        dlg.resize(520, 480)
        v = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel(
            "Tick the conversations whose full transcripts should be inlined "
            "as read-only context on every message you send.  Cross-provider "
            "is supported (Gemini reading a Claude chat) — references are "
            "passed as plain text."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(info)

        listw = QtWidgets.QListWidget()
        current_refs = set(self._current_agent.get("reference_agent_ids") or [])
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
                {"agent_id": self._current_agent["id"], "reference_agent_ids": chosen},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't save references", str(exc))
            return
        self._current_agent = updated
        ref_ids = updated.get("reference_agent_ids") or []
        self.refs_label.setText(
            f"References: {len(ref_ids)} other conversation(s) inlined as context"
            if ref_ids
            else "References: none"
        )

    # ------------------------------------------------------------------
    # Attachments (paperclip + chip row + drag-drop)
    # ------------------------------------------------------------------

    def _attach_file(self) -> None:
        if self._current_agent is None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Attach a file to this conversation",
            "",
            _ATTACHMENT_FILTER,
        )
        if not path:
            return
        self._queue_local_attachment(Path(path))

    def _queue_local_attachment(self, p: Path) -> None:
        if p.suffix.lower() not in _SUPPORTED_EXTS:
            self.transcript.appendPlainText(
                f"Warning: unsupported file type {p.suffix!r}; "
                f"supported: {sorted(_SUPPORTED_EXTS)}\n"
            )
            return
        try:
            size = p.stat().st_size
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't read file", str(exc))
            return
        # Mirror the canvas chat dialog's pre-check.  Server cap is 25 MB.
        if size > 25 * 1024 * 1024:
            QtWidgets.QMessageBox.warning(
                self,
                "File too large",
                f"{p.name} is {size // (1024 * 1024)} MB; max is 25 MB.",
            )
            return
        kind = (
            "image"
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            else "spreadsheet"
        )
        self._pending_attachments.append(
            {"local_path": str(p), "original_name": p.name, "kind": kind, "bytes": size}
        )
        self._render_pending_attachments()

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
        rm.setToolTip("Remove attachment")
        rm.setFixedSize(18, 18)
        rm.clicked.connect(lambda _=False, a=att: self._remove_attachment(a))  # type: ignore[arg-type]
        h.addWidget(rm)
        return chip

    def _remove_attachment(self, att: dict[str, Any]) -> None:
        self._pending_attachments = [a for a in self._pending_attachments if a is not att]
        self._render_pending_attachments()
        # If already uploaded server-side, clean up.  Best-effort.
        if att.get("id") and self._current_agent:
            agent_id = self._current_agent["id"]
            asyncio.ensure_future(self._delete_remote_attachment(att.get("id", ""), agent_id))

    async def _delete_remote_attachment(self, attachment_id: str, agent_id: str) -> None:
        if not attachment_id:
            return
        try:
            await self.client.call(
                "attachments.delete",
                {"id": attachment_id, "agent_id": agent_id},
            )
        except Exception as exc:
            self.transcript.appendPlainText(f"Warning: couldn't delete attachment ({exc})\n")

    async def _upload_pending_for_agent(self, agent_id: str) -> list[str]:
        """Read + base64-encode each cached local file in a thread so a
        multi-MB upload doesn't freeze the qasync event loop, then POST
        via attachments.upload.  Returns the list of attachment ids.
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
                self.transcript.appendPlainText(f"Warning: could not read {local}: {exc}\n")
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
                self.transcript.appendPlainText(
                    f"Warning: upload failed for {att.get('original_name')}: {exc}\n"
                )
                continue
            att["id"] = res.get("id")
            if res.get("warning"):
                self.transcript.appendPlainText(
                    f"Warning: {att.get('original_name')}: {res['warning']}\n"
                )
            if att["id"]:
                ids.append(att["id"])
        return ids

    # ------------------------------------------------------------------
    # Drag-and-drop on the centre pane
    # ------------------------------------------------------------------

    def _drag_enter_event(self, event: QtGui.QDragEnterEvent) -> None:
        if (
            self._current_agent
            and event.mimeData().hasUrls()
            and any(u.toLocalFile() for u in event.mimeData().urls())
        ):
            event.acceptProposedAction()

    def _drop_event(self, event: QtGui.QDropEvent) -> None:
        if self._current_agent is None:
            return
        urls = event.mimeData().urls()
        accepted = 0
        for u in urls:
            local = u.toLocalFile()
            if not local:
                continue
            self._queue_local_attachment(Path(local))
            accepted += 1
        if accepted:
            event.acceptProposedAction()
