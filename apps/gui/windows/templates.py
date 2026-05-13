"""Templates tab â€” graph-template builder and library publisher."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.canvas.commands import (
    AddEdgeCommand,
    AddNodeCommand,
    MoveNodeCommand,
    RemoveEdgeCommand,
    RemoveNodeCommand,
)
from apps.gui.canvas.edges import DraftEdge, Edge
from apps.gui.canvas.layout import LayoutCycleError, auto_layout
from apps.gui.canvas.nodes.base import BaseNode
from apps.gui.canvas.nodes.template_graph import TemplateGraphNode
from apps.gui.canvas.ports import Port, PortDirection
from apps.gui.canvas.scene import CanvasScene
from apps.gui.canvas.view import CanvasView
from apps.service.types import (
    AgentTemplate,
    TemplateEdge,
    TemplateNode,
    TemplateValidationResult,
    long_id,
)

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _tokenize(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in text.replace("\n", ",").split(","):
        token = raw.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _default_template_node(kind: str) -> dict[str, Any]:
    if kind == "start":
        return {
            "type": "start",
            "title": "Start",
            "summary": "Kick off the template.",
            "body": "Starts the template execution when the user presses Run.",
        }
    if kind == "decision":
        return {
            "type": "decision",
            "title": "Decision",
            "summary": "Branch on incoming text.",
            "body": "Branch on the incoming text.",
            "params": {"pattern": ".*"},
        }
    if kind == "agent_action":
        return {
            "type": "agent_action",
            "title": "Agent action",
            "subtitle": "worker",
            "summary": "Describe the agent's task.",
            "body": "",
            "agent_role": "worker",
            "instruction": "Do the task described by this node.",
            "card_mapping": {
                "canvas_type": "reaper",
                "name": "Agent action",
                "description": "Reusable agent card from the template builder.",
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "role": "worker",
            },
        }
    if kind == "command":
        return {
            "type": "command",
            "title": "Command",
            "summary": "echo hello",
            "body": "",
            "command": "echo hello",
        }
    if kind == "documentation":
        return {
            "type": "documentation",
            "title": "Note",
            "summary": "Documentation-only note.",
            "body": "",
        }
    if kind == "end":
        return {
            "type": "end",
            "title": "End",
            "summary": "Template output.",
            "body": "",
        }
    return {
        "type": kind,
        "title": kind.replace("_", " ").title(),
        "summary": "",
        "body": "",
    }


def _preview_text(text: str, *, limit: int = 140) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _issue_label(issue: Any) -> str:
    parts = [str(issue.get("message", "")).strip()]
    node_id = str(issue.get("node_id") or "").strip()
    edge_id = str(issue.get("edge_id") or "").strip()
    field = str(issue.get("field") or "").strip()
    if node_id:
        parts.append(f"(node {node_id})")
    elif edge_id:
        parts.append(f"(edge {edge_id})")
    if field:
        parts.append(f"[{field}]")
    return " ".join(part for part in parts if part)


def _button_style(*, primary: bool = False, destructive: bool = False, checked: bool = False) -> str:
    if destructive:
        return (
            "QPushButton{padding:5px 10px;border:1px solid #d0d3d9;border-radius:4px;"
            "background:#fff;color:#b3261e;}"
            "QPushButton:hover{background:#fde8e7;border-color:#f0b8b2;}"
            "QPushButton:pressed{background:#f9d6d2;}"
            "QPushButton:disabled{background:#f6f8fa;color:#aab1bb;border-color:#e6e7eb;}"
        )
    if primary:
        base = (
            "QPushButton{padding:5px 12px;border:1px solid #1f6feb;border-radius:4px;"
            "background:#1f6feb;color:#fff;font-weight:600;}"
            "QPushButton:hover{background:#1860d6;border-color:#1860d6;}"
            "QPushButton:pressed{background:#1558c0;}"
            "QPushButton:disabled{background:#c8d9f5;color:#ffffff;border-color:#c8d9f5;}"
        )
        if checked:
            base += "QPushButton:checked{background:#1f7a3f;border-color:#1f7a3f;}"
        return base
    base = (
        "QPushButton{padding:5px 10px;border:1px solid #d0d3d9;border-radius:4px;"
        "background:#fff;color:#0f1115;}"
        "QPushButton:hover{background:#eef0f3;}"
        "QPushButton:pressed{background:#dde1e6;}"
        "QPushButton:disabled{background:#f6f8fa;color:#aab1bb;border-color:#e6e7eb;}"
    )
    if checked:
        base += "QPushButton:checked{background:#dde6f5;color:#0f1115;border-color:#b8c8e0;}"
    return base


class _TemplateInspector(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background:#fff;")
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)
        self._body: QtWidgets.QWidget | None = None
        self._show_empty()

    def _replace(self, widget: QtWidgets.QWidget) -> None:
        if self._body is not None:
            self._layout.removeWidget(self._body)
            self._body.deleteLater()
        self._body = widget
        self._layout.addWidget(widget, stretch=1)

    def _heading(self, text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        return lbl

    def _small(self, text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#5b6068;font-size:11px;")
        return lbl

    def _show_empty(self) -> None:
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._heading("Template builder"))
        v.addWidget(
            self._small(
                "Select a template to edit it, or create a new one and start with a Start node."
            )
        )
        v.addWidget(
            self._small(
                "The canvas preview stays short; the full agent instruction lives in the inspector."
            )
        )
        v.addStretch(1)
        self._replace(wrap)

    def show_template(self, template: AgentTemplate, on_change: Callable[[], None]) -> None:
        wrap = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(wrap)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        name = QtWidgets.QLineEdit(template.name)
        name.setToolTip("Template name shown in the list, canvas sidebar, and deployment dialogs.")
        desc = QtWidgets.QPlainTextEdit(template.description)
        desc.setMinimumHeight(90)
        desc.setToolTip("Short description used in the library and publish prompts.")
        category = QtWidgets.QLineEdit(template.category)
        category.setToolTip("Grouping label for the template library.")
        tags = QtWidgets.QLineEdit(", ".join(template.tags))
        tags.setToolTip("Comma- or newline-separated tags. Duplicate entries are ignored.")
        icon = QtWidgets.QLineEdit(template.icon or "")
        icon.setToolTip("Optional icon name or symbol for the library row.")
        published = QtWidgets.QCheckBox("Published in canvas sidebar")
        published.setChecked(template.published)
        published.setToolTip("Only published templates appear in the Canvas sidebar.")

        def commit() -> None:
            template.name = name.text().strip() or "Untitled template"
            template.description = desc.toPlainText().strip()
            template.category = category.text().strip() or "general"
            template.tags = _tokenize(tags.text())
            template.icon = icon.text().strip() or None
            template.published = published.isChecked()
            on_change()
            self.changed.emit()

        for widget in (name, category, tags, icon):
            widget.editingFinished.connect(commit)  # type: ignore[arg-type]
        desc.textChanged.connect(commit)  # type: ignore[arg-type]
        published.toggled.connect(lambda _checked=False: commit())  # type: ignore[arg-type]

        form.addRow("Name", name)
        form.addRow("Description", desc)
        form.addRow("Category", category)
        form.addRow("Tags", tags)
        form.addRow("Icon", icon)
        form.addRow("", published)
        self._replace(wrap)

    def show_node(self, node: TemplateGraphNode, on_change: Callable[[], None]) -> None:
        data = node.node_data
        wrap = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(wrap)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        title = QtWidgets.QLineEdit(node.title())
        title.setToolTip("Node title shown on the canvas.")
        subtitle = QtWidgets.QLineEdit(data.get("subtitle") or node._subtitle)
        subtitle.setToolTip("Optional subtitle shown below the title.")
        
        summary = QtWidgets.QLineEdit(data.get("summary") or node._body)
        summary.setToolTip("Short summary shown on the card body (1-2 lines).")

        # Body is now mostly used as internal storage if needed.
        # The summary is what shows on the card.
        body = QtWidgets.QPlainTextEdit(data.get("body") or "")
        body.setMinimumHeight(60)
        body.setToolTip("Internal body text or secondary detail.")

        agent_role = QtWidgets.QLineEdit(str(data.get("agent_role") or ""))
        agent_role.setToolTip("Role name stored in the node payload and template validation.")
        instruction = QtWidgets.QPlainTextEdit(str(data.get("instruction") or ""))
        instruction.setMinimumHeight(90)
        instruction.setToolTip("Full task instruction for agent_action nodes.")
        command = QtWidgets.QPlainTextEdit(str(data.get("command") or ""))
        command.setMinimumHeight(70)
        command.setToolTip("Shell command stored on command nodes.")
        card_name = QtWidgets.QLineEdit(str((data.get("card_mapping") or {}).get("name") or ""))
        card_name.setToolTip("Reusable card name created when this template deploys.")
        card_provider = QtWidgets.QLineEdit(str((data.get("card_mapping") or {}).get("provider") or ""))
        card_provider.setToolTip("Provider for the deployed card.")
        card_model = QtWidgets.QLineEdit(str((data.get("card_mapping") or {}).get("model") or ""))
        card_model.setToolTip("Model name for the deployed card.")
        card_desc = QtWidgets.QPlainTextEdit(str((data.get("card_mapping") or {}).get("description") or ""))
        card_desc.setMinimumHeight(70)
        card_desc.setToolTip("Description copied into the deployed card payload.")

        def commit() -> None:
            data["title"] = title.text().strip() or "Untitled"
            data["subtitle"] = subtitle.text().strip()
            data["summary"] = summary.text().strip()
            data["body"] = body.toPlainText().strip()
            data["agent_role"] = agent_role.text().strip() or None
            data["instruction"] = instruction.toPlainText().strip() or None
            data["command"] = command.toPlainText().strip() or None
            mapping = dict(data.get("card_mapping") or {})
            mapping.update(
                {
                    "name": card_name.text().strip(),
                    "provider": card_provider.text().strip(),
                    "model": card_model.text().strip(),
                    "description": card_desc.toPlainText().strip(),
                }
            )
            if any(mapping.values()):
                data["card_mapping"] = mapping
            node.refresh_visuals()
            on_change()
            self.changed.emit()

        for widget in (title, subtitle, summary, agent_role, card_name, card_provider, card_model):
            widget.editingFinished.connect(commit)  # type: ignore[arg-type]
        body.textChanged.connect(commit)  # type: ignore[arg-type]
        instruction.textChanged.connect(commit)  # type: ignore[arg-type]
        command.textChanged.connect(commit)  # type: ignore[arg-type]
        card_desc.textChanged.connect(commit)  # type: ignore[arg-type]

        form.addRow("Type", QtWidgets.QLabel(node.template_type))
        form.addRow("Title", title)
        form.addRow("Subtitle", subtitle)
        form.addRow("Summary", summary)
        
        if data.get("type") == "agent_action":
            form.addRow("Agent role", agent_role)
            form.addRow("Instruction", instruction)
        elif data.get("type") == "command":
            form.addRow("Command", command)
        else:
            form.addRow("Body", body)
            
        if data.get("type") == "agent_action":
            form.addRow(self._heading("Card mapping"))
            form.addRow("Card name", card_name)
            form.addRow("Card provider", card_provider)
            form.addRow("Card model", card_model)
            form.addRow("Card description", card_desc)
        
        self._replace(wrap)

    def show_edge(self, edge: Edge, on_change: Callable[[], None]) -> None:
        wrap = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(wrap)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        label = QtWidgets.QLineEdit(edge.label)
        label.setToolTip("Text displayed on the edge.")
        directional = QtWidgets.QCheckBox("Directional")
        directional.setChecked(edge.directional)
        directional.setToolTip("Toggle the arrowhead on or off.")

        def commit() -> None:
            edge.label = label.text().strip()
            edge.directional = directional.isChecked()
            edge.update_path()
            edge.update()
            on_change()
            self.changed.emit()

        label.textChanged.connect(commit)  # type: ignore[arg-type]
        directional.toggled.connect(lambda _checked=False: commit())  # type: ignore[arg-type]

        form.addRow("From", QtWidgets.QLabel(edge.source.owner.title() if edge.source else "?"))
        form.addRow("To", QtWidgets.QLabel(edge.target.owner.title() if edge.target else "?"))
        form.addRow("Label", label)
        form.addRow("", directional)
        self._replace(wrap)


class TemplateBuilderPage(QtWidgets.QWidget):
    library_changed = QtCore.Signal()

    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._templates: list[AgentTemplate] = []
        self._current: AgentTemplate | None = None
        self._draft_edge: DraftEdge | None = None
        self._draft_source: Port | None = None
        self._drag_start: dict[str, QtCore.QPointF] = {}
        self._selected_node: TemplateGraphNode | None = None
        self._selected_edge: Edge | None = None
        self._scene_ready = False
        self._validation_result: Any | None = None
        self._status_clear_timer = QtCore.QTimer(self)
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.timeout.connect(lambda: self.status.setText(""))  # type: ignore[arg-type]
        self._validation_timer = QtCore.QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.timeout.connect(self._refresh_validation_now)  # type: ignore[arg-type]
        self._settings = QtCore.QSettings()
        self._settings_key = "templates/builder_splitter_state"
        self.undo_stack = QtGui.QUndoStack(self)
        self.setStyleSheet("background:#fafbfc;")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet("QSplitter::handle{background:#e6e7eb;}")
        root.addWidget(self.splitter, stretch=1)

        self.sidebar = self._build_sidebar()
        self.sidebar.setMinimumWidth(50)
        self.splitter.addWidget(self.sidebar)

        centre = QtWidgets.QWidget()
        centre.setMinimumWidth(50)
        c = QtWidgets.QVBoxLayout(centre)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(0)
        c.addWidget(self._build_toolbar())
        self.guide_frame = self._build_guide()
        c.addWidget(self.guide_frame)

        self.scene = CanvasScene()
        self.scene.selection_changed.connect(self._on_selection_changed)  # type: ignore[arg-type]
        self.view = CanvasView(self.scene)
        self.view.viewport().installEventFilter(self)
        c.addWidget(self.view, stretch=1)
        self.splitter.addWidget(centre)

        self.inspector = _TemplateInspector()
        self.inspector.setMinimumWidth(50)
        self.splitter.addWidget(self.inspector)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        QtCore.QTimer.singleShot(0, self._restore_splitter_state)

        QtCore.QTimer.singleShot(0, lambda: asyncio.ensure_future(self._reload()))

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        asyncio.ensure_future(self._reload())
        self._request_validation_refresh()

    def _build_guide(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(
            "QFrame{background:#f8fbff;border:1px solid #d7e6fb;border-radius:6px;}"
        )
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        title = QtWidgets.QLabel("Getting started")
        title.setStyleSheet("font-size:13px;font-weight:600;color:#0f1115;")
        v.addWidget(title)
        for text in (
            "1. Add a Start node, then connect nodes left to right.",
            "2. Keep agent instructions in the multiline Instruction field.",
            "3. Validate before publishing or deploying to Canvas.",
        ):
            lbl = QtWidgets.QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#5b6068;font-size:11px;")
            v.addWidget(lbl)
        self.guide_text = QtWidgets.QLabel(
            "This helper hides automatically once the template has nodes or you select an item."
        )
        self.guide_text.setWordWrap(True)
        self.guide_text.setStyleSheet("color:#5b6068;font-size:11px;")
        v.addWidget(self.guide_text)
        return frame

    def _restore_splitter_state(self) -> None:
        value = self._settings.value(self._settings_key)
        if value is not None:
            self.splitter.restoreState(value)

    def _save_splitter_state(self) -> None:
        self._settings.setValue(self._settings_key, self.splitter.saveState())

    def _request_validation_refresh(self) -> None:
        if self._current is None:
            self._update_validation_panel(None)
            return
        self.validation_summary.setText(f"Validating {self._current.name}…")
        self.validation_ready.setText("Publish/deploy readiness: checking")
        self._validation_timer.start(0)

    def _refresh_validation_now(self) -> None:
        asyncio.ensure_future(self._validate_async(update_only=True))

    def _set_status(self, text: str, *, kind: str = "info", timeout_ms: int = 3200) -> None:
        palette = {
            "info": ("#0f1115", "#f6f8fa", "#e6e7eb"),
            "success": ("#1f7a3f", "#edf8f1", "#cdeed8"),
            "warning": ("#a96b00", "#fff8e7", "#f0d58a"),
            "error": ("#b3261e", "#fdecea", "#f0b8b2"),
        }
        fg, bg, border = palette.get(kind, palette["info"])
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{fg};font-size:11px;padding:6px 8px;background:{bg};"
            f"border:1px solid {border};border-radius:4px;"
        )
        self._status_clear_timer.stop()
        if timeout_ms > 0:
            self._status_clear_timer.start(timeout_ms)

    def _sync_action_state(self) -> None:
        has_current = self._current is not None
        for attr in ("duplicate_btn", "delete_btn", "validate_btn", "layout_btn", "export_btn", "deploy_btn", "publish_btn"):
            if not hasattr(self, attr):
                return
        self.duplicate_btn.setEnabled(has_current)
        self.delete_btn.setEnabled(has_current)
        self.validate_btn.setEnabled(has_current)
        self.layout_btn.setEnabled(has_current)
        self.export_btn.setEnabled(has_current)
        self.deploy_btn.setEnabled(has_current)
        self.publish_btn.setEnabled(has_current)
        if has_current:
            self.publish_btn.setText("Unpublish" if self._current and self._current.published else "Publish")
            self.publish_btn.setEnabled(bool(self._current and self._current.published) or self._validation_valid())
        else:
            self.publish_btn.setText("Publish")
        self._update_guide()

    def _validation_valid(self) -> bool:
        result = self._validation_result
        if result is None:
            return False
        return bool(getattr(result, "valid", False))

    def _update_guide(self) -> None:
        show = self._current is None or not self.scene.nodes()
        self.guide_frame.setVisible(show)

    def _update_validation_panel(self, result: Any | None) -> None:
        self._validation_result = result
        if result is None:
            self.validation_summary.setText("No template selected.")
            self.validation_ready.setText("Publish/deploy readiness: unavailable")
            self.validation_errors.clear()
            self.validation_warnings.clear()
            self._sync_action_state()
            return

        errors = list(getattr(result, "errors", []) or [])
        warnings = list(getattr(result, "warnings", []) or [])
        valid = bool(getattr(result, "valid", False))
        summary_text = (
            f"{'Ready to publish' if valid else 'Needs fixes'} · "
            f"{len(errors)} errors · {len(warnings)} warnings"
        )
        self.validation_summary.setText(
            f"{'Ready to publish' if valid else 'Needs fixes'} · {len(errors)} errors · {len(warnings)} warnings"
        )
        self.validation_ready.setText(
            "Publish/deploy readiness: ready" if valid else "Publish/deploy readiness: blocked"
        )
        self.validation_summary.setText(summary_text)
        self.validation_errors.clear()
        for issue in errors:
            item = QtWidgets.QListWidgetItem(_issue_label(issue.model_dump(mode="json")))
            self.validation_errors.addItem(item)
        self.validation_warnings.clear()
        for issue in warnings:
            item = QtWidgets.QListWidgetItem(_issue_label(issue.model_dump(mode="json")))
            self.validation_warnings.addItem(item)
        self._sync_action_state()

    def _build_sidebar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet("background:#fff;border-right:1px solid #e6e7eb;")
        wrap.setMinimumWidth(280)
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Templates")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        header.addWidget(title)
        header.addStretch(1)
        new_btn = QtWidgets.QPushButton("+ New")
        new_btn.setToolTip("Create a new blank template.")
        new_btn.setStyleSheet(_button_style(primary=True))
        new_btn.clicked.connect(self._new_template)  # type: ignore[arg-type]
        header.addWidget(new_btn)
        v.addLayout(header)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget{border:none;background:transparent;}"
            "QListWidget::item{padding:8px 6px;border-radius:4px;}"
            "QListWidget::item:hover{background:#eef0f3;}"
            "QListWidget::item:selected{background:#dde6f5;color:#0f1115;}"
        )
        self.list_widget.currentRowChanged.connect(self._on_select)  # type: ignore[arg-type]
        v.addWidget(self.list_widget, stretch=1)

        self.validation_card = QtWidgets.QFrame()
        self.validation_card.setStyleSheet(
            "QFrame{background:#f8fbff;border:1px solid #d7e6fb;border-radius:6px;}"
        )
        validation_layout = QtWidgets.QVBoxLayout(self.validation_card)
        validation_layout.setContentsMargins(10, 10, 10, 10)
        validation_layout.setSpacing(6)

        validation_title = QtWidgets.QLabel("Validation")
        validation_title.setStyleSheet("font-size:12px;font-weight:600;color:#0f1115;")
        validation_layout.addWidget(validation_title)

        self.validation_summary = QtWidgets.QLabel("No template selected.")
        self.validation_summary.setWordWrap(True)
        self.validation_summary.setStyleSheet("color:#5b6068;font-size:11px;")
        validation_layout.addWidget(self.validation_summary)

        self.validation_ready = QtWidgets.QLabel("Publish/deploy readiness: unavailable")
        self.validation_ready.setWordWrap(True)
        self.validation_ready.setStyleSheet("color:#5b6068;font-size:11px;")
        validation_layout.addWidget(self.validation_ready)

        validation_layout.addWidget(QtWidgets.QLabel("Errors"))
        self.validation_errors = QtWidgets.QListWidget()
        self.validation_errors.setMaximumHeight(90)
        self.validation_errors.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:4px;}"
            "QListWidget::item{padding:4px 6px;}"
        )
        validation_layout.addWidget(self.validation_errors)

        validation_layout.addWidget(QtWidgets.QLabel("Warnings"))
        self.validation_warnings = QtWidgets.QListWidget()
        self.validation_warnings.setMaximumHeight(90)
        self.validation_warnings.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #e6e7eb;border-radius:4px;}"
            "QListWidget::item{padding:4px 6px;}"
        )
        validation_layout.addWidget(self.validation_warnings)
        v.addWidget(self.validation_card)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            "color:#0f1115;font-size:11px;padding:6px 8px;background:#f6f8fa;"
            "border:1px solid #e6e7eb;border-radius:4px;"
        )
        v.addWidget(self.status)

        btn_row = QtWidgets.QHBoxLayout()
        self.duplicate_btn = QtWidgets.QPushButton("Duplicate")
        self.duplicate_btn.setToolTip("Clone the current template into a new draft.")
        self.duplicate_btn.setStyleSheet(_button_style())
        self.duplicate_btn.clicked.connect(self._duplicate_selected)  # type: ignore[arg-type]
        btn_row.addWidget(self.duplicate_btn)
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.setToolTip("Delete the current template.")
        self.delete_btn.setStyleSheet(_button_style(destructive=True))
        self.delete_btn.clicked.connect(self._delete_selected)  # type: ignore[arg-type]
        btn_row.addWidget(self.delete_btn)
        v.addLayout(btn_row)

        self._sync_action_state()
        return wrap

    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("background:#fff;border-bottom:1px solid #e6e7eb;")
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(6)

        for label, kind in (
            ("Add Start", "start"),
            ("Add Decision", "decision"),
            ("Add Agent", "agent_action"),
            ("Add Command", "command"),
            ("Add Note", "documentation"),
            ("Add End", "end"),
        ):
            btn = QtWidgets.QPushButton(label)
            btn.setToolTip(f"Insert a {kind.replace('_', ' ')} node into the canvas.")
            btn.setStyleSheet(_button_style())
            btn.clicked.connect(lambda _checked=False, k=kind: self._add_node(k))  # type: ignore[arg-type]
            h.addWidget(btn)

        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setToolTip("Save the current template graph to the service.")
        self.save_btn.setStyleSheet(_button_style(primary=True))
        self.save_btn.clicked.connect(self._save)  # type: ignore[arg-type]
        h.addWidget(self.save_btn)

        self.validate_btn = QtWidgets.QPushButton("Validate")
        self.validate_btn.setToolTip("Run template validation and refresh the summary panel.")
        self.validate_btn.setStyleSheet(_button_style())
        self.validate_btn.clicked.connect(self._validate)  # type: ignore[arg-type]
        h.addWidget(self.validate_btn)

        self.layout_btn = QtWidgets.QPushButton("Auto layout")
        self.layout_btn.setToolTip("Reflow the graph with a simple top-to-bottom layout.")
        self.layout_btn.setStyleSheet(_button_style())
        self.layout_btn.clicked.connect(self._auto_layout)  # type: ignore[arg-type]
        h.addWidget(self.layout_btn)

        self.export_btn = QtWidgets.QPushButton("Export Mermaid")
        self.export_btn.setToolTip("Copy a Mermaid representation of the template graph.")
        self.export_btn.setStyleSheet(_button_style())
        self.export_btn.clicked.connect(self._export_mermaid)  # type: ignore[arg-type]
        h.addWidget(self.export_btn)

        self.deploy_btn = QtWidgets.QPushButton("Test deployment")
        self.deploy_btn.setToolTip("Check whether the current template can deploy to Canvas.")
        self.deploy_btn.setStyleSheet(_button_style())
        self.deploy_btn.clicked.connect(self._test_deployment)  # type: ignore[arg-type]
        h.addWidget(self.deploy_btn)

        self.publish_btn = QtWidgets.QPushButton("Publish")
        self.publish_btn.setToolTip(
            "Publish the template into the Canvas sidebar once validation passes."
        )
        self.publish_btn.setStyleSheet(_button_style(primary=True, checked=True))
        self.publish_btn.clicked.connect(self._toggle_publish)  # type: ignore[arg-type]
        h.addWidget(self.publish_btn)

        h.addStretch(1)
        return bar

    async def _reload(self) -> None:
        try:
            rows = await self.client.call("template_graphs.list", {})
        except Exception as exc:
            self._set_status(f"Reload failed: {exc}", kind="error")
            return
        self._templates = [AgentTemplate.model_validate(row) for row in rows]
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for template in self._templates:
            state = "Published" if template.published else "Draft"
            item = QtWidgets.QListWidgetItem(
                f"{template.name}\n{template.category}  ·  {len(template.nodes)} nodes"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, template.id)
            item.setText(f"{item.text()}  ·  {state}")
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self._current:
            for row, template in enumerate(self._templates):
                if template.id == self._current.id:
                    self.list_widget.setCurrentRow(row)
                    break
        self._sync_action_state()
        self._request_validation_refresh()

    def _new_template(self) -> None:
        self._load_template(AgentTemplate(name="Untitled template"))
        self._set_status("New template created.", kind="success")

    def _duplicate_selected(self) -> None:
        if not self._current:
            return
        asyncio.ensure_future(self._duplicate_async())

    async def _duplicate_async(self) -> None:
        try:
            dup = await self.client.call(
                "template_graphs.duplicate",
                {"template_id": self._current.id, "name": f"{self._current.name} Copy"},
            )
        except Exception as exc:
            self._set_status(f"Duplicate failed: {exc}", kind="error")
            return
        await self._reload()
        self._load_template(AgentTemplate.model_validate(dup))
        self.library_changed.emit()
        self._set_status(f"Duplicated {self._current.name}", kind="success")

    def _delete_selected(self) -> None:
        if not self._current:
            return
        asyncio.ensure_future(self._delete_async(self._current.id))

    async def _delete_async(self, template_id: str) -> None:
        try:
            res = await self.client.call("template_graphs.delete", {"template_id": template_id})
        except Exception as exc:
            self._set_status(f"Delete failed: {exc}", kind="error")
            return
        if not res.get("deleted"):
            self._set_status("Nothing deleted.", kind="warning")
            return
        self._current = None
        self._scene_reset()
        await self._reload()
        self.library_changed.emit()
        self._set_status("Deleted template.", kind="success")

    def _scene_reset(self) -> None:
        for edge in list(self.scene.edges()):
            self.scene.remove_edge(edge)
        for node in list(self.scene.nodes()):
            self.scene.remove_node(node)
        self._update_guide()

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._templates):
            self._current = None
            self._scene_reset()
            self.inspector._show_empty()
            self._update_validation_panel(None)
            self._sync_action_state()
            return
        self._load_template(self._templates[row].model_copy(deep=True))

    def _load_template(self, template: AgentTemplate) -> None:
        self._current = template
        self._scene_reset()
        node_index: dict[str, TemplateGraphNode] = {}
        for node in template.nodes:
            graph_node = TemplateGraphNode(
                node.id,
                {
                    "type": node.type,
                    "title": node.title,
                    "subtitle": node.subtitle,
                    "body": node.body,
                    "params": dict(node.params),
                    "agent_role": node.agent_role,
                    "instruction": node.instruction,
                    "command": node.command,
                    "card_mapping": (
                        node.card_mapping.model_dump(mode="json") if node.card_mapping else None
                    ),
                },
            )
            graph_node.setPos(node.x, node.y)
            self._wire_node(graph_node)
            self.scene.add_node(graph_node)
            node_index[node.id] = graph_node

        for edge in template.edges:
            src = node_index.get(edge.from_node)
            dst = node_index.get(edge.to_node)
            if src is None or dst is None:
                continue
            src_port = next((p for p in src.output_ports if p.name == edge.from_port), src.output_ports[0] if src.output_ports else None)
            dst_port = next((p for p in dst.input_ports if p.name == edge.to_port), dst.input_ports[0] if dst.input_ports else None)
            if src_port and dst_port:
                self.scene.add_edge(Edge(src_port, dst_port, label=edge.label, directional=edge.directional))

        self.inspector.show_template(template, self._mark_dirty)
        self.view.fit_all()
        self._set_status(f"Editing {template.name}")
        self._select_template_row(template.id)
        self._sync_action_state()
        self._update_guide()
        self._request_validation_refresh()

    def _select_template_row(self, template_id: str) -> None:
        for row, template in enumerate(self._templates):
            if template.id == template_id:
                self.list_widget.blockSignals(True)
                self.list_widget.setCurrentRow(row)
                self.list_widget.blockSignals(False)
                break

    def _mark_dirty(self) -> None:
        if self._current is not None:
            self._set_status(f"Editing {self._current.name}")
            self._request_validation_refresh()

    def _wire_node(self, node: BaseNode) -> None:
        for port in node.input_ports + node.output_ports:
            port.edge_drag_started.connect(self._begin_edge_drag)  # type: ignore[arg-type]
        node.geometry_changed.connect(lambda nid=node.node_id: self._note_node_moved(nid))  # type: ignore[arg-type]

    def _add_node(self, kind: str) -> None:
        if self._current is None:
            self._current = AgentTemplate(name="Untitled template")
        data = _default_template_node(kind)
        node = TemplateGraphNode(node_id=self._next_id(), node_data=data)
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(centre)
        self._wire_node(node)
        self.undo_stack.push(AddNodeCommand(self.scene, node))
        self._update_guide()
        self._set_status(f"Added {kind.replace('_', ' ')} node.", kind="success")
        self._request_validation_refresh()

    def _next_id(self) -> str:
        import secrets

        return secrets.token_hex(6)

    def _save(self) -> None:
        asyncio.ensure_future(self._save_async())

    def _collect_template(self) -> AgentTemplate:
        if self._current is None:
            self._current = AgentTemplate(name="Untitled template")
        self._current.nodes = [
            TemplateNode.model_validate(node.to_template_payload()) for node in self.scene.nodes()
            if isinstance(node, TemplateGraphNode)
        ]
        self._current.edges = [
            TemplateEdge.model_validate(
                {
                    "id": long_id(),
                    "from_node": edge.source.owner.node_id if edge.source else "",
                    "from_port": edge.source.name if edge.source else "",
                    "to_node": edge.target.owner.node_id if edge.target else "",
                    "to_port": edge.target.name if edge.target else "",
                    "label": edge.label,
                    "directional": bool(edge.directional),
                }
            )
            for edge in self.scene.edges()
        ]
        return self._current

    def _auto_layout(self) -> None:
        nodes = self.scene.nodes()
        edges = self.scene.edges()
        if not nodes:
            return
        old_positions = {n.node_id: QtCore.QPointF(n.pos()) for n in nodes}
        try:
            auto_layout(nodes, edges)
        except LayoutCycleError as exc:
            self._set_status(f"Auto layout failed: {exc}", kind="error")
            return

        moves: list[tuple[BaseNode, QtCore.QPointF, QtCore.QPointF]] = []
        for node in nodes:
            old = old_positions[node.node_id]
            new = QtCore.QPointF(node.pos())
            if (old - new).manhattanLength() >= 0.5:
                moves.append((node, old, new))
        if not moves:
            return
        self.undo_stack.beginMacro("Auto layout")
        for node, old, new in moves:
            self.undo_stack.push(MoveNodeCommand(node, old, new))
        self.undo_stack.endMacro()
        self.view.fit_all()
        self._set_status("Auto layout applied.", kind="success")

    def _remove_item(self, item: BaseNode | Edge) -> None:
        if isinstance(item, BaseNode):
            self.undo_stack.push(RemoveNodeCommand(self.scene, item))
        elif isinstance(item, Edge):
            self.undo_stack.push(RemoveEdgeCommand(self.scene, item))

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            for item in list(self.scene.selectedItems()):
                if isinstance(item, BaseNode):
                    self.undo_stack.push(RemoveNodeCommand(self.scene, item))
                elif isinstance(item, Edge):
                    self.undo_stack.push(RemoveEdgeCommand(self.scene, item))
        else:
            super().keyPressEvent(event)

    async def _save_async(self) -> None:
        template = self._collect_template()
        payload = template.model_dump(mode="json")
        try:
            if any(t.id == template.id for t in self._templates):
                payload["template_id"] = template.id
                payload["expected_version"] = template.version
                saved = await self.client.call("template_graphs.update", payload)
            else:
                saved = await self.client.call("template_graphs.create", payload)
        except Exception as exc:
            self._set_status(f"Save failed: {exc}", kind="error")
            return
        self._current = AgentTemplate.model_validate(saved)
        await self._reload()
        self.library_changed.emit()
        self._set_status(f"Saved {self._current.name}", kind="success")
        self._request_validation_refresh()

    def _validate(self) -> None:
        asyncio.ensure_future(self._validate_async())

    async def _validate_async(self, *, update_only: bool = False) -> None:
        template = self._collect_template()
        try:
            result = await self.client.call("template_graphs.validate", {"template": template.model_dump(mode="json")})
        except Exception as exc:
            self._set_status(f"Validate failed: {exc}", kind="error")
            return
        validation = TemplateValidationResult.model_validate(result)
        self._update_validation_panel(validation)
        if validation.valid:
            self._set_status(
                f"Validation passed: {len(validation.warnings)} warnings.",
                kind="success",
            )
        else:
            self._set_status(
                f"Validation blocked: {len(validation.errors)} errors.",
                kind="error",
            )
        if not update_only:
            self._sync_action_state()

    def _export_mermaid(self) -> None:
        asyncio.ensure_future(self._export_mermaid_async())

    async def _export_mermaid_async(self) -> None:
        template = self._collect_template()
        try:
            result = await self.client.call(
                "template_graphs.export_mermaid", {"template": template.model_dump(mode="json")}
            )
        except Exception as exc:
            self._set_status(f"Export failed: {exc}", kind="error")
            return
        mermaid = result.get("mermaid", "")
        QtWidgets.QApplication.clipboard().setText(mermaid)
        self._set_status("Mermaid copied to clipboard.", kind="success")

    def _test_deployment(self) -> None:
        asyncio.ensure_future(self._test_deployment_async())

    async def _test_deployment_async(self) -> None:
        template = self._collect_template()
        await self._validate_async(update_only=True)
        validation = self._validation_result
        if validation is not None and getattr(validation, "errors", []):
            self._set_status("Deployment blocked until validation errors are fixed.", kind="error")
            return
        try:
            result = await self.client.call(
                "template_graphs.deploy",
                {
                    "template": template.model_dump(mode="json"),
                    "drop_x": 0.0,
                    "drop_y": 0.0,
                },
            )
        except Exception as exc:
            self._set_status(f"Deploy failed: {exc}", kind="error")
            return
        if result.get("errors"):
            self._set_status(
                "Deployment blocked: " + "; ".join(result.get("errors") or ["blocked"]),
                kind="error",
            )
            return
        self._set_status(
            f"Deployment ready: {len(result.get('nodes') or [])} nodes, {len(result.get('edges') or [])} edges.",
            kind="success",
        )

    def _toggle_publish(self) -> None:
        if self._current is None:
            self._current = AgentTemplate(name="Untitled template")
        publishing = not self._current.published
        if publishing:
            asyncio.ensure_future(self._toggle_publish_async())
            return
        self._current.published = False
        self._mark_dirty()
        self._save()

    async def _toggle_publish_async(self) -> None:
        await self._validate_async(update_only=True)
        if not self._validation_valid():
            self._set_status("Publish blocked until validation passes.", kind="error")
            return
        if self._current is None:
            self._current = AgentTemplate(name="Untitled template")
        self._current.published = True
        self._mark_dirty()
        self._save()

    def _begin_edge_drag(self, port: Port) -> None:
        self._cancel_edge_drag()
        if port.direction == PortDirection.INPUT:
            return
        self._draft_source = port
        self._draft_edge = DraftEdge(port)
        self.scene.addItem(self._draft_edge)
        self._draft_edge.update_to(port.scene_position())

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.view.viewport() and self._draft_edge is not None:
            if event.type() == QtCore.QEvent.Type.MouseMove:
                mev = cast(QtGui.QMouseEvent, event)
                self._draft_edge.update_to(self.view.mapToScene(mev.position().toPoint()))
                return True
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                mev = cast(QtGui.QMouseEvent, event)
                self._finish_edge_drag(self.view.mapToScene(mev.position().toPoint()))
                return True
        if (
            watched is self.view.viewport()
            and event.type() == QtCore.QEvent.Type.MouseButtonRelease
            and self._drag_start
        ):
            self._flush_drag_moves()
        return super().eventFilter(watched, event)

    def _finish_edge_drag(self, scene_pos: QtCore.QPointF) -> None:
        target_port: Port | None = None
        for item in self.scene.items(scene_pos):
            if isinstance(item, Port) and item.direction == PortDirection.INPUT:
                target_port = item
                break
        source = self._draft_source
        self._cancel_edge_drag()
        self._flush_drag_moves()
        if source is None or target_port is None or target_port.owner is source.owner:
            return
        edge = Edge(source, target_port, directional=True)
        self.undo_stack.push(AddEdgeCommand(self.scene, edge))

    def _cancel_edge_drag(self) -> None:
        if self._draft_edge is not None:
            self.scene.removeItem(self._draft_edge)
        self._draft_edge = None
        self._draft_source = None

    def _note_node_moved(self, node_id: str) -> None:
        node = next((n for n in self.scene.nodes() if n.node_id == node_id), None)
        if node is None or node.isSelected():
            return
        if node_id not in self._drag_start:
            self._drag_start[node_id] = QtCore.QPointF(node.pos())

    def _flush_drag_moves(self) -> None:
        if not self._drag_start:
            return
        for node_id, start in list(self._drag_start.items()):
            node = next((n for n in self.scene.nodes() if n.node_id == node_id), None)
            if node is None:
                continue
            new_pos = QtCore.QPointF(node.pos())
            if (new_pos - start).manhattanLength() < 1.0:
                continue
            self.undo_stack.push(MoveNodeCommand(node, start, new_pos))
        self._drag_start.clear()

    def _on_selection_changed(self, items: list[BaseNode | Edge]) -> None:
        if not items:
            if self._current:
                self.inspector.show_template(self._current, self._mark_dirty)
            else:
                self.inspector._show_empty()
            return
        if len(items) > 1:
            self.inspector._show_empty()
            return
        item = items[0]
        if isinstance(item, TemplateGraphNode):
            self._selected_node = item
            self._selected_edge = None
            self.inspector.show_node(item, self._mark_dirty)
        elif isinstance(item, Edge):
            self._selected_edge = item
            self._selected_node = None
            self.inspector.show_edge(item, self._mark_dirty)
