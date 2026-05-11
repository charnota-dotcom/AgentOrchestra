"""Parse ``claude --output-format stream-json`` output.

Claude Code's stream-json mode emits newline-delimited JSON events as
the agent loop progresses.  Each line is one JSON document; the
common event shapes (which we care about) are:

  {"type": "system", "subtype": "init", ...}                      # ignored
  {"type": "assistant",
   "message": {"role": "assistant",
               "content": [{"type": "text", "text": "..."}]}}
  {"type": "assistant",
   "message": {"role": "assistant",
               "content": [{"type": "tool_use",
                            "id": "...", "name": "Bash",
                            "input": {...}}]}}
  {"type": "user",
   "message": {"role": "user",
               "content": [{"type": "tool_result",
                            "tool_use_id": "...", "content": "...",
                            "is_error": false}]}}
  {"type": "result", "subtype": "success",
   "result": "...", "usage": {...}, "total_cost_usd": 0.0}

The parser is **defensive**: malformed JSON lines are skipped (with
a debug log), unknown event types pass through as no-ops, and a
catastrophic parse failure (e.g. the CLI emitted plain text instead
of stream-json) returns an empty result that the session can fall
back from.

Note on sub-agents: the Task tool launches a sub-agent.  We treat
each ``tool_use`` whose ``name == "Task"`` as a sub-agent invocation
in the surfaced ``transcript_kind`` field — the GUI renders these
distinctly from ordinary tool calls.

Hard rule (per the sub-package README): no ``apps.gui`` /
``apps.service.providers.registry`` / ``apps.service.main`` imports —
this module is pure parsing logic, easily unit-testable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ParsedEvent:
    """One thing the parser extracted from a stream-json line.

    ``kind`` mirrors what the GUI / drones_send needs to know:

    * ``"text"`` — model emitted assistant text.  ``text`` holds the
      content.  Multiple ``"text"`` events sum to the final reply.
    * ``"tool_call"`` — model invoked a tool (Bash / Read / Edit / ...
      or ``Task`` for a sub-agent).  ``tool_name``, ``tool_input``,
      ``tool_id``, ``is_subagent`` are populated.
    * ``"tool_result"`` — a tool returned a result.  ``tool_id`` ties
      it to the matching ``"tool_call"`` event; ``tool_output``
      contains the textual result; ``is_error`` is True on tool
      failures.
    * ``"usage"`` — final usage / cost block.  ``usage`` and
      ``cost_usd`` are populated.
    * ``"finish"`` — the result envelope arrived; agent loop done.

    Step numbers are assigned by the parser in order of appearance
    so the GUI can pair tool_call N with tool_result N.
    """

    kind: str
    text: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_id: str = ""
    tool_output: str = ""
    is_subagent: bool = False
    is_error: bool = False
    step: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Top-level parse entrypoint
# ---------------------------------------------------------------------------


def parse_stream_json(raw_stdout: str) -> list[ParsedEvent]:
    """Parse the full stdout of a ``claude --output-format stream-json``
    invocation into a flat list of ``ParsedEvent``.

    Defensive against malformed input: lines that aren't valid JSON
    are skipped (with a debug log); unknown event kinds are
    ignored.  Pure function — no I/O.

    Returns an empty list for empty input (caller should treat that
    as "no events" and fall back to plain-text handling).
    """
    events: list[ParsedEvent] = []
    if not raw_stdout:
        return events

    step = 0
    # Map tool_use_id -> step so tool_result events can borrow it.
    tool_step_by_id: dict[str, int] = {}

    for raw_line in raw_stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.debug("stream-json: skipping unparseable line: %r", line[:120])
            continue
        if not isinstance(obj, dict):
            continue

        event_type = obj.get("type", "")

        if event_type == "assistant":
            # Assistant message — may contain text + tool_use blocks.
            content_blocks = _content_blocks(obj.get("message"))
            for block in content_blocks:
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text") or ""
                    if text:
                        events.append(ParsedEvent(kind="text", text=text))
                elif btype == "tool_use":
                    step += 1
                    tool_id = block.get("id") or ""
                    tool_name = block.get("name") or ""
                    tool_input = block.get("input") or {}
                    if not isinstance(tool_input, dict):
                        tool_input = {"_raw": str(tool_input)}
                    if tool_id:
                        tool_step_by_id[tool_id] = step
                    events.append(
                        ParsedEvent(
                            kind="tool_call",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_id=tool_id,
                            is_subagent=(tool_name == "Task"),
                            step=step,
                        )
                    )

        elif event_type == "user":
            # A user-role envelope carrying tool_result blocks.
            content_blocks = _content_blocks(obj.get("message"))
            for block in content_blocks:
                if block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id") or ""
                output = _flatten_tool_result_content(block.get("content"))
                is_error = bool(block.get("is_error"))
                events.append(
                    ParsedEvent(
                        kind="tool_result",
                        tool_id=tool_id,
                        tool_output=output,
                        is_error=is_error,
                        step=tool_step_by_id.get(tool_id, 0),
                    )
                )

        elif event_type == "result":
            # Final envelope.  Use the `result` text as the canonical
            # assistant text if the streamed assistant blocks didn't
            # already cover it (some CLI versions only emit the final
            # answer in this envelope).
            result_text = obj.get("result") or ""
            if isinstance(result_text, list):
                # `result` is sometimes a list of content blocks too.
                result_text = "".join(
                    c.get("text", "")
                    for c in result_text
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            if result_text and not _any_text_event(events):
                # Backfill: stream didn't carry assistant text, but the
                # result envelope did.  Treat it as the assistant turn.
                events.append(ParsedEvent(kind="text", text=str(result_text)))
            usage = obj.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            cost = obj.get("total_cost_usd") or 0.0
            try:
                cost_f = float(cost)
            except (TypeError, ValueError):
                cost_f = 0.0
            events.append(ParsedEvent(kind="usage", usage=usage, cost_usd=cost_f))
            events.append(ParsedEvent(kind="finish"))

        # Any other event type (system / progress / etc.) is ignored
        # — the parser is intentionally permissive about unrecognised
        # event types so a future stream-json revision adding new
        # kinds doesn't break us.

    return events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_blocks(message: Any) -> list[dict[str, Any]]:
    """Extract the ``content`` array from an assistant/user message.

    The stream-json shape is:
        {"role": "...", "content": [{"type": "...", ...}, ...]}
    """
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _flatten_tool_result_content(content: Any) -> str:
    """Tool results sometimes arrive as a list of content blocks
    (``[{"type": "text", "text": "..."}, ...]``) and sometimes as a
    plain string.  Normalise to a single string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text")
                if t:
                    out.append(str(t))
            elif isinstance(block, str):
                out.append(block)
        return "".join(out)
    return str(content)


def _any_text_event(events: list[ParsedEvent]) -> bool:
    return any(e.kind == "text" and e.text for e in events)
