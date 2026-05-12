"""Auto-layout — assign x, y to a graph of nodes for a clean look.

Algorithm: a small Sugiyama-style layered layout.

1. Topo-sort the graph.  Cycle = bail out (we don't draw cycles).
2. Assign each node a "rank" (= longest distance from any root in
   topo order).  Nodes with the same rank go in the same horizontal
   layer.
3. Within a layer, keep the order they appeared in topo so connected
   pairs stay near each other.
4. Place: x = rank * (NODE_WIDTH + GAP_X);  y = position-in-layer *
   (NODE_HEIGHT + GAP_Y), centered around the layer's height.

This is intentionally simple — no crossing minimisation, no compaction.
Good enough for the kind of graphs (tens of nodes, mostly shallow)
operators draw by hand.  A real Sugiyama via networkx + ELK is a
Phase-6 polish away.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.gui.canvas.edges import Edge
    from apps.gui.canvas.nodes.base import BaseNode


GAP_X = 80
GAP_Y = 30
NODE_W = 200
NODE_H = 110


class LayoutCycleError(Exception):
    """Raised when the graph contains a cycle and cannot be auto-laid out."""


def auto_layout(nodes: list[BaseNode], edges: list[Edge]) -> None:
    """Mutates each node's position in place.

    Raises LayoutCycleError if a cycle is detected.
    """
    if not nodes:
        return
    indegree: dict[str, int] = {n.node_id: 0 for n in nodes}
    out: dict[str, list[str]] = defaultdict(list)
    by_id: dict[str, BaseNode] = {n.node_id: n for n in nodes}
    for e in edges:
        if e.source is None or e.target is None:
            continue
        src = e.source.owner.node_id
        dst = e.target.owner.node_id
        if src in indegree and dst in indegree:
            out[src].append(dst)
            indegree[dst] += 1

    # Kahn's algorithm — orders nodes so all predecessors come first.
    ready = [nid for nid, d in indegree.items() if d == 0]
    order: list[str] = []
    indegree_local = dict(indegree)
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for nxt in out[nid]:
            indegree_local[nxt] -= 1
            if indegree_local[nxt] == 0:
                ready.append(nxt)
    if len(order) != len(nodes):
        # Bug 16: Surface cycle detection.
        raise LayoutCycleError("Cycle detected — auto-layout only supports acyclic graphs.")

    rank: dict[str, int] = {nid: 0 for nid in indegree}
    for nid in order:
        for nxt in out[nid]:
            if rank[nxt] < rank[nid] + 1:
                rank[nxt] = rank[nid] + 1

    # Group by rank, preserving topo order within a rank.
    layers: dict[int, list[str]] = defaultdict(list)
    for nid in order:
        layers[rank[nid]].append(nid)

    max_per_layer = max(len(layer) for layer in layers.values()) if layers else 1
    layer_height = max_per_layer * (NODE_H + GAP_Y)

    for r, layer in layers.items():
        x = r * (NODE_W + GAP_X)
        # Vertically centre the layer relative to the tallest column.
        layer_total = len(layer) * (NODE_H + GAP_Y) - GAP_Y
        y_offset = (layer_height - layer_total) / 2
        for i, nid in enumerate(layer):
            y = y_offset + i * (NODE_H + GAP_Y)
            by_id[nid].setPos(x, y)
