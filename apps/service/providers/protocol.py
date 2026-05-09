"""Provider protocol and shared message shapes.

Every adapter (Anthropic, Google, OpenAI, Ollama) implements LLMProvider.
The orchestrator only ever sees these typed shapes; vendor-specific
quirks are confined to the adapter.

Two execution modes:

- ChatSession: lightweight, in-process, no worktree.  Backs the chat panes.
- Run dispatch (open_run): worktree-bound; adapter-specific.

For V1 only ChatSession is fully implemented.  Run dispatch is a stub
ready to be filled in week 4 of Phase 1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

from apps.service.types import PersonalityCard


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "system", "tool"]
    text: str


@dataclass
class StreamEvent:
    """Normalized event yielded by ChatSession.send().

    Adapter-agnostic so the GUI binds to one shape regardless of vendor.
    """

    kind: Literal[
        "text_delta",
        "assistant_message",
        "tool_call",
        "tool_result",
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

    async def healthcheck(self) -> bool:
        ...
