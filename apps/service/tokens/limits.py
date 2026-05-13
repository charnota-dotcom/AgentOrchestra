"""Per-model context-window sizes.

Flat lookup table keyed by ``(provider, model)``.  Unknown pairs
return ``None`` — callers should hide their context gauge in that
case rather than guess.

Values come from each vendor's published documentation as of
2026-05.  Sources:

* Anthropic Claude Sonnet/Opus/Haiku 4.x: 200 K context.
* Google Gemini 2.5/3.x: roughly 1 M input window on current text models.
* OpenAI Codex-family models: 400 K context.

To add a new model: append to ``CONTEXT_WINDOWS`` below.  Provider
strings match the ``Blueprint.provider`` enum exactly (``claude-cli``,
``gemini-cli``, ``anthropic``, ``browser`` — browser uses the same
model names as the underlying service).
"""

from __future__ import annotations

CONTEXT_WINDOWS: dict[tuple[str, str], int] = {
    # --- Anthropic Claude family ---
    ("claude-cli", "claude-haiku-4-5"): 200_000,
    ("claude-cli", "claude-sonnet-4-5"): 200_000,
    ("claude-cli", "claude-sonnet-4-6"): 200_000,
    ("claude-cli", "claude-opus-4-6"): 200_000,
    ("claude-cli", "claude-opus-4-7"): 200_000,
    ("anthropic", "claude-haiku-4-5"): 200_000,
    ("anthropic", "claude-sonnet-4-5"): 200_000,
    ("anthropic", "claude-sonnet-4-6"): 200_000,
    ("anthropic", "claude-opus-4-6"): 200_000,
    ("anthropic", "claude-opus-4-7"): 200_000,
    # --- Google Gemini family ---
    ("gemini-cli", "gemini-1.5-flash"): 1_000_000,
    ("gemini-cli", "gemini-1.5-pro"): 2_000_000,
    ("gemini-cli", "gemini-2.0-flash"): 1_000_000,
    ("gemini-cli", "gemini-2.5-flash"): 1_000_000,
    ("gemini-cli", "gemini-2.5-pro"): 1_000_000,
    ("gemini-cli", "gemini-2.5-flash-lite"): 1_048_576,
    ("gemini-cli", "gemini-3-pro-preview"): 1_048_576,
    ("gemini-cli", "gemini-3-flash-preview"): 1_048_576,
    # --- Codex CLI family ---
    ("codex-cli", "gpt-5.3-codex"): 400_000,
    ("codex-cli", "gpt-5.2-codex"): 400_000,
    ("codex-cli", "gpt-5-codex"): 400_000,
    ("codex-cli", "codex-mini-latest"): 400_000,
    # --- Browser mode (operator types into a web tab) ---
    # Browser-mode drones share model names with their underlying
    # service; reuse the same numbers.
    ("browser", "claude-haiku-4-5"): 200_000,
    ("browser", "claude-sonnet-4-6"): 200_000,
    ("browser", "claude-opus-4-7"): 200_000,
    ("browser", "gemini-2.5-pro"): 1_000_000,
    ("browser", "gemini-2.5-flash"): 1_000_000,
}


def context_window(provider: str, model: str) -> int | None:
    """Return the context-window size in tokens for ``(provider, model)``.

    Returns ``None`` if the pair isn't in the table — callers should
    treat that as "don't display a percentage / gauge".
    """
    if not provider or not model:
        return None
    return CONTEXT_WINDOWS.get((provider, model))
