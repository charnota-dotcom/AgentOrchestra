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
    def __init__(
        self,
        source: Port,
        target: Port,
        *,
        label: str = "",
        directional: bool = False,
    ) -> None:
        super().__init__()
        self.source: Port | None = source
        self.target: Port | None = target
        self.label = label
        self.directional = directional
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

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        super().paint(painter, option, widget)
        if self.source is None or self.target is None:
            return
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        # Arrowhead at the target end so directional edges read
        # as "from → to".  Only draw if we asked for it; flow-edge
        # connections leave directional=False to keep the existing
        # look.
        if self.directional:
            self._draw_arrowhead(painter)
        # Label at the path midpoint, centred over the curve so it
        # doesn't clip into either node.
        if self.label:
            self._draw_label(painter)

    def _draw_arrowhead(self, painter: QtGui.QPainter) -> None:
        if self.source is None or self.target is None:
            return
        p1 = self.source.scene_position()
        p2 = self.target.scene_position()
        # Approximate the tangent at the target by sampling the bezier
        # close to its end — gives a more accurate arrowhead angle than
        # using p1->p2 directly when the curve is sharply offset.
        path = self.path()
        if path.length() <= 0:
            return
        angle_pos = path.percentAtLength(max(0.0, path.length() - 1.0))
        anchor = path.pointAtPercent(min(1.0, angle_pos + 0.001))
        end = path.pointAtPercent(1.0)
        import math

        dx = end.x() - anchor.x()
        dy = end.y() - anchor.y()
        if dx == 0 and dy == 0:
            return
        angle = math.atan2(dy, dx)
        size = 9.0
        spread = math.radians(28)
        a1 = angle + math.pi - spread
        a2 = angle + math.pi + spread
        head = QtGui.QPolygonF(
            [
                end,
                QtCore.QPointF(end.x() + math.cos(a1) * size, end.y() + math.sin(a1) * size),
                QtCore.QPointF(end.x() + math.cos(a2) * size, end.y() + math.sin(a2) * size),
            ]
        )
        painter.setBrush(self.pen().color())
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawPolygon(head)
        # Suppress unused-variable warning in tools that miss the use.
        _ = p1
        _ = p2

    def _draw_label(self, painter: QtGui.QPainter) -> None:
        path = self.path()
        if path.length() <= 0:
            return
        mid = path.pointAtPercent(0.5)
        # Background pill so the text is readable over the
        # gridlines and any curve underneath.
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(self.label)
        text_h = metrics.height()
        pad_x = 6
        pad_y = 2
        rect = QtCore.QRectF(
            mid.x() - text_w / 2 - pad_x,
            mid.y() - text_h / 2 - pad_y,
            text_w + 2 * pad_x,
            text_h + 2 * pad_y,
        )
        painter.setBrush(QtGui.QColor("#fff"))
        pen = QtGui.QPen(self.pen().color(), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QtGui.QColor("#0f1115"))
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), self.label)


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
