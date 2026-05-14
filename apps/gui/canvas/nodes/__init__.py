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
    IntegrationActionNode,
    HumanNode,
    MergeNode,
    OutputNode,
    TriggerNode,
)
from apps.gui.canvas.nodes.drone_action import DroneActionNode
from apps.gui.canvas.nodes.staging_area import StagingAreaNode
from apps.gui.canvas.nodes.template_graph import TemplateGraphNode

ReaperNode = AgentNode
FPVDroneNode = DroneActionNode

__all__ = [
    "AgentNode",
    "FPVDroneNode",
    "BaseNode",
    "BranchNode",
    "DroneActionNode",
    "IntegrationActionNode",
    "HumanNode",
    "MergeNode",
    "NodeStatus",
    "OutputNode",
    "ReaperNode",
    "StagingAreaNode",
    "TemplateGraphNode",
    "TriggerNode",
]
