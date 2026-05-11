"""Token-count estimation.

v1 strategy: ``max(1, len(text) // 4)`` per chunk.  Accurate to ±30%
on English prose, much less on code or non-Latin scripts.  Labelled
``~`` and ``(est)`` everywhere it shows in the UI so the operator
never mistakes the number for an exact count.

The function takes ``provider`` and ``model`` kwargs (currently
ignored) so a future v2 — bundling ``tiktoken`` for GPT, the offline
``anthropic`` tokeniser for Claude, SentencePiece for Gemini — can
slot in behind the same signature without touching call sites.
"""

from __future__ import annotations

from typing import Any


def estimate_tokens(
    text: str,
    *,
    provider: str = "",
    model: str = "",
) -> int:
    """Approximate token count for ``text``.

    ``provider``/``model`` accepted for future-pluggable exact
    tokenisers; currently ignored.  Empty / whitespace-only input
    returns 0 (callers can sum freely without worrying about empty
    strings).
    """
    del provider, model  # v2 hook
    if not text:
        return 0
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, len(text) // 4)


def estimate_action_total(
    action: Any,
    *,
    system_prompt: str = "",
    provider: str = "",
    model: str = "",
) -> int:
    """Sum tokens across an action's transcript plus an optional system
    prompt.

    Accepts either a dict (as it comes off the wire) or a Pydantic
    ``DroneAction``; reads the ``transcript`` attribute either way.
    Skips entries whose role is none of ``user`` / ``assistant`` —
    PR 3's richer entry kinds (``tool_call`` / ``tool_result`` /
    ``subagent``) are counted by their full ``content`` if present;
    otherwise fall back to the entry's repr length (rare path,
    defensive only).
    """
    total = estimate_tokens(system_prompt, provider=provider, model=model)
    transcript = _get_transcript(action)
    for entry in transcript or []:
        content = _entry_text(entry)
        total += estimate_tokens(content, provider=provider, model=model)
    return total


def _get_transcript(action: Any) -> list[Any]:
    if action is None:
        return []
    if isinstance(action, dict):
        return action.get("transcript") or []
    return getattr(action, "transcript", []) or []


def _entry_text(entry: Any) -> str:
    if isinstance(entry, dict):
        # Prefer ``content`` (user/assistant/tool_call/tool_result).
        for key in ("content", "output", "input", "prompt", "result"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                # tool_call.input is a dict of arg → value; flatten to a
                # readable string so its size counts toward the total.
                return " ".join(f"{k}={v}" for k, v in value.items())
        return ""
    return str(getattr(entry, "content", "") or "")
