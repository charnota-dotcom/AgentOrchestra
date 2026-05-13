"""Analytics page - run/event KPI dashboard + leaderboard."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


class AnalyticsPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Analytics")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        header.addWidget(title, stretch=1)

        self.days = QtWidgets.QComboBox()
        self.days.addItem("7 days", 7)
        self.days.addItem("14 days", 14)
        self.days.addItem("30 days", 30)
        header.addWidget(self.days)

        self.group_by = QtWidgets.QComboBox()
        self.group_by.addItem("By card", "card")
        self.group_by.addItem("By provider", "provider")
        self.group_by.addItem("By archetype", "archetype")
        header.addWidget(self.group_by)

        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)  # type: ignore[arg-type]
        header.addWidget(refresh)
        layout.addLayout(header)

        self.kpis = QtWidgets.QLabel("Loading...")
        self.kpis.setStyleSheet(
            "background:#fff;border:1px solid #e6e7eb;border-radius:6px;"
            "padding:10px;font-size:12px;color:#0f1115;"
        )
        self.kpis.setWordWrap(True)
        layout.addWidget(self.kpis)

        self.trend = QtWidgets.QTableWidget()
        self.trend.setColumnCount(6)
        self.trend.setHorizontalHeaderLabels(
            ["Date", "Runs", "Token Eff.", "Hallucination", "Re-plan", "Cost"]
        )
        self.trend.horizontalHeader().setStretchLastSection(True)
        self.trend.verticalHeader().setVisible(False)
        self.trend.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.trend, stretch=1)

        self.leaderboard = QtWidgets.QTableWidget()
        self.leaderboard.setColumnCount(6)
        self.leaderboard.setHorizontalHeaderLabels(
            ["Entity", "Samples", "Success", "Cost/Success", "Token Eff.", "Total Cost"]
        )
        self.leaderboard.horizontalHeader().setStretchLastSection(True)
        self.leaderboard.verticalHeader().setVisible(False)
        self.leaderboard.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.leaderboard, stretch=1)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#5b6068;font-size:11px;")
        layout.addWidget(self.status)

        QtCore.QTimer.singleShot(0, self._refresh)

    def _refresh(self) -> None:
        asyncio.ensure_future(self._refresh_async())

    async def _refresh_async(self) -> None:
        days = int(self.days.currentData() or 7)
        group_by = str(self.group_by.currentData() or "card")
        try:
            summary = await self.client.call("analytics.summary", {"days": days})
            leaderboard = await self.client.call(
                "analytics.leaderboard", {"days": days, "group_by": group_by, "min_samples": 1}
            )
        except Exception as exc:
            self.status.setText(f"Analytics load failed: {exc}")
            return
        self._render_summary(summary)
        self._render_leaderboard(leaderboard)
        self.status.setText(
            f"Window: last {days} days • Updated: "
            f"{QtCore.QDateTime.currentDateTime().toString('HH:mm:ss')}"
        )

    def _render_summary(self, summary: dict[str, Any]) -> None:
        k = summary.get("kpis", {}) or {}
        cps = k.get("cost_per_success")
        cps_text = f"${float(cps):.4f}" if cps is not None else "N/A"
        self.kpis.setText(
            " | ".join(
                [
                    f"Runs: {int(k.get('run_count', 0))}",
                    f"Hallucination rate: {float(k.get('hallucination_rate', 0.0)):.2f}",
                    f"Token efficiency: {float(k.get('token_efficiency', 0.0)):.4f}",
                    f"Re-plan velocity: {float(k.get('replan_velocity', 0.0)):.2f}",
                    f"Cost/success: {cps_text}",
                ]
            )
        )
        rows = summary.get("trend", []) or []
        self.trend.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [
                row.get("date", ""),
                int(row.get("runs", 0)),
                f"{float(row.get('token_efficiency', 0.0)):.4f}",
                f"{float(row.get('tool_errors', 0.0)) / max(1, int(row.get('runs', 0))):.2f}",
                f"{float(row.get('avg_replan_velocity', 0.0)):.2f}",
                f"${float(row.get('cost_usd', 0.0)):.4f}",
            ]
            for c, val in enumerate(vals):
                self.trend.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))

    def _render_leaderboard(self, leaderboard: dict[str, Any]) -> None:
        rows = leaderboard.get("rows", []) or []
        self.leaderboard.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cps = row.get("cost_per_success")
            cps_text = f"${float(cps):.4f}" if cps is not None else "N/A"
            vals = [
                row.get("entity", ""),
                int(row.get("sample_size", 0)),
                int(row.get("success_count", 0)),
                cps_text,
                f"{float(row.get('token_efficiency', 0.0)):.4f}",
                f"${float(row.get('total_cost_usd', 0.0)):.4f}",
            ]
            for c, val in enumerate(vals):
                self.leaderboard.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))
