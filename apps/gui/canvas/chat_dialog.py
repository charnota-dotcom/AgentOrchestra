"""Per-agent chat dialog opened by double-clicking a ConversationNode.

A small modal-ish QDialog (non-modal so the operator can drag the
canvas while it's open) showing one agent's transcript and a send box.
Continuing the conversation hits ``agents.send`` so the persistent
transcript on the service stays the source of truth — the canvas
ConversationNode auto-refreshes on next open.
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

        # Workspace banner — visible repo binding so the operator
        # always sees what files this agent can read / edit.
        ws_row = QtWidgets.QHBoxLayout()
        self.workspace_label = QtWidgets.QLabel(self._format_workspace_label(agent))
        self.workspace_label.setStyleSheet(
            "color:#1f7a3f;font-size:11px;background:#e9f8ee;"
            "border:1px solid #c7e8d3;border-radius:4px;padding:4px 8px;"
        )
        self.workspace_label.setWordWrap(True)
        ws_row.addWidget(self.workspace_label, stretch=1)
        change_repo_btn = QtWidgets.QPushButton("Change repo")
        change_repo_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:11px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        change_repo_btn.setToolTip(
            "Bind / unbind this conversation to a project repo.  When "
            "bound, the CLI runs inside that directory and can read, "
            "search, and edit files using its built-in tools."
        )
        change_repo_btn.clicked.connect(self._change_workspace)  # type: ignore[arg-type]
        ws_row.addWidget(change_repo_btn)
        v.addLayout(ws_row)

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

        # Pending attachments — files the operator picked but hasn't
        # sent yet.  Each is a dict {id, original_name, kind, bytes}.
        self._pending_attachments: list[dict[str, Any]] = []
        self.attachments_row = QtWidgets.QHBoxLayout()
        self.attachments_row.setSpacing(6)
        self.attachments_row.addStretch(1)
        att_wrap = QtWidgets.QWidget()
        att_wrap.setLayout(self.attachments_row)
        att_wrap.setVisible(False)
        self._attachments_wrap = att_wrap
        v.addWidget(att_wrap)

        bottom = QtWidgets.QHBoxLayout()
        self.attach_btn = QtWidgets.QPushButton("📎")
        self.attach_btn.setToolTip(
            "Attach an image (.png/.jpg/.gif/.webp) or a spreadsheet "
            "(.xlsx/.xls/.csv) to the next message.  Spreadsheets are "
            "rendered as a markdown table; images pass through to the CLI."
        )
        self.attach_btn.setStyleSheet(
            "QPushButton{padding:10px 12px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;font-size:14px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.attach_btn.clicked.connect(self._attach_file)  # type: ignore[arg-type]
        bottom.addWidget(self.attach_btn)

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

    def _attach_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Attach a file to this conversation",
            "",
            _ATTACHMENT_FILTER,
        )
        if not path:
            return
        asyncio.ensure_future(self._upload_attachment(Path(path)))

    async def _upload_attachment(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't read file", str(exc))
            return
        self.attach_btn.setEnabled(False)
        try:
            res = await self.client.call(
                "attachments.upload",
                {
                    "agent_id": self.agent["id"],
                    "original_name": path.name,
                    "content_b64": base64.b64encode(data).decode("ascii"),
                },
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't attach file", str(exc))
            return
        finally:
            self.attach_btn.setEnabled(True)
        if res.get("warning"):
            QtWidgets.QMessageBox.information(
                self,
                "Attached with warning",
                f"{path.name}: {res['warning']}",
            )
        self._pending_attachments.append(res)
        self._render_pending_attachments()

    def _render_pending_attachments(self) -> None:
        # Wipe old chip widgets (keeping the trailing stretch).
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
        layout = QtWidgets.QHBoxLayout(chip)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)
        icon = "🖼" if att.get("kind") == "image" else "📊"
        kb = max(1, int(att.get("bytes", 0)) // 1024)
        layout.addWidget(QtWidgets.QLabel(f"{icon} {att.get('original_name', '?')} · {kb} KB"))
        rm = QtWidgets.QPushButton("✕")
        rm.setToolTip("Remove attachment")
        rm.setFixedSize(18, 18)
        rm.clicked.connect(lambda _=False, a=att: self._remove_attachment(a))  # type: ignore[arg-type]
        layout.addWidget(rm)
        return chip

    def _remove_attachment(self, att: dict[str, Any]) -> None:
        # Drop locally, schedule server-side delete.
        self._pending_attachments = [
            a for a in self._pending_attachments if a.get("id") != att.get("id")
        ]
        self._render_pending_attachments()
        asyncio.ensure_future(self._delete_attachment(att.get("id", "")))

    async def _delete_attachment(self, attachment_id: str) -> None:
        if not attachment_id:
            return
        try:
            await self.client.call("attachments.delete", {"id": attachment_id})
        except Exception:
            # Worst case it stays in the agent's folder; the cleanup
            # button on the Limits tab can sweep it later.
            pass

    def _send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        attachment_ids = [a.get("id") for a in self._pending_attachments if a.get("id")]
        self.input.clear()
        self.send_btn.setEnabled(False)
        # Optimistic: show the user's message immediately.
        att_summary = ""
        if attachment_ids:
            names = [a.get("original_name", "?") for a in self._pending_attachments]
            att_summary = f"\n[attached: {', '.join(names)}]"
        self.transcript.appendPlainText(f"You:\n{text}{att_summary}\n")
        # Clear the chip row — these are bound to the just-sent turn now.
        self._pending_attachments = []
        self._render_pending_attachments()
        asyncio.ensure_future(self._send_async(text, attachment_ids))

    @staticmethod
    def _format_workspace_label(agent: dict[str, Any]) -> str:
        ws_id = agent.get("workspace_id")
        ws_name = agent.get("workspace_name") or ""
        ws_path = agent.get("workspace_path") or ""
        if not ws_id:
            return "📂 No repo bound — chat-only conversation"
        if ws_name and ws_path:
            return f"📂 Working in: <b>{ws_name}</b> ({ws_path})"
        if ws_path:
            return f"📂 Working in: {ws_path}"
        return "📂 Repo bound (id only — refresh to load details)"

    def _change_workspace(self) -> None:
        asyncio.ensure_future(self._open_workspace_dialog())

    async def _open_workspace_dialog(self) -> None:
        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't load workspaces", str(exc))
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Repo for {self.agent.get('name', '?')}")
        dlg.resize(520, 300)
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(
            QtWidgets.QLabel(
                "When a repo is selected, the CLI runs with cwd set to "
                "that directory.  The model can then use its built-in "
                "Read / Bash / Edit / Grep tools against the project."
            )
        )
        v.itemAt(0).widget().setWordWrap(True)  # type: ignore[union-attr]
        v.itemAt(0).widget().setStyleSheet("color:#5b6068;font-size:11px;")  # type: ignore[union-attr]

        listw = QtWidgets.QListWidget()
        listw.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:4px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #eef0f3;}"
        )
        none_item = QtWidgets.QListWidgetItem("(no repo — chat-only)")
        none_item.setData(QtCore.Qt.ItemDataRole.UserRole, "")
        listw.addItem(none_item)
        for w in workspaces:
            item = QtWidgets.QListWidgetItem(
                f"{w.get('name', '?')}  ·  {w.get('repo_path', '?')}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, w.get("id", ""))
            listw.addItem(item)
        # Pre-select the current binding.
        current_id = self.agent.get("workspace_id") or ""
        for i in range(listw.count()):
            it = listw.item(i)
            if it is not None and it.data(QtCore.Qt.ItemDataRole.UserRole) == current_id:
                listw.setCurrentRow(i)
                break
        v.addWidget(listw, stretch=1)

        bottom = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add new repo…")
        add_btn.clicked.connect(  # type: ignore[arg-type]
            lambda: asyncio.ensure_future(self._add_repo_from_dialog(listw))
        )
        bottom.addWidget(add_btn)
        bottom.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        bottom.addWidget(buttons)
        v.addLayout(bottom)

        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        sel = listw.currentItem()
        if sel is None:
            return
        chosen = str(sel.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        try:
            updated = await self.client.call(
                "agents.set_workspace",
                {"agent_id": self.agent["id"], "workspace_id": chosen or None},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't bind repo", str(exc))
            return
        # Augment with workspace name + path so the banner can show them.
        if chosen:
            for w in workspaces:
                if w.get("id") == chosen:
                    updated["workspace_name"] = w.get("name")
                    updated["workspace_path"] = w.get("repo_path")
                    break
        else:
            updated["workspace_name"] = None
            updated["workspace_path"] = None
        self.agent = updated
        self.workspace_label.setText(self._format_workspace_label(self.agent))
        self.sent.emit(self.agent)

    async def _add_repo_from_dialog(self, listw: QtWidgets.QListWidget) -> None:
        from pathlib import Path

        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Pick the project repo to register",
            str(Path.home()),
        )
        if not path:
            return
        try:
            ws = await self.client.call(
                "workspaces.register",
                {"path": path, "name": Path(path).name},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Couldn't register repo", str(exc))
            return
        item = QtWidgets.QListWidgetItem(
            f"{ws.get('name', '?')}  ·  {ws.get('repo_path', '?')}"
        )
        item.setData(QtCore.Qt.ItemDataRole.UserRole, ws.get("id", ""))
        listw.addItem(item)
        listw.setCurrentItem(item)

    async def _send_async(self, message: str, attachment_ids: list[str]) -> None:
        try:
            res = await self.client.call(
                "agents.send",
                {
                    "agent_id": self.agent["id"],
                    "message": message,
                    "attachment_ids": attachment_ids,
                },
            )
        except Exception as exc:
            self.transcript.appendPlainText(f"Error:\n{exc}\n")
            self.send_btn.setEnabled(True)
            return
        self.agent = res.get("agent", self.agent)
        self._render_transcript()
        self.send_btn.setEnabled(True)
        self.sent.emit(self.agent)
