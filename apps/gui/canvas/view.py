"""Canvas view — pan, zoom, and viewport handling.

Pan modes:
* Middle-mouse-drag — always.
* Space + left-drag — for users without a middle button.
* Hand-cursor scroll-bar drag — Qt default.

Zoom: Ctrl + wheel, anchored to the cursor so zooming feels like the
viewport is anchored to where the operator is looking.

This file is intentionally free of node logic — anything that touches
``BaseNode`` lives in ``page.py`` (the orchestrator) so ``view.py``
stays a focused, reusable widget.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

_MIN_ZOOM = 0.1
_MAX_ZOOM = 4.0
_ZOOM_STEP = 1.15


class CanvasView(QtWidgets.QGraphicsView):
    zoom_changed = QtCore.Signal(float)

    def __init__(self, scene: QtWidgets.QGraphicsScene) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.SmoothPixmapTransform
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.NoAnchor)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Cosmetic: disable the dotted focus rect that flickers around
        # the viewport when items get focus.
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setAcceptDrops(True)

        self._space_held = False
        self._panning = False
        self._pan_anchor = QtCore.QPoint()

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if not (event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier):
            super().wheelEvent(event)
            return
        # Zoom anchored to cursor: translate so the scene point under
        # the cursor stays put across the scale change.  Map through
        # the inverted viewport transform so we keep sub-pixel accuracy
        # — toPoint() was discarding fractional pixels each tick, which
        # accumulated into visible drift away from the cursor.
        cursor_pos = event.position()
        old_pos = self._map_to_scene_f(cursor_pos)
        current = self._current_zoom()
        raw_factor = _ZOOM_STEP if event.angleDelta().y() > 0 else 1.0 / _ZOOM_STEP
        # Clamp the target zoom into the allowed range and derive the
        # actual factor from the clamped target.  The old "abort the
        # whole event if it overshoots" branch stranded users near the
        # edges (e.g. 3.9x could never reach exactly 4.0).
        target_zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, current * raw_factor))
        if target_zoom == current:
            return
        factor = target_zoom / current
        self.scale(factor, factor)
        new_pos = self._map_to_scene_f(cursor_pos)
        delta = new_pos - old_pos
        self.translate(delta.x(), delta.y())
        self.zoom_changed.emit(self._current_zoom())

    def _map_to_scene_f(self, view_point: QtCore.QPointF) -> QtCore.QPointF:
        """``mapToScene`` for a QPointF, preserving sub-pixel precision."""
        inverted, ok = self.viewportTransform().inverted()
        if not ok:
            return self.mapToScene(view_point.toPoint())
        return inverted.map(view_point)

    def _current_zoom(self) -> float:
        return float(self.transform().m11())

    def fit_all(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        items = scene.items()
        if not items:
            return
        rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self.fitInView(rect, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self._current_zoom())

    def reset_zoom(self) -> None:
        self.resetTransform()
        self.zoom_changed.emit(1.0)

    # ------------------------------------------------------------------
    # Pan
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.MiddleButton or (
            event.button() == QtCore.Qt.MouseButton.LeftButton and self._space_held
        ):
            self._panning = True
            self._pan_anchor = event.position().toPoint()
            self.viewport().setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning:
            delta = event.position().toPoint() - self._pan_anchor
            self._pan_anchor = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning and event.button() in (
            QtCore.Qt.MouseButton.MiddleButton,
            QtCore.Qt.MouseButton.LeftButton,
        ):
            self._panning = False
            self.viewport().setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        elif event.key() == QtCore.Qt.Key.Key_F:
            self.fit_all()
        elif event.key() == QtCore.Qt.Key.Key_0 and (
            event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
        ):
            self.reset_zoom()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning:
                self.viewport().setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:
        # Alt-Tab / window switch swallows the keyRelease, so the
        # spacebar pan latch would otherwise stay on after returning
        # and break ordinary left-click interactions.
        if self._space_held or self._panning:
            self._space_held = False
            self._panning = False
            self.viewport().setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        super().focusOutEvent(event)
