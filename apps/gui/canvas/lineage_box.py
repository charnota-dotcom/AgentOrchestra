"""Translucent rounded-rectangle box drawn around a lineage cluster.

When a parent ConversationNode plus one or more of its descendants
sit on the canvas, ``CanvasPage._refresh_lineage_boxes()`` adds one
``LineageBox`` per cluster.  The box recomputes its bounding rect on
every move via the cluster nodes' ``geometry_changed`` signal so
dragging members keeps the wrapper tight.

Cosmetic only — no interaction.  Lives at the lowest z-value so it
sits behind the nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from apps.gui.canvas.nodes.base import BaseNode


_PAD = 22  # px of slop around the bounding rect on every side


class LineageBox(QtWidgets.QGraphicsObject):
    def __init__(self, root_label: str, members: list[BaseNode]) -> None:
        super().__init__()
        self.root_label = root_label
        self._members = members
        self.setZValue(-1.0)  # behind nodes
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        for m in members:
            m.geometry_changed.connect(self._reposition)  # type: ignore[arg-type]
        self._cached_rect: QtCore.QRectF = QtCore.QRectF()
        self._reposition()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _reposition(self) -> None:
        bbox: QtCore.QRectF | None = None
        # A member's underlying C++ Qt object may have been deleted
        # between our subscribe-time and now; sceneBoundingRect() on
        # such an object raises RuntimeError("wrapped C/C++ object …
        # has been deleted").  Catch and skip so a deletion can never
        # take down the whole canvas.
        for m in self._members:
            try:
                if m.scene() is None:
                    continue
                r = m.sceneBoundingRect()
            except RuntimeError:
                continue
            bbox = r if bbox is None else bbox.united(r)
        if bbox is None:
            self._cached_rect = QtCore.QRectF()
        else:
            self._cached_rect = bbox.adjusted(-_PAD, -_PAD - 14, _PAD, _PAD)
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QtCore.QRectF:
        return (
            self._cached_rect.adjusted(-2, -2, 2, 2)
            if not self._cached_rect.isEmpty()
            else QtCore.QRectF()
        )

    def paint(
        self,
        painter: QtGui.QPainter,
        _option: QtWidgets.QStyleOptionGraphicsItem,
        _widget: QtWidgets.QWidget | None = None,
    ) -> None:
        if self._cached_rect.isEmpty():
            return
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtGui.QColor(31, 122, 63, 18))  # light green wash
        pen = QtGui.QPen(QtGui.QColor(31, 122, 63, 110), 1)
        pen.setCosmetic(True)
        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(self._cached_rect, 14, 14)
        # Title chip in the top-left of the box so the operator sees
        # which conversation the cluster belongs to.
        painter.setPen(QtGui.QPen(QtGui.QColor("#1f7a3f")))
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        text = f"⚯ {self.root_label}"
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()
        chip = QtCore.QRectF(
            self._cached_rect.left() + 8,
            self._cached_rect.top() + 4,
            text_w + 12,
            text_h + 4,
        )
        painter.setBrush(QtGui.QColor(255, 255, 255, 220))
        painter.drawRoundedRect(chip, 6, 6)
        painter.drawText(chip, int(QtCore.Qt.AlignmentFlag.AlignCenter), text)

    def detach(self) -> None:
        for m in self._members:
            try:
                m.geometry_changed.disconnect(self._reposition)  # type: ignore[arg-type]
            except (RuntimeError, TypeError):
                pass
        self._members = []
