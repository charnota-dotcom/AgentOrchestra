"""Gemini CLI provider.

Wraps the local ``gemini`` binary so cards can talk to Google's models
through the user's existing Gemini CLI auth (Google AI Studio login,
Workspace SSO, or ``GEMINI_API_KEY`` already configured for the CLI).
The orchestrator stays a thin subprocess client; the CLI handles auth
and rate-limiting against the user's own quota.

Why this exists: same logic as ``claude_cli`` — many users already have
the Gemini CLI installed and signed in, so requiring a separate
``GEMINI_API_KEY`` for the orchestrator would be a redundant friction.
This provider piggybacks on whatever the CLI is already configured to
use.

V1 supports chat-style cards only.  The Gemini CLI runs its own
agentic loop with shell / file tools; bridging that into the
orchestrator's WorktreeToolset and approval gates is the same V5 work
as the Claude CLI agentic path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator
from typing import Any

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError, utc_now

log = logging.getLogger(__name__)


# Card model names → Gemini CLI ``--model`` aliases.  Pass-through for
# anything we don't explicitly know about so the CLI gets the final
# say.
_MODEL_ALIAS = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-1.5-pro": "gemini-1.5-pro",
    "gemini-1.5-flash": "gemini-1.5-flash",
    # Friendly short names so cards can say model="pro" / "flash".
    "pro": "gemini-2.5-pro",
    "flash": "gemini-2.5-flash",
}


def _gemini_binary() -> str | None:
    return shutil.which("gemini")


def _resolve_model(card_model: str) -> str | None:
    if not card_model:
        return None
    if card_model in _MODEL_ALIAS:
        return _MODEL_ALIAS[card_model]
    if card_model.startswith("gemini-"):
        return card_model
    return card_model


class GeminiCLIChatSession(ChatSession):
    """Multi-turn chat against the local ``gemini`` CLI.

    The CLI's headless mode (``gemini -p PROMPT``) takes a single
    prompt string and emits the model reply on stdout.  Multi-turn is
    folded into the prompt the same way ``claude_cli`` does it — there
    is no first-class session API in the CLI yet that we can reuse
    from a subprocess.
    """

    name = "gemini-cli"

    def __init__(
        self,
        card: PersonalityCard,
        system: str | None = None,
        cwd: str | None = None,
    ) -> None:
        self.card = card
        self.system = system or ""
        self.cwd = cwd  # repo-aware agents spawn the CLI inside their workspace
        self._history: list[dict[str, str]] = []
        self._binary = _gemini_binary()
        if not self._binary:
            raise ProviderError(
                "`gemini` not found on PATH; install the Gemini CLI first "
                "(https://github.com/google-gemini/gemini-cli)"
            )

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
        # The Gemini CLI refuses to run in an "untrusted" working
        # directory in headless mode.  The CLI advertises three
        # bypasses; we set both the env var AND the `--skip-trust`
        # flag so that whichever the installed CLI version honours,
        # it lands.  Operators were hitting `gemini CLI exit 55`
        # ("not running in a trusted directory") with the env-var-only
        # variant on certain CLI versions / cwd combinations.  See
        # https://geminicli.com/docs/cli/trusted-folders/#headless-and-automated-environments
        args: list[str] = [self._binary, "-p", prompt, "--skip-trust"]
        model = _resolve_model(self.card.model)
        if model:
            args.extend(["--model", model])

        env = {**os.environ, "GEMINI_CLI_TRUST_WORKSPACE": "true"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.cwd,
            )
        except FileNotFoundError:
            yield StreamEvent(kind="error", text="`gemini` binary not found")
            return

        try:
            stdout_b, stderr_b = await proc.communicate()
        except Exception as exc:
            yield StreamEvent(kind="error", text=f"gemini CLI failed: {exc}")
            return

        if proc.returncode != 0:
            err = stderr_b.decode("utf-8", errors="replace").strip()
            yield StreamEvent(
                kind="error",
                text=f"gemini CLI exit {proc.returncode}: {err[:500]}",
            )
            return

        text = stdout_b.decode("utf-8", errors="replace").strip()
        # The CLI prints diagnostic banners on some platforms before
        # the actual reply.  Drop leading banner-ish lines that start
        # with our known noise prefixes.
        text = _strip_cli_noise(text)
        self._history.append({"role": "assistant", "content": text})

        if text:
            yield StreamEvent(kind="text_delta", text=text)
        yield StreamEvent(
            kind="assistant_message",
            text=text,
            payload={"finished_at": utc_now().isoformat()},
        )

        # The plain-text headless mode doesn't surface usage counts or a
        # cost figure, so we record zeros and flag the run as
        # subscription-billed for the cost dashboard.
        yield StreamEvent(
            kind="usage",
            payload={
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "via_subscription": True,
            },
        )
        yield StreamEvent(kind="finish")

    def _render_prompt(self) -> str:
        if len(self._history) == 1:
            if self.system:
                return f"System: {self.system}\n\nUser: {self._history[0]['content']}"
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


def _strip_cli_noise(text: str) -> str:
    """Drop leading lines that are obviously the CLI's own chatter.

    The Gemini CLI sometimes prints a "Loaded cached credentials" or
    "Using model …" line before the real reply.  We want the model
    output only.
    """
    lines = text.splitlines()
    while lines and _is_noise_line(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


_NOISE_PREFIXES = (
    "Loaded cached credentials",
    "Using model",
    "Data collection is",
    "Authenticating",
)


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(p) for p in _NOISE_PREFIXES)


class GeminiCLIProvider:
    name: str = "gemini-cli"

    async def open_chat(
        self,
        card: PersonalityCard,
        *,
        system: str | None = None,
        cwd: str | None = None,
    ) -> ChatSession:
        if card.provider != "gemini-cli":
            raise ProviderError(f"card.provider={card.provider!r} is not gemini-cli")
        return GeminiCLIChatSession(card, system=system, cwd=cwd)

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: Any,
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        # Same V5 deferral as claude_cli: the Gemini CLI runs its own
        # tool loop and bridging it into our WorktreeToolset isn't
        # wired yet.  Surface a clear error so the dispatcher can
        # fall back or abort with a useful reason.
        yield StreamEvent(
            kind="error",
            text=(
                "agentic dispatch via the Gemini CLI is not yet wired; "
                "pick a chat archetype (Broad Research, Narrow Research, "
                "QA on Fix) or set the card's provider to 'google' with "
                "a GOOGLE_API_KEY for the Code Edit archetype"
            ),
        )

    async def healthcheck(self) -> bool:
        return _gemini_binary() is not None
