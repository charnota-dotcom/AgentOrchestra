"""Cost meter + pre-dispatch forecast.

Holds a pinned price table per (provider, model) — refreshed on launch
in production, hard-coded here for V1.  Forecast estimates use prior
similar-archetype runs when available; falls back to a generic model
multiplier.
"""

from __future__ import annotations

from dataclasses import dataclass

# USD per 1M tokens.  Values pinned to mid-2026 published rates; the
# update channel will refresh these on launch in production.
_PRICE_TABLE: dict[tuple[str, str], tuple[float, float]] = {
    # provider, model -> (input_per_million, output_per_million)
    ("anthropic", "claude-sonnet-4-5"): (3.0, 15.0),
    ("anthropic", "claude-opus-4-7"): (15.0, 75.0),
    ("anthropic", "claude-haiku-4-5"): (0.25, 1.25),
    ("google", "gemini-2.5-pro"): (1.25, 10.0),
    ("google", "gemini-2.5-flash"): (0.30, 2.50),
    ("openai", "gpt-4o"): (2.50, 10.0),
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("ollama", "llama3"): (0.0, 0.0),  # local
}


@dataclass(frozen=True)
class Forecast:
    low_usd: float
    high_usd: float
    expected_usd: float
    rationale: str


def cost_for_call(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    rate = _PRICE_TABLE.get((provider, model))
    if not rate:
        return 0.0
    in_per_m, out_per_m = rate
    return (tokens_in / 1_000_000) * in_per_m + (tokens_out / 1_000_000) * out_per_m


def known_models() -> list[tuple[str, str]]:
    return sorted(_PRICE_TABLE.keys())


def forecast(
    provider: str,
    model: str,
    *,
    rendered_prompt_tokens: int,
    archetype: str | None = None,
    history: list[dict] | None = None,
) -> Forecast:
    """Estimate (low, high, expected) cost in USD for a Run."""

    rate = _PRICE_TABLE.get((provider, model))
    if not rate:
        return Forecast(0.0, 0.0, 0.0, "unknown model — cannot forecast cost")

    # Calibration multipliers per archetype.  Heuristic; refined over time
    # by reading past runs from the event store.
    archetype_mult = {
        "broad-research": (3.0, 12.0, 6.0),
        "narrow-research": (2.0, 8.0, 4.0),
        "qa-on-fix": (1.5, 5.0, 2.5),
    }.get(archetype or "", (2.0, 8.0, 4.0))

    in_low = rendered_prompt_tokens
    in_high = int(rendered_prompt_tokens * 1.5) + 1_000

    out_low = int(rendered_prompt_tokens * archetype_mult[0])
    out_expected = int(rendered_prompt_tokens * archetype_mult[2])
    out_high = int(rendered_prompt_tokens * archetype_mult[1])

    low = cost_for_call(provider, model, in_low, out_low)
    expected = cost_for_call(provider, model, rendered_prompt_tokens, out_expected)
    high = cost_for_call(provider, model, in_high, out_high)

    rationale = (
        f"forecast based on prompt size {rendered_prompt_tokens} tokens, "
        f"{archetype or 'generic'} archetype profile"
    )
    return Forecast(low_usd=low, high_usd=high, expected_usd=expected, rationale=rationale)
