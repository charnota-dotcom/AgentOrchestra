"""One chat session against the local ``claude`` CLI.

V2 (PR ``claude/claude-cli-stream-json``): uses
``--output-format stream-json`` so the orchestrator captures the
full agent loop — tool calls, tool results, sub-agent invocations
— not just the final assistant text.  The session emits a richer
event stream over the existing ``StreamEvent`` channel:

* ``text_delta`` — assistant text content (sum of these is the
  final reply).
* ``tool_call`` — model invoked a tool.  Payload contains
  ``tool_name``, ``tool_input``, ``tool_id``, ``is_subagent``,
  ``step``.
* ``tool_result`` — a tool returned a result.  Payload contains
  ``tool_id``, ``tool_output``, ``is_error``, ``step``.
* ``assistant_message`` — final consolidated text (legacy event;
  kept for back-compat with dispatcher).
* ``usage`` — final usage / cost block.
* ``finish`` — agent loop done.

Defensive fallback: if stream-json parsing yields zero text and
zero tool events, the session falls back to treating the raw
stdout as the assistant message — same behaviour the v1 ``--output-
format json`` path had for plain-text CLI responses.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from apps.service.providers.claude_cli.stream_parser import parse_stream_json
from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError, utc_now

log = logging.getLogger(__name__)


# Card model names we know how to map to a ``claude`` CLI ``--model``
# alias.  Anything else is passed through verbatim and the CLI gets
# the final say.
_MODEL_ALIAS = {
    "claude-haiku-4-5": "haiku",
    "claude-sonnet-4-5": "sonnet",
    "claude-opus-4-7": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-6": "opus",
}


def _resolve_model(card_model: str) -> str | None:
    if not card_model:
        return None
    if card_model in _MODEL_ALIAS:
        return _MODEL_ALIAS[card_model]
    if any(card_model.startswith(prefix) for prefix in ("haiku", "sonnet", "opus")):
        return card_model
    # Pass through; the CLI errors clearly if the model name is bad.
    return card_model


class ClaudeCLIChatSession(ChatSession):
    """Multi-turn chat against the local ``claude`` CLI.

    Each ``send`` re-spawns the CLI with the prior turns folded into
    the prompt; the CLI itself doesn't accept a structured messages
    array in headless mode.  A ``--continue`` / session-id path is
    a follow-up.
    """

    name = "claude-cli"

    def __init__(
        self,
        card: PersonalityCard,
        system: str | None = None,
        cwd: str | None = None,
        *,
        binary_path: str | None = None,
    ) -> None:
        self.card = card
        self.system = system or ""
        self.cwd = cwd  # if set, CLI is spawned with this as its working dir
        self._history: list[dict[str, str]] = []
        if binary_path is None:
            from apps.service.providers.claude_cli.provider import _claude_binary

            binary_path = _claude_binary()
        if not binary_path:
            raise ProviderError(
                "`claude` not found on PATH; install Claude Code first "
                "(https://docs.claude.com/en/docs/claude-code)"
            )
        self._binary = binary_path

    async def send(
        self,
        message: str,
        *,
        attachments: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        # Attachments aren't wired through this provider in v1; accept
        # kwarg for protocol compatibility and ignore.
        self._history.append({"role": "user", "content": message})
        prompt = self._render_prompt()
        args = [
            self._binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            # The CLI requires --verbose alongside stream-json in
            # non-interactive (headless) mode — without it the CLI
            # falls back to a single result document and we lose the
            # tool-loop visibility this PR is here to capture.
            "--verbose",
        ]
        # Pass persona / repo-aware system context via the CLI's
        # native flag rather than inlining it into the user message.
        # `--append-system-prompt` preserves Claude Code's default
        # tool definitions and adds our operator text on top.
        if self.system:
            args.extend(["--append-system-prompt", self.system])
        model = _resolve_model(self.card.model)
        if model:
            args.extend(["--model", model])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,  # repo-aware agents run inside their workspace
            )
        except FileNotFoundError:
            yield StreamEvent(kind="error", text="`claude` binary not found")
            return

        try:
            stdout_b, stderr_b = await proc.communicate()
        except Exception as exc:
            yield StreamEvent(kind="error", text=f"claude CLI failed: {exc}")
            return

        if proc.returncode != 0:
            err = stderr_b.decode("utf-8", errors="replace").strip()
            yield StreamEvent(
                kind="error",
                text=f"claude CLI exit {proc.returncode}: {err[:500]}",
            )
            return

        raw_text = stdout_b.decode("utf-8", errors="replace")
        events = parse_stream_json(raw_text)

        # Defensive fallback: if the parser found no usable events
        # (e.g. CLI emitted plain text, or a stream-json revision we
        # don't recognise), treat the raw stdout as a plain assistant
        # reply.  Matches the v1 ``--output-format json`` fallback
        # behaviour so any regression in upstream CLI doesn't break
        # existing drones.
        if not events:
            fallback_text = raw_text.strip()
            self._history.append({"role": "assistant", "content": fallback_text})
            if fallback_text:
                yield StreamEvent(kind="text_delta", text=fallback_text)
            yield StreamEvent(
                kind="assistant_message",
                text=fallback_text,
                payload={"finished_at": utc_now().isoformat()},
            )
            yield StreamEvent(kind="finish")
            return

        # Aggregate the assistant text from text events; emit tool
        # events as we hit them so drones_send can append them to
        # the transcript in order.
        text_chunks: list[str] = []
        for ev in events:
            if ev.kind == "text":
                text_chunks.append(ev.text)
                yield StreamEvent(kind="text_delta", text=ev.text)
            elif ev.kind == "tool_call":
                yield StreamEvent(
                    kind="tool_call",
                    text="",
                    payload={
                        "tool_name": ev.tool_name,
                        "tool_input": ev.tool_input,
                        "tool_id": ev.tool_id,
                        "is_subagent": ev.is_subagent,
                        "step": ev.step,
                    },
                )
            elif ev.kind == "tool_result":
                yield StreamEvent(
                    kind="tool_result",
                    text="",
                    payload={
                        "tool_id": ev.tool_id,
                        "tool_output": ev.tool_output,
                        "is_error": ev.is_error,
                        "step": ev.step,
                    },
                )
            elif ev.kind == "usage":
                yield StreamEvent(
                    kind="usage",
                    payload={
                        "input_tokens": int(ev.usage.get("input_tokens") or 0),
                        "output_tokens": int(ev.usage.get("output_tokens") or 0),
                        # Cost is reported via the subscription so we
                        # record it at face value but don't add it to
                        # the user's API spend bucket.
                        "cost_usd": float(ev.cost_usd),
                        "via_subscription": True,
                    },
                )
            elif ev.kind == "finish":
                # Continue draining; we yield finish below after the
                # assistant_message envelope.
                pass

        assistant_text = "".join(text_chunks)
        self._history.append({"role": "assistant", "content": assistant_text})
        yield StreamEvent(
            kind="assistant_message",
            text=assistant_text,
            payload={"finished_at": utc_now().isoformat()},
        )
        yield StreamEvent(kind="finish")

    def _render_prompt(self) -> str:
        # Single-turn: just the user's message.  Multi-turn: prior
        # turns inlined as plain text since the CLI's headless mode
        # accepts a single prompt string.  (Drones bypass this when
        # operating in browser-mode by routing through their own
        # prompt assembly — see drones_send in apps/service/main.py.)
        if len(self._history) == 1:
            return self._history[0]["content"]
        parts = []
        if self.system:
            parts.append(f"System: {self.system}")
        for m in self._history[:-1]:
            role = "User" if m["role"] == "user" else "Assistant"
            parts.append(f"{role}: {m['content']}")
        parts.append(f"User: {self._history[-1]['content']}")
        return "\n\n".join(parts)

    async def close(self) -> None:
        return None
