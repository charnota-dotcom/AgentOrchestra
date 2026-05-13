"""Claude Code CLI provider.

Wraps the local ``claude`` binary so cards on a Max-plan subscription
work without a separate Anthropic API key.  The CLI authenticates
against ``~/.claude/`` on the user's machine; the orchestrator is
just a JSON-over-stdin/stdout client.

V1 of this provider supported chat-style cards only with
``--output-format json``.  V2 (PR ``claude/claude-cli-stream-json``)
upgrades the chat path to ``--output-format stream-json`` so the
orchestrator captures the full agent loop — tool calls, tool
results, sub-agent invocations — not just the final assistant text.
The session implementation lives in ``session.py``; this module just
exposes the ``LLMProvider`` shell and the shared ``_claude_binary()``
helper.

Why this exists: the user already pays for a Claude subscription via
Max; calling the Anthropic API directly would double-bill them.  This
provider piggybacks on the same auth they're already using.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import AsyncIterator
from typing import Any, Literal

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError

log = logging.getLogger(__name__)


def _claude_binary() -> str | None:
    return shutil.which("claude")


class ClaudeCLIProvider:
    name: Literal["claude-cli"] = "claude-cli"

    async def open_chat(
        self,
        card: PersonalityCard,
        *,
        system: str | None = None,
        cwd: str | None = None,
    ) -> ChatSession:
        if card.provider != "claude-cli":
            raise ProviderError(f"card.provider={card.provider!r} is not claude-cli")
        # Lazy import keeps the session module from being required
        # for healthcheck-only paths and avoids any circular-import
        # surprises if session.py later needs to call back into the
        # provider for things like registry lookups.
        from apps.service.providers.claude_cli.session import ClaudeCLIChatSession

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
                "Code Planning Assistant archetype"
            ),
        )

    async def healthcheck(self) -> bool:
        return _claude_binary() is not None
