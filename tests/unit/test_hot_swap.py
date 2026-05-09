"""Hot model swap decision logic."""

from __future__ import annotations

from apps.service.dispatch import hot_swap
from apps.service.types import (
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    PersonalityCard,
    SandboxTier,
)


def _card(provider: str, model: str, fallbacks: list[dict]) -> PersonalityCard:
    return PersonalityCard(
        name="x",
        archetype="demo",
        description="d",
        template_id="t",
        provider=provider,  # type: ignore[arg-type]
        model=model,
        mode=CardMode.CHAT,
        fallbacks=fallbacks,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )


def test_no_swap_below_threshold() -> None:
    card = _card(
        "anthropic",
        "claude-sonnet-4-5",
        [{"provider": "google", "model": "gemini-2.5-pro"}],
    )
    plan = hot_swap.plan_swap(card, tokens_used=1000)
    assert plan.triggered is False


def test_swap_when_threshold_crossed_and_larger_fallback() -> None:
    card = _card(
        "anthropic",
        "claude-sonnet-4-5",
        [{"provider": "google", "model": "gemini-2.5-pro"}],
    )
    plan = hot_swap.plan_swap(card, tokens_used=180_000)
    assert plan.triggered is True
    assert plan.to_provider == "google"
    assert plan.to_model == "gemini-2.5-pro"


def test_no_swap_when_no_fallbacks() -> None:
    card = _card("anthropic", "claude-sonnet-4-5", [])
    plan = hot_swap.plan_swap(card, tokens_used=999_999)
    assert plan.triggered is False


def test_no_swap_when_fallback_isnt_larger() -> None:
    # Sonnet fallback to Haiku — both ~200k, so no benefit.
    card = _card(
        "anthropic",
        "claude-sonnet-4-5",
        [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
    )
    plan = hot_swap.plan_swap(card, tokens_used=180_000)
    assert plan.triggered is False
    assert "no larger fallback" in plan.reason


def test_pick_first_larger_fallback_in_order() -> None:
    card = _card(
        "anthropic",
        "claude-sonnet-4-5",
        [
            {"provider": "anthropic", "model": "claude-haiku-4-5"},
            {"provider": "google", "model": "gemini-2.5-flash"},
            {"provider": "google", "model": "gemini-2.5-pro"},
        ],
    )
    plan = hot_swap.plan_swap(card, tokens_used=180_000)
    # Flash (1M) is the first strictly-larger option.
    assert plan.triggered is True
    assert plan.to_model == "gemini-2.5-flash"
