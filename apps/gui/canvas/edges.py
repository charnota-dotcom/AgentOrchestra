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

        # Bug 17: Repath timer to throttle updates during storms.
        self._repath_timer = QtCore.QTimer()
        self._repath_timer.setSingleShot(True)
        self._repath_timer.setInterval(0)
        self._repath_timer.timeout.connect(self.update_path)

        # Repath when either endpoint moves.
        source.owner.geometry_changed.connect(self._request_repath)
        target.owner.geometry_changed.connect(self._request_repath)

        # Bug 1: Proactively detach if either endpoint is destroyed.
        source.destroyed.connect(self._on_endpoint_destroyed)
        target.destroyed.connect(self._on_endpoint_destroyed)

        self.update_path()

    def _request_repath(self) -> None:
        """Throttle update_path calls."""
        if not self._repath_timer.isActive():
            self._repath_timer.start()

    def _on_endpoint_destroyed(self, obj: QtCore.QObject | None = None) -> None:
        """Handle unexpected deletion of a port item."""
        # If we're already being removed, this is redundant but safe.
        scene = self.scene()
        if scene:
            # We don't call scene.remove_edge because that calls detach()
            # which might try to access the already-deleting object.
            # Instead, we just remove ourselves from the scene.
            from apps.gui.canvas.scene import CanvasScene
            if isinstance(scene, CanvasScene):
                scene.remove_edge(self)
            else:
                scene.removeItem(self)

    def detach(self) -> None:
        """Disconnect signals and nullify references."""
        # Bug 2: Safe access during teardown.
        source_owner = getattr(self.source, "owner", None)
        target_owner = getattr(self.target, "owner", None)

        for endpoint_owner in (source_owner, target_owner):
            if endpoint_owner is None:
                continue
            try:
                endpoint_owner.geometry_changed.disconnect(self.update_path)
            except (RuntimeError, TypeError):
                # Bug 4: Log but don't crash.
                import logging
                logging.getLogger(__name__).debug(
                    "failed to disconnect geometry_changed for %s", endpoint_owner
                )

        # Disconnect destroyed signals too.
        for port in (self.source, self.target):
            if port is not None:
                try:
                    port.destroyed.disconnect(self._on_endpoint_destroyed)
                except (RuntimeError, TypeError):
                    pass

        self.source = None
        self.target = None

    def touches(self, node: BaseNode) -> bool:
        # Bug 20: Use node_id for comparison instead of identity (is).
        if self.source is None or self.target is None:
            return False
        return (
            self.source.owner.node_id == node.node_id
            or self.target.owner.node_id == node.node_id
        )

    def update_path(self) -> None:
        if self.source is None or self.target is None:
            return
        p1 = self.source.scene_position()
        p2 = self.target.scene_position()

        dx = p2.x() - p1.x()
        
        handle_len = max(60.0, min(300.0, abs(dx) * 0.5))

        path = QtGui.QPainterPath(p1)

        lod = 1.0
        scene = self.scene()
        if scene:
            views = scene.views()
            if views:
                lod = views[0].viewportTransform().m11()

        if lod < 0.2:
            path.lineTo(p2)
        else:
            c1 = QtCore.QPointF(p1.x() + handle_len, p1.y())
            c2 = QtCore.QPointF(p2.x() - handle_len, p2.y())
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
        # Bug 13: Rely on view's global antialiasing.
        # super().paint draws the path using the pen.
        super().paint(painter, option, widget)
        if self.source is None or self.target is None:
            return

        # Bug 10: Fade details at low LOD.
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        if lod < 0.4:
            return

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
        size = 30.0
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
        # Bug 14: Elevate Z-value above nodes (1.0), edges (0.5) and ports (2.0)
        self.setZValue(2.5)

    def update_to(self, scene_pos: QtCore.QPointF) -> None:
        p1 = self.source.scene_position()
        dx = scene_pos.x() - p1.x()
        # Bug 8: Clamp handle_len.
        handle_len = max(60.0, min(300.0, abs(dx) * 0.5))
        c1 = QtCore.QPointF(p1.x() + handle_len, p1.y())
        c2 = QtCore.QPointF(scene_pos.x() - handle_len, scene_pos.y())
        path = QtGui.QPainterPath(p1)
        path.cubicTo(c1, c2, scene_pos)
        self.setPath(path)
