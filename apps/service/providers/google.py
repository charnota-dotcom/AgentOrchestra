"""Google Gemini provider adapter.

Uses the official ``google-genai`` Python SDK for chat (clean
multi-turn ``Chat`` session that keeps history server-side).  Agentic
runs via the ``gemini`` CLI subprocess are deferred to a later
milestone — the Anthropic agent-loop pattern is the V1 reference.

The chat path emits the same StreamEvent shapes as the Anthropic
adapter so the Live pane and event store are vendor-agnostic.
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
    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, _: str) -> Any:
        raise ProviderError(
            f"missing dependency: {self.name}; install with `pip install google-genai`"
        )


def _import_sdk() -> Any:
    try:
        from google import genai  # type: ignore[import-not-found]

        return genai
    except ImportError:
        return _MissingDependency("google-genai")


class GeminiChatSession(ChatSession):
    name = "google"

    def __init__(self, card: PersonalityCard, system: str | None = None) -> None:
        self.card = card
        self.system = system or ""
        self._sdk = _import_sdk()
        if isinstance(self._sdk, _MissingDependency):
            self._client = self._sdk
            self._chat = self._sdk
            return

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ProviderError(
                "GOOGLE_API_KEY / GEMINI_API_KEY not set; load from keyring before opening chat"
            )
        self._client = self._sdk.Client(api_key=api_key)
        config: dict[str, Any] = {}
        if self.system:
            config["system_instruction"] = self.system
        self._chat = self._client.chats.create(model=card.model, config=config or None)

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        if isinstance(self._sdk, _MissingDependency):
            yield StreamEvent(kind="error", text="google-genai SDK not installed")
            return

        accumulated: list[str] = []
        usage_payload: dict[str, Any] = {}
        try:
            response = await self._chat.send_message_async(message)
            text = getattr(response, "text", None) or ""
            if text:
                accumulated.append(text)
                # SDK doesn't stream sub-tokens by default in send_message_async;
                # emit a single delta + assistant_message so the GUI behaves
                # the same way it does for streamed providers.
                yield StreamEvent(kind="text_delta", text=text)
            usage = getattr(response, "usage_metadata", None)
            if usage:
                usage_payload = {
                    "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                    "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                }

            full = "".join(accumulated)
            yield StreamEvent(
                kind="assistant_message",
                text=full,
                payload={"finished_at": utc_now().isoformat()},
            )
            if usage_payload:
                yield StreamEvent(kind="usage", payload=usage_payload)
            yield StreamEvent(kind="finish")
        except Exception as exc:
            log.exception("Gemini chat send failed")
            yield StreamEvent(kind="error", text=str(exc))

    async def close(self) -> None:
        # google-genai client manages its own lifecycle.
        return None


class GoogleProvider:
    name: str = "google"

    def __init__(self) -> None:
        self._sdk = _import_sdk()

    async def open_chat(self, card: PersonalityCard, *, system: str | None = None) -> ChatSession:
        if card.provider != "google":
            raise ProviderError(f"card.provider={card.provider!r} is not google")
        return GeminiChatSession(card, system=system)

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: Any,  # ToolExecutor
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        # Agentic Gemini runs are deferred; surface a clear error.
        yield StreamEvent(
            kind="error",
            text=(
                "agentic Gemini runs are not yet implemented; pick an "
                "Anthropic card or switch this card to chat mode"
            ),
        )

    async def healthcheck(self) -> bool:
        if isinstance(self._sdk, _MissingDependency):
            return False
        return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
