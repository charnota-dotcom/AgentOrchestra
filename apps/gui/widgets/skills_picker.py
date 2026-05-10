"""SkillsPicker — multi-select dialog for picking installed skills.

Per-provider: Claude Code skills live as ``*.md`` under
``~/.claude/skills/`` and are surfaced by the ``skills.list`` RPC.
Gemini doesn't have a first-class skills mechanism today, so the
dialog renders a hint pointing at the free-form Skills field instead.

Used by the Chat tab and the Canvas "+ New conversation" dialog so
operators can pick skills from a list rather than memorising the
``/foo /bar`` syntax.  Output is the same ``/skill1 /skill2`` token
string the existing free-form field already accepts — so the
downstream system-prompt assembler (`apps.gui.presets.compose_system`)
is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class SkillsPicker(QtWidgets.QDialog):
    """Modal dialog: shows installed skills for a provider, lets the
    operator tick the ones they want, returns ``/name1 /name2``.

    Pre-checks skill names that are already in the operator's current
    skills field (so re-opening the picker shows them as selected).
    """

    def __init__(
        self,
        client: RpcClient,
        provider: str,
        current: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.provider = provider
        self.setWindowTitle(f"Skills · {provider}")
        self.resize(540, 480)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(10)

        header = QtWidgets.QLabel("Pick skills")
        header.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        v.addWidget(header)

        self.hint = QtWidgets.QLabel("Loading skills…")
        self.hint.setStyleSheet("color:#5b6068;font-size:11px;")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:4px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #eef0f3;}"
        )
        v.addWidget(self.list_widget, stretch=1)

        # Pre-existing tokens parsed from the operator's current Skills
        # field, so the dialog can pre-tick them.
        self._current_tokens: set[str] = {
            t.lstrip("/").strip().lower()
            for t in (current or "").split()
            if t.startswith("/") and len(t) > 1
        }

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(self.reject)  # type: ignore[arg-type]
        v.addWidget(buttons)

        # Kick off the load on the next event-loop tick so the dialog
        # appears immediately (otherwise the operator sees a brief
        # frozen-empty state on a slow disk).
        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._load()))

    async def _load(self) -> None:
        try:
            res = await self.client.call("skills.list", {"provider": self.provider})
        except Exception as exc:
            self.hint.setText(f"Couldn't load skills: {exc}")
            return
        skills = res.get("skills", []) or []
        source = res.get("source", "none")
        if not skills:
            if source == "none":
                self.hint.setText(
                    f"{self.provider} has no first-class skills mechanism — type "
                    "directives in the Skills field directly (e.g. "
                    "`/research-deep /cite-sources`)."
                )
            else:
                self.hint.setText(
                    f"No skills found in {source}.  Drop "
                    "`<name>.md` files there and re-open this dialog."
                )
            return
        self.hint.setText(
            f"Found {len(skills)} skill(s) in {source}.  "
            "Tick the ones you want active for this conversation."
        )
        for sk in skills:
            name = str(sk.get("name", ""))
            description = str(sk.get("description", "") or "")
            label = f"{name}\n{description}" if description else name
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if name.lower() in self._current_tokens
                else QtCore.Qt.CheckState.Unchecked
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, name)
            self.list_widget.addItem(item)

    def selected_tokens(self) -> str:
        """Return the picked skills as ``/name1 /name2``, ready to drop
        into the existing free-form Skills field.  Preserves any
        non-skills text the operator had typed manually (anything not
        starting with ``/``)."""
        picked: list[str] = []
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it is None:
                continue
            if it.checkState() == QtCore.Qt.CheckState.Checked:
                name = str(it.data(QtCore.Qt.ItemDataRole.UserRole) or "")
                if name:
                    picked.append(f"/{name}")
        return " ".join(picked)
