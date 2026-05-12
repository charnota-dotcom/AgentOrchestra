"""Live agent pane.

Streams events for a single Run via SSE.  Shows:
- Header: run id, card name, current state, accumulated cost.
- Transcript: assistant text deltas accumulated as they arrive.
- Event log: timeline of every event the run emits, newest at top.
- Buttons: Cancel (while running), Open Review (once REVIEWING).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.ipc.sse_client import SseClient

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class LivePage(QtWidgets.QWidget):
    review_requested = QtCore.Signal(str)

    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._sse = SseClient(base_url=client.base_url, token=client.token)
        self._task: asyncio.Task[Any] | None = None
        self._run_id: str | None = None

        self.setStyleSheet("background:#fafbfc;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("Live")
        self.title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        header.addWidget(self.title)
        header.addStretch(1)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)  # type: ignore[arg-type]
        self.review_btn = QtWidgets.QPushButton("Open review")
        self.review_btn.setEnabled(False)
        self.review_btn.clicked.connect(self._open_review)  # type: ignore[arg-type]
        header.addWidget(self.cancel_btn)
        header.addWidget(self.review_btn)
        layout.addLayout(header)

        self.meta = QtWidgets.QLabel("(no run selected)")
        self.meta.setStyleSheet("color:#5b6068;")
        layout.addWidget(self.meta)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle{background:#e6e7eb;}")
        layout.addWidget(splitter, stretch=1)

        # Left: transcript
        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "background:#fff;border:1px solid #e6e7eb;border-radius:6px;"
            "padding:10px;font-family:ui-sans-serif,Inter,system-ui;font-size:13px;"
        )
        splitter.addWidget(self._wrap("Transcript", self.transcript))

        # Right: event log
        self.event_log = QtWidgets.QListWidget()
        self.event_log.setStyleSheet("background:#fff;border:1px solid #e6e7eb;border-radius:6px;")
        splitter.addWidget(self._wrap("Events", self.event_log))
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    @staticmethod
    def _wrap(title: str, body: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QFrame()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        h = QtWidgets.QLabel(title)
        h.setStyleSheet("font-size:12px;font-weight:600;color:#0f1115;")
        v.addWidget(h)
        v.addWidget(body, stretch=1)
        return w

    # ------------------------------------------------------------------

    def attach_run(self, run_id: str, *, card_name: str = "") -> None:
        """Start streaming a run.  Cancels any prior subscription."""
        self._detach_task()
        self._run_id = run_id
        self.title.setText(f"Live — {card_name or run_id}")
        self.meta.setText(f"run {run_id} · waiting for first event…")
        self.transcript.clear()
        self.event_log.clear()
        self.cancel_btn.setEnabled(True)
        self.review_btn.setEnabled(False)
        # Sync the canonical run state up-front so a short-lived run
        # that finished before the SSE stream attaches doesn't leave the
        # UI hung on "waiting for first event…" with stale buttons.
        self._task = asyncio.ensure_future(self._attach_and_consume(run_id))

    async def _attach_and_consume(self, run_id: str) -> None:
        try:
            await self._sync_initial_state(run_id)
            await self._consume(run_id)
        except asyncio.CancelledError:
            return

    async def _sync_initial_state(self, run_id: str) -> None:
        try:
            runs = await self.client.call("runs.list", {})
        except Exception:
            return
        if self._run_id != run_id:
            return
        match = next((r for r in runs if r.get("id") == run_id), None)
        if not match:
            return
        state = match.get("state") or ""
        cost = float(match.get("cost_usd") or 0.0)
        self.meta.setText(f"run {run_id} · {state} · ${cost:.4f}")
        if state in ("reviewing", "merged", "rejected", "aborted"):
            self.cancel_btn.setEnabled(False)
        if state == "reviewing":
            self.review_btn.setEnabled(True)

    def _detach_task(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _consume(self, run_id: str) -> None:
        cost = 0.0
        try:
            async for ev in self._sse.stream_run(run_id):
                kind = ev.get("kind", "")
                text = ev.get("text", "") or ""
                payload = ev.get("payload") or {}

                self.event_log.insertItem(0, f"[{kind}] {text[:120] if text else payload}")
                if self.event_log.count() > 200:
                    self.event_log.takeItem(self.event_log.count() - 1)

                if kind == "llm.call_completed":
                    delta = payload.get("delta")
                    if delta:
                        # Only auto-scroll if the operator was already
                        # parked at the bottom; otherwise yanking the
                        # viewport while they're reading earlier output
                        # is infuriating.
                        bar = self.transcript.verticalScrollBar()
                        at_bottom = bar.value() >= bar.maximum() - 4
                        cursor = self.transcript.textCursor()
                        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                        cursor.insertText(delta)
                        if at_bottom:
                            self.transcript.setTextCursor(cursor)
                            bar.setValue(bar.maximum())

                if kind == "run.state_changed":
                    state = payload.get("to") or payload.get("state")
                    if isinstance(payload.get("cost_usd"), (int, float)):
                        cost = float(payload["cost_usd"])
                    self.meta.setText(f"run {run_id} · {state} · ${cost:.4f}")
                    if state in ("reviewing", "merged", "rejected", "aborted"):
                        self.cancel_btn.setEnabled(False)
                    if state == "reviewing":
                        self.review_btn.setEnabled(True)

                if kind == "run.completed":
                    self.cancel_btn.setEnabled(False)
                    break
        except asyncio.CancelledError:
            # Normal path when attach_run() switches to a different run.
            return

    def _cancel(self) -> None:
        if not self._run_id:
            return
        self.cancel_btn.setEnabled(False)
        asyncio.ensure_future(self._cancel_async(self._run_id))

    async def _cancel_async(self, run_id: str) -> None:
        try:
            await self.client.call("runs.cancel", {"run_id": run_id})
        except Exception as exc:
            # If the cancel RPC fails the user is stranded with a
            # disabled button and no feedback; re-enable so they can
            # retry and surface the error.
            if self._run_id == run_id:
                self.cancel_btn.setEnabled(True)
            QtWidgets.QMessageBox.warning(
                self,
                "Cancel failed",
                f"Could not cancel run {run_id}: {exc}",
            )

    def _open_review(self) -> None:
        if self._run_id:
            self.review_requested.emit(self._run_id)
