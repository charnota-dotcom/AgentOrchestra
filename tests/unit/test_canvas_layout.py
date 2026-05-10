"""Tests for the canvas auto-layout algorithm.

Doesn't import PySide6 — fakes the BaseNode / Edge interface the
layout function actually consumes (``node_id``, ``setPos``, plus
edge.source.owner.node_id and edge.target.owner.node_id).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _FakeNode:
    node_id: str
    x: float = 0.0
    y: float = 0.0

    def setPos(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def pos(self) -> _FakeNode:  # not used by the layout
        return self


@dataclass
class _FakePort:
    owner: _FakeNode


@dataclass
class _FakeEdge:
    source: _FakePort | None
    target: _FakePort | None


def _node(nid: str) -> _FakeNode:
    return _FakeNode(node_id=nid)


def _edge(src: _FakeNode, dst: _FakeNode) -> _FakeEdge:
    return _FakeEdge(source=_FakePort(src), target=_FakePort(dst))


def test_auto_layout_assigns_left_to_right_ranks() -> None:
    from apps.gui.canvas.layout import auto_layout

    a, b, c = _node("a"), _node("b"), _node("c")
    auto_layout([a, b, c], [_edge(a, b), _edge(b, c)])
    assert a.x < b.x < c.x


def test_auto_layout_groups_independent_into_same_layer() -> None:
    from apps.gui.canvas.layout import auto_layout

    src = _node("src")
    fan_a, fan_b = _node("fa"), _node("fb")
    sink = _node("sink")
    auto_layout(
        [src, fan_a, fan_b, sink],
        [_edge(src, fan_a), _edge(src, fan_b), _edge(fan_a, sink), _edge(fan_b, sink)],
    )
    assert fan_a.x == fan_b.x  # same rank → same column
    assert src.x < fan_a.x < sink.x


def test_auto_layout_noops_on_empty_graph() -> None:
    from apps.gui.canvas.layout import auto_layout

    auto_layout([], [])  # no exception


def test_auto_layout_noops_on_cycles() -> None:
    from apps.gui.canvas.layout import auto_layout

    a, b = _node("a"), _node("b")
    a.setPos(7, 8)
    b.setPos(9, 10)
    auto_layout([a, b], [_edge(a, b), _edge(b, a)])
    # Cycle → positions left untouched.
    assert (a.x, a.y) == (7, 8)
    assert (b.x, b.y) == (9, 10)


@pytest.mark.parametrize("count", [1, 5, 20])
def test_auto_layout_handles_various_sizes(count: int) -> None:
    from apps.gui.canvas.layout import auto_layout

    nodes = [_node(f"n{i}") for i in range(count)]
    edges = [_edge(nodes[i], nodes[i + 1]) for i in range(count - 1)]
    auto_layout(nodes, edges)
    # Every node should have a non-default position when count > 1.
    if count > 1:
        assert nodes[0].x != nodes[-1].x
