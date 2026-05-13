"""Codex CLI provider.

Wraps the local ``codex`` binary as a subscription-style/locally-authenticated
provider. The implementation mirrors the lightweight chat-style CLI adapters.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator
from typing import Any, Literal

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError, utc_now

_MODEL_ALIAS = {
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.2-codex": "gpt-5.2-codex",
    "gpt-5-codex": "gpt-5-codex",
    "codex-mini-latest": "codex-mini-latest",
}


def _codex_binary() -> str | None:
    return shutil.which("codex")


def _resolve_model(card_model: str) -> str | None:
    if not card_model:
        return None
    return _MODEL_ALIAS.get(card_model, card_model)


async def _probe_codex_auth(*, timeout: float = 20.0) -> tuple[bool, str]:
    binary = _codex_binary()
    if not binary:
        return False, "codex not on PATH"

    proc = await asyncio.create_subprocess_exec(
        binary,
        "exec",
        "respond with the single word OK",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.communicate(), timeout=2.0)
        return False, f"timed out after {timeout}s"

    stdout = stdout_b.decode("utf-8", errors="replace").strip()
    stderr = stderr_b.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or f"codex CLI exit {proc.returncode}"
        return False, detail
    return True, stdout or stderr or "ok"


class CodexCLIChatSession(ChatSession):
    name = "codex-cli"

    def __init__(
        self,
        card: PersonalityCard,
        system: str | None = None,
        cwd: str | None = None,
    ) -> None:
        self.card = card
        self.system = system or ""
        self.cwd = cwd
        self._history: list[dict[str, str]] = []
        self._binary = _codex_binary()
        if not self._binary:
            raise ProviderError("`codex` not found on PATH; install Codex CLI first")

    async def send(
        self,
        message: str,
        *,
        attachments: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        self._history.append({"role": "user", "content": message})
        prompt = self._render_prompt()
        # Non-interactive path: run one-shot prompt with optional model.
        args: list[str] = [self._binary, "exec", prompt]
        model = _resolve_model(self.card.model)
        if model:
            args.extend(["--model", model])
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )
        except Exception as exc:
            yield StreamEvent(kind="error", text=f"codex CLI spawn failed: {exc}")
            return
        stdout_b, stderr_b = await proc.communicate()
        if proc.returncode != 0:
            err = stderr_b.decode("utf-8", errors="replace").strip()
            yield StreamEvent(kind="error", text=f"codex CLI exit {proc.returncode}: {err[:500]}")
            return
        text = stdout_b.decode("utf-8", errors="replace").strip()
        self._history.append({"role": "assistant", "content": text})
        if text:
            yield StreamEvent(kind="text_delta", text=text)
        yield StreamEvent(
            kind="assistant_message",
            text=text,
            payload={"finished_at": utc_now().isoformat()},
        )
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
        parts: list[str] = []
        if self.system:
            parts.append(f"System: {self.system}")
        for m in self._history[:-1]:
            role = "User" if m["role"] == "user" else "Assistant"
            parts.append(f"{role}: {m['content']}")
        parts.append(f"User: {self._history[-1]['content']}")
        return "\n\n".join(parts)

    async def close(self) -> None:
        return None


class CodexCLIProvider:
    name: Literal["codex-cli"] = "codex-cli"

    async def open_chat(
        self,
        card: PersonalityCard,
        *,
        system: str | None = None,
        cwd: str | None = None,
    ) -> ChatSession:
        if card.provider != "codex-cli":
            raise ProviderError(f"card.provider={card.provider!r} is not codex-cli")
        return CodexCLIChatSession(card, system=system, cwd=cwd)

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: Any,
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            kind="error",
            text=(
                "agentic dispatch via Codex CLI is not yet wired; pick a chat "
                "archetype for codex-cli or use an API-key provider for agentic flows"
            ),
        )

    async def healthcheck(self) -> bool:
        ok, _detail = await _probe_codex_auth(timeout=8.0)
        return ok
