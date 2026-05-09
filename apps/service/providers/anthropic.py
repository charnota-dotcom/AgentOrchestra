"""Anthropic provider adapter.

V1 implementation prefers the official Anthropic Python SDK
(`anthropic` package) for the ChatSession path because it is provider-
neutral and ships with every Claude release.  When the user opts in,
we will swap to the Claude Agent SDK (`claude_agent_sdk`) for the
Run-in-worktree path so we get tool-use + subagent handling for free.

This file is the ChatSession implementation.  Run dispatch is a stub
that the dispatch subsystem will fill in week 4.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError, utc_now

log = logging.getLogger(__name__)


class _MissingDependency:
    """Stand-in raised lazily when `anthropic` is not installed.

    Lets the rest of the service start without the SDK; only the
    Anthropic provider path errors when actually used.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, _: str) -> Any:
        raise ProviderError(
            f"missing dependency: {self.name}; install with `pip install anthropic`"
        )


def _import_sdk() -> Any:
    try:
        import anthropic  # type: ignore[import-not-found]
        return anthropic
    except ImportError:
        return _MissingDependency("anthropic")


class AnthropicChatSession(ChatSession):
    name = "anthropic"

    def __init__(self, card: PersonalityCard, system: str | None = None) -> None:
        self.card = card
        self.system = system or ""
        self._sdk = _import_sdk()
        self._history: list[dict[str, Any]] = []
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key and not isinstance(self._sdk, _MissingDependency):
            raise ProviderError(
                "ANTHROPIC_API_KEY not set; load from keyring before opening chat"
            )
        self._client = (
            self._sdk.AsyncAnthropic(api_key=api_key)
            if not isinstance(self._sdk, _MissingDependency)
            else self._sdk
        )

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        self._history.append({"role": "user", "content": message})
        kwargs = {
            "model": self.card.model,
            "messages": self._history,
            "max_tokens": 4096,
        }
        if self.system:
            kwargs["system"] = self.system

        accumulated_text: list[str] = []
        usage_payload: dict[str, Any] = {}

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    # Normalize the Anthropic SDK event shapes.
                    et = getattr(event, "type", "")
                    if et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        text = getattr(delta, "text", "") if delta else ""
                        if text:
                            accumulated_text.append(text)
                            yield StreamEvent(kind="text_delta", text=text)
                    elif et == "message_delta":
                        usage = getattr(event, "usage", None)
                        if usage:
                            usage_payload = {
                                "input_tokens": getattr(usage, "input_tokens", 0),
                                "output_tokens": getattr(usage, "output_tokens", 0),
                            }
                    elif et == "message_stop":
                        break

            full_text = "".join(accumulated_text)
            self._history.append({"role": "assistant", "content": full_text})
            yield StreamEvent(
                kind="assistant_message",
                text=full_text,
                payload={"finished_at": utc_now().isoformat()},
            )
            if usage_payload:
                yield StreamEvent(kind="usage", payload=usage_payload)
            yield StreamEvent(kind="finish")
        except Exception as exc:
            log.exception("Anthropic chat send failed")
            yield StreamEvent(kind="error", text=str(exc))

    async def close(self) -> None:
        if not isinstance(self._client, _MissingDependency):
            await self._client.close()


class AnthropicProvider:
    name: str = "anthropic"

    def __init__(self) -> None:
        self._sdk = _import_sdk()

    async def open_chat(
        self, card: PersonalityCard, *, system: str | None = None
    ) -> ChatSession:
        if card.provider != "anthropic":
            raise ProviderError(f"card.provider={card.provider!r} is not anthropic")
        return AnthropicChatSession(card, system=system)

    async def healthcheck(self) -> bool:
        if isinstance(self._sdk, _MissingDependency):
            return False
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
