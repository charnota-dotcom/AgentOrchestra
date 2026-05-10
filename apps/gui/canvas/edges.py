"""Edges — bezier curves connecting two ports.

A draft edge follows the cursor while the user is dragging from a
port; it commits into a "real" edge once dropped on a compatible
target port.

Edge style:

* Cubic bezier with horizontal control handles, so the curve flows
  cleanly left-to-right between an output port and an input port.
* Cosmetic 2 px stroke (zoom-independent thickness).
* Hover highlight; selectable so Delete can remove it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.canvas.nodes.base import BaseNode
    from apps.gui.canvas.ports import Port


_EDGE_PEN_NORMAL = QtGui.QColor("#5b6068")
_EDGE_PEN_HOVER = QtGui.QColor("#1f6feb")
_EDGE_PEN_SELECTED = QtGui.QColor("#1f6feb")


class Edge(QtWidgets.QGraphicsPathItem):
    def __init__(self, source: Port, target: Port) -> None:
        super().__init__()
        self.source: Port | None = source
        self.target: Port | None = target
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(0.5)
        self._hover = False
        self._sync_pen()
        # Repath when either endpoint moves.
        source.owner.geometry_changed.connect(self.update_path)  # type: ignore[arg-type]
        target.owner.geometry_changed.connect(self.update_path)  # type: ignore[arg-type]
        self.update_path()

    def detach(self) -> None:
        for endpoint_owner in (
            getattr(self.source, "owner", None),
            getattr(self.target, "owner", None),
        ):
            if endpoint_owner is None:
                continue
            try:
                endpoint_owner.geometry_changed.disconnect(self.update_path)  # type: ignore[arg-type]
            except (RuntimeError, TypeError):
                pass
        self.source = None
        self.target = None

    def touches(self, node: BaseNode) -> bool:
        return (
            self.source is not None
            and self.target is not None
            and (self.source.owner is node or self.target.owner is node)
        )

    def update_path(self) -> None:
        if self.source is None or self.target is None:
            return
        p1 = self.source.scene_position()
        p2 = self.target.scene_position()
        dx = abs(p2.x() - p1.x())
        # Pull the bezier handles outward proportional to the distance
        # between endpoints — straight handles look kinked when ports
        # are stacked vertically; long handles smooth them out.
        handle_len = max(60.0, dx * 0.5)
        c1 = QtCore.QPointF(p1.x() + handle_len, p1.y())
        c2 = QtCore.QPointF(p2.x() - handle_len, p2.y())
        path = QtGui.QPainterPath(p1)
        path.cubicTo(c1, c2, p2)
        self.setPath(path)

    def hoverEnterEvent(
        self,
        event: QtWidgets.QGraphicsSceneHoverEvent,
    ) -> None:
        self._hover = True
        self._sync_pen()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(
        self,
        event: QtWidgets.QGraphicsSceneHoverEvent,
    ) -> None:
        self._hover = False
        self._sync_pen()
        super().hoverLeaveEvent(event)

    def itemChange(
        self,
        change: QtWidgets.QGraphicsItem.GraphicsItemChange,
        value: object,
    ) -> object:
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._sync_pen()
        return super().itemChange(change, value)

    def _sync_pen(self) -> None:
        if self.isSelected():
            colour = _EDGE_PEN_SELECTED
            width = 2.5
        elif self._hover:
            colour = _EDGE_PEN_HOVER
            width = 2.0
        else:
            colour = _EDGE_PEN_NORMAL
            width = 1.5
        pen = QtGui.QPen(colour, width)
        pen.setCosmetic(True)
        self.setPen(pen)


class DraftEdge(QtWidgets.QGraphicsPathItem):
    """Edge being dragged — endpoint is the cursor, not a port.

    Dropped from the page when the drag ends.
    """

    def __init__(self, source: Port) -> None:
        super().__init__()
        self.source = source
        pen = QtGui.QPen(QtGui.QColor("#1f6feb"), 1.5, QtCore.Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(0.4)

    def update_to(self, scene_pos: QtCore.QPointF) -> None:
        p1 = self.source.scene_position()
        dx = abs(scene_pos.x() - p1.x())
        handle_len = max(60.0, dx * 0.5)
        c1 = QtCore.QPointF(p1.x() + handle_len, p1.y())
        c2 = QtCore.QPointF(scene_pos.x() - handle_len, scene_pos.y())
        path = QtGui.QPainterPath(p1)
        path.cubicTo(c1, c2, scene_pos)
        self.setPath(path)
