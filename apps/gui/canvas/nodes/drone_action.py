"""DroneActionNode — a deployed drone shown on the canvas.

Distinct from ``AgentNode``:

* AgentNode wraps a ``PersonalityCard`` template — a *prototype* used
  by the Flow executor to dispatch a fresh single-shot run.
* DroneActionNode wraps a ``DroneAction`` — a *deployed instance* of
  a blueprint with its own transcript.  Double-click to open a chat
  dialog scoped to that one action.

Inputs / outputs:

* No flow-execution ports — drones aren't dispatched by the Flow
  executor.  They're "anchored" entities that exist on the canvas to
  make the conversation lineage visible.
* Lineage edges (parent → child via ``additional_reference_action_ids``)
  are auto-drawn by ``page.py`` when both endpoints are on the canvas.
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtGui

from apps.gui.canvas.nodes.base import BaseNode
from apps.gui.canvas.ports import Port, PortDirection
from apps.gui.presets import model_label_for

_PROVIDER_HEADER = {
    "claude-cli": QtGui.QColor("#7c3aed"),
    "anthropic": QtGui.QColor("#7c3aed"),
    "gemini-cli": QtGui.QColor("#1f6feb"),
    "google": QtGui.QColor("#1f6feb"),
    "openai": QtGui.QColor("#1f7a3f"),
    "ollama": QtGui.QColor("#5b6068"),
}


class DroneActionNode(BaseNode):
    """A deployed drone (DroneAction) dropped onto the canvas."""

    def __init__(self, node_id: str, action: dict[str, Any]) -> None:
        snapshot = action.get("blueprint_snapshot") or {}
        transcript = action.get("transcript") or []
        last_assistant = next(
            (m.get("content", "") for m in reversed(transcript) if m.get("role") == "assistant"),
            "",
        )
        body = (last_assistant or "(no replies yet — double-click to chat)").strip()
        ws_name = action.get("workspace_name") or ""
        repo_marker = (
            f"  ·  📂 {ws_name}"
            if (action.get("workspace_id") and ws_name)
            else ("  ·  📂 repo" if action.get("workspace_id") else "")
        )
        provider = str(snapshot.get("provider", ""))
        raw_model = str(snapshot.get("model", "?"))
        friendly_model = model_label_for(provider, raw_model) if provider else raw_model
        role = snapshot.get("role", "worker")
        super().__init__(
            node_id=node_id,
            title=str(snapshot.get("name") or "Drone"),
            subtitle=f"{role} · {friendly_model} · {len(transcript)} turns{repo_marker}",
            body=body,
        )
        self.action = action
        self.HEADER_COLOUR = _PROVIDER_HEADER.get(provider, QtGui.QColor("#3b4252"))

        # Add ports for manual linking.
        self.add_input_port(Port(self, PortDirection.INPUT, "in"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))

        ws_path = action.get("workspace_path") or ""
        repo_line = (
            f"Repo:     {ws_name or 'bound'}{f' ({ws_path})' if ws_path else ''}\n"
            if action.get("workspace_id")
            else "Repo:     none — chat-only\n"
        )
        self.setToolTip(
            f"{snapshot.get('name', '?')}\n"
            f"Role:     {role}\n"
            f"Provider: {provider}\n"
            f"Model:    {friendly_model} ({raw_model})\n"
            f"Turns:    {len(transcript)}\n"
            f"{repo_line}"
            "\nDouble-click to open the chat window for this drone."
        )

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "drone_action",
            "x": pos.x(),
            "y": pos.y(),
            "action_id": self.action.get("id"),
            "params": {},
        }

    def refresh_visuals(self) -> None:
        """Update appearance based on current self.action state."""
        action = self.action
        snapshot = action.get("blueprint_snapshot") or {}
        transcript = action.get("transcript") or []
        provider = str(snapshot.get("provider", ""))
        raw_model = str(snapshot.get("model", "?"))
        friendly_model = model_label_for(provider, raw_model) if provider else raw_model
        role = snapshot.get("role", "worker")
        
        ws_name = action.get("workspace_name") or ""
        repo_marker = (
            f"  ·  📂 {ws_name}"
            if (action.get("workspace_id") and ws_name)
            else ("  ·  📂 repo" if action.get("workspace_id") else "")
        )

        self._title = str(action.get("name") or snapshot.get("name") or "Drone")
        self._subtitle = f"{role} · {friendly_model} · {len(transcript)} turns{repo_marker}"
        self.HEADER_COLOUR = _PROVIDER_HEADER.get(provider, QtGui.QColor("#3b4252"))

        ws_path = action.get("workspace_path") or ""
        repo_line = (
            f"Repo:     {ws_name or 'bound'}{f' ({ws_path})' if ws_path else ''}\n"
            if action.get("workspace_id")
            else "Repo:     none — chat-only\n"
        )
        self.setToolTip(
            f"{self._title}\n"
            f"Role:     {role}\n"
            f"Provider: {provider}\n"
            f"Model:    {friendly_model} ({raw_model})\n"
            f"Turns:    {len(transcript)}\n"
            f"{repo_line}"
            "\nDouble-click to open the chat window for this drone."
        )
        self.update()
