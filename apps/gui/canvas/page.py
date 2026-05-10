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

from apps.gui.canvas.edges import DraftEdge, Edge
from apps.gui.canvas.inspector import InspectorPanel
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
        c.addWidget(self._build_toolbar())

        self.scene = CanvasScene()
        self.view = _CanvasViewWithDrop(self.scene, self)
        c.addWidget(self.view, stretch=1)
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
        h.addSpacing(12)

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
        self.scene.add_node(node)

    def _wire_node(self, node: BaseNode) -> None:
        for port in node.input_ports + node.output_ports:
            port.edge_drag_started.connect(self._begin_edge_drag)  # type: ignore[arg-type]

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
        return super().eventFilter(watched, event)

    def _finish_edge_drag(self, scene_pos: QtCore.QPointF) -> None:
        target_port: Port | None = None
        for item in self.scene.items(scene_pos):
            if isinstance(item, Port) and item.direction == PortDirection.INPUT:
                target_port = item
                break
        source = self._draft_source
        self._cancel_edge_drag()
        if (
            source is None
            or target_port is None
            or target_port.owner is source.owner  # no self-loops
        ):
            return
        edge = Edge(source, target_port)
        self.scene.add_edge(edge)

    def _cancel_edge_drag(self) -> None:
        if self._draft_edge is not None:
            self.scene.removeItem(self._draft_edge)
        self._draft_edge = None
        self._draft_source = None

    # ------------------------------------------------------------------
    # Node deletion
    # ------------------------------------------------------------------

    def _delete_node(self, node: BaseNode) -> None:
        self.scene.remove_node(node)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            for item in list(self.scene.selectedItems()):
                if isinstance(item, BaseNode):
                    self.scene.remove_node(item)
                elif isinstance(item, Edge):
                    self.scene.remove_edge(item)
        else:
            super().keyPressEvent(event)

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
