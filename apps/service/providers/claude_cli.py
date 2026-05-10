"""Claude Code CLI provider.

Wraps the local ``claude`` binary so cards on a Max-plan subscription
work without a separate Anthropic API key.  The CLI authenticates
against ``~/.claude/`` on the user's machine; the orchestrator is
just a JSON-over-stdin/stdout client.

V1 of this provider supports chat-style cards only.  Agentic dispatch
through the CLI (so the CLI's own Bash/Edit/Read tool loop runs
worktree-bound) is a future slice — the CLI doesn't yet accept the
orchestrator's WorktreeToolset as a tool catalog.

Why this exists: the user already pays for a Claude subscription via
Max; calling the Anthropic API directly would double-bill them.  This
provider piggybacks on the same auth they're already using.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import Attachment, PersonalityCard, ProviderError, utc_now

log = logging.getLogger(__name__)


# Card model names we know how to map to a `claude` CLI ``--model``
# alias.  Anything else is passed through verbatim and the CLI gets
# the final say.
_MODEL_ALIAS = {
    "claude-haiku-4-5": "haiku",
    "claude-sonnet-4-5": "sonnet",
    "claude-opus-4-7": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-6": "opus",
}


def _claude_binary() -> str | None:
    return shutil.which("claude")


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
    array in headless mode.  A ``--continue`` / session-id path is a
    follow-up.
    """

    name = "claude-cli"

    def __init__(
        self,
        card: PersonalityCard,
        system: str | None = None,
        cwd: str | None = None,
    ) -> None:
        self.card = card
        self.system = system or ""
        self.cwd = cwd  # if set, CLI is spawned with this as its working dir
        self._history: list[dict[str, str]] = []
        self._binary = _claude_binary()
        if not self._binary:
            raise ProviderError(
                "`claude` not found on PATH; install Claude Code first "
                "(https://docs.claude.com/en/docs/claude-code)"
            )

    async def send(
        self,
        message: str,
        *,
        attachments: list[Attachment] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # Image attachments: prepend `@<absolute_path>` references the
        # CLI understands so the model "sees" the file.  Non-image
        # attachments are dropped here — the orchestrator inlines
        # spreadsheet text into the prompt before calling us.
        # Path safety: the prompt is one big string, so paths with
        # whitespace / newlines / extra `@` would break the CLI's
        # tokenizer or smuggle in arbitrary file references.  Refuse
        # them here even though attachments_upload also rejects them
        # — defence in depth for any code path that constructs a
        # session directly.
        att_prefix = ""
        if attachments:
            paths: list[str] = []
            for a in attachments:
                p = a.stored_path
                if any(c in p for c in (" ", "\t", "\n", "\r")):
                    yield StreamEvent(
                        kind="error",
                        text=f"attachment path contains whitespace, refused: {p}",
                    )
                    return
                if "@" in Path(p).name:
                    yield StreamEvent(
                        kind="error",
                        text=f"attachment filename contains '@', refused: {p}",
                    )
                    return
                paths.append(p)
            att_prefix = " ".join(f"@{p}" for p in paths)
        full_message = f"{att_prefix} {message}".strip() if att_prefix else message
        self._history.append({"role": "user", "content": full_message})
        prompt = self._render_prompt()
        args = [
            self._binary,
            "-p",
            prompt,
            "--output-format",
            "json",
        ]
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
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            # Some CLI versions or invocations don't honour
            # ``--output-format json`` (e.g. when the model returns a
            # plain conversational reply).  Treat the whole stdout as
            # the assistant text so the user still sees the answer;
            # token / cost info is unavailable in that mode.
            payload = {"result": raw_text.strip()}

        text = payload.get("result", "")
        if isinstance(text, list):
            text = "".join(
                c.get("text", "") for c in text if isinstance(c, dict) and c.get("type") == "text"
            )
        text = str(text or "")
        self._history.append({"role": "assistant", "content": text})

        if text:
            yield StreamEvent(kind="text_delta", text=text)
        yield StreamEvent(
            kind="assistant_message",
            text=text,
            payload={
                "finished_at": utc_now().isoformat(),
                "session_id": payload.get("session_id"),
            },
        )

        usage = payload.get("usage") or {}
        yield StreamEvent(
            kind="usage",
            payload={
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                # Cost is reported via the subscription so we record it
                # at face value but don't add it to the user's API
                # spend bucket.
                "cost_usd": float(payload.get("total_cost_usd") or 0.0),
                "via_subscription": True,
            },
        )
        yield StreamEvent(kind="finish")

    def _render_prompt(self) -> str:
        # Single-turn: just the user's message.  Multi-turn: prior
        # turns inlined as plain text since the CLI's headless mode
        # accepts a single prompt string.
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


class ClaudeCLIProvider:
    name: str = "claude-cli"

    async def open_chat(
        self,
        card: PersonalityCard,
        *,
        system: str | None = None,
        cwd: str | None = None,
    ) -> ChatSession:
        if card.provider != "claude-cli":
            raise ProviderError(f"card.provider={card.provider!r} is not claude-cli")
        return ClaudeCLIChatSession(card, system=system, cwd=cwd)

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: Any,
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        # Agentic dispatch through the Claude Code CLI is on the V5
        # roadmap.  The CLI runs its own tool loop (Bash / Edit /
        # Read / Write); bridging that into our WorktreeToolset and
        # the orchestrator's permission gates is non-trivial.  For
        # now we surface a clear, actionable error so the dispatcher
        # falls through to the configured fallback (or aborts the run
        # with a useful reason).
        yield StreamEvent(
            kind="error",
            text=(
                "agentic dispatch via the Claude Code CLI is not yet "
                "wired; pick a chat archetype (Broad Research, "
                "Narrow Research, QA on Fix) or set the card's "
                "provider to 'anthropic' with an API key for the "
                "Code Edit archetype"
            ),
        )

    async def healthcheck(self) -> bool:
        return _claude_binary() is not None
