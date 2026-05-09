"""Ollama provider adapter.

Talks to a local Ollama server via the OpenAI-compatible API at
``http://localhost:11434/v1`` by default.  No API key required.
Streams via the chat-completions endpoint.

Agentic Ollama runs (tool-using) are deferred; most local models are
weak at strict tool-call schemas, and the V1 user surface for code
editing is Anthropic-driven.  Chat-only is enough to prove the local
fallback path.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError, utc_now

log = logging.getLogger(__name__)


def _base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")


class OllamaChatSession(ChatSession):
    name = "ollama"

    def __init__(self, card: PersonalityCard, system: str | None = None) -> None:
        self.card = card
        self.system = system or ""
        self._history: list[dict[str, Any]] = []
        if self.system:
            self._history.append({"role": "system", "content": self.system})
        self._client = httpx.AsyncClient(
            base_url=_base_url(),
            timeout=httpx.Timeout(120.0, connect=5.0),
        )

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        self._history.append({"role": "user", "content": message})
        body = {
            "model": self.card.model,
            "messages": self._history,
            "stream": True,
        }
        accumulated: list[str] = []
        try:
            async with self._client.stream(
                "POST",
                "/chat/completions",
                json=body,
                headers={"accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    yield StreamEvent(
                        kind="error",
                        text=f"ollama HTTP {resp.status_code}: {err.decode(errors='replace')[:200]}",
                    )
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        accumulated.append(text)
                        yield StreamEvent(kind="text_delta", text=text)

            full = "".join(accumulated)
            self._history.append({"role": "assistant", "content": full})
            yield StreamEvent(
                kind="assistant_message",
                text=full,
                payload={"finished_at": utc_now().isoformat()},
            )
            # Ollama doesn't emit token-count metadata over /v1/chat/completions
            # streaming; we report 0 here and let the cost meter price it as
            # a free local call.
            yield StreamEvent(kind="usage", payload={"input_tokens": 0, "output_tokens": 0})
            yield StreamEvent(kind="finish")
        except Exception as exc:
            log.exception("Ollama send failed")
            yield StreamEvent(kind="error", text=str(exc))

    async def close(self) -> None:
        await self._client.aclose()


class OllamaProvider:
    name: str = "ollama"

    async def open_chat(self, card: PersonalityCard, *, system: str | None = None) -> ChatSession:
        if card.provider != "ollama":
            raise ProviderError(f"card.provider={card.provider!r} is not ollama")
        return OllamaChatSession(card, system=system)

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: Any,  # ToolExecutor
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            kind="error",
            text=(
                "agentic Ollama runs are deferred; pick a chat archetype "
                "or use an Anthropic card for code editing"
            ),
        )

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(_base_url().rsplit("/", 1)[0] + "/api/tags")
                return r.status_code == 200
        except Exception:
            return False
