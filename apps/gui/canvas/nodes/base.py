"""Base node — the rounded-rect graphics item every flow node extends.

LOD tiers (driven by ``levelOfDetailFromTransform``):

* > 0.6   full: header strip, title, subtitle, body preview, ports,
          status indicator
* 0.25-0.6 compact: header strip, title only
* < 0.25  dot: a single coloured circle, no text

Ports are drawn as small circles on the left (input) and right
(output) edges of the node.  They are children of the node so dragging
the node also drags the ports.

Selection is rendered as a 2 px blue outline around the rounded body.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.canvas.ports import Port


NODE_WIDTH = 200
NODE_HEIGHT = 110
HEADER_HEIGHT = 28
RADIUS = 8
PORT_RADIUS = 6


class NodeStatus(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_STATUS_COLOUR = {
    NodeStatus.IDLE: QtGui.QColor("#aab1bb"),
    NodeStatus.QUEUED: QtGui.QColor("#a96b00"),
    NodeStatus.RUNNING: QtGui.QColor("#1f6feb"),
    NodeStatus.COMPLETED: QtGui.QColor("#1f7a3f"),
    NodeStatus.FAILED: QtGui.QColor("#b3261e"),
    NodeStatus.SKIPPED: QtGui.QColor("#5b6068"),
}


class BaseNode(QtWidgets.QGraphicsObject):
    """Visual node on the canvas.

    Inherits ``QGraphicsObject`` (rather than ``QGraphicsItem``) so
    subclasses can declare Qt signals — useful for "node was
    double-clicked" / "status changed" without bolting on an event
    bus.
    """

    HEADER_COLOUR = QtGui.QColor("#3b4252")  # neutral default

    geometry_changed = QtCore.Signal()  # fired on move so edges can repath
    double_clicked = QtCore.Signal()

    def __init__(
        self,
        node_id: str,
        title: str,
        subtitle: str = "",
        body: str = "",
    ) -> None:
        super().__init__()
        self.node_id = node_id
        self._title = title
        self._subtitle = subtitle
        self._body = body
        self._status = NodeStatus.IDLE
        self.input_ports: list[Port] = []
        self.output_ports: list[Port] = []

        self.setFlags(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(1.0)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def boundingRect(self) -> QtCore.QRectF:
        # Add a few pixels of slop so the selection outline isn't
        # clipped at the very edge.
        return QtCore.QRectF(-3, -3, NODE_WIDTH + 6, NODE_HEIGHT + 6)

    def itemChange(
        self,
        change: QtWidgets.QGraphicsItem.GraphicsItemChange,
        value: object,
    ) -> object:
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.geometry_changed.emit()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(
        self,
        event: QtWidgets.QGraphicsSceneMouseEvent,
    ) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_status(self, status: NodeStatus) -> None:
        if status != self._status:
            self._status = status
            self.update()

    def status(self) -> NodeStatus:
        return self._status

    def set_body(self, text: str) -> None:
        self._body = text
        self.update()

    def title(self) -> str:
        return self._title

    def add_input_port(self, port: Port) -> None:
        self.input_ports.append(port)
        port.setParentItem(self)
        port.setPos(0, HEADER_HEIGHT + 12 + 18 * (len(self.input_ports) - 1))

    def add_output_port(self, port: Port) -> None:
        self.output_ports.append(port)
        port.setParentItem(self)
        port.setPos(NODE_WIDTH, HEADER_HEIGHT + 12 + 18 * (len(self.output_ports) - 1))

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        _widget: QtWidgets.QWidget | None = None,
    ) -> None:
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        if lod < 0.25:
            self._paint_dot(painter)
            return
        if lod < 0.6:
            self._paint_compact(painter)
            return
        self._paint_full(painter)

    def _paint_dot(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(_STATUS_COLOUR[self._status])
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(QtCore.QRectF(NODE_WIDTH / 2 - 12, NODE_HEIGHT / 2 - 12, 24, 24))

    def _paint_compact(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        body_rect = QtCore.QRectF(0, 0, NODE_WIDTH, HEADER_HEIGHT + 22)
        path = QtGui.QPainterPath()
        path.addRoundedRect(body_rect, RADIUS, RADIUS)
        painter.fillPath(path, QtGui.QColor("#ffffff"))
        # Header strip
        header_rect = QtCore.QRectF(0, 0, NODE_WIDTH, HEADER_HEIGHT)
        painter.fillRect(header_rect, self.HEADER_COLOUR)
        painter.setPen(QtGui.QColor("#ffffff"))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            header_rect.adjusted(10, 0, -10, 0),
            int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft),
            self._title,
        )
        # Status dot
        painter.setBrush(_STATUS_COLOUR[self._status])
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(QtCore.QRectF(NODE_WIDTH - 22, HEADER_HEIGHT / 2 - 6, 12, 12))
        self._paint_outline(painter, body_rect)

    def _paint_full(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        body_rect = QtCore.QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT)
        path = QtGui.QPainterPath()
        path.addRoundedRect(body_rect, RADIUS, RADIUS)
        painter.fillPath(path, QtGui.QColor("#ffffff"))

        # Header
        header_rect = QtCore.QRectF(0, 0, NODE_WIDTH, HEADER_HEIGHT)
        header_path = QtGui.QPainterPath()
        header_path.addRoundedRect(header_rect, RADIUS, RADIUS)
        # Square the bottom of the header so the rounded body shows
        # below it.
        header_path.addRect(QtCore.QRectF(0, HEADER_HEIGHT - RADIUS, NODE_WIDTH, RADIUS))
        painter.fillPath(header_path, self.HEADER_COLOUR)

        painter.setPen(QtGui.QColor("#ffffff"))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            header_rect.adjusted(10, 0, -28, 0),
            int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft),
            self._title,
        )

        # Status dot at top-right corner
        painter.setBrush(_STATUS_COLOUR[self._status])
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(QtCore.QRectF(NODE_WIDTH - 22, HEADER_HEIGHT / 2 - 6, 12, 12))

        # Subtitle
        if self._subtitle:
            painter.setPen(QtGui.QColor("#5b6068"))
            font.setPointSize(8)
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(
                QtCore.QRectF(10, HEADER_HEIGHT + 4, NODE_WIDTH - 20, 16),
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
                self._subtitle,
            )

        # Body preview
        if self._body:
            painter.setPen(QtGui.QColor("#0f1115"))
            font.setPointSize(8)
            painter.setFont(font)
            body_box = QtCore.QRectF(
                10, HEADER_HEIGHT + 22, NODE_WIDTH - 20, NODE_HEIGHT - HEADER_HEIGHT - 30
            )
            metrics = painter.fontMetrics()
            elided = metrics.elidedText(
                self._body,
                QtCore.Qt.TextElideMode.ElideRight,
                int(body_box.width() * 4),  # ~4 lines
            )
            painter.drawText(
                body_box,
                int(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
                | int(QtCore.Qt.TextFlag.TextWordWrap),
                elided,
            )

        self._paint_outline(painter, body_rect)

    def _paint_outline(self, painter: QtGui.QPainter, body_rect: QtCore.QRectF) -> None:
        if self.isSelected():
            pen = QtGui.QPen(QtGui.QColor("#1f6feb"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body_rect, RADIUS, RADIUS)
        elif self._status == NodeStatus.RUNNING:
            # Pulsing border when the node is currently executing.
            # Static blue is good enough for V1; an animation can be
            # bolted on later via a QVariantAnimation on a "pulse"
            # attribute.
            pen = QtGui.QPen(QtGui.QColor("#1f6feb"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body_rect, RADIUS, RADIUS)
        elif self._status == NodeStatus.FAILED:
            pen = QtGui.QPen(QtGui.QColor("#b3261e"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body_rect, RADIUS, RADIUS)
        else:
            pen = QtGui.QPen(QtGui.QColor("#d0d3d9"), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body_rect, RADIUS, RADIUS)
