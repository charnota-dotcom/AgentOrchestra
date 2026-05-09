"""Settings page — provider keys, workspaces, ingestion toggles."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtWidgets

from apps.service.secrets.keyring_store import (
    anthropic_key,
    google_key,
    openai_key,
    set_secret,
)

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class SettingsPage(QtWidgets.QWidget):
    def __init__(self, client: "RpcClient") -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        title = QtWidgets.QLabel("Settings")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        layout.addWidget(title)

        layout.addWidget(self._provider_keys())
        layout.addWidget(self._workspaces_box(), stretch=1)

    def _provider_keys(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet(
            "QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}"
        )
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(16, 12, 16, 16)
        v.setSpacing(10)

        heading = QtWidgets.QLabel("Provider keys")
        heading.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        v.addWidget(heading)

        sub = QtWidgets.QLabel("Stored in your OS keychain.  Never written to disk in plain text.")
        sub.setStyleSheet("color:#5b6068;font-size:12px;")
        v.addWidget(sub)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)

        for label, slot, getter in (
            ("Anthropic API key", "anthropic_api_key", anthropic_key),
            ("Google API key", "google_api_key", google_key),
            ("OpenAI API key", "openai_api_key", openai_key),
        ):
            row = QtWidgets.QHBoxLayout()
            line = QtWidgets.QLineEdit()
            line.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            existing = getter()
            if existing:
                line.setPlaceholderText("(stored — leave blank to keep)")
            save = QtWidgets.QPushButton("Save")
            save.clicked.connect(  # type: ignore[arg-type]
                lambda _checked=False, k=slot, w=line: self._save_key(k, w)
            )
            row.addWidget(line, stretch=1)
            row.addWidget(save)
            form.addRow(QtWidgets.QLabel(label), row)

        v.addLayout(form)
        return wrap

    def _save_key(self, slot: str, widget: QtWidgets.QLineEdit) -> None:
        text = widget.text().strip()
        if not text:
            return
        set_secret(slot, text)
        widget.clear()
        widget.setPlaceholderText("(stored — leave blank to keep)")
        QtWidgets.QToolTip.showText(widget.mapToGlobal(widget.rect().topRight()), "Saved")

    def _workspaces_box(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet(
            "QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}"
        )
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(16, 12, 16, 16)
        v.setSpacing(10)

        heading = QtWidgets.QLabel("Workspaces")
        heading.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        v.addWidget(heading)

        self.workspaces = QtWidgets.QListWidget()
        v.addWidget(self.workspaces, stretch=1)

        bar = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add workspace…")
        add_btn.clicked.connect(self._add_workspace)  # type: ignore[arg-type]
        bar.addWidget(add_btn)
        bar.addStretch(1)
        v.addLayout(bar)

        asyncio.ensure_future(self._reload())
        return wrap

    def _add_workspace(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Pick a repo")
        if not path:
            return
        asyncio.ensure_future(self._register(path))

    async def _register(self, path: str) -> None:
        try:
            await self.client.call("workspaces.register", {"path": path})
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Couldn't add workspace", str(exc))
            return
        await self._reload()

    async def _reload(self) -> None:
        try:
            ws = await self.client.call("workspaces.list", {})
        except Exception:  # noqa: BLE001
            return
        self.workspaces.clear()
        for w in ws:
            self.workspaces.addItem(f"{w['name']}  —  {w['repo_path']}")
