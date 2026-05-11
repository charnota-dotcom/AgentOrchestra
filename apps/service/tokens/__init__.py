"""Token estimation + context-window lookup.

Sub-package owns everything to do with "how many tokens is this text
and how close are we to the model's context limit?".  Pure functions,
no I/O, no Qt, no httpx — reusable in isolation.

Public API:

* ``estimate_tokens(text, *, provider, model) -> int``
  Approximate token count for ``text`` as it would be tokenised by
  ``provider``'s ``model``.  v1 uses a ``len(text) // 4`` heuristic
  (accurate to ±30% on English) and ignores the provider/model args;
  they exist so v2 can plug in real tokenisers without changing the
  call site.

* ``context_window(provider, model) -> int | None``
  Returns the model's context-window size in tokens, or ``None`` for
  unknown pairs.  Callers should hide their context-usage UI when
  this returns ``None`` rather than guessing.

* ``estimate_action_total(action, *, system_prompt) -> int``
  Convenience helper: sum of ``estimate_tokens`` across an action's
  full transcript plus an optional system-prompt string.

Hard-import rule: nothing in this sub-package may import from
``apps.gui.*`` or ``apps.service.providers.*``.  Keeps the package
testable in isolation and reusable across surfaces.
"""

from __future__ import annotations

from apps.service.tokens.estimate import (
    estimate_action_total,
    estimate_tokens,
)
from apps.service.tokens.limits import context_window

__all__ = [
    "context_window",
    "estimate_action_total",
    "estimate_tokens",
]
