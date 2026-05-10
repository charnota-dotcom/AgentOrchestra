"""Palette panel — drag source for new canvas nodes.

Two sections: control nodes (Trigger / Branch / Merge / Human /
Output), then agent cards loaded from the service.  Each row is
draggable; the canvas page reads the MIME data on drop and creates
the matching node.

V1 uses Qt's standard drag-and-drop with a custom MIME type so the
canvas can distinguish a palette drag from a normal selection drag.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.presets import (
    DEFAULT_MODEL_INDEX,
    DEFAULT_THINKING_INDEX,
    MODEL_PRESETS,
    THINKING_PRESETS,
    compose_system,
)

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

        # Persistent named conversations (Agents tab).  Drag onto the
        # canvas to anchor a conversation as a node; double-click the
        # node to open a chat dialog scoped to that one agent.
        conv_header = QtWidgets.QHBoxLayout()
        conv_header.setContentsMargins(0, 0, 0, 0)
        conv_header.addWidget(self._section_header("Conversations"), stretch=1)
        new_conv_btn = QtWidgets.QPushButton("+ New")
        new_conv_btn.setStyleSheet(
            "QPushButton{padding:2px 8px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#f6f8fa;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        new_conv_btn.setToolTip("Create a new named conversation without leaving the canvas.")
        new_conv_btn.clicked.connect(self._new_conversation_dialog)  # type: ignore[arg-type]
        conv_header.addWidget(new_conv_btn)
        layout.addLayout(conv_header)

        self.agents_list = _DragList()
        self.agents_list.setStyleSheet(self._list_stylesheet())
        layout.addWidget(self.agents_list, stretch=1)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload_all()))

    async def _reload_all(self) -> None:
        await asyncio.gather(self.reload_cards(), self.reload_agents())

    # New-conversation dialog so the operator can mint an agent
    # directly from the canvas with the *same* model / thinking / skills
    # picker the live Chat tab uses.  Mirrors that screen so a flow
    # drafted on the canvas behaves identically to the same prompt
    # typed into Chat.  On success the palette refreshes and the new
    # entry is draggable.
    def _new_conversation_dialog(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("New conversation")
        dlg.resize(560, 520)
        outer = QtWidgets.QVBoxLayout(dlg)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        header = QtWidgets.QLabel(
            "<b>New conversation</b><br/>"
            "<span style='color:#5b6068;font-size:11px;'>Same picker as the "
            "Chat tab — model, thinking depth, and skills all carry over to "
            "this agent's system prompt.</span>"
        )
        header.setTextFormat(QtCore.Qt.TextFormat.RichText)
        outer.addWidget(header)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        name_input = QtWidgets.QLineEdit()
        name_input.setPlaceholderText(
            "e.g. Agent Smith  (auto-generated from first message if blank)"
        )
        form.addRow("Name:", name_input)

        # Provider filter — quick way to narrow the model dropdown when
        # the operator only cares about Claude or Gemini.  "All" leaves
        # every preset visible.
        provider_filter = QtWidgets.QComboBox()
        provider_filter.addItem("All", "")
        provider_filter.addItem("Claude (claude-cli)", "claude-cli")
        provider_filter.addItem("Gemini (gemini-cli)", "gemini-cli")
        form.addRow("Provider:", provider_filter)

        # Model + mode picker.  Item data carries the index into
        # MODEL_PRESETS so we can recover the full ModelPreset on accept,
        # even after the visible list has been filtered.
        model_combo = QtWidgets.QComboBox()
        for i, preset in enumerate(MODEL_PRESETS):
            model_combo.addItem(preset.display(), i)
        model_combo.setCurrentIndex(DEFAULT_MODEL_INDEX)
        model_combo.setToolTip(
            "Each row is one (model + mode) cell.  Modes (Coding, General "
            "Chat, File / artifact, Image prompt) swap the system prompt "
            "without leaving the same provider, exactly like the Chat tab."
        )
        form.addRow("Model:", model_combo)

        def _filter_models(_idx: int) -> None:
            wanted = provider_filter.currentData() or ""
            saved_real_idx = model_combo.currentData()
            model_combo.blockSignals(True)
            model_combo.clear()
            for i, preset in enumerate(MODEL_PRESETS):
                if not wanted or preset.provider == wanted:
                    model_combo.addItem(preset.display(), i)
            # Try to restore the previous real index; if it was filtered
            # out, fall back to the first visible row.
            for ci in range(model_combo.count()):
                if model_combo.itemData(ci) == saved_real_idx:
                    model_combo.setCurrentIndex(ci)
                    break
            else:
                if model_combo.count() > 0:
                    model_combo.setCurrentIndex(0)
            model_combo.blockSignals(False)

        provider_filter.currentIndexChanged.connect(_filter_models)  # type: ignore[arg-type]

        # Thinking-depth picker.  Same ladder as the Chat tab.
        thinking_combo = QtWidgets.QComboBox()
        for tp in THINKING_PRESETS:
            thinking_combo.addItem(tp.label)
        thinking_combo.setCurrentIndex(DEFAULT_THINKING_INDEX)
        thinking_combo.setToolTip(
            "Tells the model how hard to think before answering.  Off keeps "
            "the prompt clean; Hard / Very hard ask for explicit reasoning."
        )
        form.addRow("Thinking:", thinking_combo)

        # Skills field — free-form `/foo /bar baz` that becomes a system
        # directive.  Matches the Chat tab's Skills input character-for-
        # character.
        skills_input = QtWidgets.QLineEdit()
        skills_input.setPlaceholderText(
            "Optional, e.g. /research-deep /cite-sources  (free-form, passed in the prompt)"
        )
        form.addRow("Skills:", skills_input)

        # Repo picker — bind the new agent to a Workspace so the CLI
        # runs inside that directory and can use its file tools.
        ws_combo = QtWidgets.QComboBox()
        ws_combo.addItem("(no repo — chat only)", "")
        ws_combo.setToolTip(
            "Pick a repo to give the agent file-tool access to.  "
            "Add… registers an existing local path; Clone… clones a git URL."
        )
        ws_row = QtWidgets.QHBoxLayout()
        ws_row.addWidget(ws_combo, stretch=1)
        add_repo_btn = QtWidgets.QPushButton("Add…")
        add_repo_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        add_repo_btn.setToolTip("Pick an existing local repo on disk.")
        add_repo_btn.clicked.connect(  # type: ignore[arg-type]
            lambda: self._palette_add_repo(dlg, ws_combo)
        )
        ws_row.addWidget(add_repo_btn)
        clone_btn = QtWidgets.QPushButton("Clone…")
        clone_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        clone_btn.setToolTip("Clone a remote git URL into a managed workspace.")
        clone_btn.clicked.connect(  # type: ignore[arg-type]
            lambda: self._palette_clone_repo(dlg, ws_combo)
        )
        ws_row.addWidget(clone_btn)
        ws_wrap = QtWidgets.QWidget()
        ws_wrap.setLayout(ws_row)
        form.addRow("Repo:", ws_wrap)
        # Populate asynchronously so the dialog appears immediately.
        asyncio.ensure_future(self._populate_workspaces(ws_combo))

        outer.addLayout(form)

        first_msg = QtWidgets.QPlainTextEdit()
        first_msg.setPlaceholderText(
            "Optional: a first message to send right after creation.  "
            "Without one, the agent is created and you double-click it on "
            "the canvas to start chatting."
        )
        first_msg.setMinimumHeight(90)
        outer.addWidget(QtWidgets.QLabel("First message:"))
        outer.addWidget(first_msg)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        outer.addWidget(buttons)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        # Recover the real preset via item data so the filter doesn't
        # silently shift our index.
        real_idx = model_combo.currentData()
        if real_idx is None or not (0 <= int(real_idx) < len(MODEL_PRESETS)):
            return
        preset = MODEL_PRESETS[int(real_idx)]
        thinking = THINKING_PRESETS[thinking_combo.currentIndex()]
        skills = skills_input.text().strip()
        # Same assembler the Chat tab uses → identical system prompt.
        system = compose_system(preset, thinking, skills)
        ws_id = ws_combo.currentData() or ""
        asyncio.ensure_future(
            self._do_create(
                (name_input.text() or "Unnamed conversation").strip(),
                preset.provider,
                preset.model,
                system,
                first_msg.toPlainText().strip(),
                ws_id,
            )
        )

    async def _populate_workspaces(
        self, combo: QtWidgets.QComboBox, *, select_id: str | None = None
    ) -> None:
        try:
            rows = await self.client.call("workspaces.list", {})
        except Exception:
            return
        prev = select_id or (combo.currentData() or "")
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(no repo — chat only)", "")
        for w in rows:
            label = f"{w.get('name', '?')} — {w.get('repo_path', '?')}"
            combo.addItem(label, w.get("id", ""))
        for i in range(combo.count()):
            if combo.itemData(i) == prev:
                combo.setCurrentIndex(i)
                break
        combo.blockSignals(False)

    def _palette_add_repo(self, parent: QtWidgets.QWidget, combo: QtWidgets.QComboBox) -> None:
        from pathlib import Path

        path = QtWidgets.QFileDialog.getExistingDirectory(
            parent,
            "Pick the project repo to give the agent access to",
            str(Path.home()),
        )
        if not path:
            return

        async def _go() -> None:
            try:
                ws = await self.client.call(
                    "workspaces.register",
                    {"path": path, "name": Path(path).name},
                )
            except Exception as exc:
                QtWidgets.QMessageBox.warning(parent, "Couldn't register repo", str(exc))
                return
            await self._populate_workspaces(combo, select_id=ws.get("id"))

        asyncio.ensure_future(_go())

    def _palette_clone_repo(self, parent: QtWidgets.QWidget, combo: QtWidgets.QComboBox) -> None:
        # Inline mini-dialog: URL + branch.  Keeps the new-conversation
        # flow self-contained — no need to bounce out to the Chat tab.
        dlg = QtWidgets.QDialog(parent)
        dlg.setWindowTitle("Clone from git")
        dlg.resize(440, 180)
        form = QtWidgets.QFormLayout(dlg)
        url_input = QtWidgets.QLineEdit()
        url_input.setPlaceholderText("https://github.com/owner/repo.git")
        form.addRow("Git URL:", url_input)
        branch_input = QtWidgets.QLineEdit()
        branch_input.setPlaceholderText("(leave blank for default)")
        form.addRow("Branch:", branch_input)
        info = QtWidgets.QLabel(
            "Clones into AgentOrchestra's data directory; large repos may take a minute."
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

        async def _go() -> None:
            try:
                ws = await self.client.call("workspaces.clone", {"url": url, "branch": branch})
            except Exception as exc:
                QtWidgets.QMessageBox.warning(parent, "Clone failed", str(exc))
                return
            await self._populate_workspaces(combo, select_id=ws.get("id"))

        asyncio.ensure_future(_go())

    async def _do_create(
        self,
        name: str,
        provider: str,
        model: str,
        system: str,
        first_message: str,
        workspace_id: str = "",
    ) -> None:
        try:
            agent = await self.client.call(
                "agents.create",
                {
                    "name": name,
                    "provider": provider,
                    "model": model,
                    "system": system,
                    "workspace_id": workspace_id or None,
                },
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Create failed", str(exc))
            return
        if first_message:
            try:
                await self.client.call(
                    "agents.send",
                    {"agent_id": agent["id"], "message": first_message},
                )
            except Exception as exc:
                # The agent exists; just the first send failed.  Show
                # the error but keep the agent in the list.
                QtWidgets.QMessageBox.warning(self, "First message failed", str(exc))
        await self.reload_agents()

    async def reload_agents(self) -> None:
        try:
            agents = await self.client.call("agents.list", {})
        except Exception:
            agents = []
        self.agents_list.clear()
        for a in agents:
            label_lines = [a.get("name", "?")]
            sub = f"{a.get('model', '?')} · {len(a.get('transcript') or [])} turns"
            if a.get("parent_name"):
                sub += f"  ↩ {a.get('parent_preset') or 'follow-up'} of {a['parent_name']}"
            label_lines.append(sub)
            item = QtWidgets.QListWidgetItem("\n".join(label_lines))
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                {"kind": "conversation", "agent": a},
            )
            self.agents_list.addItem(item)

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
