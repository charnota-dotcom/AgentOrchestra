"""Client-side handoff format renderers.

Mirrors the server-side ``drones.export`` RPC at
``apps/service/main.py:_format_drone_handoff`` so the GUI can build
the text locally without a round-trip in cases where it already has
the action snapshot in hand.  Three formats:

* ``continuation`` — persona + skills + full transcript framed as
  "pick up from the last user turn".  For continuing in a fresh tab
  of the same or a different service.
* ``fork`` — persona + skills only, no prior turns.  For spawning a
  sibling conversation with the same character on a new topic.
* ``plain`` — user/assistant turns only, no framing.  For sharing
  with a teammate, embedding in a doc, code-reviewing the
  conversation.

Service-tagged formats (Claude/GPT/Gemini per-service tuning) are a
v2 polish — the universal continuation format works across all
three big services because they're flexible about input shape.

Hard-import rule (per the sub-package README): no
``apps.service.*`` or ``apps.gui.ipc.*`` imports — pure stdlib.
The duplication with the service-side formatter is deliberate; the
GUI can render handoffs without ever hitting the RPC channel,
which keeps the Copy-to-clipboard latency tight.
"""

from __future__ import annotations

from typing import Any, Literal

HandoffFormat = Literal["continuation", "fork", "plain"]


_ROLE_DESCRIPTIONS = {
    "worker": "Worker — self-contained chat, cannot mutate peers",
    "supervisor": "Supervisor — full peer authority",
    "courier": "Courier — can append references onto peers",
    "auditor": "Auditor — read-only",
}


def render_handoff(
    kind: HandoffFormat,
    *,
    persona: str,
    role: str,
    skills: list[str],
    transcript: list[dict[str, Any]],
) -> str:
    """Build a handoff text block in one of the three supported formats.

    Returns a single string ready to put on the clipboard.  Pure
    function — no Qt, no I/O.  Tests live alongside this module in
    ``tests/test_handoff.py``.
    """
    role_line = _ROLE_DESCRIPTIONS.get(role, role)
    skills_line = "Operator-supplied skills you can invoke: " + " ".join(skills) if skills else ""

    # Drop tool_call / tool_result agent-loop entries — they're a
    # local visibility record for the GUI, not chat turns to hand off
    # to a fresh browser session.  See ``ClaudeCLIChatSession``
    # (stream-json) and ``drones_send`` for where they originate.
    transcript = [m for m in transcript if m.get("role") in ("user", "assistant")]

    if kind == "fork":
        lines: list[str] = []
        lines.append(f"You are a {role} drone with the following character:")
        if persona:
            lines.append("")
            lines.append(persona)
        if skills_line:
            lines.append("")
            lines.append(skills_line)
        lines.append("")
        lines.append("Please introduce yourself briefly and wait for the user's first instruction.")
        return "\n".join(lines).rstrip() + "\n"

    if kind == "plain":
        chunks: list[str] = []
        for m in transcript:
            who = "User" if m.get("role") == "user" else "Assistant"
            chunks.append(f"{who}: {m.get('content', '')}")
        return ("\n\n".join(chunks)).rstrip() + "\n"

    # continuation (default)
    lines = []
    lines.append(
        "You are continuing a conversation that began in another window. "
        "Pick up from the last user message; do not repeat or paraphrase "
        "prior turns."
    )
    lines.append("")
    lines.append("[Role and persona]")
    lines.append(role_line)
    if persona:
        lines.append(persona)
    if skills_line:
        lines.append("")
        lines.append("[Available skills]")
        lines.append(" ".join(skills))
    if transcript:
        lines.append("")
        lines.append("[Conversation so far]")
        lines.append("")
        for m in transcript:
            who = "User" if m.get("role") == "user" else "You (assistant)"
            lines.append(f"{who}: {m.get('content', '')}")
            lines.append("")
        lines.append("[End of prior conversation — please respond to the user's next message.]")
    return "\n".join(lines).rstrip() + "\n"
