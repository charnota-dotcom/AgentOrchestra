"""BrowserBridgeDialog — the operator-facing copy/paste dialog.

Opens when the operator hits Send on a ``provider="browser"`` drone.
Two textboxes (rendered prompt on top, paste-back reply on bottom),
a Copy button, a Save reply button, and a small status line driven
by the ``ClipboardListener``.  Optionally shows a ``ContextGauge``
in the header (from ``apps.gui.widgets.context_gauge``) so the
operator sees prompt size + projected post-paste total.

Hard-import rule: this module only depends on Qt, the rest of the
``browser_bridge`` sub-package, and the pure-data
``apps.service.tokens`` package.  No ``apps.gui.ipc`` or
``apps.service.providers``.  The caller injects an ``RpcClient``
typed via ``TYPE_CHECKING`` so the actual class isn't imported at
runtime.

See ``docs/BROWSER_PROVIDER_PLAN.md`` for the full UX walkthrough.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.browser_bridge.clipboard_listener import (
    ClipboardEvent,
    ClipboardListener,
)
from apps.gui.browser_bridge.url_launcher import open_url, set_clipboard
from apps.gui.widgets.context_gauge import ContextGauge

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class BrowserBridgeDialog(QtWidgets.QDialog):
    """Operator-facing dialog for browser-mode drones.

    Construct with:

        BrowserBridgeDialog(
            client=rpc_client,
            action_id=action_id,
            rendered_prompt=prompt_str,
            chat_url=blueprint_chat_url,
            bound_chat_url=action_bound_chat_url_or_None,
            prompt_tokens=int_or_None,
            transcript_tokens=int_or_None,
            context_window=int_or_None,
            parent=parent_widget,
        )

    Emits ``saved(updated_action_dict)`` after a successful paste-back.
    Non-modal so the operator can keep working on other drones.
    """

    saved = QtCore.Signal(dict)

    def __init__(
        self,
        *,
        client: RpcClient,
        action_id: str,
        rendered_prompt: str,
        chat_url: str | None,
        bound_chat_url: str | None,
        prompt_tokens: int | None = None,
        transcript_tokens: int | None = None,
        context_window: int | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._action_id = action_id
        self._rendered_prompt = rendered_prompt
        self._chat_url = chat_url
        self._bound_chat_url = bound_chat_url

        self.setWindowTitle("Browser bridge — paste this into your chat tab")
        self.setModal(False)
        self.resize(680, 600)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        # Header: which conversation is bound (if any), and the gauge.
        header = QtWidgets.QHBoxLayout()
        self._link_label = QtWidgets.QLabel()
        self._link_label.setStyleSheet("color:#5b6068;font-size:11px;")
        self._link_label.setOpenExternalLinks(False)
        self._link_label.linkActivated.connect(self._on_link_clicked)  # type: ignore[arg-type]
        self._link_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._refresh_link_label()
        header.addWidget(self._link_label, stretch=1)
        self._gauge = ContextGauge(parent=self, compact=True)
        self._gauge.set_token_counts(transcript_tokens, context_window)
        header.addWidget(self._gauge)
        v.addLayout(header)

        # Top textbox: read-only rendered prompt + Copy button.
        v.addWidget(self._small_label("Prompt to paste"))
        prompt_row = QtWidgets.QHBoxLayout()
        prompt_row.setSpacing(8)
        self._prompt_view = QtWidgets.QPlainTextEdit()
        self._prompt_view.setReadOnly(True)
        self._prompt_view.setPlainText(rendered_prompt)
        self._prompt_view.setStyleSheet(
            "QPlainTextEdit{background:#f6f8fa;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:8px;font-family:ui-monospace,"
            "Consolas,Menlo,monospace;font-size:12px;}"
        )
        self._prompt_view.setMinimumHeight(180)
        prompt_row.addWidget(self._prompt_view, stretch=1)

        prompt_buttons = QtWidgets.QVBoxLayout()
        prompt_buttons.setSpacing(6)
        copy_btn = QtWidgets.QPushButton("Copy")
        copy_btn.setToolTip("Copy the rendered prompt to your clipboard.")
        copy_btn.setStyleSheet(self._primary_button_style())
        copy_btn.clicked.connect(self._on_copy)  # type: ignore[arg-type]
        prompt_buttons.addWidget(copy_btn)
        open_btn = QtWidgets.QPushButton("Open chat")
        open_btn.setToolTip(
            "Open the chat URL in your default browser.  If a specific "
            "conversation is bound, that conversation re-opens; "
            "otherwise the blueprint's default URL is used."
        )
        open_btn.setStyleSheet(self._secondary_button_style())
        open_btn.clicked.connect(self._on_open_chat)  # type: ignore[arg-type]
        prompt_buttons.addWidget(open_btn)
        copy_and_open_btn = QtWidgets.QPushButton("Copy + Open")
        copy_and_open_btn.setToolTip(
            "Copy the prompt AND open the chat tab in one click.  "
            "The everyday button for browser-mode drones."
        )
        copy_and_open_btn.setStyleSheet(self._primary_button_style())
        copy_and_open_btn.clicked.connect(self._on_copy_and_open)  # type: ignore[arg-type]
        prompt_buttons.addWidget(copy_and_open_btn)
        prompt_buttons.addStretch(1)
        prompt_row.addLayout(prompt_buttons)
        v.addLayout(prompt_row)

        # Status line — driven by the clipboard listener.
        self._status_label = QtWidgets.QLabel(
            "Listening for the reply you copy from your browser..."
        )
        self._status_label.setStyleSheet("color:#5b6068;font-size:11px;font-style:italic;")
        self._status_label.setWordWrap(True)
        v.addWidget(self._status_label)

        # Bottom textbox: paste the reply here.  Operator can edit
        # before saving (in case they want to trim claude.ai noise).
        v.addWidget(
            self._small_label("Paste claude.ai's reply here (or let the listener catch it)")
        )
        self._reply_view = QtWidgets.QPlainTextEdit()
        self._reply_view.setPlaceholderText(
            "Paste the assistant reply here.  Or simply select-and-copy "
            "from your browser tab and we'll pick it up automatically."
        )
        self._reply_view.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #e6e7eb;"
            "border-radius:6px;padding:8px;"
            "font-family:ui-sans-serif,Inter,system-ui;font-size:13px;}"
        )
        self._reply_view.setMinimumHeight(140)
        self._reply_view.textChanged.connect(self._update_save_enabled)  # type: ignore[arg-type]
        v.addWidget(self._reply_view, stretch=1)

        # Bottom button row: Save / Cancel.
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._secondary_button_style())
        cancel_btn.clicked.connect(self.reject)  # type: ignore[arg-type]
        bottom.addWidget(cancel_btn)
        self._save_btn = QtWidgets.QPushButton("Save reply")
        self._save_btn.setStyleSheet(self._primary_button_style())
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)  # type: ignore[arg-type]
        bottom.addWidget(self._save_btn)
        v.addLayout(bottom)

        # Clipboard listener — starts on open, stopped on close.
        self._listener = ClipboardListener(self._on_clipboard_event, parent=self)
        self._listener.start()

    # ------------------------------------------------------------------
    # Header / link
    # ------------------------------------------------------------------

    def _refresh_link_label(self) -> None:
        if self._bound_chat_url:
            short = self._shorten_url(self._bound_chat_url)
            self._link_label.setText(
                f'<a href="{self._bound_chat_url}" '
                f'style="color:#1f6feb;text-decoration:none;">'
                f"🔗 {short}</a>  "
                "<span style='color:#7a7d85;'>(bound)</span>"
            )
        elif self._chat_url:
            short = self._shorten_url(self._chat_url)
            self._link_label.setText(
                f'<a href="{self._chat_url}" '
                f'style="color:#1f6feb;text-decoration:none;">'
                f"🔗 {short}</a>  "
                "<span style='color:#7a7d85;'>(blueprint default; "
                "first reply will pin a specific conversation)</span>"
            )
        else:
            self._link_label.setText("<span style='color:#7a7d85;'>(no chat URL configured)</span>")

    @staticmethod
    def _shorten_url(url: str) -> str:
        """Trim a URL to ~60 chars for display in the header."""
        if len(url) <= 60:
            return url
        return url[:30] + "..." + url[-25:]

    def _on_link_clicked(self, url: str) -> None:
        open_url(url)

    # ------------------------------------------------------------------
    # Top-row actions
    # ------------------------------------------------------------------

    def _on_copy(self) -> None:
        set_clipboard(self._rendered_prompt)
        self._status_label.setText(
            "Prompt copied to clipboard.  Switch to your browser, paste, "
            "hit Enter, then copy the reply when it's ready."
        )

    def _on_open_chat(self) -> None:
        target = self._bound_chat_url or self._chat_url
        if not target:
            self._status_label.setText("No chat URL configured for this drone.")
            return
        open_url(target)

    def _on_copy_and_open(self) -> None:
        self._on_copy()
        self._on_open_chat()

    # ------------------------------------------------------------------
    # Clipboard listener wiring
    # ------------------------------------------------------------------

    def _on_clipboard_event(self, event: ClipboardEvent) -> None:
        # Ignore the operator's own copy of the prompt — we just put
        # it on the clipboard ourselves.  Cheap text equality check.
        if event.text.strip() == self._rendered_prompt.strip():
            return
        self._reply_view.setPlainText(event.text)
        self._update_save_enabled()
        source_bits = []
        if event.source_url:
            source_bits.append(f"from {self._shorten_url(event.source_url)}")
        if event.captured_at:
            source_bits.append(self._humanise_ts(event.captured_at))
        suffix = "  ·  ".join(source_bits)
        if suffix:
            self._status_label.setText(f"Captured reply ({suffix}).")
        else:
            self._status_label.setText("Captured reply from clipboard.")
        # If we have a fresh source URL and the drone isn't bound yet,
        # remember it for the bind-on-save step below.
        if event.source_url and not self._bound_chat_url:
            self._bound_chat_url = event.source_url
            self._refresh_link_label()

    @staticmethod
    def _humanise_ts(ts: datetime) -> str:
        now = datetime.now(tz=UTC)
        delta = max(0.0, (now - ts).total_seconds())
        if delta < 60:
            return f"{delta:.0f}s ago"
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        return f"{delta / 3600:.1f}h ago"

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _update_save_enabled(self) -> None:
        self._save_btn.setEnabled(bool(self._reply_view.toPlainText().strip()))

    def _on_save(self) -> None:
        content = self._reply_view.toPlainText().strip()
        if not content:
            return
        self._save_btn.setEnabled(False)
        asyncio.ensure_future(self._save_async(content))

    async def _save_async(self, content: str) -> None:
        try:
            out = await self._client.call(
                "drones.append_assistant_turn",
                {"action_id": self._action_id, "content": content},
            )
        except Exception as exc:
            msg = str(exc).strip()
            body = f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
            body += (
                "\n\nFor a deeper trace, run scripts\\doctor.cmd — its "
                "`--- Recent service log ---` section shows the last "
                "lines the auto-spawned service wrote to stderr."
            )
            QtWidgets.QMessageBox.critical(self, "Save reply failed", body)
            self._save_btn.setEnabled(True)
            return
        action = out.get("action") or {}
        # If we captured a source URL during this session and the drone
        # wasn't already bound, persist it now via drones.bind_chat_url
        # so future pastes from this conversation route here.
        if self._bound_chat_url and not action.get("bound_chat_url"):
            try:
                bind_out = await self._client.call(
                    "drones.bind_chat_url",
                    {"action_id": self._action_id, "url": self._bound_chat_url},
                )
                action = bind_out.get("action") or action
            except Exception:
                # Soft failure — the URL just won't be pinned this turn.
                pass
        self._listener.stop()
        self.saved.emit(action)
        self.accept()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._listener.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _small_label(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(
            "color:#5b6068;font-size:11px;font-weight:600;"
            "text-transform:uppercase;letter-spacing:0.05em;"
        )
        return lbl

    @staticmethod
    def _primary_button_style() -> str:
        return (
            "QPushButton{padding:8px 14px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;font-size:12px;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )

    @staticmethod
    def _secondary_button_style() -> str:
        return (
            "QPushButton{padding:8px 14px;background:#fff;color:#0f1115;"
            "border:1px solid #d0d3d9;border-radius:4px;font-size:12px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
