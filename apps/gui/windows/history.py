"""History page — full-text search over instructions, artifacts, and events."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class HistoryPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("History")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        layout.addWidget(title)

        searchbar = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Search instructions, artifacts, transcripts…")
        self.input.returnPressed.connect(self._run)  # type: ignore[arg-type]
        searchbar.addWidget(self.input, stretch=1)
        self.btn = QtWidgets.QPushButton("Search")
        self.btn.clicked.connect(self._run)  # type: ignore[arg-type]
        searchbar.addWidget(self.btn)
        layout.addLayout(searchbar)

        self.results = QtWidgets.QListWidget()
        self.results.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}"
        )
        layout.addWidget(self.results, stretch=1)

    def _run(self) -> None:
        asyncio.ensure_future(self._search_async())

    async def _search_async(self) -> None:
        q = self.input.text().strip()
        if not q:
            return
        try:
            rows = await self.client.call("search", {"query": q})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "RPC error", str(exc))
            return
        self.results.clear()
        for r in rows:
            item = QtWidgets.QListWidgetItem(f"[{r['doc_kind']}] {r['title']}  —  {r['snippet']}")
            self.results.addItem(item)
