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

from apps.gui.canvas.chat_dialog import AgentChatDialog
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
from apps.gui.canvas.lineage_box import LineageBox
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
from apps.gui.canvas.nodes.conversation import ConversationNode
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
        # Lineage cluster boxes — one per parent that has at least
        # one descendant on the canvas.  Recomputed on every drop
        # via _refresh_lineage_boxes().
        self._lineage_boxes: list[LineageBox] = []
        # Draft mode flag mirrors Flow.is_draft.  Run is gated when
        # True; the toolbar shows a "Draft" badge.
        self._is_draft: bool = False

        # Layout: left palette | centre canvas | right inspector
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.palette = PalettePanel(client)
        self.palette.setFixedWidth(220)
        # When the operator clicks "+ New conversation" in the
        # palette and the agent is created server-side, the palette
        # emits this signal so we can drop a ConversationNode onto
        # the canvas and auto-open the chat dialog.  Without this the
        # operator stared at an empty canvas wondering where the new
        # agent went — annotation #6 from 2026-05-10.
        self.palette.conversation_created.connect(  # type: ignore[arg-type]
            self._on_conversation_created
        )
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

        root.addWidget(centre, stretch=1)

        self.inspector = InspectorPanel()
        self.inspector.setFixedWidth(280)
        root.addWidget(self.inspector)

        self.scene.selection_changed.connect(self.inspector.show_for)  # type: ignore[arg-type]
        # Visibility-mode highlight: redrives whenever selection
        # changes so the cluster updates as the operator clicks
        # different ConversationNodes.
        self.scene.selection_changed.connect(self._refresh_visibility_highlight)  # type: ignore[arg-type]
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

        # Visibility toggle.  When ON + a ConversationNode is
        # selected, dim every node that the selection can't see /
        # be seen by, leaving the lineage cluster fully visible so
        # the operator can read the cross-agent reach at a glance.
        self.visibility_btn = QtWidgets.QPushButton("Visibility")
        self.visibility_btn.setCheckable(True)
        self.visibility_btn.setToolTip(
            "When enabled, click a conversation to highlight everyone "
            "who can see (or be seen by) that agent's transcript.  "
            "Lineage = parents + descendants spawned via Spawn follow-up."
        )
        self.visibility_btn.setStyleSheet(
            "QPushButton{padding:4px 12px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;}"
            "QPushButton:checked{background:#1f7a3f;color:#fff;border-color:#1f7a3f;}"
        )
        self.visibility_btn.toggled.connect(self._on_visibility_toggled)  # type: ignore[arg-type]
        h.addWidget(self.visibility_btn)
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
        elif kind == "conversation":
            agent = payload.get("agent") or {}
            # If this exact agent is already on the canvas, don't
            # double-create — recentre the existing one instead.  Two
            # ConversationNodes wrapping the same agent would confuse
            # the lineage-edge drawing.
            existing = next(
                (
                    n
                    for n in self.scene.nodes()
                    if isinstance(n, ConversationNode) and n.agent.get("id") == agent.get("id")
                ),
                None,
            )
            if existing is not None:
                existing.setPos(scene_pos)
                return
            node = ConversationNode(_node_id(), agent)
        else:
            return
        node.setPos(scene_pos)
        self._wire_node(node)
        # Add via undo stack so a misclick is one Ctrl+Z away.
        self.undo_stack.push(AddNodeCommand(self.scene, node))
        # If we just dropped a conversation, draw any lineage edges
        # to / from it.
        if isinstance(node, ConversationNode):
            self._refresh_lineage_edges()
            self._refresh_lineage_boxes()

    def _wire_node(self, node: BaseNode) -> None:
        for port in node.input_ports + node.output_ports:
            port.edge_drag_started.connect(self._begin_edge_drag)  # type: ignore[arg-type]
        node.geometry_changed.connect(  # type: ignore[arg-type]
            lambda nid=node.node_id: self._note_node_moved(nid)
        )
        # Conversation nodes get a double-click hook that opens the
        # per-agent chat dialog.
        if isinstance(node, ConversationNode):
            node.double_clicked.connect(  # type: ignore[arg-type]
                lambda n=node: self._open_chat_for(n)
            )

    # ------------------------------------------------------------------
    # Per-agent chat dialog
    # ------------------------------------------------------------------

    def _on_conversation_created(self, agent: dict[str, Any]) -> None:
        """Fired by the palette right after a successful "+ New conversation".

        Drops a ConversationNode onto the canvas at the centre of the
        currently-visible viewport and auto-opens the chat dialog so
        the operator immediately sees their new agent — and is
        already in chat, not staring at an empty grid.

        Idempotent: if the agent is already on the canvas (the
        operator opened the New dialog twice for the same agent for
        whatever reason) we don't double-add.
        """
        if not isinstance(agent, dict) or not agent.get("id"):
            return
        # If a ConversationNode for this agent already exists, just
        # bring it to attention rather than spawning a duplicate.
        existing = next(
            (
                n
                for n in self.scene.nodes()
                if isinstance(n, ConversationNode) and n.agent.get("id") == agent.get("id")
            ),
            None,
        )
        if existing is not None:
            existing.setSelected(True)
            self.view.centerOn(existing)
            self._open_chat_for(existing)
            return

        # Centre of the current view in scene coordinates.
        viewport = self.view.viewport()
        viewport_centre_view = QtCore.QPoint(viewport.width() // 2, viewport.height() // 2)
        scene_pos = self.view.mapToScene(viewport_centre_view)

        node = ConversationNode(_node_id(), agent)
        node.setPos(scene_pos)
        self._wire_node(node)
        self.undo_stack.push(AddNodeCommand(self.scene, node))
        self._refresh_lineage_edges()
        self._refresh_lineage_boxes()
        node.setSelected(True)
        # Auto-open the chat dialog so the operator immediately sees
        # the agent they just created and can start talking to it.
        self._open_chat_for(node)

    def _open_chat_for(self, node: ConversationNode) -> None:
        dlg = AgentChatDialog(self.client, node.agent, parent=self)
        # When the operator sends a message, the agent's transcript on
        # the service is updated; refresh the node so it shows the
        # latest assistant turn next time the chat is opened.
        dlg.sent.connect(  # type: ignore[arg-type]
            lambda updated_agent, n=node: self._refresh_conversation_node(n, updated_agent)
        )
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _refresh_conversation_node(
        self, node: ConversationNode, updated_agent: dict[str, Any]
    ) -> None:
        node.agent = updated_agent
        transcript = updated_agent.get("transcript") or []
        last = next(
            (m.get("content", "") for m in reversed(transcript) if m.get("role") == "assistant"),
            "",
        )
        node._subtitle = f"{updated_agent.get('model', '?')} · {len(transcript)} turns"
        node.set_body((last or "(no replies yet — double-click to chat)").strip())

    # ------------------------------------------------------------------
    # Lineage edges
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Visibility toggle — dims nodes outside the selected lineage
    # cluster so the cross-agent reach is readable at a glance.
    # ------------------------------------------------------------------

    def _on_visibility_toggled(self, on: bool) -> None:
        if not on:
            self._clear_visibility_highlight()
            return
        # On enable, immediately drive the highlight off the current
        # selection (if any) so the operator sees the effect without
        # having to re-click.
        self._refresh_visibility_highlight(
            [n for n in self.scene.selectedItems() if isinstance(n, BaseNode)]
        )

    def _refresh_visibility_highlight(self, selected: list[BaseNode] | None = None) -> None:
        if not getattr(self, "visibility_btn", None) or not self.visibility_btn.isChecked():
            return
        if selected is None:
            selected = [n for n in self.scene.selectedItems() if isinstance(n, BaseNode)]
        # Find the first selected ConversationNode — visibility only
        # makes sense for those.  AgentNode (template) and control
        # nodes don't have a transcript.
        anchor: ConversationNode | None = next(
            (n for n in selected if isinstance(n, ConversationNode)), None
        )
        if anchor is None:
            self._clear_visibility_highlight()
            return
        cluster_ids = self._lineage_cluster(anchor)
        for node in self.scene.nodes():
            in_cluster = (
                isinstance(node, ConversationNode) and node.agent.get("id") in cluster_ids
            ) or node is anchor
            node.setOpacity(1.0 if in_cluster else 0.25)

    def _clear_visibility_highlight(self) -> None:
        for node in self.scene.nodes():
            node.setOpacity(1.0)

    def _lineage_cluster(self, anchor: ConversationNode) -> set[str]:
        """All agent ids in the lineage cluster of ``anchor``.

        A cluster = the anchor itself + every ancestor (transcript-
        readers) + every descendant (transcript-receivers).  Built
        from the agents currently on the canvas, so what the operator
        sees matches what's drawn.

        Visibility model:
        * Parent agents see nothing of their children's later turns.
        * Children see the snapshot of the parent's transcript at
          spawn time (we capture it into the seeded transcript).
        * Other agents see neither.

        Highlighting both directions is intentional — the operator
        wants to know who can see this conversation AND whose
        conversations this one can see.
        """
        anchor_id = anchor.agent.get("id")
        if not anchor_id:
            return set()
        by_id: dict[str, ConversationNode] = {}
        children: dict[str, list[str]] = {}  # parent_id → [child_id, ...]
        for node in self.scene.nodes():
            if not isinstance(node, ConversationNode):
                continue
            aid = node.agent.get("id")
            if not aid:
                continue
            by_id[aid] = node
            pid = node.agent.get("parent_id")
            if pid:
                children.setdefault(pid, []).append(aid)

        cluster: set[str] = {anchor_id}
        # Walk up the parent chain.
        current = anchor.agent.get("parent_id")
        while current and current in by_id and current not in cluster:
            cluster.add(current)
            current = by_id[current].agent.get("parent_id")
        # Walk down the descendant tree (BFS).
        frontier = [anchor_id]
        while frontier:
            nid = frontier.pop()
            for child in children.get(nid, []):
                if child in cluster:
                    continue
                cluster.add(child)
                frontier.append(child)
        return cluster

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

    def _refresh_lineage_boxes(self) -> None:
        """Draw a translucent box around each lineage cluster on the
        canvas.  Re-runs after any add / remove / load.  Operator
        can read at a glance which conversations form a family.
        """
        # Tear down existing boxes first.
        for box in self._lineage_boxes:
            box.detach()
            self.scene.removeItem(box)
        self._lineage_boxes.clear()

        convos = [n for n in self.scene.nodes() if isinstance(n, ConversationNode)]
        if not convos:
            return
        by_id: dict[str, ConversationNode] = {
            n.agent.get("id"): n for n in convos if n.agent.get("id")
        }
        # Group nodes by their root ancestor id.  Walk parent_id up
        # until we hit a node whose parent isn't on the canvas (or
        # whose parent_id is None).
        groups: dict[str, list[ConversationNode]] = {}
        for node in convos:
            cursor = node
            while True:
                parent_id = cursor.agent.get("parent_id")
                if parent_id and parent_id in by_id:
                    cursor = by_id[parent_id]
                else:
                    break
            root_id = cursor.agent.get("id")
            if root_id is None:
                continue
            groups.setdefault(root_id, []).append(node)

        for root_id, members in groups.items():
            # A "cluster" only makes sense if there's ≥ 2 nodes
            # (otherwise it's just a node with a wrapper around it).
            if len(members) < 2:
                continue
            root_node = by_id.get(root_id)
            label = root_node.agent.get("name", "?") if root_node else "?"
            box = LineageBox(label, members)
            self.scene.addItem(box)
            self._lineage_boxes.append(box)

    def _refresh_lineage_edges(self) -> None:
        """Auto-draw a directional, labelled edge between a parent
        ConversationNode and any spawned children also on the canvas.

        Only one edge per (parent, child) pair — re-runnable safely
        on every drop because we de-dupe by endpoint identity.
        """
        convos = [n for n in self.scene.nodes() if isinstance(n, ConversationNode)]
        if not convos:
            return
        by_agent_id: dict[str, ConversationNode] = {
            n.agent.get("id"): n for n in convos if n.agent.get("id")
        }
        existing_pairs: set[tuple[BaseNode, BaseNode]] = set()
        for e in self.scene.edges():
            if e.source is None or e.target is None:
                continue
            existing_pairs.add((e.source.owner, e.target.owner))

        from apps.gui.canvas.ports import Port, PortDirection  # local — avoid cycle

        for child in convos:
            parent_id = child.agent.get("parent_id")
            if not parent_id:
                continue
            parent = by_agent_id.get(parent_id)
            if parent is None or (parent, child) in existing_pairs:
                continue
            # ConversationNodes have no flow ports; mint hidden ports
            # just for the lineage edge so Edge's existing geometry
            # pipeline works.
            src_port = Port(parent, PortDirection.OUTPUT, name="lineage")
            src_port.setVisible(False)
            src_port.setPos(0, 0)  # owner-relative; positioned on the node centre
            src_port.setParentItem(parent)
            dst_port = Port(child, PortDirection.INPUT, name="lineage")
            dst_port.setVisible(False)
            dst_port.setPos(0, 0)
            dst_port.setParentItem(child)
            label = (child.agent.get("parent_preset") or "follow-up").replace("_", " ")
            edge = Edge(src_port, dst_port, label=label, directional=True)
            self.scene.add_edge(edge)

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
        # Refresh lineage so any LineageBox / lineage edge that
        # referenced the now-deleted node drops its stale reference.
        # Without this, the next geometry_changed signal calls
        # sceneBoundingRect() on a wrapped C++ object that has been
        # deleted and Qt raises RuntimeError.
        self._refresh_lineage_edges()
        self._refresh_lineage_boxes()
        # If the operator deleted the visibility-anchor node, clear the
        # dim so surviving nodes don't sit at 0.25 opacity until the
        # next selection change.
        self._clear_visibility_highlight()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            removed_any = False
            for item in list(self.scene.selectedItems()):
                if isinstance(item, BaseNode):
                    self.undo_stack.push(RemoveNodeCommand(self.scene, item))
                    removed_any = True
                elif isinstance(item, Edge):
                    self.undo_stack.push(RemoveEdgeCommand(self.scene, item))
                    removed_any = True
            if removed_any:
                self._refresh_lineage_edges()
                self._refresh_lineage_boxes()
                self._clear_visibility_highlight()
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
            "is_draft": self._is_draft,
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
            elif node_type == "conversation":
                agent_id = n.get("agent_id")
                # Pull from the palette's agents list if it's been
                # populated; the canvas opened before the agents-list
                # async load completed will fall back to a stub.
                agent = next(
                    (
                        c.data(QtCore.Qt.ItemDataRole.UserRole)["agent"]
                        for c in [
                            self.palette.agents_list.item(i)
                            for i in range(self.palette.agents_list.count())
                        ]
                        if c is not None
                        and c.data(QtCore.Qt.ItemDataRole.UserRole)["agent"].get("id") == agent_id
                    ),
                    {"id": agent_id, "name": "Missing agent"},
                )
                node = ConversationNode(node_id, agent)
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
        # Re-render lineage edges for any conversation nodes the
        # loaded flow brought back onto the canvas.
        self._refresh_lineage_edges()
        self._refresh_lineage_boxes()
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
