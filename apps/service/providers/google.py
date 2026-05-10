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

    async def send(self, message: str, *, attachments: Any = None) -> AsyncIterator[StreamEvent]:
        # Attachments aren't wired through this provider in V1; accept
        # the kwarg for protocol uniformity and ignore it.
        del attachments
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
        if isinstance(self._sdk, _MissingDependency):
            yield StreamEvent(kind="error", text="google-genai SDK not installed")
            return
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            yield StreamEvent(kind="error", text="GOOGLE_API_KEY not set")
            return

        client = self._sdk.Client(api_key=api_key)
        tool_decls = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": _strip_jsonschema_for_gemini(t.input_schema),
            }
            for t in executor.tools()
        ]
        gemini_tools = [{"function_declarations": tool_decls}]
        contents: list[dict[str, Any]] = [
            {"role": "user", "parts": [{"text": user_message}]},
        ]
        config: dict[str, Any] = {"tools": gemini_tools}
        if system:
            config["system_instruction"] = system

        tokens_in = tokens_out = 0
        try:
            for turn in range(max_turns):
                response = await client.aio.models.generate_content(
                    model=card.model,
                    contents=contents,
                    config=config,
                )
                usage = getattr(response, "usage_metadata", None)
                if usage:
                    tokens_in += getattr(usage, "prompt_token_count", 0) or 0
                    tokens_out += getattr(usage, "candidates_token_count", 0) or 0
                yield StreamEvent(
                    kind="usage",
                    payload={"input_tokens": tokens_in, "output_tokens": tokens_out},
                )

                candidates = getattr(response, "candidates", None) or []
                if not candidates:
                    yield StreamEvent(kind="turn_end", payload={"turn": turn + 1})
                    break
                content = getattr(candidates[0], "content", None)
                parts = getattr(content, "parts", []) if content else []

                assistant_parts: list[dict[str, Any]] = []
                tool_calls: list[tuple[str, str, dict[str, Any]]] = []
                for part in parts:
                    text = getattr(part, "text", None)
                    fcall = getattr(part, "function_call", None)
                    if text:
                        assistant_parts.append({"text": text})
                        yield StreamEvent(kind="text_delta", text=text)
                        yield StreamEvent(kind="assistant_message", text=text)
                    if fcall:
                        name = getattr(fcall, "name", "")
                        args = dict(getattr(fcall, "args", {}) or {})
                        call_id = f"gemini-{turn}-{len(tool_calls)}"
                        tool_calls.append((call_id, name, args))
                        assistant_parts.append(
                            {
                                "function_call": {"name": name, "args": args},
                            }
                        )
                        yield StreamEvent(
                            kind="tool_call",
                            text=name,
                            payload={"tool_use_id": call_id, "name": name, "params": args},
                        )

                contents.append({"role": "model", "parts": assistant_parts})

                if not tool_calls:
                    yield StreamEvent(
                        kind="turn_end",
                        payload={"turn": turn + 1, "stop_reason": "end_turn"},
                    )
                    break

                response_parts: list[dict[str, Any]] = []
                for call_id, name, args in tool_calls:
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
                    response_parts.append(
                        {
                            "function_response": {
                                "name": name,
                                "response": result.content,
                            }
                        }
                    )
                contents.append({"role": "user", "parts": response_parts})
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
            log.exception("Gemini run_with_tools failed")
            yield StreamEvent(kind="error", text=str(exc))

    async def healthcheck(self) -> bool:
        if isinstance(self._sdk, _MissingDependency):
            return False
        return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))


def _strip_jsonschema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's function schema rejects some JSON-Schema fields; strip them."""
    if not isinstance(schema, dict):
        return schema  # type: ignore[return-value]
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("default", "additionalProperties"):
            continue
        if isinstance(v, dict):
            out[k] = _strip_jsonschema_for_gemini(v)
        elif isinstance(v, list):
            out[k] = [_strip_jsonschema_for_gemini(it) if isinstance(it, dict) else it for it in v]
        else:
            out[k] = v
    return out
