"""Minimap — small bottom-right overview of the canvas.

Re-uses the same QGraphicsScene as the main view (no separate model)
and just paints it at a tiny scale via QGraphicsView.fitInView.  The
viewport rect of the main view is overlaid as a draggable rectangle
so the operator can pan the big view by moving the rectangle on the
small one.

Why not a screenshot widget: a screenshot doesn't update live as
nodes move.  Sharing the scene is the standard QGraphicsView idiom
and is virtually free (Qt only paints the scene once and the views
each composite their own viewport on top).
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class Minimap(QtWidgets.QGraphicsView):
    """Compact overview of a parent CanvasView's scene."""

    def __init__(self, source_view: QtWidgets.QGraphicsView, parent: QtWidgets.QWidget) -> None:
        super().__init__(source_view.scene(), parent)
        self._source = source_view
        self.setFixedSize(200, 140)
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setInteractive(False)
        self.setStyleSheet(
            "QGraphicsView{background:#fff;border:1px solid #d0d3d9;border-radius:6px;}"
        )

        # Re-fit when the source view scrolls or zooms so the
        # minimap always shows the whole graph plus a bit of slop.
        source_view.zoom_changed.connect(self._refit)  # type: ignore[arg-type]
        source_view.horizontalScrollBar().valueChanged.connect(self._update_viewport_rect)  # type: ignore[arg-type]
        source_view.verticalScrollBar().valueChanged.connect(self._update_viewport_rect)  # type: ignore[arg-type]

        self._dragging = False
        QtCore.QTimer.singleShot(0, self._refit)

    def _refit(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        items_rect = scene.itemsBoundingRect()
        if items_rect.isEmpty():
            items_rect = QtCore.QRectF(-200, -150, 400, 300)
        self.fitInView(
            items_rect.adjusted(-40, -40, 40, 40), QtCore.Qt.AspectRatioMode.KeepAspectRatio
        )
        self._update_viewport_rect()

    def _update_viewport_rect(self) -> None:
        # Trigger a repaint — the rect itself is computed in
        # ``drawForeground`` so it stays in sync with the source
        # view's actual visible rectangle, even if we missed a
        # scroll event.
        self.viewport().update()

    def drawForeground(
        self,
        painter: QtGui.QPainter,
        _rect: QtCore.QRectF,
    ) -> None:
        rect = self._source_visible_scene_rect()
        if rect.isEmpty():
            return
        pen = QtGui.QPen(QtGui.QColor(31, 111, 235, 200), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor(31, 111, 235, 30))
        painter.drawRect(rect)

    def _source_visible_scene_rect(self) -> QtCore.QRectF:
        viewport = self._source.viewport().rect()
        top_left = self._source.mapToScene(viewport.topLeft())
        bottom_right = self._source.mapToScene(viewport.bottomRight())
        return QtCore.QRectF(top_left, bottom_right)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._dragging = True
            self._centre_source(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging:
            self._centre_source(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _centre_source(self, mini_pos: QtCore.QPoint) -> None:
        scene_pos = self.mapToScene(mini_pos)
        self._source.centerOn(scene_pos)
        self._update_viewport_rect()
