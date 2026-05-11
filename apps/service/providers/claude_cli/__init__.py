"""Claude Code CLI provider sub-package.

Public re-exports — call sites should import from this package, not
from the implementation modules directly:

    from apps.service.providers.claude_cli import ClaudeCLIProvider

Sub-modules:

* ``provider.py`` — ``ClaudeCLIProvider`` (the ``LLMProvider``
  implementation registered with ``apps.service.providers.registry``).
* ``session.py`` — ``ClaudeCLIChatSession`` (one chat session against
  a local ``claude`` subprocess).
* ``stream_parser.py`` — parses ``claude --output-format stream-json``
  events into ``StreamEvent``s + transcript-ready dicts.

Hard import rule (same pattern as ``apps.gui.browser_bridge`` /
``apps.service.tokens``): nothing inside this sub-package imports
from outside it except the provider protocol contract
(``apps.service.providers.protocol``) and shared types
(``apps.service.types``).  Keeps it testable in isolation.

See ``docs/BROWSER_PROVIDER_PLAN.md`` (PR 3) for the design.
"""

from __future__ import annotations

from apps.service.providers.claude_cli.provider import (
    ClaudeCLIProvider,
    _claude_binary,
)
from apps.service.providers.claude_cli.session import (
    ClaudeCLIChatSession,
    _resolve_model,
)
from apps.service.providers.claude_cli.stream_parser import (
    ParsedEvent,
    parse_stream_json,
)

__all__ = [
    "ClaudeCLIChatSession",
    "ClaudeCLIProvider",
    "ParsedEvent",
    "_claude_binary",
    "_resolve_model",
    "parse_stream_json",
]
