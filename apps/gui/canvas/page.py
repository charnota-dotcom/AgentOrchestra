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
import secrets
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtGui, QtWidgets

from apps.gui.canvas.commands import (
    AddEdgeCommand,
    AddNodeCommand,
    MoveNodeCommand,
    RemoveEdgeCommand,
    RemoveNodeCommand,
)
from apps.gui.canvas.edges import DraftEdge, Edge
from apps.gui.canvas.inspector import InspectorPanel
from apps.gui.canvas.layout import auto_layout
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
from apps.gui.canvas.palette import PALETTE_MIME, PalettePanel
from apps.gui.canvas.ports import Port, PortDirection
from apps.gui.canvas.scene import CanvasScene
from apps.gui.canvas.view import CanvasView
from apps.gui.ipc.sse_client import SseClient

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _node_id() -> str:
    return secrets.token_hex(6)


_CONTROL_FACTORY = {
    "trigger": TriggerNode,
    "branch": BranchNode,
    "merge": MergeNode,
    "human": HumanNode,
    "output": OutputNode,
}


class CanvasPage(QtWidgets.QWidget):
    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._sse = SseClient(base_url=client.base_url, token=client.token)
        self._stream_task: asyncio.Task | None = None
        self._flow_id: str | None = None
        self._flow_name = "Untitled flow"
        self._draft_edge: DraftEdge | None = None
        self._draft_source: Port | None = None

        self.setStyleSheet("background:#fafbfc;")

        # Undo stack for Add/Remove/Move/Connect operations.  Bound
        # to Ctrl+Z / Ctrl+Shift+Z below.
        self.undo_stack = QtGui.QUndoStack(self)
        # Per-node "drag start" position so we can produce a single
        # MoveNodeCommand per drag rather than one per pixel of motion.
        self._drag_start: dict[str, QtCore.QPointF] = {}

        # Layout: left palette | centre canvas | right inspector
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.palette = PalettePanel(client)
        self.palette.setFixedWidth(220)
        root.addWidget(self.palette)

        centre = QtWidgets.QWidget()
        c = QtWidgets.QVBoxLayout(centre)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(0)

        # Scene + view must exist before _build_toolbar() since the
        # toolbar wires the zoom-label up to ``self.view.zoom_changed``.
        self.scene = CanvasScene()
        self.view = _CanvasViewWithDrop(self.scene, self)

        c.addWidget(self._build_toolbar())
        c.addWidget(self.view, stretch=1)

        # Minimap floats in the bottom-right corner of the centre
        # column.  Parented to the centre widget so it survives
        # resize and layout changes.
        self.minimap = Minimap(self.view, centre)
        # Position via raise + manual placement on resize.
        self._reposition_minimap()
        centre.installEventFilter(self)

        root.addWidget(centre, stretch=1)

        self.inspector = InspectorPanel()
        self.inspector.setFixedWidth(280)
        root.addWidget(self.inspector)

        self.scene.selection_changed.connect(self.inspector.show_for)  # type: ignore[arg-type]
        self.inspector.flow_name_changed.connect(self._on_flow_name_changed)  # type: ignore[arg-type]
        self.inspector.run_requested.connect(self._on_run_clicked)  # type: ignore[arg-type]
        self.inspector.cancel_requested.connect(self._on_cancel_clicked)  # type: ignore[arg-type]
        self.inspector.delete_requested.connect(self._delete_node)  # type: ignore[arg-type]

        self.view.installEventFilter(self)

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
        h.addSpacing(12)

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
        if kind == "control":
            cls = _CONTROL_FACTORY.get(payload.get("control_kind", ""))
            if cls is None:
                return
            node = cls(_node_id())
        elif kind == "agent":
            node = AgentNode(_node_id(), payload.get("card", {}))
        else:
            return
        node.setPos(scene_pos)
        self._wire_node(node)
        # Add via undo stack so a misclick is one Ctrl+Z away.
        self.undo_stack.push(AddNodeCommand(self.scene, node))

    def _wire_node(self, node: BaseNode) -> None:
        for port in node.input_ports + node.output_ports:
            port.edge_drag_started.connect(self._begin_edge_drag)  # type: ignore[arg-type]
        node.geometry_changed.connect(  # type: ignore[arg-type]
            lambda nid=node.node_id: self._note_node_moved(nid)
        )

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
        if watched is self.view and self._draft_edge is not None:
            if event.type() == QtCore.QEvent.Type.MouseMove:
                pos = self.view.mapToScene(event.position().toPoint())  # type: ignore[attr-defined]
                self._draft_edge.update_to(pos)
                return True
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                self._finish_edge_drag(
                    self.view.mapToScene(event.position().toPoint())  # type: ignore[attr-defined]
                )
                return True
        # Mouse-up on the view (without a draft edge) is the end of
        # a node drag — push the accumulated move command.
        if (
            watched is self.view
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
        edge = Edge(source, target_port)
        self.undo_stack.push(AddEdgeCommand(self.scene, edge))

    def _cancel_edge_drag(self) -> None:
        if self._draft_edge is not None:
            self.scene.removeItem(self._draft_edge)
        self._draft_edge = None
        self._draft_source = None

    # ------------------------------------------------------------------
    # Node deletion
    # ------------------------------------------------------------------

    def _delete_node(self, node: BaseNode) -> None:
        self.undo_stack.push(RemoveNodeCommand(self.scene, node))

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
        auto_layout(nodes, edges)
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

    def _save_flow(self) -> None:
        asyncio.ensure_future(self._save_flow_async())

    async def _save_flow_async(self) -> None:
        payload = {
            "name": self._flow_name,
            "nodes": [n.to_payload() for n in self.scene.nodes()],
            "edges": [
                {
                    "from_node": e.source.owner.node_id if e.source else "",
                    "from_port": e.source.name if e.source else "",
                    "to_node": e.target.owner.node_id if e.target else "",
                    "to_port": e.target.name if e.target else "",
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
        node_index: dict[str, BaseNode] = {}
        for n in flow.get("nodes", []) or []:
            node_type = n.get("type")
            node_id = n.get("id") or _node_id()
            if node_type == "agent":
                card_id = n.get("card_id")
                # We don't have a cards.get RPC; pull from the cards
                # we already cached in the palette.
                card = next(
                    (
                        c.data(QtCore.Qt.ItemDataRole.UserRole)["card"]
                        for c in [
                            self.palette.cards_list.item(i)
                            for i in range(self.palette.cards_list.count())
                        ]
                        if c is not None
                        and c.data(QtCore.Qt.ItemDataRole.UserRole)["card"].get("id") == card_id
                    ),
                    {"id": card_id, "name": "Missing card"},
                )
                node = AgentNode(node_id, card)
                node.goal_override = (n.get("params") or {}).get("goal", "")
            elif node_type in _CONTROL_FACTORY:
                node = _CONTROL_FACTORY[node_type](node_id)
                if node_type == "branch" and isinstance(node, BranchNode):
                    node.pattern = (n.get("params") or {}).get("pattern", ".*")
            else:
                continue
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
                self.scene.add_edge(Edge(src_port, dst_port))
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
            elif kind == "flow.node.token_delta":
                delta = payload.get("delta", "")
                if delta:
                    node.set_body(delta[-200:])
            elif kind == "flow.node.completed":
                node.set_status(NodeStatus.COMPLETED)
                preview = (payload.get("output") or "")[:200]
                if preview:
                    node.set_body(preview)
            elif kind == "flow.node.failed":
                node.set_status(NodeStatus.FAILED)
                err = payload.get("error") or "failed"
                node.set_body(err[:200])


class _CanvasViewWithDrop(CanvasView):
    """View subclass that accepts palette drops.

    Lives in this file rather than ``view.py`` because it needs a
    reference to the page (to forward ``handle_drop``); keeping it
    here avoids a circular import.
    """

    def __init__(self, scene: QtWidgets.QGraphicsScene, page: CanvasPage) -> None:
        super().__init__(scene)
        self._page = page

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
        self._page.handle_drop(mime.data(PALETTE_MIME).data(), scene_pos)
        event.acceptProposedAction()
