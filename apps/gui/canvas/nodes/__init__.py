"""Node types for the Flow Canvas.

The base class defines paint with three LOD tiers; subclasses
specialise the body drawing and provide ports.  See the FlowNode
type registry in ``apps/service/flows/types.py`` for the matching
backend representation.
"""

from apps.gui.canvas.nodes.agent import AgentNode
from apps.gui.canvas.nodes.base import BaseNode, NodeStatus
from apps.gui.canvas.nodes.control import (
    BranchNode,
    HumanNode,
    MergeNode,
    OutputNode,
    TriggerNode,
)
from apps.gui.canvas.nodes.conversation import ConversationNode

__all__ = [
    "AgentNode",
    "BaseNode",
    "BranchNode",
    "ConversationNode",
    "HumanNode",
    "MergeNode",
    "NodeStatus",
    "OutputNode",
    "TriggerNode",
]
