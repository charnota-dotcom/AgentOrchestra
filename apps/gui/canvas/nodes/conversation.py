"""ConversationNode — a persistent named agent (Agents tab) shown on the canvas.

Distinct from ``AgentNode``:

* AgentNode wraps a ``PersonalityCard`` template — a *prototype* used
  by the Flow executor to dispatch a fresh single-shot run.
* ConversationNode wraps an ``Agent`` — a *named, persistent
  conversation* with its own transcript.  Double-clicking it opens
  a small chat dialog so you can continue talking to that specific
  agent from the canvas.

Inputs / outputs:

* No flow-execution ports — these aren't dispatched by the Flow
  executor.  They're "anchored" entities that exist on the canvas to
  make the conversation lineage visible.
* Lineage edges (parent → child via ``parent_id``) are auto-drawn by
  ``page.py`` when both endpoints are on the canvas.
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtGui

from apps.gui.canvas.nodes.base import BaseNode

_PROVIDER_HEADER = {
    "claude-cli": QtGui.QColor("#7c3aed"),
    "anthropic": QtGui.QColor("#7c3aed"),
    "gemini-cli": QtGui.QColor("#1f6feb"),
    "google": QtGui.QColor("#1f6feb"),
    "openai": QtGui.QColor("#1f7a3f"),
    "ollama": QtGui.QColor("#5b6068"),
}


class ConversationNode(BaseNode):
    """A persistent named agent dropped onto the canvas."""

    def __init__(self, node_id: str, agent: dict[str, Any]) -> None:
        # Body shows the most recent assistant turn (or a hint if
        # nothing's been said yet) so the canvas reads at a glance.
        transcript = agent.get("transcript") or []
        last_assistant = next(
            (m.get("content", "") for m in reversed(transcript) if m.get("role") == "assistant"),
            "",
        )
        body = (last_assistant or "(no replies yet — double-click to chat)").strip()
        # Repo binding shows up in the subtitle so an operator can
        # tell at a glance which agents have file-tool access.
        ws_name = agent.get("workspace_name") or ""
        repo_marker = (
            f"  ·  📂 {ws_name}"
            if (agent.get("workspace_id") and ws_name)
            else ("  ·  📂 repo" if agent.get("workspace_id") else "")
        )
        super().__init__(
            node_id=node_id,
            title=str(agent.get("name") or "Agent"),
            subtitle=f"{agent.get('model', '?')} · {len(transcript)} turns{repo_marker}",
            body=body,
        )
        self.agent = agent
        self.HEADER_COLOUR = _PROVIDER_HEADER.get(
            str(agent.get("provider", "")), QtGui.QColor("#3b4252")
        )
        # No ports — ConversationNodes aren't part of flow execution.
        # Lineage edges are added by the page after both endpoints
        # exist on the canvas.

        # Tooltip spells out the visibility model so the operator
        # never wonders who else can see this conversation.
        parent = agent.get("parent_name")
        if parent:
            origin = f"Spawned as a follow-up of: {parent}"
        else:
            origin = "Top-level conversation (no parent)."
        ws_path = agent.get("workspace_path") or ""
        repo_line = (
            f"Repo:     {ws_name or 'bound'}{f' ({ws_path})' if ws_path else ''}\n"
            if agent.get("workspace_id")
            else "Repo:     none — chat-only\n"
        )
        self.setToolTip(
            f"{agent.get('name', '?')}\n"
            f"Provider: {agent.get('provider', '?')}\n"
            f"Model:    {agent.get('model', '?')}\n"
            f"Turns:    {len(transcript)}\n"
            f"{repo_line}"
            f"\n{origin}\n\n"
            "Visibility: this transcript is private to the agent.  "
            "Other agents only see it if they were spawned from it "
            "via the Agents tab's Spawn follow-up flow.\n\n"
            "Double-click to open the chat window for this agent."
        )

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "id": self.node_id,
            "type": "conversation",
            "x": pos.x(),
            "y": pos.y(),
            "agent_id": self.agent.get("id"),
            "params": {},
        }
