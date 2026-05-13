"""CanvasPage — the orchestrator that ties scene + view + palette
+ inspector + flow execution together.

Adds:

* Drag-and-drop from palette: drop creates the matching node at the
  cursor's scene coordinates.
* Edge dragging: click-and-drag from an output port to an input port
  on a different node.  A draft DashLine edge follows the cursor.
* Save / Load / Run via the new ``flows.*`` RPCs.
* Live SSE subscription to the active flow run; updates node
  visuals and body text as events arrive.

Keep this file thin — node rendering lives in ``nodes/``, scene-level
state in ``scene.py``, view manipulation in ``view.py``.  Nothing
domain-specific (RPC calls, flow JSON shape) is in those.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any, cast

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.canvas.commands import (
    AddEdgeCommand,
    AddNodeCommand,
    MoveNodeCommand,
    RemoveEdgeCommand,
    RemoveNodeCommand,
)
from apps.gui.canvas.drone_chat_dialog import DroneActionChatDialog
from apps.gui.canvas.edges import DraftEdge, Edge
from apps.gui.canvas.inspector import InspectorPanel
from apps.gui.canvas.minimap import Minimap
from apps.gui.canvas.nodes.agent import AgentNode
from apps.gui.canvas.nodes.base import BaseNode, NodeStatus
from apps.gui.canvas.nodes.control import (
    BranchNode,
    HumanNode,
    MergeNode,
    OutputNode,
    TriggerNode,
)
from apps.gui.canvas.nodes.drone_action import DroneActionNode
from apps.gui.canvas.nodes.staging_area import StagingAreaNode
from apps.gui.canvas.palette import PALETTE_MIME, PalettePanel
from apps.gui.canvas.ports import Port, PortDirection
from apps.gui.canvas.scene import CanvasScene
from apps.gui.canvas.view import CanvasView
from apps.service.flows.node_types import canonical_node_type
from apps.gui.ipc.sse_client import SseClient

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient

log = logging.getLogger(__name__)


def _node_id() -> str:
    return secrets.token_hex(6)


_CONTROL_FACTORY = {
    "trigger": TriggerNode,
    "branch": BranchNode,
    "merge": MergeNode,
    "human": HumanNode,
    "output": OutputNode,
    "staging_area": StagingAreaNode,
}

_NODE_FACTORY = {
    "trigger": TriggerNode,
    "branch": BranchNode,
    "merge": MergeNode,
    "human": HumanNode,
    "output": OutputNode,
    "reaper": AgentNode,
    "fpv_drone": DroneActionNode,
    "staging_area": StagingAreaNode,
}


class CanvasPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._sse = SseClient(base_url=client.base_url, token=client.token)
        self._stream_task: asyncio.Task[Any] | None = None
        self._flow_id: str | None = None
        self._flow_name = "Untitled flow"
        self._draft_edge: DraftEdge | None = None
        self._draft_source: Port | None = None

        self.setStyleSheet("background:#fafbfc;")

        # Undo stack for Add/Remove/Move/Connect operations.  Bound
        # to Ctrl+Z / Ctrl+Shift+Z below.
        self.undo_stack = QtGui.QUndoStack(self)
        self.undo_stack.indexChanged.connect(lambda _: self.view.sync_proxies())
        # Per-node "drag start" position so we can produce a single
        # MoveNodeCommand per drag rather than one per pixel of motion.
        self._drag_start: dict[str, QtCore.QPointF] = {}
        # Draft mode flag mirrors Flow.is_draft.  Run is gated when
        # True; the toolbar shows a "Draft" badge.
        self._is_draft: bool = False
        self._settings = QtCore.QSettings()
        self._settings_key = "canvas/splitter_state"

        # Layout: left palette | centre canvas | right inspector
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet("QSplitter::handle{background:#e6e7eb;}")
        main_layout.addWidget(self.splitter)

        self.palette_panel = PalettePanel(client)
        self.palette_panel.setMinimumWidth(50)
        # When the operator clicks "Deploy" in the palette and the
        # drone is deployed server-side, the palette emits this signal
        # so we can drop a DroneActionNode onto
        # the canvas and auto-open the chat dialog.  Without this the
        # operator stared at an empty canvas wondering where the new
        # drone went — same UX story as the old conversation_created.
        self.palette_panel.drone_deployed.connect(  # type: ignore[arg-type]
            self._on_drone_deployed
        )
        self.splitter.addWidget(self.palette_panel)

        centre = QtWidgets.QWidget()
        centre.setMinimumWidth(50)
        c = QtWidgets.QVBoxLayout(centre)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(0)

        # Scene + view must exist before _build_toolbar() since the
        # toolbar wires the zoom-label up to ``self.view.zoom_changed``.
        self.scene = CanvasScene()
        self.view = _CanvasViewWithDrop(self.scene, self)

        c.addWidget(self._build_toolbar())

        # Draft-mode banner.  Hidden by default; visible when
        # ``_is_draft`` is on so the operator never confuses a planning
        # surface with a live one.  Says explicitly that the picker
        # behaviour mirrors the Chat tab — the canvas is the same
        # experience, just with Run gated.
        self.draft_banner = QtWidgets.QLabel(
            "📐  Draft canvas — planning surface.  Run is disabled.  "
            "Model / thinking / skills / repo binding all behave the same "
            "as the Chat tab; flip Draft off to dispatch."
        )
        self.draft_banner.setWordWrap(True)
        self.draft_banner.setStyleSheet(
            # Canonical amber from the rest of the codebase.
            "QLabel{background:#fff5e0;border:1px solid #f0c97a;"
            "color:#a96b00;padding:6px 10px;border-radius:4px;font-size:11px;}"
        )
        self.draft_banner.setVisible(False)
        c.addWidget(self.draft_banner)

        c.addWidget(self.view, stretch=1)

        # Minimap floats in the bottom-right corner of the centre
        # column.  Parented to the centre widget so it survives
        # resize and layout changes.
        self.minimap = Minimap(self.view, centre)
        # Position via raise + manual placement on resize.
        self._reposition_minimap()
        centre.installEventFilter(self)

        self.splitter.addWidget(centre)

        self.inspector = InspectorPanel()
        self.inspector.setMinimumWidth(180)
        self.splitter.addWidget(self.inspector)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        self.scene.selection_changed.connect(self.inspector.show_for)  # type: ignore[arg-type]
        self.inspector.flow_name_changed.connect(self._on_flow_name_changed)  # type: ignore[arg-type]
        self.inspector.run_requested.connect(self._on_run_clicked)  # type: ignore[arg-type]
        self.inspector.cancel_requested.connect(self._on_cancel_clicked)  # type: ignore[arg-type]
        self.inspector.delete_requested.connect(self._remove_item)  # type: ignore[arg-type]

        self.view.viewport().installEventFilter(self)

    def _restore_splitter_state(self) -> None:
        value = self._settings.value(self._settings_key)
        if value is not None:
            self.splitter.restoreState(value)

    def _save_splitter_state(self) -> None:
        self._settings.setValue(self._settings_key, self.splitter.saveState())

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._save_splitter_state()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        """Refresh the palette's drones list every time the operator
        navigates to the canvas tab.

        Without this, a drone deployed from the Drones tab wouldn't
        appear in the canvas Drones palette until the GUI was
        restarted.  Cheap (single drones.list RPC); fires only on tab
        switch.
        """
        super().showEvent(event)
        self._restore_splitter_state()
        if hasattr(self, "palette_panel"):
            asyncio.ensure_future(self.palette_panel._reload_all())

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("background:#fff;border-bottom:1px solid #e6e7eb;")
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(6)
        for label, slot in (
            ("New", self._new_flow),
            ("Open", self._open_flow),
            ("Save", self._save_flow),
        ):
            btn = QtWidgets.QPushButton(label)
            btn.clicked.connect(slot)  # type: ignore[arg-type]
            h.addWidget(btn)
        h.addSpacing(8)

        for label, slot, shortcut in (
            ("Undo", self.undo_stack.undo, "Ctrl+Z"),
            ("Redo", self.undo_stack.redo, "Ctrl+Shift+Z"),
            ("Auto layout", self._auto_layout, ""),
        ):
            btn = QtWidgets.QPushButton(label)
            if shortcut:
                btn.setToolTip(f"{label} ({shortcut})")
            btn.clicked.connect(slot)  # type: ignore[arg-type]
            h.addWidget(btn)
        h.addSpacing(8)

        # Draft toggle — Run is disabled while on.  Mirrors
        # Flow.is_draft on save/load; flipping it locally also
        # updates the canvas tint via the toolbar badge.
        self.draft_btn = QtWidgets.QPushButton("Draft")
        self.draft_btn.setCheckable(True)
        self.draft_btn.setToolTip(
            "When on, this canvas is a scratchpad.  You can plan "
            "freely but Run is disabled until you flip it off "
            "(promoting the flow to Live)."
        )
        self.draft_btn.setStyleSheet(
            "QPushButton{padding:4px 12px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;}"
            "QPushButton:checked{background:#a96b00;color:#fff;border-color:#a96b00;}"
        )
        self.draft_btn.toggled.connect(self._on_draft_toggled)  # type: ignore[arg-type]
        h.addWidget(self.draft_btn)
        h.addSpacing(8)

        # Wire keyboard shortcuts up front so they fire even when the
        # toolbar buttons aren't focused.
        for keyseq, slot in (
            ("Ctrl+Z", self.undo_stack.undo),
            ("Ctrl+Shift+Z", self.undo_stack.redo),
            ("Ctrl+Y", self.undo_stack.redo),
        ):
            sc = QtGui.QShortcut(QtGui.QKeySequence(keyseq), self)
            sc.activated.connect(slot)  # type: ignore[arg-type]

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.setStyleSheet(
            "QPushButton{padding:4px 14px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;}"
            "QPushButton:hover{background:#1860d6;}"
        )
        self.run_btn.clicked.connect(self._on_run_clicked)  # type: ignore[arg-type]
        h.addWidget(self.run_btn)

        h.addStretch(1)
        zoom_label = QtWidgets.QLabel("Zoom: 100%")
        zoom_label.setStyleSheet("color:#5b6068;font-size:11px;")
        self.view.zoom_changed.connect(  # type: ignore[arg-type]
            lambda z: zoom_label.setText(f"Zoom: {int(z * 100)}%")
        )
        h.addWidget(zoom_label)

        fit_btn = QtWidgets.QPushButton("Fit")
        fit_btn.setToolTip("Zoom to fit all nodes (F)")
        fit_btn.clicked.connect(self.view.fit_all)  # type: ignore[arg-type]
        h.addWidget(fit_btn)
        return bar

    # ------------------------------------------------------------------
    # Drop handling
    # ------------------------------------------------------------------

    def handle_drop(self, data: bytes, scene_pos: QtCore.QPointF) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            return
        kind = payload.get("kind")
        node: BaseNode | None = None
        if kind == "control":
            cls = _CONTROL_FACTORY.get(payload.get("control_kind", ""))
            if cls is None:
                return
            node = cls(_node_id())
        elif kind == "agent":
            node = AgentNode(_node_id(), payload.get("card", {}))
        elif kind == "drone_action":
            action = payload.get("action") or {}
            # If this exact drone is already on the canvas, don't
            # double-create — recentre the existing one instead.
            existing = next(
                (
                    n
                    for n in self.scene.nodes()
                    if isinstance(n, DroneActionNode) and n.action.get("id") == action.get("id")
                ),
                None,
            )
            if existing is not None:
                existing.setPos(scene_pos)
                return
            node = DroneActionNode(_node_id(), action)
        elif kind == "template_graph":
            asyncio.ensure_future(self._deploy_template_async(payload, scene_pos))
            return
        else:
            return

        if node:
            node.setPos(scene_pos)
            self._wire_node(node)
            # Add via undo stack so a misclick is one Ctrl+Z away.
            self.undo_stack.push(AddNodeCommand(self.scene, node))

    async def _deploy_template_async(
        self,
        payload: dict[str, Any],
        scene_pos: QtCore.QPointF,
    ) -> None:
        template_id = payload.get("template_id")
        if not template_id:
            return
        try:
            result = await self.client.call(
                "template_graphs.deploy",
                {
                    "template_id": template_id,
                    "template_version": payload.get("template_version"),
                    "drop_x": scene_pos.x(),
                    "drop_y": scene_pos.y(),
                    "group_label": payload.get("name"),
                },
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Template deployment failed", str(exc))
            return
        if result.get("errors"):
            QtWidgets.QMessageBox.warning(
                self,
                "Template deployment blocked",
                "\n".join(result.get("errors") or ["Template validation failed"]),
            )
            return
        await self._apply_template_deployment(result)

    async def _apply_template_deployment(self, result: dict[str, Any]) -> None:
        node_index: dict[str, BaseNode] = {}
        self.undo_stack.beginMacro("Deploy template")
        try:
            for node_data in result.get("nodes", []) or []:
                node = self._node_from_deployment(node_data)
                if node is None:
                    continue
                node.setPos(float(node_data.get("x", 0.0)), float(node_data.get("y", 0.0)))
                self._wire_node(node)
                self.undo_stack.push(AddNodeCommand(self.scene, node))
                node_index[node.node_id] = node

            for edge_data in result.get("edges", []) or []:
                src = node_index.get(edge_data.get("from_node"))
                dst = node_index.get(edge_data.get("to_node"))
                if src is None or dst is None:
                    continue
                src_port = next(
                    (p for p in src.output_ports if p.name == edge_data.get("from_port")),
                    src.output_ports[0] if src.output_ports else None,
                )
                dst_port = next(
                    (p for p in dst.input_ports if p.name == edge_data.get("to_port")),
                    dst.input_ports[0] if dst.input_ports else None,
                )
                if src_port and dst_port:
                    edge = Edge(
                        src_port,
                        dst_port,
                        label=edge_data.get("label", ""),
                        directional=bool(edge_data.get("directional", True)),
                    )
                    self.undo_stack.push(AddEdgeCommand(self.scene, edge))
        finally:
            self.undo_stack.endMacro()

    def _node_from_deployment(self, node_data: dict[str, Any]) -> BaseNode | None:
        kind = str(node_data.get("kind") or "")
        if kind == "agent":
            card = node_data.get("card") or {}
            return AgentNode(_node_id(), card)
        if kind == "control":
            control_kind = str(node_data.get("control_kind") or "")
            cls = _CONTROL_FACTORY.get(control_kind)
            if cls is None:
                return None
            node = cls(_node_id())
            if control_kind == "branch" and isinstance(node, BranchNode):
                node.pattern = str((node_data.get("params") or {}).get("pattern") or ".*")
            if control_kind == "staging_area" and isinstance(node, StagingAreaNode):
                params = node_data.get("params") or {}
                node.set_mode(str(params.get("mode") or "manual_release"))
                if params.get("threshold") is not None:
                    node.set_threshold(int(params.get("threshold") or 1))
                if params.get("timeout_seconds") is not None:
                    node.set_timeout_seconds(int(params.get("timeout_seconds")))
                node.summary_hint = str(params.get("summary_hint") or "")
                node.release_note = str(params.get("release_note") or "")
                node.sync_view()
            return node
        return None

    def _wire_node(self, node: BaseNode) -> None:
        for port in node.input_ports + node.output_ports:
            port.edge_drag_started.connect(self._begin_edge_drag)  # type: ignore[arg-type]
        node.geometry_changed.connect(  # type: ignore[arg-type]
            lambda nid=node.node_id: self._note_node_moved(nid)
        )
        # Bug 15: Ensure annotator proxies follow the node as it is dragged.
        node.geometry_changed.connect(self.view.sync_proxies)
        self.view.sync_proxies()
        # Drone-action nodes get a double-click hook that opens the
        # edit dialog for that specific deployed instance.
        if isinstance(node, DroneActionNode):
            node.double_clicked.connect(  # type: ignore[arg-type]
                lambda n=node: self._edit_drone_instance(n)
            )

    # ------------------------------------------------------------------
    # Per-drone instance editing
    # ------------------------------------------------------------------

    def _edit_drone_instance(self, node: DroneActionNode) -> None:
        asyncio.ensure_future(self._edit_drone_instance_async(node))

    async def _edit_drone_instance_async(self, node: DroneActionNode) -> None:
        try:
            workspaces = await self.client.call("workspaces.list", {})
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Cannot open edit dialog", str(e))
            return

        from apps.gui.windows.drones import _EditDroneDialog
        dlg = _EditDroneDialog(node.action, workspaces, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        params = dlg.params()
        params["id"] = node.action["id"]
        try:
            action = await self.client.call("drones.update", params)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Update failed", str(e))
            return

        # Refresh the node's internal action state and visual labels.
        self._refresh_drone_node(node, action)
        # Ensure the palette is also aware of the rename/change.
        asyncio.ensure_future(self.palette_panel.reload_drones())

    def _convert_drone_instance(self, node: DroneActionNode) -> None:
        asyncio.ensure_future(self._convert_drone_async(node))

    async def _convert_drone_async(self, node: DroneActionNode) -> None:
        from apps.gui.windows.drones import _ConvertDroneDialog

        dlg = _ConvertDroneDialog(parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        params = dlg.params()
        params["id"] = node.action["id"]
        try:
            action = await self.client.call("drones.update", params)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Conversion failed", str(e))
            return

        # Success: refresh the node and the palette.
        self._refresh_drone_node(node, action)
        asyncio.ensure_future(self.palette_panel.reload_drones())
        QtWidgets.QMessageBox.information(
            self, "Converted", f"Drone '{node._title}' is now an autonomous agent."
        )

    # ------------------------------------------------------------------
    # Per-drone chat dialog
    # ------------------------------------------------------------------

    def _on_drone_deployed(self, action: dict[str, Any]) -> None:
        """Fired by the palette right after a successful Deploy.

        Drops a DroneActionNode onto the canvas at the centre of the
        currently-visible viewport and auto-opens the chat dialog.
        Idempotent: if the drone is already on the canvas, recentre +
        focus rather than spawning a duplicate.
        """
        if not isinstance(action, dict) or not action.get("id"):
            return
        existing = next(
            (
                n
                for n in self.scene.nodes()
                if isinstance(n, DroneActionNode) and n.action.get("id") == action.get("id")
            ),
            None,
        )
        if existing is not None:
            existing.setSelected(True)
            self.view.centerOn(existing)
            self._open_chat_for(existing)
            return

        viewport = self.view.viewport()
        viewport_centre_view = QtCore.QPoint(viewport.width() // 2, viewport.height() // 2)
        scene_pos = self.view.mapToScene(viewport_centre_view)

        node = DroneActionNode(_node_id(), action)
        node.setPos(scene_pos)
        self._wire_node(node)
        self.undo_stack.push(AddNodeCommand(self.scene, node))
        node.setSelected(True)
        self._open_chat_for(node)

    def _open_chat_for(self, node: DroneActionNode) -> None:
        dlg = DroneActionChatDialog(self.client, node.action, parent=self)
        # When the operator sends a message, the action's transcript
        # on the service is updated; refresh the node so it shows the
        # latest reply next time the chat is opened.
        dlg.sent.connect(  # type: ignore[arg-type]
            lambda updated_action, n=node: self._refresh_drone_node(n, updated_action)
        )
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _refresh_drone_node(self, node: DroneActionNode, updated_action: dict[str, Any]) -> None:
        node.action = updated_action
        node.refresh_visuals()

    # ------------------------------------------------------------------
    # Visibility toggle — dims nodes outside the selected lineage
    # cluster so the cross-agent reach is readable at a glance.
    # ------------------------------------------------------------------

    def _on_draft_toggled(self, on: bool) -> None:
        self._is_draft = on
        # Run button is gated; the persistent banner makes the mode
        # state legible from across the room and reminds the operator
        # that the picker UX mirrors the Chat tab.
        if on:
            self.run_btn.setEnabled(False)
            self.run_btn.setToolTip("Draft mode — flip Draft off to enable Run.")
        else:
            self.run_btn.setEnabled(True)
            self.run_btn.setToolTip("")
        self.draft_banner.setVisible(on)

    def _note_node_moved(self, node_id: str) -> None:
        # We can't tell from a single ItemPositionHasChanged whether
        # this was a press, a drag, or a release — so we capture the
        # start-of-drag position lazily on the first move and let the
        # release handler push the command.  A noop if we're inside
        # an undo/redo (Qt blocks signals during undo so we don't
        # land here).
        node = next((n for n in self.scene.nodes() if n.node_id == node_id), None)
        if node is None:
            return
        if node_id not in self._drag_start:
            self._drag_start[node_id] = QtCore.QPointF(node.pos())

    # ------------------------------------------------------------------
    # Edge drawing
    # ------------------------------------------------------------------

    def _begin_edge_drag(self, port: Port) -> None:
        # Clean up any stray draft edge first.
        self._cancel_edge_drag()
        if port.direction == PortDirection.INPUT:
            return  # only drag from outputs to keep direction obvious
        self._draft_source = port
        self._draft_edge = DraftEdge(port)
        self.scene.addItem(self._draft_edge)
        self._draft_edge.update_to(port.scene_position())

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        # Reposition the minimap on parent resize so it always lives
        # in the bottom-right corner of the centre column.
        if (
            event.type() == QtCore.QEvent.Type.Resize
            and hasattr(self, "minimap")
            and watched is self.minimap.parentWidget()
        ):
            self._reposition_minimap()
        if watched is self.view.viewport() and self._draft_edge is not None:
            if event.type() == QtCore.QEvent.Type.MouseMove:
                mev = cast(QtGui.QMouseEvent, event)
                pos = self.view.mapToScene(mev.position().toPoint())
                self._draft_edge.update_to(pos)
                return True
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                mev = cast(QtGui.QMouseEvent, event)
                self._finish_edge_drag(self.view.mapToScene(mev.position().toPoint()))
                return True
        # Mouse-up on the view (without a draft edge) is the end of
        # a node drag — push the accumulated move command.
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
        # Drop the move-tracking entries that any in-flight node drag
        # accumulated; the release event also lands here so this is
        # the right moment to push the move command.
        self._flush_drag_moves()
        if (
            source is None
            or target_port is None
            or target_port.owner is source.owner  # no self-loops
        ):
            return
        edge = Edge(source, target_port, directional=True)
        self.undo_stack.push(AddEdgeCommand(self.scene, edge))

    def _cancel_edge_drag(self) -> None:
        if self._draft_edge is not None:
            self.scene.removeItem(self._draft_edge)
        self._draft_edge = None
        self._draft_source = None

    # ------------------------------------------------------------------
    # Item deletion
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Move tracking (pushes a single MoveNodeCommand per drag)
    # ------------------------------------------------------------------

    def _flush_drag_moves(self) -> None:
        """Convert the current drag's accumulated motion into Move
        commands.  ``QUndoStack.push()`` calls ``redo()`` on the
        command, which sets the position to ``new_pos`` — that's a
        no-op since the node is already there, so we only see one
        history entry per drag with no visual flicker.
        """
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

    # ------------------------------------------------------------------
    # Auto layout
    # ------------------------------------------------------------------

    def _auto_layout(self) -> None:
        nodes = self.scene.nodes()
        edges = self.scene.edges()
        if not nodes:
            return
        # Snapshot positions, run the layout, then push one Move
        # command per node that actually moved.  Wrapped in a macro
        # so a single undo reverts the whole thing.
        old_positions = {n.node_id: QtCore.QPointF(n.pos()) for n in nodes}

        from apps.gui.canvas.layout import LayoutCycleError, auto_layout
        try:
            auto_layout(nodes, edges)
        except LayoutCycleError as exc:
            # Bug 16: Surface validation error.
            QtWidgets.QMessageBox.warning(self, "Auto-layout Failed", str(exc))
            return

        moves: list[tuple[BaseNode, QtCore.QPointF, QtCore.QPointF]] = []
        for n in nodes:
            old = old_positions[n.node_id]
            new = QtCore.QPointF(n.pos())
            if (old - new).manhattanLength() >= 0.5:
                moves.append((n, old, new))
        if not moves:
            return
        self.undo_stack.beginMacro("Auto layout")
        for n, old, new in moves:
            self.undo_stack.push(MoveNodeCommand(n, old, new))
        self.undo_stack.endMacro()
        self.view.fit_all()

    # ------------------------------------------------------------------
    # Minimap positioning
    # ------------------------------------------------------------------

    def _reposition_minimap(self) -> None:
        if not hasattr(self, "minimap"):
            return
        parent = self.minimap.parentWidget()
        if parent is None:
            return
        margin = 12
        x = parent.width() - self.minimap.width() - margin
        y = parent.height() - self.minimap.height() - margin
        self.minimap.move(max(margin, x), max(margin, y))
        self.minimap.raise_()

    # ------------------------------------------------------------------
    # Save / Open / New
    # ------------------------------------------------------------------

    def _new_flow(self) -> None:
        self._stop_stream()
        for edge in list(self.scene.edges()):
            self.scene.remove_edge(edge)
        for node in list(self.scene.nodes()):
            self.scene.remove_node(node)
        self._flow_id = None
        self._flow_name = "Untitled flow"
        self.inspector.show_for([])
        self.view.sync_proxies()

    def _save_flow(self) -> None:
        asyncio.ensure_future(self._save_flow_async())

    async def _save_flow_async(self) -> None:
        payload = {
            "name": self._flow_name,
            "is_draft": self._is_draft,
            "nodes": [n.to_payload() for n in self.scene.nodes()],
            "edges": [
                {
                    "from_node": e.source.owner.node_id if e.source else "",
                    "from_port": e.source.name if e.source else "",
                    "to_node": e.target.owner.node_id if e.target else "",
                    "to_port": e.target.name if e.target else "",
                    "directional": bool(e.directional),
                }
                for e in self.scene.edges()
                if e.source and e.target
            ],
        }
        try:
            if self._flow_id is None:
                res = await self.client.call("flows.create", payload)
                self._flow_id = res.get("id")
            else:
                payload["id"] = self._flow_id
                await self.client.call("flows.update", payload)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Save failed", str(exc))
            return
        QtWidgets.QMessageBox.information(self, "Saved", f"Flow saved as '{self._flow_name}'.")

    def _open_flow(self) -> None:
        asyncio.ensure_future(self._open_flow_async())

    async def _open_flow_async(self) -> None:
        try:
            flows = await self.client.call("flows.list", {})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Open failed", str(exc))
            return
        if not flows:
            QtWidgets.QMessageBox.information(self, "Open", "No saved flows yet.")
            return
        dlg = QtWidgets.QInputDialog(self)
        dlg.setComboBoxItems([f"{f['name']}  —  {f['id'][:8]}" for f in flows])
        dlg.setLabelText("Pick a flow:")
        dlg.setComboBoxEditable(False)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        idx = (
            dlg.comboBoxItems().index(dlg.textValue())
            if dlg.textValue() in dlg.comboBoxItems()
            else 0
        )
        flow = flows[idx]
        self._load_flow(flow)

    def _load_flow(self, flow: dict[str, Any]) -> None:
        self._new_flow()
        self._flow_id = flow.get("id")
        self._flow_name = flow.get("name", "Untitled flow")
        # Flow.is_draft is on the top-level dict; toggle the toolbar
        # so Run is gated correctly.
        is_draft = bool(flow.get("is_draft", False))
        if hasattr(self, "draft_btn"):
            self.draft_btn.setChecked(is_draft)
        self._is_draft = is_draft
        node_index: dict[str, BaseNode] = {}
        for n in flow.get("nodes", []) or []:
            node_type = n.get("type")
            node_id = n.get("id") or _node_id()
            node: BaseNode | None = None
            if canonical_node_type(str(node_type or "")) == "reaper":
                card_id = n.get("card_id")
                card = n.get("card")
                if not isinstance(card, dict):
                    # We don't have a cards.get RPC; pull from the cards
                    # we already cached in the palette.
                    card = next(
                        (
                            c.data(QtCore.Qt.ItemDataRole.UserRole)["card"]
                            for i in range(self.palette_panel.cards_list.count())
                            if (c := self.palette_panel.cards_list.item(i)) is not None
                            and c.data(QtCore.Qt.ItemDataRole.UserRole)["card"].get("id") == card_id
                        ),
                        {"id": card_id, "name": "Missing card"},
                    )
                agent_node = AgentNode(node_id, card)
                agent_node.goal_override = (n.get("params") or {}).get("goal", "")
                node = agent_node
            elif canonical_node_type(str(node_type or "")) in _CONTROL_FACTORY and canonical_node_type(
                str(node_type or "")
            ) != "staging_area":
                node = _CONTROL_FACTORY[canonical_node_type(str(node_type or ""))](node_id)
                if canonical_node_type(str(node_type or "")) == "branch" and isinstance(node, BranchNode):
                    node.pattern = (n.get("params") or {}).get("pattern", ".*")
            elif canonical_node_type(str(node_type or "")) == "fpv_drone":
                action_id = n.get("action_id")
                # Pull from the palette's drones list if it's been
                # populated; the canvas opened before the list async
                # load completed will fall back to a stub.
                action = next(
                    (
                        c.data(QtCore.Qt.ItemDataRole.UserRole)["action"]
                        for i in range(self.palette_panel.drones_list.count())
                        if (c := self.palette_panel.drones_list.item(i)) is not None
                        and c.data(QtCore.Qt.ItemDataRole.UserRole)["action"].get("id") == action_id
                    ),
                    {"id": action_id, "blueprint_snapshot": {"name": "Missing drone"}},
                )
                node = DroneActionNode(node_id, action)
            elif canonical_node_type(str(node_type or "")) == "staging_area":
                node = StagingAreaNode(node_id, params=n.get("params") or {})
            else:
                continue

            if node:
                node.setPos(n.get("x", 0), n.get("y", 0))
                self._wire_node(node)
                self.scene.add_node(node)
                node_index[node_id] = node

        for e in flow.get("edges", []) or []:
            src_node = node_index.get(e.get("from_node", ""))
            dst_node = node_index.get(e.get("to_node", ""))
            if src_node is None or dst_node is None:
                continue
            src_port = next(
                (p for p in src_node.output_ports if p.name == e.get("from_port")),
                src_node.output_ports[0] if src_node.output_ports else None,
            )
            dst_port = next(
                (p for p in dst_node.input_ports if p.name == e.get("to_port")),
                dst_node.input_ports[0] if dst_node.input_ports else None,
            )
            if src_port and dst_port:
                edge = Edge(src_port, dst_port, directional=bool(e.get("directional", True)))
                self.scene.add_edge(edge)
        self.inspector.show_for([])
        self.view.fit_all()

    def _on_flow_name_changed(self, name: str) -> None:
        self._flow_name = name

    # ------------------------------------------------------------------
    # Run / SSE
    # ------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        asyncio.ensure_future(self._run_flow_async())

    def _on_cancel_clicked(self) -> None:
        if self._flow_run_id is None:
            return
        asyncio.ensure_future(self.client.call("flows.cancel", {"run_id": self._flow_run_id}))

    _flow_run_id: str | None = None

    async def _run_flow_async(self) -> None:
        # Save first if there's no flow_id yet so the executor has
        # something to look up.
        if self._flow_id is None:
            await self._save_flow_async()
            if self._flow_id is None:
                return
        try:
            res = await self.client.call("flows.dispatch", {"flow_id": self._flow_id})
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Run failed", str(exc))
            return
        run_id = res.get("run_id")
        if run_id is None:
            return
        self._flow_run_id = run_id
        # Reset all node statuses for the new run.
        for n in self.scene.nodes():
            n.set_status(NodeStatus.IDLE)
        self._start_stream(run_id)

    def _start_stream(self, run_id: str) -> None:
        self._stop_stream()
        self._stream_task = asyncio.ensure_future(self._consume_stream(run_id))

    def _stop_stream(self) -> None:
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
        self._stream_task = None

    async def _consume_stream(self, run_id: str) -> None:
        # We reuse the existing per-run SSE channel; flow events are
        # multiplexed onto it under their own kinds (flow.node.*).
        async for ev in self._sse.stream_run(run_id):
            kind = ev.get("kind", "")
            payload = ev.get("payload") or {}
            node_id = payload.get("node_id")
            if not node_id:
                continue
            node = next((n for n in self.scene.nodes() if n.node_id == node_id), None)
            if node is None:
                continue
            if kind == "flow.node.queued":
                node.set_status(NodeStatus.QUEUED)
            elif kind == "flow.node.started":
                node.set_status(NodeStatus.RUNNING)
            elif kind in ("flow.node.waiting", "flow.node.human_pending"):
                node.set_status(NodeStatus.WAITING)
                reason = payload.get("reason") or payload.get("preview") or ""
                if reason:
                    node.set_body(str(reason)[:200])
            elif kind == "flow.node.released":
                node.set_status(NodeStatus.RELEASED)
                reason = payload.get("reason") or payload.get("preview") or ""
                if reason:
                    node.set_body(str(reason)[:200])
            elif kind == "flow.node.blocked":
                node.set_status(NodeStatus.BLOCKED)
                reason = payload.get("reason") or payload.get("preview") or ""
                if reason:
                    node.set_body(str(reason)[:200])
            elif kind == "flow.node.timed_out":
                node.set_status(NodeStatus.TIMED_OUT)
                reason = payload.get("reason") or payload.get("preview") or ""
                if reason:
                    node.set_body(str(reason)[:200])
            elif kind == "flow.node.rejected":
                node.set_status(NodeStatus.REJECTED)
                reason = payload.get("reason") or payload.get("preview") or ""
                if reason:
                    node.set_body(str(reason)[:200])
            elif kind == "flow.node.token_delta":
                delta = payload.get("delta", "")
                if delta:
                    node.set_body(delta[-200:])
            elif kind == "flow.node.completed":
                if node.status() != NodeStatus.RELEASED:
                    node.set_status(NodeStatus.COMPLETED)
                preview = (payload.get("output") or "")[:200]
                if preview:
                    node.set_body(preview)
            elif kind == "flow.node.failed":
                node.set_status(NodeStatus.FAILED)
                err = payload.get("error") or "failed"
                node.set_body(err[:200])
            elif kind == "flow.node.skipped":
                node.set_status(NodeStatus.SKIPPED)


class NodeAnnotationProxy(QtWidgets.QLabel):
    """Hidden widget that overlays a canvas node to make it 'visible'
    to the annotator.  Passes all mouse events through to the
    viewport so canvas interaction remains unbroken.
    """

    def __init__(self, node: BaseNode, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.node = node
        self.setObjectName(node.node_id)
        # Bug 16: Removed self.setText() as it was causing UI overlap.
        # The annotator can use objectName or parent titles for context.
        self.setStyleSheet("background:transparent;")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        # Invisible target.
        pass

    # Forward all interaction to the viewport parent.
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        event.ignore()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        event.ignore()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        event.ignore()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        event.ignore()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        from apps.gui.canvas.nodes.drone_action import DroneActionNode

        if not isinstance(self.node, DroneActionNode):
            event.ignore()
            return

        action = self.node.action
        snapshot = action.get("blueprint_snapshot") or {}
        if snapshot.get("provider") != "browser":
            # Already autonomous.
            event.ignore()
            return

        menu = QtWidgets.QMenu(self)
        convert_act = menu.addAction("Convert to autonomous Agent...")

        # Find the page to call the conversion logic.
        # proxy -> viewport -> view -> layout -> page
        view = self.parent().parent()
        page = None
        if isinstance(view, _CanvasViewWithDrop):
            page = view._page

        if page:
            picked = menu.exec(event.globalPos())
            if picked == convert_act:
                page._convert_drone_instance(self.node)
        else:
            event.ignore()


class _CanvasViewWithDrop(CanvasView):
    """View subclass that accepts palette drops.

    Lives in this file rather than ``view.py`` because it needs a
    reference to the page (to forward ``handle_drop``); keeping it
    here avoids a circular import.

    Bug 15: Manages a set of ``NodeAnnotationProxy`` widgets that
    shadow the scene's ``BaseNode`` items, allowing the annotator
    overlay to 'see' and select individual cards.
    """

    def __init__(self, scene: QtWidgets.QGraphicsScene, page: CanvasPage) -> None:
        super().__init__(scene)
        self._page = page
        self._proxies: dict[str, NodeAnnotationProxy] = {}
        self.zoom_changed.connect(self.sync_proxies)

    def sync_proxies(self) -> None:
        """Update proxy geometries to match scene items."""
        scene = self.scene()
        if not scene or not isinstance(scene, CanvasScene):
            return

        nodes = scene.nodes()
        node_ids = {n.node_id for n in nodes}

        # 1. Purge orphaned proxies.
        for nid in list(self._proxies.keys()):
            if nid not in node_ids:
                proxy = self._proxies.pop(nid)
                proxy.deleteLater()

        # 2. Add or update proxies for current nodes.
        viewport = self.viewport()
        for node in nodes:
            if node.node_id not in self._proxies:
                proxy = NodeAnnotationProxy(node, viewport)
                proxy.show()
                self._proxies[node.node_id] = proxy

            p = self._proxies[node.node_id]
            # Map node bounding rect (in scene coords) to viewport pixel coords.
            scene_rect = node.sceneBoundingRect()
            view_rect = self.mapFromScene(scene_rect).boundingRect()

            if p.geometry() != view_rect:
                p.setGeometry(view_rect)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.sync_proxies()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_proxies()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(PALETTE_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(PALETTE_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        mime = event.mimeData()
        if not mime.hasFormat(PALETTE_MIME):
            super().dropEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        self._page.handle_drop(bytes(mime.data(PALETTE_MIME).data()), scene_pos)
        event.acceptProposedAction()
