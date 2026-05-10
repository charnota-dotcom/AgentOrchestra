"""Agent node — wraps a PersonalityCard."""

from __future__ import annotations

from typing import Any

from PySide6 import QtGui

from apps.gui.canvas.nodes.base import BaseNode
from apps.gui.canvas.ports import Port, PortDirection

_PROVIDER_HEADER = {
    "claude-cli": QtGui.QColor("#7c3aed"),
    "anthropic": QtGui.QColor("#7c3aed"),
    "gemini-cli": QtGui.QColor("#1f6feb"),
    "google": QtGui.QColor("#1f6feb"),
    "openai": QtGui.QColor("#1f7a3f"),
    "ollama": QtGui.QColor("#5b6068"),
}


class AgentNode(BaseNode):
    """Visual node for an agent card.

    Inputs accept upstream output (one port for now; multi-input fan-in
    is handled by an explicit MergeNode).  Output is a single text
    payload — the agent's reply.
    """

    def __init__(self, node_id: str, card: dict[str, Any]) -> None:
        super().__init__(
            node_id=node_id,
            title=card.get("name", "Agent"),
            subtitle=f"{card.get('provider', '?')} · {card.get('model', '?')}",
            body=card.get("description", ""),
        )
        self.card = card
        self.HEADER_COLOUR = _PROVIDER_HEADER.get(card.get("provider", ""), QtGui.QColor("#3b4252"))
        self.add_input_port(Port(self, PortDirection.INPUT, "in"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))
        self.goal_override: str = ""

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "agent",
            "x": pos.x(),
            "y": pos.y(),
            "card_id": self.card.get("id"),
            "params": {"goal": self.goal_override} if self.goal_override else {},
        }
