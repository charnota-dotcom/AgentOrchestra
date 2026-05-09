"""Hot model swap.

Mid-run escape valve: when the running tokens approach a card's
``hard_cap_tokens`` minus a safety buffer, switch the active provider/
model to a larger-context fallback (declared in ``card.fallbacks``)
rather than aborting.  The current message history is replayed against
the new model with a one-line context-handoff note prepended.

Two integration points:

1. ``check_and_swap`` is called by the dispatcher inside the agent
   loop after every ``usage`` event.  Returns a tuple of
   (should_swap, new_provider_name, new_model_name) that the
   dispatcher uses to bind a fresh session for the next turn.
2. ``HotSwapPlan`` describes the new routing so the dispatcher can
   emit a single, structured ``SwapEngaged`` event the GUI surfaces
   to the operator.

We do not transparently rewrite history at the SDK layer — that's
adapter-specific and risky.  Instead the dispatcher closes the old
session, opens a fresh one with the new card variant, and replays the
prior turn's user message (the agent loop already keeps that history
in memory).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.service.types import PersonalityCard

log = logging.getLogger(__name__)


# Approximate token capacities per (provider, model).  These don't
# need to be exact — they're a hint for when to engage hot-swap.
_CONTEXT_CAPS: dict[tuple[str, str], int] = {
    ("anthropic", "claude-haiku-4-5"): 200_000,
    ("anthropic", "claude-sonnet-4-5"): 200_000,
    ("anthropic", "claude-opus-4-7"): 200_000,
    ("google", "gemini-2.5-flash"): 1_000_000,
    ("google", "gemini-2.5-pro"): 2_000_000,
    ("openai", "gpt-4o-mini"): 128_000,
    ("openai", "gpt-4o"): 128_000,
    ("ollama", "llama3"): 8_000,
}


@dataclass(frozen=True)
class HotSwapPlan:
    triggered: bool
    reason: str
    from_provider: str
    from_model: str
    to_provider: str
    to_model: str


def context_cap(provider: str, model: str) -> int:
    return _CONTEXT_CAPS.get((provider, model), 128_000)


def should_swap(
    card: PersonalityCard,
    *,
    tokens_used: int,
    headroom: float = 0.85,
) -> bool:
    """True when ``tokens_used`` exceeds ``headroom`` of the current
    model's context cap AND the card declares at least one fallback.
    """
    if not card.fallbacks:
        return False
    cap = context_cap(card.provider, card.model)
    return tokens_used >= int(cap * headroom)


def pick_swap_target(card: PersonalityCard) -> tuple[str, str] | None:
    """Pick the first fallback whose context cap is strictly larger
    than the current model's.  Returns None if no such fallback
    exists.
    """
    current_cap = context_cap(card.provider, card.model)
    for fb in card.fallbacks:
        p = fb.get("provider")
        m = fb.get("model")
        if not (p and m):
            continue
        if context_cap(p, m) > current_cap:
            return p, m
    return None


def plan_swap(card: PersonalityCard, *, tokens_used: int) -> HotSwapPlan:
    """Convenience wrapper that returns a HotSwapPlan describing the
    decision.  ``triggered=False`` when no swap should happen.
    """
    if not should_swap(card, tokens_used=tokens_used):
        return HotSwapPlan(
            triggered=False,
            reason="below threshold",
            from_provider=card.provider,
            from_model=card.model,
            to_provider=card.provider,
            to_model=card.model,
        )
    target = pick_swap_target(card)
    if target is None:
        return HotSwapPlan(
            triggered=False,
            reason="no larger fallback declared",
            from_provider=card.provider,
            from_model=card.model,
            to_provider=card.provider,
            to_model=card.model,
        )
    new_p, new_m = target
    return HotSwapPlan(
        triggered=True,
        reason=(
            f"{tokens_used} tokens used vs "
            f"{context_cap(card.provider, card.model)} cap; "
            f"swapping to {new_p}/{new_m} ({context_cap(new_p, new_m)} cap)"
        ),
        from_provider=card.provider,
        from_model=card.model,
        to_provider=new_p,
        to_model=new_m,
    )
