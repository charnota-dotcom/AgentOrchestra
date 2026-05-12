"""Skills tab — editor for reusable agent superpower templates.

A *skill* is a named instruction block (e.g. "/research-deep") that
can be selected in blueprints or drone deployment dialogs.  Unlike 
first-class skills discovered on disk, these templates are persisted
in the orchestrator's database and can be modified by the operator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.ipc.client import RpcClient

log = logging.getLogger(__name__)


class SkillsPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._skills: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. Sidebar (List of templates)
        self.sidebar = self._build_sidebar()
        layout.addWidget(self.sidebar)

        # 2. Main content (Editor form)
        self.editor = self._build_editor()
        layout.addWidget(self.editor, stretch=1)

        # Initial load.
        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload()))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setMinimumWidth(200)
        wrap.setMaximumWidth(280)
        wrap.setStyleSheet("background:#f6f8fa; border-right:1px solid #d0d3d9;")
        
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(12, 16, 12, 12)
        v.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Skills")
        title.setStyleSheet("font-size:14px; font-weight:600; color:#0f1115;")
        header.addWidget(title)
        
        add_btn = QtWidgets.QPushButton("+ New")
        add_btn.setStyleSheet(
            "QPushButton{padding:4px 8px; font-size:11px; border:1px solid #d0d3d9;"
            "border-radius:4px; background:#fff; color:#0f1115;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        add_btn.clicked.connect(self._on_new_clicked)
        header.addWidget(add_btn)
        v.addLayout(header)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget{border:none; background:transparent;}"
            "QListWidget::item{padding:8px 6px; border-radius:4px;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5; color:#0f1115;}"
        )
        self.list_widget.currentRowChanged.connect(self._on_select)
        v.addWidget(self.list_widget, stretch=1)

        return wrap

    # ------------------------------------------------------------------
    # Editor
    # ------------------------------------------------------------------

    def _build_editor(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(32, 24, 32, 32)
        v.setSpacing(20)

        # Header area
        header = QtWidgets.QVBoxLayout()
        header.setSpacing(4)
        self.title = QtWidgets.QLabel("Select a skill")
        self.title.setStyleSheet("font-size:18px; font-weight:600; color:#0f1115;")
        header.addWidget(self.title)
        self.subtitle = QtWidgets.QLabel("Templates for agent superpowers")
        self.subtitle.setStyleSheet("color:#5b6068; font-size:12px;")
        header.addWidget(self.subtitle)
        v.addLayout(header)

        # Form
        self.form_panel = QtWidgets.QFrame()
        self.form_panel.setEnabled(False)
        form = QtWidgets.QFormLayout(self.form_panel)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(16)

        self.name_in = QtWidgets.QLineEdit()
        self.name_in.setPlaceholderText("e.g. research-deep")
        self.name_in.textChanged.connect(lambda _: self._update_save_enabled())
        form.addRow("Name", self.name_in)

        self.desc_in = QtWidgets.QTextEdit()
        self.desc_in.setPlaceholderText("Instructions and capabilities for this skill...")
        self.desc_in.setAcceptRichText(False)
        self.desc_in.setStyleSheet("font-family:monospace; font-size:12px;")
        form.addRow("Instructions", self.desc_in)

        v.addWidget(self.form_panel, stretch=1)

        # Footer (Actions)
        btns = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("Save changes")
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet(
            "QPushButton{padding:8px 16px; font-weight:600; background:#1f2024; color:#fff;"
            "border-radius:6px; border:none;}"
            "QPushButton:hover{background:#3b3d44;}"
            "QPushButton:disabled{background:#d0d3d9; color:#8e929a;}"
        )
        self.save_btn.clicked.connect(self._save)
        btns.addWidget(self.save_btn)

        self.delete_btn = QtWidgets.QPushButton("Delete skill")
        self.delete_btn.setStyleSheet(
            "QPushButton{padding:8px 16px; background:transparent; color:#b3261e; border:none;}"
            "QPushButton:hover{background:#fde8e7; border-radius:6px;}"
        )
        self.delete_btn.clicked.connect(self._delete)
        self.delete_btn.setVisible(False)
        btns.addWidget(self.delete_btn)

        btns.addStretch(1)
        v.addLayout(btns)

        return wrap

    # ------------------------------------------------------------------
    # Logic
    # ------------------------------------------------------------------

    async def _reload(self) -> None:
        try:
            # skills_list returns {provider, skills, source}
            res = await self.client.call("skills.list", {"provider": "all"})
            self._skills = [s for s in res.get("skills", []) if "id" in s]
        except Exception as e:
            log.debug("skills reload skipped: backend unavailable", exc_info=True)
            self.list_widget.clear()
            self._show_placeholder()
            self.subtitle.setText(f"Reload failed: {e}")
            return

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for s in self._skills:
            item = QtWidgets.QListWidgetItem(s["name"])
            item.setData(QtCore.Qt.ItemDataRole.UserRole, s["id"])
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        if self._current:
            for i, s in enumerate(self._skills):
                if s["id"] == self._current["id"]:
                    self.list_widget.setCurrentRow(i)
                    break
        else:
            self._show_placeholder()

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._skills):
            self._show_placeholder()
            return
        
        skill = self._skills[row]
        self._current = skill
        self.title.setText(skill["name"])
        self.name_in.setText(skill["name"])
        self.desc_in.setPlainText(skill["description"])
        
        self.form_panel.setEnabled(True)
        self.save_btn.setText("Save changes")
        self.delete_btn.setVisible(True)
        self._update_save_enabled()

    def _on_new_clicked(self) -> None:
        self._current = None
        self.list_widget.clearSelection()
        self.title.setText("New Skill")
        self.name_in.clear()
        self.desc_in.clear()
        
        self.form_panel.setEnabled(True)
        self.save_btn.setText("Create skill")
        self.save_btn.setEnabled(False)
        self.delete_btn.setVisible(False)
        self.name_in.setFocus()

    def _show_placeholder(self) -> None:
        self._current = None
        self.title.setText("Select a skill")
        self.form_panel.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.delete_btn.setVisible(False)
        self.name_in.clear()
        self.desc_in.clear()

    def _update_save_enabled(self) -> None:
        has_name = bool(self.name_in.text().strip())
        self.save_btn.setEnabled(has_name)

    def _save(self) -> None:
        asyncio.ensure_future(self._save_async())

    async def _save_async(self) -> None:
        name = self.name_in.text().strip()
        desc = self.desc_in.toPlainText().strip()
        
        try:
            if self._current:
                # Update
                res = await self.client.call(
                    "skills.update",
                    {"id": self._current["id"], "name": name, "description": desc},
                )
                self._current = res
            else:
                # Create
                res = await self.client.call(
                    "skills.create",
                    {"name": name, "description": desc},
                )
                self._current = res
            
            await self._reload()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))

    def _delete(self) -> None:
        if not self._current:
            return
        
        if (
            QtWidgets.QMessageBox.question(
                self,
                "Delete skill",
                f"Permanently delete the '{self._current['name']}' skill template?",
            )
            != QtWidgets.QMessageBox.StandardButton.Yes
        ):
            return
        
        asyncio.ensure_future(self._delete_async())

    async def _delete_async(self) -> None:
        try:
            await self.client.call("skills.delete", {"id": self._current["id"]})
            self._current = None
            await self._reload()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Delete failed", str(e))
