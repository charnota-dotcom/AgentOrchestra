"""Composer page — archetype picker + form-driven wizard.

V1 implementation:
1. Lists available cards from ``cards.list``.
2. Shows the variables of the selected card's template as a form.
3. Renders the template via ``templates.render`` and displays the
   rendered prompt next to the form (read-only).
4. Runs the pre-flight linter on the rendered text and displays
   warnings/errors inline.
5. Shows a cost forecast.

Dispatch (actually starting the Run) is the next step in the dispatch
subsystem and is wired in week 4.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class ComposerPage(QtWidgets.QWidget):
    dispatched = QtCore.Signal(str, str)  # (run_id, card_name)

    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._cards: list[dict[str, Any]] = []
        self._workspaces: list[dict[str, Any]] = []
        self._current_card: dict[str, Any] | None = None
        self._template: dict[str, Any] | None = None
        self._last_instruction_id: str | None = None
        self._last_rendered: str | None = None
        self._reloading = False

        self.setStyleSheet("background:#fafbfc;")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        # Left: archetype list + form
        left = QtWidgets.QFrame()
        left.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(12)

        ll.addWidget(self._h2("Pick an agent"))
        self.cards_list = QtWidgets.QListWidget()
        self.cards_list.setMaximumHeight(140)
        self.cards_list.currentRowChanged.connect(self._on_card_selected)
        ll.addWidget(self.cards_list)

        # Workspace picker (only meaningful for agentic cards).
        ws_row = QtWidgets.QHBoxLayout()
        ws_row.addWidget(QtWidgets.QLabel("Workspace:"))
        self.workspace_combo = QtWidgets.QComboBox()
        ws_row.addWidget(self.workspace_combo, stretch=1)
        ll.addLayout(ws_row)

        ll.addWidget(self._h2("Tell it what to do"))
        self.form_host = QtWidgets.QFormLayout()
        self.form_host.setSpacing(10)
        form_widget = QtWidgets.QWidget()
        form_widget.setLayout(self.form_host)
        ll.addWidget(form_widget, stretch=1)

        layout.addWidget(left, stretch=2)

        # Right: rendered prompt + lints + dispatch button
        right = QtWidgets.QFrame()
        right.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(16, 16, 16, 16)
        rl.setSpacing(12)

        rl.addWidget(self._h2("What the agent will see"))
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet(
            "background:#0f1115;color:#dee0e3;border-radius:4px;"
            "font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:8px;"
        )
        rl.addWidget(self.preview, stretch=1)

        self.lint_box = QtWidgets.QLabel()
        self.lint_box.setWordWrap(True)
        self.lint_box.setStyleSheet("color:#0f1115;")
        rl.addWidget(self.lint_box)

        self.cost_box = QtWidgets.QLabel("Cost forecast: —")
        self.cost_box.setStyleSheet("color:#5b6068;")
        rl.addWidget(self.cost_box)

        bar = QtWidgets.QHBoxLayout()
        bar.addStretch(1)
        self.dictate_btn = QtWidgets.QPushButton("🎙 Dictate")
        self.dictate_btn.setToolTip(
            "Record a short voice memo and transcribe locally.  Requires faster-whisper installed."
        )
        self.dictate_btn.clicked.connect(self._dictate)  # type: ignore[arg-type]
        bar.addWidget(self.dictate_btn)
        self.preview_btn = QtWidgets.QPushButton("Preview")
        self.preview_btn.clicked.connect(self._preview)  # type: ignore[arg-type]
        bar.addWidget(self.preview_btn)
        self.dispatch_btn = QtWidgets.QPushButton("Dispatch")
        self.dispatch_btn.setEnabled(False)
        self.dispatch_btn.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;padding:8px 14px;border-radius:4px;}"
            "QPushButton:disabled{background:#aab1bb;color:#fff;}"
        )
        self.dispatch_btn.clicked.connect(self._dispatch)  # type: ignore[arg-type]
        bar.addWidget(self.dispatch_btn)
        rl.addLayout(bar)

        layout.addWidget(right, stretch=3)

        QtCore.QTimer.singleShot(0, self._reload_cards)

    @staticmethod
    def _h2(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("font-size:13px;font-weight:600;color:#0f1115;")
        return lbl

    def _reload_cards(self) -> None:
        asyncio.ensure_future(self._reload_cards_async())

    async def _reload_cards_async(self) -> None:
        if self._reloading:
            return
        self._reloading = True
        try:
            self._cards = await self.client.call("cards.list", {})
            self._workspaces = await self.client.call("workspaces.list", {})
        except Exception as exc:
            self.cards_list.clear()
            self.workspace_combo.clear()
            self.preview.setPlainText(f"RPC unavailable: {exc}")
            return
        finally:
            self._reloading = False
        self.cards_list.clear()
        for c in self._cards:
            badge = " · agentic" if c.get("mode") == "agentic" else ""
            item = QtWidgets.QListWidgetItem(f"{c['name']}{badge}  —  {c['description']}")
            self.cards_list.addItem(item)
        self.workspace_combo.clear()
        self.workspace_combo.addItem("(no workspace)", None)
        for w in self._workspaces:
            self.workspace_combo.addItem(f"{w['name']} — {w['repo_path']}", w["id"])
        if self._cards:
            self.cards_list.setCurrentRow(0)

    def _on_card_selected(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._cards):
            return
        self._current_card = self._cards[idx]
        asyncio.ensure_future(self._load_template_async())

    async def _load_template_async(self) -> None:
        while self.form_host.rowCount():
            self.form_host.removeRow(0)
        self._inputs: dict[str, QtWidgets.QWidget] = {}
        if not self._current_card:
            return
        try:
            tmpl = await self.client.call(
                "templates.get",
                {"template_id": self._current_card["template_id"]},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Template error", str(exc))
            return
        for v in tmpl.get("variables", []):
            kind = v.get("kind", "text")
            multiline = kind == "text"
            self._add_input(
                v["name"],
                v.get("label") or v["name"],
                multiline=multiline,
                required=bool(v.get("required", False)),
                default=v.get("default"),
            )

    def _add_input(
        self,
        name: str,
        label: str,
        *,
        multiline: bool,
        required: bool,
        default: Any = None,
    ) -> None:
        if multiline:
            w: QtWidgets.QPlainTextEdit | QtWidgets.QLineEdit = QtWidgets.QPlainTextEdit()
            w.setMinimumHeight(80)
            if default:
                w.setPlainText(str(default))
        else:
            w = QtWidgets.QLineEdit()
            if default is not None:
                w.setText(str(default))
        self._inputs[name] = w
        self.form_host.addRow(QtWidgets.QLabel(label + (" *" if required else "")), w)

    def _collect_variables(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, w in self._inputs.items():
            if isinstance(w, QtWidgets.QPlainTextEdit):
                out[k] = w.toPlainText().strip()
            elif isinstance(w, QtWidgets.QLineEdit):
                out[k] = w.text().strip()
        return out

    def _dictate(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Pick an audio recording (wav / mp3 / m4a)",
            filter="Audio (*.wav *.mp3 *.m4a *.ogg *.flac);;All files (*)",
        )
        if not path:
            return
        asyncio.ensure_future(self._dictate_async(path))

    async def _dictate_async(self, audio_path: str) -> None:
        try:
            res = await self.client.call(
                "dictation.transcribe",
                {"audio_path": audio_path},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Dictation failed",
                f"{exc}\n\nIs faster-whisper installed?",
            )
            return
        text = res.get("text", "").strip()
        if not text:
            return
        # Drop the transcript into the first text input we have.
        for widget in self._inputs.values():
            if isinstance(widget, QtWidgets.QPlainTextEdit):
                widget.setPlainText((widget.toPlainText() + "\n" + text).strip())
                return
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.setText(text)
                return

    def _preview(self) -> None:
        asyncio.ensure_future(self._preview_async())

    async def _preview_async(self) -> None:
        if not self._current_card:
            return
        vars_ = self._collect_variables()
        try:
            res = await self.client.call(
                "templates.render",
                {
                    "template_id": self._current_card["template_id"],
                    "card_id": self._current_card["id"],
                    "variables": vars_,
                },
            )
        except Exception as exc:
            self.preview.setPlainText(f"Render failed: {exc}")
            return
        self.preview.setPlainText(res["rendered_text"])
        self._last_instruction_id = res["instruction_id"]
        self._last_rendered = res["rendered_text"]

        try:
            issues = await self.client.call(
                "lint.instruction",
                {
                    "text": res["rendered_text"],
                    "archetype": self._current_card["archetype"],
                    "variables": vars_,
                },
            )
        except Exception:
            issues = []

        self._render_lints(issues)

        try:
            f = await self.client.call(
                "cost.forecast",
                {
                    "provider": self._current_card["provider"],
                    "model": self._current_card["model"],
                    "rendered_prompt_tokens": max(1, len(res["rendered_text"]) // 4),
                    "archetype": self._current_card["archetype"],
                },
            )
            self.cost_box.setText(
                f"Cost forecast: ${f['low_usd']:.2f} – ${f['high_usd']:.2f} "
                f"(expected ${f['expected_usd']:.2f})"
            )
        except Exception:
            self.cost_box.setText("Cost forecast unavailable")

        blocking = any(i["severity"] == "error" for i in issues)
        self.dispatch_btn.setEnabled(not blocking and bool(res["rendered_text"].strip()))

    def _dispatch(self) -> None:
        if not self._current_card or not self._last_instruction_id or not self._last_rendered:
            return
        asyncio.ensure_future(self._dispatch_async())

    async def _dispatch_async(self) -> None:
        if not self._current_card or not self._last_instruction_id or not self._last_rendered:
            return
        ws_id = self.workspace_combo.currentData()
        is_agentic = self._current_card.get("mode") == "agentic"
        if is_agentic and not ws_id:
            QtWidgets.QMessageBox.warning(
                self,
                "Workspace required",
                "This agent edits files; pick a workspace before dispatching.",
            )
            return
        try:
            res = await self.client.call(
                "runs.dispatch",
                {
                    "card_id": self._current_card["id"],
                    "instruction_id": self._last_instruction_id,
                    "rendered_text": self._last_rendered,
                    "workspace_id": ws_id,
                },
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Dispatch failed", str(exc))
            return
        # Hand over to the Live pane.
        self.dispatched.emit(res["run_id"], self._current_card["name"])
        self.dispatch_btn.setEnabled(False)

    def _render_lints(self, issues: list[dict[str, Any]]) -> None:
        if not issues:
            self.lint_box.setText("✓ No pre-flight issues.")
            self.lint_box.setStyleSheet("color:#1f7a3f;")
            return
        parts = []
        for i in issues:
            sev = i["severity"]
            color = {"error": "#b3261e", "warning": "#a96b00", "info": "#5b6068"}.get(
                sev, "#0f1115"
            )
            parts.append(
                f"<div style='color:{color}'><b>{sev.upper()}</b> "
                f"{i['message']}{' — ' + i['suggestion'] if i.get('suggestion') else ''}</div>"
            )
        self.lint_box.setText("".join(parts))
        self.lint_box.setStyleSheet("color:#0f1115;")
