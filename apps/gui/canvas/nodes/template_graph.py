"""Template-graph node used by the Template Builder page."""

from __future__ import annotations

from typing import Any

from PySide6 import QtGui

from apps.gui.canvas.nodes.base import BaseNode
from apps.gui.canvas.ports import Port, PortDirection

_HEADER_COLOUR = {
    "start": QtGui.QColor("#1f7a3f"),
    "trigger": QtGui.QColor("#1f7a3f"),
    "decision": QtGui.QColor("#a96b00"),
    "branch": QtGui.QColor("#a96b00"),
    "agent_action": QtGui.QColor("#7c3aed"),
    "command": QtGui.QColor("#8a5b00"),
    "documentation": QtGui.QColor("#5b6068"),
    "end": QtGui.QColor("#5b6068"),
    "output": QtGui.QColor("#5b6068"),
    "human": QtGui.QColor("#b3261e"),
    "merge": QtGui.QColor("#a87c1d"),
    "staging_area": QtGui.QColor("#8a5b00"),
}


def _preview_text(text: str, *, limit: int = 140) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class TemplateGraphNode(BaseNode):
    """Native-looking node for graph templates."""

    def __init__(self, node_id: str, node_data: dict[str, Any]) -> None:
        self.node_data = dict(node_data)
        self.template_type = str(node_data.get("type") or "documentation")
        title = str(node_data.get("title") or node_data.get("name") or "Template node")
        subtitle = str(node_data.get("subtitle") or self.template_type.replace("_", " ").title())
        
        # Initial summary generation if missing.
        if "summary" not in self.node_data:
            raw_body = str(
                node_data.get("body")
                or node_data.get("instruction")
                or node_data.get("command")
                or ""
            )
            self.node_data["summary"] = _preview_text(raw_body, limit=80)
            
        body = self.node_data["summary"]
        super().__init__(node_id=node_id, title=title, subtitle=subtitle, body=body)
        self.HEADER_COLOUR = _HEADER_COLOUR.get(self.template_type, QtGui.QColor("#3b4252"))
        self._configure_ports()

    def _configure_ports(self) -> None:
        node_type = self.template_type
        if node_type in {"start", "trigger"}:
            self.add_output_port(Port(self, PortDirection.OUTPUT, "start"))
        elif node_type in {"decision", "branch"}:
            self.add_input_port(Port(self, PortDirection.INPUT, "in"))
            self.add_output_port(Port(self, PortDirection.OUTPUT, "true"))
            self.add_output_port(Port(self, PortDirection.OUTPUT, "false"))
        elif node_type in {"agent_action", "command", "merge", "human", "output", "end", "staging_area"}:
            if node_type != "output" and node_type != "end":
                self.add_input_port(Port(self, PortDirection.INPUT, "in"))
            if node_type in {"agent_action", "command", "merge", "human", "staging_area"}:
                self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))
        # documentation-only nodes stay as notes with no ports.

    def refresh_visuals(self) -> None:
        self._title = str(self.node_data.get("title") or self._title)
        self._subtitle = str(
            self.node_data.get("subtitle") or self.template_type.replace("_", " ").title()
        )
        self._body = str(self.node_data.get("summary") or "")
        self.HEADER_COLOUR = _HEADER_COLOUR.get(self.template_type, QtGui.QColor("#3b4252"))
        self.update()

    def to_template_payload(self) -> dict[str, Any]:
        pos = self.pos()
        payload = {
            "id": self.node_id,
            "type": self.template_type,
            "title": self._title,
            "summary": self.node_data.get("summary", ""),
            "subtitle": self._subtitle,
            "body": self.node_data.get("body", ""),
            "x": pos.x(),
            "y": pos.y(),
            "params": dict(self.node_data.get("params") or {}),
            "agent_role": self.node_data.get("agent_role"),
            "instruction": self.node_data.get("instruction"),
            "command": self.node_data.get("command"),
            "card_mapping": self.node_data.get("card_mapping"),
        }
        # Keep the raw node data for forward compatibility.
        payload.update({k: v for k, v in self.node_data.items() if k not in payload})
        return payload
