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

    def show_for(self, items: list[BaseNode | Edge]) -> None:
        if not items:
            self._show_flow_panel(self._last_flow_name)
            return
        # Multi-select: show count, no per-node fields (V2 polish).
        if len(items) > 1:
            self._show_multi(items)
            return
        
        from apps.gui.canvas.nodes.base import BaseNode
        from apps.gui.canvas.edges import Edge
        
        if isinstance(items[0], BaseNode):
            self._show_node(items[0])
        elif isinstance(items[0], Edge):
            self._show_edge(items[0])

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
        from apps.gui.canvas.nodes.control import IntegrationActionNode
        from apps.gui.canvas.nodes.staging_area import StagingAreaNode

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
        elif isinstance(node, StagingAreaNode):
            v.addWidget(self._small(f"Mode: {node.mode.replace('_', ' ')}"))
            v.addWidget(self._small("Manual gate only; this node does not execute app code."))
            v.addWidget(self._small("Release / gating settings"))
            mode_input = QtWidgets.QComboBox()
            mode_input.addItems(
                [
                    "wait_for_all",
                    "wait_for_any",
                    "threshold",
                    "manual_release",
                    "agent_decision",
                    "budget_gate",
                    "quality_gate",
                ]
            )
            mode_input.setCurrentText(node.mode)
            mode_input.currentTextChanged.connect(node.set_mode)  # type: ignore[arg-type]
            v.addWidget(mode_input)

            threshold_input = QtWidgets.QSpinBox()
            threshold_input.setMinimum(1)
            threshold_input.setMaximum(999)
            threshold_input.setValue(node.threshold)
            threshold_input.valueChanged.connect(node.set_threshold)  # type: ignore[arg-type]
            v.addWidget(self._small("Threshold:"))
            v.addWidget(threshold_input)

            timeout_input = QtWidgets.QSpinBox()
            timeout_input.setMinimum(0)
            timeout_input.setMaximum(3600)
            timeout_input.setSpecialValueText("No timeout")
            timeout_input.setValue(node.timeout_seconds or 0)

            def commit_timeout(value: int) -> None:
                node.set_timeout_seconds(value or None)

            timeout_input.valueChanged.connect(commit_timeout)  # type: ignore[arg-type]
            v.addWidget(self._small("Timeout (seconds):"))
            v.addWidget(timeout_input)

            summary_input = QtWidgets.QPlainTextEdit(node.summary_hint)
            summary_input.setPlaceholderText("Optional release summary or note.")
            summary_input.setMinimumHeight(90)

            def commit_summary() -> None:
                node.summary_hint = summary_input.toPlainText().strip()
                node.sync_view()

            summary_input.textChanged.connect(commit_summary)  # type: ignore[arg-type]
            v.addWidget(self._small("Summary hint:"))
            v.addWidget(summary_input, stretch=1)
        elif isinstance(node, IntegrationActionNode):
            title_input = QtWidgets.QLineEdit(node.title())
            title_input.setPlaceholderText("collect WordFlash article")
            summary_input = QtWidgets.QLineEdit(node.summary_hint)
            summary_input.setPlaceholderText("Configured action summary")
            body_input = QtWidgets.QPlainTextEdit(node._body)
            body_input.setPlaceholderText("Short body text shown on the card.")
            body_input.setMinimumHeight(90)

            kind_input = QtWidgets.QLineEdit(node.integration_kind)
            kind_input.setPlaceholderText("mcp_tool")
            app_input = QtWidgets.QLineEdit(node.target_app)
            app_input.setPlaceholderText("WordFlash")
            action_input = QtWidgets.QLineEdit(node.action_name)
            action_input.setPlaceholderText("collect article")
            server_input = QtWidgets.QLineEdit(node.server_id)
            server_input.setPlaceholderText("trusted MCP server id")
            tool_input = QtWidgets.QLineEdit(node.tool_name)
            tool_input.setPlaceholderText("tool name")
            args_box = QtWidgets.QPlainTextEdit(node.arguments_text)
            args_box.setMinimumHeight(90)
            args_box.setPlaceholderText("Arguments payload (text or JSON)")

            def commit_content() -> None:
                node._title = title_input.text().strip() or "Machine action"
                node.summary_hint = summary_input.text().strip()
                node.set_body(body_input.toPlainText().strip())
                node.sync_view()

            def commit_action() -> None:
                node.integration_kind = kind_input.text().strip() or "mcp_tool"
                node.target_app = app_input.text().strip()
                node.action_name = action_input.text().strip()
                node.server_id = server_input.text().strip()
                node.tool_name = tool_input.text().strip()
                node.arguments_text = args_box.toPlainText().strip()
                node.sync_view()

            for widget in (title_input, summary_input):
                widget.editingFinished.connect(commit_content)  # type: ignore[arg-type]
            body_input.textChanged.connect(commit_content)  # type: ignore[arg-type]
            for widget in (kind_input, app_input, action_input, server_input, tool_input):
                widget.editingFinished.connect(commit_action)  # type: ignore[arg-type]
            args_box.textChanged.connect(commit_action)  # type: ignore[arg-type]

            v.addWidget(self._heading("Content"))
            v.addWidget(self._small("Header"))
            v.addWidget(title_input)
            v.addWidget(self._small("Summary"))
            v.addWidget(summary_input)
            v.addWidget(self._small("Body"))
            v.addWidget(body_input, stretch=1)
            v.addWidget(self._heading("Machine code"))
            v.addWidget(self._small("Configured action shown at the bottom of the card."))
            v.addWidget(self._small("Integration kind:"))
            v.addWidget(kind_input)
            v.addWidget(self._small("Target app:"))
            v.addWidget(app_input)
            v.addWidget(self._small("Action name:"))
            v.addWidget(action_input)
            v.addWidget(self._small("MCP server id:"))
            v.addWidget(server_input)
            v.addWidget(self._small("MCP tool name:"))
            v.addWidget(tool_input)
            v.addWidget(self._small("Arguments:"))
            v.addWidget(args_box, stretch=1)
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

    def _show_edge(self, edge: Edge) -> None:
        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(self._heading("Edge"))
        v.addWidget(self._small(f"From: {edge.source.owner.title() if edge.source else '?'}"))
        v.addWidget(self._small(f"To: {edge.target.owner.title() if edge.target else '?'}"))

        v.addSpacing(6)
        v.addWidget(self._small("Label (shown on the line):"))
        label_input = QtWidgets.QLineEdit(edge.label)
        label_input.setPlaceholderText("Optional label")

        def commit_label() -> None:
            edge.label = label_input.text().strip()
            edge.update_path()
            edge.update()

        label_input.textChanged.connect(commit_label)  # type: ignore[arg-type]
        v.addWidget(label_input)

        v.addSpacing(6)
        directional_cb = QtWidgets.QCheckBox("Directional (draw arrowhead)")
        directional_cb.setChecked(edge.directional)

        def toggle_directional(state: int) -> None:
            edge.directional = (state == QtCore.Qt.CheckState.Checked.value)
            edge.update()

        directional_cb.stateChanged.connect(toggle_directional)  # type: ignore[arg-type]
        v.addWidget(directional_cb)
        v.addWidget(self._small("Unchecked means a simple context line with no execution arrowhead."))

        v.addStretch(1)

        delete_btn = QtWidgets.QPushButton("Delete edge")
        delete_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;background:#f6f8fa;color:#5b6068;"
            "border-radius:4px;border:1px solid #d0d3d9;}"
            "QPushButton:hover{background:#fde8e7;border-color:#b3261e;color:#b3261e;}"
        )
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(edge))  # type: ignore[arg-type]
        v.addWidget(delete_btn)

        self._replace_body(body)

    delete_requested = QtCore.Signal(object)  # BaseNode | Edge

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
