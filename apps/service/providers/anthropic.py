"""Anthropic provider adapter.

V1 ships two execution paths over the official Anthropic Python SDK:

- ``ChatSession`` (text-only, streaming) for research and QA archetypes.
- ``run_with_tools`` (non-streaming agent loop) for worktree-bound runs
  that need file-touching tools.  Each turn we call messages.create()
  with the tool catalog, execute every tool_use block, append a
  user-role tool_result message, and continue until stop_reason is
  end_turn or the per-card turn budget is exhausted.

The non-streaming choice for the tool path keeps the implementation
simple and reliable.  The dispatcher emits synthetic StreamEvents so
the Live pane stays responsive.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from apps.service.providers.protocol import ChatSession, StreamEvent
from apps.service.types import PersonalityCard, ProviderError, utc_now

if TYPE_CHECKING:
    from apps.service.dispatch.tools import ToolExecutor

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
            raise ProviderError("ANTHROPIC_API_KEY not set; load from keyring before opening chat")
        self._client = (
            self._sdk.AsyncAnthropic(api_key=api_key)
            if not isinstance(self._sdk, _MissingDependency)
            else self._sdk
        )

    async def send(self, message: str, *, attachments: Any = None) -> AsyncIterator[StreamEvent]:
        # Attachments aren't wired through the API path yet (V1 covers
        # the CLI providers).  Accept the kwarg so the protocol stays
        # uniform, but quietly ignore it.
        del attachments
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

    async def open_chat(self, card: PersonalityCard, *, system: str | None = None) -> ChatSession:
        if card.provider != "anthropic":
            raise ProviderError(f"card.provider={card.provider!r} is not anthropic")
        return AnthropicChatSession(card, system=system)

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: ToolExecutor,
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        if card.provider != "anthropic":
            raise ProviderError(f"card.provider={card.provider!r} is not anthropic")
        if isinstance(self._sdk, _MissingDependency):
            yield StreamEvent(kind="error", text="anthropic SDK not installed")
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            yield StreamEvent(kind="error", text="ANTHROPIC_API_KEY not set")
            return

        client = self._sdk.AsyncAnthropic(api_key=api_key)
        try:
            tool_defs = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in executor.tools()
            ]
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": user_message},
            ]
            tokens_in = tokens_out = 0

            for turn in range(max_turns):
                kwargs: dict[str, Any] = {
                    "model": card.model,
                    "max_tokens": 4096,
                    "messages": messages,
                    "tools": tool_defs,
                }
                if system:
                    kwargs["system"] = system

                response = await client.messages.create(**kwargs)
                usage = getattr(response, "usage", None)
                if usage:
                    tokens_in += getattr(usage, "input_tokens", 0) or 0
                    tokens_out += getattr(usage, "output_tokens", 0) or 0
                yield StreamEvent(
                    kind="usage",
                    payload={
                        "input_tokens": tokens_in,
                        "output_tokens": tokens_out,
                    },
                )

                # Mirror the assistant turn into our message history.
                assistant_blocks: list[dict[str, Any]] = []
                for block in response.content:
                    btype = getattr(block, "type", "")
                    if btype == "text":
                        text = getattr(block, "text", "") or ""
                        assistant_blocks.append({"type": "text", "text": text})
                        if text:
                            yield StreamEvent(kind="text_delta", text=text)
                            yield StreamEvent(kind="assistant_message", text=text)
                    elif btype == "tool_use":
                        block_id = getattr(block, "id", "")
                        name = getattr(block, "name", "")
                        params = getattr(block, "input", {}) or {}
                        assistant_blocks.append(
                            {
                                "type": "tool_use",
                                "id": block_id,
                                "name": name,
                                "input": params,
                            }
                        )
                        yield StreamEvent(
                            kind="tool_call",
                            text=name,
                            payload={"tool_use_id": block_id, "name": name, "params": params},
                        )

                messages.append({"role": "assistant", "content": assistant_blocks})

                # Find tool_use blocks; if there are none, the loop is done.
                tool_uses = [b for b in assistant_blocks if b.get("type") == "tool_use"]
                if not tool_uses:
                    yield StreamEvent(
                        kind="turn_end",
                        payload={
                            "stop_reason": getattr(response, "stop_reason", None),
                            "turn": turn + 1,
                        },
                    )
                    break

                # Execute each tool, build tool_result blocks, append.
                tool_results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    result = await executor.execute(
                        tu["id"],
                        tu["name"],
                        tu["input"],
                    )
                    yield StreamEvent(
                        kind="tool_result",
                        payload={
                            "tool_use_id": result.tool_use_id,
                            "name": result.name,
                            "is_error": result.is_error,
                            "content": result.content,
                        },
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_use_id,
                            "content": _coerce_tool_content(result.content),
                            "is_error": result.is_error,
                        }
                    )

                messages.append({"role": "user", "content": tool_results})
                yield StreamEvent(
                    kind="turn_end",
                    payload={
                        "stop_reason": getattr(response, "stop_reason", None),
                        "turn": turn + 1,
                        "tool_calls": len(tool_uses),
                    },
                )

                stop_reason = getattr(response, "stop_reason", "")
                if stop_reason == "end_turn":
                    break
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
            log.exception("Anthropic run_with_tools failed")
            yield StreamEvent(kind="error", text=str(exc))
        finally:
            await client.close()

    async def healthcheck(self) -> bool:
        if isinstance(self._sdk, _MissingDependency):
            return False
        return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _coerce_tool_content(content: dict[str, Any]) -> Any:
    """Anthropic accepts tool_result content as either a string or a list
    of content blocks.  We emit a JSON-encoded string for compactness.
    """
    import json

    return json.dumps(content, default=str)
