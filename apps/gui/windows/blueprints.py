"""Blueprints tab — operator-only editor for drone templates.

A *blueprint* is a frozen template (provider + model + system persona +
default skills + default reference blueprints) that an operator
"deploys" into a *drone action* — a live conversation.  The Blueprints
tab is just the template editor; deployment + chat live on the
Drones tab (added in PR #24).

Layout (left → right):
* Sidebar — list of blueprints, ordered by recency.  ``+ New`` opens a
  minimal dialog; clicking a row loads the row into the editor.
* Centre — full editor for the selected blueprint (name, description,
  role, provider, model, system persona, skills, reference blueprints).
  Save bumps version + persists; conflict surfaces as a toast.

See ``docs/DRONE_MODEL.md`` for the design and the authority matrix.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


_ROLE_LABELS: tuple[tuple[str, str], ...] = (
    # value, human label.  Order matches the Authority matrix in
    # docs/DRONE_MODEL.md so the dropdown reads the same way.
    ("worker", "Worker — self-contained chat, cannot mutate peers"),
    ("supervisor", "Supervisor — full peer authority (refs, skills, attachments)"),
    ("courier", "Courier — can append references onto peers"),
    ("auditor", "Auditor — read-only, cannot mutate even self"),
)

# Subset of providers we currently route to.  Stays in sync with the
# Agents tab's ``_AGENTS_TAB_PRESETS`` until PR #24 unifies them.
_PROVIDERS: tuple[str, ...] = ("claude-cli", "gemini-cli", "browser")

# Default chat URL pre-filled when the operator picks ``provider="browser"``.
# Operator can change to ChatGPT / Gemini / anything URL-addressable.
_DEFAULT_CHAT_URL = "https://claude.ai/new"

# Per-provider model defaults.  An operator can type a custom model
# string into the QLineEdit instead — these are just convenience picks.
_MODEL_HINTS: dict[str, tuple[str, ...]] = {
    "claude-cli": ("claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"),
    "gemini-cli": ("gemini-2.5-pro", "gemini-2.5-flash"),
    # Browser-mode shares model names with the underlying service; the
    # operator picks whichever model they're paying for in the browser.
    "browser": (
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-haiku-4-5",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ),
}


def _belongs_to_other_provider(model: str, provider: str) -> bool:
    """True iff `model` is a known hint for some provider other than
    `provider` and is NOT a hint for `provider` itself.

    Drives the "tie model to provider" behaviour on the editor +
    create-dialog combo boxes: switching from claude-cli to gemini-cli
    while ``claude-sonnet-4-6`` is selected should reset the model
    field, but a custom string the operator typed (not present in any
    hint set) should be preserved across the switch.
    """
    if not model:
        return False
    if model in _MODEL_HINTS.get(provider, ()):
        return False
    return any(model in hints for hints in _MODEL_HINTS.values())


def _split_csv(text: str) -> list[str]:
    """Split a comma- or whitespace-separated input into a clean list.

    Used for the skills + reference-blueprint-ids inputs.  Strips
    blanks + de-duplicates while preserving order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in text.replace("\n", ",").split(","):
        token = raw.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


class BlueprintsPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._blueprints: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar(), stretch=0)
        layout.addWidget(self._build_editor(), stretch=1)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload()))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("background:#fff;border-right:1px solid #e6e7eb;")
        wrap.setFixedWidth(260)
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Blueprints")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        header.addWidget(title)
        header.addStretch(1)
        new_btn = QtWidgets.QPushButton("+ New")
        new_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#f6f8fa;font-size:12px;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        new_btn.clicked.connect(self._new_dialog)  # type: ignore[arg-type]
        header.addWidget(new_btn)
        v.addLayout(header)

        hint = QtWidgets.QLabel(
            "Operator-only.  A blueprint is a reusable template; deploy from "
            "the Drones tab to chat."
        )
        hint.setStyleSheet("color:#5b6068;font-size:11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget{border:none;background:transparent;}"
            "QListWidget::item{padding:8px 6px;border-radius:4px;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )
        self.list_widget.currentRowChanged.connect(self._on_select)  # type: ignore[arg-type]
        v.addWidget(self.list_widget, stretch=1)

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
    # Centre — editor form
    # ------------------------------------------------------------------

    def _build_editor(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(24, 18, 24, 18)
        v.setSpacing(12)

        self.title = QtWidgets.QLabel("(no blueprint selected)")
        self.title.setStyleSheet("font-size:18px;font-weight:600;color:#0f1115;")
        v.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(self.subtitle)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.name_in = QtWidgets.QLineEdit()
        self.name_in.setPlaceholderText("e.g. Code reviewer")
        # textChanged drives the Save-enabled state — the operator can
        # type a name into the big form (without first clicking + New)
        # and save: we'll create-on-empty in `_save_async`.
        self.name_in.textChanged.connect(self._update_save_enabled)  # type: ignore[arg-type]
        form.addRow("Name", self.name_in)

        self.description_in = QtWidgets.QLineEdit()
        self.description_in.setPlaceholderText("Short description (optional)")
        form.addRow("Description", self.description_in)

        self.role_in = QtWidgets.QComboBox()
        for value, label in _ROLE_LABELS:
            self.role_in.addItem(label, value)
        self.role_in.setToolTip(
            "Authority on the snapshotted role.  Worker can chat but not "
            "mutate peers; supervisor can mutate any peer; courier can append "
            "references; auditor is read-only."
        )
        form.addRow("Role", self.role_in)

        self.provider_in = QtWidgets.QComboBox()
        for p in _PROVIDERS:
            self.provider_in.addItem(p, p)
        self.provider_in.currentTextChanged.connect(self._refresh_model_hints)  # type: ignore[arg-type]
        self.provider_in.currentTextChanged.connect(self._refresh_provider_rows)  # type: ignore[arg-type]
        form.addRow("Provider", self.provider_in)

        self.model_in = QtWidgets.QComboBox()
        self.model_in.setEditable(True)
        self._refresh_model_hints(_PROVIDERS[0])
        self._model_label = QtWidgets.QLabel("Model")
        form.addRow(self._model_label, self.model_in)

        # Chat URL row — only meaningful when provider="browser".
        # The row is shown/hidden via _refresh_provider_rows so other
        # providers don't see a misleading empty box.  See
        # docs/BROWSER_PROVIDER_PLAN.md.
        self.chat_url_in = QtWidgets.QLineEdit()
        self.chat_url_in.setPlaceholderText(_DEFAULT_CHAT_URL)
        self.chat_url_in.setToolTip(
            "Where the GUI opens when you Send a message — the chat product "
            "you'll paste into.  claude.ai/new is the default; replace with "
            "https://chatgpt.com/, https://gemini.google.com/app, or any "
            "URL-addressable chat product."
        )
        self._chat_url_label = QtWidgets.QLabel("Chat URL")
        form.addRow(self._chat_url_label, self.chat_url_in)

        self.system_persona_in = QtWidgets.QPlainTextEdit()
        self.system_persona_in.setPlaceholderText(
            "Operator-typed persona.  Goes verbatim into the system prompt, "
            'e.g. "You are a careful code reviewer.  Cite line numbers."'
        )
        self.system_persona_in.setFixedHeight(120)
        self.system_persona_in.setStyleSheet(
            "QPlainTextEdit{background:#fff;border:1px solid #d0d3d9;"
            "border-radius:4px;padding:8px;font-size:13px;"
            "font-family:ui-sans-serif,Inter,system-ui;}"
        )
        form.addRow("System persona", self.system_persona_in)

        self.skills_in = QtWidgets.QLineEdit()
        self.skills_in.setPlaceholderText(
            "/research-deep, /cite-sources  (comma- or newline-separated tokens)"
        )
        self.skills_in.setToolTip(
            "Skill tokens prepended to every prompt for actions deployed from "
            "this blueprint.  Drones deployed from this blueprint can layer "
            "additional one-off skills but cannot remove these defaults."
        )
        form.addRow("Default skills", self.skills_in)

        self.refs_in = QtWidgets.QLineEdit()
        self.refs_in.setPlaceholderText(
            "blueprint-id-1, blueprint-id-2  (each action gets the latest "
            "action of these blueprints inlined as context)"
        )
        self.refs_in.setToolTip(
            "Default reference blueprint ids.  Every action deployed from "
            "this blueprint inherits a reference to the most recent action "
            "of each listed blueprint."
        )
        form.addRow("Reference blueprints", self.refs_in)

        v.addLayout(form)

        # Save row.
        save_row = QtWidgets.QHBoxLayout()
        save_row.addStretch(1)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("color:#5b6068;font-size:11px;")
        save_row.addWidget(self.status_label)
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setStyleSheet(
            "QPushButton{padding:8px 24px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;font-size:13px;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        self.save_btn.clicked.connect(self._save)  # type: ignore[arg-type]
        self.save_btn.setEnabled(False)
        save_row.addWidget(self.save_btn)
        v.addLayout(save_row)

        v.addStretch(1)
        return wrap

    def _refresh_model_hints(self, provider: str) -> None:
        current = self.model_in.currentText()
        new_hints = _MODEL_HINTS.get(provider, ())
        self.model_in.blockSignals(True)
        self.model_in.clear()
        for m in new_hints:
            self.model_in.addItem(m, m)
        if _belongs_to_other_provider(current, provider) or not current:
            # Wrong-provider model (e.g. gemini-2.5-pro after switching
            # to claude-cli) or initial empty state — pick the new
            # provider's first hint as a sensible default.
            if new_hints:
                self.model_in.setEditText(new_hints[0])
        else:
            # Either matches the new provider already, or is a custom
            # operator-typed value not in any provider's hints —
            # preserve.
            self.model_in.setEditText(current)
        self.model_in.blockSignals(False)

    def _refresh_provider_rows(self, provider: str) -> None:
        """Show / hide rows that only apply to specific providers.

        Currently: the Chat URL row is only shown for
        ``provider="browser"``.  Other providers don't have a chat URL
        concept (their drone calls the CLI / API directly), so the
        row is hidden to avoid implying it does anything.  See
        docs/BROWSER_PROVIDER_PLAN.md.
        """
        is_browser = provider == "browser"
        self.chat_url_in.setVisible(is_browser)
        self._chat_url_label.setVisible(is_browser)
        if is_browser and not self.chat_url_in.text().strip():
            # Pre-fill the default so the operator can hit Save without
            # touching this row.
            self.chat_url_in.setText(_DEFAULT_CHAT_URL)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def _reload(self) -> None:
        try:
            self._blueprints = await self.client.call("blueprints.list", {})
        except Exception as e:
            self._set_status(f"Reload failed: {e}", error=True)
            return
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for bp in self._blueprints:
            label = bp.get("name") or "(unnamed)"
            role = bp.get("role") or "worker"
            provider = bp.get("provider") or ""
            model = bp.get("model") or ""
            item = QtWidgets.QListWidgetItem(f"{label}  ·  {role}\n{provider} / {model}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, bp["id"])
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self._current:
            # Re-select the row whose id matches what was open.
            for i, bp in enumerate(self._blueprints):
                if bp["id"] == self._current["id"]:
                    self.list_widget.setCurrentRow(i)
                    break

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._blueprints):
            self._current = None
            self.title.setText("(no blueprint selected)")
            self.subtitle.setText("")
            self.save_btn.setEnabled(False)
            return
        bp = self._blueprints[row]
        self._current = bp
        self.title.setText(bp.get("name") or "(unnamed)")
        self.subtitle.setText(
            f"id {bp['id']}  ·  v{bp.get('version', 1)}  ·  updated {bp.get('updated_at', '')}"
        )
        self.name_in.setText(bp.get("name") or "")
        self.description_in.setText(bp.get("description") or "")
        # Role.
        for i in range(self.role_in.count()):
            if self.role_in.itemData(i) == (bp.get("role") or "worker"):
                self.role_in.setCurrentIndex(i)
                break
        # Provider.
        for i in range(self.provider_in.count()):
            if self.provider_in.itemData(i) == bp.get("provider"):
                self.provider_in.setCurrentIndex(i)
                break
        self._refresh_model_hints(bp.get("provider") or _PROVIDERS[0])
        self.model_in.setEditText(bp.get("model") or "")
        self.chat_url_in.setText(bp.get("chat_url") or "")
        self._refresh_provider_rows(bp.get("provider") or _PROVIDERS[0])
        self.system_persona_in.setPlainText(bp.get("system_persona") or "")
        self.skills_in.setText(", ".join(bp.get("skills") or []))
        self.refs_in.setText(", ".join(bp.get("reference_blueprint_ids") or []))
        self.save_btn.setEnabled(True)
        self._set_status("")

    def _update_save_enabled(self) -> None:
        """Save is allowed when either a row is selected (update path)
        or the operator has typed a name into the big form (create-on-
        save path).  Wired to name_in.textChanged.  Without this, the
        big form looked editable but Save was permanently grey when no
        row was selected, with no in-app hint about clicking + New —
        operator dead end (annotation #13)."""
        has_selection = self._current is not None
        has_name = bool(self.name_in.text().strip())
        self.save_btn.setEnabled(has_selection or has_name)

    def _save(self) -> None:
        asyncio.ensure_future(self._save_async())

    async def _save_async(self) -> None:
        # Common form snapshot — both create and update use the same
        # field set (verified against apps/service/main.py:1007
        # blueprints_create which accepts the full shape).
        name = self.name_in.text().strip()
        if not name:
            # Defensive: _update_save_enabled should keep Save disabled
            # when name is empty AND nothing is selected, but belt and
            # braces in case the gating is bypassed.
            self._set_status("Name is required.", error=True)
            return
        # `chat_url` is sent for every provider but only consulted by
        # the service when provider == "browser".  Empty string maps
        # to None so non-browser blueprints store NULL (no clutter).
        chat_url = self.chat_url_in.text().strip() or None
        common = {
            "name": name,
            "description": self.description_in.text().strip(),
            "role": self.role_in.currentData(),
            "provider": self.provider_in.currentData(),
            "model": self.model_in.currentText().strip(),
            "system_persona": self.system_persona_in.toPlainText(),
            "skills": _split_csv(self.skills_in.text()),
            "reference_blueprint_ids": _split_csv(self.refs_in.text()),
            "chat_url": chat_url,
        }
        self.save_btn.setEnabled(False)
        if self._current is None:
            # Create-on-save: the operator typed into the big form
            # without first clicking + New.  Treat Save as "create this
            # blueprint now" so they aren't stuck staring at a greyed
            # button with no guidance.
            self._set_status("Creating…")
            try:
                created = await self.client.call("blueprints.create", common)
            except Exception as e:
                self._set_status(f"Create failed: {e}", error=True)
                self.save_btn.setEnabled(True)
                return
            self._current = created
            await self._reload()
            self._set_status(f"Created (v{created.get('version', 1)})")
            self.save_btn.setEnabled(True)
            return
        # Existing path: update the selected blueprint.
        params = {
            "id": self._current["id"],
            **common,
            "expected_version": self._current.get("version", 1),
        }
        self._set_status("Saving…")
        try:
            updated = await self.client.call("blueprints.update", params)
        except Exception as e:
            self._set_status(f"Save failed: {e}", error=True)
            self.save_btn.setEnabled(True)
            return
        self._current = updated
        await self._reload()
        self._set_status(f"Saved (v{updated.get('version', '?')})")
        self.save_btn.setEnabled(True)

    def _delete_selected(self) -> None:
        if not self._current:
            return
        asyncio.ensure_future(self._delete_async(self._current["id"]))

    async def _delete_async(self, blueprint_id: str) -> None:
        try:
            out = await self.client.call("blueprints.delete", {"id": blueprint_id})
        except Exception as e:
            self._set_status(f"Delete failed: {e}", error=True)
            return
        if not out.get("deleted"):
            n = out.get("linked_actions", 0)
            QtWidgets.QMessageBox.information(
                self,
                "Cannot delete blueprint",
                f"This blueprint has {n} drone action(s) still linked to it.  "
                "Delete those drones first (Drones tab), then retry.",
            )
            return
        self._current = None
        await self._reload()
        self._set_status("Deleted")

    def _new_dialog(self) -> None:
        dlg = _NewBlueprintDialog(self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        params = dlg.params()
        asyncio.ensure_future(self._create_async(params))

    async def _create_async(self, params: dict[str, Any]) -> None:
        try:
            bp = await self.client.call("blueprints.create", params)
        except Exception as e:
            self._set_status(f"Create failed: {e}", error=True)
            return
        self._current = bp
        await self._reload()
        self._set_status(f"Created '{bp.get('name')}'")

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setStyleSheet(
            "color:#b3261e;font-size:11px;" if error else "color:#5b6068;font-size:11px;"
        )
        self.status_label.setText(text)

    def showEvent(self, event):  # type: ignore[override]
        # Re-pull blueprints whenever the tab becomes visible — keeps
        # the list fresh without a manual refresh button.
        super().showEvent(event)
        asyncio.ensure_future(self._reload())


class _NewBlueprintDialog(QtWidgets.QDialog):
    """Minimal create dialog — name + provider + model + role.

    The full set of fields lives on the editor form once the row is
    in the sidebar.  Keeping creation low-friction so the operator
    iterates on configs by editing rather than re-typing.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New blueprint")
        self.setModal(True)
        self.resize(440, 240)

        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self._name = QtWidgets.QLineEdit()
        self._name.setPlaceholderText("e.g. Code reviewer")
        form.addRow("Name", self._name)

        self._role = QtWidgets.QComboBox()
        for value, label in _ROLE_LABELS:
            self._role.addItem(label, value)
        form.addRow("Role", self._role)

        self._provider = QtWidgets.QComboBox()
        for p in _PROVIDERS:
            self._provider.addItem(p, p)
        self._provider.currentTextChanged.connect(self._refresh_models)  # type: ignore[arg-type]
        form.addRow("Provider", self._provider)

        self._model = QtWidgets.QComboBox()
        self._model.setEditable(True)
        self._refresh_models(_PROVIDERS[0])
        form.addRow("Model", self._model)
        v.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(self.reject)  # type: ignore[arg-type]
        v.addWidget(buttons)

    def _refresh_models(self, provider: str) -> None:
        current = self._model.currentText()
        new_hints = _MODEL_HINTS.get(provider, ())
        self._model.blockSignals(True)
        self._model.clear()
        for m in new_hints:
            self._model.addItem(m, m)
        if _belongs_to_other_provider(current, provider) or not current:
            if new_hints:
                self._model.setEditText(new_hints[0])
        else:
            self._model.setEditText(current)
        self._model.blockSignals(False)

    def params(self) -> dict[str, Any]:
        return {
            "name": self._name.text().strip() or "Untitled blueprint",
            "role": self._role.currentData(),
            "provider": self._provider.currentData(),
            "model": self._model.currentText().strip() or "claude-sonnet-4-6",
        }
