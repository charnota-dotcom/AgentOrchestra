"""QUndoCommand subclasses for the Canvas.

Five operations are reversible:

* AddNodeCommand     — drop a node from the palette
* RemoveNodeCommand  — Delete on a selected node (with edges)
* MoveNodeCommand    — drag-end position change (mergeable so a
                       single drag is one undo step)
* AddEdgeCommand     — finish an edge drag
* RemoveEdgeCommand  — Delete on a selected edge

Each command stores the bare minimum to re-create state.  The page
holds the QUndoStack and pushes commands on the relevant events.

We keep references to BaseNode / Edge instances rather than serialising
to JSON because a) the scene needs the exact object back to preserve
identity for any open Inspector binding, b) the commands' lifetime is
the same as the scene's so dangling references aren't a real concern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui

if TYPE_CHECKING:
    from apps.gui.canvas.edges import Edge
    from apps.gui.canvas.nodes.base import BaseNode
    from apps.gui.canvas.scene import CanvasScene


_MOVE_CMD_ID = 1


class AddNodeCommand(QtGui.QUndoCommand):
    def __init__(self, scene: CanvasScene, node: BaseNode) -> None:
        super().__init__(f"Add {node.title()}")
        self._scene = scene
        self._node = node

    def redo(self) -> None:
        self._scene.add_node(self._node)

    def undo(self) -> None:
        self._scene.remove_node(self._node)


class RemoveNodeCommand(QtGui.QUndoCommand):
    def __init__(self, scene: CanvasScene, node: BaseNode) -> None:
        super().__init__(f"Remove {node.title()}")
        self._scene = scene
        self._node = node
        # Snapshot incident edges so we can restore them on undo.
        self._incident: list[Edge] = [e for e in scene.edges() if e.touches(node)]

    def redo(self) -> None:
        self._scene.remove_node(self._node)

    def undo(self) -> None:
        self._scene.add_node(self._node)
        for e in self._incident:
            # The original edge object was detached + removed when
            # the node went; we re-add the same object back which
            # re-wires its geometry-changed signal connections.
            self._scene.add_edge(e)
            e.update_path()


class MoveNodeCommand(QtGui.QUndoCommand):
    """Mergeable per-node move command.

    Successive drags of the same node within one selection collapse
    into a single undo step via mergeWith — we update ``new_pos`` on
    the existing command rather than pushing a fresh one.
    """

    def __init__(
        self,
        node: BaseNode,
        old_pos: QtCore.QPointF,
        new_pos: QtCore.QPointF,
    ) -> None:
        super().__init__(f"Move {node.title()}")
        self._node = node
        self._old = QtCore.QPointF(old_pos)
        self._new = QtCore.QPointF(new_pos)

    def redo(self) -> None:
        self._node.setPos(self._new)

    def undo(self) -> None:
        self._node.setPos(self._old)

    def id(self) -> int:
        return _MOVE_CMD_ID

    def mergeWith(self, other: QtGui.QUndoCommand) -> bool:
        if not isinstance(other, MoveNodeCommand):
            return False
        if other._node is not self._node:
            return False
        self._new = other._new
        return True


class AddEdgeCommand(QtGui.QUndoCommand):
    def __init__(self, scene: CanvasScene, edge: Edge) -> None:
        super().__init__("Connect")
        self._scene = scene
        self._edge = edge

    def redo(self) -> None:
        self._scene.add_edge(self._edge)
        self._edge.update_path()

    def undo(self) -> None:
        self._scene.remove_edge(self._edge)


class RemoveEdgeCommand(QtGui.QUndoCommand):
    def __init__(self, scene: CanvasScene, edge: Edge) -> None:
        super().__init__("Disconnect")
        self._scene = scene
        self._edge = edge

    def redo(self) -> None:
        self._scene.remove_edge(self._edge)

    def undo(self) -> None:
        self._scene.add_edge(self._edge)
        self._edge.update_path()
