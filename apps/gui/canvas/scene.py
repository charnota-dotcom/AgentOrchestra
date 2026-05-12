"""Canvas scene — the model layer for the QGraphicsView.

Owns the grid background, selection behaviour, and a small registry
of the items currently on the canvas so we can serialize / deserialize
flows without walking the whole scene each time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.canvas.edges import Edge
    from apps.gui.canvas.nodes.base import BaseNode


GRID_SIZE = 20
GRID_MAJOR_EVERY = 5  # major line every Nth minor line


class CanvasScene(QtWidgets.QGraphicsScene):
    """Adds a grid + bookkeeping for nodes and edges.

    Why a custom scene rather than dropping rectangles on the default
    one: we want the grid to scale with the view (so it stays visible
    at all zoom levels), we want fast lookups of "all nodes" / "all
    edges" without traversing every QGraphicsItem, and we want to
    centralise selection-clearing semantics so click-on-empty-space
    works the way users expect.
    """

    selection_changed = QtCore.Signal(list)  # list[BaseNode | Edge]

    def __init__(self) -> None:
        super().__init__()
        # Effectively unbounded canvas — large enough to feel infinite
        # but bounded so the scrollbars and minimap have something
        # finite to work with.
        self.setSceneRect(QtCore.QRectF(-10_000, -10_000, 20_000, 20_000))
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#fafbfc")))
        self._nodes: list[BaseNode] = []
        self._edges: list[Edge] = []
        self.selectionChanged.connect(self._on_selection_changed)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    def drawBackground(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
    ) -> None:
        super().drawBackground(painter, rect)
        # Skip the grid entirely when we're zoomed way out — at very
        # small scales the lines turn into solid grey noise and cost
        # more than they're worth.
        lod = painter.worldTransform().m11()
        if lod < 0.2:
            return

        left = int(rect.left()) - (int(rect.left()) % GRID_SIZE)
        top = int(rect.top()) - (int(rect.top()) % GRID_SIZE)

        minor = QtGui.QPen(QtGui.QColor("#eef0f3"))
        minor.setCosmetic(True)
        major = QtGui.QPen(QtGui.QColor("#dadde2"))
        major.setCosmetic(True)

        # Draw vertical lines first, then horizontal — batching paint
        # state changes saves a measurable amount of paint time.
        x = left
        while x < rect.right():
            painter.setPen(major if (x // GRID_SIZE) % GRID_MAJOR_EVERY == 0 else minor)
            painter.drawLine(QtCore.QLineF(x, rect.top(), x, rect.bottom()))
            x += GRID_SIZE
        y = top
        while y < rect.bottom():
            painter.setPen(major if (y // GRID_SIZE) % GRID_MAJOR_EVERY == 0 else minor)
            painter.drawLine(QtCore.QLineF(rect.left(), y, rect.right(), y))
            y += GRID_SIZE

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def add_node(self, node: BaseNode) -> None:
        self.addItem(node)
        self._nodes.append(node)

    def remove_node(self, node: BaseNode) -> None:
        # Bug 3: Detach and remove any edges incident to this node.
        # Use a while loop or a clean filter to avoid index/iterator issues
        # during bulk deletions.
        to_remove = [e for e in self._edges if e.touches(node)]
        for edge in to_remove:
            self.remove_edge(edge)

        if node in self._nodes:
            self._nodes.remove(node)

        # Drop selection before removeItem() so the selectionChanged
        # signal fires against a still-valid wrapper rather than racing
        # the C++ deletion.
        if node.isSelected():
            node.setSelected(False)

        self.removeItem(node)

    def add_edge(self, edge: Edge) -> None:
        self.addItem(edge)
        self._edges.append(edge)

    def remove_edge(self, edge: Edge) -> None:
        if edge in self._edges:
            self._edges.remove(edge)
        edge.detach()
        if edge.scene() == self:
            self.removeItem(edge)

    def clear_draft_edges(self) -> None:
        """Bug 15: Proactively clear any abandoned DraftEdge items."""
        from apps.gui.canvas.edges import DraftEdge
        for item in self.items():
            if isinstance(item, DraftEdge):
                self.removeItem(item)

    def nodes(self) -> list[BaseNode]:
        return list(self._nodes)

    def edges(self) -> list[Edge]:
        return list(self._edges)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        from apps.gui.canvas.edges import Edge
        from apps.gui.canvas.nodes.base import BaseNode

        selected = [
            item
            for item in self.selectedItems()
            if isinstance(item, (BaseNode, Edge))
        ]
        self.selection_changed.emit(selected)
