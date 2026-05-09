"""History page — full-text search + Recent runs with replay."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class HistoryPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._runs: list[dict] = []
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("History")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        layout.addWidget(title)

        # Tabs: Search and Recent runs.
        tabs = QtWidgets.QTabWidget()
        tabs.setStyleSheet("QTabBar::tab{padding:6px 14px;}QTabBar::tab:selected{font-weight:600;}")
        layout.addWidget(tabs, stretch=1)
        tabs.addTab(self._build_search_tab(), "Search")
        tabs.addTab(self._build_runs_tab(), "Recent runs")

    def _build_search_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 16, 0, 0)
        v.setSpacing(10)

        searchbar = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Search instructions, artifacts, transcripts…")
        self.input.returnPressed.connect(self._run)  # type: ignore[arg-type]
        searchbar.addWidget(self.input, stretch=1)
        self.btn = QtWidgets.QPushButton("Search")
        self.btn.clicked.connect(self._run)  # type: ignore[arg-type]
        searchbar.addWidget(self.btn)
        v.addLayout(searchbar)

        self.results = QtWidgets.QListWidget()
        self.results.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}"
        )
        v.addWidget(self.results, stretch=1)
        return page

    def _build_runs_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 16, 0, 0)
        v.setSpacing(10)

        self.runs_table = QtWidgets.QTableWidget()
        self.runs_table.setColumnCount(5)
        self.runs_table.setHorizontalHeaderLabels(["Run ID", "Card", "State", "Cost", "Created"])
        self.runs_table.horizontalHeader().setStretchLastSection(True)
        self.runs_table.verticalHeader().setVisible(False)
        self.runs_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.runs_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        v.addWidget(self.runs_table, stretch=1)

        bar = QtWidgets.QHBoxLayout()
        bar.addStretch(1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(  # type: ignore[arg-type]
            lambda: asyncio.ensure_future(self._reload_runs())
        )
        bar.addWidget(refresh)
        self.replay_btn = QtWidgets.QPushButton("Replay…")
        self.replay_btn.clicked.connect(self._replay_selected)  # type: ignore[arg-type]
        bar.addWidget(self.replay_btn)
        v.addLayout(bar)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload_runs()))
        return page

    # -- Search ---------------------------------------------------------

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

    # -- Recent runs ----------------------------------------------------

    async def _reload_runs(self) -> None:
        try:
            self._runs = await self.client.call("runs.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "RPC error", str(exc))
            return
        self.runs_table.setRowCount(len(self._runs))
        for r, row in enumerate(self._runs):
            cells = (
                row.get("id", ""),
                row.get("card_id", ""),
                row.get("state", ""),
                f"${float(row.get('cost_usd', 0) or 0):.4f}",
                row.get("created_at", "")[:19],
            )
            for c, val in enumerate(cells):
                self.runs_table.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))

    def _replay_selected(self) -> None:
        idx = self.runs_table.currentRow()
        if idx < 0 or idx >= len(self._runs):
            QtWidgets.QMessageBox.information(self, "Replay", "Pick a run first.")
            return
        original = self._runs[idx]
        # Tiny dialog: provider + model overrides (blank = same).
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Replay run {original['id']}")
        form = QtWidgets.QFormLayout(dlg)
        provider_input = QtWidgets.QComboBox()
        provider_input.addItems(["", "anthropic", "google", "openai", "ollama"])
        model_input = QtWidgets.QLineEdit()
        model_input.setPlaceholderText("(blank to keep original model)")
        form.addRow("Provider override:", provider_input)
        form.addRow("Model override:", model_input)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(dlg.accept)  # type: ignore[arg-type]
        buttons.rejected.connect(dlg.reject)  # type: ignore[arg-type]
        form.addRow(buttons)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        provider = provider_input.currentText() or None
        model = model_input.text().strip() or None
        asyncio.ensure_future(self._do_replay(original["id"], provider, model))

    async def _do_replay(self, run_id: str, provider: str | None, model: str | None) -> None:
        try:
            res = await self.client.call(
                "runs.replay",
                {"run_id": run_id, "provider": provider, "model": model},
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Replay failed", str(exc))
            return
        QtWidgets.QMessageBox.information(self, "Replaying", f"Started new run {res['run_id']}.")
        await self._reload_runs()
