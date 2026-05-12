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
from typing import Any, Literal

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

    async def send(self, message: str, *, attachments: Any = None) -> AsyncIterator[StreamEvent]:
        # Attachments aren't wired through Ollama in V1; accept kwarg
        # for protocol uniformity and ignore.
        del attachments
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
    name: Literal["ollama"] = "ollama"

    async def open_chat(
        self,
        card: PersonalityCard,
        *,
        system: str | None = None,
        cwd: str | None = None,
    ) -> ChatSession:
        del cwd  # HTTP session has no cwd concept
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
        # OpenAI-compatible function-calling against Ollama.  Many local
        # models have weak tool-call adherence; we cap turns and surface
        # malformed-arg JSON as a tool error rather than aborting.
        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in executor.tools()
        ]
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_message})

        client = httpx.AsyncClient(
            base_url=_base_url(),
            timeout=httpx.Timeout(180.0, connect=5.0),
        )
        tokens_in = tokens_out = 0
        try:
            for turn in range(max_turns):
                body = {
                    "model": card.model,
                    "messages": messages,
                    "tools": tool_defs,
                    "stream": False,
                }
                resp = await client.post("/chat/completions", json=body)
                if resp.status_code != 200:
                    yield StreamEvent(
                        kind="error",
                        text=f"ollama HTTP {resp.status_code}: {resp.text[:200]}",
                    )
                    return
                data = resp.json()
                usage = data.get("usage") or {}
                tokens_in += int(usage.get("prompt_tokens") or 0)
                tokens_out += int(usage.get("completion_tokens") or 0)
                yield StreamEvent(
                    kind="usage",
                    payload={"input_tokens": tokens_in, "output_tokens": tokens_out},
                )

                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                text = msg.get("content") or ""
                if text:
                    yield StreamEvent(kind="text_delta", text=text)
                    yield StreamEvent(kind="assistant_message", text=text)

                messages.append(
                    {
                        "role": "assistant",
                        "content": text,
                        "tool_calls": tool_calls,
                    }
                )

                if not tool_calls:
                    yield StreamEvent(
                        kind="turn_end",
                        payload={"turn": turn + 1, "stop_reason": "stop"},
                    )
                    break

                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        args = {}
                    call_id = tc.get("id") or f"ollama-{turn}-{name}"
                    yield StreamEvent(
                        kind="tool_call",
                        text=name,
                        payload={
                            "tool_use_id": call_id,
                            "name": name,
                            "params": args,
                        },
                    )
                    result = await executor.execute(call_id, name, args)
                    yield StreamEvent(
                        kind="tool_result",
                        payload={
                            "tool_use_id": result.tool_use_id,
                            "name": result.name,
                            "is_error": result.is_error,
                            "content": result.content,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": json.dumps(result.content, default=str),
                        }
                    )

                yield StreamEvent(
                    kind="turn_end",
                    payload={"turn": turn + 1, "tool_calls": len(tool_calls)},
                )
            else:
                yield StreamEvent(
                    kind="error",
                    text=f"agent exceeded {max_turns}-turn budget",
                )

            yield StreamEvent(
                kind="finish",
                payload={"input_tokens": tokens_in, "output_tokens": tokens_out},
            )
        except Exception as exc:
            log.exception("Ollama run_with_tools failed")
            yield StreamEvent(kind="error", text=str(exc))
        finally:
            await client.aclose()

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(_base_url().rsplit("/", 1)[0] + "/api/tags")
                return r.status_code == 200
        except Exception:
            return False
