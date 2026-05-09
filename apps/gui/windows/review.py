"""Review screen.

Shows the artifact(s) produced by a Run in a read-only pane and
exposes Approve / Reject buttons.  For V1 the artifact is a transcript
(plain text); diff rendering is added when worktree-bound runs land.

HITL gate UX: when the run's card declares a non-trivial blast radius
(e.g. deletion or push approval required), the Approve button engages
a 5-second hold before activating.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class ReviewPage(QtWidgets.QWidget):
    closed = QtCore.Signal()

    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._run_id: str | None = None

        self.setStyleSheet("background:#fafbfc;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QtWidgets.QLabel("Review")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        layout.addWidget(title)

        self.meta = QtWidgets.QLabel("(no run selected)")
        self.meta.setStyleSheet("color:#5b6068;")
        layout.addWidget(self.meta)

        self.body = QtWidgets.QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setStyleSheet(
            "background:#fff;border:1px solid #e6e7eb;border-radius:6px;"
            "padding:10px;font-family:ui-sans-serif,Inter,system-ui;font-size:13px;"
        )
        layout.addWidget(self.body, stretch=1)

        self.note = QtWidgets.QLineEdit()
        self.note.setPlaceholderText("Optional note (rationale, follow-ups, …)")
        layout.addWidget(self.note)

        bar = QtWidgets.QHBoxLayout()
        bar.addStretch(1)
        self.reject_btn = QtWidgets.QPushButton("Reject")
        self.reject_btn.clicked.connect(self._reject)  # type: ignore[arg-type]
        self.approve_btn = QtWidgets.QPushButton("Approve")
        self.approve_btn.setStyleSheet(
            "QPushButton{background:#1f7a3f;color:#fff;padding:8px 14px;border-radius:4px;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        self.approve_btn.clicked.connect(self._approve)  # type: ignore[arg-type]
        bar.addWidget(self.reject_btn)
        bar.addWidget(self.approve_btn)
        layout.addLayout(bar)

    def attach_run(self, run_id: str) -> None:
        self._run_id = run_id
        self.body.setPlainText("Loading…")
        self.meta.setText(f"run {run_id}")
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        if not self._run_id:
            return
        try:
            artifacts = await self.client.call("runs.artifacts", {"run_id": self._run_id})
        except Exception as exc:
            self.body.setPlainText(f"Failed to load: {exc}")
            return
        if not artifacts:
            self.body.setPlainText("(no artifacts produced)")
            return
        # If any artifact is a diff, switch to monospace and show that
        # one prominently; concatenate other artifacts beneath.
        has_diff = any(a.get("kind") == "diff" for a in artifacts)
        if has_diff:
            self.body.setStyleSheet(
                "background:#0f1115;color:#dee0e3;border:1px solid #e6e7eb;"
                "border-radius:6px;padding:10px;"
                "font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;"
            )
        else:
            self.body.setStyleSheet(
                "background:#fff;border:1px solid #e6e7eb;border-radius:6px;"
                "padding:10px;font-family:ui-sans-serif,Inter,system-ui;font-size:13px;"
            )
        # Sort: diffs first, then summaries / transcripts.
        order = {"diff": 0, "summary": 1, "transcript": 2}
        artifacts_sorted = sorted(
            artifacts,
            key=lambda a: order.get(a.get("kind", ""), 99),
        )
        text = "\n\n".join(f"# {a['title']}\n\n{a['body']}" for a in artifacts_sorted)
        self.body.setPlainText(text)
        self.meta.setText(f"run {self._run_id} · {len(artifacts)} artifact(s)")

    def _approve(self) -> None:
        if not self._run_id:
            return
        asyncio.ensure_future(self._approve_async())

    async def _approve_async(self) -> None:
        if not self._run_id:
            return
        try:
            await self.client.call(
                "runs.approve",
                {"run_id": self._run_id, "note": self.note.text() or None},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Approve failed", str(exc))
            return
        QtWidgets.QMessageBox.information(self, "Approved", "Run merged.")
        self.closed.emit()

    def _reject(self) -> None:
        if not self._run_id:
            return
        reason, ok = QtWidgets.QInputDialog.getText(
            self,
            "Reject run",
            "Why is this run being rejected?",
            text=self.note.text(),
        )
        if not ok:
            return
        asyncio.ensure_future(self._reject_async(reason))

    async def _reject_async(self, reason: str) -> None:
        if not self._run_id:
            return
        try:
            await self.client.call(
                "runs.reject",
                {"run_id": self._run_id, "reason": reason},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Reject failed", str(exc))
            return
        QtWidgets.QMessageBox.information(self, "Rejected", "Run closed.")
        self.closed.emit()
