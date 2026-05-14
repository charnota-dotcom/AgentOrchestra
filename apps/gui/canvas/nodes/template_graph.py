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
    "integration_action": QtGui.QColor("#0f766e"),
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
    return text[: max(0, limit - 3)].rstrip() + "..."


class TemplateGraphNode(BaseNode):
    """Native-looking node for graph templates."""

    def __init__(self, node_id: str, node_data: dict[str, Any]) -> None:
        self.node_data = dict(node_data)
        self.template_type = str(node_data.get("type") or "documentation")
        title = str(node_data.get("title") or node_data.get("name") or "Template node")

        summary = str(self.node_data.get("summary") or "").strip()
        if not summary:
            raw_body = str(node_data.get("body") or node_data.get("instruction") or "")
            summary = _preview_text(raw_body, limit=80)
            self.node_data["summary"] = summary

        subtitle = str(summary or node_data.get("subtitle") or self._default_subtitle())
        body = str(node_data.get("body") or node_data.get("instruction") or "")

        super().__init__(node_id=node_id, title=title, subtitle=subtitle, body=body)
        self.HEADER_COLOUR = _HEADER_COLOUR.get(self.template_type, QtGui.QColor("#3b4252"))
        self._configure_ports()
        self.set_footer(self._execution_preview(self.node_data))

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
        elif node_type == "integration_action":
            self.add_input_port(Port(self, PortDirection.INPUT, "in"))
            self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))
        # documentation-only nodes stay as notes with no ports.

    @staticmethod
    def _execution_preview(node_data: dict[str, Any]) -> str:
        node_type = str(node_data.get("type") or "")
        if node_type == "command":
            details = " ".join(
                part
                for part in (
                    str(node_data.get("body") or "").strip(),
                    str(node_data.get("command") or "").strip(),
                )
                if part
            ).strip()
            if details:
                return f"Manual gate: {details}"
            return "Manual gate: does not execute app code."
        if node_type != "integration_action":
            return ""

        params = node_data.get("params") or {}
        target_app = str(params.get("target_app") or "").strip()
        action_name = str(params.get("action_name") or "").strip()
        tool_name = str(params.get("tool_name") or "").strip()
        server_id = str(params.get("server_id") or "").strip()
        kind = str(params.get("integration_kind") or "").strip()
        parts = [part for part in (target_app, action_name) if part]

        if kind == "passthrough":
            details: list[str] = []
            if parts:
                details.append(" | ".join(parts))
            if server_id:
                details.append(f"server: {server_id}")
            if tool_name:
                details.append(f"tool: {tool_name}")
            tail = "preview only; does not launch external app/tool code"
            if details:
                return f"Preview only: {' | '.join(details)} | {tail}"
            return f"Preview only: {tail}"

        if kind == "mcp_tool":
            details = ["Executes via MCP tool"]
            if parts:
                details.append(" | ".join(parts))
            if server_id:
                details.append(f"server: {server_id}")
            if tool_name:
                details.append(f"tool: {tool_name}")
            return " | ".join(details)

        if tool_name:
            parts.append(tool_name)
        if kind:
            parts.append(kind.replace("_", " "))
        preview = " | ".join(part for part in parts if part)
        if preview:
            return f"Configured action: {preview}"
        return ""

    def _default_subtitle(self) -> str:
        if self.template_type == "integration_action":
            params = self.node_data.get("params") or {}
            target_app = str(params.get("target_app") or "").strip()
            action_name = str(params.get("action_name") or "").strip()
            kind = str(params.get("integration_kind") or "").strip()
            parts = [part for part in (target_app, action_name) if part]
            if parts:
                if kind == "passthrough":
                    return "Preview only | " + " | ".join(parts)
                return " | ".join(parts)
            if kind == "passthrough":
                return "Preview only"
            if kind:
                return kind.replace("_", " ").title()
            return "Configured action"
        if self.template_type == "command":
            return "Legacy manual gate"
        return self.template_type.replace("_", " ").title()

    def refresh_visuals(self) -> None:
        self._title = str(self.node_data.get("title") or self._title)
        self._subtitle = str(self.node_data.get("summary") or self.node_data.get("subtitle") or self._default_subtitle())
        summary = str(self.node_data.get("summary") or "").strip()
        if summary:
            self.node_data["summary"] = summary
        else:
            raw_body = str(self.node_data.get("body") or self.node_data.get("instruction") or "")
            preview = _preview_text(raw_body, limit=80)
            self.node_data["summary"] = preview
        body = str(self.node_data.get("body") or self.node_data.get("instruction") or "")
        self._body = body
        self.set_footer(self._execution_preview(self.node_data))
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
            "footer": self._execution_preview(self.node_data),
            "x": pos.x(),
            "y": pos.y(),
            "params": dict(self.node_data.get("params") or {}),
            "agent_role": self.node_data.get("agent_role"),
            "instruction": self.node_data.get("instruction"),
            "command": self.node_data.get("command"),
            "card_mapping": self.node_data.get("card_mapping"),
        }
        payload.update({k: v for k, v in self.node_data.items() if k not in payload})
        return payload
