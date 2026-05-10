"""Ports — input/output docking points on a node.

A port is a small circle on the left or right edge of a node.
Clicking-and-dragging from a port creates a new edge whose other end
follows the cursor; releasing on another (compatible) port commits the
edge.

Ports are children of their owning node so dragging the node also
drags the ports — Qt's parent-child item hierarchy handles the
transform composition for free.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.canvas.nodes.base import BaseNode


PORT_RADIUS = 6


class PortDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class Port(QtWidgets.QGraphicsObject):
    """One docking point on a node.

    Emits ``edge_drag_started`` when the user begins dragging out a new
    edge; the canvas page listens and creates a draft edge that follows
    the cursor.  ``edge_drop_target`` fires when another port's drag
    ends over this port — used to commit the new edge.
    """

    edge_drag_started = QtCore.Signal(object)  # Port
    edge_drag_finished_on = QtCore.Signal(object, object)  # source Port, target Port

    def __init__(
        self,
        owner: BaseNode,
        direction: PortDirection,
        name: str = "",
    ) -> None:
        super().__init__()
        self.owner = owner
        self.direction = direction
        self.name = name
        self.setAcceptHoverEvents(True)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, False)
        self.setZValue(2.0)
        self._hover = False

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2)

    def paint(
        self,
        painter: QtGui.QPainter,
        _option: QtWidgets.QStyleOptionGraphicsItem,
        _widget: QtWidgets.QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        colour = QtGui.QColor("#1f6feb") if self._hover else QtGui.QColor("#5b6068")
        painter.setBrush(colour)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.5))
        painter.drawEllipse(
            QtCore.QRectF(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2)
        )

    def hoverEnterEvent(
        self,
        event: QtWidgets.QGraphicsSceneHoverEvent,
    ) -> None:
        self._hover = True
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(
        self,
        event: QtWidgets.QGraphicsSceneHoverEvent,
    ) -> None:
        self._hover = False
        self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(
        self,
        event: QtWidgets.QGraphicsSceneMouseEvent,
    ) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.edge_drag_started.emit(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def scene_position(self) -> QtCore.QPointF:
        return self.mapToScene(QtCore.QPointF(0, 0))
