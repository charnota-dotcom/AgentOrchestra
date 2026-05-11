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

from apps.gui.browser_bridge import BrowserBridgeDialog
from apps.gui.widgets.context_gauge import ContextGauge
from apps.service.tokens import context_window, estimate_action_total

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _render_html(transcript: list[dict[str, Any]]) -> str:
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
        or "<i style='color:#7a7d85;'>(no messages yet — type one below to start)</i>"
    )


def _render_tool_call_html(entry: dict[str, Any]) -> str:
    """Render a ``tool_call`` transcript entry surfaced by the
    claude-cli stream-json path.  Sub-agent invocations
    (``tool_name == "Task"``) get a coloured badge so the operator
    can tell them from regular tool calls.
    """
    import json as _json

    name = _html.escape(str(entry.get("tool_name") or "?"))
    is_subagent = bool(entry.get("is_subagent"))
    step = entry.get("step") or 0
    badge = (
        '<span style="background:#7c3aed;color:#fff;font-size:10px;'
        'padding:1px 6px;border-radius:9px;margin-left:6px;">sub-agent</span>'
        if is_subagent
        else ""
    )
    try:
        input_text = _json.dumps(entry.get("tool_input") or {}, indent=2, default=str)
    except (TypeError, ValueError):
        input_text = str(entry.get("tool_input") or {})
    # Truncate FIRST, then escape — slicing inside an escaped entity
    # like ``&quot;`` leaks malformed markup into the QTextEdit and
    # corrupts the rich-text layout for the rest of the document.
    if len(input_text) > 1200:
        input_text = input_text[:1200] + "\n... (truncated)"
    input_text = _html.escape(input_text)
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
    output = str(entry.get("tool_output") or "")
    # Truncate first; escape afterwards so we never slice through an
    # in-flight entity.
    if len(output) > 1500:
        output = output[:1500] + "\n... (truncated)"
    output = _html.escape(output)
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

        # Context-window gauge.  Compact layout for the dialog's
        # tighter horizontal space.  Hidden if the model pair is
        # unknown; otherwise lit up immediately with the size of
        # whatever transcript we opened with.
        self.context_gauge = ContextGauge(parent=self, compact=True)
        v.addWidget(self.context_gauge)
        self._refresh_gauge()

        shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.message_input)
        shortcut.activated.connect(self._send)  # type: ignore[arg-type]

    def _refresh_gauge(self) -> None:
        """Update the context gauge from the action snapshot.

        Used on dialog open + as a fallback when the server response
        doesn't carry fresh token totals (e.g. older service).
        """
        snap = self.action.get("blueprint_snapshot") or {}
        provider = snap.get("provider")
        model = snap.get("model")
        if not provider or not model:
            self.context_gauge.update(None, None)
            return
        total = estimate_action_total(
            self.action,
            system_prompt=snap.get("system_persona") or "",
            provider=provider,
            model=model,
        )
        self.context_gauge.update(total, context_window(provider, model))

    def _send(self) -> None:
        # Ctrl+Return bypasses the visual disabled-button gate, so an
        # impatient operator can mash the shortcut and queue duplicate
        # drones.send RPCs against the same action.  Honour the button
        # state explicitly.
        if not self.send_btn.isEnabled():
            return
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
            msg = str(exc).strip()
            body = f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
            body += (
                "\n\nFor a deeper trace, run scripts\\doctor.cmd — its "
                "`--- Recent service log ---` section shows the last "
                "lines the auto-spawned service wrote to stderr."
            )
            QtWidgets.QMessageBox.critical(self, "Send failed", body)
            self.send_btn.setEnabled(True)
            return
        # Browser-mode drones return needs_paste=True instead of an
        # immediate reply.  Open the BrowserBridgeDialog and let it
        # drive the round-trip; once the operator pastes back, our
        # action snapshot refreshes via the dialog's saved signal.
        action_out = out.get("action") or self.action
        if out.get("needs_paste"):
            self._open_browser_bridge(action_out, out)
            return
        self.action = action_out
        self.transcript.setHtml(_render_html(self.action.get("transcript") or []))
        # Prefer the server's fresh totals; fall back to a local
        # estimate if the response is missing them (e.g. older service).
        if out.get("context_window") is not None:
            self.context_gauge.update(
                out.get("transcript_tokens"),
                out.get("context_window"),
            )
        else:
            self._refresh_gauge()
        self.send_btn.setEnabled(True)
        # Bubble up so the canvas can refresh the node.
        self.sent.emit(self.action)

    def _open_browser_bridge(
        self,
        action: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        """Open the BrowserBridgeDialog from a browser-mode
        ``drones.send`` response on the canvas chat surface.

        Mirrors the equivalent helper on ``apps/gui/windows/drones.py``
        — same dialog, same lifecycle, with our local transcript view
        + gauge refreshed when the operator saves.
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
            self.action = updated_action
            self.transcript.setHtml(_render_html(updated_action.get("transcript") or []))
            self._refresh_gauge()
            self.send_btn.setEnabled(True)
            self.sent.emit(self.action)

        dlg.saved.connect(_on_saved)  # type: ignore[arg-type]
        dlg.rejected.connect(lambda: self.send_btn.setEnabled(True))  # type: ignore[arg-type]
        # Hold the Python wrapper on the dialog instance — without this
        # the local ``dlg`` falls out of scope as soon as the method
        # returns, the GC reaps the wrapper, and the non-modal C++
        # window flashes and vanishes before the user can paste.
        self._bridge_dlg = dlg
        dlg.show()
        # Reflect the partial transcript (with the user turn already
        # persisted) immediately so the operator sees what was sent.
        self.transcript.setHtml(_render_html(action.get("transcript") or []))
