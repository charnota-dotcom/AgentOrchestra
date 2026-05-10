"""Inspector — right-side editor for the currently selected node.

Shows static information for control nodes and editable fields for
agent nodes (Goal override, eventually provider/model swap).  When
nothing is selected, shows a flow-level panel with the flow's name
and a Run button.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.canvas.nodes.base import BaseNode


class InspectorPanel(QtWidgets.QWidget):
    flow_name_changed = QtCore.Signal(str)
    run_requested = QtCore.Signal()
    cancel_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background:#fff;")
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)
        self._body: QtWidgets.QWidget | None = None
        self._current_node: BaseNode | None = None
        self._selection_callbacks: list[Callable[[], None]] = []
        self._show_flow_panel("Untitled flow")

    # ------------------------------------------------------------------
    # Selection-driven display
    # ------------------------------------------------------------------

    def show_for(self, nodes: list[BaseNode]) -> None:
        if not nodes:
            self._show_flow_panel(self._last_flow_name)
            return
        # Multi-select: show count, no per-node fields (V2 polish).
        if len(nodes) > 1:
            self._show_multi(nodes)
            return
        self._show_node(nodes[0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replace_body(self, body: QtWidgets.QWidget) -> None:
        if self._body is not None:
            self._layout.removeWidget(self._body)
            self._body.deleteLater()
        self._body = body
        self._layout.addWidget(body, stretch=1)

    def _show_flow_panel(self, name: str) -> None:
        self._last_flow_name = name
        self._current_node = None
        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(self._heading("Flow"))
        name_input = QtWidgets.QLineEdit(name)
        name_input.setPlaceholderText("Flow name")
        name_input.editingFinished.connect(  # type: ignore[arg-type]
            lambda: self.flow_name_changed.emit(name_input.text().strip() or "Untitled flow")
        )
        v.addWidget(name_input)

        v.addSpacing(6)
        v.addWidget(self._small("Drag nodes from the palette on the left."))
        v.addWidget(self._small("Connect them by dragging from one port to another."))
        v.addWidget(self._small("Hit Run when ready."))

        v.addStretch(1)
        run_btn = QtWidgets.QPushButton("Run flow")
        run_btn.setStyleSheet(
            "QPushButton{padding:8px 14px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;}"
            "QPushButton:hover{background:#1860d6;}"
        )
        run_btn.clicked.connect(self.run_requested.emit)  # type: ignore[arg-type]
        v.addWidget(run_btn)

        cancel_btn = QtWidgets.QPushButton("Cancel run")
        cancel_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;background:#f6f8fa;color:#5b6068;"
            "border-radius:4px;border:1px solid #d0d3d9;}"
            "QPushButton:hover{background:#fde8e7;border-color:#b3261e;color:#b3261e;}"
        )
        cancel_btn.clicked.connect(self.cancel_requested.emit)  # type: ignore[arg-type]
        v.addWidget(cancel_btn)

        self._replace_body(body)

    def _show_multi(self, nodes: list[BaseNode]) -> None:
        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(self._heading(f"{len(nodes)} nodes selected"))
        v.addWidget(self._small("Multi-edit is not wired yet — pick a single node."))
        v.addStretch(1)
        self._replace_body(body)

    def _show_node(self, node: BaseNode) -> None:
        # Local import to dodge a circular dependency with nodes/agent.py
        from apps.gui.canvas.nodes.agent import AgentNode
        from apps.gui.canvas.nodes.control import BranchNode

        self._current_node = node
        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(self._heading(node.title()))

        if isinstance(node, AgentNode):
            v.addWidget(self._small(f"Card: {node.card.get('name', '?')}"))
            v.addWidget(self._small(f"Provider: {node.card.get('provider', '?')}"))
            v.addWidget(self._small(f"Model: {node.card.get('model', '?')}"))
            v.addSpacing(6)
            v.addWidget(self._small("Goal override (optional, overrides upstream input):"))
            goal_box = QtWidgets.QPlainTextEdit(node.goal_override)
            goal_box.setPlaceholderText("Leave blank to take Goal from upstream node output.")
            goal_box.setMinimumHeight(120)

            def commit_goal() -> None:
                node.goal_override = goal_box.toPlainText().strip()

            goal_box.textChanged.connect(commit_goal)  # type: ignore[arg-type]
            v.addWidget(goal_box, stretch=1)
        elif isinstance(node, BranchNode):
            v.addWidget(self._small("Regex pattern matched against the upstream text:"))
            pattern_input = QtWidgets.QLineEdit(node.pattern)
            pattern_input.editingFinished.connect(  # type: ignore[arg-type]
                lambda: setattr(node, "pattern", pattern_input.text() or ".*")
            )
            v.addWidget(pattern_input)
            v.addStretch(1)
        else:
            v.addWidget(self._small("No editable fields for this node type."))
            v.addStretch(1)

        delete_btn = QtWidgets.QPushButton("Delete node")
        delete_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;background:#f6f8fa;color:#5b6068;"
            "border-radius:4px;border:1px solid #d0d3d9;}"
            "QPushButton:hover{background:#fde8e7;border-color:#b3261e;color:#b3261e;}"
        )
        delete_btn.clicked.connect(self._fire_delete)  # type: ignore[arg-type]
        v.addWidget(delete_btn)

        self._replace_body(body)

    delete_requested = QtCore.Signal(object)  # BaseNode

    def _fire_delete(self) -> None:
        if self._current_node is not None:
            self.delete_requested.emit(self._current_node)

    @staticmethod
    def _heading(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        return lbl

    @staticmethod
    def _small(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#5b6068;font-size:11px;")
        return lbl
