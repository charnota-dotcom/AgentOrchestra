"""First-run wizard.

A four-page QWizard that walks the operator through the first launch:

1. Welcome — what the app does, what stays local, what goes to providers.
2. Provider keys — Anthropic / Google / OpenAI; saved to OS keyring.
3. Workspace — pick a git repo (or skip).
4. Defaults — sandbox tier, daily budget, claude hook bridge install.

The wizard is shown by ``apps.gui.windows.main_window`` on first run
(detected via the absence of ``~/.local/share/agentorchestra/first_run.done``).
Skipping any page is allowed; users can return to Settings at any time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

from apps.service.secrets.keyring_store import (
    anthropic_key,
    google_key,
    openai_key,
    set_secret,
)

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


SENTINEL_PATH = Path.home() / ".local" / "share" / "agentorchestra" / "first_run.done"


def first_run_pending() -> bool:
    return not SENTINEL_PATH.exists()


def mark_first_run_done() -> None:
    SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENTINEL_PATH.write_text("done\n")


class FirstRunWizard(QtWidgets.QWizard):
    def __init__(self, client: RpcClient, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Welcome to AgentOrchestra")
        self.setOption(QtWidgets.QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.resize(620, 480)

        self.addPage(self._welcome_page())
        self.addPage(self._keys_page())
        self.addPage(self._workspace_page())
        self.addPage(self._defaults_page())

    @staticmethod
    def _label(text: str, *, big: bool = False) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            "font-size:18px;font-weight:600;color:#0f1115;" if big else "color:#0f1115;"
        )
        return lbl

    # --- Page 1 -------------------------------------------------------

    def _welcome_page(self) -> QtWidgets.QWizardPage:
        page = QtWidgets.QWizardPage()
        page.setTitle("Welcome")
        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(
            self._label(
                "AgentOrchestra runs AI agents from Anthropic, Google, and "
                "your local machine in isolated branches of your repos.",
                big=True,
            )
        )
        layout.addSpacing(8)
        layout.addWidget(
            self._label(
                "If you already use Claude Code on a Pro or Max subscription, "
                "the bundled cards work out of the box — they pipe through "
                "the local `claude` CLI and reuse your existing auth.  No "
                "API key needed for the simple chat cards.\n\n"
                "What stays on your machine: every instruction you write, "
                "every transcript, every diff, all credentials.\n\n"
                "What leaves: only the prompts and tool-call outputs the "
                "agent itself produces, sent to the provider you pick per "
                "card.  Use Settings → Provider keys to manage credentials "
                "at any time."
            )
        )
        return page

    # --- Page 2 -------------------------------------------------------

    def _keys_page(self) -> QtWidgets.QWizardPage:
        page = QtWidgets.QWizardPage()
        page.setTitle("Provider keys")
        page.setSubTitle(
            "If you already use Claude Code on a Pro / Max subscription, "
            "you can skip every key here — the bundled cards default to "
            "the local `claude` CLI and use that subscription's auth.  "
            "Add API keys only if you want to call providers directly."
        )
        form = QtWidgets.QFormLayout(page)

        self._anth = QtWidgets.QLineEdit()
        self._anth.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._anth.setPlaceholderText("(set)" if anthropic_key() else "sk-ant-…")
        form.addRow("Anthropic API key:", self._anth)

        self._google = QtWidgets.QLineEdit()
        self._google.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._google.setPlaceholderText("(set)" if google_key() else "AIza…")
        form.addRow("Google API key:", self._google)

        self._openai = QtWidgets.QLineEdit()
        self._openai.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._openai.setPlaceholderText("(set)" if openai_key() else "sk-…")
        form.addRow("OpenAI API key:", self._openai)

        return page

    # --- Page 3 -------------------------------------------------------

    def _workspace_page(self) -> QtWidgets.QWizardPage:
        page = QtWidgets.QWizardPage()
        page.setTitle("Default workspace")
        page.setSubTitle("Pick a git repo to register, or skip and add one later.")
        layout = QtWidgets.QVBoxLayout(page)

        row = QtWidgets.QHBoxLayout()
        self._repo_path = QtWidgets.QLineEdit()
        row.addWidget(self._repo_path, stretch=1)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse_repo)  # type: ignore[arg-type]
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _browse_repo(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Pick a repo")
        if path:
            self._repo_path.setText(path)

    # --- Page 4 -------------------------------------------------------

    def _defaults_page(self) -> QtWidgets.QWizardPage:
        page = QtWidgets.QWizardPage()
        page.setTitle("Defaults")
        layout = QtWidgets.QVBoxLayout(page)

        self._sandbox = QtWidgets.QComboBox()
        self._sandbox.addItem("Devcontainer-style (fast, V1 default)", "devcontainer")
        self._sandbox.addItem("Docker (safer, requires Docker)", "docker")
        layout.addWidget(QtWidgets.QLabel("Default sandbox tier:"))
        layout.addWidget(self._sandbox)
        layout.addSpacing(8)

        self._budget = QtWidgets.QSpinBox()
        self._budget.setMinimum(1)
        self._budget.setMaximum(500)
        self._budget.setValue(20)
        self._budget.setPrefix("$")
        layout.addWidget(QtWidgets.QLabel("Daily spend cap (informational):"))
        layout.addWidget(self._budget)
        layout.addSpacing(8)

        self._install_hooks = QtWidgets.QCheckBox(
            "Install Claude Code hook bridge so external sessions appear in History."
        )
        self._install_hooks.setChecked(True)
        layout.addWidget(self._install_hooks)
        layout.addStretch(1)
        return page

    # --- Finish -------------------------------------------------------

    def accept(self) -> None:
        # Save keys we typed (skip blanks).
        for slot, widget in (
            ("anthropic_api_key", self._anth),
            ("google_api_key", self._google),
            ("openai_api_key", self._openai),
        ):
            text = widget.text().strip()
            if text:
                set_secret(slot, text)

        repo = self._repo_path.text().strip()
        if repo:
            asyncio.ensure_future(self._register_workspace(repo))
        if self._install_hooks.isChecked():
            asyncio.ensure_future(self._install_hook_bridge())

        mark_first_run_done()
        super().accept()

    async def _register_workspace(self, repo: str) -> None:
        try:
            await self.client.call("workspaces.register", {"path": repo})
        except Exception as exc:
            QtCore.qWarning(f"first-run workspace register failed: {exc}")

    async def _install_hook_bridge(self) -> None:
        try:
            await self.client.call(
                "hooks.install",
                {"service_url": self.client.base_url},
            )
        except Exception as exc:
            QtCore.qWarning(f"first-run hook install failed: {exc}")
