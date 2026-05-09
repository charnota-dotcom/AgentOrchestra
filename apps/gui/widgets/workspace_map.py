"""Workspace map — a lightweight visual of agent branches.

For V1 we render a simple list of "lanes": each non-cleaned branch in
the active workspace, with state, last commit time, and a colored
indicator.  A QGraphicsView-based DAG view is a V3 concern; this
widget is good enough to surface that branches exist and that they're
isolated.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


_STATE_COLORS = {
    "created": "#aab1bb",
    "active": "#1f6feb",
    "paused": "#a96b00",
    "awaiting_review": "#1f7a3f",
    "merging": "#a87c1d",
    "conflicted": "#b3261e",
    "merged": "#5b6068",
    "rejected": "#5b6068",
    "abandoned": "#5b6068",
    "stale": "#a96b00",
    "cleaned": "#aab1bb",
}


class WorkspaceMap(QtWidgets.QFrame):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 16)
        v.setSpacing(8)

        title = QtWidgets.QLabel("Workspaces map")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        v.addWidget(title)

        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(4)
        v.addLayout(self.body)
        v.addStretch(1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(  # type: ignore[arg-type]
            lambda: asyncio.ensure_future(self.reload())
        )
        v.addWidget(self.refresh_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self.reload()))

    async def reload(self) -> None:
        # Clear current rows.
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as exc:
            self.body.addWidget(_label(f"(no workspaces: {exc})", italic=True))
            return
        if not workspaces:
            self.body.addWidget(_label("(no workspaces registered)", italic=True))
            return

        try:
            runs = await self.client.call("runs.list", {})
        except Exception:
            runs = []

        for ws in workspaces:
            self.body.addWidget(_workspace_row(ws))
            ws_runs = [r for r in runs if r.get("workspace_id") == ws["id"]]
            if not ws_runs:
                self.body.addWidget(_label("    (no runs yet)", italic=True))
                continue
            for run in ws_runs[:8]:
                self.body.addWidget(_run_lane(run))


def _workspace_row(ws: dict) -> QtWidgets.QWidget:
    row = QtWidgets.QWidget()
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(0, 6, 0, 2)
    icon = QtWidgets.QLabel("◆")
    icon.setStyleSheet("color:#1f6feb;font-size:14px;")
    h.addWidget(icon)
    name = QtWidgets.QLabel(f"<b>{ws.get('name', '?')}</b>")
    name.setStyleSheet("color:#0f1115;")
    h.addWidget(name)
    path = QtWidgets.QLabel(ws.get("repo_path", ""))
    path.setStyleSheet("color:#5b6068;font-size:11px;")
    h.addWidget(path)
    h.addStretch(1)
    return row


def _run_lane(run: dict) -> QtWidgets.QWidget:
    state = run.get("state", "")
    color = _STATE_COLORS.get(state, "#5b6068")
    row = QtWidgets.QWidget()
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(20, 0, 0, 0)

    pip = QtWidgets.QLabel("●")
    pip.setStyleSheet(f"color:{color};font-size:14px;")
    h.addWidget(pip)

    name = QtWidgets.QLabel(run.get("id", ""))
    name.setStyleSheet(
        "color:#0f1115;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;"
    )
    h.addWidget(name)

    state_lbl = QtWidgets.QLabel(state)
    state_lbl.setStyleSheet(f"color:{color};font-size:12px;")
    h.addWidget(state_lbl)

    cost = float(run.get("cost_usd", 0) or 0)
    cost_lbl = QtWidgets.QLabel(f"${cost:.4f}")
    cost_lbl.setStyleSheet("color:#5b6068;font-size:12px;")
    h.addStretch(1)
    h.addWidget(cost_lbl)
    return row


def _label(text: str, *, italic: bool = False) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    style = "color:#5b6068;font-size:12px;"
    if italic:
        style += "font-style:italic;"
    lbl.setStyleSheet(style)
    return lbl
