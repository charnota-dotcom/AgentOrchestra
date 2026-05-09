"""Provider protocol and shared message shapes.

Every adapter (Anthropic, Google, OpenAI, Ollama) implements LLMProvider.
The orchestrator only ever sees these typed shapes; vendor-specific
quirks are confined to the adapter.

Two execution modes:

- ChatSession: lightweight, in-process, no worktree.  Backs the chat
  panes and the V1 research/QA archetypes.
- Agent loop (run_with_tools): worktree-bound; the adapter executes a
  tool-using loop, calling back into ToolExecutor for each tool_use,
  yielding normalized events to the dispatcher.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from apps.service.types import PersonalityCard

if TYPE_CHECKING:
    from apps.service.dispatch.tools import ToolExecutor


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "system", "tool"]
    text: str


@dataclass
class StreamEvent:
    """Normalized event yielded by ChatSession.send() and run_with_tools().

    Adapter-agnostic so the GUI binds to one shape regardless of vendor.
    """

    kind: Literal[
        "text_delta",
        "assistant_message",
        "tool_call",
        "tool_result",
        "turn_end",
        "usage",
        "finish",
        "error",
    ]
    text: str = ""
    payload: dict = field(default_factory=dict)


class ChatSession(Protocol):
    """A multi-turn conversation with one provider/model."""

    async def send(self, message: str) -> AsyncIterator[StreamEvent]:
        """Send a user message and stream back events."""
        ...

    async def close(self) -> None:
        ...


class LLMProvider(Protocol):
    name: Literal["anthropic", "google", "openai", "ollama"]

    async def open_chat(self, card: PersonalityCard, *, system: str | None = None) -> ChatSession:
        ...

    async def run_with_tools(
        self,
        card: PersonalityCard,
        *,
        system: str | None,
        user_message: str,
        executor: "ToolExecutor",
        max_turns: int = 16,
    ) -> AsyncIterator[StreamEvent]:
        """Run an agent loop that may call tools.

        Implementations:
        1. Send the user message with the executor's tool definitions.
        2. On each assistant turn:
           a. Yield ``text_delta`` / ``assistant_message`` for any text.
           b. For each ``tool_use`` block, yield ``tool_call``, invoke
              ``executor.execute(...)``, then yield ``tool_result`` and
              feed the result back to the model.
        3. End when stop_reason is end_turn or max_turns is exhausted.
        """
        ...

    async def healthcheck(self) -> bool:
        ...
