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
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.presets import MODE_CODING, MODEL_PRESETS

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


# Coding-mode subset of the shared MODEL_PRESETS — the Agents tab's
# "+ New agent" dialog is for spawning a worker without the chat-style
# mode + thinking + skills pickers.  Operators who want those should
# use the canvas New-Conversation dialog or the Chat tab.  Showing the
# full 12-row matrix here would be confusing without those companion
# fields.
_AGENTS_TAB_PRESETS: tuple = tuple(p for p in MODEL_PRESETS if p.mode == MODE_CODING)
# Loud import-time check — if the Coding mode constant ever drifts or
# gets renamed and this list ends up empty, the dialog would silently
# open with zero rows and IndexError on accept.  Better to crash on
# launch than to mislead the operator at runtime.
assert _AGENTS_TAB_PRESETS, (
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
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        self.title = QtWidgets.QLabel("(no agent selected)")
        self.title.setStyleSheet("font-size:18px;font-weight:600;color:#0f1115;")
        v.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(self.subtitle)

        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
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
            return
        self._current_agent = self._agents[idx]
        self.send_btn.setEnabled(True)
        self.spawn_btn.setEnabled(True)
        self._render_agent(self._current_agent)

    def _render_agent(self, agent: dict[str, Any]) -> None:
        self.title.setText(agent.get("name", "?"))
        sub = (
            f"{agent.get('model', '?')} · {agent.get('provider', '?')}"
            f" · {len(agent.get('transcript') or [])} turns"
        )
        if agent.get("parent_name"):
            sub += f"  ·  follow-up of {agent['parent_name']}"
        self.subtitle.setText(sub)
        self.transcript.clear()
        for turn in agent.get("transcript", []):
            who = "You" if turn.get("role") == "user" else agent.get("name", "Agent")
            self.transcript.appendPlainText(f"{who}:\n{turn.get('content', '')}\n")

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
        if not text:
            return
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        # Optimistic append so the user sees their message immediately.
        self.transcript.appendPlainText(f"You:\n{text}\n")
        asyncio.ensure_future(self._do_send(self._current_agent["id"], text))

    async def _do_send(self, agent_id: str, message: str) -> None:
        try:
            res = await self.client.call(
                "agents.send",
                {"agent_id": agent_id, "message": message},
            )
        except Exception as exc:
            self.transcript.appendPlainText(f"Error:\n{exc}\n")
            self.send_btn.setEnabled(True)
            return
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
