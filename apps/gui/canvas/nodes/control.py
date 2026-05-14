"""Control nodes — Trigger / Branch / Merge / Human / Output.

These have no underlying card; the flow executor implements them
directly.  Visually they share the same shape as agent nodes but use
distinct header colours and short labels so the topology is readable
at a glance.
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtGui

from apps.gui.canvas.nodes.base import BaseNode
from apps.gui.canvas.ports import Port, PortDirection


class TriggerNode(BaseNode):
    """Manual start of a flow.  No input port; one output port."""

    HEADER_COLOUR = QtGui.QColor("#1f7a3f")

    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id=node_id,
            title="Trigger",
            subtitle="Start of flow (manual)",
            body="Click Run on the canvas to start.",
        )
        self.add_output_port(Port(self, PortDirection.OUTPUT, "start"))

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "trigger",
            "x": pos.x(),
            "y": pos.y(),
            "params": {},
        }


class BranchNode(BaseNode):
    """Route the upstream output to one of N downstream paths.

    V1 supports two predicate kinds:

    * ``regex`` — match the upstream text against ``params.pattern``
      and pick the ``true`` or ``false`` port.
    * ``llm`` — ask a tiny judge call (the existing claude-cli or
      gemini-cli) to label the input; ``params.labels`` is the list
      of allowed labels and each label gets its own output port.

    For V1 we ship the regex flavour with two outputs — branch labels
    are first-class so the flow JSON survives later predicate changes.
    """

    HEADER_COLOUR = QtGui.QColor("#a96b00")

    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id=node_id,
            title="Branch",
            subtitle="Route on regex match",
            body="If pattern matches → true port, else → false port.",
        )
        self.add_input_port(Port(self, PortDirection.INPUT, "in"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "true"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "false"))
        self.pattern: str = ".*"

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "branch",
            "x": pos.x(),
            "y": pos.y(),
            "params": {"kind": "regex", "pattern": self.pattern},
        }


class MergeNode(BaseNode):
    """Join N parallel branches into one downstream path."""

    HEADER_COLOUR = QtGui.QColor("#a87c1d")

    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id=node_id,
            title="Merge",
            subtitle="Concatenate inputs",
            body="Joins N upstream outputs into a single text blob.",
        )
        # Two visible input ports for the common case; the flow
        # executor accepts any number incident on the node id.
        self.add_input_port(Port(self, PortDirection.INPUT, "a"))
        self.add_input_port(Port(self, PortDirection.INPUT, "b"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "merge",
            "x": pos.x(),
            "y": pos.y(),
            "params": {"strategy": "concatenate"},
        }


class HumanNode(BaseNode):
    """Pause the flow until a human approves or rejects."""

    HEADER_COLOUR = QtGui.QColor("#b3261e")

    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id=node_id,
            title="Human",
            subtitle="Approval required",
            body="Run pauses here; click Approve / Reject in the GUI to continue.",
        )
        self.add_input_port(Port(self, PortDirection.INPUT, "in"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "approved"))

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "human",
            "x": pos.x(),
            "y": pos.y(),
            "params": {},
        }


class OutputNode(BaseNode):
    """Terminal sink — renders the upstream result."""

    HEADER_COLOUR = QtGui.QColor("#5b6068")

    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id=node_id,
            title="Output",
            subtitle="Final answer",
            body="(waiting for upstream)",
        )
        self.add_input_port(Port(self, PortDirection.INPUT, "in"))

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "output",
            "x": pos.x(),
            "y": pos.y(),
            "params": {},
        }


class IntegrationActionNode(BaseNode):
    """A machine-action node that calls a trusted external app/tool."""

    HEADER_COLOUR = QtGui.QColor("#0f766e")

    def __init__(
        self,
        node_id: str,
        *,
        title: str = "Machine action",
        subtitle: str | None = None,
        body: str = "",
        params: dict[str, Any] | None = None,
    ) -> None:
        params = dict(params or {})
        self.integration_kind = str(params.get("integration_kind") or "mcp_tool")
        self.target_app = str(params.get("target_app") or "")
        self.action_name = str(params.get("action_name") or "")
        self.server_id = str(params.get("server_id") or "")
        self.tool_name = str(params.get("tool_name") or "")
        self.arguments_text = str(params.get("arguments") or "")
        self.summary_hint = str(params.get("summary_hint") or "")
        self.body_text = str(params.get("body") or body or "")
        self.release_note = str(params.get("release_note") or "")

        super().__init__(
            node_id=node_id,
            title=title or "Machine action",
            subtitle=subtitle or self._build_subtitle(),
            body=self._build_body(self.body_text),
        )
        self.add_input_port(Port(self, PortDirection.INPUT, "in"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))
        self.set_footer(self._build_footer())

    def _build_subtitle(self) -> str:
        if self.summary_hint.strip():
            return self.summary_hint.strip()
        parts = [self.integration_kind.replace("_", " ")]
        if self.target_app:
            parts.append(self.target_app)
        if self.action_name:
            parts.append(self.action_name)
        return " | ".join(parts)

    def _build_body(self, fallback: str = "") -> str:
        if fallback.strip():
            return fallback.strip()
        return ""

    def _build_footer(self) -> str:
        parts = [part for part in (self.target_app, self.action_name) if part]
        if not parts and not self.integration_kind and not self.tool_name and not self.server_id:
            return ""
        footer_parts: list[str] = ["Configured action"]
        if parts:
            footer_parts.append(" · ".join(parts))
        if self.integration_kind:
            footer_parts.append(self.integration_kind.replace("_", " "))
        if self.server_id:
            footer_parts.append(f"server: {self.server_id}")
        if self.tool_name:
            footer_parts.append(f"tool: {self.tool_name}")
        return " · ".join(footer_parts)

    def sync_view(self) -> None:
        self._subtitle = self._build_subtitle()
        self.set_body(self._build_body(self._body))
        self.set_footer(self._build_footer())
        self.update()

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        params: dict[str, Any] = {
            "integration_kind": self.integration_kind,
            "target_app": self.target_app,
            "action_name": self.action_name,
            "server_id": self.server_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments_text,
            "summary_hint": self.summary_hint,
            "release_note": self.release_note,
        }
        return {
            "id": self.node_id,
            "type": "integration_action",
            "x": pos.x(),
            "y": pos.y(),
            "title": self._title,
            "subtitle": self._subtitle,
            "body": self._body,
            "footer": self._build_footer(),
            "params": params,
        }
