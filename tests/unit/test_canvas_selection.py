"""Regression tests for canvas item selection."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets
from PySide6.QtTest import QTest

from apps.gui.canvas.nodes.control import TriggerNode
from apps.gui.canvas.nodes.template_graph import TemplateGraphNode
from apps.gui.canvas.page import NodeAnnotationProxy, _CanvasViewWithDrop
from apps.gui.canvas.scene import CanvasScene


class _DummyPage:
    pass


def _app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_node_annotation_proxy_is_hit_testable() -> None:
    _app()
    node = TriggerNode("n1")
    parent = QtWidgets.QWidget()
    proxy = NodeAnnotationProxy(node, parent)

    assert proxy.testAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
    assert proxy.testAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
    assert proxy.accessibleName() == node.title()


def test_canvas_selection_survives_annotation_overlay() -> None:
    app = _app()
    scene = CanvasScene()
    view = _CanvasViewWithDrop(scene, _DummyPage())  # type: ignore[arg-type]
    view.resize(800, 600)

    node = TriggerNode("n1")
    node.setPos(200, 150)
    scene.add_node(node)
    view.centerOn(node)
    view.sync_proxies()
    view.show()
    app.processEvents()

    target = view.mapFromScene(node.sceneBoundingRect().center())
    QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        target,
    )
    app.processEvents()

    assert node.isSelected() is True
    assert scene.selectedItems() == [node]

    QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.QPoint(10, 10),
    )
    app.processEvents()

    assert scene.selectedItems() == []
    view.close()


def test_annotation_proxy_click_selects_card() -> None:
    app = _app()
    scene = CanvasScene()
    view = _CanvasViewWithDrop(scene, _DummyPage())  # type: ignore[arg-type]
    view.resize(800, 600)

    node = TriggerNode("n2")
    node.setPos(240, 160)
    scene.add_node(node)
    view.centerOn(node)
    view.sync_proxies()
    view.show()
    app.processEvents()

    proxy = view._proxies[node.node_id]
    QTest.mouseClick(
        proxy,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        proxy.rect().center(),
    )
    app.processEvents()

    assert node.isSelected() is True
    assert scene.selectedItems() == [node]
    view.close()


def test_template_graph_node_can_be_selected() -> None:
    app = _app()
    scene = CanvasScene()
    view = _CanvasViewWithDrop(scene, _DummyPage())  # type: ignore[arg-type]
    view.resize(800, 600)

    node = TemplateGraphNode(
        "t1",
        {
            "type": "agent_action",
            "title": "Template card",
            "summary": "Click me",
        },
    )
    node.setPos(220, 180)
    scene.add_node(node)
    view.centerOn(node)
    view.show()
    app.processEvents()

    target = view.mapFromScene(node.sceneBoundingRect().center())
    QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        target,
    )
    app.processEvents()

    assert node.isSelected() is True
    assert scene.selectedItems() == [node]
    view.close()
