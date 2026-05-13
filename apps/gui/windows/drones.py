"""Drones tab — deployed instances of blueprints, ready to chat.

A *drone action* is what an operator deploys when they pick a blueprint
and start a conversation.  This tab is the operator's chat surface
against those actions.

Layout (left → right):
* Sidebar — list of actions, ordered by recency.  ``Deploy`` opens a
  modal that picks a blueprint + workspace.  Selecting an action loads
  its transcript.
* Centre — transcript + multi-line message input + Send.

Companion to ``apps/gui/windows/blueprints.py``.  See
``docs/DRONE_MODEL.md`` for the design.
"""

from __future__ import annotations

import asyncio
import html as _html
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.browser_bridge import BrowserBridgeDialog
from apps.gui.widgets.context_gauge import ContextGauge
from apps.service.tokens import context_window, estimate_action_total

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _render_transcript_html(transcript: list[dict[str, Any]]) -> str:
    """Render an action's transcript as styled HTML for the viewer.

    Mirrors the lightweight markup the Agents tab uses — User /
    Assistant labels with role-coloured backgrounds.  Plain text only;
    the message body is HTML-escaped before insertion.

    Recognises four entry kinds:

    * ``role=user`` / ``role=assistant`` — chat turns, rendered as
      coloured speech bubbles (the v1 shape).
    * ``role=tool_call`` — the assistant invoked a tool.  Rendered as
      a slim monospace card with the tool name, input, and (for
      ``Task``) a "sub-agent" badge.  Captured from claude-cli's
      stream-json output.
    * ``role=tool_result`` — a tool returned a result.  Rendered as a
      paired card; turns red on ``is_error``.

    See ``ClaudeCLIChatSession`` in
    ``apps/service/providers/claude_cli/session.py``.
    """
    blocks: list[str] = []
    for m in transcript:
        role = m.get("role") or "user"
        if role == "user":
            content = _html.escape(m.get("content") or "")
            blocks.append(
                '<div style="background:#eef3fb;border-radius:6px;padding:10px 12px;'
                'margin-bottom:10px;"><b style="color:#1f6feb;">You</b><br>'
                f'<pre style="white-space:pre-wrap;font-family:inherit;margin:6px 0 0 0;">'
                f"{content}</pre></div>"
            )
        elif role == "tool_call":
            blocks.append(_render_tool_call_html(m))
        elif role == "tool_result":
            blocks.append(_render_tool_result_html(m))
        else:
            content = _html.escape(m.get("content") or "")
            blocks.append(
                '<div style="background:#f7f8fa;border:1px solid #e6e7eb;border-radius:6px;'
                'padding:10px 12px;margin-bottom:10px;"><b style="color:#5b6068;">Drone</b><br>'
                f'<pre style="white-space:pre-wrap;font-family:inherit;margin:6px 0 0 0;">'
                f"{content}</pre></div>"
            )
    return (
        "".join(blocks)
        or "<i style='color:#7a7d85;'>(no messages yet — send one to get started)</i>"
    )


def _render_tool_call_html(entry: dict[str, Any]) -> str:
    name = _html.escape(str(entry.get("tool_name") or "?"))
    is_subagent = bool(entry.get("is_subagent"))
    step = entry.get("step") or 0
    badge = (
        '<span style="background:#7c3aed;color:#fff;font-size:10px;'
        'padding:1px 6px;border-radius:9px;margin-left:6px;">sub-agent</span>'
        if is_subagent
        else ""
    )
    tool_input = entry.get("tool_input") or {}
    try:
        import json as _json

        input_text = _json.dumps(tool_input, indent=2, default=str)
    except (TypeError, ValueError):
        input_text = str(tool_input)
    input_text = _html.escape(input_text)
    if len(input_text) > 1200:
        input_text = input_text[:1200] + "\n... (truncated)"
    label_color = "#7c3aed" if is_subagent else "#0a7d4d"
    return (
        '<div style="background:#fbfaf3;border:1px solid #ece6c5;border-radius:6px;'
        'padding:8px 12px;margin-bottom:6px;font-size:12px;">'
        f'<b style="color:{label_color};">tool_call</b> '
        f'<span style="color:#5b6068;">#{int(step)}</span> '
        f'<code style="background:#fff3c4;padding:1px 4px;border-radius:3px;">{name}</code>'
        f"{badge}<br>"
        '<pre style="white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;'
        f'font-size:11px;color:#3a3d44;margin:6px 0 0 0;">{input_text}</pre></div>'
    )


def _render_tool_result_html(entry: dict[str, Any]) -> str:
    step = entry.get("step") or 0
    is_error = bool(entry.get("is_error"))
    output = _html.escape(str(entry.get("tool_output") or ""))
    if len(output) > 1500:
        output = output[:1500] + "\n... (truncated)"
    border = "#f3b1b1" if is_error else "#cfe8d4"
    bg = "#fdf2f2" if is_error else "#f2f9f4"
    label_color = "#b3261e" if is_error else "#0a7d4d"
    label = "tool_result (error)" if is_error else "tool_result"
    return (
        f'<div style="background:{bg};border:1px solid {border};border-radius:6px;'
        'padding:8px 12px;margin-bottom:10px;font-size:12px;">'
        f'<b style="color:{label_color};">{label}</b> '
        f'<span style="color:#5b6068;">#{int(step)}</span><br>'
        '<pre style="white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;'
        f'font-size:11px;color:#3a3d44;margin:6px 0 0 0;">{output}</pre></div>'
    )


from apps.gui.ipc.sse_client import SseClient


class DroneCardWidget(QtWidgets.QWidget):
    """Custom widget for a drone action card in the sidebar list.

    Using a real QWidget instead of a plain QListWidgetItem string allows
    the annotator overlay to 'see' and select individual drones.
    """

    def __init__(self, action: dict[str, Any], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.action_id = action["id"]
        self.setObjectName(f"drone_card_{self.action_id}")

        snap = action.get("blueprint_snapshot") or {}
        name = action.get("name") or snap.get("name") or "(unnamed blueprint)"
        role = snap.get("role") or "worker"
        provider = snap.get("provider") or ""
        model = snap.get("model") or ""
        n_turns = len(action.get("transcript") or [])

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)

        title = QtWidgets.QLabel(f"{name}  ·  {role}")
        title.setStyleSheet("font-weight:600; color:#0f1115;")
        v.addWidget(title)

        subtitle = QtWidgets.QLabel(f"{provider} / {model}  ·  {n_turns} msg")
        subtitle.setStyleSheet("color:#5b6068; font-size:11px;")
        v.addWidget(subtitle)

    def update_action(self, action: dict[str, Any]) -> None:
        """Refresh the labels when the action is updated (e.g. more messages)."""
        snap = action.get("blueprint_snapshot") or {}
        name = action.get("name") or snap.get("name") or "(unnamed blueprint)"
        role = snap.get("role") or "worker"
        provider = snap.get("provider") or ""
        model = snap.get("model") or ""
        n_turns = len(action.get("transcript") or [])

        title = self.findChild(QtWidgets.QLabel)  # first label is title
        if title:
            title.setText(f"{name}  ·  {role}")

        # Find the second label for subtitle
        labels = self.findChildren(QtWidgets.QLabel)
        if len(labels) > 1:
            labels[1].setText(f"{provider} / {model}  ·  {n_turns} msg")


class DronesPage(QtWidgets.QWidget):
    def __init__(
        self, client: RpcClient, sse: SseClient, provider_mode: str = "all"
    ) -> None:
        """
        provider_mode:
          - "all":        Show every deployed action.
          - "manual":     Only 'browser' provider (Drones).
          - "autonomous": Everything EXCEPT 'browser' (Agents).
        """
        super().__init__()
        self.client = client
        self.sse = sse
        self.provider_mode = provider_mode
        self._actions: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar(), stretch=0)
        layout.addWidget(self._build_centre(), stretch=1)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload()))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("background:#fff;border-right:1px solid #e6e7eb;")
        wrap.setFixedWidth(280)
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title_text = "FPV Drones" if self.provider_mode == "manual" else "Reapers"
        if self.provider_mode == "all":
            title_text = "FPV Drones & Reapers"
            
        title = QtWidgets.QLabel(title_text)
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        header.addWidget(title)
        header.addStretch(1)
        deploy_btn = QtWidgets.QPushButton("Deploy")
        deploy_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #1f6feb;"
            "border-radius:4px;background:#1f6feb;color:#fff;font-size:12px;}"
            "QPushButton:hover{background:#1860d6;}"
        )
        deploy_btn.setToolTip(
            "Pick a blueprint + (optional) workspace and spawn a fresh FPV drone "
            "action.  The action's blueprint snapshot is frozen at deploy "
            "time — later blueprint edits don't affect this action."
        )
        deploy_btn.clicked.connect(self._deploy_dialog)  # type: ignore[arg-type]
        header.addWidget(deploy_btn)
        v.addLayout(header)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setStyleSheet(
            "QListWidget{border:none;background:transparent;}"
            "QListWidget::item{border-radius:4px;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )
        self.list_widget.currentRowChanged.connect(self._on_select)  # type: ignore[arg-type]
        self.list_widget.itemDoubleClicked.connect(self._edit_selected)  # type: ignore[arg-type]
        v.addWidget(self.list_widget, stretch=1)

        btns = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select all")
        select_all_btn.setStyleSheet(
            "QPushButton{padding:4px 8px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        select_all_btn.clicked.connect(self.list_widget.selectAll)  # type: ignore[arg-type]
        btns.addWidget(select_all_btn)

        edit_btn = QtWidgets.QPushButton("Edit")
        edit_btn.setStyleSheet(
            "QPushButton{padding:4px 8px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        edit_btn.clicked.connect(self._edit_selected)  # type: ignore[arg-type]
        btns.addWidget(edit_btn)

        delete_btn = QtWidgets.QPushButton("Delete")
        delete_btn.setStyleSheet(
            "QPushButton{padding:4px 8px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;color:#5b6068;}"
            "QPushButton:hover{background:#fde8e7;border-color:#b3261e;color:#b3261e;}"
        )
        delete_btn.clicked.connect(self._delete_selected)  # type: ignore[arg-type]
        btns.addWidget(delete_btn)
        v.addLayout(btns)
        return wrap

    # ------------------------------------------------------------------
    # Centre — transcript + input
    # ------------------------------------------------------------------

    def _build_centre(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        self.title = QtWidgets.QLabel("(no drone selected)")
        self.title.setStyleSheet("font-size:18px;font-weight:600;color:#0f1115;")
        v.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        self.subtitle.setWordWrap(True)
        v.addWidget(self.subtitle)

        # References section — shows linked peers.
        self.refs_row = QtWidgets.QHBoxLayout()
        self.refs_label = QtWidgets.QLabel("References: none")
        self.refs_label.setStyleSheet("color:#5b6068;font-size:11px;")
        self.refs_row.addWidget(self.refs_label, stretch=1)
        edit_refs_btn = QtWidgets.QPushButton("Edit…")
        edit_refs_btn.setStyleSheet(
            "QPushButton{padding:2px 6px;font-size:10px;border:1px solid #d0d3d9;"
            "border-radius:3px;background:#f6f8fa;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        edit_refs_btn.clicked.connect(self._edit_references)
        self.refs_row.addWidget(edit_refs_btn)
        v.addLayout(self.refs_row)

        # Workspace banner — green when bound, hidden when chat-only.
        self.workspace_label = QtWidgets.QLabel("")
        self.workspace_label.setStyleSheet(
            "color:#1f7a3f;font-size:11px;background:#e9f8ee;"
            "border:1px solid #c7e8d3;border-radius:4px;padding:4px 8px;"
        )
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setVisible(False)
        v.addWidget(self.workspace_label)

        self.transcript = QtWidgets.QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QTextEdit{background:#fff;border:1px solid #e6e7eb;"
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

        # Context-window gauge: hidden until the first send returns
        # token totals.  See docs/BROWSER_PROVIDER_PLAN.md (PR 1).
        self.context_gauge = ContextGauge(parent=wrap)
        v.addWidget(self.context_gauge)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.message_input)
        shortcut.activated.connect(self._send_message)  # type: ignore[arg-type]
        return wrap

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def _reload(self) -> None:
        try:
            raw_actions = await self.client.call("drones.list", {})
        except Exception as e:
            self._actions = []
            self.subtitle.setText(f"Reload failed: {e}")
            self.subtitle.setStyleSheet("color:#b3261e;font-size:11px;")
            return

        # Filter based on provider_mode.
        if self.provider_mode == "manual":
            self._actions = [
                a for a in raw_actions 
                if (a.get("blueprint_snapshot") or {}).get("provider") == "browser"
            ]
        elif self.provider_mode == "autonomous":
            self._actions = [
                a for a in raw_actions 
                if (a.get("blueprint_snapshot") or {}).get("provider") != "browser"
            ]
        else:
            self._actions = raw_actions

        self.list_widget.blockSignals(True)
        # We try to maintain the scroll position and selection if we're just updating.
        # But for a full reload, clearing and re-adding is safer.
        self.list_widget.clear()
        for a in self._actions:
            item = QtWidgets.QListWidgetItem()
            widget = DroneCardWidget(a)
            item.setSizeHint(widget.sizeHint())
            item.setData(QtCore.Qt.ItemDataRole.UserRole, a["id"])
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
        self.list_widget.blockSignals(False)

        if self._current:
            for i, a in enumerate(self._actions):
                if a["id"] == self._current["id"]:
                    self.list_widget.setCurrentRow(i)
                    break

    def _edit_selected(self) -> None:
        if not self._current:
            return
        asyncio.ensure_future(self._edit_dialog_async())

    async def _edit_dialog_async(self) -> None:
        if not self._current:
            return
        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Cannot open edit dialog", str(e))
            return

        dlg = _EditDroneDialog(self._current, workspaces, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        params = dlg.params()
        params["id"] = self._current["id"]
        try:
            action = await self.client.call("drones.update", params)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Update failed", str(e))
            return

        self._current = action
        await self._reload()

    def _edit_references(self) -> None:
        if not self._current:
            return
        asyncio.ensure_future(self._edit_references_async())

    async def _edit_references_async(self) -> None:
        if not self._current:
            return
        
        try:
            # We need all actions to show in the picker.
            all_actions = await self.client.call("drones.list", {})
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Cannot open references editor", str(e))
            return

        existing = self._current.get("additional_reference_action_ids") or []
        dlg = _ReferenceEditorDialog(
            self._current["id"], all_actions, existing, parent=self
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        picked = dlg.picked_ids()
        try:
            action = await self.client.call(
                "drones.update",
                {"id": self._current["id"], "additional_reference_action_ids": picked},
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Update failed", str(e))
            return

        self._current = action
        # Update the label immediately.
        self._refresh_refs_label(action)
        await self._reload()

    def _refresh_refs_label(self, action: dict[str, Any]) -> None:
        refs = action.get("additional_reference_action_ids") or []
        if not refs:
            self.refs_label.setText("References: none")
        else:
            self.refs_label.setText(f"References: {len(refs)} peer(s) linked")

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._actions):
            self._current = None
            self.title.setText("(no drone selected)")
            self.subtitle.setText("")
            self.workspace_label.setVisible(False)
            self.transcript.setHtml(_render_transcript_html([]))
            self.context_gauge.set_token_counts(None, None)
            self.send_btn.setEnabled(False)
            return
        action = self._actions[row]
        self._current = action
        snap = action.get("blueprint_snapshot") or {}
        self.title.setText(snap.get("name") or "(unnamed blueprint)")
        self.subtitle.setText(
            f"id {action['id']}  ·  blueprint {action.get('blueprint_id')}  ·  "
            f"role {snap.get('role', 'worker')}  ·  "
            f"{snap.get('provider', '')} / {snap.get('model', '')}"
        )
        if action.get("workspace_id"):
            asyncio.ensure_future(self._load_workspace_label(action["workspace_id"]))
        else:
            self.workspace_label.setVisible(False)
        self.transcript.setHtml(_render_transcript_html(action.get("transcript") or []))
        self._refresh_refs_label(action)
        # Show a baseline gauge value from the action's transcript so
        # switching drones immediately reflects their size, without
        # waiting for the next send to populate.
        self._refresh_gauge_from_action(action)
        self.send_btn.setEnabled(True)

    def _refresh_gauge_from_action(self, action: dict[str, Any]) -> None:
        """Estimate token usage client-side from the action snapshot.

        Used when no fresh ``drones.send`` response is available (e.g.
        the operator just selected the drone in the sidebar).  Reuses
        the shared ``apps.service.tokens`` estimator — same pure-Python
        functions the service calls, no host-boundary crossing.
        """
        snap = action.get("blueprint_snapshot") or {}
        provider = snap.get("provider")
        model = snap.get("model")
        if not provider or not model:
            self.context_gauge.set_token_counts(None, None)
            return
        total = estimate_action_total(
            action,
            system_prompt=snap.get("system_persona") or "",
            provider=provider,
            model=model,
        )
        self.context_gauge.set_token_counts(total, context_window(provider, model))

    async def _load_workspace_label(self, workspace_id: str) -> None:
        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception:
            self.workspace_label.setVisible(False)
            return
        ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if not ws:
            self.workspace_label.setVisible(False)
            return
        self.workspace_label.setText(
            f"📂 Bound to repo: {ws.get('name', '')} — {ws.get('repo_path', '')}"
        )
        self.workspace_label.setVisible(True)

    def _send_message(self) -> None:
        if not self._current:
            return
        text = self.message_input.toPlainText().strip()
        if not text:
            return
        self.message_input.clear()
        self.send_btn.setEnabled(False)
        asyncio.ensure_future(self._send_async(self._current["id"], text))

    async def _send_async(self, action_id: str, message: str) -> None:
        # 1. Start streaming deltas in the background.
        stream_task = asyncio.create_task(self._consume_drone_stream(action_id))
        
        try:
            out = await self.client.call(
                "drones.send", {"action_id": action_id, "message": message}
            )
        except Exception as e:
            stream_task.cancel()
            # Show the exception type as well as the message — some
            # service-side errors (timeouts, parse failures) raise
            # exceptions whose str() is empty, leaving the dialog body
            # blank and the operator with nothing to copy/paste.
            msg = str(e).strip()
            body = f"{type(e).__name__}: {msg}" if msg else type(e).__name__
            body += (
                "\n\nFor a deeper trace, run scripts\\doctor.cmd — its "
                "`--- Recent service log ---` section shows the last "
                "lines the auto-spawned service wrote to stderr."
            )
            QtWidgets.QMessageBox.critical(self, "Send failed", body)
            self.send_btn.setEnabled(True)
            return
        
        await stream_task
        action = out.get("action") or {}
        # Browser-mode drones return needs_paste=True instead of a
        # reply — the service rendered the prompt and now the operator
        # paste-rounds-trips it through their browser.  Open the
        # BrowserBridgeDialog and let it drive the rest.  See
        # docs/BROWSER_PROVIDER_PLAN.md.
        if out.get("needs_paste"):
            self._open_browser_bridge(action, out)
            return
        # Update local cache + viewer.
        self._current = action
        for i, a in enumerate(self._actions):
            if a.get("id") == action.get("id"):
                self._actions[i] = action
                break
        self.transcript.setHtml(_render_transcript_html(action.get("transcript") or []))
        # Update the context-window gauge with the fresh totals from the
        # response.  Hidden if context_window is None (unknown model).
        self.context_gauge.set_token_counts(
            out.get("transcript_tokens"),
            out.get("context_window"),
        )
        self.send_btn.setEnabled(True)
        # Re-pull list so the sidebar's "N msg" counter updates.
        await self._reload()

    async def _consume_drone_stream(self, action_id: str) -> None:
        """Listen for DRONE_TOKEN_DELTA events and update the transcript live."""
        # Append a placeholder assistant turn to the transcript.
        # This is a bit of a hack because the transcript is HTML, but it
        # works for real-time feedback.
        cursor = self.transcript.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        
        # Add the bubble container.
        self.transcript.insertHtml(
            '<div id="live-reply" style="background:#f7f8fa;border:1px solid #e6e7eb;'
            'border-radius:6px;padding:10px 12px;margin-bottom:10px;">'
            '<b style="color:#5b6068;">Drone</b><br>'
            '<pre id="live-content" style="white-space:pre-wrap;font-family:inherit;'
            'margin:6px 0 0 0;"></pre></div>'
        )
        
        # Find the end again to start inserting text.
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        # Move back inside the </div></pre>
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.Left, n=12) 

        try:
            async for ev in self.sse.stream_drone(action_id):
                if ev.get("kind") == "drone.token_delta":
                    delta = (ev.get("payload") or {}).get("delta", "")
                    if delta:
                        # Append plain text to the end of the transcript.
                        # QTextEdit handles escaping when using insertText.
                        self.transcript.moveCursor(QtGui.QTextCursor.MoveOperation.End)
                        # We need to be careful about the HTML structure.
                        # Simple approach: just append to the browser.
                        self.transcript.insertPlainText(delta)
                        self.transcript.ensureCursorVisible()
        except Exception:
            log.debug("drone stream consumed/failed", exc_info=True)
        finally:
            self.transcript.insertHtml("</pre></div>")

    def _open_browser_bridge(
        self,
        action: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        """Open the BrowserBridgeDialog from a browser-mode
        ``drones.send`` response.

        The service has already persisted the user turn; the dialog
        handles the operator's copy → paste-into-browser → copy-back
        round-trip, then calls ``drones.append_assistant_turn`` to
        save the reply.  We wire ``saved`` so the local cache +
        transcript view refresh once that lands.
        """
        dlg = BrowserBridgeDialog(
            client=self.client,
            action_id=action.get("id", ""),
            rendered_prompt=response.get("rendered_prompt") or "",
            chat_url=response.get("chat_url"),
            bound_chat_url=response.get("bound_chat_url"),
            prompt_tokens=response.get("prompt_tokens"),
            transcript_tokens=response.get("transcript_tokens"),
            context_window=response.get("context_window"),
            parent=self,
        )

        def _on_saved(updated_action: dict[str, Any]) -> None:
            self._current = updated_action
            for i, a in enumerate(self._actions):
                if a.get("id") == updated_action.get("id"):
                    self._actions[i] = updated_action
                    break
            self.transcript.setHtml(_render_transcript_html(updated_action.get("transcript") or []))
            self._refresh_gauge_from_action(updated_action)
            self.send_btn.setEnabled(True)
            asyncio.ensure_future(self._reload())

        dlg.saved.connect(_on_saved)  # type: ignore[arg-type]
        # Also re-enable Send when the operator cancels the dialog
        # (otherwise they're stuck with a disabled button).
        dlg.rejected.connect(lambda: self.send_btn.setEnabled(True))  # type: ignore[arg-type]
        dlg.show()
        # Show the partial transcript that has the new user turn even
        # if the operator hasn't pasted a reply yet, so they see what
        # was sent.
        self.transcript.setHtml(_render_transcript_html(action.get("transcript") or []))

    def _delete_selected(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return
        
        ids = [item.data(QtCore.Qt.ItemDataRole.UserRole) for item in selected]
        count = len(ids)
        msg = f"Delete {count} drone action(s)?  Their transcripts will be lost." if count > 1 else "Delete this drone action?  Its transcript will be lost."
        
        if (
            QtWidgets.QMessageBox.question(
                self,
                "Delete drones" if count > 1 else "Delete drone",
                msg,
            )
            != QtWidgets.QMessageBox.StandardButton.Yes
        ):
            return
        asyncio.ensure_future(self._delete_multiple_async(ids))

    async def _delete_multiple_async(self, action_ids: list[str]) -> None:
        try:
            # Parallel delete requests for speed.
            await asyncio.gather(
                *[self.client.call("drones.delete", {"id": aid}) for nid, aid in enumerate(action_ids)],
                return_exceptions=True
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Delete failed", str(e))
        
        self._current = None
        await self._reload()

    def _deploy_dialog(self) -> None:
        asyncio.ensure_future(self._deploy_dialog_async())

    async def _deploy_dialog_async(self) -> None:
        try:
            blueprints = await self.client.call("blueprints.list", {})
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Cannot open deploy dialog", str(e))
            return
        if not blueprints:
            QtWidgets.QMessageBox.information(
                self,
                "No blueprints yet",
                "Create a blueprint on the Blueprints tab first, then deploy from here.",
            )
            return
        dlg = _DeployDialog(blueprints, workspaces, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        params = dlg.params()
        try:
            action = await self.client.call("drones.deploy", params)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Deploy failed", str(e))
            return
        self._current = action
        await self._reload()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        asyncio.ensure_future(self._reload())


class _DeployDialog(QtWidgets.QDialog):
    """Pick a blueprint + (optional) workspace + (optional) one-off
    skills, return the params for ``drones.deploy``.
    """

    def __init__(
        self,
        blueprints: list[dict[str, Any]],
        workspaces: list[dict[str, Any]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Deploy drone")
        self.setModal(True)
        self.resize(520, 320)

        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self._blueprint = QtWidgets.QComboBox()
        for bp in blueprints:
            label = (
                f"{bp.get('name', '(unnamed)')}  ·  {bp.get('role', 'worker')}  ·  "
                f"{bp.get('provider', '')} / {bp.get('model', '')}"
            )
            self._blueprint.addItem(label, bp["id"])
        form.addRow("Blueprint", self._blueprint)

        self._workspace = QtWidgets.QComboBox()
        self._workspace.addItem("(no repo — chat only)", None)
        for ws in workspaces:
            self._workspace.addItem(
                f"{ws.get('name', '')} — {ws.get('repo_path', '')}", ws.get("id")
            )
        self._workspace.setToolTip(
            "Optional repo binding.  When bound, the CLI runs inside the repo "
            "and can read / search / edit files using its built-in tools."
        )
        form.addRow("Workspace", self._workspace)

        self._skills = QtWidgets.QLineEdit()
        self._skills.setPlaceholderText(
            "/oneoff-skill, /another  (optional, layered on top of blueprint defaults)"
        )
        skills_row = QtWidgets.QHBoxLayout()
        skills_row.addWidget(self._skills, stretch=1)
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.setStyleSheet(
            "QPushButton{padding:3px 8px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#f6f8fa;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        browse_btn.clicked.connect(self._browse_skills)
        skills_row.addWidget(browse_btn)
        form.addRow("Extra skills", skills_row)
        v.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Deploy")
        buttons.accepted.connect(self.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(self.reject)  # type: ignore[arg-type]
        v.addWidget(buttons)

    def _browse_skills(self) -> None:
        from apps.gui.widgets.skills_picker import SkillsPicker

        # For _DeployDialog, extract from current blueprint label.
        # For _EditDroneDialog, we'll use a slightly different path if needed, 
        # but both classes share the same parent pattern.
        provider = "browser"
        if hasattr(self, "_blueprint"):
            label = self._blueprint.currentText()
            if " · " in label:
                parts = label.split(" · ")
                if len(parts) > 2 and " / " in parts[2]:
                    provider = parts[2].split(" / ")[0].strip()
        elif hasattr(self, "_action"): # _EditDroneDialog
            provider = (self._action.get("blueprint_snapshot") or {}).get("provider", "browser")
        elif hasattr(self, "action"): # _EditDroneDialog (alt field name)
            provider = (self.action.get("blueprint_snapshot") or {}).get("provider", "browser")

        dlg = SkillsPicker(self.parent().client, provider=provider, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        picked = dlg.picked_skills().split()
        if not picked:
            return

        from apps.gui.windows.blueprints import _split_csv

        current = _split_csv(self._skills.text())
        for p in picked:
            if p not in current:
                current.append(p)
        self._skills.setText(", ".join(current))

    def params(self) -> dict[str, Any]:
        skills = [s.strip() for s in self._skills.text().replace("\n", ",").split(",") if s.strip()]
        return {
            "blueprint_id": self._blueprint.currentData(),
            "workspace_id": self._workspace.currentData(),
            "additional_skills": skills,
        }


class _EditDroneDialog(QtWidgets.QDialog):
    """Edit a deployed drone action's runtime properties."""

    def __init__(
        self,
        action: dict[str, Any],
        workspaces: list[dict[str, Any]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit drone")
        self.setModal(True)
        self.resize(520, 280)

        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        # Some fields are read-only after deploy (frozen in snapshot).
        snap = action.get("blueprint_snapshot") or {}
        
        self._name = QtWidgets.QLineEdit()
        self._name.setText(action.get("name") or snap.get("name") or "")
        self._name.setPlaceholderText("Instance name")
        form.addRow("Name", self._name)

        bp_label = QtWidgets.QLabel(
            f"(Blueprint id {action.get('blueprint_id', '?')})"
        )
        bp_label.setStyleSheet("color:#5b6068;")
        form.addRow("Blueprint", bp_label)

        self._workspace = QtWidgets.QComboBox()
        self._workspace.addItem("(no repo — chat only)", None)
        current_ws = action.get("workspace_id")
        idx = 0
        for i, ws in enumerate(workspaces):
            self._workspace.addItem(
                f"{ws.get('name', '')} — {ws.get('repo_path', '')}", ws.get("id")
            )
            if ws.get("id") == current_ws:
                idx = i + 1
        self._workspace.setCurrentIndex(idx)
        form.addRow("Workspace", self._workspace)

        self._skills = QtWidgets.QLineEdit()
        self._skills.setText(", ".join(action.get("additional_skills") or []))
        self._skills.setPlaceholderText("/skill, /another (one-off skills for this drone)")
        skills_row = QtWidgets.QHBoxLayout()
        skills_row.addWidget(self._skills, stretch=1)
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.setStyleSheet(
            "QPushButton{padding:3px 8px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#f6f8fa;color:#5b6068;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        browse_btn.clicked.connect(self._browse_skills)
        skills_row.addWidget(browse_btn)
        form.addRow("Extra skills", skills_row)

        self._chat_url = QtWidgets.QLineEdit()
        self._chat_url.setText(action.get("bound_chat_url") or "")
        self._chat_url.setPlaceholderText("https://... (pinned conversation URL)")
        form.addRow("Chat URL", self._chat_url)

        v.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(self.reject)  # type: ignore[arg-type]
        v.addWidget(buttons)

    def _browse_skills(self) -> None:
        from apps.gui.widgets.skills_picker import SkillsPicker

        # For _DeployDialog, extract from current blueprint label.
        # For _EditDroneDialog, we'll use a slightly different path if needed, 
        # but both classes share the same parent pattern.
        provider = "browser"
        if hasattr(self, "_blueprint"):
            label = self._blueprint.currentText()
            if " · " in label:
                parts = label.split(" · ")
                if len(parts) > 2 and " / " in parts[2]:
                    provider = parts[2].split(" / ")[0].strip()
        elif hasattr(self, "_action"): # _EditDroneDialog
            provider = (self._action.get("blueprint_snapshot") or {}).get("provider", "browser")
        elif hasattr(self, "action"): # _EditDroneDialog (alt field name)
            provider = (self.action.get("blueprint_snapshot") or {}).get("provider", "browser")

        dlg = SkillsPicker(self.parent().client, provider=provider, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        picked = dlg.picked_skills().split()
        if not picked:
            return

        from apps.gui.windows.blueprints import _split_csv

        current = _split_csv(self._skills.text())
        for p in picked:
            if p not in current:
                current.append(p)
        self._skills.setText(", ".join(current))

    def params(self) -> dict[str, Any]:
        skills = [s.strip() for s in self._skills.text().replace("\n", ",").split(",") if s.strip()]
        return {
            "workspace_id": self._workspace.currentData(),
            "additional_skills": skills,
            "bound_chat_url": self._chat_url.text().strip() or None,
        }


class _ConvertDroneDialog(QtWidgets.QDialog):
    """Pick an autonomous personality for a browser-based drone."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert to autonomous Agent")
        self.setModal(True)
        self.resize(440, 180)

        from apps.gui.presets import PROVIDER_MODELS, PROVIDERS

        v = QtWidgets.QVBoxLayout(self)
        msg = QtWidgets.QLabel(
            "This will convert the manual browser-based drone into a fully "
            "autonomous CLI agent. The existing transcript will be preserved."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#5b6068; margin-bottom:8px;")
        v.addWidget(msg)

        form = QtWidgets.QFormLayout()

        self._provider = QtWidgets.QComboBox()
        # Only CLI providers (exclude 'browser').
        cli_providers = [p for p in PROVIDERS if p != "browser"]
        for p in cli_providers:
            self._provider.addItem(p, p)
        self._provider.currentTextChanged.connect(self._refresh_models)
        form.addRow("Agent Provider", self._provider)

        self._model = QtWidgets.QComboBox()
        self._model.setEditable(False)
        self._refresh_models(cli_providers[0] if cli_providers else "")
        form.addRow("Agent Model", self._model)

        v.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _refresh_models(self, provider: str) -> None:
        from apps.gui.presets import PROVIDER_MODELS

        self._model.clear()
        for m in PROVIDER_MODELS.get(provider, ()):
            self._model.addItem(m, m)

    def params(self) -> dict[str, str]:
        return {
            "provider": self._provider.currentText(),
            "model": self._model.currentText(),
        }


class _ReferenceEditorDialog(QtWidgets.QDialog):
    """Pick other drones to use as context references."""

    def __init__(
        self,
        current_action_id: str,
        all_actions: list[dict[str, Any]],
        existing_refs: list[str],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit References")
        self.setModal(True)
        self.resize(500, 400)

        v = QtWidgets.QVBoxLayout(self)
        msg = QtWidgets.QLabel(
            "Select other drones whose conversation history will be "
            "provided as context to this agent. This works across different "
            "models and providers."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#5b6068; margin-bottom:8px;")
        v.addWidget(msg)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        self.list_widget.setStyleSheet(
            "QListWidget::item{padding:6px;border-radius:4px;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )

        for a in all_actions:
            if a["id"] == current_action_id:
                continue
            
            snap = a.get("blueprint_snapshot") or {}
            name = a.get("name") or snap.get("name") or "Drone"
            item = QtWidgets.QListWidgetItem(f"{name} ({a['id'][:8]})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, a["id"])
            self.list_widget.addItem(item)
            if a["id"] in existing_refs:
                item.setSelected(True)

        v.addWidget(self.list_widget)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def picked_ids(self) -> list[str]:
        return [
            str(item.data(QtCore.Qt.ItemDataRole.UserRole))
            for item in self.list_widget.selectedItems()
        ]
